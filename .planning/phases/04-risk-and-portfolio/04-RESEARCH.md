# Phase 4: Risk and Portfolio - Research

**Researched:** 2026-03-14
**Domain:** Deterministic portfolio state, sizing, and risk-evaluation gating for the single-user paper-trading path
**Confidence:** HIGH

<planning_inputs>
## Planning Inputs

### Available Context
- No `04-CONTEXT.md` exists for this phase. Planning uses `.planning/PROJECT.md`, `.planning/ROADMAP.md`, `.planning/STATE.md`, the completed Phase 1 and Phase 3 summaries, and the current codebase.
- `.planning/REQUIREMENTS.md` is not present in this repo, so requirement mapping must be derived from `.planning/ROADMAP.md` and `.planning/PROJECT.md`.
- The codebase already has the key upstream inputs this phase should compose on rather than replace: typed settings, registry-backed strategy metadata, deterministic `SignalBatch` output, persisted `strategy_runs`, persisted daily bars and market sessions, and a session-by-session backtest runner with explicit sizing and duplicate-entry handling.

### Locked Decisions From Project State
- The platform remains local-first, PostgreSQL-backed, and CLI-first.
- `TrendFollowingDailyV1` signal generation must stay the source of truth for strategy decisions; Phase 4 should not move portfolio or risk concepts into the strategy layer.
- Every signal must pass through a mandatory risk engine before execution. No bypass path is acceptable.
- Phase 4 owns deterministic sizing, portfolio state, blocked-signal logging, and the execution-gating surface.
- Phase 5 owns broker submission, order lifecycle tracking, reconciliation, and scheduled paper execution. Phase 4 must prepare for that flow without pre-building Alpaca logic.
- Human-readable blocked-trade explanations are part of MVP trustworthiness, not optional logging polish.

### Claude's Discretion
- Whether to anchor paper-risk evaluation batches under a new `strategy_runs` type or a dedicated root table.
- The exact split between strategy-level risk config and account-level portfolio config, as long as both are typed and externalized.
- Whether to persist approved and rejected decisions in one audit table or in adjacent tables with a shared run root.
- Which stale-data rule is the simplest credible v1 baseline, as long as it is deterministic, testable, and based on persisted session or bar truth instead of wall-clock heuristics alone.

</planning_inputs>

<research_summary>
## Summary

Phase 4 should stay narrow and compositional:

1. Keep strategy output pure. `SignalBatch` already excludes risk, portfolio, and broker concerns, and that separation should remain intact.
2. Introduce durable live-portfolio state instead of reusing backtest artifacts. The current backtest runner tracks `cash`, `open_positions`, and `gross_exposure` in memory, but Phase 4 needs normalized tables and services that represent ongoing paper-trading state.
3. Build the risk engine as a deterministic service around existing strategy signals, persisted portfolio state, and persisted market-data freshness checks.
4. Persist risk decisions with both machine-readable rule codes and human-readable explanations so later operator inspection and Phase 5 execution can consume the same audit trail.
5. Split the phase into two sequential plans:
   - `04-01`: portfolio state, typed portfolio or risk settings, deterministic sizing, and exposure accounting
   - `04-02`: risk-validation pipeline, decision persistence, CLI-first evaluation flow, and execution gating

**Primary recommendation:** Add dedicated `positions` and `account_snapshots` persistence plus a portfolio service first, then implement a real `RiskService` that consumes `strategy.generate_signals(...)`, validates each signal against persisted state and stale-data rules, persists the decision, and exposes a CLI-first evaluation command without submitting broker orders yet.

</research_summary>

<codebase_findings>
## Codebase Findings

