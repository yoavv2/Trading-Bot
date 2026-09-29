---
status: diagnosed
trigger: "UAT-20 observation: OPERATOR_CONTROL StrategyRuns have completed_at < started_at"
created: 2026-09-29
updated: 2026-09-29
goal: find_root_cause_only
---

## Symptoms

expected: every completed StrategyRun satisfies completed_at >= started_at.
actual: the break-glass CLI report (run 06f2f707) had started_at 13:17:27.777293+03 and completed_at 13:17:27.751735+03, so it completed about 26ms before it started.

## Evidence (live Homebrew Postgres, READ ONLY, 2026-09-29)

| run_type | completed rows | completed_at < started_at | min(completed_at - started_at) |
|---|---|---|---|
| operator_control | 25 | **25 (100%)** | -93 ms |
| backtest | 9 | 0 | +23 ms |
| risk_evaluation | 2 | 0 | +60 ms |
| dry_bootstrap | 1 | 0 | +35 ms |

- The violation is systematic, not intermittent. Every operator_control run ever written is inverted, including the two pre-Phase-20 rows from 2026-07-08 (worker_cli).
- strategy_runs has no CHECK constraint on the ordering (pg_constraint shows no contype='c').
- Nothing reads run duration today: there is no duration or elapsed calculation in src/ or console/src. The impact is audit-record integrity, not a crash.

## Root cause

Two clocks disagree on ordering in `services/operator_controls.py`:

1. `changed_at = datetime.now(UTC)` is taken in **Python, before the transaction begins**:
   - `_set_strategy_status`, line 293
   - `_set_kill_switch_state`, line 468
2. `StrategyRun.started_at` is filled by `server_default=func.now()` (`db/models/strategy_run.py:86-90`). PostgreSQL `now()` is the **transaction start time**, so it is always later than a Python timestamp captured before `session_scope()` opened the transaction.
3. `strategy_run.completed_at = changed_at` is set at lines 343 and 524. The same pre-transaction value is also written to:
   - `ExecutionEvent.event_at` (lines 354, 537)
   - `SystemControl.last_changed_at` (line 503)
   - `result_summary.changed_at`

So completed_at < started_at by construction, whenever the clocks agree. With a remote DB (`.env` has a Neon `DATABASE_URL`), clock skew can make the gap arbitrarily large in either direction.

Other run types avoid this only by accident. They also mix the Python clock (completed_at) with the DB clock (started_at), but real work sits between the two readings. They carry the same latent skew risk against a remote DB.

Origin: 0c1800f (06-03) and 54b2555 (07-03), which predates Phase 20. Phase 20 made this code the primary synchronous HTTP control path (CTRL-01/02) and the break-glass CLI path. UAT Tests 1, 2, 4 and 5 assert these audit rows exist.

## Verdict

This is a **real invariant violation**: 100% of operator_control rows break completed_at >= started_at. It is not a display artifact. It is pre-existing, low severity, and cheap to fix. Phase 20 owns the control path, so fixing it here is proportionate.

## Suggested fix direction (for the gap planner)

- Use one clock per control transaction, taken from the DB. Inside the transaction, after the row lock (`_load_global_kill_switch(for_update=True)` / `_ensure_locked_strategy_record`), read `changed_at = session.execute(select(func.clock_timestamp())).scalar_one()`.
  - Use it for completed_at, event_at, last_changed_at and result_summary.changed_at.
  - `clock_timestamp()` read after the lock is always >= `now()`, the transaction start, so completed_at >= started_at holds by construction, and also under lock waits (the UAT 5a in-flight case).
- Regression tests on a real Postgres, following the existing throwaway-DB pattern, for trip/reset/enable/disable, changed and unchanged:
  - `run.completed_at >= run.started_at`
  - `event.event_at == run.completed_at`
  - for kill-switch changed=true: `system_controls.last_changed_at == run.completed_at`
  - a lock-wait variant: hold FOR UPDATE on the control row in a second connection, release after N ms, and assert the ordering still holds
- Optional DB enforcement (the user prefers DB invariants): migration 0022 adds `CHECK (completed_at IS NULL OR completed_at >= started_at) NOT VALID` on strategy_runs. NOT VALID enforces new and updated rows without rewriting the 25 legacy audit rows. Needs a user decision: rewriting audit history versus NOT VALID versus no constraint.

## Spec / decision impact

No existing Phase 20 decision states the timestamp source. Add a one-line invariant to the control-path decisions (D-10/D-11 area in 20-CONTEXT.md): "control audit timestamps come from a single DB clock read taken inside the mutating transaction, after the row lock".
