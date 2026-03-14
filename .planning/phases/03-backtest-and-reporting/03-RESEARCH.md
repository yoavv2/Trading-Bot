# Phase 3: Backtest and Reporting - Research

**Researched:** 2026-03-14
**Domain:** Deterministic daily-bar backtesting, persisted research outputs, and CLI-first reporting for `TrendFollowingDailyV1`
**Confidence:** MEDIUM-HIGH

<planning_inputs>
## Planning Inputs

### Available Context
- No `03-CONTEXT.md` exists for this phase. Planning uses `.planning/PROJECT.md`, `.planning/ROADMAP.md`, `.planning/STATE.md`, the completed Phase 2 summaries, and the current codebase.
- The current platform already has the key ingredients this phase should compose on rather than replace: typed settings, SQLAlchemy + Alembic persistence, persisted `market_sessions` and `daily_bars`, a session-aware market-data access layer, a strategy registry, and deterministic `SignalBatch` output from `TrendFollowingDailyV1`.
- `.planning/REQUIREMENTS.md` is not present in this repo, so requirement mapping must be derived from `.planning/ROADMAP.md` and `.planning/PROJECT.md`.

### Locked Decisions From Project State
- The platform remains local-first, PostgreSQL-backed, and CLI-first.
- `TrendFollowingDailyV1` signal generation is already implemented and must remain the source of truth for the first backtest runner.
- The first backtest implementation must stay deterministic, auditable, and daily-bar-based. No intraday simulation, broker integration, or paper-order concepts belong here.
- Phase 4 owns the mandatory risk pipeline and portfolio guardrails. Phase 3 should not pre-build broker-style order gating or attempt to replace that future work.
- Reports should be inspectable without raw log spelunking.

### Claude's Discretion
- Whether to extend `strategy_runs` as the canonical root record for backtests or introduce a separate root table.
- The simplest credible fill and sizing assumptions for the first daily-bar simulator.
- Which run-level metrics should be persisted immediately versus computed in a report layer.
- Whether report generation should write markdown, JSON, CSV, or all three in the first implementation.

</planning_inputs>

<research_summary>
## Summary

Phase 3 should stay narrow and compositional:

1. Reuse the existing `strategy.generate_signals(db_session, as_of)` boundary instead of inventing a second strategy API for backtests.
2. Treat `strategy_runs` as the root persisted run record and add child tables for the backtest-specific artifacts that Phase 3 must inspect later.
3. Use explicit, versioned simulation assumptions that avoid lookahead:
   - evaluate signals on session `T`
   - fill actionable entries and exits on the next persisted session's open
   - use a simple, deterministic allocation model that does not pretend to be the future Phase 4 risk engine
4. Split the phase into three sequential plans:
   - `03-01`: backtest settings and persistence foundation
   - `03-02`: deterministic runner and execution flow
   - `03-03`: metrics summaries, reports, and exports

**Primary recommendation:** Implement a lightweight internal simulator around the current strategy signal boundary, persist every run under `strategy_runs` plus backtest child tables, use next-session-open fills with configurable fee/slippage assumptions, and expose research inspection through CLI-generated reports and exports rather than HTTP endpoints.

</research_summary>

<codebase_findings>
## Codebase Findings

### Existing Reusable Assets
- `src/trading_platform/strategies/trend_following_daily/strategy.py` already emits deterministic typed signals from persisted daily bars.
- `src/trading_platform/services/market_data_access.py` already encapsulates session-aware bar reads and persisted session lookups.
- `src/trading_platform/db/models/strategy_run.py` already provides a persisted run root with lifecycle state and summary JSON.
- `src/trading_platform/services/bootstrap.py` already shows the repo's preferred pattern for run creation, lifecycle updates, and CLI-triggered persistence.

### Current Gaps Blocking Phase 3
- There is no service that iterates session-by-session across a date range and turns `SignalBatch` output into simulated trades or equity changes.
- There are no persisted backtest artifacts beyond the generic `strategy_runs` record.
- The current market-data access layer exposes lookback-window helpers, but not the higher-level session iteration and "next session after X" utilities a backtest runner will need.
- `src/trading_platform/services/analytics.py` is still a placeholder, so Phase 3 should not depend on a generic analytics subsystem being real yet.

### Planning Implications
- `strategy_runs` is the least disruptive place to anchor backtests because the codebase already creates, updates, and reports through that table.
- Backtest-specific artifacts should live in dedicated child tables keyed to `strategy_run_id` so Phase 6 can later query them without reverse-engineering JSON blobs.
- Phase 3 should add a backtest-specific service and reporting layer, not "finish" the generic analytics service ahead of schedule.

</codebase_findings>

<external_findings>
## External Findings

### Execution Timing and Lookahead Avoidance
- Daily-bar backtesting conventions generally avoid filling an order on the same bar that generated the signal. The safe baseline is: compute on bar/session `T`, then execute on the next bar/session.

