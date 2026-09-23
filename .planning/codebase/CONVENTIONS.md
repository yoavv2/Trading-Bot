# Coding Conventions

**Analysis Date:** 2026-09-23

## Naming Patterns

**Files:**
- **Python:** `snake_case.py` (e.g., `data.py`, `job_reads.py`, `concurrency_guard.py`)
- **TypeScript/React:** `PascalCase.ts` or `PascalCase.tsx` for components (e.g., `ErrorState.tsx`, `FetchMeta.tsx`)
- **Tests:** `test_*.py` (Python) or `*.test.ts(x)` (TypeScript) (e.g., `test_job_cancellation.py`, `api.test.ts`)

**Functions:**
- **Python:** `snake_case` verbs describing action (e.g., `enforce_startup_config()`, `request_cancellation()`, `find_ready_job_ids()`)
- **TypeScript:** `camelCase` with verb prefix (e.g., `fetchApi()`, `useApiQuery()`)

**Variables:**
- **Python:** `snake_case` throughout (e.g., `database_name`, `job_id`, `session_scope`)
- **TypeScript:** `camelCase` for variables and constants (e.g., `statusLabel`, `parsedBody`, `contentType`)

**Types:**
- **Python:** `PascalCase` for class definitions (e.g., `DailyBar`, `IngestionResult`, `MarketDataService`)
- **TypeScript:** `PascalCase` for type names (e.g., `ApiResult<T>`, `ApiSuccess<T>`, `ApiFailure`), using `type` keyword for discriminated unions

## Code Style

**Formatting:**
- **Python:** Ruff (linter and formatter) with target Python 3.12, line length 100
- **TypeScript:** ESLint with Next.js core web vitals config, TypeScript strict mode enabled
- No automatic formatting hook for TypeScript/ESLint in pre-commit (only Python has ruff-format via pre-commit scoped to Phase-12 services)

**Linting:**
- **Python (Ruff):**
  - Enabled rules: E (pycodestyle errors), F (Pyflakes), I (isort import sorting), W (pycodestyle warnings)
  - Ignore: E501 (line-too-long) — pre-existing long lines in comments/strings/assertions are exempt
  - Per-file ignores: F811 (redefinition) disabled in `tests/*` to allow pytest fixture reuse
  - First-party module: `trading_platform`
- **TypeScript/Next.js:**
  - ESLint with `eslint-config-next/core-web-vitals` and `eslint-config-next/typescript`
  - Strict TypeScript compiler: `noEmit: true`, `strict: true`, `isolatedModules: true`

**Import Organization:**
1. Python: `from __future__ import annotations` (always first)
2. Standard library imports (datetime, os, sys, re, etc.)
3. Third-party imports (fastapi, pydantic, sqlalchemy, etc.)
4. Local imports from `trading_platform` module
5. TypeScript: Standard lib → third-party (next, react) → local imports via path aliases (`@/*`)

**Path Aliases:**
- **TypeScript:** `@/*` maps to `console/src/*` (see `console/tsconfig.json`)

## Error Handling

**Patterns:**
- **Python:** Custom exceptions inheriting from base exception types
  - `ValueError` for invalid input/state (e.g., `InvalidIdempotencyKeyError`, `UnknownJobTypeForSubmissionError`)
  - `RuntimeError` for operation failures (e.g., `ConcurrentRunLockedError`, `JobNotCancellableError`)
  - `KeyError`/`LookupError` for lookup failures (e.g., `UnknownStrategyError`, `UnknownJobTypeError`)
  - Domain-specific errors as subclasses (e.g., `PolygonAuthError(PolygonClientError)`)
  - Never rely on implicit error propagation; define custom exceptions per module
- **TypeScript:** Explicit result types using discriminated unions
  - `ApiResult<T> = ApiSuccess<T> | ApiFailure` with `ok` boolean flag
  - Always return typed results; never throw from async API functions
  - Include `endpoint` in both success and failure results for debugging (CONS-02 requirement)
  - Status is `null` when network/proxy is unreachable, numeric for HTTP errors

## Logging

**Framework:**
- **Python:** Standard library `logging` with custom `JsonLogFormatter` (see `core/logging.py`)
- **TypeScript:** `console` object (no structured logging; frontend is read-only)

