# Testing Patterns

**Analysis Date:** 2026-04-16

## Test Framework

**Runner:**
- `pytest` 9.0.0+
- Config: `pyproject.toml` with `[tool.pytest.ini_options]` section
- Test paths: `tests/` directory

**Assertion Library:**
- pytest's built-in assertions
- FastAPI `TestClient` for API testing

**Run Commands:**
```bash
PYTHONPATH=src pytest tests/ -q              # Run all tests
pytest tests/ -q --tb=short                  # Run with short traceback
```

## Test File Organization

**Location:**
- Co-located in `tests/` directory at project root (separate from source)
- All test files in single directory: `tests/test_*.py`

**Naming:**
- Test files: `test_*.py` (e.g., `test_backtest_runner.py`, `test_strategy_registry.py`)
- Test functions: `test_*` (e.g., `test_registry_lists_and_resolves_default_strategy()`)

## Test Structure

**Common imports:**
```python
from __future__ import annotations

import os
import sys
import uuid
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
import psycopg
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
```

**Fixture patterns:**
- Use `@pytest.fixture()` decorator
- Return type hints with `Iterator` for cleanup: `def fixture() -> Iterator[str]:`
- Fixtures yield resources and cleanup in `finally` blocks
- Example from `tests/test_backtest_runner.py`:
  ```python
  @pytest.fixture()
  def migrated_backtest_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
      database_name = f"backtest_runner_{uuid.uuid4().hex[:8]}"
      admin_params = _admin_connection_settings()

      try:
          with _connect_admin(admin_params) as connection:
              with connection.cursor() as cursor:
                  cursor.execute(f'CREATE DATABASE "{database_name}"')
      except psycopg.Error as exc:
          pytest.fail(
              "PostgreSQL is required for tests/test_backtest_runner.py. "
              f"Connection error: {exc}"
          )

      _set_database_env(monkeypatch, database_name)
      clear_settings_cache()
      clear_engine_cache()
      command.upgrade(build_alembic_config(), "head")

      try:
          yield database_name
      finally:
          clear_settings_cache()
          clear_engine_cache()
          # Cleanup code
  ```

**Test organization:**
```python
def test_feature_name() -> None:
    # Arrange
    setup_data = ...

    # Act
    result = function_under_test(setup_data)

    # Assert
    assert result.status == "success"
```

## Mocking

**Framework:** `monkeypatch` (pytest built-in)

**Patterns:**
- Use `monkeypatch` fixture to override environment variables: `monkeypatch.setenv("KEY", "value")`
- Use `monkeypatch` to override module attributes
- Use test fixtures to set up real or mock resources

**What to Mock:**
- Environment variables (via `monkeypatch.setenv()`)
- Settings cache clearing: `clear_settings_cache()`, `clear_engine_cache()`
- Database connection parameters for isolated test databases

**What NOT to Mock:**
- Database layer when tests require persistence (create temporary test databases instead)
- Service classes that are part of the system under test
- Strategy implementations (test real implementations)

## Fixtures and Factories

**Test Data:**
- Use helper functions to seed test data: `_seed_symbol_and_bars()` in `tests/test_backtest_runner.py`
- Example pattern:
  ```python
  def _seed_symbol_and_bars(
      session,
      *,
      ticker: str,
      bar_map: dict[date, tuple[str, str]],
  ) -> Symbol:
      symbol = Symbol(id=uuid.uuid4(), ticker=ticker, active=True)
      session.add(symbol)
      session.flush()

      for session_date, prices in bar_map.items():
          open_price, close_price = prices
          session.add(
              DailyBarModel(
                  id=uuid.uuid4(),
                  symbol_id=symbol.id,
                  session_date=session_date,
                  open=Decimal(open_price),
                  high=Decimal(close_price) + Decimal("1"),
                  low=Decimal(open_price) - Decimal("1"),
                  close=Decimal(close_price),
                  volume=1_000_000,
                  adjusted=True,
                  provider="polygon",
              )
          )

      session.flush()
      return symbol
  ```

**Location:**
- Helper functions defined in test files directly (not in separate factory modules)
- Database fixtures use temporary isolated PostgreSQL databases for each test

## Coverage

**Requirements:** No explicit coverage requirements configured

## Test Types

**Unit Tests:**
- Test individual services and functions in isolation
- Example: `test_strategy_registry.py` tests registry resolution
- Don't require database when logic is pure

**Integration Tests:**
- Test components working together
- Require database: `test_backtest_runner.py`, `test_market_data_access.py`
- Use temporary isolated PostgreSQL databases
- Example: test full backtest workflow from signals to trades

**E2E Tests:**
- FastAPI `TestClient` tests for API endpoints
- Example from `test_app_boot.py`:
  ```python
  app = create_app()
  with TestClient(app) as client:
      response = client.get("/strategies")
      assert response.status_code == 200
  ```

**Database Tests:**
- Tests requiring PostgreSQL run with temporary isolated databases
- Database fixture pattern:
  ```python
  @pytest.fixture()
  def migrated_access_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
      database_name = f"access_test_{uuid.uuid4().hex[:8]}"
      admin_params = _admin_connection_settings()

      with _connect_admin(admin_params) as connection:
          with connection.cursor() as cursor:
              cursor.execute(f'CREATE DATABASE "{database_name}"')

      _set_database_env(monkeypatch, database_name)
      clear_settings_cache()
      clear_engine_cache()
      command.upgrade(build_alembic_config(), "head")

      try:
          yield database_name
      finally:
          clear_settings_cache()
          clear_engine_cache()
          with _connect_admin(admin_params) as connection:
              with connection.cursor() as cursor:
                  cursor.execute(f'DROP DATABASE IF EXISTS "{database_name}"')
  ```

## Common Patterns

**Async Testing:**
- Not used (application is synchronous with FastAPI)
- Uses `TestClient` for synchronous testing of async endpoints

**Error Testing:**
```python
import pytest

def test_error_case() -> None:
    with pytest.raises(UnknownStrategyError):
        registry.resolve("missing_strategy")
```

**Configuration Testing:**
- Use temporary config files with `tmp_path` fixture
- Example from `test_app_boot.py`:
  ```python
  def _write_config(path: Path, payload: dict) -> None:
      path.write_text(yaml.safe_dump(payload, sort_keys=False))

  def test_settings_loader_merges_file_and_environment(monkeypatch, tmp_path: Path) -> None:
      config_file = tmp_path / "app.yaml"
      strategy_dir = tmp_path / "strategies"
      strategy_dir.mkdir()

      _write_config(config_file, {...})
      _write_config(strategy_dir / "trend_following_daily.yaml", {...})

      monkeypatch.setenv("TRADING_PLATFORM_CONFIG_FILE", str(config_file))
      clear_settings_cache()
      settings = load_settings()

      assert settings.api.port == 9090
  ```

## Test Execution

**Default test set:**
```bash
PYTHONPATH=src pytest \
    tests/test_app_boot.py \
    tests/test_db_migrations.py \
    tests/test_strategy_registry.py \
    tests/test_dry_run.py \
    tests/test_market_data_ingestion.py \
    tests/test_market_data_access.py \
    tests/test_trend_following_strategy.py -q
```

**Requirements:**
- PostgreSQL must be running for database tests
- Environment variables from `.env` or `docker compose up -d db`

---

*Testing analysis: 2026-04-16*
