---
phase: 20-complete-operation-migration-safety-controls
plan: 16
subsystem: api
tags: [job-registry, fastapi, pydantic, react, job-catalog]

# Dependency graph
requires:
  - phase: 20-complete-operation-migration-safety-controls
    provides: "20-07 (risk-evaluation), 20-08 (reconciliation), 20-09 (paper-session), 20-11 (broker-order-sync), 20-13 (HTTP routes + retry mechanism), 20-14 (ingest-bars), 20-15 (sync-symbol-metadata, sync-market-sessions) -- seven unregistered spec+handler+form triples and the HTTP mechanism layer they plug into"
provides:
  - "build_default_registry() with all 8 Job types registered (backtest + the 7 new types)"
  - "Exact-set pins for cancellation_mode (D-01), required_execution_mode (D-22), and retry_prerequisite_for (D-19) over the production registry"
  - "A market-data payload-field-set pin proving no ingest-bars/sync-symbol-metadata/sync-market-sessions spec accepts a mode/behavior flag (OPS-05)"
  - "A production-registry GET /api/v1/job-types catalog test: 8 items, every description nonblank, cancellation_mode per the D-01 map, paper-session's queued_only cancellation documented in its description (D-03/OPS-03)"
  - "console JOB_TYPE_FORMS with all 8 job types mapped to their submission form components, reachable from /jobs/new"
affects: [20-17-retry-console, 20-18-controls-console, 20-19-remaining-job-type-console-wiring, 20-20-paper-session-retry-wiring, 20-21-market-data-e2e, 20-22-controls-console-live-verify, 20-23-operations-console-live-verify]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Registry registration landed in one commit for all 7 types (not per-type) to avoid the STATE.md-documented concurrent-edit collision history on registry.py/jobTypeForms.ts (hot shared files)."

key-files:
  created:
    - console/src/lib/jobTypeForms.test.ts
  modified:
    - src/trading_platform/jobs/registry.py
    - tests/test_orchestration_boundaries.py
    - tests/test_job_catalog.py
    - tests/test_job_operations_e2e.py
    - tests/test_job_registry.py
    - console/src/lib/jobTypeForms.ts

key-decisions:
  - "Reworded one build_default_registry docstring line (was: 'one \\`\\`registry.register(SomeHandler(...))\\`\\` call') because its literal backtick-quoted text was itself a false-positive match for the plan's own acceptance-criteria grep (\\`grep -c \"registry.register(\" registry.py\\` must equal exactly 8); reworded to describe the same contract without repeating the call syntax verbatim."
  - "Fixed tests/test_job_registry.py::test_build_default_registry_registers_backtest (not in this plan's files_modified) from an exact-list assertion to a membership assertion -- Rule 1 auto-fix, directly caused by this task's registry.py change, scope-limited to the one assertion this plan's own change broke."
  - "Left OPS-02, OPS-03, OPS-04, OPS-05, OPS-06 all Pending in REQUIREMENTS.md despite being this plan's frontmatter requirements -- confirmed by reading 20-19-PLAN.md (OPS-02/04/06, depends_on 20-16), 20-20-PLAN.md (OPS-03, depends_on 20-16), and 20-21-PLAN.md (OPS-05, depends_on 20-16): each requirement's literal 'from the UI' / operator-invocable text needs a true end-to-end test over the production app (POST /api/v1/jobs -> run-jobs --once -> SUCCEEDED) that this plan does not run. This plan only makes the types reachable in the registry and the console form map. Matches the 19-01/19-03/20-04/20-07/20-13/20-15 precedent of not overclaiming end-to-end behavior at the wiring layer."

requirements-completed: []  # See key-decisions: OPS-02..06 remain Pending, closed by 20-19/20-20/20-21 respectively.

# Metrics
duration: ~20min
completed: 2026-09-28
---

# Phase 20 Plan 16: Register Remaining 7 Job Types + Pin Registry Contract Summary

**All 8 Job types (backtest + 7 Phase 20 types) now register in the production `build_default_registry()` and the console `JOB_TYPE_FORMS` map, with exact-set pins on cancellation mode, execution mode, retry prerequisites, and market-data payload shape.**

## Performance

- **Duration:** ~20 min
- **Tasks:** 2
- **Files modified:** 6 (5 Python, 1 TypeScript) + 1 new TypeScript test file

