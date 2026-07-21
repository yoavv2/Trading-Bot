---
phase: 18-orchestration-surface
plan: 02
subsystem: jobs
tags: [python, sqlalchemy, postgresql, transactions, cancellation]

requires:
  - phase: 17-job-framework
    provides: Job submission, guarded lifecycle transitions, and cancellation semantics
  - phase: 18-orchestration-surface
    provides: endpoint-scoped Job mutation persistence
provides:
  - caller-owned transaction participation for Job submission
  - caller-owned transaction participation for queued and running cancellation
  - rollback regressions for Job, dependency, event, and cancellation audit mutations
affects: [18-03, job-orchestration, idempotency]

tech-stack:
  added: []
  patterns: [session-owned private primitives with standalone compatibility wrappers, flush-without-commit transaction composition]

key-files:
  created: []
  modified:
    - src/trading_platform/jobs/dependencies.py
    - src/trading_platform/jobs/cancellation.py
    - tests/test_job_dependencies.py
    - tests/test_job_cancellation.py

key-decisions:
  - "Keep public submit_job and request_cancellation backward-compatible while delegating both modes to one Session-owned mutation path."
  - "Caller-session paths flush but never commit or close, so a future orchestration service can atomically persist idempotency and Job mutations."

requirements-completed: [ORCH-02, ORCH-03]
duration: 8min
completed: 2026-07-21
---

# Phase 18 Plan 02: Transaction-Composable Job Primitives Summary

**Backward-compatible Job submission and cancellation primitives now share caller-owned transactions, allowing idempotency reservation and Job/audit mutations to commit or roll back atomically.**

## Performance

- **Duration:** 8 min
- **Started:** 2026-07-21T17:14:59Z
- **Completed:** 2026-07-21T17:22:50Z
- **Tasks:** 2/2
- **Files modified:** 4

## Accomplishments

- Extracted one Session-accepting insertion path for Job UUID generation, dependency validation/deduplication, Job and JobDependency insertion, the SUBMITTED event, and flush.
- Added optional caller Session support to `submit_job` without changing standalone `session_scope(settings)` commit behavior.
- Routed all queued/running cancellation branches through one Session-owned implementation, retaining row locks, lifecycle-only status changes, cascade behavior, and immutable first-request audit facts.
- Added real-PostgreSQL caller commit/rollback coverage for submission and queued/running cancellation.

## Task Commits

Each task was committed atomically:

1. **Task 1: Make submit_job caller-session-capable without duplicating insertion semantics** - `b92f7c6` (feat)
2. **Task 2: Make request_cancellation caller-session-capable while preserving first-request audit facts** - `8b42420` (feat)

## Files Created/Modified

- `src/trading_platform/jobs/dependencies.py` - Shared Session-owned submission insertion path with standalone compatibility wrapper.
- `src/trading_platform/jobs/cancellation.py` - Shared Session-owned queued/running cancellation path with standalone compatibility wrapper.
- `tests/test_job_dependencies.py` - Matching standalone/caller-session submission and full rollback assertions.
- `tests/test_job_cancellation.py` - Commit/rollback and first requester/reason/timestamp immutability assertions.

## Decisions Made

- Preserve Phase 17 public function contracts by adding keyword-only `session: Session | None = None` rather than replacing standalone APIs.
- Keep all submission and cancellation mutation semantics in a single internal Session path, preventing divergent event, dependency, lifecycle, or audit behavior between invocation modes.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Repaired the local pre-commit hook launcher after repository relocation**
- **Found during:** Task 1 commit
- **Issue:** The generated Git hook referenced the prior repository path, so it could not find `pre-commit`.
- **Fix:** Reinstalled the hook with the working project virtual environment; no tracked source or dependency files changed.
- **Verification:** Both task commits completed through the configured hooks.
- **Committed in:** Environment-only repair before `b92f7c6`

**2. [Rule 2 - Missing Critical] Asserted full first-request audit immutability**
- **Found during:** Task 2
- **Issue:** The existing repeat-running-cancellation test asserted only requester identity, leaving reason and timestamp immutability unproven despite the Phase 18 threat mitigation.
- **Fix:** Extended the regression to assert the first reason and request timestamp remain unchanged on a second request.
- **Files modified:** `tests/test_job_cancellation.py`
- **Verification:** `pytest tests/test_job_cancellation.py -x -q` passed 20 tests.
- **Committed in:** `8b42420`

**3. [Rule 1 - Bug] Imported the cancellation event outcome enum used by the new parity assertion**
- **Found during:** Task 2 verification
- **Issue:** The new caller-session event parity test referenced `JobTransitionOutcome` without importing it.
- **Fix:** Added the missing model enum import.
- **Files modified:** `tests/test_job_cancellation.py`
- **Verification:** Focused cancellation suite passed 20 tests after the correction.
- **Committed in:** `8b42420`

---

**Total deviations:** 3 auto-fixed (1 blocking environment repair, 1 missing critical assertion, 1 test bug)
**Impact on plan:** All changes preserve the planned API and Phase 17 semantics; no additional runtime surface or dependency was introduced.

## Issues Encountered

- The plan-level mypy command is blocked by a pre-existing missing `PyYAML` type stub in `src/trading_platform/core/settings.py`; the focused Job modules introduce no reported type errors. Pytest (37 tests) and Ruff pass.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Plan 18-03 can invoke the exact existing `submit_job(..., session=session)` and `request_cancellation(..., session=session)` primitives inside its idempotency transaction.
- The transaction boundary remains explicit: standalone callers retain Phase 17 commits, while orchestration callers own commit/rollback.

## Self-Check: PASSED

- Confirmed all four modified plan artifacts and this summary exist.
- Confirmed task commits `b92f7c6` and `8b42420` exist in Git history.

---
*Phase: 18-orchestration-surface*
*Completed: 2026-07-21*
