---
phase: 17-job-framework
plan: 02
subsystem: infra
tags: [protocol, registry, ast-enforcement, python]

# Dependency graph
requires:
  - phase: 17-01
    provides: "Job/JobDependency/JobEvent/JobLog ORM models and the closed JobStatus/JobFailureReason/JobCancellationCause vocabulary"
provides:
  - "Frozen JobContext Protocol (progress/log/cancellation surface, D-08/D-11/D-13) and JobHandler Protocol (registry-resolvable run() contract, JOB-04 boundary documented)"
  - "JobCancelledError, the cooperative-cancellation signal carried by JobContext.raise_if_cancelled()"
  - "JobRegistry (register/resolve/list_job_types) with UnknownJobTypeError and duplicate-registration ValueError; build_default_registry() returns an empty registry in Phase 17"
  - "JOB-03 registry-extensibility enforcement test with a non-emptiable 6-entry queue-framework module list"
  - "JOB-04 reverse import-boundary enforcement test: auto-scoping AST scan over all 33 src/trading_platform/services/ modules, non-emptiable >=30 scope guard"
affects: [17-03, 17-04, 17-05, 17-06, 17-07, 17-08, 17-09]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "@runtime_checkable Protocol for handler contracts (JobContext, JobHandler) instead of ABC, mirroring no prior local precedent but consistent with the codebase's frozen-dataclass error style"
    - "AST-walk-based reverse import-boundary test (services must not import up into jobs/api/worker/fastapi/apscheduler/celery), inverting the direction of tests/test_log_enforcement.py's forward AST-walk technique"
    - "Relative-import resolution in AST scans (level>0 ImportFrom resolved against the file's __package__ dotted name) so a relative import cannot evade a forbidden-import-root check"

key-files:
  created:
    - src/trading_platform/jobs/__init__.py
    - src/trading_platform/jobs/contracts.py
    - src/trading_platform/jobs/registry.py
    - tests/test_job_registry.py
    - tests/test_job_import_boundary.py
  modified: []

key-decisions:
  - "jobs/__init__.py contains zero imports/re-exports (verified by the plan's own grep acceptance criterion) so every later Phase 17 plan adding a sibling module never touches this shared file, eliminating a merge-conflict hotspot across the phase's parallel plans"
  - "test_adding_a_job_type_touches_zero_queue_framework_modules skips AST-scanning any QUEUE_FRAMEWORK_MODULES file that does not yet exist (created by 17-03..17-07), while pinning the module list itself at exactly 6 entries so the scope cannot be silently emptied"
  - "SERVICE_MODULES glob is auto-scoping (rglob under services/, no hardcoded list) so JOB-04 enforcement grows automatically as services are added; a >=30 floor (33 modules exist today) guards against a broken glob turning the parametrized test into a no-op"

requirements-completed: [JOB-03, JOB-04]

# Metrics
duration: ~15min
completed: 2026-07-19
---

# Phase 17 Plan 02: Job Handler Contract and Registry Summary

**Frozen `JobContext`/`JobHandler` Protocols plus an in-memory `JobRegistry`, with JOB-03 (registry extensibility) and JOB-04 (reverse import-boundary) each proven by a non-emptiable-scope enforcement test.**

## Performance

- **Duration:** ~15 min
- **Tasks:** 3 completed
- **Files modified:** 5 (all created)

## Accomplishments
- `trading_platform.jobs.contracts` defines `JobContext` (`@runtime_checkable` Protocol exposing `job_id`/`job_type`/`payload`, `report_progress(...)` for D-11, `log(...)` for D-13, and `is_cancellation_requested()`/`raise_if_cancelled()` for D-08) and `JobHandler` (`job_type` + `run(context) -> Mapping[str, Any]`, with a docstring stating the JOB-04 service-only import boundary and the no-direct-lifecycle-writes rule). `JobCancelledError` carries the cancelled `job_id`.
- `trading_platform.jobs.registry` mirrors `strategies/registry.py` exactly in shape: `JobRegistry.register`/`resolve`/`list_job_types`/`__contains__`, `UnknownJobTypeError(KeyError)` frozen dataclass, duplicate-registration `ValueError`. `build_default_registry()` returns an empty registry and documents that Phase 19 registers concrete handlers here with zero queue-framework-module edits.
- `tests/test_job_registry.py` (6 tests): register/resolve/list, duplicate rejection, unknown-type typed error, empty default registry, and the JOB-03 enforcement test itself — a static AST scan proving no queue-framework module (of a frozen 6-entry list: `queue.py`, `runner.py`, `lifecycle.py`, `dependencies.py`, `cancellation.py`, `context.py`) references a concrete job-type string literal, paired with a dynamic proof that registering and resolving a brand-new handler at runtime needs nothing from those modules.
- `tests/test_job_import_boundary.py` (35 tests): an auto-scoping AST walk over every one of the 33 modules under `src/trading_platform/services/`, asserting none imports `trading_platform.jobs`/`trading_platform.api`/`trading_platform.worker`/`fastapi`/`starlette`/`apscheduler`/`celery` — including relative imports, resolved to absolute dotted names before the forbidden-root check so a relative import cannot evade it. Failure messages name the offending file, import, and line number.
- Manually verified the JOB-04 failure mode: temporarily added `import trading_platform.jobs.registry` to `services/analytics.py`, confirmed the corresponding parametrized case failed with the file, offending import, and line number named in the assertion message, then reverted the file to its original content and re-ran green.

