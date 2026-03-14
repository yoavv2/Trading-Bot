---
phase: 04-risk-and-portfolio
plan: 01
subsystem: database
tags: [portfolio, sizing, postgres, alembic, risk, state]

requires:
  - phase: 03-03
    provides: Persisted backtest artifacts, typed settings conventions, and CLI-first service patterns reused by the live portfolio foundation
provides:
  - Typed portfolio runtime settings for allocation caps and stale-data tolerance
  - Normalized live `positions` and `account_snapshots` tables
  - A portfolio service that loads persisted state and computes deterministic whole-share sizing
affects:
  - phase 04 risk pipeline execution
  - phase 05 paper execution

tech-stack:
  added: []
  patterns:
    - Live portfolio state is stored separately from phase 3 backtest artifacts
    - Portfolio exposure is marked from persisted daily bars rather than broker-specific code
    - Whole-share sizing is bounded by typed account-level allocation caps

key-files:
  created:
    - src/trading_platform/services/portfolio.py
    - src/trading_platform/db/models/position.py
    - src/trading_platform/db/models/account_snapshot.py
    - alembic/versions/0006_phase4_portfolio_state.py
    - tests/test_portfolio_service.py
  modified:
    - src/trading_platform/core/settings.py
    - config/app.yaml
    - src/trading_platform/db/models/__init__.py
    - tests/test_db_migrations.py

key-decisions:
  - "Interpreted `risk_per_trade` as a v1 notional budget fraction for deterministic sizing because the strategy does not yet define stop-distance-based risk"
  - "Live portfolio positions and account snapshots remain separate from backtest trades and equity snapshots"
  - "Portfolio valuation composes on persisted daily bars and falls back to entry price when a mark is unavailable"

patterns-established:
  - "Portfolio service: typed settings plus database-backed state loading for risk evaluation"
  - "Live-state persistence: strategy-linked positions and account snapshots anchored in PostgreSQL"

requirements-completed:
  - REQ-07
  - REQ-06
  - REQ-11

duration: 3min
completed: 2026-03-14
---

# Phase 4 Plan 01: Portfolio State and Sizing Summary

**Typed live portfolio settings, normalized PostgreSQL state, and deterministic whole-share sizing for the risk engine**

## Performance

- **Duration:** ~3 min
- **Started:** 2026-03-14T17:05:18Z
- **Completed:** 2026-03-14T17:08:13Z
- **Tasks:** 3
- **Files modified:** 9

## Accomplishments
- Added typed portfolio runtime settings for starting cash, allocation caps, and stale-data tolerance in the shared settings model
- Created live `positions` and `account_snapshots` models plus Alembic revision `0006_phase4_port`
- Implemented a portfolio service that computes deterministic whole-share entry sizing, loads persisted state, marks open positions from daily bars, and records account snapshots

## Task Commits

Each task was committed atomically:

1. **Task 1: Add typed Phase 4 settings and a deterministic portfolio service surface** - `56bfaac` (feat)
2. **Task 2: Add normalized portfolio-state tables and the Phase 4 migration** - `b649fb9` (feat)
3. **Task 3: Lock sizing and exposure accounting with deterministic tests** - `ecc1723` (feat)

## Files Created/Modified

- `src/trading_platform/services/portfolio.py` - Portfolio dataclasses, sizing logic, DB-backed state loading, and snapshot recording
- `src/trading_platform/db/models/position.py` - Normalized live position model for future paper execution
- `src/trading_platform/db/models/account_snapshot.py` - Persisted account cash and exposure snapshots
- `alembic/versions/0006_phase4_portfolio_state.py` - Phase 4 portfolio-state schema migration
- `src/trading_platform/core/settings.py` - Typed portfolio runtime settings
- `config/app.yaml` - Default portfolio configuration block
- `tests/test_portfolio_service.py` - Unit and DB-backed portfolio service coverage
- `tests/test_db_migrations.py` - Migration assertions for the Phase 4 portfolio tables

## Decisions Made

- Treated `risk_per_trade` as a deterministic notional budget fraction for v1 sizing because there is no stop-loss distance in the current strategy contract.
- Kept live portfolio state isolated from Phase 3 research artifacts to avoid overloading backtest tables with live-trading semantics.
- Marked open positions from persisted daily bars so the live portfolio service stays broker-agnostic until Phase 5.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

- The full verification command initially failed inside the sandbox because the local PostgreSQL instance was not reachable over sandboxed TCP. The same planned command passed once rerun against the local database outside the sandbox.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- The risk pipeline can now depend on typed portfolio settings, durable open-position state, and persisted account snapshots.
- Phase 5 can later reuse the same live-state tables instead of inferring portfolio state from backtest artifacts.

---
*Phase: 04-risk-and-portfolio*
*Completed: 2026-03-14*