## Accomplishments
- `build_default_registry()` registers all 8 Job types (backtest, broker-order-sync, ingest-bars, paper-session, reconciliation, risk-evaluation, sync-market-sessions, sync-symbol-metadata) in one commit, closing the "hot shared file" concurrent-edit risk this plan was designed to avoid.
- New exact-set tests pin the registry's per-type contract: `test_default_registry_cancellation_modes_are_pinned` (D-01), `test_default_registry_execution_modes_are_pinned` (D-22), `test_default_registry_retry_prerequisites_are_pinned` (D-19, also asserts every declared prerequisite is itself registered), and `test_market_data_specs_have_no_mode_flags` (OPS-05, inspects each market-data spec's private pydantic payload model's `model_fields` keys).
- New `test_production_registry_catalog_lists_all_eight_types` in `tests/test_job_catalog.py` proves the read route over the real production registry: 8 items, nonblank descriptions, correct cancellation_mode per type, and paper-session's description literally contains "Cancellable only while queued" (D-03/OPS-03).
- Console `JOB_TYPE_FORMS` now maps all 8 job types to their form components; `jobTypeForms.test.ts` pins the exact 8-key sorted set and that every value is callable.
- Every Job type is now submittable from `/jobs/new` through the single lookup map (D-17).

## Task Commits

Each task was committed atomically:

1. **Task 1: Register the 7 types + pin the registry contract + catalog** - `677491c` (feat)
2. **Task 2: Register the 7 forms in the console lookup map** - `c0cbbc3` (feat)

**Plan metadata:** (this commit)

## Files Created/Modified
- `src/trading_platform/jobs/registry.py` - `build_default_registry()` now imports and registers all 7 remaining handler/spec pairs alongside backtest, in alphabetical job_type order; docstring updated.
- `tests/test_orchestration_boundaries.py` - Renamed `test_default_registry_registers_exactly_the_phase19_job_types` to `..._phase20_job_types` (now asserts the full 8-type sorted list); added 4 new pin tests for cancellation mode, execution mode, retry prerequisites, and market-data payload shape.
- `tests/test_job_catalog.py` - Added `test_production_registry_catalog_lists_all_eight_types` exercising `GET /api/v1/job-types` over `build_default_registry()`.
- `tests/test_job_operations_e2e.py` - `test_job_types_catalog_lists_backtest_with_defaults` now selects the backtest item by `job_type` instead of asserting `len(items) == 1` (the 8-item count is now pinned in `test_job_catalog.py`).
- `tests/test_job_registry.py` - `test_build_default_registry_registers_backtest` changed from an exact `== ["backtest"]` assertion to a membership check (Rule 1 auto-fix; this test is not in the plan's `files_modified` but was directly broken by the registry change).
- `console/src/lib/jobTypeForms.ts` - Imports and registers all 7 new form components in `JOB_TYPE_FORMS`.
- `console/src/lib/jobTypeForms.test.ts` (new) - Pins the exact 8-key sorted job-type set and that every value is a function.

## Decisions Made
See `key-decisions` in frontmatter: (1) reworded one docstring line to avoid a self-defeating grep false-positive against the plan's own acceptance criteria; (2) Rule 1 auto-fixed `test_job_registry.py`'s now-broken exact-list assertion; (3) left all five of this plan's frontmatter requirements (OPS-02..06) Pending, confirmed against the three downstream E2E plans (20-19, 20-20, 20-21) that each literally close one or more of them via a real `POST /api/v1/jobs` -> `run-jobs --once` -> SUCCEEDED round trip this plan does not perform.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed test_job_registry.py's registry-count assertion broken by this plan's own registry change**
- **Found during:** Task 1 verification (full-suite run)
- **Issue:** `test_build_default_registry_registers_backtest` asserted `registry.list_job_types() == ["backtest"]`, which fails now that `build_default_registry()` registers 8 types. This test is not in the plan's declared `files_modified`, but is directly caused by the current task's change to `registry.py`.
- **Fix:** Changed the assertion from an exact single-item list to `"backtest" in registry.list_job_types()`, with a comment pointing to the new exact-set pin in `test_orchestration_boundaries.py`.
- **Files modified:** `tests/test_job_registry.py`
- **Verification:** `PYTHONPATH=src .venv/bin/pytest tests/test_orchestration_boundaries.py tests/test_job_catalog.py tests/test_job_registry.py tests/test_job_operations_e2e.py -q` -> 75 passed; full suite -> 924 passed.
- **Committed in:** `677491c` (Task 1 commit)

---

**Total deviations:** 1 auto-fixed (1 Rule 1 bug fix)
**Impact on plan:** Necessary to keep the full suite green after the registry change; no scope creep -- confined to the single assertion this task's own change broke.

## Issues Encountered
None.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- All 8 Job types are registered in the production registry and console form map; 20-19/20-20/20-21 can now run their E2E tests against the real, populated `build_default_registry()` and `JOB_TYPE_FORMS`.
- OPS-02 through OPS-06 remain Pending in REQUIREMENTS.md by design -- each will be marked Complete by its respective downstream E2E plan (20-19, 20-20, 20-21) once operator-invocable end-to-end behavior is proven against the production app.
- No blockers.

---
*Phase: 20-complete-operation-migration-safety-controls*
*Completed: 2026-09-28*

## Self-Check: PASSED

All created/modified files verified present on disk; both task commits (677491c, c0cbbc3) verified present in git log.
