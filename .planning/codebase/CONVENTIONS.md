# Coding Conventions

**Analysis Date:** 2026-11-01

Scope: the whole repository at git HEAD `73a70e7`, with uncommitted console edits in the working tree. The active product is the research app (`src/trading_platform/api/research/`, `src/trading_platform/services/research/`, `console/src/components/research/`). The frozen trading surface follows the same conventions, but it is not the default target for new work.

## Naming Patterns

**Python files and modules:**
- `snake_case.py` throughout `src/trading_platform/` and `tests/`. Example: `src/trading_platform/services/research/study_assistant.py`, `src/trading_platform/services/research/tiingo_ingestion.py`.
- Packages carry a domain name, not a layer name: `services/research/`, `api/research/`, `jobs/handlers/`, `db/models/`, `strategies/spec/`.
- Test modules are flat in `tests/` and named `test_<area>.py`, for example `tests/test_research_assistant.py`, `tests/test_research_data_pipeline.py`, `tests/test_tiingo_client.py`. Older trading-era files carry phase numbers (`tests/test_phase20_1_e1_guard_windows.py`). Do not put phase numbers in new file names; name new files after the domain they cover.
- Shared test helpers live in `tests/support/` (for example `tests/support/migrated_db.py`, `tests/support/research_fixtures.py`, `tests/support/query_counter.py`).

**Python functions and variables:**
- `snake_case` for functions, methods, and variables. Example: `def run_research_backtest(...)` in `src/trading_platform/services/research/backtest.py`.
- Private helpers have a single leading underscore: `_error`, `_translate`, `_as_of`, `_read_body` in `src/trading_platform/api/research/studies.py`; `_coverage_checks` in `src/trading_platform/services/research/studies.py`.
- Module-level constants are `UPPER_SNAKE_CASE`: `HOUR`, `DAY`, `OUTCOME_OK`, `PURPOSE_PRICES` in `src/trading_platform/services/research/budget.py`; `OUTPUT_SCHEMA`, `SYSTEM_PROMPT` in `src/trading_platform/services/research/assistant.py`.
- Public router objects end in `_router`: `studies_router` and `revisions_router` in `src/trading_platform/api/research/studies.py`.

**Python classes and types:**
- `PascalCase` for classes. Exceptions end in `Error` (`BudgetExhaustedError`, `DraftNotFoundError`), or in `Failure` for provider-call outcomes (`ProviderFailure` in `src/trading_platform/services/research/assistant.py`).
- Closed codes are carried either as a class attribute (`code = "study_not_found"`) or as an instance attribute. See Error Handling below.
- Value objects are `@dataclass(frozen=True)`: 167 occurrences in `src/`. Example: `Admission` in `src/trading_platform/services/research/budget.py`.
- Request, response, and settings models are Pydantic `BaseModel` (84 occurrences). Examples: `SubmitJobRequest` in `src/trading_platform/api/routes/jobs.py`, `DatabaseSettings` in `src/trading_platform/core/settings.py`.
- Structural interfaces use `typing.Protocol`: `RequestBudget` in `src/trading_platform/services/research/budget.py`.
- Closed string sets use `Literal[...]` (`Literal["local", "test", ...]` in `src/trading_platform/core/settings.py`) or `StrEnum` (47 files under `src/` reference `StrEnum`; example `PriceFailure` in `src/trading_platform/services/alpaca.py`).

**Console (TypeScript/React) files:**
- React components: `PascalCase.tsx`, one exported component per file, grouped by feature under `console/src/components/<area>/`. Examples: `console/src/components/research/StudyWizard.tsx`, `console/src/components/research/ValidationPanel.tsx`.
- Hooks: `useCamelCase.ts` in `console/src/lib/`. Examples: `console/src/lib/useApiQuery.ts`, `console/src/lib/useStudyPreflight.ts`.
- Pure logic and API helpers: `camelCase.ts` in `console/src/lib/` or `console/src/lib/research/`. Examples: `console/src/lib/research/windows.ts`, `console/src/lib/research/preflight.ts`, `console/src/lib/idempotencyKey.ts`.
- Research contract types live in `console/src/lib/research/types.ts` (`Metrics`, `Readiness`, `Candidate`, and others).
- Next.js route files are lowercase `page.tsx` and `layout.tsx` under `console/src/app/`, with dynamic segments in brackets (`console/src/app/research/studies/[studyId]/page.tsx`).