## Task Commits

Each task was committed atomically:

1. **Task 1: Define the JobContext and JobHandler contracts** - `c998b8f` (feat)
2. **Task 2: JobRegistry and the JOB-03 extensibility enforcement test** - `f974ccd` (feat)
3. **Task 3: JOB-04 reverse import-boundary enforcement test** - `7e26c94` (test)

_Plan-metadata commit follows this SUMMARY.md's own creation._

## Files Created/Modified
- `src/trading_platform/jobs/__init__.py` - Deliberately minimal package docstring; zero imports/re-exports
- `src/trading_platform/jobs/contracts.py` - `JobContext`, `JobHandler` Protocols; `JobCancelledError`
- `src/trading_platform/jobs/registry.py` - `JobRegistry`, `UnknownJobTypeError`, `build_default_registry()`
- `tests/test_job_registry.py` - JOB-03 register/resolve/duplicate/unknown-type + extensibility enforcement
- `tests/test_job_import_boundary.py` - JOB-04 reverse AST import-boundary enforcement

## Decisions Made
- Kept `jobs/__init__.py` free of any import/re-export statement (not even `from __future__ import annotations`, since the acceptance grep counts any line starting `^from`), per the plan's explicit instruction that this shared file must never need editing by a sibling Phase 17 plan.
- `_collect_imported_modules` in the JOB-04 test resolves `ast.ImportFrom` relative imports (`level > 0`) against the file's `__package__` dotted name using the same `bits = package.rsplit(".", level - 1)` algorithm CPython's `importlib._bootstrap._resolve_name` uses, so a hypothetical `from . import jobs`-style relative import inside a nested `services/` subpackage cannot evade the forbidden-root check.
- `QUEUE_FRAMEWORK_MODULES`' AST scan silently skips any of the 6 listed files that does not yet exist (5 of 6 are created by later Phase 17 plans 17-03 through 17-07), while the module-list length itself (6) is asserted unconditionally so a later contributor cannot narrow the list to make the check trivially pass.

## Deviations from Plan

None - plan executed exactly as written. One `ruff format` auto-reformat was applied to `tests/test_job_import_boundary.py` (line-wrapping only, no logic change) as part of the project's pre-commit format gate; re-verified green after reformatting, no deviation classification needed since this is standard tooling, not a plan/behavior change.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- `trading_platform.jobs.contracts.JobContext`/`JobHandler` are now the frozen interfaces every later Phase 17 plan (queue/lease claim, lifecycle transitions, dependencies, cancellation, progress/logging, API read routes) and Phase 19's concrete operation handlers build against.
- `trading_platform.jobs.registry.JobRegistry`/`build_default_registry` are ready for Phase 19 to append `register(...)` calls for concrete handlers with zero edits to any queue-framework module — the exact claim JOB-03's enforcement test pins.
- The JOB-04 reverse import-boundary test now runs on every future `services/` module automatically (auto-scoping glob), so any future service accidentally importing up into the Job/HTTP/scheduling layers will fail CI immediately rather than being caught in review.
- No code blockers identified for 17-03 onward. Full test suite verified green (349 passed, 0 failed) after this plan's changes, including the two new test files (41 tests total: 6 in `test_job_registry.py`, 35 in `test_job_import_boundary.py`).

---
*Phase: 17-job-framework*
*Completed: 2026-07-19*

## Self-Check: PASSED

All five created files verified present on disk; all four task/summary commit hashes (c998b8f, f974ccd, 7e26c94, 21380a6) verified present in git log.
