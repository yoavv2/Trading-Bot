---
phase: 07-correctness-kernel
plan: 03
subsystem: operator-safety
tags: [kill-switch, system-control, operator-controls, paper-execution, postgres, audit-trail, cli]

requires:
  - phase: 07-correctness-kernel
    provides: Closed order lifecycle kernel (07-01) and deterministic idempotent intent identity (07-02)
provides:
  - Durable global kill-switch state persisted in `system_controls`
  - Explicit audited trip/reset operator actions separate from per-strategy enable/disable
  - Batch-entry, pre-submit, and mid-run enforcement inside paper execution
  - Read-only and reconciliation continuity while submissions are halted
  - Worker CLI actions (`trip-kill-switch`, `reset-kill-switch`, `show-kill-switch`)
  - Operator status surfacing of kill-switch state and recent blocked paper executions
affects:
  - phase 08 advisory-lock hardening
  - phase 09 reconciliation rewrite
  - future dashboard operator surfaces

tech-stack:
  added: []
  patterns:
    - "System-level operator state lives in `system_controls` separate from per-strategy status"
    - "Submission paths read the persisted kill switch at batch entry and before each broker call"
    - "Blocked paper executions are recorded as failed `strategy_runs` plus durable `execution_events` instead of silent aborts"
    - "Operator CLI exposes global kill-switch actions alongside per-strategy controls"

key-files:
  created:
    - src/trading_platform/db/models/system_control.py
    - alembic/versions/0015_phase7_global_kill_switch.py
  modified:
    - src/trading_platform/db/models/__init__.py
    - src/trading_platform/services/operator_controls.py
    - src/trading_platform/services/paper_execution.py
    - src/trading_platform/services/operator_reads.py
    - src/trading_platform/services/operator_status.py
    - src/trading_platform/worker/__main__.py
    - tests/test_operator_controls.py
    - tests/test_paper_execution.py
    - tests/test_db_migrations.py

key-decisions:
  - "The global kill switch is a dedicated `system_controls` row with its own `KillSwitchState` enum, so the emergency brake does not overload per-strategy `Strategy.status`"
  - "Kill-switch trip/reset flows create `OPERATOR_CONTROL` `strategy_runs` plus typed `execution_events` so every mutation is durably audited with actor, reason, and trigger source"
  - "Paper submission reads the persisted switch at batch entry and again before every candidate, so mid-run trips stop subsequent submissions without a restart"
  - "While tripped, reconciliation, recovery, sync_paper_state, and operator reads continue running so operators retain investigation surfaces"
  - "Blocked submissions land as failed paper-execution runs with structured `blocked_reason`/`action` fields instead of silent aborts or missing rows"
  - "The CLI exposes `trip-kill-switch`, `reset-kill-switch`, and `show-kill-switch` as `operator-control` actions so the global halt and per-strategy controls share one operator entry point"

patterns-established:
  - "Durable platform-wide operator state persists in `system_controls` and is the authoritative restart-safe source"
  - "Submission boundaries consult the persisted kill switch at every broker side-effect boundary"
  - "Blocked submission outcomes are first-class paper-execution runs with explicit `blocked_reason` and `kill_switch` snapshots in result summaries"
  - "Operator status reports carry the kill-switch snapshot in the top-level summary plus recent blocked-submission drilldown context"

requirements-completed:
  - SAFE-01
  - SAFE-02
  - SAFE-03
  - SAFE-04
  - SAFE-05

duration: 48 min
completed: 2026-04-20
---

# Phase 07 Plan 03: Global Kill Switch Summary

**Durable global submission halt with audited trip/reset operator actions, batch and mid-run enforcement in paper execution, read-only continuity while tripped, and kill-switch visibility across CLI, operator reads, and status surfaces.**

## Performance

- **Duration:** 48 min
- **Started:** 2026-04-19 (Task 1)
- **Completed:** 2026-04-20
- **Tasks:** 3
- **Files modified:** 11

## Accomplishments

