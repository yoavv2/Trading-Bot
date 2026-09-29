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
- Docker Compose PostgreSQL, plus an opt-in full container stack (`stack` profile)
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

### Local development (canonical)

There is exactly one supported way to run the platform locally: the API, the
Job worker and the operator console as host processes, started together by
`make dev`, against the PostgreSQL database named in `.env`
(`localhost:5432`, e.g. a native Homebrew PostgreSQL).

One-time setup:

1. Create an environment file and fill in the database and provider keys:

   ```bash
   cp .env.example .env
   ```

2. Create a virtual environment and install dependencies:

   ```bash
   python3.12 -m venv .venv
   .venv/bin/pip install --upgrade pip
   .venv/bin/pip install -e '.[dev]'
   make console-install
   cp console/.env.example console/.env.local
   ```

3. Make sure PostgreSQL is running on `localhost:5432`, then migrate and seed
   (re-run `make migrate` after pulling new migrations):

   ```bash
   make migrate
   make seed
   ```

Every day:

```bash
make dev
```

`make dev` refuses to start if port 8000 or 3000 is already taken (for example
by the Compose stack), then runs in one process group:

- **API** on `http://127.0.0.1:8000` with `--reload`: backend code changes
  under `src/` are picked up automatically. Mutations are enabled for this API
  process only (`TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED=true`); the
  application default stays off.
- **Job worker** (`python -m trading_platform.worker run-jobs`), restarted by
  `watchfiles` on changes under `src/`. A restart lets the in-flight Job finish
  for up to 30 seconds; a Job still running after that is reclaimed by the
  lost-lease sweep.
- **Console** on `http://localhost:3000`.

Press `Ctrl-C` to stop all three. Changes to `config/` or `.env` need a
`make dev` restart.

Run operations (backtests, data ingestion, risk, paper sessions,
reconciliation) as Jobs from the console at `/jobs/new`, and manage the kill
switch and strategy enable/disable from `/controls`. The HTTP Job API is the
only mutation path.

`make api`, `make worker` and `make console` run a single piece in the
foreground for debugging. `make api` is configuration-neutral: it reads
`mutations_enabled` from `.env`/config like any deployment, so mutating routes
return `403 mutations_disabled` unless you enable them there.

### Alternative: full Docker Compose stack

The containerized stack (`db`, one-shot `migrate`, `api`, `worker`) sits behind
the Compose `stack` profile. Use it **instead of** `make dev`, never alongside
it: its API binds port 8000 and its worker polls its own `db`.

```bash
make up      # docker compose --profile stack up --build -d
make logs    # follow db/api/worker logs
make down    # stop and remove the stack
```

Notes:

- The images contain an installed copy of `src/`; code changes need `make up`
  again (rebuild). Nothing reloads.
- The stack uses the Compose `db` (its own volume, unseeded) and only the
  environment set in `docker-compose.yml` (mutations enabled; no Polygon or
  Alpaca keys). Seeding and other `scripts/` run from the host repository.
- A bare `docker compose up` starts only `db`. If a native PostgreSQL already
  owns port 5432, set `POSTGRES_HOST_PORT` to publish the Compose database on
  another port.
- `migrate` runs `alembic upgrade head` (assets baked into the image) before
  `api` and `worker` start. The image pins `TRADING_PLATFORM_CONFIG_FILE` and
  `TRADING_PLATFORM_STRATEGY_CONFIG_DIR` to `/app/config`, because the
  installed package cannot locate the repo `config/` directory on its own.

## Common Commands

```bash
make dev        # Canonical local development: API + worker + console
make api        # API only, with --reload (configuration-neutral)
make worker     # Job worker only, restarted on code changes
make console    # Operator console only
make migrate    # Apply Alembic migrations to the .env database
make seed       # Seed the initial strategy record
make up         # Alternative: full Compose stack (stack profile)
make down       # Stop the Compose stack
make logs       # Follow Compose db/api/worker logs
make export-backtest-report  # Read-only backtest report export
make generate-signals        # Read-only signal evaluation
make test       # Run the current test suite
```

All mutating operations run as Jobs from the console (`/jobs/new`) and
controls from `/controls`; there are no Makefile targets for them.

## Operator Console

The Next.js operator console lives at `console/` and is started by `make dev`.
It proxies to this FastAPI app (no new backend capabilities). See
`console/README.md` for `.env.local` and the proxy design.

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