**Console functions and variables:**
- `camelCase` for functions and variables. Fixed tables and defaults are `UPPER_SNAKE_CASE`: `EMPTY_WIZARD`, `DEFAULT_SPLIT` (`console/src/components/research/StudyWizard.tsx`, `console/src/lib/research/windows.ts`), `MUTATION_ERROR_COPY` (`console/src/lib/api.ts`).
- Contract shapes are `type` aliases. Example: `type VersionsBody = { versions: StrategyVersion[] };` in `console/src/components/research/StudyWizard.tsx`.

## Code Style

**Python version:** `requires-python = ">=3.12"` in `pyproject.toml`, ruff `target-version = "py312"`, and mypy `python_version = "3.12"`. The `.venv` runs Python 3.13.15, so 3.13-only syntax passes locally while violating the declared target. Write code for 3.12.

**Python formatting:**
- Line length is 100 (`[tool.ruff] line-length = 100` in `pyproject.toml`); target is `py312`.
- Formatting is **not** enforced repo-wide. `ruff format --check src tests` reports 170 of 389 files would be rewritten at HEAD. Do not run `ruff format` across the whole tree; it produces unrelated churn.
- The only enforced formatter is the pre-commit `ruff-format` hook, scoped to `src/trading_platform/services/(config|execution|reconciliation)/` and `src/trading_platform/worker/` (`.pre-commit-config.yaml`). Inside that scope, format the files you touch.
- Research modules are mixed: 9 of the 27 modules under `services/research/` and `api/research/` pass `ruff format --check`; 18 do not. Keep the local style of the file you edit rather than reformatting it. Long literal dicts and calls on one line are common in `src/trading_platform/services/research/budget.py` and `src/trading_platform/api/research/studies.py`.

**Python linting:**
- Tool: `ruff check`, configured in `pyproject.toml` under `[tool.ruff.lint]`.
- Rule families: `select = ["E", "F", "I", "W"]`. `E501` (line length) is **ignored** on purpose (`ignore = ["E501"]`), so long lines are not a lint failure.
- Per-file ignores: `"tests/*" = ["F811"]` (the pytest fixture-as-parameter idiom); `"alembic/versions/*" = ["I001", "F401", "F821", "F841"]` (historical migrations are immutable).
- Gate: `.venv/bin/ruff check src tests` passes at HEAD. Run it on the files you change.
- Pre-commit pins `ruff` at `v0.15.21` (`.pre-commit-config.yaml`); `.venv` has `ruff 0.16.8`. Use the venv binary for local checks and expect the version to differ from the pin.

**Python type checking:**
- Tool: `mypy`, configured in `[tool.mypy]` in `pyproject.toml`: `python_version = "3.12"`, `mypy_path = "src"`, `warn_unused_ignores = true`, `no_implicit_optional = true`.
- Scope is **three packages only**: `src/trading_platform/services/execution`, `src/trading_platform/services/reconciliation`, `src/trading_platform/services/config` (26 source files; `.venv/bin/mypy` passes at HEAD).
- Research modules are **not** type-checked by mypy. Annotate them to the same standard anyway, but do not expect mypy to catch regressions there.
- Pre-commit runs a local mypy hook over the same three packages (`.pre-commit-config.yaml`, entry `.venv/bin/mypy`).

