# Technology Stack

**Analysis Date:** 2026-11-01

## Languages

**Primary:**
- Python 3.13 (runtime), declared `>=3.12` - all backend code under `src/trading_platform/`, scripts under `scripts/`, migrations under `alembic/versions/`, tests under `tests/`
- TypeScript `^5` - operator console under `console/src/` (Next.js App Router, TSX components, `*.test.ts(x)` suites)

**Secondary:**
- SQL (PostgreSQL dialect) - emitted by Alembic revisions in `alembic/versions/` (33 revisions, `0001_*` through `0033_research_assistant`); no hand-written SQL migration files
- YAML - runtime configuration in `config/app.yaml`, strategy specs in `config/strategies/*.yaml`, research examples in `config/research/examples/*.yaml`, Render blueprint in `render.yaml`, compose file `docker-compose.yml`, pre-commit hooks in `.pre-commit-config.yaml`
- CSS - Tailwind v4 via `console/src/app/globals.css` and `console/postcss.config.mjs`
- Shell/Make - `Makefile` (118 lines) is the operator entry point for every local workflow

## Runtime

**Backend Environment:**
- Python 3.13.15 observed in the project virtualenv `.venv/` (`.venv/bin/python`)
- Container image base: `python:3.13-slim` (`Dockerfile` line 1)
- Declared minimum: `requires-python = ">=3.12"` in `pyproject.toml`; ruff and mypy target `py312`. The 3.13 runtime is what is actually exercised.

**Console Environment:**
- Node.js v22.22.0 and npm 10.9.4 observed locally; not pinned. No `.nvmrc`, `.node-version`, or `engines` field exists in `console/package.json`.
- Next.js 16.2.10 requires a modern Node runtime; the version used locally is not recorded in the repo.

**Package Managers:**
- Python: `pip` 26.2 observed in `.venv/`, with a `setuptools>=69` build backend (`pyproject.toml` `[build-system]`). Installed via `pip install .` in the Docker image and into `.venv/` for local work.
  - Lockfile: **missing**. No `uv.lock`, `poetry.lock`, `Pipfile`, or `requirements*.txt` exists at the repository root. Dependency versions are constraint ranges only.
- Node: `npm` (the Makefile runs `cd console && npm install` through the `console-install` target).
  - Lockfile: **present** - `console/package-lock.json`

## Frameworks

**Core (backend):**
- FastAPI `>=0.131.0,<1.0.0` (installed 0.141.1) - HTTP API; app factory and routers in `src/trading_platform/api/app.py`, routers in `src/trading_platform/api/routes/` and `src/trading_platform/api/research/`
- Starlette 1.7.0 (transitive, via FastAPI) - ASGI layer
- Uvicorn `>=0.34.0,<1.0.0` with `[standard]` extras (installed 0.54.0) - ASGI server; `watchfiles` (used by `make worker` and `make dev`) is pulled in by the `[standard]` extras
- SQLAlchemy `>=2.0.0,<3.0.0` (installed 2.1.1) - ORM and engine; declarative base in `src/trading_platform/db/base.py`, engine factory in `src/trading_platform/db/session.py`
- Alembic `>=1.18.0,<2.0.0` (installed 1.20.0) - schema migrations; config `alembic.ini`, environment `alembic/env.py`
- Pydantic `2.13.5` (transitive) and pydantic-settings `>=2.12.0,<3.0.0` (installed 2.15.0) - typed settings in `src/trading_platform/core/settings.py`

**Core (console):**
- Next.js 16.2.10 - operator console; config `console/next.config.ts`
- React 19.2.4 and React DOM 19.2.4 - UI runtime
- Recharts 3.9.2 - charts (`console/src/components/research/CurveChart.tsx`, `console/src/components/runs/detail/EquityCurveChart.tsx`)

