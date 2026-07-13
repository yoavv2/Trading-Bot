---
phase: 09-reconciliation-rewrite
plan: 02
subsystem: reconciliation
tags: [dataclasses, enum, decimal, algorithms, testing, reconciliation]

# Dependency graph
requires:
  - phase: 09-reconciliation-rewrite (plan 09-01)
    provides: Typed Local*Snapshot dataclasses, closed ReconciliationFinding enum, hashable ReconciliationIdentity key
provides:
  - Pure match_snapshots() over typed snapshots -> tuple[Finding, ...]; indexed, O(n), no nested entity loops
  - Count-based linear-scaling benchmark proving comparisons(10x entities) stays within a small multiple of 10x (Phase 9 Success Criterion 2)
  - Order-matching precedence (client_order_id then broker_order_id) preserved from the pre-rewrite reconciliation.py
affects: [09-03-orchestrator, 09-04-correction-separation]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Single keyed-dict-per-side, then key-union iteration, replacing nested for-x-in-local/for-y-in-broker scans"
    - "Filter zero/flat entities out of both index maps before key-union, so they structurally cannot reach the finding loop"
    - "Comparison-count instrumentation returned alongside findings from internal _match_* helpers, enabling a count-based (not wall-clock) scaling benchmark"

key-files:
  created:
    - src/trading_platform/services/reconciliation_matcher.py
    - tests/test_reconciliation_matcher.py
  modified: []

key-decisions:
  - "side is part of the position identity key (inherited from 09-01's ReconciliationIdentity), so a sign-flipped position (e.g. local LONG 10, broker SHORT 5) resolves to two distinct keys, deliberately yielding a MISSING_BROKER + MISSING_LOCAL pair rather than a single QUANTITY_MISMATCH -- tested explicitly so this is a written contract, not an accident."
  - "Order state-mismatch mapping (broker ExecutionOrderStatus -> expected local lifecycle string) is hardcoded as a plain-string dict rather than importing OrderLifecycleState/OrderTransitionEventType from db.models, keeping the matcher module free of ORM imports per the plan's verification bar."
  - "paper_order_id/broker_order_id are populated on order and fill findings (looked up via the same index maps built for matching) so 09-03's ExecutionEvent persistence keeps the same attribution the pre-rewrite _build_findings provided."
  - "Task 1's RED test commit already included the Task 2 linear-scaling benchmark test (single test file, both belong together) -- see Deviations."

patterns-established:
  - "Matcher module boundary: TYPE_CHECKING-only import of Broker*Snapshot types (mirrors 09-01's pattern) keeps services.alpaca's httpx dependency out of the runtime import graph."
  - "Internal _match_positions/_match_orders/_match_fills each return (findings, comparison_count); the public match_snapshots() sums findings only, while tests/benchmarks call the internal helpers directly for count assertions."

requirements-completed: [RECON-06, RECON-08]

# Metrics
duration: ~20min
completed: 2026-07-13
---

# Phase 9: Reconciliation Rewrite — Plan 02 Summary

**Pure, indexed `match_snapshots()` replacing nested-scan matching: positions resolve through one `(symbol, account, side)`-keyed map, orders/fills through client_order_id/broker_order_id/broker_fill_id maps, and a count-based benchmark proves comparisons scale linearly (not quadratically) across a 10x entity-count increase.**

## Performance

- **Duration:** ~20 min
- **Completed:** 2026-07-13
- **Tasks:** 2
- **Files modified:** 2 (both created)

