# Testing Patterns

**Analysis Date:** 2026-09-23

## Test Framework

**Runner:**
- **Python:** pytest 9.x
  - Config: `pyproject.toml` → `[tool.pytest.ini_options]` with `testpaths = ["tests"]`
  - Tests in `/tests/` directory at project root
- **TypeScript:** Vitest 4.1.x
  - Config: `console/vitest.config.ts` with `environment: "node"` and pattern `src/**/*.test.{ts,tsx}`
  - Tests co-located with source files using `.test.ts` or `.test.tsx` suffix

**Assertion Library:**
- **Python:** pytest's built-in assertion introspection
- **TypeScript:** Vitest's `expect()` API (compatible with Jest)

**Run Commands:**
```bash
# Python
pytest                      # Run all tests
pytest -v                   # Verbose output
pytest tests/test_*.py      # Run specific test file
pytest -k "test_cancel"     # Run tests matching pattern

# TypeScript
npm test                    # Run tests (cd console/)
vitest run                  # Run once (not watch mode)
vitest                      # Watch mode
```

## Test File Organization

**Location:**
- **Python:** Co-located in `tests/` directory at project root; mirrors source structure by naming (e.g., `test_job_cancellation.py` tests job cancellation logic)
- **TypeScript:** Co-located with source in `console/src/**/*.test.ts(x)` (e.g., `api.test.ts` in `src/lib/`)

**Naming:**
- **Python:** `test_<feature>.py` (e.g., `test_job_cancellation.py`, `test_analytics_service.py`)
- **TypeScript:** `<module>.test.ts(x)` (e.g., `api.test.ts`)

**Structure:**
```
Trading-Bot-Project/
├── tests/                           # Python tests
│   ├── conftest.py                 # Shared fixtures
│   ├── support/                    # Helper modules
│   ├── fixtures/                   # Reusable test data
│   └── test_*.py                   # Individual test files
│
└── console/src/
    ├── lib/
    │   ├── api.ts                  # Source
    │   └── api.test.ts             # Co-located test
    ├── components/
    │   ├── ErrorState.tsx          # Component
    │   └── ErrorState.test.tsx      # Co-located test (if present)
```

## Test Structure

**Suite Organization:**

**Python:**
```python
"""Phase 17 Job cancellation behavior tests (JOB-06, D-07-D-10)."""

from __future__ import annotations

import pytest
from trading_platform.db.models import Job, JobStatus

def test_cancel_queued_job_transitions_immediately(migrated_job_cancellation_db: str) -> None:
    """Test that cancelling a QUEUED job transitions it to CANCELLED immediately."""
    settings = load_settings()
    with session_scope(settings) as session:
        job = _seed_job(session)
        job_id = job.id

    result = request_cancellation(job_id=job_id, requested_by="operator_1", settings=settings)

    assert result.mode == "immediate"
    assert result.accepted is True
    assert result.status is JobStatus.CANCELLED

    with session_scope(settings) as session:
        persisted = session.get(Job, job_id)
        assert persisted is not None
        assert persisted.status is JobStatus.CANCELLED
```

**TypeScript:**
```typescript
import { describe, expect, it, vi, afterEach } from "vitest";
import { fetchApi } from "./api";

describe("fetchApi", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("returns ok:true with parsed data on a successful JSON response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse(200, { status: "ok" })),
    );

    const result = await fetchApi<{ status: string }>("/api/v1/system");

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.data).toEqual({ status: "ok" });
      expect(result.endpoint).toBe("/api/v1/system");
    }
  });
});
```

**Patterns:**
- **Setup:** Helper functions (e.g., `_seed_job()`, `_seed_running_job()`) prepare test state
- **Fixtures:** `@pytest.fixture` at module or file scope; parametrized fixtures for repeated setups
- **Teardown:** Context managers (`with session_scope()`) handle cleanup; `afterEach()` in TypeScript
- **Assertions:** Use explicit assertions; avoid testing internal state only test observable behavior

## Mocking

**Framework:**
- **Python:** `pytest.MonkeyPatch` fixture for environment and function patching
- **TypeScript:** Vitest's `vi` module for stubbing globals, mocking modules, and spying on functions

**Patterns:**

**Python:**
```python
def test_with_monkeypatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock environment variables and internal functions."""
    monkeypatch.setenv("TRADING_PLATFORM_DATABASE__NAME", "test_db")
    monkeypatch.setitem(settings.model_config, "env_file", None)
    
    # Function now sees mocked environment
```

**TypeScript:**
```typescript
it("handles network failures", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockRejectedValue(new TypeError("Failed to fetch")),
  );

  const result = await fetchApi("/api/v1/system");
  
  expect(result.ok).toBe(false);
  if (!result.ok) {
    expect(result.status).toBeNull();
  }
  
  vi.unstubAllGlobals(); // Always cleanup
});
```

**What to Mock:**
- Database connections and fixtures (see `migrated_job_cancellation_db`)
- External API calls (Polygon, Alpaca) via mocked HTTP responses
- Global state (environment variables via `monkeypatch`)
- File system operations (temporary databases, file I/O)

