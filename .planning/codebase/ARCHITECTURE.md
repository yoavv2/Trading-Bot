# Architecture

**Analysis Date:** 2026-04-16

## Pattern

Layered service-oriented architecture with 6 distinct layers:

1. **Core Config** — `src/trading_platform/core/` — Settings bootstrap, logging configuration
2. **API Presentation** — `src/trading_platform/api/` — FastAPI routes, dependency injection
3. **Strategy Domain** — `src/trading_platform/strategies/` — Strategy contracts (ABC), registry, implementations
4. **Data Persistence** — `src/trading_platform/db/` — SQLAlchemy ORM models, session management, Alembic migrations
5. **Service Layer** — `src/trading_platform/services/` — Business logic, workflow orchestration (18 service modules)
6. **Worker Orchestration** — `src/trading_platform/worker/` — CLI command dispatch for batch operations

## Data Flows

**Dry-Run Bootstrap:**
`Worker CLI -> BootstrapService -> StrategyRegistry -> MarketDataService -> DB (StrategyRun)`

**Market Data Ingestion:**
`Worker CLI -> IngestionService -> PolygonClient (httpx) -> DB (DailyBar, MarketSession)`

**Backtesting:**
`Worker CLI -> BacktestService -> Strategy.generate_signals() -> DB (StrategyRun, Signal, Trade)`

**Risk Evaluation:**
`Worker CLI -> RiskService -> PositionService -> DB (RiskEvent)`

**Paper Execution:**
`Worker CLI -> PaperExecutionService -> AlpacaClient (httpx) -> DB (Order, Fill, Position)`

## Key Abstractions

**Settings Hierarchy (Pydantic):**
- `Settings` in `src/trading_platform/core/settings.py` (385 lines)
- Nested Pydantic models for typed config sections
- Loaded via `load_settings()` with `@lru_cache(maxsize=1)`
- YAML files merged with environment variable overrides

**BaseStrategy ABC:**
- `src/trading_platform/strategies/base.py`
- Contract: `generate_signals()`, `metadata` property
- Implementations register via `StrategyRegistry`
- Currently one implementation: `TrendFollowingDailyV1`

**Service Boundaries:**
- `MarketDataService` — data ingestion and access
- `RiskService` — position risk evaluation
- `ExecutionService` — paper/live order execution
- `ReconciliationService` — position/order reconciliation
- `AnalyticsService` — strategy performance analytics
- `PortfolioService` — portfolio-level aggregation

**StrategyRun ORM Model:**
- Central audit trail entity
- Tracks run lifecycle: bootstrap -> signals -> trades -> reconciliation
- Status tracked via `StrategyRunStatus` StrEnum

## Entry Points

**API Server:**
- `src/trading_platform/api/app.py` — FastAPI factory (`create_app()`)
- 6 route modules in `src/trading_platform/api/routes/`: health, strategies, runs, analytics, operations, system

**Worker CLI:**
- `src/trading_platform/worker/__main__.py` (673 lines) — command dispatch
- Commands: bootstrap, ingest, backtest, risk-check, paper-execute, reconcile

## Cross-Cutting Concerns

**Logging:**
- JSON structured logging via `JsonLogFormatter`
- Context includes: `strategy_id`, `run_id`, `session_date`, `strategy_status`
- Configured once via `configure_logging()` in `src/trading_platform/core/logging.py`

**Validation:**
- Pydantic for config/API request validation
- SQLAlchemy column constraints for data integrity
- Domain validation in service layer (e.g., risk thresholds)

**Database Sessions:**
- Context manager pattern via `session_scope()` in `src/trading_platform/db/session.py`
- Engine cached with `@lru_cache`

**Authentication:**
- Broker auth: API key/secret in settings (Alpaca, Polygon)
- API auth: not yet implemented

---

*Architecture analysis: 2026-04-16*
