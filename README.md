# Trading Strategy Platform

Trading Strategy Platform is a local-first Python foundation for a single-user, auditable strategy system. The current repository is the first operational slice of that platform: typed configuration loading, a FastAPI control plane, strategy registration, PostgreSQL persistence, Alembic migrations, and a persisted dry-run flow for the initial `TrendFollowingDailyV1` strategy.

This is not a live trading system yet. It is the Phase 1 foundation for one.

## Current Scope

Implemented today:

- FastAPI application with `/health`, `/ready`, `/strategies`, and `/api/v1/system`
- File-first configuration using `config/app.yaml` and `config/strategies/*.yaml`
- Environment variable overrides via the `TRADING_PLATFORM_` prefix
- Strategy registry with the first registered strategy: `trend_following_daily`
- PostgreSQL persistence for strategy catalog entries and dry-run execution records
- Alembic migration flow for schema management
- Seed script for the initial strategy catalog entry
- Worker and script-based dry-run flow that persists a `strategy_runs` record
- Docker Compose services for PostgreSQL, API, and a placeholder worker
- Pytest coverage for app boot, strategy registry, migrations, and dry-run persistence

Not implemented yet:

- Historical market-data ingestion
- Backtesting
- Paper broker integration
- Real risk, execution, and analytics engines
- Live or paper trading automation beyond placeholder scaffolding

## Stack

- Python 3.12+
- FastAPI
- SQLAlchemy
- Alembic
- PostgreSQL 16
- Docker Compose
- pytest

## Repository Layout

```text
.
├── alembic/                    # Database migrations
├── config/
│   ├── app.yaml               # Base application/runtime config
│   └── strategies/
│       └── trend_following_daily.yaml
├── scripts/
│   ├── dry_run.py             # Persisted dry-run bootstrap CLI
│   ├── migrate.py             # Alembic wrapper
│   └── seed_phase1.py         # Seed initial strategy metadata
├── src/trading_platform/
│   ├── api/                   # FastAPI app and routes
│   ├── core/                  # Settings and logging
│   ├── db/                    # SQLAlchemy models and session helpers
│   ├── services/              # Placeholder data/risk/execution/analytics services
│   ├── strategies/            # Strategy contracts and implementations
│   └── worker/                # Worker CLI
├── tests/
├── docker-compose.yml
├── Dockerfile
├── Makefile
└── pyproject.toml
```

## Quick Start

### Recommended: local Python + Dockerized Postgres

This is the most complete development workflow right now because migrations and helper scripts run from the host repository.

1. Create an environment file:

   ```bash
   cp .env.example .env
   ```

2. Create a virtual environment and install dependencies:

   ```bash
   python3.12 -m venv .venv
   .venv/bin/pip install --upgrade pip
   .venv/bin/pip install -e '.[dev]'
   ```

3. Start PostgreSQL:

   ```bash
   docker compose up -d db
   ```

4. Apply database migrations:

   ```bash
   PYTHONPATH=src .venv/bin/python scripts/migrate.py upgrade head
   ```

5. Seed the initial strategy catalog entry:

   ```bash
   PYTHONPATH=src .venv/bin/python scripts/seed_phase1.py
   ```

6. Start the API:

   ```bash
   PYTHONPATH=src .venv/bin/python -m trading_platform.api.app
   ```

7. In another shell, run a dry bootstrap:

   ```bash
   PYTHONPATH=src .venv/bin/python -m trading_platform.worker dry-run --strategy trend_following_daily
   ```

### Docker Compose Notes

`docker compose up --build -d` starts the `db`, `api`, and `worker` services, but the current runtime image does not bundle the `alembic/` or `scripts/` directories. That means schema creation still needs to happen from the host repository before the API and worker are fully useful.

If you want a fully self-contained Docker workflow later, the image will need to include migration assets or a dedicated migration container.

## Common Commands

The `Makefile` wraps the main development flows:

```bash
make up         # Start db/api/worker with Docker Compose
make down       # Stop containers and remove orphans
make logs       # Follow db/api/worker logs
make migrate    # Apply Alembic migrations from the host environment
make seed       # Seed the initial strategy record
make dry-run    # Execute the worker dry-run for the default strategy
make test       # Run the current test suite
```

Override the default strategy for the dry-run flow with:

```bash
make dry-run STRATEGY=trend_following_daily
```

## Configuration Model

Runtime settings are assembled in this order:

1. Built-in typed defaults in `src/trading_platform/core/settings.py`
2. Base YAML from `config/app.yaml`
3. Strategy YAML files from `config/strategies/*.yaml`
4. Environment variable overrides from `.env` or the process environment

Environment overrides use the `TRADING_PLATFORM_` prefix and `__` as the nested delimiter.

Examples:

```bash
TRADING_PLATFORM_API__PORT=8001
TRADING_PLATFORM_DATABASE__HOST=localhost
TRADING_PLATFORM_STRATEGIES__TREND_FOLLOWING_DAILY__RISK__MAX_POSITIONS=7
```

The current default strategy configuration lives at `config/strategies/trend_following_daily.yaml` and defines:

- strategy id and display name
- enabled flag
- universe symbols
- moving-average windows
- basic risk parameters
- exit configuration

## API Surface

Current routes exposed by the FastAPI app:

- `GET /health` returns a basic liveness response
- `GET /ready` reports bootstrap and database readiness
- `GET /strategies` lists public metadata for registered strategies
- `GET /api/v1/system` returns application, API, strategy catalog, and database metadata

Example:

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/ready
curl http://127.0.0.1:8000/strategies
curl http://127.0.0.1:8000/api/v1/system
```

## Persistence

The current schema is intentionally minimal and centers on two tables:

- `strategies`: persisted strategy catalog metadata
- `strategy_runs`: persisted dry-run execution records

The dry-run flow creates or refreshes the strategy record and then stores a `dry_bootstrap` run with status transitions such as `pending`, `running`, and `succeeded`.

## Testing

Run the test suite with:

```bash
make test
```

Some tests require a reachable PostgreSQL instance on the configured host/port. The existing tests are designed for the local Compose database.

## Current Strategy

The first registered strategy is `TrendFollowingDailyV1`, exposed as `trend_following_daily`.

Its current role in the codebase is to prove:

- strategy discovery through a registry
- config-driven metadata
- dry-run execution plumbing
- persistence of strategy and run metadata

It does not yet place orders, ingest candles, or run backtests.

## Roadmap Direction

The intended direction for the project is:

1. Historical data ingestion for the initial U.S. equities universe
2. Deterministic backtesting and persisted analytics
3. Paper trading through a broker integration
4. Stronger risk controls and observability
5. A future dashboard backed by FastAPI APIs