**Console formatting and linting:**
- ESLint 9 flat config in `console/eslint.config.mjs`: `eslint-config-next/core-web-vitals` plus `eslint-config-next/typescript`. Clean at HEAD (`cd console && npx eslint` prints nothing).
- TypeScript: `console/tsconfig.json` with `"strict": true`, `"noEmit": true`, `"moduleResolution": "bundler"`, `"jsx": "react-jsx"`, `"isolatedModules": true`, and the path alias `"@/*": ["./src/*"]`. Clean at HEAD (`npx tsc --noEmit`).
- No Prettier configuration exists under `console/`. Match the surrounding file. The research components use 2-space indentation, double quotes, and semicolons, and wrap JSX attributes across lines.
- Tailwind CSS v4 through `console/postcss.config.mjs`; global styles in `console/src/app/globals.css`.

## Import Organization

**Python order** (enforced by ruff `I` rules):
1. Standard library (`import json`, `from dataclasses import dataclass`).
2. Third-party (`fastapi`, `pydantic`, `sqlalchemy`, `psycopg`, `anthropic`, `httpx`).
3. First-party `trading_platform` (the only module in `known-first-party`).

`tests` is **not** first-party in the ruff config. Imports of `tests.support...` therefore sort into the third-party block, next to `fastapi` and `pytest`. Example from `tests/test_research_assistant.py`:
```python
import httpx2 as httpx
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from tests.support.migrated_db import migrated_database

import trading_platform.api.app as api_app
```

Source and test files begin with `from __future__ import annotations`, placed after the module docstring. 371 files under `src/` and `tests/` use it.

**Console order** (conventional, not lint-enforced):
1. `"use client";` first, when the module uses hooks or browser APIs (61 files under `console/src/components/`; 9 route files under `console/src/app/`; 5 files under `console/src/lib/`).
2. Third-party (`react`, `@testing-library/react`, `vitest`).
3. Alias imports `@/...` (`@/lib/api`, `@/lib/research/types`).
4. Relative sibling imports (`./AssetPicker`, `./ResearchPeriod`).

Example, `console/src/components/research/StudyWizard.tsx`:
```tsx
"use client";

import { useState } from "react";
import { createRevision, createStudy } from "@/lib/api";
import { useApiQuery } from "@/lib/useApiQuery";
import { ErrorState } from "@/components/ErrorState";
import type { AssetList, CatalogCoverage, Revision, StrategyVersion, Study, StudySettings } from "@/lib/research/types";
import { AssetPicker } from "./AssetPicker";
```

**Path aliases:**
- Python: `src/` on `sys.path` (`PYTHONPATH=src`, or `mypy_path = "src"`). Import as `trading_platform.<pkg>...`. Tests import `tests.support` from the repo root; `tests/conftest.py` inserts the repo root and `src/` into `sys.path`.
- Console: `@/*` maps to `console/src/*` (`console/tsconfig.json` `paths`, mirrored in `console/vitest.config.ts`).

## Error Handling

### Python: typed exceptions with closed codes

The research layer defines typed exceptions whose `code` is a closed, documented string, and the HTTP layer translates them. Three styles exist at HEAD. Use style 1 for new HTTP-facing errors.

1. **Class-attribute `code` and `status` (preferred).** `StudyError` carries both as class attributes and takes a free-form `detail` dict:
   ```python
   # src/trading_platform/services/research/studies.py
   class StudyError(Exception):
       code = "study_error"
       status = 409

       def __init__(self, message: str = "", **detail: Any) -> None:
           self.detail = detail
           super().__init__(message or self.code)


   class StudyNotFoundError(StudyError):
       code = "study_not_found"
       status = 404
   ```
   The router maps `exc.status`, `exc.code`, and `exc.detail` in one place: `_translate` in `src/trading_platform/api/research/studies.py`.

2. **Class-attribute `code` with a typed `__init__`; status chosen by the router.** `ResearchStrategyError` and its subclasses in `src/trading_platform/services/research/strategies.py` set `code` on the class and take typed constructor arguments (`draft_id`, `field`, `reason`, `errors`). The router picks the HTTP status: `_translate` in `src/trading_platform/api/research/strategies.py` maps `DraftNotFoundError` to 404 and `DraftInvalidError` to 422. `BudgetExhaustedError` in `src/trading_platform/services/research/budget.py` follows the same shape.

