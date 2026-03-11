# Trading Strategy Platform

## What This Is

A single-user algorithmic trading platform for research, backtesting, and paper trading. The v1 system is intentionally scoped around one operator, one brokerage account, one portfolio, and one initial strategy, but the architecture must be strategy-platform-ready from day one so additional strategies, per-strategy configs, per-strategy analytics, and future dashboard controls can be added without reshaping the core engine.

The initial product focus is a Daily Trend Following workflow for U.S. equities on daily candles. The first end-to-end outcome is historical validation followed by automated paper execution and next-day inspection with full auditability, not live trading or a public SaaS product.

## Core Value

Build a trustworthy, auditable trading platform that can reproducibly validate a strategy, run it in daily paper trading, and explain every action or blocked action without ambiguity.

## Requirements

### Validated

<!-- Shipped and confirmed valuable. -->

(None yet — ship to validate)

### Active

<!-- Current scope. Building toward these. -->

- [ ] Build the platform as a single-user operator system with one operator, one brokerage account, one deployment owner, one credential set, and one portfolio in v1.
- [ ] Design the core as a multi-strategy-ready platform rather than a one-off bot, including strategy isolation, per-strategy configs, per-strategy statistics, strategy selection support in the architecture, and a future-facing strategy registry model.
- [ ] Implement `TrendFollowingDailyV1` as the first strategy for a narrow daily U.S. equities universe: `SPY`, `QQQ`, `AAPL`, `MSFT`, `NVDA`, `AMD`, `META`, `AMZN`, `GOOGL`, and `TSLA`.
- [ ] Ingest and persist reproducible historical daily OHLCV bars for the initial universe, with normalization, symbol metadata, calendar awareness, and enough handling for clean daily-bar research.
- [ ] Run deterministic backtests for `TrendFollowingDailyV1`, persist runs, trades, equity curves, and summary metrics, and make fees/slippage assumptions explicit.
- [ ] Persist candles, signals, strategy runs, orders, fills, positions, account snapshots, risk events, and performance summaries in PostgreSQL.
- [ ] Route every signal through a mandatory risk engine before execution, including risk-per-trade checks, position limits, strategy allocation limits, portfolio allocation limits, stale-data blocking, duplicate prevention, reconciliation guards, and repeated-failure guards.
- [ ] Support paper trading through Alpaca for v1, using the same strategy and risk flow as backtesting where possible, with daily scheduled execution and persistent order lifecycle tracking.
- [ ] Produce trustworthy analytics for both backtests and paper trading, including per-run metrics, trade inspection, current positions, recent orders, and enough statistics to compare runs without reading raw logs.
- [ ] Make observability part of the product through structured logs, visible failures, blocked-trade explanations, kill-switch support, restart-safe behavior, and full audit trails.
- [ ] Externalize and version configuration so strategy settings, risk settings, and runtime configuration are not hardcoded inside the strategy implementation.
- [ ] Keep the first implementation local-first and Dockerized, with FastAPI reserved for core APIs and future dashboard consumption.

### Out of Scope

<!-- Explicit boundaries. Includes reasoning to prevent re-adding. -->

