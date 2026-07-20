---
phase: 17-job-framework
reviewed: 2026-07-20T00:00:00Z
depth: standard
files_reviewed: 22
files_reviewed_list:
  - src/trading_platform/jobs/lifecycle.py
  - src/trading_platform/jobs/cancellation.py
  - src/trading_platform/jobs/dependencies.py
  - src/trading_platform/jobs/queue.py
  - src/trading_platform/jobs/runner.py
  - src/trading_platform/jobs/context.py
  - src/trading_platform/jobs/progress.py
  - src/trading_platform/jobs/contracts.py
  - src/trading_platform/jobs/registry.py
  - src/trading_platform/jobs/__init__.py
  - src/trading_platform/db/models/job.py
  - src/trading_platform/db/models/job_dependency.py
  - src/trading_platform/db/models/job_event.py
  - src/trading_platform/db/models/job_log.py
  - src/trading_platform/db/models/__init__.py
  - alembic/versions/0018_phase17_job_framework.py
  - src/trading_platform/services/job_reads.py
  - src/trading_platform/api/routes/jobs.py
  - src/trading_platform/api/dependencies.py
  - src/trading_platform/api/app.py
  - src/trading_platform/worker/commands/run_jobs.py
  - src/trading_platform/worker/parser.py
findings:
  critical: 2
  warning: 3
  info: 2
  total: 7
status: issues_found
---

# Phase 17: Code Review Report

**Reviewed:** 2026-07-20T00:00:00Z
**Depth:** standard
**Files Reviewed:** 22
**Status:** issues_found

## Summary

Phase 17 implements a DB-backed job queue with a closed lifecycle state machine, a claim/lease queue (`SELECT ... FOR UPDATE SKIP LOCKED`), a dependency DAG with submission-time cycle rejection, cooperative cancellation, and a restart-safe worker loop. The lifecycle guard (`apply_job_transition`) is disciplined: it is the sole writer of `Job.status`, takes a row lock, audits every accepted and rejected transition, and self-enforces invariants (forced `outcome_uncertain`, rejection of a `failure_reason` on a `CANCELLED` target) rather than trusting callers. The read API is clean from a security standpoint (see note below).

However, two correctness defects break core guarantees under concurrency:

1. The dependency-outcome cascade (the mechanism that prevents QUEUED dependents from being stranded behind a dead ancestor, D-04) is **not** invoked on two terminal-transition paths — most importantly the cancellation-timeout sweep that is live in the worker loop.
2. The worker loop does not guard `execute_job`, so a foreseeable concurrent transition (another worker's timeout sweep or lease reclaim while this worker's handler is still running) makes the terminal write raise `IllegalJobTransition` / `JobNotCancellableError`, crashing the worker process.

Three warnings and two info items follow.

## Critical Issues

### CR-01: Terminal FAILED/CANCELLED transitions skip the dependency cascade, stranding QUEUED dependents (D-04)

**File:** `src/trading_platform/jobs/cancellation.py:272-325` (primary, live) and `src/trading_platform/jobs/cancellation.py:98-199` (latent)
**Issue:**
`cascade_dependency_outcome` is the *only* mechanism preventing a QUEUED Job from being left stranded forever behind a dead dependency (D-04). It is invoked on every terminal write in `runner.py` (lines 130, 220, 236) and in `reclaim_lost_jobs` (`queue.py:218`) — but it is **missing** from two functions that also produce terminal states:

- `sweep_cancellation_timeouts` (`cancellation.py:305`) transitions a RUNNING Job to FAILED (`CANCELLATION_TIMEOUT`) and returns, never cascading. This path is **live**: `run_worker_loop` calls it on every poll (`runner.py:299`). A QUEUED Job depending on a Job that times out cancellation → FAILED will never satisfy `find_ready_job_ids` (which requires every dependency to reach SUCCEEDED), is never claimed, and is never cascade-cancelled. It is stranded permanently.
- `request_cancellation` (`cancellation.py:126-152`) transitions a QUEUED Job to CANCELLED immediately and returns, never cascading — same stranding for its dependents.

The asymmetry is the proof this is an omission, not a design choice: `reclaim_lost_jobs` and `sweep_cancellation_timeouts` are analogous "sweep a dead RUNNING Job to FAILED" functions, and only the former cascades. The module docstrings explicitly claim cascade runs "on every terminal FAILED/CANCELLED transition."

