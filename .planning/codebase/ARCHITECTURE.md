# Architecture

**Analysis Date:** 2026-07-07

## Pattern Overview

**Overall:** Layered service-oriented architecture with explicit separation of concerns:
- HTTP API layer (FastAPI)
- Domain service layer (business logic)
- Persistence layer (SQLAlchemy ORM + PostgreSQL)
- Strategy plugin layer (abstract base with registry)
- CLI worker layer for async/scheduled operations

**Key Characteristics:**
- Synchronous execution model with session-based database access
- Pluggable strategy implementations via abstract base + registry pattern
- Event-sourced decision records (risk, orders, fills) persisted for auditability
- Multi-run-type architecture supporting backtest, paper execution, and live analysis
- Operator control layer enforcing kill switches and per-strategy toggles
- Structured logging with contextual metadata throughout

## Layers

**API Layer:**
- Purpose: HTTP endpoints for operator inspection and control
- Location: `src/trading_platform/api/`
- Contains: FastAPI application bootstrap, route modules, dependency injection
- Depends on: Service layer, strategy registry, settings
- Used by: HTTP clients, operator CLI

**Service Layer:**
- Purpose: Core business logic for trading operations, analytics, and reconciliation
- Location: `src/trading_platform/services/`
- Contains: Specialized services (backtesting, paper execution, risk evaluation, analytics, reconciliation)
- Depends on: Database models, external broker APIs, market data access, strategy registry
- Used by: API routes, worker CLI commands

**Database/Persistence Layer:**
- Purpose: ORM models and session management
- Location: `src/trading_platform/db/`
- Contains: SQLAlchemy models (18 distinct entity types), session factory, connection pooling
- Depends on: Settings (for connection strings), PostgreSQL driver
- Used by: All services, strategy implementations

**Strategy Layer:**
- Purpose: Pluggable strategy implementations
- Location: `src/trading_platform/strategies/`
- Contains: Base strategy abstract class, signal types, registry for lookup
- Depends on: Market data access, database session, settings
- Used by: Backtesting service, risk evaluation, signal generation

**Worker/CLI Layer:**
- Purpose: Non-HTTP entry points for scheduled/manual operations
- Location: `src/trading_platform/worker/__main__.py`
- Contains: Command-line interface with 20+ subcommands for operations
- Depends on: Service layer, settings
- Used by: Cron jobs, manual operator intervention, scheduled workflows

**Core/Configuration Layer:**
- Purpose: Configuration management and cross-cutting concerns
- Location: `src/trading_platform/core/`
- Contains: Settings loader (YAML + env overrides), structured logging
- Depends on: PyYAML, Pydantic
- Used by: All layers at startup/initialization

## Data Flow

**Backtest Workflow:**

1. Worker CLI receives `backtest` command with strategy and date range
2. `BacktestingService.run_backtest()` loads strategy via registry
3. Strategy's `generate_signals()` queries daily bars via `MarketDataAccess`
4. Backtest engine simulates order execution with fills, tracks equity curve
5. Results persisted as `BacktestTrade`, `BacktestSignal`, `BacktestMetric` entities
6. Worker CLI or API renders report via `BacktestReportingService`

**Paper Execution Workflow:**

1. Risk evaluation produces approved `RiskEvent` records for symbol/side/quantity
2. Worker CLI calls `run_paper_session()` which:
   - Fetches approved risk decisions for session date
   - Derives intended orders from risk decisions
   - Checks operator controls (per-strategy enable/kill switch)
   - Submits orders to Alpaca via `AlpacaExecutionService`
   - Records `PaperOrder` and `ExecutionEvent` entities
3. `sync_paper_state()` polls broker for fills, updates local `PaperFill` records
4. `reconcile_paper_execution()` validates local state matches broker, reports drift
5. Analytics queries all execution history for performance summaries

**Risk Evaluation Workflow:**

1. Worker CLI calls `run_risk_evaluation()` with target session date
2. Strategy signals generated for all universe symbols as of that date
3. `RiskService` evaluates each signal through:
   - Position sizing (respecting max_positions, risk_per_trade)
   - Portfolio constraints (margin, sector limits)
   - Exit rules (close below threshold MA)
4. Approved order intents recorded as `RiskEvent` entities
5. Risk run persisted with `StrategyRun` (status=succeeded)

**Analytics/Reporting Flow:**

1. API or worker queries `StrategyAnalyticsService`
2. Service pulls backtest metrics, paper execution fills, equity curves
3. Computes Sharpe, drawdown, win rate, P&L statistics
4. Renders as JSON or markdown summary with recent operational inspection

