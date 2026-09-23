# External Integrations

**Analysis Date:** 2026-09-23

## APIs & External Services

**Market Data:**
- Polygon.io - Daily OHLCV bar ingestion, symbol metadata refresh
  - SDK/Client: httpx (custom REST client in `src/trading_platform/services/polygon.py`)
  - Auth: API key via `TRADING_PLATFORM_MARKET_DATA__POLYGON__API_KEY`
  - Base URL: https://api.polygon.io
  - Usage: Market data provider for backtesting and live ingest jobs
  - Configuration: `PolygonProviderSettings` in `src/trading_platform/core/settings.py`

**Execution & Broker:**
- Alpaca - Paper trading order submission and position sync
  - SDK/Client: httpx (custom client in `src/trading_platform/services/alpaca.py`)
  - Auth: Key/Secret via `TRADING_PLATFORM_BROKER__ALPACA__API_KEY` and `__API_SECRET`
  - Base URL: https://paper-api.alpaca.markets (paper trading, not live)
  - Usage: Execute paper trades, sync positions and orders from broker
  - Configuration: `AlpacaBrokerSettings` in `src/trading_platform/core/settings.py`

## Data Storage

**Databases:**
- PostgreSQL 16 (local dev), Neon PostgreSQL (production free tier)
  - Connection: Environment variables `TRADING_PLATFORM_DATABASE__*`
  - Client: psycopg[binary] 3.2+ (sync + async support)
  - ORM: SQLAlchemy 2.0+
  - Models: `src/trading_platform/db/models/` (runs, trades, positions, bars, signals)
  - Connection URL: `postgresql+psycopg://[user]:[password]@[host]:[port]/[name]`

**File Storage:**
- Local filesystem only — `.data/` directory (not committed to git)
- Backtest results, exports, and temporary data stored locally
- No cloud storage integration (single-user, local-first architecture)

**Caching:**
- Not detected — no Redis, Memcached, or in-memory cache layer
- Database acts as primary persistent store
- Frontend uses `cache: "no-store"` in API calls to ensure fresh data

## Authentication & Identity

**Auth Provider:**
- Custom/None — Single-user operator mode only
- No OAuth (Google, GitHub, etc.)
- No API key authentication on read routes (public, unauthenticated GET endpoints)
- Mutation endpoints (POST/PUT) are also unauthenticated in current free-tier checkpoint
- Environment: `TRADING_PLATFORM_APP__OPERATOR_MODE = "single_user"`

**Implementation:**
- Settings validation enforces single-user constraints
- No JWT, session tokens, or credentials management
- Future multi-user support would require auth layer addition

## Monitoring & Observability

**Error Tracking:**
- Not detected — no Sentry, DataDog, or external APM
- Errors logged locally in JSON format via Python logging

**Logs:**
- JSON-structured logging via Python `logging` module
- Configuration: `src/trading_platform/core/logging.py`
- Output: stdout (captured by Docker/Render)
- Log level: Configurable via `TRADING_PLATFORM_LOGGING__LEVEL` (default INFO)
- Sanitization: Credential scrubbing via `src/trading_platform/core/log_sanitizer.py`

**Tracing:**
- Not detected — no distributed tracing (OpenTelemetry, Datadog, etc.)

## CI/CD & Deployment

**Hosting — Backend:**
- Render (Docker runtime)
  - Blueprint: `render.yaml` (free-tier read-only API deployment)
  - Region: Oregon (configurable, set close to database)
  - Service type: Web service (Python via Docker)
  - Health check: `/health` endpoint
  - Startup: `alembic upgrade head && uvicorn ...` (migrations + server)

**Hosting — Frontend:**
- Vercel (Next.js native platform)
  - Configuration: `.vercel/project.json` (Vercel project linking only)
  - Deployment: Connected to GitHub repo for auto-deploy on commits
  - Project: `trading-platform-console`

**Hosting — Database:**
- Neon (PostgreSQL)
  - Tier: Free (with caveats on compute/storage)
  - Connection: Direct host (not pooler) for long-lived containers
  - SSL: Auto-negotiated by psycopg default `sslmode=prefer`

**CI/CD Pipeline:**
- Not detected — no GitHub Actions, GitLab CI, or Jenkins workflows
- Deployment via manual push to Render/Vercel (Blueprint and Vercel UI)
- Pre-commit hooks locally (`pre-commit` framework)

**Build & Deployment:**
- Docker image: `python:3.13-slim` (both API and worker)
- Docker Compose: Local multi-service orchestration (db, api, worker)
- Dockerfile: Single image for API and worker (selectable via CMD)

## Environment Configuration

**Required Environment Variables:**

*Database (all environments):*
- `TRADING_PLATFORM_DATABASE__HOST` - PostgreSQL hostname
- `TRADING_PLATFORM_DATABASE__PORT` - PostgreSQL port (default 5432)
- `TRADING_PLATFORM_DATABASE__NAME` - Database name
- `TRADING_PLATFORM_DATABASE__USER` - Database user
- `TRADING_PLATFORM_DATABASE__PASSWORD` - Database password

*API (all environments):*
- `TRADING_PLATFORM_API__PORT` - API server port (default 8000)
- `TRADING_PLATFORM_APP__ENVIRONMENT` - Deployment environment (local/test/development/staging/production)
- `TRADING_PLATFORM_LOGGING__LEVEL` - Log level (default INFO)

*Market Data (worker/ingest only):*
- `TRADING_PLATFORM_MARKET_DATA__POLYGON__API_KEY` - Polygon.io API key (required for live bar ingest)

*Broker (worker/execution only):*
- `TRADING_PLATFORM_BROKER__ALPACA__API_KEY` - Alpaca API key
- `TRADING_PLATFORM_BROKER__ALPACA__API_SECRET` - Alpaca API secret

*Frontend (console/dev):*
- `TRADING_CONSOLE_API_BASE_URL` - Backend API base URL (default http://127.0.0.1:8000)

**Secrets Location:**
- `.env` file (local development, git-ignored)
- `.env.example` (template for local setup)
- `.env.production.example` (template for Render deployment)
- Render environment variables entered in dashboard (sync: false for secrets)
- Never committed to git; provided at runtime via environment

## Webhooks & Callbacks

**Incoming:**
- Not detected — no webhook endpoints for external services

**Outgoing:**
- Not detected — platform does not send webhooks to external services

**Job Polling:**
- Worker polls Alpaca API for order/position updates (`sync_paper_state` job)
- Worker ingests Polygon.io data on schedule (`ingest_bars` job)
- No push-based integrations or event subscriptions

## API Communication (Frontend ↔ Backend)

**Client Configuration:**
- Next.js rewrite proxy: `/backend/*` rewrites to backend API base URL
- Frontend fetch: `fetchApi()` in `src/lib/api.ts` queries `/backend/api/v1/*` endpoints
- Error handling: All HTTP errors and network failures mapped to `ApiResult<T>` (union of success/failure)
- No authentication headers (single-user, public read-only API in free tier)
- Cache policy: `cache: "no-store"` on all API calls (always fresh)

---

*Integration audit: 2026-09-23*