**Testing:**
- pytest `>=9.0.0,<10.0.0` (installed 9.1.1) - backend; config is `[tool.pytest.ini_options]` in `pyproject.toml` (`testpaths = ["tests"]`). The suite holds 158 `test_*.py` files under `tests/`.
- Vitest `^4.1.10` - console unit tests; config `console/vitest.config.ts` (environment `node`, includes `src/**/*.test.{ts,tsx}`, alias `@` to `console/src`)
- jsdom `^29.1.1` - DOM environment, opted into per test file with the header comment `// @vitest-environment jsdom` (for example `console/src/components/AppShell.test.tsx`, `console/src/components/research/research.test.tsx`); the Vitest default in `console/vitest.config.ts` is `node`
- `@testing-library/react ^16.3.2` and `@testing-library/dom ^10.4.1` - React component testing

**Build/Dev:**
- ruff `>=0.8,<1.0` (installed 0.16.8) - lint (`E`, `F`, `I`, `W`; `E501` ignored) and format; config `[tool.ruff]` in `pyproject.toml`
- ruff pre-commit hook pinned to `v0.15.21` in `.pre-commit-config.yaml` - note this differs from the installed 0.16.8 in `.venv/`
- mypy `>=1.13,<2.0` (installed 1.20.2) - scoped to `src/trading_platform/services/execution`, `services/reconciliation`, `services/config` (see `[tool.mypy]` in `pyproject.toml`); run through a local pre-commit hook using `.venv/bin/mypy`
- pre-commit `>=4.0,<5.0` - hook runner (`.pre-commit-config.yaml`)
- ESLint `^9` with `eslint-config-next 16.2.10` - console linting; config `console/eslint.config.mjs`
- TypeScript `^5` - console type checking; config `console/tsconfig.json` (`strict: true`, path alias `@/*` to `./src/*`)
- Tailwind CSS `^4` with `@tailwindcss/postcss ^4` - console styling; config `console/postcss.config.mjs`
- Docker / Docker Compose - local Postgres and the optional full container stack; `Dockerfile`, `docker-compose.yml`

## Key Dependencies

**Critical (backend, declared in `pyproject.toml` `[project] dependencies`):**
- `anthropic` `>=1.12.0,<2.0.0` (installed 1.12.1) - official Anthropic SDK; used only by the research assistant in `src/trading_platform/services/research/assistant.py` and `study_assistant.py`. Implicit SDK retries are disabled (`max_retries=0`).
- `httpx` `>=0.28.0,<1.0.0` (installed 0.28.1) - HTTP client for every external data provider and for public catalog downloads (`src/trading_platform/services/tiingo.py`, `polygon.py`, `alpaca.py`, `research/catalog.py`, `symbol_metadata_sync.py`)
- `psycopg[binary]` `>=3.2.0,<4.0.0` (installed 3.3.6) - PostgreSQL driver, used through SQLAlchemy's `postgresql+psycopg://` URL scheme; also used directly by `scripts/create_research_db.py`
- `exchange-calendars` `>=4.5,<5.0` (installed 4.13.2) - exchange session calendar (default `XNYS`); configured in `src/trading_platform/services/calendar.py` and the `CalendarSettings` model
- `PyYAML` `>=6.0.2,<7.0.0` (installed 6.0.3) - loads `config/app.yaml` and strategy YAML in `src/trading_platform/core/settings.py`
- `sqlalchemy` and `alembic` - see Frameworks above

**Critical (console, declared in `console/package.json`):**
- `next` 16.2.10 - the console framework; server-side rewrite `/backend/:path*` proxies to the FastAPI API (`console/next.config.ts`)
- `react` / `react-dom` 19.2.4
- `recharts` 3.9.2

**Infrastructure:**
- `uvicorn[standard]` - runtime server and file-watch reload for `make dev` / `make api`
- `pydantic-settings` - environment and `.env` loading (`EnvironmentOverrides` in `src/trading_platform/core/settings.py`)
- Python standard library `hashlib` (`src/trading_platform/services/research/budget.py`) and `urllib` - used directly (`urllib.parse.quote` in `src/trading_platform/services/alpaca.py`)

**Not used (verified by grep across `src/`, `scripts/`):**
- No Redis, Celery, RQ, Kafka, RabbitMQ, or other external message broker. The job queue is PostgreSQL-backed (see INTEGRATIONS.md).
- No cloud SDKs (`boto3`, `google-cloud-*`, Azure SDK).
- No error-tracking or APM SDK (Sentry, OpenTelemetry, Prometheus client).
- No ORM beyond SQLAlchemy; no pandas or numpy dependency is declared in `pyproject.toml`.