**Planning implications**
- Phase 3 should default to next-session-open execution for market-style entry and exit fills.
- Reported trade records must store the assumption explicitly so the operator can tell whether a run used `next_session_open`, zero slippage, or another policy.

### Fee and Slippage Simplicity
- The simplest credible first model is explicit but configurable: fixed or basis-point commission plus basis-point slippage applied deterministically to the fill price.

**Planning implications**
- Backtest assumptions belong in typed settings and in the persisted run snapshot, not in hardcoded constants inside the runner.
- Phase 3 does not need venue-specific fee schedules yet; it needs visible assumptions that can be changed and replayed.

### Reporting Expectations
- Research inspection is most useful when the operator can see:
  - a run-level summary
  - per-trade details
  - an equity curve export
  - the assumptions used by the run

**Planning implications**
- Phase 3 reports should be file- and CLI-friendly first: markdown or JSON summaries plus CSV exports for trades and equity points.
- Generic dashboard work should stay deferred to Phase 6 and later milestones.

</external_findings>

<recommendations>
## Recommended Architecture

### Persistence Shape
- Extend `StrategyRunType` with a dedicated backtest value instead of adding a separate root table.
- Add child tables keyed to `strategy_run_id` for:
  - persisted per-session backtest signals
  - simulated trades with entry/exit prices, quantity, PnL, and holding period
  - equity curve or account snapshots at session granularity
- Keep run-level assumption snapshots on the run record itself so every backtest can be replayed or explained.

### Backtest Execution Model
- Iterate over persisted `market_sessions` in ascending order for the requested date range.
- For each session:
  - evaluate the strategy at session close using already-persisted bars
  - queue entries and exits for the next persisted session
  - fill those queued actions at the next session open with deterministic fee/slippage adjustments
- Keep the first simulator long-only and single-strategy.
- Do not import the future Phase 4 risk engine. Use a simpler allocation policy such as equal-weight slots capped by configured `max_positions`, and make that assumption explicit in config and reports.

### Reporting Model
- Compute run-level metrics from persisted trades and equity snapshots, not from ephemeral in-memory state.
- Emit at least:
  - total return
  - max drawdown
  - trade count
  - win rate
  - average win
  - average loss
  - profit factor
  - exposure percentage
  - average holding period
- Provide CLI-driven exports for:
  - run summary
  - trade ledger
  - equity curve

### Testing Strategy
- Use temporary PostgreSQL databases plus seeded `symbols`, `market_sessions`, and `daily_bars`.
- Build fixture-driven tests for determinism, no-lookahead behavior, and explicit failure handling when a next-session fill cannot be formed.
- Keep report tests deterministic by seeding persisted runs rather than mocking the entire reporting path.

</recommendations>

## Validation Architecture

- `tests/test_backtest_runner.py`
  - verify next-session-open fill timing, deterministic repeat runs, equity tracking, and persisted run artifacts
- `tests/test_backtest_reporting.py`
  - verify summary metrics, markdown or JSON rendering, CSV exports, and no-trade edge cases
- Extend `tests/test_db_migrations.py`
  - verify Phase 3 tables and enum changes land correctly at Alembic head
- Keep the phase fully automation-friendly through `pytest` and CLI `--help` checks

**Quick command:** `PYTHONPATH=src .venv/bin/pytest tests/test_backtest_runner.py tests/test_backtest_reporting.py tests/test_db_migrations.py -q`

**Full command:** `PYTHONPATH=src .venv/bin/pytest tests -q`

## Plan Split Recommendation

### 03-01: Backtest settings and persistence foundation
- Own typed backtest settings, `strategy_runs` extensions, backtest artifact tables, and the first migration set for Phase 3.
- Keep the change isolated to schema and compatibility so existing dry-run behavior remains intact.

### 03-02: Deterministic runner and execution flow
- Own session iteration helpers, the internal simulator, duplicate-entry suppression, and the operator CLI or worker path for running backtests.
- Deliver the first real research execution path on top of the existing Phase 2 strategy boundary.

### 03-03: Run reports, metrics summaries, and exports for research inspection
- Own metrics computation, human-readable summaries, and machine-readable exports from persisted backtest data.
- Keep the operator surface CLI-first and avoid pulling Phase 6 API work forward.

## Sources

- Backtrader order creation and execution notes: https://www.backtrader.com/docu/order-creation-execution/order-creation-execution/
- Backtrader commission schemes reference: https://www.backtrader.com/docu/commission-schemes/commission-schemes/
- Current project sources:
  - `src/trading_platform/strategies/trend_following_daily/strategy.py`
  - `src/trading_platform/services/market_data_access.py`
  - `src/trading_platform/db/models/strategy_run.py`
  - `src/trading_platform/services/bootstrap.py`

---
*Phase: 03-backtest-and-reporting*
*Research completed: 2026-03-14*
