---
phase: 17-job-framework
plan: 06
subsystem: infra
tags: [sqlalchemy, postgresql, cooperative-cancellation, python]

# Dependency graph
requires:
  - phase: 17-01
    provides: "Job/JobEvent ORM models, cancellation_requested_at/_by/_reason/_acknowledged_at/_cause columns, JobEventType.CANCELLATION_REQUESTED/CANCELLED/CANCELLATION_TIMEOUT"
  - phase: 17-03
    provides: "apply_job_transition -- the single guarded writer of Job.status, including the QUEUED+CANCELLED and RUNNING+CANCELLED/CANCELLATION_TIMEOUT legal edges"
  - phase: 17-04
    provides: "is_cancellation_requested/raise_if_cancelled -- the handler-side checkpoint a RUNNING Job polls to observe a pending request"
provides:
  - "request_cancellation -- atomic QUEUED->CANCELLED under a row lock (D-07) and cooperative RUNNING-request persistence that does not transition status (D-08)"
  - "acknowledge_cancellation -- RUNNING->CANCELLED only after a handler acknowledges, rejecting fabricated cancellations with no pending request"
  - "find_cancellation_timeout_job_ids / sweep_cancellation_timeouts -- grace-period overrun reported honestly as FAILED/cancellation_timeout/outcome_uncertain=true, never as a false CANCELLED (D-09)"
  - "CANCELLATION_GRACE_SECONDS -- the default 300s cooperative grace period, overridable by plan 17-07's worker loop"
affects: [17-07, 17-08, 17-09]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Row-locked (with_for_update=True) atomic cancel-vs-claim serialization on the Job row, mirroring lifecycle.py's own locking convention"
    - "Status-preserving JobEvent (to_status=None) appended directly (not via apply_job_transition) for CANCELLATION_REQUESTED -- the one Phase 17 audit event that does not accompany a status change"
    - "Mutable (non-frozen) dataclass exception -- a frozen dataclass exception cannot be raised through a nested @contextmanager-based session_scope because contextlib attaches a traceback to the raised instance post-construction"

key-files:
  created:
    - src/trading_platform/jobs/cancellation.py
    - tests/test_job_cancellation.py
  modified: []

key-decisions:
  - "JobNotCancellableError is a mutable @dataclass (not frozen=True) despite strategies/registry.py's UnknownStrategyError precedent being frozen -- raising a frozen-dataclass exception through session_scope's @contextmanager triggered dataclasses.FrozenInstanceError when contextlib tried to set __traceback__ on the instance during propagation. Discovered live via pytest, not anticipated from the plan text; fixed and re-verified all 15 tests plus every acceptance-criteria grep green afterward."
  - "CancellationResult.already_terminal is always False on every return path in this plan -- request_cancellation raises JobNotCancellableError for a terminal Job rather than returning a result carrying already_terminal=True. The field is retained per the plan's literal dataclass definition for forward-compatible callers, but no current code path sets it True."
  - "Tasks 1 and 2 were implemented together in the same new file (both add functions to jobs/cancellation.py) and committed in a single commit, mirroring the 17-05 precedent for two tasks sharing one new file; Task 3 (tests) is its own commit."

requirements-completed: []  # JOB-06 left Pending -- see 'Requirements Note' below

# Metrics
duration: ~20min
completed: 2026-07-19
---

# Phase 17 Plan 06: Cooperative Job Cancellation Summary

**`jobs/cancellation.py` ships all three JOB-06 cancellation outcomes -- atomic QUEUED cancel, cooperative RUNNING cancel gated on handler acknowledgement, and an honest FAILED/cancellation_timeout landing when a handler ignores the grace period -- pinned by 15 tests against a real Postgres database.**

## Performance

- **Duration:** ~20 min
- **Tasks:** 3 completed
- **Files modified:** 2 (both created)

## Accomplishments

