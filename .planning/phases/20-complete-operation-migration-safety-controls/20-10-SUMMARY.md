---
phase: 20-complete-operation-migration-safety-controls
plan: 10
subsystem: jobs
tags: [job-orchestration, cancellation, retry, idempotency, sqlalchemy, postgresql]

# Dependency graph
requires:
  - phase: 20-complete-operation-migration-safety-controls
    provides: "20-01: migration 0021 (jobs.retry_of_job_id UNIQUE FK, submit_job(retry_of_job_id=)); 20-04: JobCancellationMode.QUEUED_ONLY + registry.retry_prerequisite_for(spec)"
provides:
  - "JobOrchestrationService.cancel(): a RUNNING Job whose type is QUEUED_ONLY is rejected with JobNotCancellableRunningError before session.begin_nested(), writing zero rows; race against claim_next_job resolves deterministically via the shared row lock"
  - "JobOrchestrationService.retry(job_id=, idempotency_key=): idempotent operator retry -- FAILED/CANCELLED originals only, one retry per Job (uq_jobs_retry_of_job_id), D-18 payload revalidation-as-gate (payload copied verbatim), D-19 reconcile-first block for uncertain FAILED Jobs whose type declares a retry_prerequisite_job_type"
  - "JobOrchestrationService.retry_block(job_id=): read-only D-19/D-20 query over the jobs table returning the same RetryBlock (or None) retry() would raise/allow"
  - "New typed errors: JobNotCancellableRunningError, JobNotRetryableError, RetryAlreadyExistsError, RetryBlockedError, InvalidRetryPayloadError, RetryBlock dataclass"
affects: [20-11-market-data-job-types, 20-13-retry-api-route-and-controls, 20-16-remaining-job-type-registrations, 20-17-retry-console, 20-20-paper-session-retry-wiring]

tech-stack:
  added: []
  patterns:
    - "Pre-begin_nested rejection: both the queued-only cancel check and every retry precondition (terminal-status, existing-child, D-19 block, D-18 payload gate) run against the row-locked object BEFORE session.begin_nested() opens, so every rejection path writes zero rows by construction rather than by a rollback"
    - "_is_named_uniqueness_error(exc, *, constraint_name=...) is now a reusable IntegrityError dispatcher keyed by exact Postgres constraint name -- retry() uses it twice (uq_job_mutations_endpoint_key -> replay, uq_jobs_retry_of_job_id -> RetryAlreadyExistsError with the winning id re-read after rollback). NOTE: because retry() takes FOR UPDATE on the original Job before the existing-child pre-check, two concurrent fresh-key retries always serialize on that lock -- the loser's pre-check (select(Job.id).where(Job.retry_of_job_id == job_id)) already sees the winner's committed row, so it raises RetryAlreadyExistsError from the pre-check, never from the uq_jobs_retry_of_job_id IntegrityError branch. That branch is defense-in-depth against a narrower race this lock ordering does not produce under normal conditions, and is not exercised by any test in this plan."
    - "D-19's reconcile-first predicate reads only Job.payload/Job.status/Job.completed_at via Job.payload[\"strategy_id\"].as_string() == strategy_id -- no domain-report coupling, mirroring services/execution/submit_orders.py's existing JSON-path comparison pattern"

key-files:
  created: []
  modified:
    - src/trading_platform/orchestration/job_mutations.py
    - tests/test_job_orchestration.py

key-decisions:
  - "OPS-03 and OPS-07 stay Pending in REQUIREMENTS.md -- this plan ships only the JobOrchestrationService mechanism layer (queued-only cancel rejection, retry()/retry_block()); neither requirement's literal text (\"cancellable only before broker submission begins, and the catalog states this\" / \"retry a Job from its detail view\") is satisfied without the HTTP route and console UI, which later plans (20-13, 20-17, 20-20 per their own frontmatter) own. Matches the 17-01/19-01/19-03/20-01/20-04 precedent."
  - "The claim/cancel race test (D-02) exercises the real serialization mechanism directly -- claim_next_job's SKIP LOCKED read vs. cancel()'s blocking FOR UPDATE read on the same Job row -- rather than mocking timing, so the two possible outcomes {CANCELLED+unclaimed, RUNNING+rejected} are proven against real lock semantics across 10 iterations."
  - "_retry_block_for() and retry()'s explicit spec-lookup both resolve the submission spec independently (retry() needs the typed UnknownJobTypeForSubmissionError first; _retry_block_for() needs a silent None on an unknown type for the read-only retry_block() path) -- a small duplicate lookup rather than threading a resolved spec through, keeping retry_block() usable standalone."