Reachability note: `request_cancellation` is not yet wired to any endpoint or CLI command in Phase 17 (the API is read-only; `worker/parser.py` has no cancel command; only tests call it), so its stranding is latent until Phase 18 wires operator cancellation. The `sweep_cancellation_timeouts` gap is live now.

**Fix:** Cascade after each terminal transition, mirroring `reclaim_lost_jobs`. In `sweep_cancellation_timeouts`, after the `apply_job_transition(... CANCELLATION_TIMEOUT ...)` call inside the loop:
```python
        cascade_dependency_outcome(session, terminal_job_id=job_id)
        swept.append(job_id)
```
In `request_cancellation`'s QUEUED branch, after the immediate transition (the session is already open and row-locked):
```python
            apply_job_transition(session, job_id=job_id, request=JobTransitionRequest(...))
            cascade_dependency_outcome(session, terminal_job_id=job_id)
            return CancellationResult(...)
```
Add regression tests asserting a QUEUED dependent is CANCELLED with cause `DEPENDENCY_FAILED` (timeout path) / `DEPENDENCY_CANCELLED` (immediate-cancel path).

### CR-02: `run_worker_loop` does not guard `execute_job`; a concurrent sweep/reclaim crashes the worker process

**File:** `src/trading_platform/jobs/runner.py:305-311` (loop) and `runner.py:201-237` (terminal writes)
**Issue:**
`execute_job`'s success / cancelled / error terminal writes run *outside* the handler `try/except` (lines 201-237) and call `apply_job_transition` / `acknowledge_cancellation`, which assume the Job is still RUNNING and owned by this worker. Under concurrency that assumption can be false:

Deterministic trigger (no heartbeat failure required): worker B polls and runs `sweep_cancellation_timeouts`, transitioning Job J — currently executing in worker A with an ignored cancellation request — from RUNNING to FAILED. The sweep does **not** clear J's lease or otherwise signal worker A. Worker A's handler then returns (or raises `JobCancelledError`) within the ≤`HEARTBEAT_SECONDS` (20s) window before its next `renew_lease` would detect the status change, so `lease_lost` is not set. Worker A then executes its terminal write:
- success path → `apply_job_transition(SUCCEEDED)` from status FAILED → `resolve_transition_target` returns None → `IllegalJobTransition` raised.
- cancelled path → `acknowledge_cancellation` sees status ≠ RUNNING → `JobNotCancellableError` raised.

`run_worker_loop` wraps `execute_job` in **no** `try/except` (lines 305-311), so the exception propagates out of the loop, through `run_jobs_command`, and terminates the worker process. A lease-reclaim during handler execution produces the same class of crash. The in-flight Job is already correctly terminal, so this is **not** data loss — the defect is a worker-process crash on a foreseeable race, which undercuts the "restart-safe worker loop" guarantee (one racing worker can crash another).

**Fix:** Make the terminal-write paths tolerant of a Job that was concurrently terminalized. Either (a) re-read status under the row lock and short-circuit when the Job is no longer RUNNING/owned (treat as lease-lost: log and return the current status without writing), or (b) catch `IllegalJobTransition` / `JobNotCancellableError` from the terminal writes in `execute_job` and convert them to the lease-lost return path. Additionally, wrap the `execute_job` call in `run_worker_loop` in a `try/except` that logs and continues, so no single Job can crash the loop.

## Warnings

### WR-01: `report_progress` does not guard `Job.status`, allowing progress writes onto a terminal Job (D-12)

**File:** `src/trading_platform/jobs/context.py:69-88`
**Issue:**
`report_progress` loads the Job and calls `apply_progress` with no status check. In the same race as CR-02 (a handler still running after a concurrent sweep/reclaim terminalized the Job), a `ctx.report_progress(...)` call writes `progress_percent` / `progress_updated_at` onto a Job that is already FAILED or CANCELLED. This violates the D-12 invariant the code repeatedly claims — `progress.py` and `lifecycle.py` state that FAILED/CANCELLED Jobs "preserve their last-reported progress untouched" and that progress is written "only while the Job is still RUNNING." It is inconsistent with the codebase's own enforce-don't-assume philosophy: `apply_job_transition` raises on illegal transitions and `lifecycle.py` forces `outcome_uncertain` itself rather than trusting callers, yet `report_progress` assumes its RUNNING precondition instead of enforcing it.
**Fix:** Guard the write under the loaded (ideally row-locked) Job:
```python
        with session_scope(self._settings) as session:
            job = session.get(Job, self._job_id)
            if job is None:
                raise ValueError(f"Job '{self._job_id}' does not exist.")
            if job.status is not JobStatus.RUNNING:
                return  # cooperative no-op; a terminalized Job keeps its last snapshot
            apply_progress(job, snapshot, now=now)
```

