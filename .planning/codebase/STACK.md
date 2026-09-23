# Technology Stack

**Analysis Date:** 2026-09-23

## Languages

**Primary:**
- Python 3.12+ - Backend API, worker services, data ingestion, backtesting
- TypeScript 5 - Frontend (React components, type-safe API client)
- JavaScript - Next.js configuration, build scripts

**Secondary:**
- YAML - Configuration files (`config/app.yaml`, strategy configs)
- SQL - Database queries via SQLAlchemy ORM

## Runtime

**Environment:**
- Python 3.12 (via `uvicorn` ASGI server)
- Node.js (Next.js 16 frontend development and SSR)

**Package Manager:**
- pip - Python dependencies from `pyproject.toml`
- npm - Node.js dependencies from `console/package.json`
- Lockfile: `poetry.lock` (not present), `package-lock.json` present for Node

## Frameworks

**Core Backend:**
- FastAPI 0.131+ - REST API framework for trading platform endpoints
- Uvicorn 0.34+ - ASGI application server (HTTP runtime)
- SQLAlchemy 2.0+ - ORM for database models and queries
- Alembic 1.18+ - Schema migrations (PostgreSQL)
- Pydantic Settings 2.12+ - Typed configuration management
- Pydantic v2 - Data validation (via FastAPI dependency)

**Frontend:**
- Next.js 16.2.10 - React meta-framework, SSR, routing, API rewrites
- React 19.2.4 - UI component library
- TailwindCSS 4 - Utility-first CSS styling (with PostCSS 4)

**Testing:**
- pytest 9.0+ - Python unit/integration tests
- pytest plugins - Fixtures and assertions via pytest ecosystem
- vitest 4.1.10 - JavaScript unit tests (Vite-based, faster than Jest)
- @testing-library/react 16.3.2 - React component testing utilities
- @testing-library/dom 10.4.1 - DOM testing queries
- jsdom 29.1.1 - Browser environment simulation for tests

**Development & Quality:**
- ruff 0.8+ - Python linting and code formatting (fast Rust-based)
  - Configuration: `tool.ruff` in `pyproject.toml` (line-length 100)
  - Enforced rules: E, F, I, W (imports, formatting, style)
  - Skipped: E501 (line-too-long — pre-existing long lines in codebase)
- mypy 1.13+ - Python static type checking
  - Configuration: `tool.mypy` in `pyproject.toml`
  - Enabled on: `services/execution`, `services/reconciliation`, `services/config`
- ESLint 9 - JavaScript/TypeScript linting
  - Config: `console/eslint.config.mjs`
  - Uses: eslint-config-next for Next.js rules
- pre-commit 4.0+ - Git hooks for automated checks before commits
  - Configuration: `.pre-commit-config.yaml`

**UI & Charting:**
- recharts 3.9.2 - React charting library (analytics dashboards)

## Key Dependencies

**Backend — Critical:**
- psycopg[binary] 3.2+ - PostgreSQL async/sync client with binary extensions
- httpx 0.28+ - HTTP client for Polygon.io and Alpaca API calls (async-capable)
- FastAPI 0.131+ - Core REST API framework
- Uvicorn[standard] 0.34+ - Production-grade ASGI server with uvloop/httptools

**Backend — Infrastructure:**
- SQLAlchemy 2.0+ - ORM and schema definition
- Alembic 1.18+ - Database migrations (manages schema versioning)
- Pydantic Settings 2.12+ - Type-safe environment variable parsing
- PyYAML 6.0+ - YAML configuration file parsing (app.yaml, strategy configs)
- exchange-calendars 4.5+ - US equity market calendars (trading session dates)

**Frontend:**
- Next.js 16.2.10 - Server-side rendering, API route rewrites, static optimization
- React 19.2.4 - Component library
- recharts 3.9.2 - Charting (LP performance metrics, portfolio analytics)
- TailwindCSS 4 - Utility CSS with PostCSS integration
- @tailwindcss/postcss 4.x - PostCSS plugin for TailwindCSS

## Configuration

**Environment:**
- All settings loaded via `pydantic_settings.BaseSettings` with `TRADING_PLATFORM_` prefix
- Sources: Environment variables (override YAML defaults)
- Defaults: `config/app.yaml` (root), `config/strategies/` (per-strategy)
- Loading: `src/trading_platform/core/settings.py` — `load_settings()` function

**Key Environment Variables:**
- Database: `TRADING_PLATFORM_DATABASE__HOST`, `__PORT`, `__NAME`, `__USER`, `__PASSWORD`
- API: `TRADING_PLATFORM_API__PORT` (default 8000)
- Logging: `TRADING_PLATFORM_LOGGING__LEVEL` (default INFO, format: JSON)
- App: `TRADING_PLATFORM_APP__ENVIRONMENT` (local/test/development/staging/production)
- Polygon.io: `TRADING_PLATFORM_MARKET_DATA__POLYGON__API_KEY` (required for live data)
- Alpaca: `TRADING_PLATFORM_BROKER__ALPACA__API_KEY`, `__API_SECRET` (required for paper trading)
- Frontend: `TRADING_CONSOLE_API_BASE_URL` (default http://127.0.0.1:8000)

**Build:**
- `pyproject.toml` - Python project metadata, dependencies, tool config (ruff, mypy, pytest)
- `console/package.json` - Node dependencies and scripts
- `console/tsconfig.json` - TypeScript compiler options
- `console/next.config.ts` - Next.js configuration (API rewrites, Turbopack root)
- `.pre-commit-config.yaml` - Git hook automation (ruff, mypy, eslint)
- `Makefile` - Development targets (test, backtest, ingest, console dev)

## Platform Requirements

**Development:**
- Python 3.12+ with pip
- Node.js with npm
- PostgreSQL 16+ (via Docker or local installation)
- Git (for pre-commit hooks)
- Docker & Docker Compose (for local service orchestration)

**Production:**
- Python 3.12+ container (Render uses python:3.13-slim)
- PostgreSQL 16+ (externally hosted, e.g., Neon)
- Node.js for Next.js deployment (Vercel managed)

---

*Stack analysis: 2026-09-23*
