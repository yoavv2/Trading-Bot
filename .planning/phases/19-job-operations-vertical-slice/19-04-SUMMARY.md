---
phase: 19-job-operations-vertical-slice
plan: 04
subsystem: api
tags: [fastapi, job-registry, catalog, orch-06, closed-enum]

requires:
  - phase: 19-job-operations-vertical-slice/19-02
    provides: require_mutations_enabled dependency, Settings.orchestration.mutations_enabled
provides:
  - JobCancellationMode closed StrEnum ({"step_boundary"})
  - JobSubmissionSpec Protocol extended (description, cancellation_mode, submission_defaults())
  - JobRegistry.register registration-time validation of the above three attributes
  - GET /api/v1/job-types read-only catalog route (D-20 shape, D-23 minimal fields)
affects: [19-06, 19-07, 19-08, console-job-type-picker, console-submission-form-prefill]

tech-stack:
  added: []
  patterns:
    - "Registration-time contract validation before either registry mapping mutates (mirrors the existing job_type-match check)"
    - "Separate sibling APIRouter (own prefix) instead of nesting a new GET route under a router that already declares a greedy /{id} path"
    - "Catalog resilience: per-entry try/except around a read-time side-effecting call (submission_defaults()), never failing the whole response; sanitized log (type(exc).__name__ only, never str(exc))"

key-files:
  created:
    - src/trading_platform/api/routes/job_types.py
    - tests/test_job_catalog.py
  modified:
    - src/trading_platform/jobs/registry.py
    - src/trading_platform/api/app.py
    - tests/test_job_mutation_api.py
    - tests/test_job_mutation_e2e.py
    - tests/test_job_orchestration.py
    - tests/test_mutation_guard.py

key-decisions:
  - "ORCH-06 marked Complete: its literal text (endpoint exists, lists every registered type with description + cancellation mode, enforcement test) is fully satisfied by this plan's mechanism; the requirement text does not require a populated production catalog, and no later Phase 19 plan lists ORCH-06 in its frontmatter."
  - "test_default_registry_types_all_appear_in_catalog is a vacuous pass today (build_default_registry() stays empty per this plan's explicit 'do not change build_default_registry' scope) -- the enforcement mechanism is proven correct against a hand-built registry in the other catalog tests; it becomes non-vacuous once a later plan registers backtest."

patterns-established:
  - "Job-type catalog metadata (description/cancellation_mode/submission_defaults) is a registration-time invariant enforced in JobRegistry.register, not a convention checked elsewhere."

requirements-completed: [ORCH-06]

duration: 25min
completed: 2026-09-24
---

# Phase 19 Plan 04: Job-Type Catalog Summary

**GET /api/v1/job-types read-only catalog endpoint (ORCH-06) backed by a new registration-time contract on `JobRegistry.register` — every publicly submittable Job type must declare a description, a closed `JobCancellationMode`, and a `submission_defaults()` callable before it can register.**

## Performance

- **Duration:** ~25 min
- **Started:** 2026-09-24 (session start)
- **Completed:** 2026-09-24
- **Tasks:** 2 (both `type="auto"`, plus one small in-scope test cleanup)
- **Files modified:** 7 (2 created, 5 modified)

## Accomplishments

- `JobCancellationMode` closed `StrEnum` (`STEP_BOUNDARY = "step_boundary"`) added to `jobs/registry.py`.
- `JobSubmissionSpec` Protocol extended with `description: str`, `cancellation_mode: JobCancellationMode`, and `submission_defaults() -> Mapping[str, Any] | None` (D-10).
- `JobRegistry.register` now validates all three before mutating either internal mapping — blank/oversized description, non-enum cancellation mode, or a missing/non-callable `submission_defaults` all raise `ValueError` naming the job type and the failing attribute, and leave the registry unchanged.
- New `GET /api/v1/job-types` route (`src/trading_platform/api/routes/job_types.py`), mounted as its own sibling `APIRouter(prefix="/api/v1/job-types")` — not nested under the `/api/v1/jobs` router, which already owns a greedy `/{job_id}` path. Returns `{mutations_enabled, items: [{job_type, description, cancellation_mode, submission_defaults?}]}` (D-20); item keys are a strict subset of the four allowed fields (D-23); runner-only registrations (no submission spec) are skipped, not 500'd; `submission_defaults()` failures are caught per-entry and logged with `job_type` + `type(exc).__name__` only, never `str(exc)`.
- `tests/test_job_catalog.py` (10 tests): shape + mutation flag (parametrized true/false), D-23 key-minimality, resilience under `None`/raising defaults, runner-only skip, ORCH-06 enforcement (every default-registry type resolves a spec and appears in the catalog), registration rejection for each of the three new invariants, router-separation (path + no `/api/v1/jobs/job-types` collision), and an AST-based import-boundary check (no `trading_platform.services`/`trading_platform.db`/`sqlalchemy` import in `job_types.py`).

