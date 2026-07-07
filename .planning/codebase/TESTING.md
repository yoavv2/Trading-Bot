# Testing Patterns

**Analysis Date:** 2026-07-07

## Test Framework

**Runner:**
- pytest 9.0.0+
- Config: `pyproject.toml` (minimal configuration)

**Run Commands:**
```bash
pytest tests/                  # Run all tests
pytest tests/ -v              # Verbose output
pytest tests/ --tb=short      # Shorter tracebacks
pytest tests/test_strategy_registry.py  # Run single file
pytest tests/ -k "test_normalize"  # Run matching tests
```

**Configuration (`pyproject.toml`):**
```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
```

**Assertion Library:**
- pytest's built-in assertions with `assert` statements
- pytest helpers: `pytest.raises()`, `pytest.mark`

## Test File Organization

**Location:**
- Co-located in `tests/` directory parallel to `src/`
- No conftest.py file for shared fixtures
- Individual test files responsible for their own setup

**Naming:**
- Files: `test_{module}.py` (e.g., `test_market_data_ingestion.py`, `test_strategy_registry.py`)
- Classes: `Test{ComponentName}` (e.g., `TestPolygonClientAuth`, `TestPolygonClientFetch`)
- Functions: `test_{specific_behavior}()` (e.g., `test_normalize_timestamp_converts_ms_to_utc_datetime`)

**Structure:**
```
tests/
├── fixtures/              # Test data files
│   └── polygon_daily_bars.json
├── conftest.py           # [Not present - fixtures defined per file]
├── test_app_boot.py
├── test_strategy_registry.py
├── test_market_data_ingestion.py
└── ...
```

## Test Structure

**Test Class Organization:**
```python
class TestNormalizationHelpers:
    """Group related unit tests in classes for logical organization."""
    
    def test_normalize_timestamp_converts_ms_to_utc_datetime(self) -> None:
        # Arrange
        ts_ms = 1704067200000
        
        # Act
        result = _normalize_timestamp(ts_ms)
        
        # Assert
        assert result is not None
        assert result.tzinfo is not None
        assert result == datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

    def test_normalize_timestamp_none_input(self) -> None:
        assert _normalize_timestamp(None) is None
```

**Test Function Patterns:**

1. **Unit Test (Isolated):**
```python
def test_registry_lists_and_resolves_default_strategy() -> None:
    clear_settings_cache()
    registry = build_default_registry(load_settings())

    strategies = registry.list_public()

    assert len(strategies) == 1
    assert strategies[0]["strategy_id"] == "trend_following_daily"
```

2. **Integration Test (With Database):**
```python
@pytest.fixture()
def migrated_order_state_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Create a temporary database, migrate it, and clean up."""
    database_name = f"order_state_machine_{uuid.uuid4().hex[:8]}"
    admin_params = _admin_connection_settings()
    
    try:
        with _connect_admin(admin_params) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f'CREATE DATABASE "{database_name}"')
    except psycopg.Error as exc:
        pytest.fail("PostgreSQL is required...")
    
    _set_database_env(monkeypatch, database_name)
    clear_settings_cache()
    clear_engine_cache()
    command.upgrade(build_alembic_config(), "head")
    
    try:
        yield database_name
    finally:
        # Cleanup
        clear_settings_cache()
        clear_engine_cache()
```

3. **API Test (With TestClient):**
```python
def test_app_bootstrap_serves_foundation_endpoints(monkeypatch, tmp_path: Path) -> None:
    config_file = tmp_path / "app.yaml"
    strategy_dir = tmp_path / "strategies"
    strategy_dir.mkdir()
    
    _write_config(config_file, {...})
    
    monkeypatch.setenv("TRADING_PLATFORM_CONFIG_FILE", str(config_file))
    monkeypatch.setenv("TRADING_PLATFORM_STRATEGY_CONFIG_DIR", str(strategy_dir))
    
    clear_settings_cache()
    app = create_app()
    
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
```

**Imports Pattern:**
Each test file adds src/ to path:
```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
```

## Mocking

**Framework:** `unittest.mock` (standard library)

**Mocking HTTP Responses:**
```python
from unittest.mock import MagicMock, patch

def test_fetch_returns_normalized_bars(self) -> None:
    fixture = _load_fixture()
    settings = _make_polygon_settings()
    
    with patch("httpx.Client.get", return_value=self._make_response(fixture)):
        client = PolygonClient(settings)
        bars = client.fetch_daily_bars(
            DailyBarRequest(
                symbol="AAPL",
                from_date=date(2024, 1, 1),
                to_date=date(2024, 1, 3),
            )
        )
    
    assert len(bars) == 3
```

**Response Mock Helper:**
```python
def _make_response(self, payload: dict) -> MagicMock:
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = payload
    return mock_response
```

**Stateful Mocking (Side Effects):**
```python
responses = [self._make_response(page1), self._make_response(page2)]
call_count = 0

def mock_get(url, **kwargs):
    nonlocal call_count
    result = responses[call_count]
    call_count += 1
    return result

with patch("httpx.Client.get", side_effect=mock_get):
    # test code
```

**Patching Modules:**
Use full import path in patch target:
```python
with patch("httpx.Client.get", return_value=...):
    # Tests code that calls httpx.Client.get()
```

