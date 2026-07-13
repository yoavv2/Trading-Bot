---
phase: 09-reconciliation-rewrite
plan: 03
subsystem: reconciliation
tags: [sqlalchemy, orm-projection, reconciliation, read-only, dataclasses]

# Dependency graph
requires:
  - phase: 09-reconciliation-rewrite (plan 09-01)
    provides: Closed ReconciliationFinding enum, typed Finding value object, Local*Snapshot dataclasses
  - phase: 09-reconciliation-rewrite (plan 09-02)
    provides: Pure indexed match_snapshots() over typed snapshots
provides:
  - reconcile_paper_execution rewritten as a strictly read-only orchestrator over typed snapshots + the pure matcher
  - Two read-only blocking evaluations (_evaluate_account_divergence D1, _evaluate_threshold_breach D2) replacing inline mutation-coupled logic
  - Exactly one materialized report per run (StrategyRun + ExecutionEvent rows) with closed-enum findings tied to their source snapshot identity
affects: [09-04-correction-separation]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "ORM-to-typed-snapshot projection boundary owned by the orchestrator (not the matcher): _project_local_order/_project_local_fill/_project_local_position/_project_local_account convert ORM rows to 09-01 dataclasses immediately after the read-only SELECT, before anything touches the pure matcher"
    - "Read-only report-summary flags (account_divergence, threshold_breach) computed alongside closed-enum findings but persisted only into StrategyRun.result_summary, never as a fake finding or ExecutionEvent row"
    - "_finding_event_dict as the single seam that ties a closed-enum Finding back to its source snapshot identity (symbol/account/side) and source ids in ExecutionEvent.details, called identically for both persistence and result_summary serialization"

key-files:
  created: []
  modified:
    - src/trading_platform/services/reconciliation.py
    - tests/test_execution_reconciliation.py

key-decisions:
  - "D1 (account divergence) preserved as a read-only report-summary flag, not a finding: all three pre-rewrite branches kept exactly -- (B1) no AccountSnapshot ever persisted AND broker/local positions exist -> account_snapshot_missing_locally=true sub-flag, BLOCKS; (B2) no AccountSnapshot AND flat book -> empty dict, does NOT block; (B3) AccountSnapshot exists AND cash/buying_power/equity/gross_exposure/open_positions deltas exceed tolerance -> populated deltas, BLOCKS. Both B1 and B2 are pinned by dedicated tests since a literal 'D1 = not a finding' reading would silently drop B1's blocking behavior."
  - "D2 (repeated-failure threshold) split cleanly: the READ half stays in reconcile as _evaluate_threshold_breach, evaluating the exact same two predicates the pre-rewrite code used (submission_attempt_count >= threshold for SUBMISSION_FAILED orders; sync_failure_count + 1 >= threshold for orders with a sync error THIS run, derived from the matcher's Finding.paper_order_id/message) with zero row writes. The WRITE half (the increment) is deleted from reconcile entirely and relocates to the 09-04 corrective path."
  - "D3 (clean state): no synthetic 'reconciliation_clean' ExecutionEvent. A clean run persists the same one StrategyRun + zero ExecutionEvent rows, with result_summary.finding_count == 0 as the clean signal."
  - "blocks_execution = bool(findings) or bool(account_divergence) or bool(threshold_breach), computed once and persisted into result_summary so the report explains why it blocks, rather than being reconstructed ad hoc from findings alone."
  - "_local_state_from_broker_status was deleted alongside _build_findings (not explicitly named in the plan) because it became dead code coupled 1:1 to the deleted function and duplicated the matcher's own broker-status-to-local-status mapping; keeping it would have left an unreferenced, misleading duplicate of matching logic in the orchestrator module."
  - "Both tasks' code changes to reconciliation.py landed in the Task 1 commit (not split across Task 1/2 commits) because the old persistence loop reads finding.event_type/severity directly (ReconciliationFinding dataclass fields) while match_snapshots() returns Finding objects with .category (not .event_type) -- the function is not import/runnable, let alone test-green, until the persistence loop is switched to Finding.to_event_dict(). Task 2's commit is therefore the test-file-only half of the plan's task split; this is a structural commit-grouping deviation, not a scope change (mirrors 09-02's identical commit-grouping note)."

patterns-established:
  - "Report-summary-only blocking flags: account_divergence and threshold_breach are computed as read-only dicts/lists inside reconcile, folded into StrategyRun.result_summary, and combined into blocks_execution -- but never surface as ExecutionEvent rows, keeping the closed 5-member finding enum truly closed."

requirements-completed: [RECON-01, RECON-02, RECON-03, RECON-09]