patterns-established:
  - "Retry-prerequisite D-19 matrix testing: a dedicated fake submission spec declaring retry_prerequisite_job_type, seeded prerequisite Job rows with controlled completed_at/strategy_id/status, and a single parametrized test asserting retry_block() and retry() agree on every case in the closed decision table."

requirements-completed: []  # OPS-03/OPS-07 deliberately left Pending -- see key-decisions; this plan ships only the JobOrchestrationService mechanism, not the retry API route or console UI

# Metrics
duration: ~25min
completed: 2026-09-28
---

# Phase 20 Plan 10: Queued-Only Cancel Rejection + Operator Retry with Lineage Summary

**JobOrchestrationService gained a row-lock-scoped queued-only cancel rejection (D-02) and a full idempotent retry() method with lineage, D-18 payload revalidation, and a D-19 reconcile-first block, reusing the exact submit()/cancel() idempotency shape and the Phase 18 endpoint/key uniqueness backstop.**

## Performance

- **Duration:** ~25 min
- **Tasks:** 2 completed
- **Files modified:** 2 (job_mutations.py, test_job_orchestration.py)

## Accomplishments
- `JobOrchestrationService.cancel()` now rejects a RUNNING Job of a `QUEUED_ONLY` type with `JobNotCancellableRunningError`, checked against the same row-locked `Job` object acquired for the existing terminal check, before `session.begin_nested()` -- a rejected cancel writes zero rows (no `JobMutation`, no `cancellation_requested_at`, no `JobEvent`). An unregistered `job_type` is not treated as queued-only (cooperative path still applies).
- A 10-iteration `threading.Barrier(2)` race test pins the D-02 invariant against the real lock mechanism: `claim_next_job`'s `SKIP LOCKED` read vs. `cancel()`'s blocking `FOR UPDATE` read on the same QUEUED queued-only Job resolves to exactly one of `{CANCELLED and unclaimed, RUNNING and rejected}`, never `RUNNING` with `cancellation_requested_at` set.
- New `JobOrchestrationService.retry(job_id=, idempotency_key=)`: validates the key, computes a fingerprint over `job_id` alone, replays on a matching prior mutation, row-locks the original, requires `FAILED`/`CANCELLED` status, rejects a second retry via a `uq_jobs_retry_of_job_id`-backed pre-check plus `IntegrityError` fallback, applies the D-19 reconcile-first block, revalidates the stored payload against the current spec as a gate only (the new Job's payload is `dict(original.payload)` verbatim, D-18), and inserts via `submit_job(retry_of_job_id=job_id)` inside the same `begin_nested()`/`JobMutation` shape `submit()`/`cancel()` already use.
- New `JobOrchestrationService.retry_block(job_id=)`: a read-only query returning the same `RetryBlock` (or `None`) `retry()` would raise/allow, reading only the `jobs` table.
- `_is_named_uniqueness_error` generalized to accept `constraint_name`; `retry()` dispatches on both `uq_job_mutations_endpoint_key` (idempotent replay) and `uq_jobs_retry_of_job_id` (re-reads the winning retry Job id and raises `RetryAlreadyExistsError`).
- Source-boundary confirmed by grep: `orchestration/job_mutations.py` is the only call site outside `jobs/dependencies.py` that passes `retry_of_job_id=` to `submit_job` -- no automatic retry path exists (D-17).

## Task Commits

1. **Task 1: Queued-only cancel rejection inside the row lock + race test** - `7ae7d88` (feat)
2. **Task 2: Idempotent retry with lineage, D-18 revalidation and D-19 reconcile-first block** - `8121210` (feat)
3. **Post-review fix: assert retry_block() and a blocked retry() write zero rows** - `da557c3` (test, advisor-caught gap against the plan's own D-19 "writes nothing" bullet, before handback)

## Files Created/Modified
- `src/trading_platform/orchestration/job_mutations.py` - `JobNotCancellableRunningError` + queued-only cancel rejection in `cancel()`; `RETRY_ENDPOINT_ID`/`RETRY_BLOCKED_CODE` constants; `RetryBlock` dataclass; `JobNotRetryableError`/`RetryAlreadyExistsError`/`RetryBlockedError`/`InvalidRetryPayloadError`; `_retry_block_for()`, `retry_block()`, `retry()`; generalized `_is_named_uniqueness_error(exc, *, constraint_name=)`; module docstring documents D-17
- `tests/test_job_orchestration.py` - `_QueuedOnlyHandler`/`_QueuedOnlySpec` + registry/service builders; queued-only cancel behavior tests + D-02 race test; `_RetryProbeHandler`/`_RetryProbeSubmissionSpec` (declares `retry_prerequisite_job_type`) + registry/service builders; `_seed_job` extended with `payload`/`completed_at`/`outcome_uncertain`; full D-16..D-20 retry behavior matrix (lineage, chain-links-to-parent, replay/conflict, non-terminal rejection, second-retry conflict + concurrent race, invalid-payload rejection, unregistered-type rejection, D-19 8-case parametrized matrix, `retry_block` missing-job error)

## Decisions Made
- OPS-03/OPS-07 left Pending in REQUIREMENTS.md -- documented in frontmatter `key-decisions` above; later plans (20-13, 20-17, 20-20) own the HTTP route and console UI that make these requirements' literal end-to-end text true.
- The D-02 race test exercises real Postgres lock semantics (SKIP LOCKED vs. blocking FOR UPDATE on the same row) rather than mocking, so the invariant is proven against the actual mechanism, not a simulation of it.

## Deviations from Plan

None - plan executed exactly as written. `_is_named_uniqueness_error`'s generalization and the two independent spec lookups in `retry()`/`_retry_block_for()` were both explicitly called out in the plan's own `<action>` text, not discovered deviations.

## Issues Encountered

None. No auth gates, no checkpoints (plan is fully autonomous, no `checkpoint:*` tasks).

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- `JobOrchestrationService.retry()`/`retry_block()` and the queued-only cancel rejection are ready for the retry API route (`POST /api/v1/jobs/{job_id}/retry`) and `cancel_job`'s new `except JobNotCancellableRunningError` clause -- both explicitly out of this plan's scope per its `files_modified` list, owned by 20-13 (`CTRL-01, CTRL-02, OPS-07, OPS-03`).
- **Known gap for 20-13 to close:** `api/routes/jobs.py::cancel_job` does not yet catch `JobNotCancellableRunningError` -- if a `QUEUED_ONLY` job type is registered in `build_default_registry` before 20-13 lands the `except` clause mapping it to a 409, a RUNNING cancel of that type would surface as an unhandled 500 through the HTTP layer (the service-layer behavior itself is correct and tested; only the route-layer translation is missing, and no `QUEUED_ONLY` type is registered yet, so this is not user-reachable today).
- Full suite verified green at 771 passed (0 failed), up from the 746-pass pre-plan baseline (25 new tests from Tasks 1-2: 5 in the queued-only cancel/race group, 20 in the retry group; a post-review commit added assertions to an existing test without adding new test functions).
- `.venv/bin/ruff check src/trading_platform/orchestration` clean.

---
*Phase: 20-complete-operation-migration-safety-controls*
*Completed: 2026-09-28*

## Self-Check: PASSED

- FOUND (modified): src/trading_platform/orchestration/job_mutations.py
- FOUND (modified): tests/test_job_orchestration.py
- FOUND commit: 7ae7d88 (Task 1)
- FOUND commit: 8121210 (Task 2)
- FOUND commit: da557c3 (post-review test fix)

## Process Note

Commits `7ae7d88` (Task 1) and `8121210` (Task 2) -- both `feat(20-10): ...` -- were created without the `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>` trailer this session's attribution instructions require; only the `test(20-10)` post-review commit (`da557c3`) and the two `docs(20-10)` commits carry it. Per the git safety protocol's explicit preference for new commits over `--amend`, and the 19-08/20-01 summary precedent of disclosing rather than correcting a trailer gap via amend, this is disclosed rather than fixed. No work was lost; this is a metadata-only gap on two otherwise-correct commits.
