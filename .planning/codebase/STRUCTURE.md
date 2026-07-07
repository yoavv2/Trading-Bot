# Codebase Structure

**Analysis Date:** 2026-07-07

## Directory Layout

```
Trading Bot Project/
├── src/trading_platform/           # Main application code
│   ├── __init__.py                 # Package marker
│   ├── core/                       # Cross-cutting concerns
│   │   ├── logging.py              # Structured logging setup
│   │   └── settings.py             # YAML config + env overrides
│   ├── api/                        # HTTP API layer
│   │   ├── app.py                  # FastAPI bootstrap
│   │   ├── dependencies.py         # FastAPI dependency injection
│   │   └── routes/                 # HTTP endpoint modules
│   │       ├── analytics.py        # Strategy analytics endpoints
│   │       ├── health.py           # Health check endpoints
│   │       ├── operations.py       # Operator control endpoints
│   │       ├── runs.py             # Strategy run history endpoints
│   │       ├── strategies.py       # Strategy discovery endpoints
│   │       └── system.py           # System status endpoints
│   ├── db/                         # Persistence layer
│   │   ├── session.py              # Engine/session management
│   │   ├── base.py                 # Base model class
│   │   └── models/                 # SQLAlchemy ORM entities
│   │       ├── __init__.py         # Model exports
│   │       ├── strategy.py         # Strategy master record
│   │       ├── strategy_run.py     # Audit record for each run
│   │       ├── daily_bar.py        # Market data (OHLCV)
│   │       ├── backtest_*.py       # Backtest results (trades, signals, metrics, equity)
│   │       ├── paper_order.py      # Paper order lifecycle
│   │       ├── paper_fill.py       # Fill records from broker
│   │       ├── execution_event.py  # Execution lifecycle events
│   │       ├── order_event.py      # Order state transitions
│   │       ├── risk_event.py       # Approved risk decisions
│   │       ├── position.py         # Current positions
│   │       ├── account_snapshot.py # Account state snapshots
│   │       ├── market_session.py   # Trading calendar
│   │       ├── symbol.py           # Security metadata
│   │       └── system_control.py   # Kill switch + operator toggles
│   ├── services/                   # Business logic layer
│   │   ├── bootstrap.py            # Strategy dry-run initialization
│   │   ├── backtesting.py          # Backtest engine
│   │   ├── backtest_reporting.py   # Backtest report export
│   │   ├── risk.py                 # Risk evaluation engine
│   │   ├── paper_execution.py      # Paper order submission/sync
│   │   ├── reconciliation.py       # Broker state reconciliation
│   │   ├── execution.py            # Low-level order submission
│   │   ├── alpaca.py               # Alpaca broker adapter
│   │   ├── analytics.py            # Strategy performance analytics
│   │   ├── market_data_access.py   # Daily bar queries + session logic
│   │   ├── ingestion.py            # Market data import from Polygon
│   │   ├── polygon.py              # Polygon API client
│   │   ├── calendar.py             # Market session calendar management
│   │   ├── operator_controls.py    # Kill switch + strategy toggles
│   │   ├── operator_reads.py       # Operator query service
│   │   ├── operator_status.py      # Operator status/diagnostics
│   │   ├── order_state_machine.py  # Order lifecycle transitions
│   │   ├── order_identity.py       # Order correlation logic
│   │   ├── portfolio.py            # Portfolio aggregation
│   │   └── data.py                 # Market data API abstraction
│   ├── strategies/                 # Strategy plugin layer
│   │   ├── base.py                 # BaseStrategy abstract class
│   │   ├── registry.py             # Strategy discovery + instantiation
│   │   ├── signals.py              # Signal types and batches
│   │   └── [implementations]/      # Specific strategy implementations
│   └── worker/                     # CLI entry point
│       ├── __main__.py             # Worker CLI with 20+ commands
│       └── __init__.py             # Package marker
│
├── config/                         # Configuration files
│   ├── app.yaml                    # Platform settings (host, port, DB, logging)
│   └── strategies/                 # Per-strategy YAML configs
│       └── trend_following_daily.yaml  # Example strategy config
│
├── alembic/                        # Database migrations
│   ├── alembic.ini                 # Alembic configuration
│   ├── env.py                      # Migration environment
│   ├── script.py.mako              # Migration template
│   └── versions/                   # Migration scripts
│
├── tests/                          # Test suite
│   ├── fixtures/                   # Shared test fixtures
│   ├── test_*.py                   # Unit/integration tests (18 test files)
│   └── conftest.py                 # Pytest configuration
│
├── scripts/                        # Standalone utility scripts
│   ├── sync_symbol_metadata.py     # Symbol metadata sync from Polygon
│   ├── sync_sessions.py            # Trading calendar sync
│   └── [other utilities]/          # Other administrative scripts
│
├── .planning/                      # GSD planning documents
│   ├── codebase/                   # Architecture/structure docs
│   └── phases/                     # Phase execution records
│
├── .data/                          # Generated data (ignored in git)
│   └── backtest-reports/           # Backtest output artifacts
│
├── pyproject.toml                  # Python package definition
├── alembic.ini                     # Alembic config
├── Dockerfile                      # Container definition
├── docker-compose.yml              # Local development environment
├── Makefile                        # Build targets
└── README.md                       # Project documentation
```