# Metrics
duration: ~35min
completed: 2026-07-13
---

# Phase 9: Reconciliation Rewrite — Plan 03 Summary

**`reconcile_paper_execution` rewritten as a strictly read-only orchestrator: typed ORM projections feed the pure indexed matcher from 09-02, one materialized StrategyRun + ExecutionEvent report is persisted per run with closed-enum findings tied back to their source snapshot identity, and the account-divergence / repeated-failure-threshold safety guards are preserved as read-only report-summary flags with the sync-failure increment removed entirely (relocating to 09-04).**

## Performance

- **Duration:** ~35 min
- **Completed:** 2026-07-13
- **Tasks:** 2
- **Files modified:** 2 (both pre-existing)

## Accomplishments
- `reconcile_paper_execution` no longer contains a single write to `paper_orders`/`positions`/`account_snapshots` (RECON-03): `_build_findings`, `_order_error_messages`, `_apply_sync_failure_state`, and the now-dead `_local_state_from_broker_status` are deleted outright, with a `# increment moved to corrective path (09-04)` breadcrumb marking where the sync-failure write used to happen.
- ORM rows are projected into the 09-01 typed snapshots (`_project_local_order`/`_project_local_fill`/`_project_local_position`/`_project_local_account`) immediately after the read-only `SELECT`s, then handed to 09-02's `match_snapshots()` — no ORM instance crosses into the matcher (RECON-01/02/05).
- Exactly one materialized report per run: the StrategyRun (RECONCILIATION type) create/update is unchanged, and every `Finding` becomes one `ExecutionEvent` via `_finding_event_dict()`, which serializes `category.name` (e.g. `MISSING_LOCAL`, `STATE_MISMATCH`) and folds the finding's identity (symbol/account/side) and source ids (paper_order_id/broker_order_id) into `details` so every persisted finding ties back to its source snapshot (RECON-09).
- Two read-only blocking evaluations replace the inline mutation-coupled logic: `_evaluate_account_divergence` (D1, all three pre-rewrite account branches preserved exactly) and `_evaluate_threshold_breach` (D2, read-only, both predicates, zero writes). Both are persisted into `result_summary` (not as findings) and folded into `blocks_execution = bool(findings) or bool(account_divergence) or bool(threshold_breach)`.
- A clean/flat reconcile persists the same one StrategyRun report with zero `ExecutionEvent` rows and `result_summary.finding_count == 0` — no synthetic `reconciliation_clean` finding (D3/RECON-09).
- 7 tests pass, 2 explicitly skipped with a breadcrumb (the old in-reconcile mutation assertions, superseded by their read-only replacements); full repo suite: 212 passed, 2 skipped, 1 pre-existing unrelated error (logged in `deferred-items.md`, not caused by this plan).

## Task Commits

1. **Task 1: Load typed snapshots, drive the matcher, compute read-only blocking signals** — `718cd1b` (feat). This commit contains the FULL `reconciliation.py` rewrite (both Task 1's and Task 2's code changes to that file), because the old persistence loop and the new one are not independently runnable — see Deviations.
2. **Task 2: Materialize closed-enum report + invariant/clean/threshold/account tests** — `a70528d` (test)

## Files Created/Modified
- `src/trading_platform/services/reconciliation.py` — `reconcile_paper_execution` rewritten to a read-only orchestrator; `_build_findings`/`_order_error_messages`/`_apply_sync_failure_state`/`_local_state_from_broker_status` deleted; new `_project_local_*` projection helpers, `_evaluate_account_divergence`, `_evaluate_threshold_breach`, `_finding_event_dict` added (net -149 lines vs. pre-rewrite).
- `tests/test_execution_reconciliation.py` — version-chain test's vacuous assertion fixed to `STATE_MISMATCH`; two old mutation tests skipped with breadcrumbs; the repeated-submission-failure test repurposed into the threshold-still-blocks test; four new tests added (no-mutation, clean-run, account-missing-blocks B1, never-synced-flat B2).

## Decisions Made
See `key-decisions` in frontmatter above.

## Deviations from Plan

### Structural (no Rule 1-4 trigger, documented for transparency)