## Task Commits

Each task was committed atomically:

1. **Task 1: Registry contract — JobCancellationMode, extended spec protocol, registration validation** — `8d6ff42` (feat)
2. **Task 2: GET /api/v1/job-types router + catalog tests** — `c4fdfe9` (feat)

**Additional in-scope commit:** `c7a7b03` (test) — removed a filler test that only proved `raise X` raises `X`; 10 tests remain, still meeting the plan's `>= 10` acceptance criterion.

**Plan metadata:** committed alongside this SUMMARY.

## Files Created/Modified

- `src/trading_platform/jobs/registry.py` — `JobCancellationMode`, extended `JobSubmissionSpec` Protocol, `JobRegistry.register` validation
- `src/trading_platform/api/routes/job_types.py` — new read-only catalog route
- `src/trading_platform/api/app.py` — `app.include_router(job_types_router)` alongside `jobs_router`
- `tests/test_job_catalog.py` — new, 10 tests
- `tests/test_job_mutation_api.py`, `tests/test_job_mutation_e2e.py`, `tests/test_job_orchestration.py` — test-only submission specs extended to satisfy the new registration contract (plan-scoped)
- `tests/test_mutation_guard.py` — test-only `_GuardProbeSubmissionSpec` extended (see Deviations — this file was NOT in the plan's `files_modified` but broke as a direct consequence of Task 1)

## Decisions Made

- ORCH-06 marked Complete (see `key-decisions` above and REQUIREMENTS.md/ROADMAP.md updates) — the requirement's literal text is fully satisfied by the mechanism this plan ships; it does not require the production registry to be non-empty.
- Kept the resilience `except Exception` in `job_types.py` broad (not narrowed to a specific exception type) per the plan's explicit "return None on exception" instruction and the threat register's T-19-04-03 (DoS mitigation) — any failure in `submission_defaults()`, not just DB errors, must not fail the whole catalog response.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] `tests/test_mutation_guard.py`'s pre-existing test-only submission spec broke under the new registration contract**
- **Found during:** Task 2 verification (`python -m pytest tests/test_job_catalog.py tests/test_job_api.py tests/test_orchestration_boundaries.py tests/test_mutation_guard.py -x -q`, per the plan's own declared verify command for Task 2)
- **Issue:** `_GuardProbeSubmissionSpec` (shipped by Plan 19-02, not in this plan's `files_modified` frontmatter) had no `description`/`cancellation_mode`/`submission_defaults`, so `JobRegistry.register` raised `ValueError` for it as soon as Task 1's validation landed.
- **Fix:** Added `description`, `cancellation_mode = JobCancellationMode.STEP_BOUNDARY`, and `submission_defaults() -> None` to `_GuardProbeSubmissionSpec`, mirroring the fix already applied to the three plan-scoped test files.
- **Files modified:** `tests/test_mutation_guard.py`
- **Verification:** `pytest tests/test_job_catalog.py tests/test_job_api.py tests/test_orchestration_boundaries.py tests/test_mutation_guard.py -q` → 60 passed.
- **Committed in:** `c4fdfe9` (part of Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 blocking)
**Impact on plan:** Necessary to make the plan's own declared Task 2 verification command pass; no scope creep — the fix is the minimal three-line addition already established as the pattern by Task 1.

## Issues Encountered

- `test_job_types_route_is_separate_router` initially iterated `app.routes` directly and found nothing, because FastAPI/Starlette route objects require the `effective_candidates()` unwrapping already established by `tests/test_orchestration_boundaries.py::_effective_routes()`. Fixed by adopting the same pattern; not a deviation (test-only authoring detail, fixed before first commit).
- A grep-based acceptance check (`grep -n "trading_platform.services\|sqlalchemy" src/trading_platform/api/routes/job_types.py` expecting no matches) initially false-positived on the module's own docstring, which named the forbidden imports it was documenting the *absence* of. Reworded the docstring to describe the constraint without embedding the literal forbidden strings; fixed before commit.

## User Setup Required

None — no external service configuration required.

## Next Phase Readiness

- The `JobCancellationMode`/`description`/`submission_defaults()` contract is now load-bearing: any later plan that registers a concrete operation type in `build_default_registry` (expected in a subsequent Phase 19 plan per the pattern map) must supply all three or registration raises `ValueError`.
- `test_default_registry_types_all_appear_in_catalog` (`tests/test_job_catalog.py`) will become a non-vacuous, real end-to-end proof the moment `build_default_registry` registers `backtest` — no test change needed at that point, it already asserts the invariant generically.
- `GET /api/v1/job-types` is live and route-tested but returns `items: []` in the running application until that same later plan lands, since `build_default_registry()` is intentionally untouched here (explicit plan scope boundary).
- Full test suite: 542 passed, 0 failed (`.venv/bin/python -m pytest -q`).

---
*Phase: 19-job-operations-vertical-slice*
*Completed: 2026-09-24*