3. **Instance-level `code`.** `AssistantError` in `src/trading_platform/services/research/assistant.py` takes `code` as a constructor argument, so one class covers many closed codes:
   ```python
   class AssistantError(Exception):
       """Refusal or failure with a closed ``code`` (the console copy keys on it)."""

       def __init__(self, code: str, message: str, **detail: Any) -> None:
           super().__init__(message)
           self.code = code
           self.detail = detail
   ```
   `ProviderFailure` in the same file carries `status_code`, `request_id`, and `provider_message` for diagnostics. Note that `AssistantError` has no HTTP status of its own, so its router must map `code` to a status explicitly.

Other exception families follow the same idea: `IntegrityBlocksFreezeError` and `InputsChangedAfterFreezeError` in `src/trading_platform/services/research/freeze.py`; `ConfigValidationError` in `src/trading_platform/services/config/validation.py`.

**Trading-era exceptions** are plain subclasses of `ValueError`, `LookupError`, `RuntimeError`, or `Exception`, usually without a closed `code`. Examples: `IllegalJobTransition(RuntimeError)` in `src/trading_platform/jobs/lifecycle.py`; `AlpacaClientError(Exception)` in `src/trading_platform/services/alpaca.py`. The built-in base is chosen by the failure class (`LookupError` for not-found, `ValueError` for invalid input). Do not copy this for research code; use style 1.

**Rules for new code:**
- Raise a named subclass with a `code` attribute at any boundary the API or console consumes. Do not raise bare `Exception` or free-text `ValueError` there.
- Do not catch `Exception` broadly in a service. The research routers do `except Exception as exc: raise _translate(exc) from exc` (for example `src/trading_platform/api/research/assistant.py`), and `_translate` re-raises anything it does not recognise. Keep that shape: translate known types, let the rest propagate.
- Chain with `from exc` when re-raising at a boundary.

### Python: input validation

- Research request bodies are validated by hand in `_read_body` (`src/trading_platform/api/research/studies.py`, `src/trading_platform/api/research/strategies.py`). The body must be a JSON object, its keys must be a subset of an allowlist, and required keys must be present. Failures return 422 with `code: "invalid_request"` and a `reason`.
- The generic Job submission uses a Pydantic model with `field_validator` (`SubmitJobRequest` in `src/trading_platform/api/routes/jobs.py`).
- Settings are validated at load time with Pydantic validators (`src/trading_platform/core/settings.py`).

### API error shapes

Two response shapes exist in the codebase. New research endpoints use the first.

**Preferred (research and mutation routes): a dict `detail` with a closed `code`.**
```json
{"detail": {"code": "study_not_found"}}
```
Extra fields are merged into `detail`, for example `{"detail": {"code": "job_not_found", "job_id": "..."}}`. Build these with a small `_error` helper:
```python
# src/trading_platform/api/research/studies.py
def _error(status_code: int, code: str, **detail: Any) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, **detail})
```
The same helper exists in `src/trading_platform/api/routes/jobs.py`, `src/trading_platform/api/routes/controls.py`, `src/trading_platform/api/routes/recovery.py`, `src/trading_platform/api/routes/execution_operations.py`, and `src/trading_platform/api/research/strategies.py`.

**Legacy (read routes on the trading surface): plain string `detail`.** Example: `raise HTTPException(status_code=404, detail=str(exc)) from exc` in `src/trading_platform/api/routes/jobs.py` and `src/trading_platform/api/routes/analytics.py`. Do not copy this for new code; the console cannot map free text to copy.

**Status codes in use:**
- 201 for creates: `JSONResponse(status_code=status.HTTP_201_CREATED, content=...)`.
- 404 for unknown ids: `study_not_found`, `revision_not_found`, `draft_not_found`, `version_not_found`.
- 409 for state conflicts: `job_not_retryable`, and the `StudyError` default.
- 422 for validation and business refusals: `invalid_request`, `draft_invalid`, `invalid_retry_payload`.
- 503 when settings are not loaded: `src/trading_platform/api/routes/health.py`.