- Added a durable `system_controls` row and `KillSwitchState` enum so the global kill switch persists across restarts and stays separate from per-strategy status (Task 1).
- Built `OperatorControlService.trip_kill_switch` / `reset_kill_switch` flows that create audited `OPERATOR_CONTROL` runs plus typed `kill_switch_trip`/`kill_switch_reset` execution events (Task 1).
- Enforced the kill switch at batch entry and before every candidate submission in `run_paper_order_submission`, including mid-run enforcement that halts remaining candidates without a restart (Task 2).
- Preserved reconciliation, in-flight recovery, `sync_paper_state`, and operator reads while the switch is tripped so investigation and read-only flows continue uninterrupted (Task 2).
- Recorded blocked submissions as failed paper-execution runs with explicit `blocked_reason`, `action`, and `kill_switch` snapshots instead of silent aborts, with structured `paper_execution_blocked` log events (Task 2).
- Exposed global kill-switch control in the worker CLI via new `operator-control {trip-kill-switch|reset-kill-switch|show-kill-switch}` actions and added `render_kill_switch_report` output (Task 3).
- Extended `OperatorReadService` with `get_kill_switch_state` and `list_blocked_paper_executions` and added `kill_switch` plus `recent_blocked_paper_executions` to `OperatorStatusReport`, including markdown rendering (Task 3).

## Task Commits

1. **Task 1: Persist global kill-switch state and audit actions** - `54b2555` (feat)
2. **Task 2: Enforce the switch at session entry and before every broker submission** - `6cfdfa0` (feat)
3. **Task 3: Surface kill-switch state in CLI and operator inspection reads** - `83eb1c2` (feat)

## Files Created/Modified

- `src/trading_platform/db/models/system_control.py` - Adds the `system_controls` table model, `KillSwitchState` enum, and `GLOBAL_KILL_SWITCH_NAME` constant.
- `alembic/versions/0015_phase7_global_kill_switch.py` - Migration that creates `system_controls` and seeds the global kill switch in `armed` state with `system_bootstrap` metadata.
- `src/trading_platform/db/models/__init__.py` - Re-exports the new system-control symbols for package consumers.
- `src/trading_platform/services/operator_controls.py` - Adds `KillSwitchStateSnapshot`, `KillSwitchControlReport`, trip/reset/get flows, and `load_kill_switch_state`/`render_kill_switch_report` helpers; keeps per-strategy enable/disable intact and separate.
- `src/trading_platform/services/paper_execution.py` - Checks the persisted switch at batch entry and before each candidate; records blocked and blocked-mid-run paper-execution runs with structured summaries while leaving reconciliation, recovery, and read flows running.
- `src/trading_platform/services/operator_reads.py` - Adds `get_kill_switch_state` and `list_blocked_paper_executions` so operator surfaces can read the persisted switch and blocked-submission runs.
- `src/trading_platform/services/operator_status.py` - Carries `kill_switch` and `recent_blocked_paper_executions` in `OperatorStatusReport`, including markdown lines and structured-log context.
- `src/trading_platform/worker/__main__.py` - Extends `operator-control` with `trip-kill-switch`, `reset-kill-switch`, and `show-kill-switch` actions plus a dedicated kill-switch handler.
- `tests/test_operator_controls.py` - Covers trip/reset persistence, restart-safe reads, CLI parser/round-trip, operator-reads blocked-execution listing, and status surface kill-switch fields.
- `tests/test_paper_execution.py` - Adds pre-submit halt, manual-reset-only semantics, mid-run enforcement, reconciliation continuity, and read-only `sync_paper_state` continuity tests plus `ExplodingExecutionService` / `MidRunTrippingExecutionService` harnesses.
- `tests/test_db_migrations.py` - Asserts the `system_controls` table shape and seeded default row.

## Decisions Made

