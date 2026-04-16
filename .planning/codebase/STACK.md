# Technology Stack

**Analysis Date:** 2026-04-16

## Languages

**Primary:**
- Python 3.12+ - Core application, API, worker, and strategy implementations
- SQL - Database schema and migrations via Alembic

**Secondary:**
- YAML - Configuration files for app settings and strategy parameters

## Runtime

**Environment:**
- Python 3.13 (per Dockerfile)
- CPython interpreter

**Package Manager:**
- pip (via setuptools)
- Lockfile: Not detected (using `pyproject.toml` only)

## Frameworks

**Core:**
- FastAPI 0.131.0+ - REST API framework with async request handling
- Uvicorn 0.34.0+ - ASGI server for hosting FastAPI application
- SQLAlchemy 2.0.0+ - ORM for database modeling and queries
- Pydantic 2.12.0+ (via pydantic-settings) - Data validation and settings management

**Testing:**
- pytest 9.0.0+ - Testing framework
- Config: `pytest.ini_options` in `pyproject.toml`

**Build/Dev:**
- Alembic 1.18.0+ - Database migration management
- setuptools 69+ - Package build system

## Key Dependencies

**Critical:**
- `exchange-calendars` 4.5+ - NYSE (XNYS) trading calendar for session dates and trading hours
- `httpx` 0.28.0+ - HTTP client for Polygon.io and Alpaca API calls with retry logic
- `psycopg[binary]` 3.2.0+ - PostgreSQL adapter for SQLAlchemy connections
- `PyYAML` 6.0.2+ - Configuration file parsing

**Infrastructure:**
- `pandas` - Data manipulation for calendar date operations (via exchange-calendars)

## Configuration

**Environment:**
- Configuration hierarchy:
  1. Built-in defaults in `src/trading_platform/core/settings.py`
  2. YAML config file at `config/app.yaml`
  3. Strategy-specific YAML files in `config/strategies/`
  4. Environment variable overrides with `TRADING_PLATFORM_` prefix

- Key environment variables:
  - `TRADING_PLATFORM_APP__ENVIRONMENT` - Environment name (local/test/development/staging/production)
  - `TRADING_PLATFORM_DATABASE__*` - Database connection settings
  - `TRADING_PLATFORM_MARKET_DATA__POLYGON__API_KEY` - Polygon.io API key (required)
  - `TRADING_PLATFORM_BROKER__ALPACA__API_KEY` - Alpaca API key (required for live trading)
  - `TRADING_PLATFORM_BROKER__ALPACA__API_SECRET` - Alpaca API secret (required for live trading)
  - `TRADING_PLATFORM_API__PORT` - API server port (default: 8000)
  - `TRADING_PLATFORM_LOGGING__LEVEL` - Log level (default: INFO)

**Build:**
- `pyproject.toml` - Package metadata, dependencies, entry points
- `Dockerfile` - Container image specification (Python 3.13-slim base)
- `docker-compose.yml` - Multi-service orchestration (database, API, worker)
- `alembic.ini` - Database migration configuration

## Platform Requirements

**Development:**
- Python 3.12+
- PostgreSQL 16+ (via docker-compose or local install)
- YAML configuration files in `config/` directory
- Polygon.io API key for market data access

**Production:**
- Docker and Docker Compose (for containerized deployment)
- PostgreSQL database (16+ recommended)
- Secure credential management for Polygon.io and Alpaca API keys
- Uvicorn-compatible ASGI hosting or Docker

## Database

**Type:**
- PostgreSQL 16 (Alpine Linux variant in docker-compose)
- Driver: `psycopg` (PostgreSQL native binary adapter)
- Connection string format: `postgresql+psycopg://{user}:{password}@{host}:{port}/{database}`

**Migrations:**
- Tool: Alembic 1.18.0+
- Location: `alembic/` directory with versioned migration scripts
- Models: SQLAlchemy ORM with declarative base in `src/trading_platform/db/base.py`
- Naming convention: Enforced via `NAMING_CONVENTION` in base module

---

*Stack analysis: 2026-04-16*