- `request_cancellation` row-locks the target Job (`with_for_update=True`) and branches on status: QUEUED cancels atomically via `apply_job_transition` inside the same transaction a concurrent worker would use to claim it, so a cancelled QUEUED Job can never subsequently be claimed (D-07, proven directly by `test_cancelled_queued_job_is_never_claimable` against `find_ready_job_ids`); RUNNING persists the request facts and appends a `CANCELLATION_REQUESTED` `JobEvent` with `to_status=None` WITHOUT transitioning status (D-08) -- this event type is deliberately absent from `lifecycle.py`'s transition table, so it is appended directly rather than through `apply_job_transition`.
- A second `request_cancellation` call against a RUNNING Job with an already-pending request is a no-op (`accepted=False`) that does not overwrite the first requester's identity, reason, or `requested_at` -- pinned by `test_second_cancellation_request_does_not_overwrite_first_requester`.
- `acknowledge_cancellation(session, *, job_id)` takes an open, caller-owned session (the shape plan 17-07's runner will call it with) and transitions RUNNING->CANCELLED via `apply_job_transition` only when a pending request already exists on the Job row; otherwise it raises `JobNotCancellableError`, so a handler can never fabricate a cancellation nobody asked for (T-17-06-02).
- `find_cancellation_timeout_job_ids`/`sweep_cancellation_timeouts` implement D-09: a single-query detector (RUNNING + non-null `cancellation_requested_at` + null `cancellation_acknowledged_at` + past the grace cutoff) and an idempotent sweep that lands each match on FAILED with `failure_reason=CANCELLATION_TIMEOUT` and `outcome_uncertain=True` -- never CANCELLED. Idempotency mirrors `reclaim_stale_runs`/`cascade_dependency_outcome`: once a Job leaves RUNNING it no longer matches the predicate, so a second sweep is a safe no-op (`test_sweep_is_idempotent`).
- D-12 progress preservation holds across the timeout path (`test_timeout_path_preserves_last_progress`) because `apply_job_transition` never writes progress columns, unchanged from 17-03.
- Every cancellation-related transition carries the full D-10 audit record (`requested_by`, `reason`, `requested_at`, `acknowledged_at`, `terminal_cause`) on both the `Job` row and the append-only `JobEvent`, verified end-to-end by `test_running_job_becomes_cancelled_only_after_acknowledgement`.
- `tests/test_job_cancellation.py` (15 tests, all green against a real migrated Postgres database): atomic QUEUED cancel with full field assertions, never-claimable proof, cooperative RUNNING request persistence, acknowledgement-gated CANCELLED with full D-10 record, rejection of an acknowledgement with no pending request, terminal-Job rejection parametrized over SUCCEEDED/FAILED/CANCELLED, second-request non-overwrite, grace-period-overrun FAILED landing (with an explicit `is not JobStatus.CANCELLED` assertion), within-grace-period non-sweep, acknowledged-Job non-sweep, sweep idempotency, D-12 progress preservation, and the standalone detector query.
- Verified `grep -c "\.status = "` on `cancellation.py` returns 0 (all status changes route through `apply_job_transition`), `with_for_update=True` appears twice (the QUEUED-atomic lock in `request_cancellation` and the lock in `acknowledge_cancellation`), `session.commit()` returns 0 (caller owns the transaction boundary throughout), and no `requeue`/`retry` function or bare `JobStatus.QUEUED` assignment exists anywhere in the module (D-02). Confirmed the whole-tree grep from the plan's `<verification>` block: `cancellation_requested_at = ` originates only in `jobs/cancellation.py` (every other module only reads the field).
- Full suite verified green: 399 passed (384 baseline + 15 new), zero regressions. `ruff check`, `ruff format --check`, and `mypy` all pass clean on both new files.

## Task Commits

Each task was committed atomically (Tasks 1 and 2 both add functions to the same new file, `cancellation.py`, and were written together before either was verified -- see Deviations, mirroring the 17-05 precedent):

1. **Tasks 1+2: Cancellation request/acknowledgement + grace-period timeout sweep** - `e77f837` (feat)
2. **Task 3: Cancellation behavior tests** - `2f2beae` (test)

## Files Created/Modified

- `src/trading_platform/jobs/cancellation.py` - `CANCELLATION_GRACE_SECONDS`, `CancellationResult`, `JobNotCancellableError`, `request_cancellation`, `acknowledge_cancellation`, `find_cancellation_timeout_job_ids`, `sweep_cancellation_timeouts`
- `tests/test_job_cancellation.py` - 15 JOB-06/D-07/D-08/D-09/D-10/D-12 tests + local `migrated_job_cancellation_db` fixture + `_seed_job`/`_seed_running_job` helpers

## Decisions Made

- `JobNotCancellableError` is a plain (non-frozen) `@dataclass` exception rather than mirroring `strategies/registry.py`'s frozen-dataclass `UnknownStrategyError` precedent exactly. A frozen dataclass overrides `__setattr__` to reject all post-init attribute assignment, including the interpreter's own `__traceback__` attachment when the exception propagates out through `session_scope`'s `@contextmanager`-decorated generator -- this raised `dataclasses.FrozenInstanceError` instead of the intended `JobNotCancellableError` on every terminal-Job cancellation test. Removing `frozen=True` keeps the dataclass-generated `__init__` (so the acceptance-criteria grep for literal `.status = ` source text still returns 0) while allowing normal exception propagation.
- `CancellationResult.already_terminal` is defined per the plan's literal dataclass shape but is always `False` on every path this plan returns from, since a terminal-Job cancellation attempt raises `JobNotCancellableError` rather than returning a result -- the field exists for forward-compatible callers, not because any current branch sets it `True`.
- Reused the exact `migrated_job_dependencies_db`/`migrated_job_lifecycle_db` local-fixture pattern (`migrated_job_cancellation_db`) rather than a shared `conftest.py` fixture, consistent with every prior Phase 17 test module.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Frozen-dataclass exception broke propagation through `session_scope`**
- **Found during:** Task 3, first `pytest` run of `test_cancel_terminal_job_is_rejected`
- **Issue:** `JobNotCancellableError` was initially defined as `@dataclass(frozen=True)` (mirroring `UnknownStrategyError`'s precedent literally). Raising it inside `session_scope`'s `@contextmanager`-based `with`-block caused `contextlib`'s exception-reraise machinery to attempt `exc.__traceback__ = traceback`, which a frozen dataclass's overridden `__setattr__` rejects, raising `dataclasses.FrozenInstanceError` in place of the intended exception.
- **Fix:** Removed `frozen=True`, keeping the `@dataclass`-generated `__init__` (so the field-assignment acceptance-criteria grep still finds no literal `.status = ` source text) while allowing normal attribute mutation during exception propagation.
- **Files modified:** `src/trading_platform/jobs/cancellation.py`
- **Verification:** `pytest tests/test_job_cancellation.py -q` (15/15 green, including the three parametrized terminal-status cases), re-ran every acceptance-criteria grep (`.status = ` == 0, `with_for_update=True` == 2, `session.commit()` == 0, requeue/retry == 0) and the whole-tree `cancellation_requested_at = ` origin check -- all green after the fix.
- **Committed in:** `2f2beae` (bundled with the Task 3 test commit, since it was discovered while preparing that commit)

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** Behavior-preserving exception-class fix; no scope creep. All plan-specified functions, signatures, and acceptance criteria delivered as written.

## Issues Encountered

None beyond the frozen-dataclass fix documented above, which was found and resolved within Task 3's own verification loop.

## User Setup Required

None - no external service configuration required.

## Requirements Note

This plan's frontmatter lists `requirements: [JOB-06]`, and this plan ships the full cancellation *mechanism* the requirement describes -- atomic QUEUED cancel, cooperative RUNNING cancel gated on acknowledgement, honest grace-period-timeout failure, and the complete D-10 audit trail, all unit-tested end-to-end against a real database.

However, per the 17-01/17-03/17-04/17-05 precedent (do not mark a requirement Complete until an operator can actually exercise the behavior end-to-end, not just call the underlying function from a test), `requirements mark-complete` was deliberately skipped for JOB-06 in this plan. Nothing in the codebase yet invokes `request_cancellation` from an operator-facing surface (that is Phase 18/19's orchestration-API scope), and `acknowledge_cancellation`/`sweep_cancellation_timeouts` are only ever called directly by tests today -- the worker loop that would call them during real RUNNING-Job execution is plan 17-07's scope. JOB-06 remains `Pending` in `REQUIREMENTS.md`; it should be marked Complete once 17-07 wires the acknowledgement/sweep calls into the real claim/execution loop and (per the same precedent) that path is verified, or at `/gsd-transition`.

## Next Phase Readiness

- `request_cancellation`, `acknowledge_cancellation`, `find_cancellation_timeout_job_ids`, `sweep_cancellation_timeouts`, and `CANCELLATION_GRACE_SECONDS` are all importable from `trading_platform.jobs.cancellation` for plan 17-07's worker loop: the runner calls `acknowledge_cancellation` when a handler raises `JobCancelledError` (from `jobs/context.py`'s checkpoint) or otherwise stops after observing a request, and calls `sweep_cancellation_timeouts` on its poll cadence (optionally overriding `grace_seconds`) to fail Jobs whose handlers never acknowledged.
- `request_cancellation` is ready for whichever Phase 18/19 operator-facing surface submits cancellation requests -- it needs only `job_id`/`requested_by`/optional `reason`.
- No code blockers identified for 17-07 onward. Full suite holds at 399/0.

---
*Phase: 17-job-framework*
*Completed: 2026-07-19*

## Self-Check: PASSED

Both created files verified present on disk (`src/trading_platform/jobs/cancellation.py`, `tests/test_job_cancellation.py`); all three commit hashes (e77f837, 2f2beae, 28e938a) verified present in git log.
