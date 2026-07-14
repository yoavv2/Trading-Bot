---
phase: 11-query-performance
plan: 01
subsystem: execution
tags: [sqlalchemy, postgres, query-optimization, n+1, paper-execution]

# Dependency graph
requires: []
provides:
  - "Reusable SQL statement-count test harness (tests/support/query_counter.py)"
  - "Batched, 2-query paper-execution preflight (auto-resolve path)"
  - "Query-count invariant integration test pinning PERF-01"
affects: [paper-execution, operator-console-paper-surfaces]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "before_cursor_execute-based query counting for hard query-count assertions in tests"
    - "Fold source-run resolution into candidate load via LIMIT-1 subquery LEFT JOINed on approved-predicate ON clause (preserves zero-candidate row)"
    - "Pure decision-core extraction so a shared decision function can be driven by either a query-based resolver or an in-memory-index resolver"

key-files:
  created:
    - tests/support/__init__.py
    - tests/support/query_counter.py
    - tests/test_paper_preflight_query_count.py
  modified:
    - src/trading_platform/services/paper_execution.py

key-decisions:
  - "Extracted _build_intent_decision as a pure decision core shared by the original query-based _resolve_paper_intent_decision (execution submission loop, unchanged) and a new in-memory _resolve_paper_intent_decision_from_index (preflight only), instead of batching the submission loop -- the submission loop relies on mid-loop visibility of orders committed by earlier candidates in the same run, which batching would break"
  - "Used joinedload (not selectinload) for supersedes_paper_order eager-loading in Q2, since selectinload would fire a second statement and break the 2-query bound; joinedload is a many-to-one hop with no row multiplication"
  - "Folded Q1 as a LIMIT-1 run-resolution subquery LEFT JOINed to RiskEvent+Symbol with the approved/decision_code predicates in the JOIN ON clause (not WHERE), so a run with zero approved candidates still returns exactly one row and the resolved run id is never lost"

patterns-established:
  - "For a hard N+1 bound: fold multi-step resolution into one subquery+LEFT JOIN and pre-load a superset via one batched query with in-memory indexes, rather than optimizing per-iteration queries"

requirements-completed: [PERF-01]

duration: ~10min
completed: 2026-07-14
---

# Phase 11 Plan 01: Paper Preflight N+1 Elimination Summary

**Batched paper-execution preflight (`_build_paper_session_plan`, auto-resolve path) down from ~2-3·M queries to a flat 2 queries, proven via a real `before_cursor_execute` query-counting integration test (K=1 vs K=25 candidates, same count).**

## Performance

- **Duration:** ~10 min
- **Started:** 2026-07-14T10:16:00+03:00 (approx, first task commit)
- **Completed:** 2026-07-14T10:25:54+03:00
- **Tasks:** 3/3 completed
- **Files modified:** 4 (3 created, 1 modified)

## Accomplishments

- Built a reusable, dependency-free `count_queries(bind)` test harness (SQLAlchemy `before_cursor_execute` event) accepting a Session, Engine, or Connection.
- Eliminated the per-candidate N+1 query pattern in the auto-resolve paper-execution preflight: `_build_paper_session_plan` now issues exactly 2 SQL statements total, verified by dumping the actual statements against a real seeded Postgres test database (confirmed: one folded run-resolution+candidate-load statement, one batched PaperOrder statement with a joined `supersedes_paper_order`).
- Added an integration test that pins both the literal `<= 2` bound and the load-bearing non-scaling invariant (K=1 candidate issues the same query count as K=25).

## Task Commits

Each task was committed atomically:

1. **Task 1: Add a reusable SQL query-counting test harness** - `6685daf` (test)
2. **Task 2: Fold source-run resolution + batch intent resolution to make preflight a 2-query flow** - `be1c366` (feat)
3. **Task 3: Query-count invariant integration test for preflight** - `82bf9de` (test)

**Plan metadata:** (this commit)

_Note: Task commits 2 and 3 landed out of their natural task order (3 before 2) due to a concurrent parallel-plan git collision -- see Deviations below._

## Files Created/Modified

- `tests/support/__init__.py` - Empty package marker for the new `tests/support` package.
- `tests/support/query_counter.py` - `count_queries(bind)` context manager; counts every executed SQL statement against the resolved Engine and captures each statement string for debugging.
- `src/trading_platform/services/paper_execution.py` - Added `_load_auto_resolve_candidates` (Q1, folded run-resolution + candidate load), `_load_paper_order_index` (Q2, batched PaperOrder load with in-memory `by_intent_hash`/`predecessors_by_key` indexes), `_build_intent_decision` (pure decision core), `_resolve_paper_intent_decision_from_index` (preflight-only in-memory resolver); rewired `_build_paper_session_plan`'s auto-resolve branch onto these. The `requested_risk_run_id`-provided branch and the execution submission loop's `_resolve_paper_intent_decision` are unchanged.
- `tests/test_paper_preflight_query_count.py` - Two integration tests: `test_preflight_query_count_is_at_most_two`, `test_preflight_query_count_is_invariant_to_candidate_count`.

## Decisions Made