- Live trading with real capital — v1 must prove correctness and auditability through backtesting and paper trading before real-money exposure.
- Multi-user support — the first build is explicitly a single-user operator platform.
- Authentication and RBAC — there is only one operator in v1, so auth complexity would add cost without product value.
- SaaS onboarding — this is not a public product in the first build.
- Subscription or billing systems — there is no commercial SaaS surface in v1.
- Team workflows — one operator means no shared workflows or permissions model is needed yet.
- User management — no additional users or tenant boundaries exist in the first version.
- Mobile app support — the initial goal is engine correctness and inspectability, not mobile access.
- Options trading — the initial market is U.S. equities only.
- Short selling — long-only daily trend following is the first validated workflow.
- Leverage or margin logic — unnecessary complexity for the initial paper-trading system.
- Intraday strategies — v1 is daily candles only.
- High-frequency trading — completely misaligned with the intended daily trend-following architecture.
- News-based trading — outside the first strategy design and data model.
- AI-generated trade decisions — the first strategy should remain simple, testable, and deterministic.
- Portfolio optimization across many strategies — deferred until there is more than one strategy worth comparing.
- Multiple brokers — Alpaca is the only v1 paper broker; broker abstraction exists, but multi-broker execution is deferred.
- IBKR integration — reserved for a later phase after v1 operational flow is stable.
- Cloud autoscaling — local-first simplicity is preferred over production infrastructure sophistication.
- Distributed workers — unnecessary for the initial daily-scheduled single-operator workload.
- Advanced event streaming — would add architectural weight before the daily workflow is proven.
- Complex real-time websocket dashboards — the future dashboard is deferred and does not need real-time complexity in v1.
- Tax reporting — not part of the first research and paper-trading platform.
- Social or community features — no multi-user or social surface exists in the initial product.
- Copy trading — outside the single-operator scope.
- Alerting to many channels — some visibility is required, but broad notification integrations are not part of the first build.
- Strategy marketplace — not relevant to a private single-user platform.
- Hyperparameter optimization platform — premature optimization before the first strategy workflow is trusted.
- Full no-code strategy builder — strategy implementation remains code-defined in the first version.
- Strategy ensembles — deferred until the platform supports multiple validated strategies.
- Multi-account support — one brokerage account and one portfolio only in v1.
- Multi-market support outside U.S. equities — initial market scope stays narrow to reduce moving parts.
- Minute bars or tick bars — daily bars only.
- Corporate-actions-heavy handling beyond what is needed for clean daily bars — do the minimum required for reliable daily-bar research first.
- Complex walk-forward optimization UI — not needed before the core backtest and paper workflow is stable.

## Context

### Product Positioning

- The product should be framed as a Python strategy platform with a future dashboard, not as a simple one-off bot.
- The platform should support research first, execution second. No strategy reaches live trading before backtesting and paper trading.
- The architecture must remain single-user in v1 while already supporting future strategy enablement, per-strategy configuration, per-strategy analytics, strategy comparison, and future API/dashboard controls.

### Phase Scope

- **Phase 1 scope**: one initial strategy (`TrendFollowingDailyV1`), historical backtesting, paper trading, performance analytics, risk management, strategy-selection support in the architecture, and persistent trade/order/signal logging.
- **Phase 2 scope**: multiple strategies, strategy-level statistics, strategy comparison, portfolio-level analytics, user-configurable strategy selection, and richer monitoring/reporting.
- **Phase 3 scope**: a web dashboard, likely with a Python backend for trading engine and analytics APIs plus a Node.js frontend for richer charts, metrics, trade history, and controls.

### Initial Strategy

- **Strategy name**: `TrendFollowingDailyV1`
- **Objective**: capture medium-term directional trends on liquid instruments using simple, testable rules.
- **Market**: U.S. equities
- **Timeframe**: daily candles only
- **Initial universe**: `SPY`, `QQQ`, `AAPL`, `MSFT`, `NVDA`, `AMD`, `META`, `AMZN`, `GOOGL`, `TSLA`
- **Example entry logic**:
  - close above long-term moving average
  - short-term moving average above long-term moving average
  - optional volume confirmation later
  - optional market regime filter later
- **Example exit logic**:
  - close below moving average
  - moving averages cross down
  - trailing stop hits
  - max holding period is reached
- **First strategy version details**:
  - Entry when `close > SMA 200`
  - Entry when `SMA 50 > SMA 200`
  - Exit when `close < SMA 50`
  - Position sizing based on fixed fractional risk
  - Only enter when there is no current open position on the symbol
- Complexity should stay intentionally low in the first version. The system complexity should come from platform reliability, auditability, and separation of concerns, not from early strategy sophistication.