Use `status.HTTP_422_UNPROCESSABLE_CONTENT`. The research and job routers use it (48 occurrences under `src/`); the older `HTTP_422_UNPROCESSABLE_ENTITY` name does not appear.

**Envelopes.** Successful research reads carry an `as_of` timestamp and a named payload key: `{"as_of": ..., "count": ..., "studies": [...]}` from `list_studies`, and `{"as_of": ..., "study": ...}` from `get_study`. Keep this envelope for new research reads. Creates return the object under a noun key (`{"revision": ...}`) with status 201.

**Mutation gating.** Mutating research routes declare `dependencies=[Depends(require_mutations_enabled)]` (`src/trading_platform/api/dependencies.py`). Read-only routes do not. Example: `POST /api/v1/research/studies/preflight` in `src/trading_platform/api/research/studies.py` writes nothing and has no gate; its comment says so. The research routers are mounted in their own package (`src/trading_platform/api/research/__init__.py`), and the trading surface's mutating-route allowlist is pinned by tests. Do not add mutating routes under `src/trading_platform/api/routes/`.

### Console: fetch and mutation results

- `fetchApi<T>(endpoint)` in `console/src/lib/api.ts` **never throws**. It resolves to `ApiResult<T>`: `{ ok: true, data, endpoint, asOf }` on success, or `{ ok: false, endpoint, status, message, body?, asOf }` on failure. `status` is `null` when the network or proxy is unreachable. Requests go through the Next.js rewrite `/backend/:path*` (`console/next.config.ts`) to the API.
- Mutations return `MutationResult<T>`. A failed mutation exposes `code`, read from `detail.code`, and a `detail` object:
  ```ts
  // console/src/lib/api.ts
  const code = detail && typeof detail.code === "string" ? detail.code : null;
  ```
- User-facing copy for a mutation code is looked up in `MUTATION_ERROR_COPY` (`console/src/lib/api.ts`) through `mutationErrorMessage(code, detail)`. Unknown or null codes fall back to `Operation failed (<code>). Try again; if it persists, check the API logs.` Add a copy entry whenever a route introduces a code the operator should see.
- `isOutcomeUncertain(...)` decides whether a failed mutation may still have been applied (transport failure, 5xx, or an unreadable 2xx). Show that caveat in the UI instead of a plain failure.
- Components do not call `fetch` directly. Read with `useApiQuery(endpoint, options)` (`console/src/lib/useApiQuery.ts`) and mutate through the typed functions in `console/src/lib/api.ts`. Research writes go through the `researchMutation<T>(method, endpoint, body)` helper in `console/src/lib/api.ts`; `createStudy`, `createRevision`, and `createAssetList` are thin wrappers over it. Trading writes go through the `postJson` and `putJson` helpers in the same file.
- Do not swallow errors in components. Surface `ApiFailure.endpoint` and `message`; the `api.ts` header names this as the CONS-02 rule, which requires the failing endpoint to be named.

## Logging

**Python framework:** the standard library `logging` module with a JSON formatter. `configure_logging(settings)` in `src/trading_platform/core/logging.py` installs it. Every emitted record passes through `sanitize()` (`src/trading_platform/core/log_sanitizer.py`), which masks credentials and full broker order IDs by default.

