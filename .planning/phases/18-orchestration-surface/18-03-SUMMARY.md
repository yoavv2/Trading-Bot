---
phase: 18-orchestration-surface
plan: 03
subsystem: orchestration
tags: [python, postgresql, sqlalchemy, idempotency, jobs, cancellation]

requires:
  - phase: 18-orchestration-surface
    provides: endpoint-scoped JobMutation uniqueness and caller-owned Job transactions
  - phase: 17-job-framework
    provides: generic Job persistence, submission, and cancellation primitives
provides:
  - transport-independent registered Job submission with canonical idempotency outcomes
  - transport-neutral public payload validation contract on JobRegistry
  - compact relative Job references and idempotent cancellation orchestration
affects: [18-04, 18-05, 18-06, job-api, operation-triggers]

tech-stack:
  added: []
  patterns: [pre-transaction payload validation, savepoint-backed uniqueness recovery, endpoint-scoped canonical fingerprints, compact relative mutation references]

key-files:
  created:
    - src/trading_platform/orchestration/__init__.py
    - src/trading_platform/orchestration/job_mutations.py
    - tests/test_job_orchestration.py
  modified:
    - src/trading_platform/jobs/registry.py

key-decisions:
  - "Keep public payload validation adjacent to the Job registry so runner-only registrations remain non-public."
  - "Use a named-constraint savepoint rollback to discard concurrent losing Job candidates before replay/conflict resolution."
  - "Reserve fresh cancellation identities even for already pending or cancelled Jobs while preserving the original audit facts."

patterns-established:
  - "Validate and canonicalize public submission input before entering session_scope."
  - "Build all mutation responses from one Job-to-reference builder with fixed relative observation links."

requirements-completed: [ORCH-03, ORCH-04]
duration: 20min
completed: 2026-07-21
---

# Phase 18 Plan 03: Job Orchestration Service Summary

**Transport-independent Job orchestration with registered JSON-safe payload validation, database-backed idempotent replay/conflicts, cancellation audit preservation, and compact relative references.**

## Performance

- **Duration:** 20 min
- **Started:** 2026-07-21T17:35:00Z
- **Completed:** 2026-07-21T17:55:01Z
- **Tasks:** 2/2
- **Files modified:** 4

## Accomplishments

- Added `JobSubmissionSpec` and `InvalidJobPayloadError` without changing the frozen Phase 17 handler contract.
- Created the application-layer service that validates before any session, fingerprints canonical normalized input, and uses the database uniqueness constraint plus savepoints for deterministic replay/conflict outcomes.
- Added idempotent cancellation through the Phase 17 primitive, retaining first-request audit facts and returning compact current Job references for submission and cancellation.
- Proved real-PostgreSQL validation, concurrent replay/conflict rollback, cancellation, reference, transaction-composition, and import-boundary invariants.

## Task Commits

Each task was committed atomically:

1. **Task 1: Implement deterministic registered Job submission with race-safe replay/conflict outcomes** - `b5fc1a7` (feat)
2. **Task 2: Implement idempotent cancellation and the one compact current-reference builder** - `18a7d97` (feat)

## Files Created/Modified

- `src/trading_platform/orchestration/__init__.py` - Deliberately empty application-layer package boundary.
- `src/trading_platform/orchestration/job_mutations.py` - Idempotent submission/cancellation service, typed outcomes, fingerprints, and compact references.
- `src/trading_platform/jobs/registry.py` - Transport-neutral public submission specifications adjacent to handler registration.
- `tests/test_job_orchestration.py` - Real-PostgreSQL submission races, rollback, cancellation, validation, and reference regressions.

## Decisions Made

- Kept public payload contracts in `jobs.registry`, while placing DB-aware orchestration in a separate package so `services` remains Job-independent.
- Used a nested savepoint around candidate Job/mutation inserts so a named uniqueness loser leaves no stray Job or event before loading the committed winner.
- Created a new cancellation mutation identity for fresh keys against pending or cancelled Jobs, without calling the cancellation primitive or overwriting audit facts.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Made named-constraint detection type-safe**
- **Found during:** Task 2 verification
- **Issue:** The initial PostgreSQL constraint diagnostic lookup was valid at runtime but mypy could not prove that `IntegrityError.orig` exposed `diag`.
- **Fix:** Read the optional diagnostic through `getattr` before checking its constraint name.
- **Files modified:** `src/trading_platform/orchestration/job_mutations.py`
- **Verification:** `mypy src/trading_platform/orchestration` passed with no issues.
- **Committed in:** `18a7d97`

---

**Total deviations:** 1 auto-fixed (1 correctness/type-safety fix)
**Impact on plan:** The fix preserves the planned PostgreSQL named-constraint recovery behavior without changing the public contract.

## Issues Encountered

- Ruff required import normalization in the new test module; the formatter applied the repository convention before focused tests ran.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Plans 18-04 through 18-06 can expose the service through adapters without duplicating validation, transaction, idempotency, or reference logic.
- The default registry remains empty; Phase 19 can register production handler/specification pairs through the new contract.

## Self-Check: PASSED

- Confirmed `src/trading_platform/orchestration/__init__.py`, `src/trading_platform/orchestration/job_mutations.py`, `src/trading_platform/jobs/registry.py`, and `tests/test_job_orchestration.py` exist.
- Confirmed task commits `b5fc1a7` and `18a7d97` exist in Git history.

---
*Phase: 18-orchestration-surface*
*Completed: 2026-07-21*