### First Successful Use Case

The first end-to-end happy path for the project is:

1. Ingest daily historical bars for the initial universe.
2. Run `TrendFollowingDailyV1` backtests on that universe.
3. Persist run results, trades, and metrics.
4. Inspect a trustworthy analytics report.
5. Enable the same strategy in paper trading mode.
6. Run the strategy automatically each trading day after market close or on a fixed daily schedule.
7. Generate signals.
8. Pass every signal through risk checks.
9. Submit paper orders through the broker API.
10. Persist orders, fills, blocked trades, and account snapshots.
11. Review the next day:
    - what signals were generated
    - which trades were blocked and why
    - which orders were submitted
    - which fills happened
    - current paper positions
    - updated strategy statistics

The most important success path is historical validation -> paper execution -> next-day inspection with full auditability.

### Product Principles

1. **Research first, execution second**: no strategy goes live before backtesting and paper trading.
2. **Strategy modules must be isolated**: every strategy needs its own config, signals, metrics, and evaluation flow.
3. **Risk engine is mandatory**: no signal can reach execution without risk validation.
4. **Broker state is the source of truth**: internal state must reconcile against actual broker state.
5. **Observability is part of the product**: logs, alerts, strategy metrics, and order lifecycle tracking are product requirements, not optional extras.

### Architecture Direction

The planned high-level components are:

1. **Market Data Layer**: historical OHLCV ingestion, normalization, symbol metadata, trading calendar awareness, and any required corporate action adjustments for clean daily data.
2. **Strategy Engine**: indicator computation, entry/exit signal generation, and strategy-specific outputs.
3. **Portfolio Engine**: positions, exposure by symbol, exposure by strategy, cash usage, and allocation logic.
4. **Risk Engine**: max risk per trade, max positions, max daily loss, drawdown guard, duplicate prevention, stale-data blocking, and strategy-level risk constraints.
5. **Execution Engine**: order creation, broker API integration, order status updates, retries, error handling, fill tracking, and broker reconciliation.
6. **Analytics Engine**: backtest metrics, paper trading metrics, live-performance-ready metrics, trade attribution, strategy comparison, and slippage/fee analysis.
7. **Persistence Layer**: candles, signals, strategy runs, orders, fills, positions, account snapshots, risk events, and performance summaries.
8. **API Layer**: later expose platform data and controls to the dashboard through FastAPI.

### Multi-Strategy Design Target

- Each strategy should be a pluggable module implementing a common interface.
- Strategy modules must support isolated config, indicators, signals, metrics, and evaluation flow.
- The architecture must later support:
  - Trend Following
  - Mean Reversion
  - Breakout
  - Momentum Rotation
  - Volatility Regime strategies
  - Strategy ensembles
- The future operator control model should allow:
  - activating one strategy
  - activating multiple strategies
  - assigning capital allocation per strategy
  - comparing performance across strategies
  - viewing trade history by strategy

Example interface direction:

```python
from abc import ABC, abstractmethod
from typing import Any, Dict, List


class BaseStrategy(ABC):
    name: str
    version: str

    @abstractmethod
    def generate_signals(self, market_data, portfolio_state) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    def get_default_config(self) -> Dict[str, Any]:
        pass

    @abstractmethod
    def compute_indicators(self, market_data):
        pass
```

### Strategy Statistics Model

Each strategy should have an isolated analytics profile with at least:

- total return
- CAGR
- Sharpe ratio
- Sortino ratio
- max drawdown
- win rate
- average win
- average loss
- profit factor
- expectancy
- exposure percentage
- number of trades
- average holding period
- turnover
- best trade
- worst trade

Additional useful metrics later:

- monthly returns
- rolling drawdown
- rolling Sharpe
- slippage impact
- fee impact
- regime performance
- symbol contribution
- long-only versus total breakdown

The future dashboard should support strategy comparison views across return, drawdown, Sharpe, win rate, profit factor, and trade count.

