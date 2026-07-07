# External Integrations

**Analysis Date:** 2026-07-07

## APIs & External Services

**Market Data:**
- Polygon.io - Historical daily OHLCV bars and symbol metadata
  - SDK/Client: Custom httpx-based client in `src/trading_platform/services/polygon.py`
  - Auth: Bearer token via `TRADING_PLATFORM_MARKET_DATA__POLYGON__API_KEY`
  - Usage: `PolygonClient` for `/v2/aggs/ticker/{symbol}/range/` endpoint
  - Config source: `src/trading_platform/core/settings.py::PolygonProviderSettings`

**Broker & Execution:**
- Alpaca Markets - Paper trading and order submission (not live)
  - SDK/Client: Custom httpx-based client in `src/trading_platform/services/alpaca.py`
  - Auth: Two credentials via `TRADING_PLATFORM_BROKER__ALPACA__API_KEY` and `TRADING_PLATFORM_BROKER__ALPACA__API_SECRET`
  - Base URL: `https://paper-api.alpaca.markets` (configurable in `config/app.yaml`)
  - Usage:
    - POST `/v2/orders` - Order submission
    - GET `/v2/orders` - List orders
    - GET `/v2/account/activities/FILL` - Fetch fills/executions
    - GET `/v2/positions` - Current positions
    - GET `/v2/account` - Account snapshot
  - Config source: `src/trading_platform/core/settings.py::AlpacaBrokerSettings`

## Data Storage

**Databases:**
- PostgreSQL 16+
  - Connection: `postgresql+psycopg://user:password@host:5432/trading_platform`
  - Driver: psycopg (binary) 3.2+
  - Client: SQLAlchemy 2.0+ ORM with synchronous session factory
  - Configuration: `src/trading_platform/core/settings.py::DatabaseSettings`
  - Session management: `src/trading_platform/db/session.py`
  - Migrations: Alembic 1.18+ (migrations in `alembic/versions/`)

**Database Models:**
Located in `src/trading_platform/db/models/`:
- `daily_bar.py` - Market data from Polygon
- `symbol.py` - Symbol metadata and universe
- `market_session.py` - Trading session calendar
- `strategy.py` - Strategy registry
- `strategy_run.py` - Historical backtest/paper runs
- `backtest_equity_snapshot.py`, `backtest_metric.py`, `backtest_signal.py`, `backtest_trade.py` - Backtest results
- `paper_order.py`, `paper_fill.py` - Paper trading execution records
- `execution_event.py`, `order_event.py` - Execution state machine events
- `position.py` - Current portfolio positions
- `risk_event.py` - Risk constraint violations
- `account_snapshot.py` - Broker account state snapshots
- `system_control.py` - Global system state (kill switches, operator mode)

**File Storage:**
- Local filesystem only
- Data directory: `.data/` (configurable as `paths.data_dir` in `config/app.yaml`)
- Backtest reports: `.data/backtest-reports/{run_id}/`

**Caching:**
- In-memory engine/session factory caching in `src/trading_platform/db/session.py`
- Settings loading cache via `@lru_cache` in `src/trading_platform/core/settings.py::load_settings()`
- No distributed caching (Redis, Memcached, etc.)

## Authentication & Identity

**Auth Provider:**
- Custom - No third-party identity provider
- Operator mode: Single-user only (hardcoded in `src/trading_platform/core/settings.py::AppMetadata.operator_mode`)

**API Auth Patterns:**
- No authentication on REST endpoints (internal tool)
- External service authentication:
  - Polygon: Bearer token in Authorization header
  - Alpaca: Custom headers `APCA-API-KEY-ID` and `APCA-API-SECRET-KEY`

## Monitoring & Observability

**Error Tracking:**
- Not detected - No Sentry, Rollbar, or similar integration

**Logs:**
- Structured JSON logging to stdout
- Logger configuration: `src/trading_platform/core/logging.py::configure_logging()`
- Log level configured in `config/app.yaml::logging.level` (default: INFO)
- Service name: `logging.service` (default: "trading-platform-api")

**Retry Logic:**
- Polygon client: Exponential backoff configured in `PolygonProviderSettings` (max_retries, retry_backoff_factor)
- Alpaca client: Exponential backoff configured in `AlpacaBrokerSettings` (max_retries, retry_backoff_factor)
- Transient HTTP errors (429, 5xx) trigger retries; auth errors (401, 403) fail immediately

## CI/CD & Deployment

**Hosting:**
- Docker containers (development and production)
- Docker Compose for local development: `docker-compose.yml`
- Images built from `Dockerfile` (python:3.13-slim base)

**Local Development:**
- Makefile targets for common operations:
  - `make up` / `make down` - Start/stop docker-compose
  - `make migrate` - Run database migrations
  - `make test` - Run pytest suite
  - `make ingest-bars`, `make backtest`, `make run-paper-session` - CLI operations

**CI Pipeline:**
- Not detected - No GitHub Actions, GitLab CI, CircleCI, or similar configuration

## Environment Configuration

**Required env vars:**
- `TRADING_PLATFORM_MARKET_DATA__POLYGON__API_KEY` - Polygon.io API key (required)
- `TRADING_PLATFORM_BROKER__ALPACA__API_KEY` - Alpaca API key (required)
- `TRADING_PLATFORM_BROKER__ALPACA__API_SECRET` - Alpaca API secret (required)

**Database env vars (optional - override defaults in config/app.yaml):**
- `TRADING_PLATFORM_DATABASE__HOST` - PostgreSQL host (default: localhost)
- `TRADING_PLATFORM_DATABASE__PORT` - PostgreSQL port (default: 5432)
- `TRADING_PLATFORM_DATABASE__NAME` - Database name (default: trading_platform)
- `TRADING_PLATFORM_DATABASE__USER` - Database user (default: trading_platform)
- `TRADING_PLATFORM_DATABASE__PASSWORD` - Database password (default: trading_platform)

**Docker Compose env vars:**
- `POSTGRES_DB` - Database name (default: trading_platform)
- `POSTGRES_USER` - Database user (default: trading_platform)
- `POSTGRES_PASSWORD` - Database password (default: trading_platform)

**Secrets location:**
- Environment variables (recommended for production)
- `.env` file (for local development, via pydantic-settings)
- NOTE: .env files should never be committed

**Configuration precedence (highest to lowest):**
1. Environment variables with `TRADING_PLATFORM_` prefix
2. Strategy YAML files from `config/strategies/`
3. Main config file `config/app.yaml`
4. Pydantic model defaults in `src/trading_platform/core/settings.py`

## Webhooks & Callbacks

**Incoming:**
- Not detected - No webhook endpoints

**Outgoing:**
- Not detected - No external webhook notifications

---

*Integration audit: 2026-07-07*
