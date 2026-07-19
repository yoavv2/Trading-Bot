---
phase: 17-job-framework
plan: 04
subsystem: infra
tags: [sqlalchemy, postgresql, protocol, sanitization, python]

# Dependency graph
requires:
  - phase: 17-01
    provides: "Job/JobLog ORM models, progress columns, cancellation_requested_at, (job_id, sequence) unique constraint"
  - phase: 17-02
    provides: "Frozen JobContext Protocol surface (report_progress/log/is_cancellation_requested/raise_if_cancelled) and JobCancelledError"
provides:
  - "ProgressSnapshot value object -- validated, partial-update progress reporting (D-11/D-12/D-14)"
  - "DatabaseJobContext -- the concrete JobContext a handler receives; the write side of JOB-07 (progress + structured logs) and the D-08 cooperative-cancellation checkpoint"
  - "The single Job-log write path, routing every context dict through core.log_sanitizer.sanitize (D-13), with deterministic per-Job sequence ordering and log-volume safeguards (MAX_LOG_MESSAGE_CHARS/MAX_LOG_CONTEXT_BYTES)"
affects: [17-05, 17-06, 17-07, 17-08, 17-09]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Per-call short session_scope transactions (no session held across handler work) so progress/log writes are durable and API-visible while the handler is still running"
    - "with_for_update row lock on the parent Job to serialize (job_id, sequence) assignment across concurrent log writers, with one IntegrityError retry as a backstop"
    - "Public-surface equality assertion (inspect.getmembers minus underscore-prefixed names == the Protocol's member set) as a runtime JOB-04 boundary guard, restated as a dedicated test"

key-files:
  created:
    - src/trading_platform/jobs/progress.py
    - src/trading_platform/jobs/context.py
    - tests/test_job_context.py
  modified: []

key-decisions:
  - "_enforce_context_size_limit runs AFTER sanitize() so the persisted _original_bytes reflects the size of what would otherwise have been stored (post-redaction), not the raw handler-supplied size"
  - "is_cancellation_requested() caches only a True result in an instance flag; a False result is never cached, so a cancellation requested after the first safe-point check is still observed on the next call"
  - "ProgressSnapshot truncates an oversized step string to 255 chars via object.__setattr__ in __post_init__ (frozen dataclass) rather than raising, per the plan's explicit instruction that progress reporting must not fail on a long step description"
  - "Reused the exact local migrated-database fixture pattern 17-03's tests/test_job_lifecycle.py established (migrated_job_context_db) rather than importing a shared fixture, since no conftest.py exposes one yet"

requirements-completed: []  # Plan frontmatter lists [JOB-07, JOB-06], but this plan ships only the write-side/checkpoint half of each. JOB-07 also needs the read-only API routes (17-08); JOB-06 also needs the operator cancellation action path (17-06). Left Pending per the 17-01/17-03 precedent -- see "Requirements Frontmatter Discrepancy" below.

# Metrics
duration: ~25min
completed: 2026-07-19
---

# Phase 17 Plan 04: Job Execution Context (Progress, Logs, Cancellation Checkpoint) Summary

**DatabaseJobContext -- the concrete JobContext implementation handed to every Job handler -- ships partial-update progress reporting, sanitized deterministic structured logs with volume safeguards, and the D-08 cooperative-cancellation checkpoint, pinned by 11 tests against a real Postgres database.**

## Performance

- **Duration:** ~25 min
- **Tasks:** 3 completed
- **Files modified:** 3 (all created)

