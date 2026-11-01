# External Integrations

**Analysis Date:** 2026-11-01

## APIs & External Services

**Research market data (active product):**
- Tiingo REST API (end-of-day equity prices and ticker metadata) - the research product's bar source
  - Base URL: `https://api.tiingo.com` (`TiingoProviderSettings.base_url` in `src/trading_platform/core/settings.py`)
  - Client: `src/trading_platform/services/tiingo.py` (`httpx.Client`, retries via `max_retries`, `retry_backoff_factor`)
  - Endpoints used: `GET /tiingo/daily/<ticker>` (metadata) and `GET /tiingo/daily/<ticker>/prices` (EOD rows), both in `services/tiingo.py`
  - Auth: `Authorization: Token <key>` header. The key is never placed in a URL; the client rejects any `token` query parameter (`FORBIDDEN_QUERY_PARAMS = frozenset({"token"})` in `services/tiingo.py`).
  - Key env: `TRADING_PLATFORM_RESEARCH__TIINGO__API_KEY`, or the alias `TIINGO_API_KEY` (resolved in `core/settings.py`, `_apply_tiingo_key_alias`)
  - Quota: an in-process request budget plus a shared database ledger (see Data Storage). Configured defaults: 50 requests/hour, 1,000 requests/day, 500 unique symbols/month (`TRADING_PLATFORM_RESEARCH__TIINGO__REQUESTS_PER_HOUR`, `..._REQUESTS_PER_DAY`, `..._UNIQUE_SYMBOLS_PER_MONTH`).
  - Ingestion: job type `ingest-tiingo-bars` (`src/trading_platform/jobs/handlers/ingest_tiingo_bars.py`, submission in `ingest_tiingo_bars_submission.py`), service `src/trading_platform/services/research/tiingo_ingestion.py`
  - Supported-ticker list: `https://apimedia.tiingo.com/docs/tiingo/daily/supported_tickers.zip`, downloaded without an API key by `src/trading_platform/services/research/catalog.py` (sent with a `User-Agent` header) and run by job type `catalog-sync` (`src/trading_platform/jobs/handlers/catalog_sync.py`)

**Anthropic (research assistant, LLM):**
- Anthropic Messages API via the official `anthropic` Python SDK (`anthropic>=1.12.0,<2.0.0`)
  - Client wrapper: `AnthropicProvider` in `src/trading_platform/services/research/assistant.py` (class at ~line 245). The study assistant (`src/trading_platform/services/research/study_assistant.py`) reuses the same provider and settings.
  - Model: default `claude-haiku-5-5` (`ResearchAiSettings.model` in `core/settings.py`; override `TRADING_PLATFORM_RESEARCH__AI__MODEL`)
  - Calls: one bounded, synchronous `messages.create` per draft or revision request, through `messages.with_raw_response.create`
  - Structured output: `output_config={"format": {"type": "json_schema", "schema": ...}}`. The schema must stay union-free; the schema comment in `assistant.py` (~line 390) records the provider's limit.
  - Retries: SDK retries disabled (`max_retries=0`); every attempt is counted in the shared ledger. Per-request deadline: `TRADING_PLATFORM_RESEARCH__AI__TIMEOUT_SECONDS` (default 60s).
  - Prompt caching: the system prompt is sent with `cache_control: {"type": "ephemeral"}` (`assistant.py`).
  - Errors are mapped to typed codes: timeout, auth failure, rate limit, provider status error, and provider unreachable (`assistant.py`, `ProviderFailure`).
  - Enablement: the assistant is usable only when `TRADING_PLATFORM_RESEARCH__AI__ENABLED` is true, `ANTHROPIC_API_KEY` (or `TRADING_PLATFORM_RESEARCH__AI__API_KEY`) is set, and both `TRADING_PLATFORM_RESEARCH__AI__MAX_REQUESTS_PER_DAY` and `..._MAX_OUTPUT_TOKENS` are positive (`ResearchAiSettings.usable`). The defaults for both limits are 0, so the assistant stays off until they are set.
  - Concurrency: `TRADING_PLATFORM_RESEARCH__AI__MAX_CONCURRENT_REQUESTS` (default 1), enforced in the shared ledger across processes.
  - Optional endpoint override: `TRADING_PLATFORM_RESEARCH__AI__BASE_URL` (empty means the SDK default; set only for a local mock in validation runs)
  - Routes: `src/trading_platform/api/research/assistant.py`, prefix `/api/v1/research/assistant`. Its POST routes (`/proposals`, `/study-proposals`, `/proposals/{ai_draft_id}/apply`) are guarded by `require_mutations_enabled`.

