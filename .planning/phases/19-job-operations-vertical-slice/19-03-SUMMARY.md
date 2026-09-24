---
phase: 19-job-operations-vertical-slice
plan: 03
subsystem: api
tags: [job-strategy-run-link, sqlalchemy, fastapi, job-reads, backtesting]

# Dependency graph
requires:
  - phase: 19-01
    provides: "strategy_runs.job_id nullable/unique FK -> jobs.id (migration 0020)"
provides:
  - "run_backtest(..., job_id=...) writes strategy_runs.job_id in the same transaction that creates the run (D-02)"
  - "JobReadService.get_job_detail exposes resources[] derived from the job_id FK, with closed JobResourceKind (D-04/D-05)"
  - "Run detail/list serialization exposes job_id (D-07 backend half)"
affects: [job-operations-vertical-slice-console-plans, phase-20-operation-migration]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Opaque job_id keyword threaded through a domain service without importing the Job framework (services/ stays JOB-04-clean)"
    - "resources[] derived at read time from a single FK query, never stored/inferred from logs or timestamps"

key-files:
  created:
    - tests/test_backtest_job_link.py
    - tests/test_job_resources_read.py
  modified:
    - src/trading_platform/services/backtesting.py
    - src/trading_platform/services/job_reads.py
    - src/trading_platform/services/operator_reads.py

key-decisions:
  - "job_id kept as an opaque uuid.UUID | None keyword on run_backtest/_create_backtest_run, set on the StrategyRun(...) constructor before session.add/flush -- no follow-up UPDATE, per D-02"
  - "JobResourceKind lives in services/job_reads.py as a StrEnum with exactly one member (strategy_run); resources[] is built from an unfiltered select(StrategyRun).where(StrategyRun.job_id == job_uuid) so it is never gated on Job status (D-05/D-13 read side)"
  - "OPS-01 and JOBUI-02 left Pending in REQUIREMENTS.md -- this plan ships only the service/API layer; OPS-01 needs the registered backtest Job handler + worker wiring, and JOBUI-02 needs the console Job-detail screen, neither of which is in this plan's scope"

patterns-established:
  - "Reuse tests/test_backtest_runner.py's migrated_backtest_db/strategy_config_override/_seed_market_data fixtures directly via import rather than duplicating the DB-lifecycle harness"

requirements-completed: []

# Metrics
duration: 25min
completed: 2026-09-24
---

# Phase 19 Plan 03: Job <-> StrategyRun Link (backend) Summary

**`run_backtest` now threads an opaque `job_id` into the run-creation transaction, and both `GET /api/v1/jobs/{id}` (`resources[]`, closed `JobResourceKind`) and run-detail (`job_id`) expose the persisted link in both directions.**

## Performance

- **Duration:** ~25 min
- **Tasks:** 2 completed
- **Files modified:** 5 (3 source, 2 new test files)

## Accomplishments

- `run_backtest`/`_create_backtest_run` accept `job_id: uuid.UUID | None = None` and write it on `StrategyRun(...)` inside the same `session_scope` block that creates the run (D-02) -- no follow-up UPDATE, verified by a probe that patches `_execute_backtest_run` to read the row mid-transaction and observes `status=RUNNING` with `job_id` already set.
- `_serialize_run_summary` in `operator_reads.py` now includes `job_id` (UUID string or `null`), satisfying the backend half of D-07 for both run detail and run list.
- `JobReadService.get_job_detail` gains a `resources[]` array built from a single `StrategyRun.job_id` query, with a new closed `JobResourceKind(StrEnum)` (`{"strategy_run"}`) in `services/job_reads.py`. `resources[]` is not filtered by Job status, so it stays visible while RUNNING and survives every terminal state (SUCCEEDED, FAILED, CANCELLED) as long as a run is linked -- pinned by a parametrized test including the CANCELLED-Job/SUCCEEDED-run case (D-13 read side).

## Task Commits

1. **Task 1: Thread job_id through run_backtest and expose job_id on run reads** - `51614c6` (feat)
2. **Task 2: resources[] on Job detail with closed JobResourceKind** - `eedee63` (feat)

**Plan metadata:** (this commit)

## Files Created/Modified

- `src/trading_platform/services/backtesting.py` - `run_backtest`/`_create_backtest_run` accept and persist `job_id`
- `src/trading_platform/services/operator_reads.py` - `_serialize_run_summary` adds `job_id`
- `src/trading_platform/services/job_reads.py` - `JobResourceKind` enum + `resources[]` in `get_job_detail`
- `tests/test_backtest_job_link.py` - creation-time linkage probe, null-when-absent case, run-detail exposure (3 tests)
- `tests/test_job_resources_read.py` - enum closure, empty/visible/terminal-state cases (parametrized), HTTP shape (7 tests)

## Decisions Made

- `job_id` stays a plain, opaque `uuid.UUID | None` keyword argument on `run_backtest`/`_create_backtest_run` -- no import from `trading_platform.jobs` anywhere in `backtesting.py`, keeping `tests/test_job_import_boundary.py` (JOB-04) satisfied automatically. One docstring wording was adjusted mid-task specifically to avoid the literal substring `trading_platform.jobs` appearing in the file, since the plan's own acceptance criteria greps for that exact string returning zero matches.
- `resources[]`'s single query (`select(StrategyRun).where(StrategyRun.job_id == job_uuid)`) mirrors the existing `dependencies`/`blocking_dependencies` derived-at-read-time pattern already in `get_job_detail`, keeping the method's shape consistent rather than introducing a separate helper.
- REQUIREMENTS.md: both `OPS-01` and `JOBUI-02` remain Pending, consistent with the 19-01 precedent -- this plan delivers only the service/API layer of the Job<->StrategyRun link. `OPS-01`'s literal "proven end-to-end Console -> HTTP -> Job -> worker -> existing backtest service" needs the registered `backtest` Job handler and worker wiring (not yet built); `JOBUI-02`'s literal "Operator can view" needs the console Job-detail screen consuming `resources[]` (not yet built). Marking either complete now would overclaim.

## Deviations from Plan

None - plan executed exactly as written. One micro-adjustment: the `run_backtest` docstring was worded to avoid literally containing the string `trading_platform.jobs` so the plan's own acceptance-criteria grep (`grep -n "trading_platform.jobs" src/trading_platform/services/backtesting.py` returns no matches) stays true; this is documentation-only, not a Rule 1-4 deviation.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Job <-> StrategyRun link is now persisted and readable from both the Job side (`resources[]`) and the run side (`job_id`) -- ready for the backtest Job handler (a later 19 plan) to call `run_backtest(..., job_id=context.job_id, trigger_source="job")` and for the console Job-detail screen to render `resources[]` via the D-04 `kind -> route` lookup map.
- `docs(19-03): complete ...` metadata commit and STATE.md/ROADMAP.md updates follow this summary.
- No blockers identified.

---
*Phase: 19-job-operations-vertical-slice*
*Completed: 2026-09-24*
