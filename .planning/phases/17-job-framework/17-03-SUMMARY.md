---
phase: 17-job-framework
plan: 03
subsystem: infra
tags: [sqlalchemy, postgresql, state-machine, python]

# Dependency graph
requires:
  - phase: 17-01
    provides: "Job/JobDependency/JobEvent/JobLog ORM models and the closed JobStatus/JobFailureReason/JobCancellationCause vocabulary"
provides:
  - "apply_job_transition -- the single guarded writer of Job.status, backed by an 8-edge closed transition table with absorbing terminal states"
  - "IllegalJobTransition + rejected-transition audit persistence (every accepted or rejected transition writes exactly one JobEvent row)"
  - "D-01/D-03/D-09 outcome_uncertain and infrastructure-failure-vs-cancellation guards enforced in code, not left to caller discipline"
  - "D-12 progress-preservation guarantee: apply_job_transition never writes progress_percent/progress_step/progress_current/progress_total/progress_updated_at"
affects: [17-04, 17-05, 17-06, 17-07, 17-08, 17-09]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Closed nested-dict transition table + IllegalTransition RuntimeError + resolve_transition_target(...) -> Status | None, adapted from services/execution/transition.py's order-lifecycle pattern"
    - "with_for_update=True row lock on the single-writer transition function so concurrent workers serialize on the Job row instead of racing"
    - "Caller-owns-the-transaction-boundary convention (flush, never commit), matching reclaim_stale_runs's convention in services/stale_runs.py"

key-files:
  created:
    - src/trading_platform/jobs/lifecycle.py
    - tests/test_job_lifecycle.py
  modified: []

key-decisions:
  - "D-01 defensive default only covers WORKER_LOST/LEASE_EXPIRED (auto-filling JobFailureReason if the caller omitted it); CANCELLATION_TIMEOUT requires the caller to pass failure_reason=JobFailureReason.CANCELLATION_TIMEOUT explicitly, matching the plan's literal task-5 wording which names only the first two events for auto-default"
  - "outcome_uncertain is force-set True for WORKER_LOST/LEASE_EXPIRED/CANCELLATION_TIMEOUT regardless of caller input (D-03/D-09), and otherwise only written when the request explicitly supplies a non-None value -- an omitted request field never overwrites the Job's existing outcome_uncertain"
  - "tests/test_job_lifecycle.py replicates the migrated-database fixture pattern locally (migrated_job_lifecycle_db) rather than importing migrated_database across test modules, since no conftest.py currently shares it -- same precedent test_stale_run_reclaim.py set in Phase 8"
  - "Illegal-transition and CANCELLED+failure_reason tests catch the raised exception INSIDE the open session_scope with-block (pytest.raises nested inside, not wrapping, the with-block) so the already-flushed rejected JobEvent audit row is committed by session_scope's normal exit path rather than rolled back by its exception handler"

requirements-completed: []  # JOB-01 is a multi-plan requirement (D-01 through D-12 span 17-03 through 17-07); this plan ships the code-level closed-transition-table guarantee but JOB-06's operator cancellation action, JOB-05's dependency cascade, and JOB-07's observation surface are later plans' scope. Left Pending per the 17-01/16-02/11-03 precedent (do not mark complete until the requirement's full behavior is verifiable end-to-end).

# Metrics
duration: ~20min
completed: 2026-07-19
---

# Phase 17 Plan 03: Job Lifecycle Transition Guard Summary

**`apply_job_transition` -- the single guarded writer of `Job.status`, backed by an 8-edge closed transition table with absorbing terminal states, per-transition audit rows, and code-level D-01/D-03/D-09/D-12 enforcement -- pinned by 9 tests against a real Postgres database.**

## Performance

- **Duration:** ~20 min
- **Tasks:** 2 completed
- **Files modified:** 2 (both created)

