# Testing Patterns

**Analysis Date:** 2026-11-01

Scope: HEAD `73a70e7` plus the uncommitted console edits in the working tree. The Python suite was **not** run for this analysis; the console suite was run. Baseline statements below cite the last recorded runs and say so.

## Test Framework

**Python runner:**
- pytest, pinned to `>=9.0.0,<10.0.0` in the `dev` extra of `pyproject.toml`. The `.venv` runs Python 3.13.15 although the project targets 3.12.
- Config: `[tool.pytest.ini_options]` in `pyproject.toml` sets only `testpaths = ["tests"]`. There is no `pytest.ini` or `setup.cfg`. The only conftest is `tests/conftest.py`. No custom markers are registered.
- Assertions: pytest's plain `assert` with assertion rewriting.
- Database access: SQLAlchemy 2 and `psycopg` 3 against a real PostgreSQL server.
- HTTP: FastAPI `TestClient` (`fastapi.testclient`) and `httpx` `MockTransport`.

**Console runner:**
- Vitest `^4.1.10` (`console/package.json`; script `"test": "vitest run"`).
- Config: `console/vitest.config.ts`. The default environment is `node`. `include: ["src/**/*.test.{ts,tsx}"]`. The alias `"@"` maps to `console/src`, mirroring `console/tsconfig.json`.
- DOM tests opt in per file with a docblock, `// @vitest-environment jsdom`. 39 files use it, for example `console/src/components/research/research.test.tsx`.
- Rendering: `@testing-library/react` `^16.3.2` with `@testing-library/dom` `^10.4.1`, and `jsdom` `^29.1.1`.
- Assertions: Vitest's built-in `expect`. No `jest-dom` matchers are installed, so assert on text and roles through Testing Library queries.

**Browser testing:** Playwright is **not** a dependency in `console/package.json` or `pyproject.toml`, and no Playwright spec files exist in the repository. Browser runs are ad hoc and are recorded in the planning notes (`.planning/research/strategy-research-pivot/01-IMPLEMENTATION-PLAN.md`).

**Coverage:** not configured in either stack. There is no coverage flag and no threshold.

**CI:** no CI configuration (`.github/`) exists at the repository root. All verification is local.

## Run Commands

**Python** (from the repo root; the venv is `.venv/`):
```bash
make test                                                                  # Six trading-era files only (see below)
PYTHONPATH=src .venv/bin/pytest -q                                         # Full suite (README "Testing")
PYTHONPATH=src .venv/bin/pytest tests/test_research_assistant.py -q        # One file
PYTHONPATH=src .venv/bin/pytest tests/test_research_assistant.py::test_output_schema_meets_provider_constraints -q
PYTHONPATH=src .venv/bin/pytest -k "research and budget" -q                # Keyword filter
PYTHONPATH=src .venv/bin/pytest tests/test_research_*.py -q                # Research modules
PYTHONPATH=src .venv/bin/pytest --lf -q                                    # Re-run last failures
```

`make test` (see the `test` target in `Makefile`) runs `tests/test_app_boot.py tests/test_db_migrations.py tests/test_strategy_registry.py tests/test_market_data_ingestion.py tests/test_market_data_access.py tests/test_trend_following_strategy.py -q`. The README states that this is **not** the full suite.

**Console** (from `console/`):
```bash
npm test                                             # vitest run, all suites, one pass
npx vitest run src/components/research               # Path filter
npx vitest run src/lib/research/preflight.test.ts    # One file
npx vitest                                           # Watch mode
npx tsc --noEmit                                     # Type check (strict)
npx eslint                                           # Lint (next core-web-vitals + typescript)
npm run build                                        # Production build
```

**Static checks for Python** (gates; formatting is not a gate):
```bash
.venv/bin/ruff check src tests      # Lint: E, F, I, W; E501 ignored
.venv/bin/mypy                      # Typed packages only: execution, reconciliation, config
```

**Make targets that touch the database but are not tests:** `make research-bootstrap` seeds the research database. `make up` starts the full container stack (`--profile stack`: database, migrate, API, worker), which takes port 8000 and blocks `make dev`. `make dev` starts the research API, worker, and console for manual checks. To start only the database for tests, run `docker compose up -d` with no profile.

## Test File Organization

