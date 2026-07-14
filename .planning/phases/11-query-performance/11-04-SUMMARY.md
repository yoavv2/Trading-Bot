---
phase: 11-query-performance
plan: 04
subsystem: database
tags: [postgres, sqlalchemy, explain, performance, paper-fills]

# Dependency graph
requires:
  - phase: 11-query-performance
    provides: "11-03 identified the unconditional broker-fill history scan and proved the existing unique index"
provides:
  - "Batch-scoped broker-fill deduplication with empty-input short circuit and deterministic 1,000-ID chunks"
  - "Regression proof that lookup work depends on current distinct IDs rather than historical fill count"
  - "Realistic-volume EXPLAIN proof using uq_paper_fills_broker_fill_id with no paper_fills Seq Scan"
affects: [paper-execution, reconciliation, query-performance]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "External-ID membership checks deduplicate and sort the current batch, then query fixed-size IN-predicate chunks."
    - "Performance regressions assert SQL shape, bound-parameter count, and EXPLAIN plans instead of wall-clock time."

key-files:
  created: []
  modified:
    - src/trading_platform/services/paper_execution.py
    - tests/test_paper_execution.py
    - tests/test_query_index_usage.py
    - .planning/REQUIREMENTS.md
    - .planning/phases/11-query-performance/deferred-items.md

key-decisions:
  - "Use a fixed 1,000-ID dedup lookup chunk, comfortably below PostgreSQL's bind-parameter ceiling and above Alpaca's normal 500-fill page."
  - "Reuse uq_paper_fills_broker_fill_id; the selective query shape makes the existing unique index sufficient, so no model or migration change is needed."

patterns-established:
  - "Empty broker batches perform zero PaperFill dedup SELECTs."
  - "Current-batch IDs are distinct and sorted before bounded SQLAlchemy IN predicates are issued."

requirements-completed: [PERF-03]

# Metrics
duration: 57 min
completed: 2026-07-14
---

# Phase 11 Plan 04: Broker-Fill Dedup Gap Closure Summary

**Broker-fill synchronization now performs deterministic, batch-bounded indexed lookups instead of reading all historical fills, closing PERF-03 without a schema change.**

## Performance

- **Duration:** 57 min
- **Started:** 2026-07-14T11:04:19Z
- **Completed:** 2026-07-14T12:01:35Z
- **Tasks:** 3
- **Files modified:** 5

## Accomplishments

- Replaced the unconditional historical `paper_fills` ID read with distinct, sorted current-batch lookups split into fixed 1,000-ID chunks; empty input issues no dedup SELECT.
- Added deterministic regression coverage proving identical query/bind work at 1 versus 251 historical rows, exact 1,000/1 chunk splitting at 1,001 distinct IDs, and preserved historical/same-response duplicate plus `filled_at` behavior.
- Repurposed the former accepted-Seq-Scan test into realistic-volume proof that the selective statement uses `uq_paper_fills_broker_fill_id` and never Seq Scans `paper_fills`; all five critical query EXPLAIN tests pass.
- Marked PERF-03 Complete and appended 11-04 resolution evidence to the preserved 11-03 deferred diagnosis.

## Task Commits

Each task was committed atomically:

1. **Task 1: Batch-scoped, empty-safe, chunked dedup lookup** - `6b238c0` (perf)
2. **Task 2: Historical-independence and boundary regressions** - `8437484` (test)
3. **Task 3: Named-index EXPLAIN proof and PERF-03 closeout** - `19cc65c` (test)

## Files Created/Modified

- `src/trading_platform/services/paper_execution.py` - Adds the 1,000-ID chunk constant and selective current-batch fill-ID loader.
- `tests/test_paper_execution.py` - Covers empty input, historical-size independence, deterministic chunking, and duplicate/`filled_at` preservation.
- `tests/test_query_index_usage.py` - Replaces the old acceptable-Seq-Scan assertion with named unique-index proof.
- `.planning/REQUIREMENTS.md` - Marks PERF-03 checked and Complete after executable proof passed.
- `.planning/phases/11-query-performance/deferred-items.md` - Preserves the 11-03 diagnosis and appends the 11-04 resolution evidence.

## Decisions Made

- Selected 1,000 IDs per lookup chunk: twice the normal Alpaca page size while remaining conservative against PostgreSQL parameter limits.
- Kept the existing unique constraint/index as the final database integrity boundary; selectivity, not another index, was the missing performance property.
- Used SQLAlchemy expression predicates exclusively, so broker-controlled identifiers remain bound parameters rather than interpolated SQL.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

- The first focused regression run exposed a test-only whitespace assumption (`WHERE` followed a newline in emitted SQL); changed the assertion to a token-aware regex and the required rerun passed. Production behavior was unaffected.
- The first sandboxed PostgreSQL run could not reach local port 5432; reran the same required command with approved local-database access.

## User Setup Required

None - no external service configuration required.

## Verification Evidence

- `python -m pytest tests/test_paper_execution.py -q -x` — 25 passed before adding new regressions.
- `python -m pytest tests/test_paper_execution.py -q -k "fill and (dedup or empty or chunk or duplicate or lifecycle)"` — 5 passed, 24 deselected.
- `python -m pytest tests/test_query_index_usage.py tests/test_paper_execution.py -q` — 34 passed; includes all five critical-query EXPLAIN assertions.
- Source inspection found only the predicate-bearing `select(PaperFill.broker_fill_id).where(...)` statement.
- No schema, model, or migration file changed; 11-01 through 11-03 plans and summaries remain unchanged.

## Next Phase Readiness

- Phase 11 is complete at 4/4 plans with PERF-01, PERF-02, and PERF-03 all satisfied.
- Phase 12 may proceed when its paused-milestone gate and planning state allow it; this plan introduces no migration dependency.

## Self-Check: PASSED

- FOUND all five declared modified artifacts.
- FOUND task commits `6b238c0`, `8437484`, and `19cc65c`.
- PASSED every task acceptance criterion and the plan-level verification command.
- CONFIRMED no HIGH threat remains unmitigated: input is deduplicated/chunked, duplicates stay guarded, identifiers are bound, and empty input performs no lookup.

---
*Phase: 11-query-performance*
*Completed: 2026-07-14*