### Integrations and Stack

- **Market data provider for v1**: Polygon
  - reason: strong API coverage for U.S. equities, strong historical support, and appropriate programmatic ergonomics for the first build
- **Paper broker for v1**: Alpaca
  - reason: simpler developer experience and explicit paper-trading support
- **Broker reserved for later**: IBKR
  - reason: broader long-term power, but more operational complexity than needed for v1
- **Locked stack for v1**:
  - Python
  - FastAPI
  - PostgreSQL
  - Docker
- **Likely but not mandatory on day one**:
  - Redis for locks, scheduling coordination, async tasks, and future queueing
- **Future dashboard stack**:
  - Node.js
  - Next.js
  - TypeScript
  - charting library

### Backtesting Direction

- The project should avoid overinvesting in a large custom simulation framework before the workflow is proven.
- The preferred v1 direction is a lightweight internal daily-bar backtester.
- The backtester should remain simple and deterministic:
  - no event-driven complexity
  - no intraday simulation
  - no tick-level assumptions
- This matches the first strategy better than building a broad simulation platform too early.

### Repository Structure Direction

```text
trading-platform/
  apps/
    api/
    worker/
    dashboard/                # later, Node.js frontend
  packages/
    core/
      strategy/
        base.py
        registry.py
        trend_following_daily/
      risk/
      portfolio/
      execution/
      analytics/
      backtest/
    data/
      providers/
      models/
      ingestion/
      normalization/
    infra/
      db/
      logging/
      config/
      scheduling/
      alerts/
  migrations/
  scripts/
    seed_data/
    backfill/
    run_backtest/
    run_paper/
    reconcile/
  tests/
    unit/
    integration/
    strategy/
```

### Domain Model

Core entities to support:

- `Symbols`: tradeable instruments
- `MarketBars`: historical daily OHLCV data
- `StrategyConfig`: per-strategy configuration
- `StrategyRun`: a backtest or paper-execution run
- `Signals`: generated buy, sell, or exit signals
- `Orders`: submitted broker orders
- `Fills`: execution results
- `Positions`: current and historical open positions
- `AccountSnapshots`: equity, buying power, cash, and exposure over time
- `RiskEvents`: blocked trades, threshold breaches, and kill-switch triggers
- `PerformanceSummaries`: metrics per strategy, per run, and per period

### Configuration Model

Strategy configuration should be explicit, externalized, and versioned. Example direction:

```json
{
  "strategy_name": "trend_following_daily",
  "strategy_version": "v1",
  "enabled": true,
  "universe": ["SPY", "QQQ", "AAPL", "MSFT"],
  "timeframe": "1D",
  "entry_rules": {
    "fast_ma": 50,
    "slow_ma": 200
  },
  "exit_rules": {
    "exit_ma": 50
  },
  "risk": {
    "risk_per_trade_pct": 0.5,
    "max_open_positions": 5,
    "max_daily_loss_pct": 2.0
  }
}
```

This should live in configuration or the database, not inside strategy code.

### Execution Modes

The platform should be designed around three execution modes from the beginning:

1. **Backtest mode**: historical data only, metrics output, no broker connection.
2. **Paper trading mode**: real market data with simulated or paper broker orders, validating the real execution flow.
3. **Live mode**: real capital and the strongest safeguards, explicitly not enabled until the platform is stable.

The design target is one unified interface where the execution adapter changes by mode.

### Risk Management Rules

Mandatory v1 risk policy areas:

- max risk per trade
- max open positions
- max strategy allocation
- max total portfolio allocation
- max daily loss
- max drawdown threshold
- no duplicate open position per symbol
- no orders when data is stale
- no new trades if broker reconciliation fails
- no new trades after repeated order failures

Example v1 policy targets:

- risk per trade: `0.5%`
- max positions: `5`
- max daily loss: `2%`
- max strategy allocation: `50%`
- one open position per symbol