**Location:**
- Python: all test modules sit in the flat `tests/` directory: 162 entries, 178 `.py` files including helpers. There are no per-package test directories and no `tests/__init__.py`. `tests/conftest.py` inserts the repo root and `src/` into `sys.path`.
- Shared helpers live in `tests/support/` (`__init__.py` is empty). Recorded data lives in `tests/fixtures/` (only `polygon_daily_bars.json`).
- Console: tests are **colocated** with the module they test, as `*.test.ts` or `*.test.tsx` under `console/src/`. Examples: `console/src/lib/research/preflight.test.ts` beside `preflight.ts`; `console/src/components/research/studyWizard.test.tsx` beside `StudyWizard.tsx`.

**Naming:**
- Python: `test_<area>.py`, functions `test_<behaviour_in_snake_case>`. Names describe the behaviour pinned, for example `test_header_auth_only_and_no_token_anywhere_in_the_url` and `test_rows_are_sorted_ascending_even_when_the_provider_returns_descending` in `tests/test_tiingo_client.py`.
- Console: `<Module>.test.tsx` for components, `<module>.test.ts` for logic. Casing is mixed (`research.test.tsx`, `studyWizard.test.tsx`, `jobFormKit.test.tsx`); follow the name of the module under test.

**Research test modules** (the active product):
- `tests/test_research_foundation.py`, `tests/test_research_api_isolation.py`, `tests/test_research_strategies_api.py`, `tests/test_research_data_pipeline.py`, `tests/test_research_studies_pipeline.py`, `tests/test_research_engine_metrics.py`, `tests/test_research_budget.py`, `tests/test_research_console_contract.py`, `tests/test_research_bootstrap.py`, `tests/test_research_assistant.py`, `tests/test_research_assistant_converter.py`, `tests/test_research_study_assistant.py`, `tests/test_research_study_preflight.py`, `tests/test_tiingo_client.py`.

**Console test modules** (51 files at measurement): for example `components/research/research.test.tsx`, `components/research/studyWizard.test.tsx`, `components/research/StudiesView.test.tsx`, `components/research/AssetsView.test.tsx`, `components/research/ResultsView.dashboard.test.tsx`, `lib/research/preflight.test.ts`, `lib/research/windows.test.ts`, `lib/api.test.ts`, `lib/useApiQuery.test.tsx`.

## Test Structure

**Suite layout** (`tests/test_research_assistant.py`, abridged to show the shape):
```python
"""S5 assistant: bounded, accounted, recorded drafting and revision (acceptance 20).

A stub provider stands in for the SDK in most tests; one test drives the real
``AnthropicProvider`` through the SDK against an ``httpx.MockTransport`` ...
"""

from __future__ import annotations

from tests.support.migrated_db import migrated_database

from trading_platform.services.research.assistant import OUTPUT_SCHEMA, ResearchAssistantService

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def research_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    with migrated_database(monkeypatch, "research_s5") as name:
        _configure(monkeypatch)
        yield name


# ---------------------------------------------------------------------------
# Pure pieces
# ---------------------------------------------------------------------------

def test_output_schema_meets_provider_constraints() -> None:
    """No recursion, no numeric/string bounds, additionalProperties false everywhere, ..."""
    ...
```

Patterns to follow:
- A module docstring states what the file pins and cites the acceptance or plan item when one exists.
- Sections are separated by `# ----` banner comments (`# Fixtures`, `# Pure pieces`).
- Pure logic is tested without a database. Tests whose claim involves persistence take the `research_db` fixture.
- Test functions are annotated `-> None`. Add a docstring when the behaviour needs explaining.

**Parametrization** is the norm for enumerable cases: 261 `@pytest.mark.parametrize` uses across `tests/`. Use it for closed-code tables. `test_closed_failure_codes(status, error)` in `tests/test_tiingo_client.py` is parametrized over the Tiingo failure codes. `test_integrity_row_codes_each_have_a_fixture` in `tests/test_research_data_pipeline.py` checks that every integrity code has a fixture.

**Markers:** only built-ins are used: `pytest.mark.parametrize` (261), `pytest.mark.usefixtures` (27), and one `pytest.mark.skipif`. Do not add a custom marker unless it is registered in `pyproject.toml`; none are registered today.

**Setup and teardown:**
- Fixtures that `yield` do their teardown after the `yield`. The DB helper drops its database in a `finally` block.
- Clear the caches with `clear_settings_cache()` and `clear_engine_cache()` around any database or environment change.
- Change the environment with `monkeypatch.setenv` or `monkeypatch.setitem`, not with `os.environ[...] = ...` in a test body.