**Getting a logger:**
- The approved factory is `get_logger(name)` in `src/trading_platform/core/logging.py`.
- A **static AST test enforces it for an explicit list of 12 modules**: `IN_SCOPE_MODULES` in `tests/test_log_enforcement.py`. Those files must not call `logging.getLogger(...)`. The list is `services/execution/submit_orders.py`, `services/alpaca.py`, `services/execution/transition.py`, `services/concurrency_guard.py`, `services/operator_status.py`, `services/operator_controls.py`, `services/reconciliation/report.py`, `worker/__main__.py`, `services/bootstrap.py`, `api/app.py`, `core/startup.py`, and `services/config/validation.py`. A plain `import logging` for level constants is allowed.
- Outside that list the codebase is mixed. 14 files call `get_logger(...)`; 11 call `logging.getLogger(__name__)`, for example `src/trading_platform/services/ingestion.py`, `src/trading_platform/services/polygon.py`, `src/trading_platform/services/calendar.py`, `src/trading_platform/services/symbol_metadata_sync.py`. Both forms are accepted outside the enforced list. Use `get_logger(__name__)` in new modules, and keep `getLogger(__name__)` where a file already uses it.

**Event style:** log a snake_case event name as the message, with structured data passed in `extra={"context": {...}}`. Examples from `src/trading_platform/services/research/catalog.py`:
```python
logger.warning("catalog_nasdaq_names_unavailable", extra={"context": {"error": str(exc)}})
```
`emit_structured_log` in `src/trading_platform/core/logging.py` routes its `context` through `sanitize()`. Prefer it when a call site needs the single sanitization chokepoint explicitly.

**Rules:**
- Never log secrets, API keys, `Authorization` headers, or full broker order IDs. The sanitizer is a backstop; do not rely on it. Do not pass a provider key into a log `context` at all.
- Command-line tools write results to stdout with `print(...)`, for example `src/trading_platform/worker/commands/run_jobs.py` and `src/trading_platform/worker/commands/operator.py`. CLI errors go to `sys.stderr`. Do not use `print` inside services or routers.

**Console:** no logging framework. Surface failures through the `ApiFailure` and `MutationResult` shapes above.

## Comments and Docstrings

**Module docstrings often state invariants.** Many research modules open with a list of the rules the module enforces, sometimes with plan references:
- `src/trading_platform/services/research/studies.py` lists rules such as "a revision is an immutable settings snapshot; a change is a new revision" and "readiness lists every failing item with a closed code".
- `src/trading_platform/services/research/budget.py` explains the atomic admission rule and what is and is not counted.
- `src/trading_platform/api/research/__init__.py` explains why the research routers live outside `api/routes/`.

Write a module docstring like this when a module carries a non-obvious invariant. Name the invariant and, where one exists, the test or constraint that enforces it.

**Plan and phase references:** docstrings and comments cite planning identifiers (`plan S3`, `D-12`, `LOG-06`, `10-06-PLAN.md`). These point at `.planning/` and `.planning/research/`. Keep a reference when it explains a constraint; do not add new ones to explain ordinary code.

**Inline comments explain why:** an ordering, a race guard, or a deliberately excluded behaviour. Example from `src/trading_platform/api/research/studies.py`: the `preflight` route is documented as ungated because it writes nothing.

**JSDoc/TSDoc:** block comments (`/** ... */`) on exported helpers and contract-sensitive functions in the console. Examples: the `fetchApi` block in `console/src/lib/api.ts` states "Never throws"; the `EMPTY_WIZARD` block in `console/src/components/research/StudyWizard.tsx` states the defaults policy (values start empty and are required inputs, never product defaults).

**Do not add** comments that restate the code or describe what changed. Describe the constraint instead.

## Function Design

**Size:** routers stay thin. A route parses and validates the request, calls one service method, and maps errors with `_translate`. Business rules live in `src/trading_platform/services/research/*.py`. Example: `create_study` in `src/trading_platform/api/research/studies.py` is about ten lines.

**Parameters:**
- Use keyword-only arguments for configuration-like values: `def create_study(self, *, name, kind, settings)` style, as in `StudyService` callers.
- Inject collaborators through constructor arguments so tests can replace them: `ResearchAssistantService(load_settings(), provider=provider, process_id=...)` in `src/trading_platform/services/research/assistant.py`, and `TiingoClient(settings, budget=..., transport=..., sleep=...)` in `src/trading_platform/services/tiingo.py`.
- FastAPI dependencies are aliased once per module: `Service = Annotated[StudyService, Depends(get_study_service)]` in `src/trading_platform/api/research/studies.py`.