**Public reference downloads (no credentials):**
- NASDAQ Trader symbol directory: `https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqtraded.txt`
- SEC company tickers: `https://www.sec.gov/files/company_tickers.json`
  - Both are fetched by `src/trading_platform/services/research/catalog.py` and by `public_catalog_sources` in `src/trading_platform/services/research/bootstrap.py`, with `User-Agent` headers. The research bootstrap (`make research-bootstrap`, `scripts/bootstrap_research.py`) uses them.

**Trading stack (frozen; not used by research-mode processes):**
- Polygon.io (daily aggregates and ticker reference data)
  - Base URL: `https://api.polygon.io` (`PolygonProviderSettings` in `core/settings.py`)
  - Client: `src/trading_platform/services/polygon.py` (`httpx.Client`, `Authorization: Bearer <key>` header)
  - Endpoints: `/v2/aggs/ticker/{symbol}/range/...` for bars (`polygon.py` ~line 146); `/v3/reference/tickers/{ticker}` for symbol metadata (`src/trading_platform/services/symbol_metadata_sync.py` ~line 78)
  - Key env: `TRADING_PLATFORM_MARKET_DATA__POLYGON__API_KEY`
  - Consumers: job types `ingest-bars` (`src/trading_platform/jobs/handlers/ingest_bars.py`) and `sync-symbol-metadata`. Neither is registered in the research registry (`src/trading_platform/jobs/registry.py`, `RESEARCH_JOB_TYPES`).
- Alpaca (paper trading broker and market data)
  - Trading base URL: `https://paper-api.alpaca.markets` (`AlpacaBrokerSettings.base_url`, default in `core/settings.py`)
  - Data base URL: `https://data.alpaca.markets` (`alpaca_data_base_url`; `price_feed: iex` in `config/app.yaml`)
  - Client: `src/trading_platform/services/alpaca.py` (`httpx.Client`; headers `APCA-API-KEY-ID` and `APCA-API-SECRET-KEY`; `urllib.parse.quote` for path segments)
  - Keys: `TRADING_PLATFORM_BROKER__ALPACA__API_KEY`, `TRADING_PLATFORM_BROKER__ALPACA__API_SECRET`. The secret is required only in trading modes; `src/trading_platform/services/config/secrets.py` checks the required secrets and the endpoint mode per mode.
  - Scope: order submission, order sync, account reconciliation, and external activity (`services/execution/`, `services/reconciliation/`, `services/external_activity.py`). The `paper-session`, `broker-order-sync`, `reconciliation`, and `risk-evaluation` job types all sit in the trading registry only.
  - The `.env` file also contains a name `TRADING_PLATFORM_BROKER__ALPACA__ENDPOINT`. No settings field reads it, so it has no effect.

**Other external libraries with network behavior:**
- None beyond the above. No webhooks, messaging (email, SMS, chat), payments, or analytics SDKs were found.

## Data Storage

**Databases (PostgreSQL, two logical databases on one server model):**

- Driver: psycopg 3 through SQLAlchemy `postgresql+psycopg://` URLs (`DatabaseSettings.url` in `src/trading_platform/core/settings.py`)
- Engine factory: `src/trading_platform/db/session.py` (`create_engine`); declarative base: `src/trading_platform/db/base.py`; model modules: `src/trading_platform/db/models/` (one module per table group, including `research.py`)

1. **`trading_platform`** - the frozen trading database
   - Default name in `DatabaseSettings`, `config/app.yaml`, and the compose `POSTGRES_DB` default
   - Used by the Compose stack and `make dev-trading`
