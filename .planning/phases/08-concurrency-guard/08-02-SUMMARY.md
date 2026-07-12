---
phase: 08-concurrency-guard
plan: 02
subsystem: database
tags: [postgresql, advisory-lock, concurrency, pg_try_advisory_lock, sqlalchemy]

# Dependency graph
requires:
  - phase: 08-01
    provides: STALE StrategyRunStatus enum value + externalized stale_run_timeout_minutes setting (not directly consumed by this plan, but part of the same phase's foundation)
provides:
  - "ConcurrentRunLockedError typed denial exception (strategy_id + session_date, class-assertable, human-readable __str__)"
  - "advisory_lock_key() deterministic BIGINT-safe key derivation from (strategy_id, session_date)"
  - "session_run_lock() context manager: non-blocking session-level pg advisory lock on one dedicated AUTOCOMMIT connection, held for the whole guarded region"
  - "CONCURRENT_RUN_LOCK_EXIT_CODE = 3 module constant"
  - "Proven crash-release guarantee: PostgreSQL auto-releases the advisory lock when the holding connection drops"
affects: [08-03, 08-04, 08-05]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Advisory-lock key derivation: blake2b(digest_size=8) over 'strategy_id:session_date_iso', interpreted as a signed big-endian 8-byte int -- deterministic, BIGINT-safe, no collision handling needed given single-user scope"
    - "Dedicated AUTOCOMMIT connection pattern for session-scoped pg_try_advisory_lock/pg_advisory_unlock, kept separate from pooled session_scope() connections used for the guarded region's own DB writes"

key-files:
  created:
    - src/trading_platform/services/concurrency_guard.py
    - tests/test_concurrency_guard.py
  modified: []

key-decisions:
  - "session_run_lock() sets isolation_level=AUTOCOMMIT on its dedicated connection so no idle transaction is held open around the two advisory-lock statements -- broker I/O and any guarded writes happen entirely outside this connection's scope, matching the plan's crash-release design (connection drop, not transaction rollback, releases the lock)."
  - "Test DB fixture for this plan skips the Alembic migration used by test_paper_execution.py's fixture: session_run_lock only calls pg_try_advisory_lock/pg_advisory_unlock, which are database-wide functions independent of any table, so no schema is needed."
  - "Crash-release test uses a raw psycopg connection (bypassing the SQLAlchemy pool) to hold the lock and then .close() it -- this guarantees the underlying TCP/backend session actually terminates, which is what proves Postgres's auto-release behavior; a pooled SQLAlchemy connection's close() only returns it to the pool without necessarily severing the backend session."

requirements-completed: [LOCK-01, LOCK-06]

# Metrics
duration: ~15min
completed: 2026-07-12
---

# Phase 8 Plan 02: Advisory-Lock Primitive Summary

**Greenfield `session_run_lock()` context manager providing a non-blocking PostgreSQL session-level advisory lock keyed on (strategy_id, session_date), with a typed denial exception and a proven crash-release guarantee.**

## Performance

- **Duration:** ~15 min
- **Tasks:** 2
- **Files modified:** 2 (both newly created)

## Accomplishments
- `ConcurrentRunLockedError` (frozen-dataclass `RuntimeError` subclass) carrying `strategy_id`/`session_date`, with a human-readable `__str__`, assertable by class per the codebase's custom-exception convention.
- `advisory_lock_key()` deterministic, BIGINT-safe key derivation (blake2b digest over the canonical `strategy_id:session_date` string, interpreted as a signed 64-bit int).
- `session_run_lock()` context manager: opens one dedicated AUTOCOMMIT connection, non-blockingly `pg_try_advisory_lock`s the key, yields on success, and in `finally` explicitly `pg_advisory_unlock`s + closes on normal exit -- while a dropped/crashed connection lets PostgreSQL auto-release the lock server-side (no reliance on the `finally` block for that path).
- `CONCURRENT_RUN_LOCK_EXIT_CODE = 3` module constant, distinct from argparse's usage exit code (2).
- 10 tests total: 6 pure unit tests (key determinism, key variance by session_date, BIGINT range, error message, exception-class assertability, exit-code constant) plus 4 Postgres integration tests proving: same-tuple contention raises the typed error immediately (no blocking), a different `session_date` acquires concurrently without contention, the lock releases cleanly on normal `with`-block exit, and a lock held by a connection that is force-closed (simulated crash) is auto-released by PostgreSQL so a fresh `session_run_lock()` acquires cleanly.

## Task Commits

Each task was committed atomically:

1. **Task 1: ConcurrentRunLockedError + advisory_lock_key + exit-code constant** - `6c235b2` (feat)
2. **Task 2: session_run_lock() context manager + contention + crash-release tests** - `50de304` (feat)

## Files Created/Modified
- `src/trading_platform/services/concurrency_guard.py` - `CONCURRENT_RUN_LOCK_EXIT_CODE`, `advisory_lock_key()`, `ConcurrentRunLockedError`, `session_run_lock()` context manager (105 lines)
- `tests/test_concurrency_guard.py` - unit tests (key derivation, typed error) + Postgres integration tests (contention, disjoint-tuple concurrency, release-on-exit, crash-release) with a self-contained, unmigrated temp-database fixture (223 lines)

## Decisions Made
- AUTOCOMMIT isolation on the dedicated lock-holding connection (see `key-decisions` above) -- keeps the connection free of any idle transaction, so the crash-release mechanism is purely "connection drop releases session-level advisory locks," not entangled with transaction rollback semantics.
- Test database fixture intentionally skips Alembic migration since no table access is needed for this plan's tests -- named `advisory_lock_db` (not `migrated_*`) to keep the fixture's docstring/name honest about what it actually does.
- Crash-release test drives the "crashed" connection via raw `psycopg.connect()` outside the SQLAlchemy pool, ensuring `.close()` actually terminates the backend session (a pooled `Connection.close()` merely returns it to the pool and would not reliably prove the crash-release guarantee).

## Deviations from Plan

None - plan executed exactly as written. Both tasks' `<verify>` commands (`pytest tests/test_concurrency_guard.py -x -k "key or error"` after Task 1; `pytest tests/test_concurrency_guard.py -x` after Task 2) passed on first run with no fixes required.

## Issues Encountered
None.

## User Setup Required
None - no external service configuration required. Tests require a locally reachable PostgreSQL instance (already required by other integration tests in this repo, e.g. `tests/test_paper_execution.py`); the local Postgres@14 instance was already running and reachable.

## Next Phase Readiness
`session_run_lock()` is ready to be acquired at the submission entrypoint (`run_paper_order_submission`) in 08-04, per the Phase 8 context's lock-scope decision. 08-03 (stale-run detection/reclaim) and 08-04/08-05 build directly on this primitive; no blockers.

---
*Phase: 08-concurrency-guard*
*Completed: 2026-07-12*

## Self-Check: PASSED

- FOUND: src/trading_platform/services/concurrency_guard.py
- FOUND: tests/test_concurrency_guard.py
- FOUND: 6c235b2 (Task 1 commit)
- FOUND: 50de304 (Task 2 commit)