**1. Task 1's commit contains all of `reconciliation.py`'s rewritten logic, not just the matcher-wiring half**
- **Found during:** Task 1, before writing any code (surfaced by advisor review)
- **Reasoning:** The plan splits Task 1 ("load typed snapshots, drive the matcher, compute blocking signals") from Task 2 ("materialize the report, preserve blocks_execution, add tests") as if they touch independent code regions of the same function. They do not: the pre-rewrite persistence loop reads `finding.event_type`/`finding.severity` directly off the old `ReconciliationFinding` dataclass, while `match_snapshots()` returns `Finding` objects with `.category` (an enum) instead. The moment `_build_findings` is deleted, the persistence loop cannot run at all until it is switched to `Finding.to_event_dict()` — which is explicitly Task 2's scope ("materialize... via `Finding.to_event_dict()`"). Task 1's own verify command (`pytest -x -q` against the OLD test file) could not pass in isolation regardless of code correctness, since the old test file's string-based `event_type` assertions and mutation assertions are Task 2's job to fix.
- **Effect:** Task 1's commit (`718cd1b`) contains the complete, coherent rewrite of `reconcile_paper_execution` and all its helper functions — both the matcher-wiring and the report-materialization halves — because they are one non-decomposable code change. Task 2's commit (`a70528d`) is therefore test-file-only. Both tasks' `<done>` criteria are independently verifiable against the final state: Task 1's ("reconcile uses typed snapshots + match_snapshots; `_build_findings` removed; no `_apply_sync_failure_state` call; all three account branches preserved") and Task 2's ("one report per run with enum-named findings tied to source snapshots; clean run = empty findings + report; blocks_execution still trips; the five new/updated tests pass; relocated mutation tests explicitly skipped") both hold.
- **Files affected:** `src/trading_platform/services/reconciliation.py` (Task 1 commit only)
- **Commits:** `718cd1b`, `a70528d`
- **Precedent:** Identical in kind to 09-02's documented "Task 1 RED commit and Task 2's benchmark test were combined" deviation — a commit-grouping note with no scope, behavior, or requirement change.

**2. `_local_state_from_broker_status` deleted in addition to the three functions the plan named**
- **Found during:** Task 1
- **Reasoning:** The plan explicitly names `_build_findings`, `_order_error_messages`, and the call to `_apply_sync_failure_state` for deletion. `_local_state_from_broker_status` was called from exactly one site — inside `_build_findings`, at the line computing `expected_local_status` — and nowhere else in the module. Deleting `_build_findings` without also deleting this helper would leave an unreferenced, misleading duplicate of the matcher's own `_BROKER_STATUS_TO_EXPECTED_LOCAL_STATUS` mapping sitting dead in the orchestrator module.
- **Effect:** Removed as dead code coupled to the same deletion; no behavior change (nothing else called it).
- **Files affected:** `src/trading_platform/services/reconciliation.py`
- **Commit:** `718cd1b`

**3. `_QUANTITY_TOLERANCE` module constant deleted**
- **Found during:** Task 1
- **Reasoning:** Its only use site was position quantity-mismatch comparison inside `_build_findings`, logic now owned entirely by `reconciliation_matcher._match_positions` (which carries its own `_QUANTITY_TOLERANCE`). After `_build_findings`'s deletion the constant was unreferenced.
- **Effect:** Removed; `_MONEY_TOLERANCE` (still used by `_evaluate_account_divergence`) retained.
- **Files affected:** `src/trading_platform/services/reconciliation.py`
- **Commit:** `718cd1b`

---

**Total deviations:** 1 structural (commit-grouping only) + 2 minor dead-code removals directly coupled to the plan's own named deletions. No behavior, scope, or requirement change in any case.
**Impact on plan:** None on functional scope. Every plan-level `<done>` and `<verify>` criterion passes against the final state; the only difference from a hypothetical literal reading is which of the two commits a given line landed in.

## Issues Encountered
None requiring debugging iteration — the advisor's pre-implementation review (called before writing code) surfaced the Task 1/Task 2 coupling, the `threshold_breach`-is-not-a-finding trap, and the two-predicate threshold-breach requirement up front, so the rewrite passed its test run on the first attempt after the two commits.

## User Setup Required
None — no external service configuration required.

## Next Phase Readiness
- `reconcile_paper_execution` is now a pure read-only orchestrator; 09-04 (correction-separation) can build the corrective entrypoint that owns the sync-failure `sync_failure_count`/`last_sync_error`/`last_sync_failure_at` increment relocated out of this plan, re-homing the two skipped tests (`test_reconciliation_persists_blocking_findings_and_updates_sync_failure_state`, `test_reconciliation_persists_clean_event_and_resets_sync_failures`) onto that new entrypoint.
- `threshold_breach` and `account_divergence` are available in `StrategyRun.result_summary` (not on the `ReconciliationReport` dataclass) for any consumer that needs the detailed reasons behind `blocks_execution`; 09-04 or the operator console can query them directly.
- No blockers for 09-04.

---
*Phase: 09-reconciliation-rewrite*
*Completed: 2026-07-13*