2. **`trading_research`** - the active research database
   - Forced by `RESEARCH_DB` / `RESEARCH_ENV` in the `Makefile`, which sets `TRADING_PLATFORM_DATABASE__NAME=trading_research` for every research process (API, worker, bootstrap). A `.env` edit cannot redirect a research process to `trading_platform`.
   - Created and migrated by `scripts/create_research_db.py`, which refuses the protected name `trading_platform` (`PROTECTED_DATABASE_NAMES`). It connects as an admin to the database named by `TRADING_PLATFORM_ADMIN_DB` (default `postgres`) to create the target.
   - Seeded and synced by `scripts/bootstrap_research.py` (`make research-bootstrap`): four example strategy versions, the public asset catalog, and exchange sessions from `TRADING_PLATFORM_RESEARCH__CALENDAR_START` (`2014-01-02` in the Makefile)

**Local server:**
- `docker-compose.yml` service `db`: `postgres:16-alpine`, port `${POSTGRES_HOST_PORT:-5432}`, volume `postgres_data`, healthcheck with `pg_isready`. Its credentials come from `POSTGRES_*` variables with local defaults.

**Hosted:**
- Neon Postgres (external, direct host, SSL required) per `render.yaml` and `.env.production.example`. The hosted service targets the trading schema; Render does not provision a database.

**Migrations (Alembic):**
- Config: `alembic.ini` (`script_location = alembic`, `prepend_sys_path = src`). The `sqlalchemy.url` in `alembic.ini` is a local default. At runtime `alembic/env.py` (line 20) overrides it with `settings.database.url`.
- Versions directory: `alembic/versions/`, revisions `0001_*` through `0033_research_assistant`
- **Single head: `0033_research_assistant`** (verified with `alembic heads`)
- Recent research revisions: `0031_research_platform`, `0032_research_studies`, `0033_research_assistant`
- Both databases use this one chain. The live revision of each database is not recorded in the repository and was not queried.
- Apply: `make migrate` (`scripts/migrate.py upgrade head`), the Dockerfile `CMD` (`alembic upgrade head` before uvicorn starts), or the compose `migrate` service (profile `stack`)

**Key tables by area:**
- Research (`src/trading_platform/db/models/research.py`): `asset_catalog`, `asset_lists`, `asset_list_items`, `strategy_drafts`, `strategy_versions`, `research_studies`, `study_revisions`, `data_freezes`, `research_freezes`, `research_run_links`, `study_evaluations`, `study_revision_jobs`, `test_window_exposures`, `ai_drafts`, `provider_request_ledger`
- Shared provider budget: `provider_request_ledger` (one row per provider attempt; Tiingo and Anthropic both count here). The admission check in `src/trading_platform/services/research/budget.py` (`DatabaseRequestBudget`) runs inside `pg_advisory_xact_lock(provider)`.
- Market data: `daily_bars`, `symbols`, `market_sessions`, `market_data_ingestion_runs`
- Jobs: `jobs`, `job_events`, `job_logs`, `job_dependencies`, `job_mutations`
- Trading (frozen): `strategies`, `strategy_runs`, `paper_orders`, `paper_fills`, `order_events`, `order_submission_attempts`, `positions`, `account_snapshots`, `account_reconciliation_runs`, `execution_operations`, `recovery_records`, `system_controls`

**File storage:**
- Local filesystem only: `.data/` (gitignored; default `paths.data_dir` in `config/app.yaml`). Subdirectories present locally: `.data/research/` (`studies`, `freezes`, `logs`, and a validation directory), and `.data/backtest-reports/`.
- `scripts/create_research_db.py --restore-inputs` restores preserved `inputs/` exports from `.data/research/freezes/<id>/inputs` and verifies a manifest digest.
- No cloud object storage (S3, GCS, Azure Blob) is used.

**Caching:**
- None. No Redis, Memcached, or in-process cache service. The Anthropic prompt cache is provider-side (see above).

## Job Queue & Workers

**Queue:**
- PostgreSQL-backed, restart-safe claim/lease queue in `src/trading_platform/jobs/queue.py`
- Claims use `SELECT ... FOR UPDATE SKIP LOCKED` on the oldest ready Job. Each claim sets `lease_owner` and `lease_expires_at`; the worker renews the lease on a cadence (`renew_lease`). A crashed worker's lease lapses, and a lost-lease sweep reclaims the Job.
- Job lifecycle, submission, and cancellation: `src/trading_platform/jobs/` (`runner.py`, `lifecycle.py`, `cancellation.py`, `dependencies.py`, `registry.py`, `contracts.py`)