### Development Roadmap Direction

- **Phase 0 — Foundation**: repository structure, config management, database setup, migrations, logging, basic domain models, strategy interface, provider interfaces.
- **Phase 1 — Historical Data and Backtesting**: daily data ingestion, bar storage, backtest runner, `TrendFollowingDailyV1`, performance report generator, trade logs, CSV/JSON exports.
- **Phase 2 — Risk and Portfolio Engine**: sizing logic, risk validation pipeline, portfolio exposure model, blocked-signal logging, drawdown calculations, strategy allocation model.
- **Phase 3 — Paper Trading**: broker adapter interface, Alpaca paper integration, order lifecycle state machine, fill ingestion, reconciliation process, daily scheduled strategy run.
- **Phase 4 — Analytics Layer**: per-strategy summaries, equity curves, drawdown analysis, rolling metrics, symbol-level analysis, trade attribution reports.
- **Phase 5 — Multi-Strategy Support**: strategy registry, strategy enable/disable, strategy config management, multi-strategy backtests, comparison reports, per-strategy allocation.
- **Phase 6 — API Layer**: FastAPI endpoints for strategies, runs, trades, metrics, positions, account snapshots, and risk events.
- **Phase 7 — Web Dashboard**: Node.js and Next.js interface for strategy list/detail, statistics, equity curve, positions, order history, risk events, comparisons, and paper-trading status.

### MVP Definition

The MVP is a Python trading platform that can:

- run `TrendFollowingDailyV1`
- backtest it on historical daily data
- show performance statistics
- run it in paper trading mode
- log trades, signals, and risk events
- support future strategy plug-ins

MVP is done when all of the following are true:

- historical daily bars for the initial universe can be ingested reproducibly
- `TrendFollowingDailyV1` runs end to end without manual patching
- backtest results are deterministic for the same inputs and config
- trades, equity curve, and summary metrics are persisted
- fees and slippage assumptions are explicit and included
- every generated signal passes through a risk engine
- blocked signals are persisted with human-readable reasons
- position sizing is deterministic and testable
- duplicate positions and orders are prevented in the defined v1 way
- the strategy runs on a daily schedule without manual intervention
- paper orders submit successfully through the selected broker
- order states and fills are persisted
- restarts do not corrupt state
- broker and account reconciliation works at least daily
- per-run metrics are trustworthy enough to compare runs
- trade history is inspectable
- current positions and recent orders are queryable
- strategy performance can be reviewed without reading raw logs
- structured logs exist
- failures are visible
- stale or missing data blocks trading
- a kill switch or hard stop exists
- configuration is externalized and versioned

The desired MVP verdict is:

`I can run the strategy on historical data, evaluate it, enable daily paper trading, and inspect exactly what happened the next day with confidence.`

### Testing Strategy

Three test layers are required:

- **Unit tests** for indicators, signal generation, sizing, risk checks, and metric calculations
- **Integration tests** for ingestion-to-database flow, strategy run to signal persistence, signal-to-risk-to-order flow, and reconciliation
- **Simulation tests** for backtest reproducibility, multi-day paper workflow, restart resilience, and broker disconnect scenarios

### Operational Concerns

The following must exist before real money is ever considered:

- kill switch
- daily reconciliation
- stale data checks
- broker API failure handling
- structured logs
- error visibility or alerts
- restart-safe workers
- idempotent order submission
- audit trail for every signal and action

### First Build Sequence

1. Set up project skeleton, database, config, logging, and strategy base classes.
2. Implement historical daily-bar ingestion.
3. Implement `TrendFollowingDailyV1`.
4. Implement the backtest runner and metrics engine.
5. Implement the risk engine and portfolio sizing.
6. Implement paper-trading execution flow.
7. Implement analytics summaries and exports.
8. Add strategy registry and second-strategy-ready architecture.
9. Expose FastAPI endpoints.
10. Build the Node dashboard later.

### Backend API Direction

