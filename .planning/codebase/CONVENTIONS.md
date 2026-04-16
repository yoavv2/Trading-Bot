# Coding Conventions

**Analysis Date:** 2026-04-16

## Naming Patterns

**Files:**
- Module files use `snake_case`: `calendar.py`, `market_data_access.py`, `paper_execution.py`
- ORM model files are singular: `daily_bar.py`, `strategy_run.py`, `market_session.py`
- Test files use `test_*.py` pattern: `test_backtest_runner.py`, `test_strategy_registry.py`

**Functions:**
- Functions use `snake_case`: `build_settings_payload()`, `load_settings()`, `upsert_market_sessions()`
- Private/internal functions prefixed with underscore: `_load_yaml_file()`, `_deep_merge()`, `_admin_connection_settings()`
- Factory/builder functions use `build_*` or `create_*` prefix: `build_default_registry()`, `create_app()`, `build_log_context()`

**Variables:**
- Use `snake_case` for all variables and parameters: `admin_params`, `run_id`, `strategy_id`, `bar_map`
- Constants use `UPPER_SNAKE_CASE`: `_DEFAULT_EXCHANGE = "XNYS"`, `DEFAULT_APP_CONFIG_FILE`, `PROJECT_ROOT`
- Abbreviations are lowercase in multi-word identifiers: `as_of` (not `asOf`), `db_session`, `run_id`

**Types:**
- Type names use `PascalCase`: `StrategyMetadata`, `BaseStrategy`, `StrategyBootstrapResult`
- Enum members use `UPPER_SNAKE_CASE` for values: `DRY_BOOTSTRAP = "dry_bootstrap"`, `SUCCEEDED = "succeeded"`
- Dataclasses and frozen dataclasses use `PascalCase`: `StrategyMetadata`, `StrategyBootstrapResult`

## Code Style

**Formatting:**
- Language: Python 3.12+
- No explicit formatter configuration (Makefile has no lint/format targets)
- Code follows PEP 8 conventions

**Linting:**
- No explicit linting tool configured in pyproject.toml
- Follows standard Python conventions

## Import Organization

**Order:**
1. `from __future__ import annotations` (absolute first)
2. Built-in modules (`os`, `sys`, `logging`, `uuid`, `pathlib`, `typing`, `functools`)
3. Third-party libraries (`sqlalchemy`, `pydantic`, `fastapi`, `yaml`, `httpx`, `psycopg`, `exchange_calendars`)
4. Project imports (`trading_platform.*`)

**Path Aliases:**
- No path aliases configured; all imports are absolute from `trading_platform.*` root
- All imports start with package root: `from trading_platform.core.settings import Settings`

**Conditional imports:**
- Use `TYPE_CHECKING` block for forward references to avoid circular imports:
  ```python
  from typing import TYPE_CHECKING
  if TYPE_CHECKING:
      from sqlalchemy.orm import Session as DbSession
      from trading_platform.strategies.signals import SignalBatch
  ```

## Error Handling

**Patterns:**
- Raise specific built-in exceptions: `ValueError`, `KeyError`, `LookupError`, `NotImplementedError`
- Custom exceptions are dataclasses that inherit from built-ins: `UnknownStrategyError(KeyError)` in `src/trading_platform/strategies/registry.py`
- Service-specific exceptions: `AlpacaAuthError`, `AlpacaClientError`, `PolygonAuthError`, `PolygonClientError` in service modules
- Always provide descriptive messages: `raise LookupError(f"Run '{run_id}' was not found.")`
- Use `from exc` to chain exceptions and preserve stack traces

## Logging

**Framework:** Python's built-in `logging` module

**Patterns:**
- Configure logging once via `configure_logging()` in `src/trading_platform/core/logging.py`
- Use `logging.getLogger(__name__)` to get module-scoped logger
- Structured logging with JSON format via custom `JsonLogFormatter` class
- Log context passed via `extra={"context": {...}}` parameter
- Helper function `emit_structured_log()` provides consistent context handling
- Log context includes: `strategy_id`, `run_id`, `session_date`, `strategy_status`, `blocked_reason`, plus extra kwargs

## Comments

**When to Comment:**
- Docstrings on all public functions, classes, and modules using triple-quote format
- Docstrings describe purpose, args, returns, and notable behavior
- Inline comments for non-obvious logic
- Section separators for grouping related functions: `# ----------------------------------------------------------------`

## Function Design

**Size:**
- Functions typically 5-30 lines
- Larger functions split with helper functions prefixed with underscore
- Example: `_resolve_config_locations()`, `_load_yaml_file()`, `_build_session_rows()` are private helpers

**Parameters:**
- Use keyword-only parameters (`*,`) for clarity on function intention
- Type hints mandatory using Python 3.10+ union syntax (`Type | None`)

**Return Values:**
- All functions have explicit return type hints: `-> dict[str, Any]`, `-> Settings`, `-> None`
- Return types use `dict[str, Any]` for flexible JSON-like structures
- Use `-> None` for procedures that don't return values

## Module Design

**Exports:**
- Explicit `__all__` lists for modules exporting multiple items
- Example from `src/trading_platform/db/models/__init__.py` aggregates all ORM models
- Allows importing from parent: `from trading_platform.db.models import StrategyRun, StrategyRunStatus`

**Barrel Files:**
- Use barrel files (`__init__.py`) to aggregate related exports
- All modules start with docstring explaining purpose

**Configuration & Constants:**
- Use Pydantic `BaseModel` for typed configuration sections
- Use `@property` for computed values
- Use Pydantic `Field()` with validation: `Field(default=0.01, ge=0.0, le=1.0)`
- Load settings via singleton cached function: `load_settings()` with `@lru_cache(maxsize=1)`
- Provide cache-clearing function for testing: `clear_settings_cache()`

---

*Convention analysis: 2026-04-16*