## Configuration

**Environment loading:**
- Backend settings are one typed tree (`Settings` in `src/trading_platform/core/settings.py`) with `pydantic-settings`.
- Environment variables use the prefix `TRADING_PLATFORM_` and the nested delimiter `__` (for example `TRADING_PLATFORM_DATABASE__HOST`, `TRADING_PLATFORM_RESEARCH__AI__ENABLED`). Source: `EnvironmentOverrides.model_config` at `src/trading_platform/core/settings.py` (`env_prefix="TRADING_PLATFORM_"`, `env_nested_delimiter="__"`, `env_file=".env"`).
- The repository-root `.env` is read by settings (gitignored). The root template is `.env.example`; the hosted template is `.env.production.example`.
- Two non-prefixed aliases are resolved from the process environment and from `.env` via `dotenv_values`: `TIINGO_API_KEY` (`src/trading_platform/core/settings.py` ~line 596) and `ANTHROPIC_API_KEY` (~line 632).
- Precedence, lowest to highest (`load_settings` in `src/trading_platform/core/settings.py`): code defaults, then `config/app.yaml`, then the strategy YAML bundle, then `TRADING_PLATFORM_*` environment overrides (including `.env`). The `TIINGO_API_KEY` and `ANTHROPIC_API_KEY` aliases are applied after the merge.

**Configuration files:**
- `config/app.yaml` - base application config (API port, database defaults, logging, market-data universe, broker and execution defaults). `TRADING_PLATFORM_CONFIG_FILE` overrides its location.
- `config/strategies/*.yaml` - four strategy specs (`trend_following_daily`, `rsi_mean_reversion_daily`, `donchian_breakout_daily`, `time_series_momentum_daily`); `TRADING_PLATFORM_STRATEGY_CONFIG_DIR` overrides the directory.
- `config/research/examples/*.yaml` - research strategy examples used by `scripts/bootstrap_research.py` and `services/research/examples.py`.
- Known dead config: `config/app.yaml` places `calendar:` and `metadata:` blocks under `backtest:`. `BacktestSettings` (`src/trading_platform/core/settings.py`) defines no such fields, so those blocks are not mapped. The effective calendar (`XNYS`, 60 sessions) and metadata provider (`polygon`) come from `MarketDataSettings` code defaults. The values happen to match.