## Fixtures and Factories

### Root autouse fixture

`tests/conftest.py` defines an **autouse** fixture, `isolate_operator_env`. It disables dotenv loading for every test:
```python
@pytest.fixture(autouse=True)
def isolate_operator_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(_settings.EnvironmentOverrides.model_config, "env_file", None)
    _settings.clear_settings_cache()
    yield
    _settings.clear_settings_cache()
```
The operator's `.env` must never reach a test. Do not remove this fixture, and do not load `.env` in a test.

### Migrated PostgreSQL databases

**Helper:** `migrated_database(monkeypatch, prefix, *, revision="head")` in `tests/support/migrated_db.py`. It is a context manager that:
1. Creates a throwaway database named `<prefix>_<8 hex chars>` through an admin connection.
2. Sets `TRADING_PLATFORM_DATABASE__HOST`, `__PORT`, `__USER`, `__PASSWORD`, and `__NAME` with `monkeypatch`.
3. Clears the settings and engine caches, then runs `alembic upgrade` to `revision` (default: head) through `scripts.migrate.build_alembic_config()`.
4. Yields the database name. On exit it terminates the backends of that database owned by `current_user`, then drops it.

If PostgreSQL is unreachable, the helper calls `pytest.fail(...)`. **A missing database produces failing tests, not skipped ones.** Start the database first with `docker compose up -d` (no profile starts only the `db` service; `make up` also starts the API and worker). Connection defaults are `localhost:5432` with the local development role. Override them with `TRADING_PLATFORM_DATABASE__*`, and with `TRADING_PLATFORM_ADMIN_DB` (default `postgres`) for the admin connection.

**Usage:** 58 test files use the helper; 49 import it. The standard per-module fixture wraps it:
```python
# tests/test_research_assistant.py
@pytest.fixture()
def research_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    with migrated_database(monkeypatch, "research_s5") as name:
        _configure(monkeypatch)
        yield name
```
Give each module a short, unique `prefix` (`research_s5`, `research_script`, and so on). The name of the throwaway database comes from it.

**Do not copy per-module `migrated_*_db` fixtures.** The helper is the shared mechanism. Use `revision=` to stop at a migration boundary; `test_migration_0031_round_trips` in `tests/test_research_data_pipeline.py` is an example.

**Non-superuser role:** the test role is not a superuser. Teardown can raise `InsufficientPrivilege` when a database is dropped. See Known Baseline Failures.

### Research seed data

`tests/support/research_fixtures.py` provides deterministic synthetic bars:
- `make_bars(symbol, sessions, *, seed=7, start_price=..., drift=..., volatility=...)` returns a list of `Bar` (a frozen dataclass), generated from a seeded `random.Random`.
- `weekday_sessions(start, count)` gives naive weekday sessions. `xnys_sessions(start, count)` gives real exchange sessions from `trading_platform.services.calendar.sessions_in_range`.
- `FakeBarStore(sessions, series)` serves `bars_for_sessions(...)` from memory with the same semantics as the access layer. It records every call in `.calls`. Use it when the code under test takes a bar store and no database is needed.
- `seed_research_bars(session, symbol, bars, *, provider="tiingo", both_series=True, ...)` writes the same series into a migrated database, for both adjusted and unadjusted rows.

```python
from tests.support.research_fixtures import FakeBarStore, make_bars, weekday_sessions

sessions = weekday_sessions(date(2024, 1, 2), 260)
store = FakeBarStore(sessions, {"SPY": make_bars("SPY", sessions, seed=11)})
```

### Other support helpers

Check `tests/support/` for an existing helper before writing setup code:

