---
phase: 08-concurrency-guard
plan: 05
subsystem: infra
tags: [postgresql, advisory-lock, cli, concurrency, pytest, psycopg]

# Dependency graph
requires:
  - phase: 08-concurrency-guard (08-02)
    provides: ConcurrentRunLockedError, CONCURRENT_RUN_LOCK_EXIT_CODE, session_run_lock(), advisory_lock_key()
  - phase: 08-concurrency-guard (08-03)
    provides: find_stale_runs(), reclaim_stale_runs()
  - phase: 08-concurrency-guard (08-04)
    provides: run_paper_order_submission() reordered to lock-before-writes + running-row-first + reclaim-after-running-write
provides:
  - Worker CLI (submit-paper-orders, run-paper-session) maps ConcurrentRunLockedError to CONCURRENT_RUN_LOCK_EXIT_CODE with no traceback and a CLI-level WARNING naming the tuple
  - End-to-end automated proof that a crashed lock-holder's advisory lock auto-releases and the next run acquires cleanly + reclaims the leftover running row as STALE (LOCK-06)
affects: [09-reconciliation-rewrite, operator-runbooks, scheduler-integration]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "CLI commands that call a lock-guarded service function catch the service's typed lock-denial exception once, at the command layer, and translate it to a reserved SystemExit code via one shared helper rather than duplicating try/except bodies."
    - "Crash simulation in tests: acquire a Postgres session-level advisory lock on a raw, non-pooled psycopg connection, then close that connection directly (no explicit unlock) to prove OS/Postgres-level auto-release, as distinct from testing the application's own cleanup path."

key-files:
  created: [tests/test_concurrency_guard_e2e.py]
  modified: [src/trading_platform/worker/__main__.py]

key-decisions:
  - "Added a single shared `_handle_concurrent_run_lock_denied()` helper in worker/__main__.py used by both submit-paper-orders and run-paper-session, rather than duplicating the WARNING-log + stderr-print + SystemExit sequence in each command function."
  - "Test A (exit-code) in the e2e suite exercises only submit-paper-orders directly (per the plan's Task 2 action text); run-paper-session shares the identical wrapper/helper and CONCURRENT_RUN_LOCK_EXIT_CODE constant, so its exit-code behavior is covered by code-path symmetry rather than a duplicate integration test."
  - "Test B's crash simulation reuses the exact non-pooled-raw-connection pattern established in 08-02's test_concurrency_guard.py (acquire lock on a raw psycopg connection, close it without pg_advisory_unlock) but adds a real, durable pre-existing `running` StrategyRun row and a real run_paper_order_submission() call afterward, proving the LOCK-06 crash/restart guarantee end-to-end rather than just at the lock-primitive level."

requirements-completed: [LOCK-01, LOCK-06]

# Metrics
duration: ~20min
completed: 2026-07-13
---

# Phase 8 Plan 5: CLI Exit-Code Mapping + Crash/Restart Proof Summary

**Worker CLI now exits with a dedicated non-zero code (3) and no traceback when another run holds the (strategy, session) lock, and a new end-to-end test proves a crashed lock-holder's connection drop auto-releases the lock so the next run acquires cleanly and reclaims the leftover `running` row as STALE.**

## Performance

- **Duration:** ~20 min
- **Tasks:** 2
- **Files modified:** 2 (1 modified, 1 created)

## Accomplishments
- Both `submit-paper-orders` and `run-paper-session` worker CLI commands now catch `ConcurrentRunLockedError`, log a CLI-level WARNING naming the tuple and command, print a concise stderr line, and exit with `CONCURRENT_RUN_LOCK_EXIT_CODE` (3) with no traceback — while a successful run still exits 0.
- `tests/test_concurrency_guard_e2e.py` proves, against a real migrated Postgres database: (Test A) a held lock forces `submit-paper-orders` to exit code 3 with zero DB writes and zero paper orders; (Test B) a crashed lock-holder (raw connection force-closed without unlocking) auto-releases the advisory lock, and a subsequent `run_paper_order_submission()` call for the same tuple acquires cleanly, reclaims the 40-minute-old leftover `running` row to `STALE` with a `paper_run_reclaimed_stale` audit `ExecutionEvent`, and reaches `SUCCEEDED`.
- Full repo test suite (169 tests) passes with no regressions.

## Task Commits

Each task was committed atomically:

1. **Task 1: Map ConcurrentRunLockedError to the reserved exit code in the worker CLI** - `e6f7acb` (feat)
2. **Task 2: Crash/restart e2e — auto-release + clean reacquire + stale reclaim** - `907bd65` (test)

**Plan metadata:** (this commit)

## Files Created/Modified
- `src/trading_platform/worker/__main__.py` - Imports `ConcurrentRunLockedError`/`CONCURRENT_RUN_LOCK_EXIT_CODE`; both paper commands wrap their service call in `try/except ConcurrentRunLockedError`; new shared `_handle_concurrent_run_lock_denied()` helper emits a WARNING (`paper_command_lock_denied`), prints a stderr line, and raises `SystemExit(CONCURRENT_RUN_LOCK_EXIT_CODE)`.
- `tests/test_concurrency_guard_e2e.py` - New self-provisioned-Postgres e2e test file: Test A (CLI exit-code under lock contention), Test B (crash-release + clean reacquire + stale reclaim, proving LOCK-06 end-to-end).

## Decisions Made
See `key-decisions` in frontmatter above.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

Phase 8 (Concurrency Guard) is now fully complete: at most one side-effecting run per `(strategy_id, session_date)` is enforced via a non-blocking Postgres advisory lock acquired before any broker call or state-affecting write (LOCK-01/02), the running row is the literal first persisted write after lock acquisition (LOCK-03), stale `running` rows past a configurable timeout are detectable in one query and cleanly, auditably reclaimed (LOCK-04/05), and the crash-release + clean-reacquire guarantee is proven end-to-end (LOCK-06). Both worker CLI entrypoints that can trigger a side-effecting run (`submit-paper-orders`, `run-paper-session`) surface lock contention as a dedicated, scriptable exit code. No blockers for Phase 9 (reconciliation rewrite), which can build on this lock boundary without re-deriving it.

---
*Phase: 08-concurrency-guard*
*Completed: 2026-07-13*

## Self-Check: PASSED

- FOUND: src/trading_platform/worker/__main__.py
- FOUND: tests/test_concurrency_guard_e2e.py
- FOUND: .planning/phases/08-concurrency-guard/08-05-SUMMARY.md
- FOUND commit: e6f7acb
- FOUND commit: 907bd65
