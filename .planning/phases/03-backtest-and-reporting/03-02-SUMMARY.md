---
phase: 03-backtest-and-reporting
plan: 02
subsystem: backtesting
tags: [backtest, simulation, cli, worker, postgres, deterministic-tests]

requires:
  - phase: 03-01
    provides: Backtest settings, run root, and normalized backtest artifact tables

provides:
  - Deterministic daily-bar backtest runner service
  - Next-session-open entry and exit fills with explicit fee and slippage handling
  - CLI and worker backtest entrypoints backed by the same service
  - Deterministic PostgreSQL-backed tests covering no-lookahead fills and duplicate-entry suppression

affects:
  - 03-03 reporting and export generation
  - future risk and paper-execution phase sequencing

tech-stack:
  added: []
  patterns:
    - Backtest runner treats `strategy.generate_signals()` as the strategy boundary and never reimplements indicator logic
    - Signals are evaluated at session close and converted into next-session-open fills on the following persisted session
    - Exits execute before entries at the next session open so slot-based rotation is deterministic
    - Duplicate LONG signals while a position is open are persisted but ignored for execution

key-files:
  created:
    - src/trading_platform/services/backtesting.py
    - scripts/run_backtest.py
    - tests/test_backtest_runner.py
  modified:
    - src/trading_platform/services/market_data_access.py
    - src/trading_platform/worker/__main__.py
    - Makefile

key-decisions:
  - "Used `strategy.generate_signals()` as a black-box boundary so backtesting composes on the Phase 2 strategy contract instead of duplicating SMA logic"
  - "Processed scheduled exits before entries on fill sessions so symbol rotation can free a slot deterministically at the open"
  - "Sized entries with equal-weight slots and whole shares, keeping the first simulator simple and explicit"
  - "Persisted ignored duplicate-entry and no-open-position exit decisions in signal metadata for later inspection"

requirements-completed:
  - REQ-05
  - REQ-06

duration: 17min
completed: 2026-03-14
---

# Phase 3 Plan 02: Deterministic Backtest Runner Summary

**Daily-session backtest runner wired through script and worker CLIs, persisting deterministic signal, trade, and equity artifacts with next-session-open fills and duplicate-entry suppression**

## Performance

- **Duration:** ~17 min
- **Started:** 2026-03-14T11:28:58Z
- **Completed:** 2026-03-14T11:45:44Z
- **Tasks:** 3
- **Files modified:** 6 (3 created, 3 modified)

## Accomplishments

- Added `src/trading_platform/services/backtesting.py` with a deterministic session-by-session simulator that reuses `strategy.generate_signals()` and persists `StrategyRun`, `BacktestSignal`, `BacktestTrade`, and `BacktestEquitySnapshot` artifacts
- Implemented explicit next-session-open fill handling with slippage and per-order commissions, plus equal-weight slot sizing and whole-share quantity rounding
- Added reusable market-data access helpers for persisted session ranges and one-session bar reads, keeping the runner on the persisted-data path instead of provider logic
- Exposed the runner through both `scripts/run_backtest.py` and `trading-platform-worker backtest`, and added a `make backtest` operator shortcut
- Added PostgreSQL-backed tests proving no-lookahead fills, duplicate LONG suppression while a position is open, and deterministic repeat-run output stability

## Task Commits

Each task was committed atomically:

1. **Task 1: Implement the deterministic daily-session backtest runner** - `6c4662d` (feat)
2. **Task 2: Wire the runner into the worker and CLI surfaces with duplicate-entry guards** - `697599e` (feat)
3. **Task 3: Prove determinism, no-lookahead fills, and dry-run compatibility** - `2b08442` (test)

## Files Created/Modified

- `src/trading_platform/services/backtesting.py` - Backtest orchestration, run lifecycle, fill handling, sizing, and artifact persistence
- `src/trading_platform/services/market_data_access.py` - Added persisted session range and single-session bar lookup helpers used by the runner
- `src/trading_platform/worker/__main__.py` - Added `backtest` subcommand and shared CLI surface
- `scripts/run_backtest.py` - Standalone backtest CLI returning JSON run summaries
- `Makefile` - Added `make backtest` target
- `tests/test_backtest_runner.py` - Seeded PostgreSQL integration tests for fill timing, duplicate suppression, and deterministic reruns

## Decisions Made

- **Close-to-next-open execution boundary:** Signals are always generated from a completed session and only filled on the following session open, which prevents lookahead.
- **Exit-first open processing:** Pending exits run before entries on the same fill session so the simulator can rotate out of one position and into another without ambiguous slot accounting.
- **Whole-share slot sizing:** Entries allocate up to one equal-weight slot using whole shares; this keeps the simulator simple and the quantity rule explicit in persisted assumptions.
- **Inspectable ignored actions:** Duplicate LONG signals and EXITs without an open position are persisted with action metadata instead of being silently discarded.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

- The first `tests/test_backtest_runner.py` pass exposed a fixture teardown issue: the cleanup query attempted to terminate every backend connected to the temporary database, including a superuser-owned background process. The fixture was narrowed to terminate only the current test user’s sessions, after which the suite passed cleanly.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- `03-03` can compute reports and exports entirely from persisted `strategy_runs`, `backtest_signals`, `backtest_trades`, and `backtest_equity_snapshots`.
- Run summaries already record assumptions, counts, and ending equity, which gives reporting a stable starting point before formal metrics persistence is added.
- The operator can execute the same deterministic runner through either the worker or the standalone script, so reporting can mirror that CLI-first workflow.

---

## Self-Check: PASSED

Key files exist on disk, task commits are present in git history, and the seeded runner verification passed.

*Phase: 03-backtest-and-reporting*
*Completed: 2026-03-14*