**What NOT to Mock:**
- Core business logic (job cancellation, portfolio calculations)
- Domain models and dataclasses
- Type assertions and type guards
- Single-responsibility functions (mock their dependencies, not them)

## Fixtures and Factories

**Test Data:**

**Python:**
```python
def _seed_job(session: Any, *, status: JobStatus = JobStatus.QUEUED, **overrides: Any) -> Job:
    """Factory helper to create test job with sensible defaults."""
    defaults: dict[str, Any] = {
        "job_type": "phase17_cancellation_probe",
        "payload": {},
        "status": status,
    }
    defaults.update(overrides)
    job = Job(**defaults)
    session.add(job)
    session.flush()
    return job

def _seed_running_job(session: Any, **overrides: Any) -> Job:
    """Seed a QUEUED Job and transition it to RUNNING."""
    job = _seed_job(session, status=JobStatus.QUEUED, **overrides)
    apply_job_transition(
        session, job_id=job.id, request=JobTransitionRequest(event_type=JobEventType.CLAIMED)
    )
    return job
```

**TypeScript:**
```typescript
function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function textResponse(status: number, body: string): Response {
  return new Response(body, {
    status,
    headers: { "content-type": "text/html" },
  });
}
```

**Location:**
- **Python:** Helper functions prefixed with `_` (e.g., `_seed_job()`, `_admin_connection_settings()`) live in test files alongside tests
- **TypeScript:** Helper functions defined in test files or imported from test utilities

## Coverage

**Requirements:** Not explicitly enforced; aim for high coverage of critical paths (job lifecycle, execution, reconciliation)

**View Coverage:**
```bash
# Python
pytest --cov=trading_platform tests/

# TypeScript (if configured)
# Not currently configured; would need vitest coverage setup
```

## Test Types

**Unit Tests:**
- **Scope:** Single function/module behavior in isolation
- **Approach:**
  - Python: Test individual service methods, domain logic, and data transformations
  - TypeScript: Test utility functions and helper logic (e.g., `fetchApi()`)
  - Use fixtures for stable test data
  - Examples: `test_job_cancellation.py` tests cancellation logic with seeded jobs

**Integration Tests:**
- **Scope:** Multiple components working together (job lifecycle with database, API endpoints)
- **Approach:**
  - Python: Use database fixtures (`migrated_job_cancellation_db`, `migrated_job_lifecycle_db`) to test database interactions
  - Multiple test functions sharing same fixture for transactional tests
  - Example: `test_job_lifecycle.py` tests job state transitions end-to-end

**E2E Tests:**
- **Framework:** Not automated; manual testing via operator console and API
- **Approach:** Operator-level smoke tests via Vercel deployments and read-only console

## Common Patterns

**Async Testing:**

**Python:**
```python
# Pytest handles async naturally; mark functions as `async def`
async def test_async_job_operation() -> None:
    # Async test code
    result = await some_async_function()
    assert result is not None
```

**TypeScript:**
```typescript
it("handles async API calls", async () => {
  const result = await fetchApi<{ status: string }>("/api/v1/system");
  
  expect(result.ok).toBe(true);
  if (result.ok) {
    expect(result.data.status).toBe("ok");
  }
});
```

**Error Testing:**

**Python:**
```python
def test_cancel_running_job_persists_request_without_transitioning(
    migrated_job_cancellation_db: str,
) -> None:
    """Test that cancelling a RUNNING job raises JobNotCancellableError."""
    settings = load_settings()
    with session_scope(settings) as session:
        job = _seed_running_job(session)
        job_id = job.id

    # Expect cooperative mode, not immediate transition
    result = request_cancellation(
        job_id=job_id, requested_by="operator_1", reason="stop it", settings=settings
    )
    
    assert result.mode == "cooperative"
    # Job is still RUNNING; cancellation is pending
```

**TypeScript:**
```typescript
it("returns ok:false with status:null on network failure", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockRejectedValue(new TypeError("Failed to fetch")),
  );

  const result = await fetchApi("/api/v1/system");

  expect(result.ok).toBe(false);
  if (!result.ok) {
    expect(result.status).toBeNull();
    expect(result.message.toLowerCase()).toMatch(/unreachable|network|failed/);
    expect(result.endpoint).toBe("/api/v1/system");
  }
});
```

## Conftest and Shared Fixtures

**Location:** `tests/conftest.py` (Python only; TypeScript fixtures are per-file)

**Example:**
```python
@pytest.fixture(autouse=True)
def isolate_operator_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep operator's real .env out of test process (00-VERIFY step 1)."""
    monkeypatch.setitem(_settings.EnvironmentOverrides.model_config, "env_file", None)
    _settings.clear_settings_cache()
    yield
    _settings.clear_settings_cache()
```

**Autouse Fixtures:**
- `isolate_operator_env`: Runs on every test; clears settings cache and disables .env loading to prevent real environment contamination

---

*Testing analysis: 2026-09-23*