## Directory Purposes

**`src/trading_platform/`:**
- Purpose: Main application package installed as `trading-platform`
- Contains: All source code organized by layer (api, services, db, strategies, etc.)
- Key files: `__init__.py` (empty package marker)

**`src/trading_platform/core/`:**
- Purpose: Cross-cutting concerns used throughout application
- Contains: Settings loader, structured logging configuration
- Key files: `settings.py` (configuration assembly), `logging.py` (JSON logging setup)

**`src/trading_platform/api/`:**
- Purpose: HTTP API for operator inspection and control
- Contains: FastAPI application, route modules, dependency injection setup
- Key files: `app.py` (server bootstrap), `dependencies.py` (DI container), `routes/` (endpoints)

**`src/trading_platform/db/`:**
- Purpose: Persistence layer (ORM, session management, models)
- Contains: 18 SQLAlchemy model files (one entity per file), session factory
- Key files: `session.py` (database connection), `base.py` (shared model base), `models/__init__.py` (exports)

**`src/trading_platform/services/`:**
- Purpose: Business logic and domain operations
- Contains: 23 service modules for trading operations, analytics, broker integration, etc.
- Key files: `backtesting.py` (backtest engine), `paper_execution.py` (order submission), `analytics.py` (performance reporting)

**`src/trading_platform/strategies/`:**
- Purpose: Strategy plugin system with abstract contract and registry
- Contains: Base class, signal types, registry for strategy lookup and instantiation
- Key files: `base.py` (abstract strategy contract), `registry.py` (plugin discovery), `signals.py` (signal types)

**`src/trading_platform/worker/`:**
- Purpose: CLI entry point for non-HTTP operations
- Contains: 20+ subcommands for backtest, risk, execution, analytics, control operations
- Key files: `__main__.py` (argument parser + command routing)

**`config/`:**
- Purpose: YAML configuration files (not code)
- Contains: `app.yaml` (platform settings), `strategies/` (per-strategy configs)
- Key files: `app.yaml` (host, port, database URL, logging level), `strategies/*.yaml` (strategy parameters)

**`alembic/`:**
- Purpose: Database migration management
- Contains: Version control for schema changes via Alembic (SQLAlchemy migration tool)
- Key files: `versions/` (individual migration scripts with up/down)

**`tests/`:**
- Purpose: Test suite (unit + integration)
- Contains: 18 test files covering core functionality
- Key files: `conftest.py` (pytest setup, fixtures), `test_*.py` (test cases)

**`scripts/`:**
- Purpose: Standalone utility scripts for administrative tasks
- Contains: Symbol metadata sync, market session calendar sync, other maintenance utilities
- Key files: `sync_symbol_metadata.py` (ticker overview sync from Polygon), `sync_sessions.py` (trading calendar)

**`.planning/`:**
- Purpose: GSD (Guided Software Development) planning and phase records
- Contains: Architecture/structure analysis documents, phase execution records
- Key files: `codebase/ARCHITECTURE.md`, `codebase/STRUCTURE.md`, phase logs

**`.data/`:**
- Purpose: Generated runtime data (not committed)
- Contains: Backtest reports, market data snapshots, simulation outputs
- Key files: `backtest-reports/` (UUID-keyed directories with report artifacts)

## Key File Locations

**Entry Points:**
- `src/trading_platform/api/app.py`: HTTP server startup (FastAPI)
- `src/trading_platform/worker/__main__.py`: CLI worker with 20+ commands
- `src/trading_platform/api/app.py:main()`: `trading-platform-api` command entry
- `src/trading_platform/worker/__main__.py:main()`: `trading-platform-worker` command entry

**Configuration:**
- `config/app.yaml`: Platform-level settings (host, port, DB, logging, paths)
- `config/strategies/*.yaml`: Per-strategy parameter overrides
- `src/trading_platform/core/settings.py`: Typed settings loader (merges YAML + env)

**Core Logic:**
- `src/trading_platform/services/backtesting.py`: Backtest simulation engine
- `src/trading_platform/services/risk.py`: Risk evaluation and position sizing
- `src/trading_platform/services/paper_execution.py`: Order submission and state sync
- `src/trading_platform/services/analytics.py`: Performance analytics and reporting
- `src/trading_platform/services/reconciliation.py`: Broker state reconciliation

