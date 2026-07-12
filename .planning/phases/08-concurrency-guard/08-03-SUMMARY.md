---
phase: 08-concurrency-guard
plan: 03
subsystem: database
tags: [postgresql, sqlalchemy, strategy-run, execution-event, audit]

# Dependency graph
requires:
  - phase: 08-01
    provides: STALE StrategyRunStatus enum value + externalized stale_run_timeout_minutes setting, consumed as the `timeout_minutes` parameter and `STALE` target status
provides:
  - "find_stale_runs(session, timeout_minutes=) single-query detector for running paper-execution runs past the timeout (LOCK-04)"
  - "reclaim_stale_runs(session, strategy_public_id=, session_date=, timeout_minutes=, reclaiming_run_id=) tuple-scoped STALE marking with one ExecutionEvent audit row per reclaimed run, idempotent, caller-owned transaction (LOCK-05)"
affects: [08-04, 08-05]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "session_date tuple-scoping done in Python against the already-fetched candidate rows (parameters_snapshot/result_summary as_of_session JSON field), not in SQL, because strategy_runs has no session_date column -- documented inline per the Phase 8 CONTEXT decision to avoid adding a column this phase"
    - "Reuses Phase 7's ExecutionEvent audit channel (event_type='paper_run_reclaimed_stale', severity='warning', blocks_execution=False) rather than inventing a parallel audit mechanism"

key-files:
  created:
    - src/trading_platform/services/stale_runs.py
    - tests/test_stale_run_reclaim.py
  modified: []

key-decisions:
  - "reclaim_stale_runs() flushes but never commits -- the caller (08-04's lock-acquisition entrypoint) owns the transaction boundary, consistent with the plan's explicit instruction."
  - "Detection query filters on status=RUNNING, run_type=PAPER_EXECUTION, and started_at < cutoff only -- session_date/tuple scoping is reclaim-specific, not part of the single-query LOCK-04 detector, matching the plan's split of responsibilities between the two functions."

requirements-completed: [LOCK-04, LOCK-05]

# Metrics
duration: ~10min
completed: 2026-07-12
---

# Phase 8 Plan 03: Stale-Run Detection + Reclaim Summary

**`find_stale_runs()` single-query detector plus `reclaim_stale_runs()` tuple-scoped, idempotent STALE marking with a durable per-row `ExecutionEvent` audit trail, reusing Phase 7's StrategyRun/ExecutionEvent pattern.**

## Performance

- **Duration:** ~10 min
- **Tasks:** 2
- **Files modified:** 2 (both newly created)

## Accomplishments
- `find_stale_runs(session, *, timeout_minutes)`: one `select(StrategyRun)` query returning every `RUNNING` `PAPER_EXECUTION` run whose `started_at` is older than `now() - timeout_minutes`; runs inside the window or in a terminal status are excluded.
- `reclaim_stale_runs(session, *, strategy_public_id, session_date, timeout_minutes, reclaiming_run_id=None)`: joins `Strategy` to scope by public strategy id, filters the same running-past-timeout predicate, then matches `session_date` in Python against `parameters_snapshot`/`result_summary`'s `as_of_session` JSON field (no first-class column exists). Flips every match to `STALE`, sets `completed_at`, and inserts one `ExecutionEvent` (`event_type="paper_run_reclaimed_stale"`, `severity="warning"`, `blocks_execution=False`) per reclaimed row. Returns reclaimed run ids; does not commit.
- 3 Postgres integration tests: detection correctly isolates the one past-timeout running row from a fresh running row and an old-but-succeeded row; reclaim flips both of two past-threshold rows for a tuple to STALE (leaving a fresh same-tuple row untouched) and writes exactly two matching audit events; a second reclaim call on the now-STALE rows is a no-op (idempotency).

## Task Commits

Each task was committed atomically:

1. **Task 1: find_stale_runs() single-query detection** - `9ae052d` (feat)
2. **Task 2: reclaim_stale_runs() tuple-scoped STALE marking + audit** - `1a806c0` (feat)

## Files Created/Modified
- `src/trading_platform/services/stale_runs.py` - `find_stale_runs()` + `reclaim_stale_runs()` (115 lines)
- `tests/test_stale_run_reclaim.py` - self-provisioned temp-Postgres integration tests, fixture pattern matching `tests/test_paper_execution.py`/`tests/test_concurrency_guard.py` (281 lines)

## Decisions Made
- `reclaim_stale_runs()` intentionally does not commit, per the plan's explicit instruction that the caller (08-04's lock-guarded entrypoint) owns the transaction boundary.
- The tuple's `session_date` match is done in Python over already-selected rows (not folded into the SQL `WHERE`) since `strategy_runs` has no `session_date` column and the Phase 8 CONTEXT authorizes only the `STALE` enum migration this phase — an inline comment in `stale_runs.py` documents this deliberately.

## Deviations from Plan

None — plan executed exactly as written. Both tasks' `<verify>` commands (`pytest tests/test_stale_run_reclaim.py -x -k "detect"` after Task 1; `pytest tests/test_stale_run_reclaim.py -x` after Task 2) passed on first run with no fixes required. Full repo suite (`pytest tests/ -q`) — 164 passed, no regressions.

Note: implementation and test files were found already fully written but uncommitted in the working tree at session start (consistent with prior work not yet committed). Correctness was independently re-verified against the plan's task-by-task `<verify>` commands before committing, and the single combined file was split into two atomic task-scoped commits (Task 1 content first, Task 2 content added second) rather than committed as one lump, to preserve the plan's per-task commit granularity.

## Issues Encountered
None.

## User Setup Required
None - no external service configuration required. Tests require a locally reachable PostgreSQL instance (already required by other integration tests in this repo); the local Postgres@14 instance was already running and reachable.

## Next Phase Readiness
`find_stale_runs()` and `reclaim_stale_runs()` are ready to be wired into `run_paper_order_submission`'s lock-guarded entrypoint in 08-04, per the Phase 8 context's "reclaim after lock acquisition, before continuing" flow. Not yet wired into any caller — that is explicitly 08-04's scope. No blockers.

---
*Phase: 08-concurrency-guard*
*Completed: 2026-07-12*

## Self-Check: PASSED

- FOUND: src/trading_platform/services/stale_runs.py
- FOUND: tests/test_stale_run_reclaim.py
- FOUND: 9ae052d (Task 1 commit)
- FOUND: 1a806c0 (Task 2 commit)