## Accomplishments
- `match_snapshots()` in `reconciliation_matcher.py`: pure function (no DB, no I/O, no mutation), positions/orders/fills each resolved via a single keyed-dict pass — zero nested `for x in local: for y in broker` loops anywhere (RECON-06).
- Flat (zero-quantity) positions filtered out of both index maps before key-union, so flat/flat pairs AND a flat position present on only one side produce zero findings (RECON-08), verified with three explicit tests.
- Sign-flip behavior (local LONG vs broker SHORT of the same symbol) deliberately yields a MISSING_BROKER + MISSING_LOCAL pair, not a QUANTITY_MISMATCH — written and tested as a contract per the plan's explicit instruction.
- Order matching preserves the existing "prefer client_order_id when a version-chain successor exists" precedence, verified against the same scenario shape as the pre-rewrite `test_reconciliation_prefers_client_order_id_when_version_chain_exists` DB test.
- Count-based linear-scaling benchmark: `comparisons(2000 positions) <= 1.5 * 10 * comparisons(200 positions)` and `comparisons <= 2 * total_entities` — passes for this indexed matcher and would fail for a nested-scan O(n²) implementation.
- 22 tests pass in `tests/test_reconciliation_matcher.py`; full repo suite (210 tests, Postgres-backed included) has no regressions.

## Task Commits

1. **Task 1: Pure indexed matcher emitting closed-enum findings** — `b09ade7` (test, RED) → `101f846` (feat, GREEN)
2. **Task 2: Linear-scaling benchmark** — folded into the same two commits above (see Deviations)

**Plan metadata:** (this commit)

## Files Created/Modified
- `src/trading_platform/services/reconciliation_matcher.py` — `match_snapshots()` public entrypoint plus internal `_match_positions`/`_match_orders`/`_match_fills` helpers, each returning `(findings, comparison_count)`; named tolerance constants; finding builders attributing `identity`/`paper_order_id`/`broker_order_id` (421 lines).
- `tests/test_reconciliation_matcher.py` — category-correctness tests for all 5 finding categories, RECON-08 flat-position tests, sign-flip test, client_order_id-precedence test, closed-enum assertion, and the count-based linear-scaling benchmark (396 lines, 22 tests).

## Decisions Made
See `key-decisions` in frontmatter above.

## Deviations from Plan

### Structural (no Rule 1-4 trigger, documented for transparency)

**1. Task 1 RED commit and Task 2's benchmark test were combined into a single test file/commit**
- **Found during:** Task 1 (writing failing tests)
- **Reasoning:** Both tasks modify the exact same file (`tests/test_reconciliation_matcher.py`); writing the benchmark test alongside the category-correctness tests in one pass was more coherent than reopening the same file for a second, mechanically-separate commit with no intervening implementation change.
- **Effect:** The `test(09-02...)` commit (`b09ade7`) contains all 22 tests including the benchmark; the `feat(09-02...)` commit (`101f846`) implements `reconciliation_matcher.py`, which makes all 22 (both tasks' scope) pass together. Both tasks' `<done>` criteria are independently verifiable and both pass — see Verification below.
- **Files affected:** `tests/test_reconciliation_matcher.py`
- **Commits:** `b09ade7`, `101f846`

---

**Total deviations:** 1 structural (commit-grouping only; no behavior, scope, or requirement change).
**Impact on plan:** None on functional scope — both tasks' done criteria are satisfied and independently verifiable in the test file; only the commit-granularity for Task 2 differs from a hypothetical separate no-op-implementation commit.

## Issues Encountered
None — implementation passed all 22 tests on the first write; no debugging iterations were needed.

## User Setup Required
None — no external service configuration required.

## Next Phase Readiness
- `reconciliation_matcher.match_snapshots()` is ready for 09-03 (orchestrator) to wire in place of `reconciliation.py`'s `_build_findings`, consuming the typed snapshots 09-01 established and the closed-enum findings this plan emits.
- `reconciliation.py`'s `_build_findings` and the old string-classified `ReconciliationFinding` dataclass are untouched, as scoped — this plan intentionally builds the standalone pure matcher only; the actual replacement/wiring is 09-03's responsibility.
- No blockers for 09-03.

---
*Phase: 09-reconciliation-rewrite*
*Completed: 2026-07-13*

## Self-Check: PASSED
- FOUND: src/trading_platform/services/reconciliation_matcher.py
- FOUND: tests/test_reconciliation_matcher.py
- FOUND: .planning/phases/09-reconciliation-rewrite/09-02-SUMMARY.md
- FOUND commit: b09ade7
- FOUND commit: 101f846
