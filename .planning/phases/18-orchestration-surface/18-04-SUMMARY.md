---
phase: 18-orchestration-surface
plan: 04
subsystem: api
tags: [python, fastapi, postgresql, idempotency, jobs, orchestration]

requires:
  - phase: 18-orchestration-surface
    provides: transport-independent idempotent Job mutation service and registry-adjacent public payload validation
  - phase: 17-job-framework
    provides: generic Job persistence, cancellation primitive, and read-only observation routes
provides:
  - thin POST Job submission and cancellation HTTP adapters over JobOrchestrationService
  - typed idempotency and validation HTTP errors with compact relative Job references
  - real-PostgreSQL API tests for success, replay, rejection, and cancellation contracts
affects: [18-05, 18-06, operation-triggers, job-api]

tech-stack:
  added: []
  patterns: [Depends-injected orchestration adapter, typed exception-to-HTTP mapping, endpoint method allowlist enforcement]

key-files:
  created:
    - tests/test_job_mutation_api.py
  modified:
    - src/trading_platform/api/dependencies.py
    - src/trading_platform/api/routes/jobs.py
    - tests/test_job_api.py

key-decisions:
  - "Routes return JobOrchestrationService references unchanged, keeping validation, fingerprints, transactions, and lifecycle work outside HTTP adapters."
  - "FastAPI 0.131 effective route contexts are aggregated by path for the exact method-set contract test."

patterns-established:
  - "Mutation routes accept an optional aliased Idempotency-Key header so the service can return typed missing-key 400 outcomes."
  - "Every successful Job mutation serializes only the service-built compact relative reference."

requirements-completed: [ORCH-01, ORCH-03, ORCH-04]
duration: 3min
completed: 2026-07-21
---

# Phase 18 Plan 04: Job Mutation API Summary

**FastAPI submission and cancellation adapters with typed idempotency outcomes, fixed relative Job references, and real-PostgreSQL HTTP contract coverage.**

## Performance

- **Duration:** 3 min
- **Started:** 2026-07-21T18:06:17Z
- **Completed:** 2026-07-21T18:09:37Z
- **Tasks:** 2/2
- **Files modified:** 4

## Accomplishments

- Added the only two Job mutation routes, delegating exclusively to `JobOrchestrationService` and preserving all five observation routes.
- Mapped every expected service outcome to stable status codes and error details while preserving service-built compact relative links.
- Added HTTP contract coverage for new/replayed/conflicting submissions, zero-write rejections, cancellation lifecycle outcomes, and endpoint-scoped idempotency.

## Task Commits

Each task was committed atomically:

1. **Task 1: Wire Job registry/orchestration dependencies and thin POST adapters** - `6d85e3d` (feat)
2. **Task 2: Pin all HTTP statuses, headers, compact bodies, and zero-write rejections** - `064dd80` (test)

## Files Created/Modified

- `src/trading_platform/api/dependencies.py` - Provides injectable Job registry and orchestration service dependencies.
- `src/trading_platform/api/routes/jobs.py` - Maps generic submission and cancellation HTTP requests to typed orchestration outcomes.
- `tests/test_job_api.py` - Enforces the exact GET/POST Job route method surface.
- `tests/test_job_mutation_api.py` - Exercises real-PostgreSQL mutation status, header, body, link, and no-write contracts.

## Decisions Made

- Returned `MutationResult.reference.to_dict()` directly so routes cannot drift from the compact link contract.
- Aggregated FastAPI 0.131 included-router effective route contexts in the route fence because registered API routes are no longer exposed as flat `app.routes` entries.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Adapted route introspection to FastAPI 0.131 included routers**
- **Found during:** Task 1 verification
- **Issue:** The existing flat `app.routes` scan observes included routers as opaque `_IncludedRouter` entries, producing no Job routes.
- **Fix:** Aggregated `effective_candidates()` contexts by path before asserting the exact HTTP method sets.
- **Files modified:** `tests/test_job_api.py`
- **Verification:** `pytest tests/test_job_api.py -x -q` passed with the full Job route surface asserted.
- **Committed in:** `6d85e3d`

---

**Total deviations:** 1 auto-fixed (1 blocking compatibility fix)
**Impact on plan:** The compatibility adaptation keeps the planned exact method fence executable on the installed FastAPI version without changing production behavior.

## Issues Encountered

- Ruff normalized the new dependency imports; no behavior changed.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Phase 18's HTTP mutation contract is ready for CLI adapter and structural-boundary work in Plans 18-05 and 18-06.
- The production default registry remains empty; Phase 19 is still the first phase that may register production operation handlers.

## Self-Check: PASSED

- Confirmed `src/trading_platform/api/dependencies.py`, `src/trading_platform/api/routes/jobs.py`, `tests/test_job_api.py`, and `tests/test_job_mutation_api.py` exist.
- Confirmed task commits `6d85e3d` and `064dd80` exist in Git history.

---
*Phase: 18-orchestration-surface*
*Completed: 2026-07-21*