## Accomplishments
- `src/trading_platform/jobs/progress.py` defines `ProgressSnapshot` (`percent`/`step`/`current`/`total`, all optional): rejects a `percent` outside 0-100 and a `current > total` combination with `ValueError`, truncates an oversized `step` to 255 chars rather than raising, and exposes `is_empty()`/`to_dict()`. `apply_progress(job, snapshot, *, now)` performs a partial update -- only non-`None` fields are written, so reporting `step` alone never blanks a previously reported `percent` -- and is a documented no-op (`False`, no write) on an empty snapshot. `mark_completed(job, *, now)` is the SUCCEEDED-only path that writes 100; its docstring states it must never be called on FAILED/CANCELLED. D-14 (no pruning/TTL) is recorded in the module docstring and verified absent from the module's code.
- `src/trading_platform/jobs/context.py` defines `DatabaseJobContext`, the concrete `JobContext` implementation: `job_id`/`job_type` read-only properties, `payload` returned as a `MappingProxyType` so a handler cannot mutate the framework's copy. Its public surface is exactly the seven `JobContext` Protocol members -- no session, engine, or status setter is reachable, verified both by a static `inspect.getmembers` assertion and a runtime `isinstance(..., JobContext)` check in the test suite.
- `report_progress(...)` builds a `ProgressSnapshot` (whose own validation raises before any DB access), opens one short `session_scope`, loads the Job, and calls `apply_progress` -- each call is its own transaction, so progress is visible to API readers while the handler is still running (JOB-07's "during execution" clause).
- `log(...)` is the single Job-log write path: normalizes/validates `level` against `{debug, info, warning, error, critical}`; routes `context` through `trading_platform.core.log_sanitizer.sanitize` at exactly one call site (grep-verified); assigns `sequence` as one greater than the current per-Job max under a `with_for_update` row lock on the parent Job, retrying once on a lost-race `IntegrityError`; truncates `message` to `MAX_LOG_MESSAGE_CHARS = 4000`; and replaces an oversized `context` (serialized JSON > `MAX_LOG_CONTEXT_BYTES = 16384` bytes) with `{"_truncated": True, "_original_bytes": <n>}`.
- `is_cancellation_requested()` reads `Job.cancellation_requested_at` in a short read-only `session_scope`, caching only a confirmed `True` result (never a `False`) so a cancellation requested mid-handler is still observed. `raise_if_cancelled()` raises `JobCancelledError(job_id)` when true (D-08).
- `tests/test_job_context.py` (11 tests, all green against a real migrated Postgres database): partial progress-update semantics, out-of-range percent rejection with no row mutation, progress visibility while `RUNNING`, monotonic log sequence `[1, 2, 3]`, deterministic ordering under a forced identical `logged_at` timestamp, sanitized log context (raw secret absent, `[REDACTED]` present), unknown-level rejection, message/context truncation at exactly 4000 chars / the byte-limit marker, cancellation-request read reflecting a persisted request, `JobCancelledError` carrying the correct `job_id`, and the public-surface guard restated as a test.
- Full suite verified green: 369 passed, 0 failed (358 pre-existing + 11 new), no regressions.

## Task Commits

Each task was committed atomically:

1. **Task 1: ProgressSnapshot value object** - `2461d4d` (feat)
2. **Task 2: DatabaseJobContext -- the handler-facing execution context** - `a14b0bb` (feat)
3. **Task 3: Context behavior tests** - `85f353d` (test)

_Plan-metadata commit follows this SUMMARY.md's own creation._

## Files Created/Modified
- `src/trading_platform/jobs/progress.py` - `ProgressSnapshot`, `apply_progress`, `mark_completed`
- `src/trading_platform/jobs/context.py` - `DatabaseJobContext`, `MAX_LOG_MESSAGE_CHARS`, `MAX_LOG_CONTEXT_BYTES`
- `tests/test_job_context.py` - 11 JOB-07/D-08/D-11/D-13 behavior tests + local `migrated_job_context_db` fixture + `_seed_job`/`_make_context` helpers

## Decisions Made
- `_enforce_context_size_limit` is applied strictly after `sanitize()` in `log()`'s call sequence, so `_original_bytes` on a truncated record reflects the post-redaction size that would otherwise have been persisted -- not the raw, potentially-larger, pre-sanitization size.
- Kept the cancellation-request cache asymmetric (cache `True`, never cache `False`) per the plan's explicit instruction, so a tight safe-point-check loop avoids hammering the database once cancellation is confirmed, while never risking a stale `False` masking a request that arrives mid-loop.
- Reused 17-03's exact local `migrated_*_db` fixture pattern (create throwaway database, set env, `clear_settings_cache`/`clear_engine_cache`, `alembic upgrade head`, teardown via `pg_terminate_backend` + `DROP DATABASE`) rather than introducing a shared `conftest.py` fixture, consistent with the precedent that no shared fixture currently exists for this shape.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking, tooling] ruff format reformatted progress.py after it had already been committed in Task 1**
- **Found during:** Task 3, running the project's pre-commit format gate before the Task 3 commit
- **Issue:** `ruff format --check` flagged `src/trading_platform/jobs/progress.py` (a docstring/line-wrap reformat) alongside the new test file; the project's pre-commit hook is a merge-blocking gate.
- **Fix:** Ran `ruff format` on both files; re-verified the Task 1 acceptance criteria (validation behavior, `D-14` grep count, zero-pruning grep count) and the full `tests/test_job_context.py` suite green after reformatting.
- **Files modified:** `src/trading_platform/jobs/progress.py` (whitespace/line-wrap only, no logic change)
- **Verification:** `pytest tests/test_job_context.py -q` (11/11 green), `mypy` clean, full suite 369/0
- **Committed in:** `85f353d` (bundled with the Task 3 test commit, since it was discovered while preparing that commit)

---

**Total deviations:** 1 auto-fixed (1 blocking/tooling)
**Impact on plan:** Formatting-only; no behavior change. No scope creep.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Requirements Frontmatter Discrepancy

This plan's frontmatter lists `requirements: [JOB-07, JOB-06]`, but neither is fully satisfied by this plan alone:

- **JOB-07** ("progress and structured logs observable via the API during and after execution") -- this plan ships the write side only (`report_progress`, `log`, both durably committed per-call so they are visible to any reader mid-execution). The read-only API routes that make them *observable via the API* (`api/routes/jobs.py`) land in plan 17-08 per `17-PATTERNS.md`.
- **JOB-06** ("operator can cancel a queued or running Job... audited") -- this plan ships only the D-08 cooperative-cancellation *checkpoint* a handler polls (`is_cancellation_requested`/`raise_if_cancelled`). The operator-facing cancellation *action* path (persisting a cancellation request, the grace-period timeout, `jobs/cancellation.py`) lands in a later Phase 17 plan (17-06 per `17-PATTERNS.md`).

Per the 17-01/17-03/16-02/11-03 precedent in STATE.md (do not mark a requirement Complete until the behavior it describes is actually verifiable end-to-end), `requirements mark-complete` was deliberately skipped for both IDs. Both remain `Pending` in `REQUIREMENTS.md`; a blocker note has been added to STATE.md so a later Phase 17 plan (or `/gsd-transition`) marks each complete once its full behavior ships and is verified.

## Next Phase Readiness

- `DatabaseJobContext` is now importable from `trading_platform.jobs.context` for the queue/runner plan to construct and hand to `JobHandler.run()` -- it is the concrete object satisfying the `JobContext` Protocol frozen in 17-02.
- `apply_progress`/`mark_completed` are ready for the runner: `mark_completed` must be called on the `SUCCEEDED` path only, immediately before (or as part of) the `apply_job_transition` call to `SUCCEEDED` -- never on `FAILED`/`CANCELLED`, per D-12.
- `is_cancellation_requested()`/`raise_if_cancelled()` are ready for plan 17-06's cancellation-action path: that plan writes `Job.cancellation_requested_at` (the D-08 request), and this plan's context is what a running handler polls to observe and acknowledge it.
- `MAX_LOG_MESSAGE_CHARS`/`MAX_LOG_CONTEXT_BYTES` are importable module constants ready for plan 17-08's API layer to reference (e.g. documenting the truncation contract in a response schema).
- No code blockers identified for 17-05 onward. Full test suite holds at 369/0 passing.

---
*Phase: 17-job-framework*
*Completed: 2026-07-19*