## Accomplishments
- `src/trading_platform/jobs/lifecycle.py` defines the complete, closed `_LEGAL_TRANSITIONS` table (exactly 8 edges: `QUEUED`+`CLAIMED`->`RUNNING`, `QUEUED`+`CANCELLED`->`CANCELLED`, and 6 edges out of `RUNNING`), with `SUCCEEDED`/`FAILED`/`CANCELLED` as absorbing terminal states carrying zero outgoing edges -- there is no edge back to `QUEUED` anywhere in the table, so no automatic requeue or retry of a crashed Job is representable (D-02).
- `apply_job_transition(session, *, job_id, request)` is the single authorized writer of `Job.status`: it row-locks the Job (`with_for_update=True`), resolves the legal target, and either mutates the row + appends an `ACCEPTED` `JobEvent` or appends a `REJECTED` `JobEvent` (with `to_status=None`) and raises `IllegalJobTransition` -- the rejected row is persisted, not discarded, satisfying the audit half of JOB-01.
- D-01 enforced in code: `WORKER_LOST`/`LEASE_EXPIRED` auto-default their `failure_reason` when the caller omits it, and any request that would land on `CANCELLED` while carrying a `failure_reason` raises `ValueError` -- infrastructure failure can never be persisted as an operator cancellation.
- D-03/D-09 enforced in code: `WORKER_LOST`, `LEASE_EXPIRED`, and `CANCELLATION_TIMEOUT` force `outcome_uncertain=True` on the Job row regardless of what the caller supplied.
- D-12 enforced in code: `apply_job_transition` never assigns to any of the five progress columns; a terminal transition leaves a Job's last-reported progress exactly as it was.
- `tests/test_job_lifecycle.py` (9 tests, all green against a real migrated Postgres database): legal `QUEUED`->`RUNNING` and `QUEUED`->`CANCELLED` transitions, a subsequent-transition-from-terminal-state rejection, the illegal-transition rejected-audit-row proof, `WORKER_LOST`/`LEASE_EXPIRED` landing on `FAILED` (never `CANCELLED`) with `outcome_uncertain is True`, `CANCELLATION_TIMEOUT` landing on `FAILED` with `outcome_uncertain is True`, the `CANCELLED`+`failure_reason` `ValueError` guard, D-12 progress preservation across a terminal transition, and the closed-table coverage assertion (`set(_LEGAL_TRANSITIONS) == set(JobStatus)`, exactly 8 total edges).
- Verified `grep -rn "\.status = JobStatus" src/trading_platform/ | grep -v lifecycle.py` returns no results -- `lifecycle.py` is confirmed the sole writer of `Job.status` in the codebase.
- Full suite verified green: 358 passed (1 test outside this plan's scope, `test_alpaca_execution.py::test_run_paper_order_submission_persists_idempotent_paper_orders`, errored only under full-suite parallel-load contention and passed cleanly in isolation -- matches the pre-existing, previously-documented `pg_terminate_backend`/InsufficientPrivilege flakiness noted in STATE.md's Blockers section, not a regression from this plan).

## Task Commits

Each task was committed atomically:

1. **Task 1: Closed transition table and guarded transition function** - `eb9f3bf` (feat)
2. **Task 2: Transition enforcement tests** - `9f35489` (test)

## Files Created/Modified
- `src/trading_platform/jobs/lifecycle.py` - `_LEGAL_TRANSITIONS`, `IllegalJobTransition`, `resolve_transition_target`, `JobTransitionRequest`/`JobTransitionResult`, `apply_job_transition`
- `tests/test_job_lifecycle.py` - 9 JOB-01 transition enforcement tests + local `migrated_job_lifecycle_db` fixture + `_seed_job` helper

## Decisions Made
- Kept the D-01 auto-default narrow (only `WORKER_LOST`/`LEASE_EXPIRED`) per the plan's literal task-5 wording rather than also defaulting `CANCELLATION_TIMEOUT`'s `failure_reason` -- the corresponding test passes `failure_reason=JobFailureReason.CANCELLATION_TIMEOUT` explicitly in the request, consistent with how a real cancellation-timeout caller (plan 17-06) would construct the request.
- `outcome_uncertain` is only written when either forced by the three D-01/D-03/D-09 event types or explicitly supplied non-`None` by the caller -- an omitted field never clobbers whatever the Job row already had, avoiding an accidental silent reset to `False`.
- The two exception-path tests (`test_illegal_transition_raises_and_persists_rejected_event`, `test_queued_to_cancelled_is_legal_and_terminal`'s second half) nest `pytest.raises` inside the `session_scope` `with`-block rather than wrapping the whole block, so `session_scope`'s own `try/except` never sees the exception and proceeds to its normal-exit `session.commit()` -- this is what makes the already-flushed rejected `JobEvent` audit row durable instead of rolled back.

## Deviations from Plan

None - plan executed exactly as written. `ruff format` applied one whitespace-only reformat to `lifecycle.py` (a docstring line-length wrap) as part of the project's pre-commit format gate; re-verified all acceptance-criteria commands (transition-table assertions, zero-`progress_*`-writes grep, zero-`session.commit()`-calls grep, mypy) green after reformatting -- standard tooling, not a plan/behavior change.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- `apply_job_transition` is now importable from `trading_platform.jobs.lifecycle` for every remaining Phase 17 plan: the queue/lease claim loop (17-04) calls it for `QUEUED`->`RUNNING` and worker-loss/lease-expiry `FAILED` landings; dependency cascade cancellation (17-05) calls it for `CANCELLED` with `JobCancellationCause.DEPENDENCY_FAILED`/`DEPENDENCY_CANCELLED`; the cancellation action path (17-06) calls it for both the atomic `QUEUED` cancel (D-07) and the cooperative `RUNNING` cancel (D-08) plus the `CANCELLATION_TIMEOUT` path (D-09); progress reporting (17-07) must NOT call this function for progress writes, since D-12 is enforced by this function's deliberate omission of the progress columns.
- No code blockers identified for 17-04 onward. Full test suite holds at 358/359 passing (the one non-passing result is a documented pre-existing environmental flake outside this plan's scope, confirmed to pass in isolation).

---
*Phase: 17-job-framework*
*Completed: 2026-07-19*
