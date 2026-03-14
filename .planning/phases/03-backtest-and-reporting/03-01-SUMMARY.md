---
phase: 03-backtest-and-reporting
plan: 01
subsystem: database
tags: [backtest, postgres, alembic, config, persistence, strategy-runs]

requires:
  - phase: 02-03
    provides: TrendFollowingDailyV1 signal generation and typed SignalBatch output

provides:
  - Typed backtest settings block under application settings
  - `strategy_runs` support for `backtest` run_type with `parameters_snapshot`
  - Normalized `backtest_signals`, `backtest_trades`, and `backtest_equity_snapshots` tables
  - Alembic revision `0004_phase3_btf` for Phase 3 foundation
  - Regression coverage for migration head and dry-run compatibility

affects:
  - 03-02 deterministic runner
  - 03-03 reporting and exports
  - future paper-execution run lineage

tech-stack:
  added: []
  patterns:
    - Backtest artifacts hang off `strategy_runs` rather than introducing a second run root
    - Backtest assumptions load through typed settings and persist via `parameters_snapshot`
    - Research outputs stay normalized in dedicated tables instead of opaque JSON blobs

key-files:
  created:
    - alembic/versions/0004_phase3_backtest_foundation.py
    - src/trading_platform/db/models/backtest_signal.py
    - src/trading_platform/db/models/backtest_trade.py
    - src/trading_platform/db/models/backtest_equity_snapshot.py
  modified:
    - config/app.yaml
    - src/trading_platform/core/settings.py
    - src/trading_platform/db/models/strategy_run.py
    - src/trading_platform/db/models/__init__.py
    - tests/test_db_migrations.py
    - tests/test_dry_run.py

key-decisions:
  - "Reused `strategy_runs` as the single run root and added `run_type=backtest` instead of creating a parallel backtest-run table"
  - "Captured run assumptions in `parameters_snapshot` so later reports can tie metrics back to the exact execution inputs"
  - "Stored signals, trades, and equity history in separate normalized tables for auditability and deterministic reporting"

requirements-completed:
  - REQ-05
  - REQ-06
  - REQ-11

duration: 12min
completed: 2026-03-14
---

# Phase 3 Plan 01: Backtest Persistence Foundation Summary

**Typed backtest settings plus a shared `strategy_runs` root with normalized signal, trade, and equity tables, all landed behind an Alembic revision that preserves the existing dry-run flow**

## Performance

- **Duration:** ~12 min
- **Started:** 2026-03-14T11:17:00Z
- **Completed:** 2026-03-14T11:28:57Z
- **Tasks:** 3
- **Files modified:** 10 (4 created, 6 modified)

## Accomplishments

- Added a typed `backtest` configuration surface covering initial capital, fill strategy, allocation model, fee assumptions, and slippage assumptions through the existing YAML-plus-settings path
- Extended `strategy_runs` with a `backtest` run type and `parameters_snapshot` JSON so all future Phase 3 artifacts share the existing run lineage model
- Added normalized backtest artifact tables for signals, simulated trades, and session-level equity snapshots, all keyed to one run record
- Landed Alembic revision `0004_phase3_btf` to create the new schema and safely downgrade the enum-backed run type when needed
- Locked the foundation with migration assertions and a dry-run regression assertion proving the shared run-model changes did not break existing behavior

## Task Commits

Each task was committed atomically:

1. **Task 1: Add typed backtest settings and extend the shared run model safely** - `3cde2a4` (feat)
2. **Task 2: Add normalized backtest artifact tables and the Phase 3 migration** - `b0aff48` (feat)
3. **Task 3: Lock the foundation with migration and dry-run regression coverage** - `67ea9a2` (test)

## Files Created/Modified

- `config/app.yaml` - Added explicit backtest defaults for capital, fill timing, allocation, fees, and slippage
- `src/trading_platform/core/settings.py` - Added typed backtest settings models and environment override support
- `src/trading_platform/db/models/strategy_run.py` - Added `backtest` run type, `parameters_snapshot`, and backtest artifact relationships
- `src/trading_platform/db/models/backtest_signal.py` - Normalized signal-event model keyed by run, symbol, and session
- `src/trading_platform/db/models/backtest_trade.py` - Simulated trade ledger model with entry/exit fills, costs, and PnL
- `src/trading_platform/db/models/backtest_equity_snapshot.py` - Session-level equity and exposure history model
- `src/trading_platform/db/models/__init__.py` - Exported the new backtest ORM models
- `alembic/versions/0004_phase3_backtest_foundation.py` - Added Phase 3 schema migration and downgrade path
- `tests/test_db_migrations.py` - Asserted enum expansion, `parameters_snapshot`, and the new backtest tables/constraints
- `tests/test_dry_run.py` - Verified the legacy dry-run path still persists an empty parameters snapshot

## Decisions Made

- **One run root, multiple run types:** `strategy_runs` remains the canonical root table, which keeps later query paths and reporting surfaces consistent across dry runs and backtests.
- **Assumptions are data, not hidden config:** `parameters_snapshot` was added now so execution and reporting can both point at the same persisted assumption payload.
- **Normalized artifacts over JSON blobs:** Separate signal, trade, and equity tables make audit queries and deterministic report generation straightforward without unpacking nested JSON.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

- Local PostgreSQL access is blocked inside the default sandbox, so the migration and regression suite had to be rerun with elevated permissions. Once rerun against the local database, `PYTHONPATH=src .venv/bin/pytest tests/test_db_migrations.py tests/test_dry_run.py -q` passed cleanly.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- `03-02` can now create one backtest `strategy_runs` record and persist all downstream artifacts under it without schema churn.
- Typed settings already expose the simulator assumptions the runner must honor, so the execution path can stay config-driven instead of hard-coding research constants.
- Reporting work in `03-03` can read stable, normalized tables instead of reconstructing state from logs or in-memory objects.

---

## Self-Check: PASSED

Key files exist on disk and the three task commits are present in git history.

*Phase: 03-backtest-and-reporting*
*Completed: 2026-03-14*