**Environment variable names** (values are never recorded in this repo's docs):

Database (both product modes):
- `TRADING_PLATFORM_DATABASE__HOST`, `TRADING_PLATFORM_DATABASE__PORT`, `TRADING_PLATFORM_DATABASE__NAME`, `TRADING_PLATFORM_DATABASE__USER`, `TRADING_PLATFORM_DATABASE__PASSWORD`
- `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST_PORT` - consumed by `docker-compose.yml` for the `db` container and the Compose-internal DB name/user
- `TRADING_PLATFORM_ADMIN_DB` - admin database for `scripts/create_research_db.py` (default `postgres`)

Application and API:
- `TRADING_PLATFORM_APP__ENVIRONMENT`, `TRADING_PLATFORM_API__PORT`, `TRADING_PLATFORM_LOGGING__LEVEL`
- `TRADING_PLATFORM_CONFIG_FILE`, `TRADING_PLATFORM_STRATEGY_CONFIG_DIR`
- `TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED` - gates mutating Job routes (default `false`)
- `PORT` - honored by the Docker `CMD` (`Dockerfile`), for Render

Research mode (set by the Makefile for every research process via `RESEARCH_ENV`):
- `TRADING_PLATFORM_RESEARCH__MODE`, `TRADING_PLATFORM_RESEARCH__CALENDAR_START`
- `TRADING_PLATFORM_RESEARCH__MAX_ASSETS_PER_STUDY`, `TRADING_PLATFORM_RESEARCH__QUANTITY_POLICY_DEFAULT`

Tiingo (research market data):
- `TRADING_PLATFORM_RESEARCH__TIINGO__API_KEY` or the alias `TIINGO_API_KEY`
- `TRADING_PLATFORM_RESEARCH__TIINGO__REQUESTS_PER_HOUR`, `..._REQUESTS_PER_DAY`, `..._UNIQUE_SYMBOLS_PER_MONTH`

Anthropic research assistant:
- `TRADING_PLATFORM_RESEARCH__AI__ENABLED`, `ANTHROPIC_API_KEY` (or `TRADING_PLATFORM_RESEARCH__AI__API_KEY`)
- `TRADING_PLATFORM_RESEARCH__AI__MAX_REQUESTS_PER_DAY`, `..._MAX_OUTPUT_TOKENS`
- Optional: `TRADING_PLATFORM_RESEARCH__AI__MODEL` (default `claude-haiku-5-5`), `..._TIMEOUT_SECONDS`, `..._MAX_INPUT_CHARACTERS`, `..._MAX_REVISIONS_PER_DRAFT`, `..._MAX_CONCURRENT_REQUESTS`, `..._BASE_URL`

Trading stack (frozen; not read by research-mode processes):
- `TRADING_PLATFORM_MARKET_DATA__POLYGON__API_KEY`
- `TRADING_PLATFORM_BROKER__ALPACA__API_KEY`, `TRADING_PLATFORM_BROKER__ALPACA__API_SECRET`

Console:
- `TRADING_CONSOLE_API_BASE_URL` (in `console/.env.local` / `console/.env.example`); default `http://127.0.0.1:8000` in `console/next.config.ts`

Names present in the local `.env` but not read by any code path: `DATABASE_URL` (no reader found in `src/`, `scripts/`, `alembic/`, or `tests/`) and `TRADING_PLATFORM_BROKER__ALPACA__ENDPOINT` (not a field of `AlpacaBrokerSettings`, so `extra="ignore"` drops it).

**Secrets location:**
- Local development: repository-root `.env` (gitignored) and `console/.env.local`
- Hosted (Render): dashboard-entered values for `sync: false` variables in `render.yaml`
- No secrets manager or vault integration is present.

**Build and tooling config:**
- `pyproject.toml` - package metadata, dependencies, ruff, mypy, pytest
- `alembic.ini` - Alembic script location and a local default `sqlalchemy.url`; runtime overrides it from `settings.database.url` in `alembic/env.py`
- `Dockerfile`, `docker-compose.yml`, `render.yaml`
- `.pre-commit-config.yaml`
- `console/package.json`, `console/package-lock.json`, `console/tsconfig.json`, `console/next.config.ts`, `console/vitest.config.ts`, `console/eslint.config.mjs`, `console/postcss.config.mjs`
- `Makefile` - the operator entry point: `make dev` (research product), `make dev-trading` (frozen trading surface), `make research-bootstrap`, `make migrate`, `make test` (runs a subset of `tests/`), `make up`/`down` (Compose stack)

## Platform Requirements

**Development:**
- Python 3.13 with a virtualenv at `.venv/` (the Makefile defaults `PYTHON=.venv/bin/python`, `UVICORN=.venv/bin/uvicorn`, `PYTEST=.venv/bin/pytest`)
- Node.js with npm for `console/`
- PostgreSQL 16 - supplied by `docker-compose.yml` (`postgres:16-alpine`, host port `POSTGRES_HOST_PORT`, default `5432`) or a local instance
- `lsof` on the host - required by the `make dev` port check
- Docker Desktop (optional) - for `make up` (stack profile) and the container build

**Production / hosted:**
- Docker image built from `Dockerfile` (`python:3.13-slim`); migrations run at container start (`alembic upgrade head` in the Dockerfile `CMD`)
- Render free-tier web service, Docker runtime, region `oregon`, health check `/health` (`render.yaml`)
- External PostgreSQL: Neon (direct host, SSL) per `render.yaml` and `.env.production.example`
- Scope of the hosted deploy: the read-only trading API, with `TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED=false`. Research mode is not set there, so the hosted service does not run the research product. The worker is not deployed.
- Console hosting: a Vercel link exists in `.vercel/repo.json` (project name `trading-platform-console`, directory `.`). Whether a deployment is live is not established by the repository.

---

*Stack analysis: 2026-11-01*
