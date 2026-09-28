# Trading Strategy Platform

Trading Strategy Platform is a local-first Python foundation for a single-user, auditable strategy system. The current repository is the first operational slice of that platform: typed configuration loading, a FastAPI control plane, strategy registration, PostgreSQL persistence, Alembic migrations, and and the initial `TrendFollowingDailyV1` strategy.

This is not a live trading system yet. It is the Phase 1 foundation for one.

## Current Scope

Implemented today:

- FastAPI application with `/health`, `/ready`, `/strategies`, and `/api/v1/system`
- File-first configuration using `config/app.yaml` and `config/strategies/*.yaml`
- Environment variable overrides via the `TRADING_PLATFORM_` prefix
- Strategy registry with the first registered strategy: `trend_following_daily`
- PostgreSQL persistence for strategy catalog entries and Job-linked run records
- Alembic migration flow for schema management
- Seed script for the initial strategy catalog entry
- Operator console Jobs and controls as the sole manual mutation surface
- Docker Compose services for PostgreSQL, API, and a placeholder worker
- Pytest coverage for app boot, strategy registry, and migrations

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
│   ├── export_backtest_report.py / generate_signals.py / operator_status.py / report_strategy_analytics.py  # read/report tools
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

7. Run operations (backtests, data ingestion, risk, paper sessions, reconciliation)
   as Jobs from the operator console at `/jobs/new`, and manage the kill switch
   and strategy enable/disable from `/controls`. The old per-operation scripts and
   Makefile targets have been removed; the HTTP Job API is the only mutation path.

### Docker Compose Notes

`docker compose up --build -d` starts `db`, runs a one-shot `migrate` service (`alembic upgrade head`, using the migration assets baked into the image), then starts `api` and `worker` once migrations succeed. The image pins its config location via `TRADING_PLATFORM_CONFIG_FILE=/app/config/app.yaml` and `TRADING_PLATFORM_STRATEGY_CONFIG_DIR=/app/config/strategies`, because the installed package cannot locate the repo `config/` directory on its own.

The `scripts/` directory is not bundled; run seeding and other scripts from the host repository. If host port 5432 is already in use (e.g. a native Postgres), set `POSTGRES_HOST_PORT` to publish the compose database on another port.

## Common Commands

The `Makefile` wraps the main development flows:

```bash
make up         # Start db/api/worker with Docker Compose
make down       # Stop containers and remove orphans
make logs       # Follow db/api/worker logs
make migrate    # Apply Alembic migrations from the host environment
make seed       # Seed the initial strategy record
make export-backtest-report  # Read-only backtest report export
make generate-signals        # Read-only signal evaluation
make test       # Run the current test suite
```

All mutating operations run as Jobs from the console (`/jobs/new`) and
controls from `/controls`; there are no Makefile targets for them.

## Operator Console

A read-only Next.js operator console lives at `console/`. It proxies to this
FastAPI app's read surface (no new backend capabilities) so an operator can see
run/strategy/analytics/system status without touching the API directly. Start
it with:

```bash
make console
```

See `console/README.md` for setup (`.env.local`) and the proxy design.

## Break-glass Kill Switch

Every normal way to change the global kill switch or a strategy's
enabled/disabled state goes through the console's mutation-gated HTTP API
(`OperatorControlService`, guarded by `mutations_enabled`). If the API is
unavailable, there is exactly one supported bypass, and it is trip-only:

```bash
docker compose run --rm worker python -m trading_platform.worker kill-switch-trip --reason "api down"
```

This is the worker CLI's sole exception to "the HTTP Job API is the only
manual mutation surface" (see `docs/gsd/decisions/D-15` or 20-CONTEXT.md).
It writes the same `StrategyRun` (`operator_control`, `trigger_source:
break_glass_cli`) and `ExecutionEvent` (`kill_switch_trip`) audit rows as
the HTTP control, requires a non-empty `--reason` (max 500 characters), and
works with no broker credentials configured and no `mutations_enabled` flag
set, since it never reads that flag. It can only make the system safer: the
worker CLI has no reset, enable, or disable command. Once the API is back,
reset the kill switch (and re-enable/disable strategies) only through the
console/HTTP control.

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
- `strategy_runs`: persisted strategy run records

Job-driven operations create or refresh the strategy record and store run rows linked to their Job.

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
- strategy execution plumbing
- persistence of strategy and run metadata

It does not yet place orders, ingest candles, or run backtests.

## Roadmap Direction

The intended direction for the project is:

1. Historical data ingestion for the initial U.S. equities universe
2. Deterministic backtesting and persisted analytics
3. Paper trading through a broker integration
4. Stronger risk controls and observability
5. A future dashboard backed by FastAPI APIs
