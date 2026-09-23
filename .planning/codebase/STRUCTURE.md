# Codebase Structure

**Analysis Date:** 2026-09-23

## Directory Layout

```
Trading-Bot-Project/
├── .claude/                    # Claude/agent-specific configuration
├── .planning/                  # Planning documents, roadmaps, phase plans
│   └── codebase/              # Architecture and structure docs (this location)
├── alembic/                    # Database schema migrations (Alembic)
│   ├── versions/              # Migration files (numbered SQL/Python)
│   ├── env.py                 # Migration environment configuration
│   └── script.py.mako         # Migration script template
├── config/                     # YAML configuration files
│   ├── app.yaml               # Base application configuration (API, logging, DB)
│   └── strategies/            # Strategy-specific configuration files
│       └── trend_following_daily.yaml
├── console/                    # Next.js frontend (React 19 operator console)
│   ├── src/
│   │   ├── app/              # Next.js App Router pages
│   │   │   ├── page.tsx      # Home (system status dashboard)
│   │   │   ├── paper/        # Paper trading pages
│   │   │   ├── runs/         # Strategy run pages
│   │   │   └── strategy/     # Strategy pages
│   │   ├── components/        # React components
│   │   │   ├── status/       # System status panels
│   │   │   ├── paper/        # Paper trading panels
│   │   │   ├── runs/         # Run detail components
│   │   │   ├── strategy/     # Strategy panels
│   │   │   ├── FetchMeta.tsx # Fetch timestamp/error display
│   │   │   ├── ErrorState.tsx
│   │   │   └── KillSwitchBanner.tsx
│   │   └── lib/              # Utilities
│   │       ├── api.ts        # API client (fetch with error handling)
│   │       └── useApiQuery.ts # React hook for API queries
│   ├── public/                # Static assets
│   ├── package.json           # npm dependencies
│   ├── tsconfig.json          # TypeScript configuration
│   ├── next.config.ts         # Next.js configuration (API proxy setup)
│   ├── vitest.config.ts       # Vitest test runner configuration
│   ├── eslint.config.mjs      # ESLint configuration
│   └── README.md              # Console-specific documentation
├── scripts/                    # Command-line tools and bootstrap scripts
│   ├── dry_run.py            # Worker dry-run bootstrap
│   ├── migrate.py            # Alembic migration wrapper
│   ├── seed_phase1.py        # Database seed script (initial strategy catalog)
│   └── [other CLI commands]
├── src/trading_platform/      # Python backend (installed as editable package)
│   ├── __init__.py
│   ├── api/                   # FastAPI application and routes
│   │   ├── app.py            # FastAPI app creation, lifespan, route mounting
│   │   ├── dependencies.py    # Dependency injection (services, filters)
│   │   └── routes/           # Endpoint definitions by domain
│   │       ├── health.py     # GET /health, /ready (liveness/readiness)
│   │       ├── jobs.py       # POST/GET /api/v1/jobs (submit/query/cancel)
│   │       ├── analytics.py  # GET /api/v1/analytics (run metrics)
│   │       ├── operations.py # POST /api/v1/operations (controls)
│   │       ├── runs.py       # GET /api/v1/runs (strategy run queries)
│   │       ├── strategies.py # GET /strategies (strategy metadata)
│   │       └── system.py     # GET /api/v1/system (app/db/catalog info)
│   ├── core/                  # Core application logic
│   │   ├── settings.py       # Typed configuration (Pydantic)
│   │   ├── logging.py        # Structured JSON logging setup
│   │   ├── startup.py        # Bootstrap validation and gates
│   │   └── log_sanitizer.py  # Log output filtering (secrets)
│   ├── db/                    # Database layer
│   │   ├── base.py           # SQLAlchemy base classes, TimestampedModel
│   │   ├── session.py        # Database session factory and scope context manager
│   │   └── models/           # SQLAlchemy ORM models
│   │       ├── __init__.py   # Model imports and re-exports
│   │       ├── job.py        # Job (generic work unit) model
│   │       ├── job_event.py  # JobEvent (immutable state change log)
│   │       ├── job_log.py    # JobLog (worker progress messages)
│   │       ├── job_mutation.py # JobMutation (idempotency tracking)
│   │       ├── strategy_run.py # StrategyRun (execution record)
│   │       ├── backtest_trade.py # BacktestTrade (simulated fills)
│   │       ├── paper_order.py    # PaperOrder (simulated orders)
│   │       ├── daily_bar.py      # DailyBar (market data)
│   │       └── [other models]
│   ├── orchestration/         # Job submission and cancellation
│   │   └── job_mutations.py  # JobOrchestrationService (idempotent mutations)
│   ├── jobs/                  # Job framework (registry, contracts, lifecycle)
│   │   ├── registry.py       # JobRegistry (in-memory handler lookup)
│   │   ├── contracts.py      # JobHandler protocol and related types
│   │   ├── dependencies.py   # Job submission and dependency resolution
│   │   ├── queue.py          # Job queue management
│   │   ├── runner.py         # Job execution coordination
│   │   ├── cancellation.py   # Job cancellation logic
│   │   └── lifecycle.py      # Job state transitions
│   ├── services/              # Domain business logic
│   │   ├── job_reads.py      # JobReadService (job queries and filtering)
│   │   ├── analytics.py      # Analytics aggregation (equity curves, metrics)
│   │   ├── backtesting.py    # Backtest simulation engine
│   │   ├── backtest_reporting.py # Result analysis and metrics
│   │   ├── operator_status.py   # System health and readiness checks
│   │   ├── operator_controls.py # Kill switch and operator commands
│   │   ├── operator_reads.py    # Read queries for operator UI
│   │   ├── risk.py           # Risk evaluation pipeline
│   │   ├── execution/        # Broker execution (paper, live)
│   │   │   ├── engine.py
│   │   │   └── paper.py      # Paper trading fill simulation
│   │   ├── reconciliation/   # Order/fill reconciliation
│   │   ├── config/           # Strategy configuration validation
│   │   ├── ingestion.py      # Market data ingestion coordination
│   │   ├── data.py           # Data access helpers
│   │   ├── alpaca.py         # Alpaca broker API integration
│   │   ├── polygon.py        # Polygon data provider integration
│   │   └── bootstrap.py      # Service initialization
│   ├── strategies/            # Strategy framework and implementations
│   │   ├── registry.py       # StrategyRegistry (lookup by ID)
│   │   ├── base.py           # Strategy base class and protocol
│   │   ├── signals.py        # Signal generation utilities
│   │   └── trend_following_daily/ # Trend following strategy implementation
│   │       ├── __init__.py
│   │       ├── strategy.py   # Strategy logic (signal generation, parameters)
│   │       └── config.py     # Configuration schema
│   ├── worker/                # Worker CLI and commands
│   │   ├── __main__.py       # Entry point, command dispatch
│   │   ├── parser.py         # Argument parser construction
│   │   └── commands/         # Command implementations
│   │       ├── serve.py      # Run worker loop (poll for jobs)
│   │       ├── dry_run.py    # Single dry-run execution
│   │       └── [other commands]
│   └── bootstrap.py           # Old entry point (superseded by api/app.py)
├── tests/                     # pytest test suite
│   ├── conftest.py           # pytest fixtures and shared configuration
│   ├── test_app_boot.py      # FastAPI bootstrap tests
│   ├── test_job_*.py         # Job framework tests (registry, runner, cancellation)
│   ├── test_paper_*.py       # Paper trading tests
│   ├── test_backtest_*.py    # Backtesting tests
│   ├── test_api_reads.py     # API read endpoint tests
│   ├── test_startup_validation.py # Configuration validation
│   ├── test_dry_run.py       # Worker dry-run tests
│   └── [other test files]
├── .git/                      # Git repository
├── .venv/                     # Python virtual environment (local development)
├── .env                       # Environment variables (secrets, do not commit)
├── .env.example               # Template for .env
├── .gitignore                 # Git ignore rules
├── .pre-commit-config.yaml    # Pre-commit hooks (linting, mypy)
├── Dockerfile                 # Docker image for API and worker
├── docker-compose.yml         # Local services (PostgreSQL, API, worker)
├── Makefile                   # Development workflow shortcuts
├── pyproject.toml             # Python package definition, tool config (pytest, ruff, mypy)
├── alembic.ini                # Alembic CLI configuration
├── README.md                  # Project documentation
└── render.yaml                # Render.com deployment configuration
```