**Worker process:**
- Entry point: `trading-platform-worker` (`pyproject.toml` `[project.scripts]`) -> `trading_platform.worker.__main__:main`
- Command: `run-jobs` (`src/trading_platform/worker/commands/run_jobs.py`, parser in `src/trading_platform/worker/parser.py`)
- Local run: `make worker` wraps the worker in `watchfiles`, sends SIGINT with a 30-second grace period so an in-flight Job can finish, and then kills the process. After that the lost-lease sweep reclaims the Job.
- Hosted: not deployed (`render.yaml` deploys the API only).
- The job-type registry is chosen per process: `build_research_registry` when `research.mode` is on, otherwise the trading registry (`src/trading_platform/jobs/registry.py`).

**Research job types (`RESEARCH_JOB_TYPES` in `src/trading_platform/jobs/registry.py`; constants in `src/trading_platform/jobs/handlers/research_submission.py`; handlers in `research_jobs.py`):**
- `catalog-sync` - public catalog refresh (`jobs/handlers/catalog_sync.py`)
- `ingest-tiingo-bars` - Tiingo EOD ingest (`jobs/handlers/ingest_tiingo_bars.py`)
- `sync-market-sessions` - exchange session sync (`jobs/handlers/sync_market_sessions.py`)
- `research-freeze` - freezes a study's inputs (`jobs/handlers/research_jobs.py`)
- `research-backtest` - research backtest run (`jobs/handlers/research_jobs.py`)
- `research-evaluate` - evaluation run (`jobs/handlers/research_jobs.py`)

**Trading job types (registry, not reachable in research mode):**
- `ingest-bars`, `sync-symbol-metadata`, `risk-evaluation`, `paper-session`, `broker-order-sync`, `reconciliation`, `backtest`, `record-external-activity` (constant in `src/trading_platform/services/broker_jobs.py`)

**Job API:**
- `/api/v1/jobs` (list, detail, submit, cancel, retry) and `/api/v1/job-types` (`src/trading_platform/api/routes/jobs.py`, `job_types.py`). Mutating routes are gated by `require_mutations_enabled` (`src/trading_platform/api/dependencies.py`, line 58), which checks `TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED` (default `false`).

## Authentication & Identity

**API auth:**
- None. FastAPI has no authentication middleware, no API-key header check, and no CORS middleware in `src/trading_platform/api/app.py`. The API is single-user and unauthenticated, with `operator_mode: single_user` in `config/app.yaml`.
- Every write route is gated by `require_mutations_enabled` (`src/trading_platform/api/dependencies.py`, line 58), which returns HTTP 403 with `{"code": "mutations_disabled"}` when `TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED` is not true. This covers the research POST/PUT/DELETE routes in `api/research/strategies.py`, `studies.py`, `catalog.py`, and `assistant.py`, and the Job routes in `api/routes/jobs.py`, `controls.py`, `recovery.py`, and `execution_operations.py`.
- Two POST routes are deliberately ungated because they compute without writing: `POST /api/v1/research/strategies/validate` (`api/research/strategies.py` ~line 147; pure YAML validation through `validate_yaml_text`) and `POST /api/v1/research/studies/preflight` (`api/research/studies.py` ~line 93; read-only coverage check whose docstring states nothing is written). All GET routes are open reads.
- Hosted exposure: the Render service is public and unauthenticated. Its gated write routes are disabled by `TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED=false` in `render.yaml`, so the residual exposure is data disclosure, not control. The two ungated POST routes compute without writing and add no control surface. This is stated in the `render.yaml` header comment, which predates the research routes.

**Console auth:**
- None. The console (`console/`) has no login, session, or identity layer. It reaches the API through the server-side rewrite `/backend/:path*` -> `${TRADING_CONSOLE_API_BASE_URL}/:path*` (`console/next.config.ts`). The default base URL is `http://127.0.0.1:8000`.
- The console does not call Tiingo, Anthropic, Polygon, or Alpaca directly. Its only outbound network dependency is the API rewrite. No `NEXT_PUBLIC_*` variables exist.