**What to Mock:**
- External HTTP/API calls (Polygon, Alpaca)
- System time (datetime, time.sleep)
- File system (use pytest's tmp_path instead)

**What NOT to Mock:**
- Internal database calls (use real database in tests)
- Internal service methods (test integration)
- Pydantic model validation

## Fixtures and Factories

**Test Data Helpers (Not Global Fixtures):**
Helper functions prefixed with underscore, defined per test file:

```python
def _make_polygon_settings(api_key: str = "test-key") -> PolygonProviderSettings:
    return PolygonProviderSettings(
        base_url="https://api.polygon.io",
        api_key=api_key,
        adjusted=True,
        max_retries=0,
        retry_backoff_factor=0.0,
        timeout_seconds=5.0,
    )

def _make_market_data_settings(api_key: str = "test-key") -> MarketDataSettings:
    return MarketDataSettings(
        polygon=_make_polygon_settings(api_key=api_key),
        ingest=IngestSettings(
            default_lookback_days=10,
            universe=("AAPL", "MSFT"),
        ),
    )
```

**Fixture Files:**
- Location: `tests/fixtures/`
- Format: JSON files with sample API responses
- Example: `tests/fixtures/polygon_daily_bars.json`

**Loading Fixtures:**
```python
FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "polygon_daily_bars.json"

def _load_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text())
```

**pytest.fixture Usage:**
For complex setup/teardown (database creation):
```python
@pytest.fixture()
def migrated_order_state_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    # Setup
    database_name = f"test_db_{uuid.uuid4().hex[:8]}"
    _create_database(database_name)
    _migrate_database(database_name)
    
    yield database_name  # Tests run here
    
    # Teardown
    _drop_database(database_name)
```

**monkeypatch Fixture:**
Pytest's built-in fixture for environment variables and module state:
```python
def test_settings_loader(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRADING_PLATFORM_API__PORT", "9090")
    monkeypatch.setenv("TRADING_PLATFORM_CONFIG_FILE", str(config_file))
    
    clear_settings_cache()
    settings = load_settings()
    assert settings.api.port == 9090
```

## Coverage

**Requirements:** Not enforced in `pyproject.toml`

**View Coverage:**
```bash
pytest tests/ --cov=src/trading_platform --cov-report=html
```

**Coverage Target:** Not specified in configuration

## Test Types

**Unit Tests:**
- Isolated testing of single functions/classes
- Mock external dependencies (HTTP, database)
- Located in `tests/test_*.py` files
- Example: `tests/test_market_data_ingestion.py::TestNormalizationHelpers`
- Quick to run, deterministic

**Integration Tests:**
- Test interaction between components
- Use real database (temporary, created per test)
- Test service-to-service interactions
- Example: `tests/test_order_state_machine.py` creates actual PostgreSQL databases
- Slower but comprehensive

**API/E2E Tests:**
- Use FastAPI's `TestClient` for synchronous API testing
- No async test runner configured
- Located in files like `tests/test_app_boot.py`, `tests/test_api_reads.py`
- Example:
```python
from fastapi.testclient import TestClient

app = create_app()
with TestClient(app) as client:
    response = client.get("/api/v1/strategies")
    assert response.status_code == 200
```

## Common Patterns

**Error Testing:**
```python
def test_raises_auth_error_when_api_key_is_empty(self) -> None:
    settings = _make_polygon_settings(api_key="")
    with pytest.raises(PolygonAuthError, match="API key"):
        PolygonClient(settings)

def test_raises_auth_error_on_401_response(self) -> None:
    settings = _make_polygon_settings()
    mock_response = MagicMock()
    mock_response.status_code = 401
    mock_response.raise_for_status.return_value = None
    
    with patch("httpx.Client.get", return_value=mock_response):
        client = PolygonClient(settings)
        with pytest.raises(PolygonAuthError, match="401"):
            client.fetch_daily_bars(request)
```

**Pagination Testing:**
Tests verify that pagination handlers follow next_url:
```python
def test_fetch_handles_pagination(self) -> None:
    """Client must follow next_url to collect all pages."""
    page1 = {
        "status": "OK",
        "results": [...],
        "next_url": "https://api.polygon.io/...",
    }
    page2 = {
        "status": "OK",
        "results": [...],
    }
```

**Database State Testing:**
Tests that create specific database states:
```python
def test_apply_order_transition(migrated_order_state_db):
    # Database is already migrated via fixture
    # Create test data
    run = _create_strategy_run(...)
    event = _create_order_event(...)
    
    # Apply transition
    result = apply_order_transition(run.id, event)
    
    # Verify state changed in database
    session = session_scope()
    order = session.query(PaperOrder).filter(...).first()
    assert order.status == OrderLifecycleState.FILLED
```

**Settings Override Testing:**
Tests that environment variables and config files properly override defaults:
```python
def test_settings_loader_merges_file_and_environment(monkeypatch, tmp_path):
    config_file = tmp_path / "app.yaml"
    _write_config(config_file, {"api": {"port": 8000}})
    
    monkeypatch.setenv("TRADING_PLATFORM_API__PORT", "9090")
    monkeypatch.setenv("TRADING_PLATFORM_CONFIG_FILE", str(config_file))
    
    clear_settings_cache()
    settings = load_settings()
    
    assert settings.api.port == 9090  # Environment override wins
```

## Setup and Teardown

**Per-Test Setup:**
Use function scope (default) or class scope as needed:
```python
@pytest.fixture()
def test_db(tmp_path: Path) -> Iterator[Session]:
    db_url = f"sqlite:///{tmp_path}/test.db"
    setup_database(db_url)
    yield get_session()
    cleanup_database(db_url)
```

**Cache Clearing:**
Modules with caching require explicit clearing between tests:
```python
def test_something() -> None:
    clear_settings_cache()  # Clear @lru_cache from load_settings()
    clear_engine_cache()     # Clear SQLAlchemy engine cache
    # Test code
```

**Module Initialization:**
Test files explicitly initialize settings/engine for each test to avoid cross-test pollution:
```python
def test_app_bootstrap(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADING_PLATFORM_CONFIG_FILE", str(config_file))
    clear_settings_cache()  # Ensure fresh load
    app = create_app()
    # Test code
```

---

*Testing analysis: 2026-07-07*