### Existing Reusable Assets
- `src/trading_platform/strategies/signals.py` explicitly keeps signals free of risk-sizing, portfolio, and broker concepts. That is the correct upstream boundary for Phase 4.
- `src/trading_platform/services/risk.py` is still a placeholder contract, which gives Phase 4 a clean seam to replace without undoing other architecture.
- `src/trading_platform/services/backtesting.py` already contains deterministic cash, exposure, and duplicate-entry logic. Those calculations should inform Phase 4 service design, but the backtest tables themselves should remain backtest-specific.
- `src/trading_platform/services/market_data_access.py` already provides the session and bar queries Phase 4 needs for stale-data and latest-bar checks.
- `src/trading_platform/services/bootstrap.py` and `src/trading_platform/worker/__main__.py` establish the repo's preferred operator pattern: a persisted service plus CLI or worker entrypoints.
- `tests/test_dry_run.py`, `tests/test_backtest_runner.py`, and `tests/test_db_migrations.py` already show the expected PostgreSQL-backed testing and migration-verification pattern.

### Current Gaps Blocking Phase 4
- There are no normalized `positions`, `account_snapshots`, or `risk_events` tables yet.
- There is no service that loads current portfolio state from the database and produces deterministic sizing or exposure calculations for paper trading.
- There is no persisted signal-to-risk-evaluation flow outside the Phase 3 backtest path.
- The only signal persistence currently implemented is `backtest_signals`, which is intentionally tied to historical simulation and should not become the live trading audit table by accident.
- `StrategyRunType` currently distinguishes `dry_bootstrap` and `backtest`, but there is no run-root convention yet for risk-evaluation or paper-trading batches.

### Planning Implications
- Phase 4 should add live-portfolio state models rather than stretching backtest tables into a second job.
- Sizing logic should live in a dedicated portfolio service so the risk pipeline can compose on it instead of duplicating math.
- Approved or rejected signal decisions should anchor to the same database-first audit model that later paper-order execution will use.
- The strategy API and signal types should remain unchanged; Phase 4 belongs entirely in services, persistence, settings, and operator entrypoints.

</codebase_findings>

<implementation_edge_cases>
## Implementation Edge Cases

### Stale-Data Blocking
- The safest v1 stale-data rule is based on persisted session or bar freshness, not on process wall-clock time alone.
- Phase 4 should block trading when the requested evaluation session lacks the expected persisted bar coverage for the strategy universe, or when the latest persisted completed session is older than the intended evaluation boundary.

### Duplicate Protection
- Duplicate prevention should check durable open-position state, not only same-session signal metadata.
- The backtest runner already ignores duplicate `LONG` signals while a position is open. Phase 4 should preserve that principle in the real risk pipeline, using persisted positions as the source of truth.

### Allocation and Sizing
- The project direction calls for fixed-fraction sizing with whole-share determinism, bounded by max positions and allocation caps.
- Phase 4 should compute a proposed size first, then validate it against account-level and strategy-level caps so rejections can explain exactly which rule failed.

### Run Root and Auditability
- The codebase already treats `strategy_runs` as the canonical run root for dry runs and backtests.
- Extending that pattern for risk-evaluation batches is lower risk than inventing a second orchestration root, provided Phase 4 keeps the new run type narrow and leaves broker-order state for Phase 5.

</implementation_edge_cases>

<recommendations>
## Recommended Architecture

### Configuration Shape
- Keep strategy-local knobs such as `risk_per_trade` and `max_positions` in the strategy YAML.
- Add typed top-level portfolio or risk runtime settings for account-wide caps Phase 4 owns, such as:
  - `max_strategy_allocation_pct`
  - `max_total_portfolio_allocation_pct`
  - `max_daily_loss_pct`
  - stale-data tolerance or freshness policy
- Store these in `config/app.yaml` and `src/trading_platform/core/settings.py` so later paper execution inherits the same typed settings path.

### Persistence Shape
- Add normalized live-state tables for:
  - current or historical `positions`
  - `account_snapshots`
  - `risk_events` or an equivalent per-signal decision audit table
- Keep these separate from `backtest_trades` and `backtest_equity_snapshots`.
- If Phase 4 needs a persisted batch root for evaluation, extend `strategy_runs` with a Phase 4-specific run type instead of creating a parallel orchestration root.