| Helper | Purpose |
|---|---|
| `tests/support/query_counter.py` | `count_queries(bind)` context manager. It yields a `QueryCounter` dataclass whose `count` field is updated while the block is open. Used by 24 test files, for example `tests/test_paper_preflight_query_count.py`, `tests/test_symbol_readiness.py`, `tests/test_calendar_facts.py`. |
| `tests/support/calendar_facts.py` | `seed_calendar`, `seed_bars`, `clock_at`, `et` for market-session fixtures. |
| `tests/support/symbol_metadata.py` | `ready_symbol_fields` for symbol rows that pass readiness. |
| `tests/support/scripted_broker.py` | Scripted broker double for the execution tests. About 20 KB; read it before use. |
| `tests/support/price_source.py` | `ScriptedPriceSource`, `FreshPriceSource`, `observation` for price-source doubles. |
| `tests/support/recovery_fixtures.py`, `operation_fixtures.py`, `paper_ownership.py`, `paper_eligibility.py`, `paper_execution_seams.py`, `basis_fixtures.py`, `real_reconciliation.py`, `account_check_fixtures.py`, `submission_attempts.py` | Seed functions for execution, recovery, and reconciliation tables (for example `seed_operation`, `seed_intent`, `seed_strategy`, `seed_fresh_broker_snapshot`). |
| `tests/support/budget_worker.py` | `admit_many(database_name, attempts, limit, tag)`. It runs in a separate OS process (see Concurrency below). |

### Fixtures in `conftest.py`

Only `tests/conftest.py` exists. Per-module fixtures (`@pytest.fixture` definitions: 151 lines across the suite) are the normal pattern. Keep a fixture in its module while only that module uses it. Move it to `tests/support/` when a second module needs it.

## Mocking

### Python: monkeypatch and fake classes

`pytest`'s `monkeypatch` is the default: 69 test files use `monkeypatch.setattr`. `unittest.mock` appears in 7 files. Prefer explicit fake classes that match the production duck type. Use `unittest.mock` only where call recording is needed and a fake class would be heavier.

**Stub provider for the research assistant** (`tests/test_research_assistant.py`):
```python
class StubProvider:
    """Scripted provider: each call pops the next scripted outcome; records every request."""

    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []
        self.delay = 0.0
        self.lock = threading.Lock()

    def complete(self, **kwargs: Any) -> ProviderResult:
        with self.lock:
            self.calls.append(kwargs)
            item = self.script.pop(0)
        if self.delay:
            time.sleep(self.delay)
        if isinstance(item, Exception):
            raise item
        return item
```
A script item is either a `ProviderResult`, usually built with the module's `ok(text, stop="end_turn")` helper, or an `Exception` to raise. Assert on `provider.calls` to prove what was sent and how often.

**Stub Tiingo client for the research pipeline** (`tests/test_research_data_pipeline.py`): a duck-typed class with `fetch_metadata`, `fetch_daily_prices`, and `close`. It has no base class. A rate-limit variant subclasses it, as `RateLimitedAfterOneClient`:
```python
class StubTiingoClient:
    def __init__(self, series: dict[str, list[TiingoDailyRow]], *, failing: set[str] | None = None) -> None:
        self.series = series
        self.failing = failing or set()
        self.requests_made = 0

    def fetch_daily_prices(self, ticker: str, from_date: date, to_date: date) -> list[TiingoDailyRow]:
        self.requests_made += 1
        return [r for r in self.series[ticker] if from_date <= r.session_date <= to_date]

    def close(self) -> None:
        pass
```

### Python: transport-level mocking for HTTP clients

Production HTTP clients accept a `transport` argument, so tests replace the network with `httpx.MockTransport` rather than patching request functions. `TiingoClient(settings, budget=..., transport=..., sleep=...)` in `src/trading_platform/services/tiingo.py` is built this way. `tests/test_tiingo_client.py` uses it:
```python
client = TiingoClient(
    settings,
    budget=UnlimitedRequestBudget(),
    transport=httpx.MockTransport(handler),
    sleep=lambda s: None,
)
```
Pass `sleep=lambda s: None` so retry back-off costs no time. `UnlimitedRequestBudget` is used in `tests/test_tiingo_client.py`; check its definition before reusing it elsewhere.

**Which httpx module to import in a test.** Two packages are installed:
- `httpx` (0.28; declared in `pyproject.toml`) backs the Tiingo, Alpaca, Polygon, and catalog clients in `src/`. Sixteen test-tree files import it: 15 test modules and `tests/support/scripted_broker.py`.
- `httpx2` (2.x) is a transitive dependency of the official `anthropic` SDK, which requires `httpx2<3,>=2.0.0`. `tests/test_research_assistant.py` imports `import httpx2 as httpx` so that the `MockTransport` it builds is the one the SDK accepts. Use `httpx2` only when the SDK under test is `anthropic`. `httpx2` is not declared in `pyproject.toml`, so do not import it from production code.

