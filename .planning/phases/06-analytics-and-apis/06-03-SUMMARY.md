---
phase: 06-analytics-and-apis
plan: 03
subsystem: operator-controls
tags: [operator-control, kill-switch, observability, worker, cli, postgres]

requires:
  - phase: 06-02
    provides: Shared operator reads, analytics summaries, and versioned read APIs
provides:
  - Persisted operator enable or disable controls with durable audit runs and execution events
  - Fail-closed paper execution and paper-session orchestration when a strategy is disabled
  - Operator status and control CLI or worker surfaces backed by shared read services
affects:
  - phase 06 milestone completion and MVP readiness verdict
  - future dashboard surfaces that need trusted control and blocked-state visibility

tech-stack:
  added: []
  patterns:
    - Strategy control actions persist under `strategy_runs` with `run_type=operator_control`
    - Blocked paper execution attempts are queryable through `execution_events` instead of only logs
    - Status output composes existing read services instead of introducing route-local or script-local SQL

key-files:
  created:
    - src/trading_platform/services/operator_controls.py
    - src/trading_platform/services/operator_status.py
    - scripts/operator_control.py
    - scripts/operator_status.py
    - alembic/versions/0012_phase6_operator_controls.py
    - tests/test_operator_controls.py
  modified:
    - src/trading_platform/services/paper_execution.py
    - src/trading_platform/services/reconciliation.py
    - src/trading_platform/services/bootstrap.py
    - src/trading_platform/core/logging.py
    - src/trading_platform/worker/__main__.py
    - src/trading_platform/db/models/strategy_run.py
    - tests/test_paper_execution.py
    - tests/test_db_migrations.py

key-decisions:
  - "Persisted strategy status is authoritative and `ensure_strategy_record()` now preserves operator-changed status instead of resetting it to active"
  - "Blocked paper execution attempts are stored as failed `paper_execution` runs with normalized `execution_events` so the operator can audit kill-switch outcomes later"
  - "Operator status output reuses the shared operator-read service and derives a latest paper-session outcome instead of duplicating query logic"

patterns-established:
  - "Structured log helpers standardize `strategy_id`, `run_id`, `session_date`, `strategy_status`, and `blocked_reason` context across control and execution paths"
  - "Control and status commands are available both as standalone scripts and as worker subcommands"
  - "DB-backed tests cover status transitions, blocked execution persistence, status reporting, and Alembic enum head coverage"

requirements-completed:
  - REQ-10
  - REQ-06

duration: 3h
completed: 2026-03-15
---

# Phase 06 Plan 03: Operator Controls and Observability Summary

**A real kill switch, durable operator audit trail, and a daily-use status surface**

## Performance

- **Duration:** 3h
- **Started:** 2026-03-15T04:37:08Z
- **Completed:** 2026-03-15T07:33:22Z
- **Tasks:** 3
- **Files modified:** 14

## Accomplishments

- Added a persisted operator-control service that enables or disables a strategy through `Strategy.status`, records `operator_control` runs, and writes normalized audit events.
- Fixed `ensure_strategy_record()` so it preserves an operator-disabled strategy instead of silently resetting persisted status back to `active`.
- Updated paper-order submission and paper-session orchestration to fail closed when a strategy is disabled, and to persist blocked attempts as durable `paper_execution` runs plus `execution_events`.
- Added operator status reporting that reuses the shared read services to summarize current control state, latest account snapshot, latest paper-session outcome, recent blocking events, and recent failed runs.
- Added standalone and worker CLI surfaces for operator control and operator status, plus richer structured log helpers for execution and control flows.
- Extended DB-backed verification for control transitions, blocked execution, operator status output, paper-session gating, and Alembic head coverage.

## Task Commits

1. **Tasks 1-3: Add persisted controls, kill-switch enforcement, and status surfaces** - `0c1800f` (feat)

## Files Created/Modified

- `src/trading_platform/services/operator_controls.py` - Persisted enable or disable actions, control-state reads, and control-report rendering.
- `src/trading_platform/services/operator_status.py` - Shared status summary composition and rendering built on top of operator reads.
- `src/trading_platform/services/paper_execution.py` - Disabled-strategy gating, durable blocked-execution records, and richer structured logs.
- `src/trading_platform/services/reconciliation.py` - Structured reconciliation completion and failure logs.
- `src/trading_platform/services/bootstrap.py` - Preserve persisted operator status when refreshing strategy metadata.
- `src/trading_platform/core/logging.py` - Reusable structured context builders for logs.
- `src/trading_platform/worker/__main__.py` - New `operator-control` and `operator-status` worker commands.
- `scripts/operator_control.py` - Standalone operator control CLI.
- `scripts/operator_status.py` - Standalone operator status CLI.
- `alembic/versions/0012_phase6_operator_controls.py` - Alembic head update for `operator_control` strategy runs.
- `tests/test_operator_controls.py` - DB-backed control, blocked-execution, and status-report verification.
- `tests/test_paper_execution.py` - Disabled-strategy fail-closed coverage and safer temporary-database teardown.
- `tests/test_db_migrations.py` - Alembic head assertion for the new `strategy_run_type` enum value.

## Decisions Made

- Treated persisted strategy status as authoritative platform state and fixed the bootstrap path so metadata refresh does not erase operator control decisions.
- Modeled blocked kill-switch outcomes as failed paper-execution runs with explicit `blocked_reason` metadata because the platform does not yet have a dedicated blocked run state.
- Kept status reporting service-backed and local-first by composing the Phase 6 read surfaces rather than adding new direct SQL in scripts or worker entrypoints.

## Deviations from Plan

- None. The implementation matched the planned control persistence model, kill-switch behavior, and shared status-surface approach.

## Issues Encountered

- Local PostgreSQL-backed verification still required elevated sandbox access because sandboxed TCP connections to `localhost:5432` are not permitted in this environment.

## User Setup Required

- None for plan completion. Live control and status commands continue to rely on the configured local PostgreSQL database and the existing runtime configuration.

## Next Phase Readiness

- Phase 06 is complete. The milestone is ready for audit and closure because the platform now has analytics, inspection APIs, a persisted kill switch, and operator-visible blocked-state reporting.

## Self-Check: PASSED

- Verified `PYTHONPATH=src .venv/bin/pytest tests/test_operator_controls.py tests/test_paper_execution.py tests/test_api_reads.py tests/test_db_migrations.py -q` passed.
- Verified `PYTHONPATH=src .venv/bin/python scripts/operator_control.py --help` passed.
- Verified `PYTHONPATH=src .venv/bin/python scripts/operator_status.py --help` passed.
- Verified `PYTHONPATH=src .venv/bin/python -m trading_platform.worker operator-status --help` passed.
- Verified disabled strategies block new paper execution before broker reads or submissions and persist the blocked reason durably.

---
*Phase: 06-analytics-and-apis*
*Completed: 2026-03-15*