**State Management:**

- Immutable entity records: `StrategyRun`, `RiskEvent`, `ExecutionEvent`, `OrderEvent`
- Current state snapshots: `Position`, `PaperOrder`, `AccountSnapshot`
- Time-series data: `DailyBar` (market data), `BacktestEquitySnapshot` (equity curves)
- Control state: `SystemControl` table stores global kill switch + per-strategy toggles
- All state changes logged to database for full auditability

## Key Abstractions

**StrategyRegistry:**
- Purpose: Plugin system for discovering and loading strategy implementations
- Examples: `src/trading_platform/strategies/registry.py`, `src/trading_platform/strategies/base.py`
- Pattern: Registry maintains map of strategy_id → Strategy class. Lazy instantiation via `resolve(strategy_id)`. Base class `BaseStrategy` defines abstract interface.

**SignalBatch:**
- Purpose: Typed container for strategy signals as of a session date
- Examples: `src/trading_platform/strategies/signals.py`
- Pattern: Immutable dataclass containing one Signal per universe symbol. Signals carry symbol, side (LONG/SHORT/FLAT), and reasoning metadata.

**StrategyRun:**
- Purpose: Audit record linking all work (backtest, risk, execution) to a strategy execution
- Examples: `src/trading_platform/db/models/strategy_run.py`
- Pattern: Immutable entity with run_id (UUID), status (pending/succeeded/failed), type (backtest/risk/paper_order_submission), trigger_source (who initiated)

**MarketDataAccess:**
- Purpose: Abstraction over daily bar queries and market session metadata
- Examples: `src/trading_platform/services/market_data_access.py`
- Pattern: Session-aware queries that lazily load bars, cache session metadata, provide date validation

**OrderStateMachine:**
- Purpose: Deterministic transitions from intended order through lifecycle states
- Examples: `src/trading_platform/services/order_state_machine.py`
- Pattern: Given PaperOrder + broker fill snapshot, compute next state (pending → filled, cancelled, etc). State transition immutable.

**OperatorControl:**
- Purpose: Enforce authorization and execution halts
- Examples: `src/trading_platform/services/operator_controls.py`
- Pattern: SystemControl entity records kill switch state + per-strategy enable/disable. All paper submission paths check controls before broker contact.

## Entry Points

**FastAPI HTTP Server:**
- Location: `src/trading_platform/api/app.py`
- Triggers: `trading-platform-api` CLI command or Docker container startup
- Responsibilities: 
  - Loads settings from YAML + env at startup
  - Registers six route modules (health, analytics, strategies, runs, operations, system)
  - Serves structured logging throughout request lifecycle
  - Provides dependency injection (settings, services, registry)

**Worker CLI:**
- Location: `src/trading_platform/worker/__main__.py`
- Triggers: `trading-platform-worker <command>` with 20+ subcommands
- Responsibilities:
  - Argument parsing for diverse commands (backtest, risk, submit, reconcile, operator-control, etc.)
  - Configuration loading and logging setup
  - Delegation to service functions
  - JSON output to stdout for consumption by external schedulers/dashboards

## Error Handling

**Strategy:** Layered error propagation with validation at boundaries:

**Patterns:**
- Input validation: Pydantic models validate settings at load time; datetime/date args validated in CLI argument parsing
- Business logic errors: Services raise `ValueError` (bad input), `LookupError` (not found), domain-specific exceptions
- API errors: Route handlers catch exceptions, map to HTTP status codes (404 not found, 400 bad request, 503 service unavailable)
- Database errors: Session scope context manager rolls back transaction on any exception; errors propagate up for caller handling
- External API errors: `AlpacaClient` catches network errors, invalid credentials, API rate limits; logs and re-raises as service exception

## Cross-Cutting Concerns

**Logging:** Structured JSON logging via `core/logging.py`. Every operation emits context dict with run_id, strategy_id, session_date, operation name. Elasticsearch-ready format.

**Validation:** Pydantic BaseModel for all settings and API payloads. SQLAlchemy constraints (NOT NULL, UNIQUE, FK) enforced at database. Strategy params validated in registry resolution.

**Authentication:** Not implemented (single_user operator_mode). Future: API could validate operator credentials before allowing control operations (enable/disable/kill-switch).

**Transactions:** SQLAlchemy session-scope pattern ensures all database writes atomic. Paper order submission is NOT transactional across broker contact; instead relies on idempotency and reconciliation.

---

*Architecture analysis: 2026-07-07*