**Provider fakes for the study assistant.** `tests/test_research_study_assistant.py` reuses `StubProvider` through a cross-module import: `from tests.test_research_assistant import StubProvider, ok`. This works, but it couples two test modules. If a third module needs the stub, move `StubProvider` and `ok` into `tests/support/`.

**Live providers:** no test in the modules read calls a live provider. The SDK path is exercised only through `httpx2.MockTransport`. The four live assistant requests recorded in the planning notes were manual and are not tests. Any new test must stub the provider.

**Credentials in tests:** the research test modules set fake AI and provider settings through a per-module `_configure(monkeypatch, ...)` helper, using `monkeypatch.setenv` and a placeholder key value. Do not put a real or realistic-looking key in a test, and do not read `.env`.

**Clock and time:** pass explicit `as_of` dates and sessions into services (for example `FakeBarStore.bars_for_sessions(..., as_of=...)`, or `clock_at(...)` from `tests/support/calendar_facts.py`). Avoid `datetime.now()` in code a test must pin.

### Python: what to mock and what to run for real

**Mock or stub:**
- External providers: Tiingo, Polygon, Alpaca, Anthropic. Use the duck-typed fake or `httpx.MockTransport` (`httpx2` for the SDK).
- The Tiingo request budget. Use `UnlimitedRequestBudget` when a test is not about budgeting. Use `DatabaseRequestBudget` against a migrated database when it is.
- Sleeps and wall-clock delays (`sleep=lambda s: None`, `StubProvider.delay`).
- Settings that the test does not examine (`monkeypatch.setenv` plus `clear_settings_cache()`).

**Run for real:**
- PostgreSQL. Research, reconciliation, and job tests run real migrations (`migrated_database`) against real constraints and triggers. Constraint and append-only checks are part of the behaviour under test; `test_migration_head_tables_columns_and_append_only_trigger` in `tests/test_research_data_pipeline.py` is one example.
- The FastAPI app. API tests use `fastapi.testclient.TestClient` (45 files), usually with `migrated_database`.
- The exchange calendar (`trading_platform.services.calendar`, backed by `exchange-calendars`) for session logic.

## Coverage

**Requirements:** None enforced.

**View Coverage:** Not configured. Neither `pyproject.toml` nor `console/vitest.config.ts` defines a coverage provider or threshold.

## Test Types

**Unit tests:** pure functions and classes, for example `evaluate_output`, `render_spec_yaml`, and `ResearchAssistantService` driven by a `StubProvider`.

**Integration tests:** service tests that take the `research_db` fixture, and API tests that use `TestClient` with `migrated_database`. `tests/test_research_api_isolation.py` builds the research app and asserts which routes exist.

**End-to-end tests:** the modules named `*_e2e.py`, for example `tests/test_job_mutation_e2e.py`, `tests/test_account_jobs_e2e.py`, and `tests/test_g1_real_reconciliation_release_e2e.py`. These are trading-era tests and are slower than the research suite.

**Architecture and boundary tests** are static scans written as ordinary tests. Keep the pattern when adding a source-tree rule:
- `tests/test_log_enforcement.py`: an AST scan that bans direct `logging.getLogger(...)` calls in the 12 listed modules. It also drives a real emission through the production formatter and checks the serialized JSON for leaked secrets.
- `tests/test_job_import_boundary.py`: no module under `src/trading_platform/services/` imports the Job framework, HTTP, or a scheduler. The file list comes from a glob, so new service modules are covered automatically.
- `tests/test_orchestration_boundaries.py` and `tests/test_read_path_purity.py`: further boundary and purity scans.
- `tests/test_research_api_isolation.py`: in research mode the app serves only research and infrastructure routes, and no trading route exists.
- `tests/test_research_assistant.py::test_output_schema_meets_provider_constraints`: asserts that the structured-output schema meets provider limits. The rules it enforces are no recursion, no numeric or string bounds, `additionalProperties: false` on every object, every property listed as required, and a capped number of union types.
- Console: `console/src/lib/consoleBoundaries.test.ts` walks `console/src` with `node:fs` for boundary rules. `console/src/lib/consoleMutationInventory.test.ts` pins two sets of endpoint literals: the five trading mutations passed to `postJson`/`putJson`, and the research mutations passed to `researchMutation`. It also checks that nothing outside `lib/api.ts` calls a mutation helper. `console/src/lib/consoleRouteInventory.test.ts` covers routes.