## Directory Purposes

**`alembic/`:**
- Purpose: Database schema version control
- Contains: Migration files, environment setup
- Key files: `versions/` (numbered migrations), `env.py` (Alembic runtime config)

**`config/`:**
- Purpose: Application and strategy configuration (YAML format)
- Contains: app.yaml (base config), strategies/*.yaml (strategy-specific)
- Key files: `app.yaml` (API host/port, logging, database, strategy catalog)

**`console/`:**
- Purpose: Operator dashboard and monitoring UI
- Contains: Next.js frontend with React components
- Key files: `src/app/page.tsx` (home dashboard), `src/lib/api.ts` (API client)
- Generated: `.next/` (build output, not committed)

**`scripts/`:**
- Purpose: Utility scripts for bootstrapping, migrations, seeding
- Contains: One-off CLI commands
- Key files: `migrate.py`, `seed_phase1.py`, `dry_run.py`

**`src/trading_platform/`:**
- Purpose: Main Python backend package
- Contains: API, services, models, orchestration, strategies, worker
- Package structure: Installed as editable (`pip install -e .`) from pyproject.toml

**`src/trading_platform/api/`:**
- Purpose: HTTP API layer (FastAPI)
- Contains: App factory, route handlers, dependency injection
- Key files: `app.py` (FastAPI bootstrap), `routes/` (endpoint definitions)

**`src/trading_platform/db/`:**
- Purpose: Database persistence layer (SQLAlchemy + PostgreSQL)
- Contains: ORM models, session factory
- Key files: `models/job.py` (job state), `models/strategy_run.py` (execution records)

**`src/trading_platform/orchestration/`:**
- Purpose: Idempotent job submission and state mutations
- Contains: Job orchestration service
- Key files: `job_mutations.py` (submit/cancel with idempotency)

**`src/trading_platform/jobs/`:**
- Purpose: Generic job framework (registry, contracts, lifecycle)
- Contains: Handler registry, execution coordination, cancellation logic
- Key files: `registry.py` (handler lookup), `runner.py` (execution), `cancellation.py`

**`src/trading_platform/services/`:**
- Purpose: Domain business logic (analytics, backtesting, risk, execution)
- Contains: Specialized service classes for different domains
- Key files: `job_reads.py` (queries), `backtesting.py` (simulation), `risk.py` (risk eval)

**`src/trading_platform/strategies/`:**
- Purpose: Strategy framework and implementations
- Contains: Registry, base class, individual strategy modules
- Key files: `base.py` (Strategy protocol), `trend_following_daily/` (first strategy)

**`src/trading_platform/core/`:**
- Purpose: Core application infrastructure
- Contains: Settings, logging, startup validation
- Key files: `settings.py` (typed config), `logging.py` (structured logging)

**`src/trading_platform/worker/`:**
- Purpose: Background job execution (CLI entrypoint)
- Contains: Command parser and handlers
- Key files: `__main__.py` (dispatch), `commands/` (implementations)

**`tests/`:**
- Purpose: pytest test suite
- Contains: Unit, integration, and system tests
- Pattern: Test files co-located with package modules (e.g., `test_job_registry.py` tests `jobs/registry.py`)

## Key File Locations

**Entry Points:**
- `src/trading_platform/api/app.py:main()` - FastAPI server startup
- `src/trading_platform/worker/__main__.py:main()` - Worker CLI dispatch
- `console/src/app/page.tsx` - Frontend home page

**Configuration:**
- `src/trading_platform/core/settings.py` - Typed settings definitions
- `config/app.yaml` - Base application configuration
- `config/strategies/trend_following_daily.yaml` - Strategy configuration
- `pyproject.toml` - Package definition and tool configuration
- `console/next.config.ts` - Next.js configuration (API proxy setup)

**Core Logic:**
- `src/trading_platform/orchestration/job_mutations.py` - Job submission/cancellation
- `src/trading_platform/services/job_reads.py` - Job queries and filtering
- `src/trading_platform/jobs/registry.py` - Handler resolution
- `src/trading_platform/services/backtesting.py` - Backtest simulation engine
- `src/trading_platform/services/risk.py` - Risk evaluation pipeline

**Testing:**
- `tests/conftest.py` - Pytest fixtures (database, app instance, session scope)
- `tests/test_job_registry.py` - Job registry tests
- `tests/test_app_boot.py` - API bootstrap tests
- `tests/test_paper_execution.py` - Paper trading tests
- `tests/test_backtest_runner.py` - Backtest simulation tests

## Naming Conventions

**Files:**
- **Service classes:** `*_service.py` or `{domain}.py` (e.g., `job_reads.py`, `risk.py`)
- **Model files:** `{entity}.py` (e.g., `job.py`, `strategy_run.py`)
- **Test files:** `test_{module}.py` (e.g., `test_job_registry.py`)
- **Configuration:** `{app|service}.yaml` or `{name}.config.{ext}`
- **Routes:** `{domain}.py` (e.g., `jobs.py`, `runs.py`)

**Directories:**
- **Packages:** lowercase with underscores (e.g., `trading_platform`, `job_reads`)
- **Modules within packages:** lowercase with underscores (e.g., `base.py`, `registry.py`)
- **Feature collections:** lowercase plural (e.g., `services/`, `strategies/`)
- **Next.js routes:** directory per route segment (e.g., `app/runs/[runId]/`)

**Python Functions/Classes:**
- **Classes:** PascalCase (e.g., `JobRegistry`, `JobOrchestrationService`, `UnknownJobTypeError`)
- **Functions:** snake_case (e.g., `submit_job()`, `resolve_handler()`)
- **Constants:** UPPER_SNAKE_CASE (e.g., `MAX_IDEMPOTENCY_KEY_LENGTH`)
- **Private members:** `_leading_underscore` (e.g., `_handlers` dict in JobRegistry)

**React Components:**
- **Components:** PascalCase file names (e.g., `HealthPanel.tsx`)
- **Hooks:** `use*` prefix (e.g., `useApiQuery.ts`)
- **Types:** `.ts` extension with PascalCase (e.g., `types.ts` inside component directories)

## Where to Add New Code

**New Feature:**
- **Backend API endpoint:** Create route file in `src/trading_platform/api/routes/{domain}.py` with APIRouter; include in `app.py`
- **Backend service logic:** Add class in `src/trading_platform/services/{domain}.py` implementing the domain logic
- **Database model:** Add ORM class in `src/trading_platform/db/models/{entity}.py` extending Base and TimestampedModel
- **Tests:** Create `tests/test_{feature}.py` using pytest fixtures from `conftest.py`

**New Job Type:**
- **Handler:** Create module in `src/trading_platform/jobs/` or extend existing command in `worker/commands/`
- **Submission spec:** Implement JobSubmissionSpec protocol for payload validation
- **Registration:** Register handler and spec in `build_default_registry()` in `src/trading_platform/jobs/registry.py`
- **Tests:** Add tests in `tests/test_job_*.py` for handler behavior and edge cases

**New Strategy:**
- **Implementation:** Create directory `src/trading_platform/strategies/{strategy_name}/`
- **Modules:** Implement `strategy.py` (signal generation) and `config.py` (configuration schema)
- **Configuration:** Add YAML file at `config/strategies/{strategy_name}.yaml`
- **Registry:** Strategy auto-discovered via `StrategyRegistry.discover()`

**New Frontend Component:**
- **Component file:** Create in `console/src/components/{domain}/{ComponentName}.tsx`
- **Types:** If complex, add `console/src/components/{domain}/types.ts`
- **Tests:** Create `console/src/components/{domain}/{ComponentName}.test.tsx` using vitest
- **Page integration:** Import and use in appropriate page under `console/src/app/`

**New Configuration:**
- **Application config:** Add key to `config/app.yaml` and corresponding Pydantic model in `settings.py`
- **Strategy config:** Add to `config/strategies/{strategy_name}.yaml` and strategy's `config.py`
- **Environment overrides:** Use `TRADING_PLATFORM_` prefix with `__` nesting (e.g., `TRADING_PLATFORM_API__PORT=8001`)

## Special Directories

**`.venv/`:**
- Purpose: Python virtual environment (local development only)
- Generated: Yes (via `python -m venv`)
- Committed: No

**`.next/`:**
- Purpose: Next.js build output
- Generated: Yes (via `npm run build`)
- Committed: No

**`.planning/`:**
- Purpose: Phase planning documents, roadmaps, analysis
- Generated: Semi-automatically via GSD commands
- Committed: Yes

**`alembic/versions/`:**
- Purpose: Database migration history
- Generated: Via `alembic revision` or manual creation
- Committed: Yes (immutable history)

**`.mypy_cache/` and `.pytest_cache/`:**
- Purpose: Tool caches
- Generated: Yes (during tool runs)
- Committed: No

---

*Structure analysis: 2026-09-23*
