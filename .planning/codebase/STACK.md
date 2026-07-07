# Technology Stack

**Analysis Date:** 2026-07-07

## Languages

**Primary:**
- Python 3.13+ - Core application language, required by `pyproject.toml` (>=3.12)

## Runtime

**Environment:**
- Python 3.13 (from `.venv/pyvenv.cfg`)
- Docker container: `python:3.13-slim` (Dockerfile)

**Package Manager:**
- pip with setuptools
- Lockfile: Not detected (uses `pyproject.toml` for versioning)

## Frameworks

**Core:**
- FastAPI 0.131+ - REST API framework for HTTP endpoints
- Uvicorn 0.34+ - ASGI web server for running the API

**Database:**
- SQLAlchemy 2.0+ - ORM for database operations
- Alembic 1.18+ - Database schema migrations
- psycopg[binary] 3.2+ - PostgreSQL driver

**Testing:**
- pytest 9.0+ - Test runner and framework

**Build/Dev:**
- setuptools 69+ - Package building and installation

## Key Dependencies

**Critical:**
- `fastapi` - REST API framework; enables all HTTP endpoints in `src/trading_platform/api/`
- `sqlalchemy` - ORM for all database models in `src/trading_platform/db/models/`
- `psycopg[binary]` - PostgreSQL connectivity required by `DatabaseSettings` in `src/trading_platform/core/settings.py`
- `httpx` - HTTP client used by both `PolygonClient` (`src/trading_platform/services/polygon.py`) and `AlpacaClient` (`src/trading_platform/services/alpaca.py`)

**Data Processing:**
- `pandas` - Data manipulation and analysis for market data processing
- `exchange-calendars` - Market calendar management for NYSE (XNYS) sessions

**Configuration:**
- `pydantic-settings` - Environment variable management with settings loading
- `PyYAML` - YAML file parsing for `app.yaml` and strategy configurations
- `pydantic` - Data validation for all settings classes in `src/trading_platform/core/settings.py`

## Configuration

**Environment:**
- Loaded from `config/app.yaml` (default location: `PROJECT_ROOT/config/app.yaml`)
- Environment overrides via `TRADING_PLATFORM_*` variables with nested delimiter `__`
- Optional `.env` file support via pydantic-settings
- Entry point: `src/trading_platform/core/settings.py::load_settings()`

**Build:**
- `pyproject.toml` - Project metadata, dependencies, setuptools configuration
- `Dockerfile` - Multi-stage containerization for production
- `docker-compose.yml` - Local development services (PostgreSQL database, API, worker)

**Configuration Files:**
- `config/app.yaml` - Application settings (database, API, broker, market data, execution)
- `config/strategies/*.yaml` - Strategy-specific configurations (loaded dynamically)
- `alembic.ini` - Database migration tool configuration

## Platform Requirements

**Development:**
- Python 3.13+ with venv support
- PostgreSQL 16+ (via docker-compose in development)
- Docker and docker-compose for local testing

**Production:**
- Docker container runtime (python:3.13-slim base image)
- PostgreSQL 16+ database (separate service)
- Environment variables for secrets injection

## Entry Points

**API Service:**
- `trading-platform-api` (setuptools script) → `src/trading_platform/api/app.py::main()`
- Runs uvicorn on configurable host/port (default: 0.0.0.0:8000)
- FastAPI app instance at `src/trading_platform/api/app.py::app`

**Worker Service:**
- `trading-platform-worker` (setuptools script) → `src/trading_platform/worker/__main__.py::main()`
- Daemon process for scheduled execution and async jobs

## Testing

**Test Runner:**
- pytest with configuration in `pyproject.toml` (testpaths: tests/)
- Run via: `make test` or `.venv/bin/pytest`
- Test files located in `tests/` directory

---

*Stack analysis: 2026-07-07*