**Browser end-to-end:** not part of the repository suite. Browser runs are manual and recorded in planning notes.

## Common Patterns

### Python: concurrency

- **Threads** (in-process races): `tests/test_research_assistant.py` starts `threading.Thread` workers (around line 604) that call `service.propose(...)` concurrently, and asserts the outcome. `StubProvider` takes a `threading.Lock` so its script is consumed safely across threads.
- **Processes** (cross-process invariants): `tests/test_research_budget.py` runs `admit_many` from `tests/support/budget_worker.py` through a multiprocessing pool (`pool.starmap(admit_many, [...])`). Each worker process builds its own engine and `DatabaseRequestBudget`, so the test proves the advisory-lock admission is atomic across processes. Threads cannot prove this; use a process pool for cross-process claims.
- **Event loop:** the assistant route is `async` (`await request.body()` in `src/trading_platform/api/research/studies.py` and the assistant router). A test around line 840 of `tests/test_research_assistant.py` checks that two concurrent requests are not serialized by the event loop. Route tests otherwise use the synchronous `TestClient`.

### Python: error testing

Assert the closed code and the status, not the message text. Typed exceptions carry a `code` attribute (the assistant's codes are the `ai_*` family, for example `ai_daily_limit_reached` and `ai_revision_limit_reached`, in `src/trading_platform/services/research/assistant.py`):
```python
with pytest.raises(AssistantError) as info:
    service.propose(user_text="...")   # illustrative; the call that raises depends on the scenario
assert info.value.code == "ai_daily_limit_reached"
```

At the API boundary, assert the status and the `{"detail": {"code": ...}}` body:
```python
response = client.post(f"/api/v1/research/studies/{study_id}/revisions", json=body)
assert response.status_code == 422
assert response.json()["detail"]["code"] == "invalid_request"
```
Parametrize over every code in a family, as `test_closed_failure_codes` does in `tests/test_tiingo_client.py`.

### Python: query budgets

`count_queries(bind)` (in `tests/support/query_counter.py`) counts every SELECT, INSERT, UPDATE, and DELETE on the engine while the block runs. Use it to pin a query budget, not to snapshot SQL:
```python
from tests.support.query_counter import count_queries

with count_queries(engine) as counter:
    service.preflight(...)
assert counter.count <= 10
```
`counter.count` is the field on the `QueryCounter` dataclass. The bind argument may be an engine or a connection.

### Console: component tests with fetch stubs

Stub the network with `vi.stubGlobal("fetch", ...)` and restore it in `afterEach`. `vi.stubGlobal("fetch", ...)` is the most common stub in the console suite:
```tsx
// console/src/components/research/research.test.tsx
// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ResultsView } from "./ResultsView";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
```
Type the fixtures against the contract types in `console/src/lib/research/types.ts` (for example `const METRICS: Metrics = {...}` in `research.test.tsx`).

Mock a hook module when a test is about rendering rather than data loading:
```tsx
vi.mock("@/lib/useApiQuery", () => ({ useApiQuery: query }));
vi.mock("@/lib/useMutationCapability", () => ({
  useMutationCapability: () => ({ state: "enabled", reason: null, loading: false, catalog: null }),
}));
```
Mock Next.js navigation when a component reads the route:
```tsx
vi.mock("next/navigation", () => ({ usePathname: () => "/research/strategies/new" }));
```
Mock a heavy child by its module path, for example `vi.mock("./CurveChart", ...)` in the research tests.

Stub `crypto` with `vi.stubGlobal("crypto", {...})` (15 occurrences) when a test must control generated ids. `console/src/lib/idempotencyKey.ts` prefers `crypto.randomUUID()` and falls back to other sources.

### Console: pure logic tests

Logic modules are tested in the default `node` environment with no DOM and no mocks: `console/src/lib/research/preflight.test.ts`, `console/src/lib/research/windows.test.ts`, `console/src/lib/jobOutcome.test.ts`, `console/src/lib/idempotencyKey.test.ts`. When a rule can be a pure function, move it out of the component into `console/src/lib/` first, then test it there.
```ts
// console/src/lib/research/windows.test.ts (node environment, no docblock)
import { describe, expect, it } from "vitest";
import { computeWindows, DEFAULT_SPLIT } from "./windows";

describe("computeWindows", () => {
  it("derives the preset windows from the split", () => {
    // arrange, act, expect against the exported function
  });
});
```

### Console: hook tests

Hook tests render a small harness component and use `render` with `waitFor`. See `console/src/lib/useApiQuery.test.tsx` and `console/src/lib/useActivePaperStrategy.test.tsx`.

### Console: source-scan tests

`console/src/lib/consoleMutationInventory.test.ts` pins two endpoint sets in `console/src/lib/api.ts`. The trading set is the five `postJson`/`putJson` literals: `POST /api/v1/jobs`, `POST /api/v1/jobs/{id}/cancel`, `POST /api/v1/jobs/{id}/retry`, `PUT /api/v1/controls/kill-switch`, and `PUT /api/v1/controls/strategies/{id}`. The research set is the literals passed to `researchMutation<T>("POST" | "PUT" | "DELETE", endpoint)`. A third test fails if any file outside `lib/api.ts` calls a mutation helper.

To add a research write, add a `researchMutation` call in `console/src/lib/api.ts`, then add its normalised endpoint to the expected list of the research test in the same file. The test fails until both agree. Adding a trading write changes the frozen surface and breaks the pinned list by design.

## Known Baseline Failures

These are recorded runs. Nothing in this section was re-run for this analysis.

### Python full suite

- **Last full-suite run on a research tree:** `final8_pytest.txt`, 2026-10-08 01:16, before S5. The run recorded 3662 passed, 2 failed, 1 error (`.planning/research/strategy-research-pivot/01-IMPLEMENTATION-PLAN.md` line 408; README "Testing").
- **No full-suite run is recorded on the final post-S5 tree (HEAD `73a70e7`).** Do not claim the suite is green.
- **The two failures** are pre-pivot baseline tests. Both assert `10 == 3` on `universe_size`:
  - `tests/test_api_reads.py::test_strategy_analytics_and_run_reads_match_shared_services`
  - `tests/test_app_boot.py::test_app_bootstrap_serves_foundation_endpoints`
- **The error** is a teardown-only `InsufficientPrivilege` on `DROP DATABASE ... WITH (FORCE)` in `tests/test_alpaca_execution.py`. The test body passes. The error leaves `alpaca_execution_*` databases on the local server. The planning notes classify it as an environment privilege issue present since 2026-10-05.
- **Intermittent history:** `test_create_research_db_script_creates_migrates_refuses_trading_name` (`tests/test_research_data_pipeline.py`) failed once in a 20-file slice on 2026-10-07 and passed on three reruns. Its teardown had terminated backends without the `usename = current_user` filter, which is now applied in `tests/support/migrated_db.py`. Treat a recurrence as a new issue.
- **`make test`** includes `tests/test_app_boot.py`, so it includes one baseline failure. It is not evidence of suite health.

When verifying a change, run the affected files rather than the full suite. Compare any failure with the list above before investigating it.

### Console

- At measurement on 2026-11-01, `npx vitest run` reported **51 test files and 480 tests, all passed** (about 7 s). `npx tsc --noEmit` and `npx eslint` were clean.
- This was measured against a working tree with uncommitted console edits, not against `HEAD`. The README's "431 vitest tests" is a figure from the S5 milestone and is out of date. The HEAD commit body (`73a70e7`) records vitest at 47 files and 469 tests when that commit was made; the working tree differs from that by 4 files and 11 tests.
- No console baseline failures are known.

## Writing a New Test: Checklist

1. Python: add the test to the flat `tests/` directory as `test_<area>.py`, or to the existing module for that area. Console: add `<name>.test.ts(x)` beside the module.
2. For a database-backed Python test, take a `research_db`-style fixture built on `migrated_database`. Do not copy a `migrated_*_db` fixture from another module.
3. For a provider, pass a fake with the production duck type (`StubProvider`, `StubTiingoClient`) or an `httpx.MockTransport` through the client's `transport=` argument. Pass `sleep=lambda s: None` for retry logic.
4. Assert typed codes and response shapes (`{"detail": {"code": ...}}`), and parametrize over every code in a family.
5. For a DOM test, add `// @vitest-environment jsdom` and `afterEach(() => { cleanup(); vi.unstubAllGlobals(); })`.
6. Run only the affected files first (`PYTHONPATH=src .venv/bin/pytest <file> -q`, `npx vitest run <path>`), then `.venv/bin/ruff check` on changed Python files, and `npx tsc --noEmit` and `npx eslint` for the console.

---

*Testing analysis: 2026-11-01*