- **Pure-core extraction over global batching.** `_resolve_paper_intent_decision` (used by the execution submission loop in `_run_paper_order_submission_guarded`) was left query-based and unchanged. Only `_build_paper_session_plan`'s preflight now resolves decisions from in-memory indexes via a new sibling function, `_resolve_paper_intent_decision_from_index`. Both call a shared pure `_build_intent_decision(identity, existing_order, predecessor, candidate, failure_threshold)`. This keeps PERF-01's scope (preflight only) from silently changing the execution loop's behavior, which depends on seeing orders committed by earlier candidates within the same run.
- **`joinedload`, not `selectinload`, for `supersedes_paper_order`.** `selectinload` issues a second SELECT, which would make Q2 count as 2 statements on its own and push the total to 3. `supersedes_paper_order` is a many-to-one relationship (no row multiplication risk), so `joinedload` keeps the eager-load inside Q2's single statement.
- **LEFT JOIN, not INNER JOIN, for the Q1 fold.** An inner join between the run-resolution subquery and RiskEvent/Symbol would silently produce zero rows (and thus lose the resolved run id, breaking `PaperSessionPlan.source_risk_run_id`'s non-optional contract and the `noop_no_candidates` code path) whenever a run exists but has zero approved candidates. The approved/decision_code predicates were moved into the JOIN's `ON` clause specifically so they don't behave like a `WHERE` filter that would drop the run-only row.
- **`.as_string()` (not `.astext`) for JSON comparisons.** The `parameters_snapshot`/`result_summary` columns use SQLAlchemy's generic `sa.JSON` type (not `postgresql.JSONB`), so the Postgres-specific `.astext` comparator attribute isn't available. `.as_string()` is the generic cross-backend equivalent and was spiked against a real Postgres table before use, confirming it compiles to `->>` and returns `NULL` for a missing key (matching the existing Python `dict.get(...)` semantics exactly, including the `None != target` behavior).

## Deviations from Plan

### Auto-fixed Issues

None beyond the plan's own scope -- no Rule 1/2/3 auto-fixes were needed; the implementation matched the plan's design exactly once the LEFT JOIN correction (below) was applied during design review.

### Process deviation (git working-tree collision, not a code defect)

**Concurrent parallel-plan-executor `git add -A` swept Task 2's uncommitted diff into an unrelated 11-03 commit.**
- **Found during:** Attempting to stage and commit Task 2 (`git status --short` showed my own uncommitted `paper_execution.py` diff had vanished, already present with zero diff against a commit titled `test(11-03): ...`).
- **Root cause:** This repo is shared by parallel plan-executor agents per `config.json`'s `parallelization.enabled: true`. A concurrent Phase 11 Plan 03 executor ran a broad `git add -A`-style stage-and-commit while my Task 2 edit was uncommitted in the same working tree, bundling it into its own commit. This is the same failure mode already documented in STATE.md for the 10-04/10-05 concurrent-sibling-plan collision.
- **Resolution:** Per explicit guidance (not destructive git surgery -- rebasing/resetting a live concurrent agent's history risks destroying its work), Task 3 (the only still-uncommitted, at-risk file) was committed immediately by explicit filename. Shortly after, the concurrent agent's own commit was independently amended/rewritten (hash changed from `2bbefcb` to `052629c`) and no longer included my Task 2 diff -- the diff reappeared, unstaged, in my working tree. It was re-verified (full diff matched exactly my intended Task 2 change, 320 insertions / 69 deletions matching the earlier 389-line stat) and committed cleanly as `be1c366`.
- **Verification:** Re-ran `pytest tests/test_paper_preflight_query_count.py tests/test_paper_execution.py -q` against the final committed tree (`be1c366`) after the collision resolved -- 27/27 passed. One transient run mid-collision showed 11 errors under concurrent Postgres load from the other agent's simultaneous migration/test runs (not a regression -- every failing test passed individually and the full pair passed cleanly on immediate re-run).
- **No code or test content was lost, altered, or needs re-verification** -- this was a commit-attribution artifact only, now fully resolved with both tasks correctly committed under 11-01.

---

**Total deviations:** 0 code auto-fixes; 1 process deviation (git working-tree collision from parallel plan execution), fully resolved with no data loss.
**Impact on plan:** None on the delivered implementation or tests. Commit history for this plan is now correctly attributed (`6685daf`, `82bf9de`, `be1c366`).

## Issues Encountered

- Transient test failures (11 errors) observed once mid-session when running the full `test_paper_preflight_query_count.py` + `test_paper_execution.py` pair while a concurrent Phase 11 Plan 03 executor was simultaneously running its own migrations/tests against the same local Postgres instance. Every individually-failing test passed in isolation, and the full pair passed cleanly (27/27) on immediate re-run against the final committed tree -- consistent with the pre-existing documented Postgres-contention-under-parallel-load flake in STATE.md (distinct from, but same category as, the `pg_terminate_backend` teardown flake noted for 10-04).

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- PERF-01 is satisfied and durably proven: the auto-resolve paper-execution preflight issues exactly 2 SQL queries regardless of approved-candidate count, with a real integration test (`tests/test_paper_preflight_query_count.py`) pinning both the `<= 2` bound and the K=1-vs-K=25 non-scaling invariant.
- The new `tests/support/query_counter.py` harness is reusable for any future query-count-bound test in this codebase (e.g. PERF-02/PERF-03 or future plans), without needing to reinvent a counting mechanism.
- No blockers for the remaining Phase 11 plans (PERF-02, PERF-03), which were executing concurrently alongside this plan.

---
*Phase: 11-query-performance*
*Completed: 2026-07-14*

## Self-Check: PASSED

All created/modified files confirmed present on disk; all three task commits (`6685daf`, `82bf9de`, `be1c366`) confirmed present in git history.