**Return values:**
- Read routes return `dict[str, Any]`. Routes that set a non-200 status return `JSONResponse`.
- Services return `dict`/`list` payloads for the API, or frozen dataclasses for internal values.

## Module Design

**Exports:** no `__all__` is required. Public names are the non-underscore module-level names. Import the specific symbol: `from trading_platform.services.research.budget import BudgetExhaustedError`.

**Barrel files:** `src/trading_platform/services/research/__init__.py` and `src/trading_platform/api/research/__init__.py` hold package docstrings, not re-export lists. Import from the concrete module.

**Console exports:**
- Components and hooks use **named exports**: 206 named exports across `console/src/components/` and `console/src/lib/`. There are zero default exports under those two directories.
- All 14 default exports in the console sit under `console/src/app/`, where Next.js requires them (`page.tsx` and `layout.tsx` files).
- Keep this split. A new component is `export function StudyWizard(...)`, not `export default`.

**Dependency direction:** domain services never import the Job framework, the HTTP layer, or a scheduler. `tests/test_job_import_boundary.py` enforces this over every module under `src/trading_platform/services/` (the set is discovered by glob, so new modules are covered automatically). Console boundaries are checked by `console/src/lib/consoleBoundaries.test.ts`, which scans `console/src` for source files.

## Configuration

**Python settings:** `pydantic-settings` (`BaseSettings`) in `src/trading_platform/core/settings.py`, with the environment prefix `TRADING_PLATFORM_` and `__` as the nested delimiter. The `Makefile` shows the pattern: `TRADING_PLATFORM_RESEARCH__MODE=true`, `TRADING_PLATFORM_DATABASE__NAME=...`. Typed sections (`DatabaseSettings`, `LoggingSettings`, and others) are plain `BaseModel`s. Load with `load_settings()`; reset the cache with `clear_settings_cache()`.

**Secrets:** read from the environment only. A repo-root `.env` exists for local development; tests never load it (the autouse fixture `isolate_operator_env` in `tests/conftest.py` disables dotenv loading). `.env.example` and `.env.production.example` document the variable names.

**Console configuration:** `console/next.config.ts` (the `/backend/:path*` rewrite). Research versus trading mode is read client-side from the `/health` response by `console/src/lib/useResearchMode.ts` (`ResearchModeState`).

## Commit Messages

Style is Conventional Commits with a scope, a lowercase subject, and an imperative verb. Recent history at HEAD (`git log --oneline -30`):
- `feat(research): preflight coverage, calendar-based end dates and missing-input warnings in the study wizard`
- `feat(research): rework the study wizard with asset tags, research periods and a study assistant`
- `docs(research): describe the Strategy Research product and freeze the trading roadmap`
- `test(20.1-38): label the effect-Job retype case as a scope pin, not an approval`
- `fix(20.1-38): pin the 0029 origin guard and cast guard 4's WHEN (review r5 WR-03, IN-02)`

Types in use: `feat`, `fix`, `test`, `docs`. Scopes are `research` for the current product, or a planning identifier for trading-era phases (`20.1-38`).

Commit bodies are common and detailed (`git log -5 --format='%B---'`). They use a short paragraph on the change, followed by labelled sections such as `Read-only preflight (services/research/studies.py, ...)`, `Wizard (...)`, and `Verification`, with file paths and test counts. Mirror that for research commits: name the affected files and the verification that was run.

Trailers: 47 of the last 50 commit bodies at HEAD carry a `Co-Authored-By: Claude <model> <noreply@anthropic.com>` trailer. The models named are Sonnet 5.5, Opus 5.5, and Fable 5.1. None of the last 50 carries a `Generated with` footer. Apply the attribution the current instructions require for the commit being made.

---

*Convention analysis: 2026-11-01*