### Portfolio Engine
- Introduce a `portfolio.py` service that can:
  - load current open positions and the latest account snapshot
  - compute gross exposure, cash usage, and strategy allocation
  - propose deterministic whole-share sizes from fixed-fraction risk and configured caps
  - expose a typed portfolio-state value object usable by the risk engine and later paper execution

### Risk Engine
- Replace `PlaceholderRiskService` with a real pipeline that:
  - accepts `SignalBatch`, evaluation session, and portfolio state
  - runs stale-data checks before any entry or exit approval
  - blocks duplicate entries for symbols with open positions
  - enforces max positions, strategy allocation, total allocation, and configured sizing rules
  - emits typed approved or rejected decisions with machine-readable codes and human-readable explanations
- Keep broker reconciliation and repeated-order-failure guards represented as future constraints or explicit TODOs in the interface if needed, but do not implement broker-state behaviors until Phase 5.

### Operator Surface
- Add a CLI or worker evaluation path such as `scripts/evaluate_risk.py` and `trading-platform-worker evaluate-risk`.
- The command should:
  - resolve the evaluation session
  - generate signals via the existing strategy boundary
  - load persisted portfolio state
  - evaluate every signal through the risk engine
  - persist the resulting decisions
  - print a concise JSON or markdown summary
- Do not submit orders in Phase 4.

### Testing Strategy
- Add `tests/test_portfolio_service.py` for deterministic sizing, whole-share rounding, cash or exposure accounting, and allocation-cap behavior.
- Add `tests/test_risk_pipeline.py` for stale-data rejection, duplicate prevention, max-position rejection, allocation-cap rejection, and approved-sizing output.
- Extend `tests/test_db_migrations.py` to verify the new tables and any `strategy_run_type` changes.
- Keep a regression slice against `tests/test_backtest_runner.py` so the new portfolio or risk code does not leak into the Phase 3 backtest path.

</recommendations>

## Validation Architecture

- `tests/test_portfolio_service.py`
  - verify fixed-fraction sizing, whole-share rounding, exposure calculations, and account-snapshot reads
- `tests/test_risk_pipeline.py`
  - verify stale-data blocks, duplicate-position rejection, max-position rejection, allocation-cap rejection, and approved-sized decisions
- Extend `tests/test_db_migrations.py`
  - verify `positions`, `account_snapshots`, and `risk_events` tables plus any new `strategy_run_type` labels land correctly at Alembic head
- CLI checks
  - verify `scripts/evaluate_risk.py --help` and `python -m trading_platform.worker evaluate-risk --help` once the operator surface lands

**Quick command:** `PYTHONPATH=src .venv/bin/pytest tests/test_portfolio_service.py tests/test_risk_pipeline.py tests/test_db_migrations.py -q`

**Full command:** `PYTHONPATH=src .venv/bin/pytest tests -q`

## Plan Split Recommendation

### 04-01: Portfolio state, sizing logic, and exposure accounting
- Own typed Phase 4 settings, normalized portfolio-state models, and a deterministic portfolio service for cash, exposure, and proposed sizing.
- Keep the work free of signal-validation branching and broker-order logic so the state foundation stays small and testable.

### 04-02: Risk-validation pipeline, rejection logging, and execution gating
- Own the concrete `RiskService`, per-signal decision persistence, and a CLI-first evaluation flow that turns `SignalBatch` output into approved or blocked execution candidates.
- Keep the work focused on gating and auditability; actual order submission, retries, reconciliation, and fill tracking remain in Phase 5.

## Sources

No external sources were required for this phase beyond the current project planning docs and codebase.

Current project sources:
- `src/trading_platform/core/settings.py`
- `src/trading_platform/services/risk.py`
- `src/trading_platform/services/backtesting.py`
- `src/trading_platform/services/market_data_access.py`
- `src/trading_platform/services/bootstrap.py`
- `src/trading_platform/worker/__main__.py`
- `src/trading_platform/strategies/signals.py`
- `src/trading_platform/db/models/strategy_run.py`
- `tests/test_backtest_runner.py`
- `tests/test_db_migrations.py`

---
*Phase: 04-risk-and-portfolio*
*Research completed: 2026-03-14*