**Testing:**
- `tests/conftest.py`: Pytest configuration and shared fixtures
- `tests/test_app_boot.py`: Application startup and settings tests
- `tests/test_backtest_runner.py`: Backtest engine tests
- `tests/test_risk_pipeline.py`: Risk evaluation tests
- `tests/test_paper_execution.py`: Order submission and sync tests

## Naming Conventions

**Files:**
- Service files: `snake_case.py` (e.g., `paper_execution.py`, `order_state_machine.py`)
- Model files: `snake_case.py` (e.g., `backtest_trade.py`, `daily_bar.py`)
- Test files: `test_<subject>.py` (e.g., `test_backtest_runner.py`)
- Route files: `snake_case.py` in `api/routes/` (e.g., `analytics.py`, `operations.py`)

**Directories:**
- Package directories: `snake_case` (e.g., `trading_platform`, `api`, `services`)
- Data directories: `kebab-case` (e.g., `backtest-reports`, `config`, `.data`)
- Version directories: UUID strings (e.g., `.data/backtest-reports/6aee5ae6-...`)

**Classes:**
- ORM models: `PascalCase` (e.g., `Strategy`, `PaperOrder`, `RiskEvent`)
- Services: `PascalCase` + "Service" suffix (e.g., `StrategyAnalyticsService`, `OperatorReadService`)
- Enums: `PascalCase` (e.g., `OrderLifecycleState`, `StrategyRunStatus`)
- Dataclasses: `PascalCase` (e.g., `StrategyMetadata`, `PaperExecutionCandidate`)

**Functions/Methods:**
- Module functions: `snake_case` (e.g., `run_backtest()`, `build_metadata()`)
- Private functions: Leading underscore (e.g., `_resolve_database_settings()`)

## Where to Add New Code

**New Feature (e.g., new trading signal type):**
- Primary code: `src/trading_platform/services/<feature_name>.py`
- Strategy integration: Update `src/trading_platform/strategies/signals.py` if new signal type
- Tests: `tests/test_<feature_name>.py`
- Example: Adding volatility evaluation would go in `services/volatility.py`, called from `services/risk.py`

**New API Endpoint:**
- Route module: `src/trading_platform/api/routes/<feature_name>.py`
- Register in: `src/trading_platform/api/app.py` (include_router call)
- Dependency injection: Add functions to `src/trading_platform/api/dependencies.py` if needed
- Tests: `tests/test_api_<feature_name>.py`

**New Strategy Implementation:**
- Implementation file: `src/trading_platform/strategies/<strategy_name>.py`
- Subclass: `BaseStrategy` from `src/trading_platform/strategies/base.py`
- Configuration: `config/strategies/<strategy_name>.yaml`
- Registration: Automatically discovered by `StrategyRegistry` if placed in same directory
- Tests: `tests/test_<strategy_name>_strategy.py`

**Database Model (new entity):**
- Model file: `src/trading_platform/db/models/<entity_name>.py`
- Export in: `src/trading_platform/db/models/__init__.py`
- Migration: Create Alembic migration in `alembic/versions/` via `alembic revision --autogenerate`
- Tests: Add assertions in relevant integration tests

**Utility Script:**
- Location: `scripts/<task_name>.py`
- CLI integration: Can be imported and called from `worker.__main__.py` if it needs to be a subcommand
- Standalone use: Can be run directly via `python scripts/<task_name>.py`

**Shared Utility Function:**
- If cross-cutting: `src/trading_platform/core/` (with caution, core should stay minimal)
- If domain-specific: Create in relevant service module or new `src/trading_platform/services/common.py`

## Special Directories

**`src/trading_platform/db/models/`:**
- Purpose: ORM model definitions (one file per entity)
- Generated: Migration files generated via Alembic
- Committed: All source files committed; migration scripts in `alembic/versions/`

**`.data/backtest-reports/`:**
- Purpose: Generated backtest output artifacts (CSV files, equity curves)
- Generated: Yes, by `BacktestReportingService` during backtest export
- Committed: No, in `.gitignore`

**`alembic/versions/`:**
- Purpose: Database schema change scripts
- Generated: Partially (can be auto-generated from model changes)
- Committed: Yes, all migration scripts tracked

**`tests/fixtures/`:**
- Purpose: Shared test data and fixture factories
- Generated: No, hand-written test data
- Committed: Yes, part of test suite

**`.planning/phases/`:**
- Purpose: Phase execution records and logs
- Generated: Yes, by GSD orchestrator during phase execution
- Committed: Yes, for audit trail

---

*Structure analysis: 2026-07-07*