**Patterns:**
- **Python:**
  - All logs emitted as JSON with `timestamp`, `level`, `logger`, `message`, and optional `context` dict
  - Use `get_logger("module.name")` to get a pre-configured logger
  - Call `emit_structured_log(logger, level, message, context={...})` for structured context, not raw `logger.info()`
  - Sensitive values (API keys, passwords, order IDs) are automatically redacted via `sanitize()` (LOG-06)
  - Context dict passed through `sanitize()` before JSON emission
- **TypeScript:**
  - React components use `console.error()` for errors
  - Console errors include endpoint and status information

## Comments

**When to Comment:**
- Document intent, not obvious code: explain WHY, not WHAT
- Link to phase/ticket when a comment references a deferral or known issue (e.g., "Deferred to Phase 2" or "CFG-05")
- Use comments for complex state-machine logic or non-obvious control flow
- Avoid line-by-line narration of self-documenting code

**JSDoc/TSDoc:**
- **Python:** Module docstrings at the top of every file (e.g., `"""Market-data service contracts and typed request/response models."""`)
  - Class and function docstrings for public APIs
  - Use `"""..."""` for docstrings
  - Reference related functions/classes in docstrings
- **TypeScript:**
  - Block comment `/** ... */` for function/component documentation
  - Describe props, return type, and behavior
  - Example: `/** Fetches \`endpoint\` (un-prefixed FastAPI path) ... Never throws — every code path resolves to an ApiResult. */`

## Function Design

**Size:**
- **Python:** Prefer smaller functions (< 50 lines) that do one thing; break up complex operations into steps
- **TypeScript:** React components can be longer (up to 100 lines) but aim for composability via sub-components

**Parameters:**
- **Python:**
  - Use dataclass/named tuples for multi-param request objects (e.g., `DailyBarRequest`)
  - Keyword-only arguments after complex logic (use `*` separator)
  - Type hints on all parameters
- **TypeScript:**
  - React components: destructure props with `type ComponentProps`
  - Avoid spread operator on unknown objects; prefer explicit prop typing

**Return Values:**
- **Python:**
  - Functions return domain objects (e.g., `DailyBar`, `IngestionResult`) or raise custom exceptions
  - Use properties/computed attributes for derived values (e.g., `IngestionResult.failed_count`)
- **TypeScript:**
  - Async functions return `Promise<ApiResult<T>>` for API calls
  - UI functions return JSX elements
  - Hooks return typed state tuples or objects

## Module Design

**Exports:**
- **Python:**
  - Module `__init__.py` may list `__all__` for public API
  - Import patterns: `from module import ClassName, function_name`
  - Modules are organized by domain (services, jobs, api, db, core)
- **TypeScript:**
  - Named exports for utility functions (e.g., `export async function fetchApi<T>()`)
  - Default exports for React components
  - Type exports: `export type ApiResult<T> = ...`

**Barrel Files:**
- **Python:** Not commonly used; prefer direct imports from specific modules
- **TypeScript:** Not used; import directly from source files

## Type System

**Python:**
- **Type hints required:** All function parameters and return types must have type hints
- **Python 3.12 features:** Use `|` for unions (e.g., `str | None`) instead of `Optional[str]`
- **Frozen dataclasses:** Use `@dataclass(frozen=True)` for immutable value objects (e.g., `DailyBarRequest`)
- **Generic types:** Use `list[str]` (built-in generics) instead of `List[str]`

**TypeScript:**
- **Strict mode:** Always enabled; no `any` type without explicit `// @ts-ignore` comment and justification
- **Discriminated unions:** Use boolean or literal fields to discriminate (e.g., `ok: true | false`)
- **Type guards:** Use `if (result.ok) { ... }` to narrow types
- **Generics:** Use for API response types (e.g., `ApiResult<T>`)

## Dataclass and Record Patterns

**Python:**
- Use `@dataclass` for request/response models (e.g., `DailyBarRequest`, `DailyBar`)
- Mark request/response objects as `frozen=True` when they represent immutable domain values
- Use `field(default_factory=...)` for list/dict defaults in mutable dataclasses
- Include docstring on class explaining its purpose

**TypeScript:**
- Use `type` for prop definitions (e.g., `type ErrorStateProps = { failure: ApiFailure; title?: string }`)
- Destructure props in component signature: `export function ErrorState({ failure, title }: ErrorStateProps)`

---

*Convention analysis: 2026-09-23*