### WR-02: Heartbeat thread swallows `renew_lease` exceptions, silently killing all future renewals

**File:** `src/trading_platform/jobs/runner.py:145-149`
**Issue:**
`_heartbeat_loop` calls `renew_lease` with no exception handling. `renew_lease` opens its own `session_scope`, so a transient DB error raises out of the thread target and terminates the heartbeat thread. Thread exceptions do not propagate to the main thread, and `lease_lost` is only set on a clean `False` return — not on an exception. A single transient error during one heartbeat therefore stops all further renewals for that Job without any signal; the lease later lapses and a sweep reclaims the Job to FAILED while the handler is still running, feeding directly into the CR-02 crash. The `HEARTBEAT_SECONDS * 2 < LEASE_SECONDS` margin only protects against *missed* beats, not a *dead* heartbeat thread.
**Fix:** Wrap the `renew_lease` call in `try/except Exception`: log the error and either retry on the next tick or set `lease_lost` and return, so a lost heartbeat is an explicit, observable signal rather than silent death.

### WR-03: `event_code` / `handler_type` are not length-validated or truncated before insert

**File:** `src/trading_platform/jobs/context.py:90-146`
**Issue:**
`context.log` truncates `message` to `MAX_LOG_MESSAGE_CHARS` (line 104) but passes `event_code` (column `String(64)`) and `handler_type` (`String(64)`) through unbounded. A handler supplying an `event_code` longer than 64 chars triggers a `DataError` (string-right-truncation) at commit, which is **not** an `IntegrityError` and so is not caught by the sequence-retry loop (lines 147-149) — it propagates out of `ctx.log`, and if the handler does not catch it the whole Job fails. Log emission is documented to "never raise merely because a handler's step description ran long" (the truncate-don't-reject policy in `progress.py`); the same policy is not applied here.
**Fix:** Truncate `event_code` and `handler_type` to their column widths (`event_code[:64]`, `self._job_type[:64]`) before constructing the `JobLog`, consistent with the `message` truncation already in place.

## Info

### IN-01: `JobReadService.list_jobs` raises an uncaught `ValueError` on an unrecognized status

**File:** `src/trading_platform/services/job_reads.py:69`
**Issue:**
`JobStatus(resolved_filters.status)` raises `ValueError` for any string that is not a valid enum member. Via the HTTP route this is unreachable (FastAPI validates `status: JobStatus` in `get_job_read_filters`), but the module is explicitly documented as a "transport-agnostic read layer" intended for reuse. A non-HTTP caller passing an invalid status gets an unhandled `ValueError` (a 500 if surfaced through a future transport) rather than a clean not-found/empty result.
**Fix:** Validate/normalize the status at the service boundary and raise a typed, catchable error (or return an empty list) instead of letting the bare `ValueError` escape.

### IN-02: `capped_limit` has an upper bound but no lower bound

**File:** `src/trading_platform/services/job_reads.py:64`, `168`, `217`
**Issue:**
`capped_limit = min(limit, MAX_LIMIT)` caps the ceiling but not the floor. Via the API `limit` is guarded (`Query(..., ge=1, le=...)`), so this is currently safe, but a direct service caller passing `limit <= 0` reaches `.limit(0)` (returns nothing) or `.limit(-1)` (a driver error) at this public boundary.
**Fix:** Clamp both ends, e.g. `capped_limit = max(1, min(limit, MAX_LIMIT))`, so the service is safe independent of its caller.

## Note: read API security assessment (checked, clear)

The read routes (`api/routes/jobs.py`, `services/job_reads.py`) were checked specifically for injection and input-validation gaps and are clean: every query is SQLAlchemy-ORM parameterized (no string interpolation into SQL); `job_id` path params are typed `UUID` and `status`/`run_type` query params are typed enums, all validated by FastAPI before reaching the service; `job_type` is bound as a parameter; pagination bounds are enforced with `Query(ge=..., le=...)`. No SQL/command/path-traversal or authorization-bypass vector was found in the reviewed read surface. (Authentication/authorization for the read surface, if required, is a cross-cutting concern not introduced by this phase and out of scope for this review.)

---

_Reviewed: 2026-07-20T00:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