**Provider auth:**
- Tiingo: `Authorization: Token` header
- Anthropic: SDK `api_key` argument (sent as the provider's auth header by the SDK)
- Polygon: `Authorization: Bearer` header
- Alpaca: `APCA-API-KEY-ID` and `APCA-API-SECRET-KEY` headers
- NASDAQ Trader and SEC downloads: no credentials

## Monitoring & Observability

**Error tracking:**
- None detected. No Sentry, Rollbar, Bugsnag, or similar SDK.

**Logs:**
- Structured settings in `config/app.yaml` under `logging:` (`level: INFO`, `format: json`, `service: trading-platform-api`); the `LoggingSettings` model is in `src/trading_platform/core/settings.py`
- Job logs and events persisted to the `job_logs` and `job_events` tables
- Provider error text is truncated before storage (`assistant.py`, `_provider_error_text`, 1000 characters)

**Health:**
- `GET /health` (liveness) and `GET /ready` in `src/trading_platform/api/routes/health.py`. `/ready` checks the database only when `readiness.dependency_checks_enabled` is true; `config/app.yaml` sets it to `true` with `require_database: true`. The `render.yaml` health check uses `/health`.

**Metrics / tracing:**
- None detected.

## CI/CD & Deployment

**Hosting:**
- API (hosted): Render, free-tier Docker web service (`render.yaml`, service `trading-platform-api`, region `oregon`). Read-only API scope, mutations disabled, no worker.
- Console: a Vercel link exists locally (`.vercel/repo.json`, project name `trading-platform-console`, directory `.`). The repository does not record a live deployment.
- Local: `make dev` runs the API, worker, and console as three processes; `make up` runs the Compose stack behind the `stack` profile.

**CI Pipeline:**
- None in the repository. No `.github/` directory and no other CI configuration file was found.
- The only automated gate is the local pre-commit configuration (`.pre-commit-config.yaml`): ruff lint, ruff format scoped to `services/(config|execution|reconciliation)/` and `worker/`, and a local mypy hook for the same three service packages.

**Container image:**
- `Dockerfile`: `python:3.13-slim`; installs the package with `pip install .`; `CMD` runs `alembic upgrade head` and then `exec uvicorn trading_platform.api.app:app --host 0.0.0.0 --port ${PORT:-8000}`.

## Environment Configuration

**Required for the research product (local `make dev`):**
- Writes are off by default. `TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED` is set to `true` only for the API process under `make dev` and `make dev-trading` (`Makefile`), and by the compose `api` service (`docker-compose.yml`). `make api` leaves it unset, so research writes under `make api` return 403. Setting it in `.env` also enables writes.
- `TRADING_PLATFORM_DATABASE__*` (local Postgres) - `TRADING_PLATFORM_DATABASE__NAME` is forced to `trading_research` by the Makefile
- `TRADING_PLATFORM_RESEARCH__TIINGO__API_KEY` or `TIINGO_API_KEY` - for Tiingo ingest
- `ANTHROPIC_API_KEY` or `TRADING_PLATFORM_RESEARCH__AI__API_KEY`, plus `TRADING_PLATFORM_RESEARCH__AI__ENABLED`, `..._MAX_REQUESTS_PER_DAY`, `..._MAX_OUTPUT_TOKENS` - all four required before the assistant runs

**Required for the frozen trading stack (not needed for research):**
- `TRADING_PLATFORM_MARKET_DATA__POLYGON__API_KEY`
- `TRADING_PLATFORM_BROKER__ALPACA__API_KEY`, `TRADING_PLATFORM_BROKER__ALPACA__API_SECRET`

**Required for the hosted API (`render.yaml`, entered in the dashboard):**
- `TRADING_PLATFORM_DATABASE__HOST`, `..._NAME`, `..._USER`, `..._PASSWORD` (Neon); `TRADING_PLATFORM_DATABASE__PORT` is set to `5432` in the blueprint

**Secrets location:**
- Local: repository-root `.env` (gitignored; `.env.example` lists names with placeholder values) and `console/.env.local`
- Hosted: Render dashboard (`sync: false` variables)
- No vault or secrets manager is integrated.

**Names present in local `.env` with no reader in code:** `DATABASE_URL` (no reference in `src/`, `scripts/`, `alembic/`, or `tests/`).

## Webhooks & Callbacks

**Incoming:**
- None. No webhook endpoint or signature verification exists in `src/trading_platform/api/`.

**Outgoing:**
- None. No code posts to external callback URLs. Outbound traffic consists only of the provider API calls and public catalog downloads listed above.

---

*Integration audit: 2026-11-01*
