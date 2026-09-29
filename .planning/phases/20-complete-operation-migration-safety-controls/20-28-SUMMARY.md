---
phase: 20-complete-operation-migration-safety-controls
plan: 28
subsystem: operator-controls
tags: [audit, timestamps, postgres, clock_timestamp, gap-closure]
requires:
  - phase: 20-complete-operation-migration-safety-controls
    provides: "20-25 edits to 20-CONTEXT.md (ordering only; no code dependency)"
provides:
  - "Every OPERATOR_CONTROL run satisfies completed_at >= started_at (trip, reset, enable, disable; changed and unchanged; including under lock waits)"
  - "D-11a in 20-CONTEXT.md: control audit timestamps come from one DB clock read inside the mutating transaction, after the row lock"
affects: [operator-controls, kill-switch, UAT tests 1, 2, 4, 5 audit trail]
tech-stack:
  added: []
  patterns: ["single clock_timestamp() read after the FOR UPDATE lock, normalized to UTC, feeds all audit timestamps"]
key-files:
  created: [tests/test_operator_control_timestamps.py]
  modified: [src/trading_platform/services/operator_controls.py, .planning/phases/20-complete-operation-migration-safety-controls/20-CONTEXT.md]
key-decisions:
  - "clock_timestamp() (not now()) after the row lock: it is >= the transaction start now() that StrategyRun.started_at records, so ordering holds by construction, including when the lock wait is long"
  - "No migration and no CHECK constraint (open user decision, deferred)"
requirements-completed: [CTRL-01, CTRL-02]
duration: ~20min
completed: 2026-09-29
---

# Phase 20 Plan 28: Control Audit Timestamp Ordering Summary

Closes UAT gap 4. `changed_at = datetime.now(UTC)` was taken in Python before the transaction opened, while `started_at` is Postgres `now()` (transaction start), so all 25 live control runs had `completed_at < started_at`. Both `_set_strategy_status` and `_set_kill_switch_state` now read one `clock_timestamp()` from the DB inside the transaction, immediately after the row lock, via the new `_db_clock_now(session)` helper (raises `ControlWriteError` on a naive value, returns UTC).

## Delivered

- `services/operator_controls.py`: `_db_clock_now`; the two pre-transaction `datetime.now(UTC)` calls removed (grep gate: 0 occurrences); `sqlalchemy.func` imported as `sa_func` because the decorator's local parameter is named `func`. Existing uses of `changed_at` (completed_at, event_at, last_changed_at on change only, `result_summary.changed_at`) unchanged and now receive the DB value.
- `tests/test_operator_control_timestamps.py` (13 tests, Postgres): 8 parametrized ordering cases (4 actions x changed/unchanged) asserting `completed_at >= started_at`, `event_at == completed_at`, `fromisoformat(changed_at) == completed_at`, and a `+00:00` suffix; kill switch `last_changed_at == completed_at` and `last_change_run_id == run.id` on change; `last_changed_at` untouched on an unchanged trip; break-glass report ordering; two lock-wait cases (system_controls row and strategies row) that observe `pg_stat_activity wait_event_type='Lock'`, hold 0.3 s, and assert `completed_at - started_at >= 0.3 s`. The holder connection is released in `finally`.
- `20-CONTEXT.md`: D-11a inserted between D-11 and D-12; the D-08a text from 20-25 preserved.

## Verification

- RED first: against pre-fix code 11 of 13 tests failed for the intended reason (completed_at earlier than started_at, lock-wait delta below 0.3 s); the two `last_changed_at` tests pass on both sides, as expected.
- After the fix: the 4 targeted suites (timestamps, operator_controls, control_routes, kill_switch_trip_cli) 75 passed; ruff clean.
- Full backend suite: 1076 passed. `tests/test_orchestration_boundaries.py`: 47 passed (read-only run).
- TS_OK, DOCS_OK and USER_FILES_UNCHANGED all printed. No alembic changes.

## Deviations from Plan

None - plan executed exactly as written. Test environment: the Postgres the test config points at was reachable and no environment-related failures occurred.

## Deferred / open decisions

- Migration 0022 adding `CHECK (completed_at IS NULL OR completed_at >= started_at) NOT VALID` on `strategy_runs` (enforces the rule for new rows without rewriting the 25 legacy audit rows). Still an open user decision (rewrite audit history, NOT VALID, or no constraint). This plan adds no migration and no constraint; nothing here claims DB-level enforcement. The 25 legacy inverted rows are left as they are.

## Residual

- Other run types (non-OPERATOR_CONTROL) also mix the Python clock with DB `now()` for their timestamps. They are ordered by accident (Python time is taken after the transaction begins). Out of scope for this plan and not changed.
- The break-glass/API report strings (`started_at`/`completed_at`) are isoformat of the DB-session-timezone values, so their UTC offset follows the DB session time zone; only `result_summary.changed_at` is guaranteed UTC (+00:00). Ordering comparisons are unaffected.

## Commits

- cfed4e4: Task 1, D-11a in 20-CONTEXT.md
- 09ebbf8: Task 2, `_db_clock_now` fix and Postgres timestamp regression tests

## Threat Flags

None.

## Self-Check: PASSED