- The global kill switch is intentionally a separate `system_controls` row with its own `KillSwitchState` enum rather than an extra value on `Strategy.status`; the emergency brake must never conflate with per-strategy disable semantics.
- Trip/reset are audited operator events that write `strategy_runs` (type `operator_control`, scope `global_kill_switch`) plus typed `execution_events` (`kill_switch_trip` / `kill_switch_reset`) with explicit actor/reason/trigger-source metadata.
- Paper submission reads the switch snapshot at batch entry and re-reads it before each candidate so a mid-run trip takes effect on the next submission boundary without a restart or external signaling.
- Only `reset_kill_switch` clears the halt; a tripped switch stays tripped across repeated submission attempts until an operator explicitly resets it.
- Reconciliation, in-flight recovery, `sync_paper_state`, and operator reads keep running while the switch is tripped so operators can still investigate and reconcile platform state safely.
- Blocked submissions land as `StrategyRunStatus.FAILED` paper-execution runs with structured `blocked_reason=global_kill_switch_tripped`, `action=blocked_global_kill_switch` (or `blocked_mid_run_global_kill_switch`), and an embedded `kill_switch` snapshot, rather than being silently dropped.
- CLI access stays under one `operator-control` entry point with action choices; kill-switch actions ignore `--strategy` because the switch is global.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical] Added explicit Task 2 tests that previous WIP had left unasserted.**
- **Found during:** Task 2 resume
- **Issue:** The interrupted Task 2 WIP defined `ExplodingExecutionService` and `MidRunTrippingExecutionService` harness classes but did not register any tests asserting (a) pre-submit halt, (b) mid-run enforcement, (c) reconciliation continuity, (d) read-only `sync_paper_state` continuity, or (e) manual-reset-only semantics. The plan's verify step required all five.
- **Fix:** Added five new paper-execution tests that use those harnesses (plus `FakeExecutionService` after reset) to assert each behavior end-to-end, including DB persistence of blocked runs and kill-switch details.
- **Files modified:** tests/test_paper_execution.py
- **Verification:** `PYTHONPATH=src .venv/bin/pytest tests/test_paper_execution.py tests/test_operator_controls.py tests/test_db_migrations.py -q` → `37 passed`.
- **Committed in:** `6cfdfa0` (Task 2 commit)

**2. [Rule 3 - Blocking] Added `submitted_intents` tracking to `ExplodingExecutionService`.**
- **Found during:** Task 2 resume (reconciliation continuity test)
- **Issue:** `ExplodingExecutionService` only raised when called, but the new reconciliation continuity test asserts zero submissions by reading `execution_service.submitted_intents`; the class had no such attribute.
- **Fix:** Added an `__init__` that initializes `submitted_intents: list[OrderIntent] = []` without changing the raise-on-submit behavior.
- **Files modified:** tests/test_paper_execution.py
- **Verification:** Reconciliation continuity test now asserts `submitted_intents == []` and passes.
- **Committed in:** `6cfdfa0` (Task 2 commit)

---

**Total deviations:** 2 auto-fixed (1 missing critical, 1 blocking)
**Impact on plan:** Both auto-fixes were required to satisfy the plan's Task 2 verification contract and did not change planned behavior — only made existing behavior actually verified.

## Issues Encountered

- The previous executor was interrupted by an API quota limit mid-Task 2, leaving uncommitted WIP on disk (the implementation in `paper_execution.py` and the test harness classes). Resume flow preserved that WIP, finished the missing assertions, and committed Task 2 atomically before Task 3.
- Full verification required elevated local PostgreSQL access because sandboxed localhost TCP access is blocked in this environment (same as phases 04 and 06). With elevated access the slice passed on the first run after Task 3 landed.

## User Setup Required

- None.

## Next Phase Readiness

- Phase 07 (Correctness Kernel) is now complete: closed order lifecycle (07-01), deterministic idempotent intents (07-02), and durable global kill switch (07-03) are all in place and verified.
- Phase 08 (advisory-lock hardening) can now layer on top of a restart-safe operator kill switch without redefining submission boundaries.
- Phase 09 (reconciliation rewrite) inherits the blocked-run result-summary shape (`blocked_reason`, `action`, `kill_switch`) and can rely on reconciliation running continuously even while submissions are halted.

## Self-Check: PASSED

- Verified `PYTHONPATH=src .venv/bin/pytest tests/test_operator_controls.py tests/test_paper_execution.py tests/test_db_migrations.py -q` → `37 passed`.
- Verified `PYTHONPATH=src .venv/bin/python -m trading_platform.worker operator-control --help` shows the updated control surface with `trip-kill-switch`, `reset-kill-switch`, and `show-kill-switch` actions.
- Verified all three task commits exist on disk: `54b2555` (Task 1), `6cfdfa0` (Task 2), `83eb1c2` (Task 3).
- Verified key files exist on disk: `src/trading_platform/db/models/system_control.py`, `alembic/versions/0015_phase7_global_kill_switch.py`, updated `operator_controls.py`, `paper_execution.py`, `operator_reads.py`, `operator_status.py`, `worker/__main__.py`.

---
*Phase: 07-correctness-kernel*
*Completed: 2026-04-20*
