---
phase: 17-job-framework
fixed_at: 2026-07-20T00:00:00Z
review_path: .planning/phases/17-job-framework/17-REVIEW.md
iteration: 1
findings_in_scope: 5
fixed: 5
skipped: 0
status: all_fixed
---

# Phase 17: Code Review Fix Report

**Fixed at:** 2026-07-20T00:00:00Z
**Source review:** .planning/phases/17-job-framework/17-REVIEW.md
**Iteration:** 1

**Summary:**
- Findings in scope: 5 (CR-01, CR-02, WR-01, WR-02, WR-03)
- Fixed: 5
- Skipped: 0
- Info findings (IN-01, IN-02): out of scope for this pass, not addressed.

**Verification:** All fixes verified by re-read (Tier 1) plus targeted and full
test runs against a live PostgreSQL database. `python -m pytest tests/ -q`
passed with **452 passed** after all fixes. Four new regression tests were
added (two for CR-01, two for CR-02) plus two for the warnings (WR-01, WR-03).

## Fixed Issues

### CR-01: Terminal FAILED/CANCELLED transitions skip the dependency cascade (D-04)

**Files modified:** `src/trading_platform/jobs/cancellation.py`, `tests/test_job_cancellation.py`
**Commit:** ea49989
**Status:** fixed: requires human verification (concurrency/cascade logic)
**Applied fix:** Added `cascade_dependency_outcome(session, terminal_job_id=job_id)`
after the terminal transition in two paths, mirroring the existing
`reclaim_lost_jobs` pattern in `queue.py`:
- In `sweep_cancellation_timeouts`, after the `CANCELLATION_TIMEOUT` transition
  inside the loop (the live worker-loop path).
- In `request_cancellation`'s QUEUED branch, after the immediate `CANCELLED`
  transition (same open, row-locked transaction).

To avoid adding a new module-level `cancellation -> dependencies` import
coupling (dependencies never imports cancellation), the cascade helper is
imported at function scope in both functions. Two regression tests assert a
QUEUED dependent is CANCELLED with cause `DEPENDENCY_FAILED` (timeout path) and
`DEPENDENCY_CANCELLED` (immediate-cancel path), and is no longer returned by
`find_ready_job_ids`.

### CR-02: `run_worker_loop` does not guard `execute_job`; a concurrent sweep crashes the worker

**Files modified:** `src/trading_platform/jobs/runner.py`, `tests/test_job_runner.py`
**Commit:** 88213f0
**Status:** fixed: requires human verification (concurrency race handling)
**Applied fix:** Two layers, matching the REVIEW's fix option (b) plus the
"Additionally, wrap..." guidance:
1. In `execute_job`, wrapped all three terminal-write branches
   (success / cancelled / error) in a `try/except (IllegalJobTransition,
   JobNotCancellableError)`. When a concurrent sweep/reclaim terminalized the
   Job mid-execution, the terminal write now logs
   `job_runner_concurrently_terminalized` and returns the current (already
   correct) terminal status instead of propagating — treated exactly like the
   existing lost-lease path. `LookupError` still propagates.
2. In `run_worker_loop`, wrapped the `execute_job` call in `except Exception`
   as a genuine last-resort net (via `logger.exception`) so no single Job can
   crash the poll loop. `jobs_executed` is still incremented so `max_jobs`
   bounds hold and the loop cannot spin forever on one pathological Job.

Two regression tests exercise the success-path terminalization
(`IllegalJobTransition`) and the cancelled-path terminalization
(`JobNotCancellableError`), each asserting `execute_job` returns the honest
already-terminal status without raising. Both patch `HEARTBEAT_SECONDS` large so
the race window (terminalized but `lease_lost` unset) is what is exercised.

### WR-01: `report_progress` does not guard `Job.status` (D-12)

**Files modified:** `src/trading_platform/jobs/context.py`, `tests/test_job_context.py`
**Commit:** 0cdb079
**Status:** fixed
**Applied fix:** Added a `JobStatus` import and a status guard in
`report_progress`: if the loaded Job is not `RUNNING`, the method returns a
cooperative no-op, preserving the terminalized Job's last progress snapshot
(D-12). A parametrized regression test (SUCCEEDED/FAILED/CANCELLED) asserts the
progress write is skipped and the last snapshot is preserved.

### WR-02: Heartbeat thread swallows `renew_lease` exceptions, silently killing renewals

**Files modified:** `src/trading_platform/jobs/runner.py`
**Commit:** 0f4fcce
**Status:** fixed
**Applied fix:** Wrapped the `renew_lease` call in `_heartbeat_loop` in
`try/except Exception`. A transient DB error now logs `job_runner_heartbeat_error`
and continues to the next tick (rather than terminating the heartbeat thread
silently), so a single transient blip does not abandon a healthy Job. If the
error persists, the lease lapses and a sweep reclaims the Job — now tolerated by
the CR-02 terminal-write guard — rather than crashing.

No dedicated regression test was added: a heartbeat-thread DB exception is not
deterministically reproducible without brittle thread-timing hooks; the fix is
a straightforward log-and-continue guard.

### WR-03: `event_code` / `handler_type` not length-validated before insert

**Files modified:** `src/trading_platform/jobs/context.py`, `tests/test_job_context.py`
**Commit:** 5c65856
**Status:** fixed
**Applied fix:** Added `MAX_LOG_EVENT_CODE_CHARS` / `MAX_LOG_HANDLER_TYPE_CHARS`
(both 64, matching the `String(64)` columns) and truncated `event_code` and
`handler_type` to those widths before constructing the `JobLog`, consistent with
the existing `message` truncation and the truncate-don't-reject policy. This
prevents a `DataError` (string-right-truncation) at commit, which — unlike an
`IntegrityError` — is not caught by the sequence-retry loop. A regression test
logs an over-length event_code and handler_type and asserts both are truncated
to 64 chars without raising.

---

_Fixed: 2026-07-20T00:00:00Z_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