The future API should expose platform data cleanly to the dashboard without direct database access. Useful endpoint directions include:

- `GET /strategies`
- `GET /strategies/{strategy_id}`
- `POST /strategies/{strategy_id}/run-backtest`
- `GET /runs`
- `GET /runs/{run_id}`
- `GET /strategies/{strategy_id}/metrics`
- `GET /strategies/{strategy_id}/equity-curve`
- `GET /trades`
- `GET /orders`
- `GET /fills`
- `GET /positions`
- `GET /account-snapshots`
- `GET /risk-events`

### Operating Assumptions

- v1 is for one operator only: you.
- There is one brokerage account, one deployment owner, one credential set, and one portfolio.
- Local development and operation come first, using macOS and Docker Compose.
- Infra and market-data costs should stay low.
- One paid data provider is acceptable if it materially improves reliability.
- Operational simplicity beats broad market coverage or infrastructure sophistication in v1.
- The initial target is a working backtest and paper-trading system before any dashboard expansion.

## Constraints

- **Operator model**: Single-user only in v1 — the system is intentionally a private operator platform, not a public SaaS product.
- **Market scope**: U.S. equities on daily candles only — keeps the first build narrow enough to validate the workflow reliably.
- **Primary strategy**: `TrendFollowingDailyV1` only in the first release — ensures the engine and risk flow are proven before broader strategy expansion.
- **Execution safety**: No live trading in v1 — paper trading must validate the execution and reconciliation flow first.
- **Tech stack**: Python, FastAPI, PostgreSQL, and Docker are effectively locked for v1 — avoids tech churn during the MVP build.
- **Infrastructure**: Local-first with Docker Compose on macOS — favors simplicity, repeatability, and low operating cost before cloud deployment.
- **Brokering**: Alpaca for v1 paper trading, Polygon for market data — reduces integration ambiguity in the first implementation.
- **Complexity budget**: Redis is optional at the earliest stage — do not add coordination infrastructure before the daily-scheduled runner needs it.
- **Auditability**: Every action and blocked action must be persisted and explainable — trust in the platform depends on not having ambiguous state transitions.
- **Reliability**: Broker reconciliation and stale-data checks must gate execution — internal state cannot override broken external truth.
- **Cost**: Keep infra and data costs low — avoid institutional-grade spend before validation.
- **Product scope**: Dashboard work is deferred until the engine, backtest flow, and paper-trading loop are trustworthy — UI should not outrun the platform.

## Key Decisions

<!-- Decisions that constrain future work. Add throughout project lifecycle. -->

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Build a strategy platform, not a one-off bot | Future multi-strategy support is a core design requirement from day one | — Pending |
| Scope v1 to a single operator and one brokerage account | Multi-user complexity is unnecessary for the first validated workflow | — Pending |
| Start with `TrendFollowingDailyV1` on daily U.S. equities | A simple, testable, low-frequency strategy best matches the first platform build | — Pending |
| Use Polygon for v1 market data | Good U.S. equities coverage and strong API ergonomics | — Pending |
| Use Alpaca for v1 paper trading | Easiest path to a reliable early paper-trading workflow | — Pending |
| Defer IBKR to a later phase | Operational complexity is not justified before the first system is stable | — Pending |
| Prefer a lightweight internal daily-bar backtester for v1 | The first strategy does not need an event-driven simulator or intraday fidelity | — Pending |
| Treat risk validation as mandatory before execution | No signal should reach execution without passing platform guardrails | — Pending |
| Treat broker state as the source of truth | Reconciliation must override optimistic internal assumptions | — Pending |
| Keep the first implementation local-first and Dockerized | Simplicity, repeatability, and low cost matter more than cloud sophistication | — Pending |
| Defer dashboard work until the engine is trustworthy | The engine, analytics, and paper workflow must be credible before UI investment | — Pending |

---
*Last updated: 2026-03-11 after project initialization questioning*
