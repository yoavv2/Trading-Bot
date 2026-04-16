# External Integrations

**Analysis Date:** 2026-04-16

## APIs & External Services

**Market Data:**
- Polygon.io - Daily OHLCV bar ingestion for US equities
  - SDK/Client: Custom `PolygonClient` in `src/trading_platform/services/polygon.py`
  - Auth: `TRADING_PLATFORM_MARKET_DATA__POLYGON__API_KEY` (environment variable)
  - Base URL: `https://api.polygon.io`
  - Endpoints used:
    - `GET /v2/aggs/ticker/{symbol}/range/1/day/{from_date}/{to_date}` - Daily aggregates with pagination
  - Configuration: `PolygonProviderSettings` in `src/trading_platform/core/settings.py`
  - Retry policy: Exponential backoff with configurable max retries (default: 3) and backoff factor (default: 0.5)
  - Timeout: 30 seconds default

**Execution/Broker:**
- Alpaca - Paper-trading order submission and position/account snapshots
  - SDK/Client: Custom `AlpacaClient` in `src/trading_platform/services/alpaca.py`
  - Auth: 
    - `TRADING_PLATFORM_BROKER__ALPACA__API_KEY` (environment variable)
    - `TRADING_PLATFORM_BROKER__ALPACA__API_SECRET` (environment variable)
  - Base URL: `https://paper-api.alpaca.markets`
  - Endpoints used:
    - `POST /v2/orders` - Submit market orders
    - `GET /v2/orders` - List orders with status filtering
    - `GET /v2/account` - Get account/buying power snapshot
    - `GET /v2/positions` - List current positions
    - `GET /v2/account/activities/FILL` - Fetch executed fills
  - Configuration: `AlpacaBrokerSettings` in `src/trading_platform/core/settings.py`
  - Retry policy: Exponential backoff (max retries: 3, backoff factor: 0.5)
  - Timeout: 30 seconds default
  - Status codes handled: 401/403 (auth errors), 429/500/502/503/504 (transient)

## Data Storage

**Databases:**
- PostgreSQL 16 (Alpine)
  - Connection: Via `TRADING_PLATFORM_DATABASE__*` environment variables
  - Client: SQLAlchemy 2.0.0+ with `psycopg` driver
  - Pooling: Connection pooling with `pool_pre_ping=True` for stale connection cleanup
  - Models location: `src/trading_platform/db/models/`
  - Session management: `src/trading_platform/db/session.py`

**File Storage:**
- Local filesystem only
  - Data directory: `.data/` (configurable via `TRADING_PLATFORM_PATHS__DATA_DIR`)
  - Contains backtest output, ingest logs, and local state files

**Caching:**
- None - Application uses database as single source of truth
- SQLAlchemy engine and session factories are cached in-process via `_ENGINE_CACHE` and `_SESSION_FACTORY_CACHE` dictionaries

## Authentication & Identity

**Auth Provider:**
- Custom API key-based authentication for external services
  - Polygon.io: Bearer token in `Authorization` header
  - Alpaca: Custom headers `APCA-API-KEY-ID` and `APCA-API-SECRET-KEY`
- Single-user operator mode - no per-user authentication
  - `TRADING_PLATFORM_APP__OPERATOR_MODE` = "single_user"

**Security Notes:**
- Credentials stored as environment variables (never in code)
- `.env` files supported via `pydantic-settings` but not committed
- Alpaca client validates credentials on initialization and raises `AlpacaAuthError` on 401/403

## Monitoring & Observability

**Error Tracking:**
- None detected

**Logs:**
- Structured JSON logging to stdout/stderr
  - Framework: Python logging module with custom configuration
  - Format: JSON with contextual metadata
  - Service name: `trading-platform-api` (worker: `trading-platform-worker`)
  - Configuration: `LoggingSettings` in `src/trading_platform/core/settings.py`
  - Location: `src/trading_platform/core/logging.py`
  - Configured level: Defaults to INFO (overridable via `TRADING_PLATFORM_LOGGING__LEVEL`)
  - Example logs: `polygon_fetch_bars`, `polygon_request_retry`, `alpaca_request_retry`, `market_sessions_upserted`

## CI/CD & Deployment

**Hosting:**
- Docker containers (application containerized)
  - Base image: `python:3.13-slim`
  - Built from `Dockerfile` in project root
  - Entry points:
    - API: `uvicorn trading_platform.api.app:app --host 0.0.0.0 --port 8000`
    - Worker: `python -m trading_platform.worker serve --interval-seconds 30`

**Deployment Stack:**
- Docker Compose for local/development:
  - Service: `db` (PostgreSQL 16-Alpine)
  - Service: `api` (FastAPI server on port 8000)
  - Service: `worker` (Background task processor)
  - Health checks on database readiness before dependent services start
  - Volume: `postgres_data` for data persistence

**CI Pipeline:**
- None detected

## Environment Configuration

**Required env vars:**
- `TRADING_PLATFORM_MARKET_DATA__POLYGON__API_KEY` - Polygon.io API key (fails if missing)
- `TRADING_PLATFORM_BROKER__ALPACA__API_KEY` - Alpaca API key (fails if missing for live trading)
- `TRADING_PLATFORM_BROKER__ALPACA__API_SECRET` - Alpaca API secret (fails if missing for live trading)

**Recommended env vars:**
- `TRADING_PLATFORM_DATABASE__HOST` - Database host (default: `db` in docker-compose)
- `TRADING_PLATFORM_DATABASE__PORT` - Database port (default: 5432)
- `TRADING_PLATFORM_DATABASE__NAME` - Database name (default: `trading_platform`)
- `TRADING_PLATFORM_DATABASE__USER` - Database user (default: `trading_platform`)
- `TRADING_PLATFORM_DATABASE__PASSWORD` - Database password (default: `trading_platform`)
- `TRADING_PLATFORM_APP__ENVIRONMENT` - Environment (local/test/development/staging/production)
- `TRADING_PLATFORM_API__PORT` - API port (default: 8000)

**Secrets location:**
- Environment variables (`.env` file or shell exports)
- Credentials file example: `.env.example` documents expected variables
- No secrets are hardcoded or stored in configuration files

## Webhooks & Callbacks

**Incoming:**
- None detected

**Outgoing:**
- None - Application polls Alpaca and Polygon.io; does not register webhooks

## HTTP Client Configuration

**Client Library:**
- httpx 0.28.0+ for all external API calls
- Synchronous client mode (used in both API and worker contexts)
- Features:
  - Timeout enforcement (30 seconds default)
  - Automatic retry with exponential backoff for transient errors
  - Custom header injection for authentication
  - JSON request/response handling

**Error Handling:**
- Transient errors (429, 500-504): Retry with backoff
- Authentication errors (401, 403): Raise non-recoverable error immediately
- Network errors (timeouts, connection errors): Retry with backoff
- Max retries: 3 (configurable per provider)
- Backoff factor: 0.5 (exponential: 0.5s, 1s, 2s)

---

*Integration audit: 2026-04-16*
