---
phase: 07-correctness-kernel
plan: 01
subsystem: execution-kernel
tags: [paper-orders, state-machine, postgres, sqlalchemy, alembic, audit-trail]

requires:
  - phase: 05-paper-execution
    provides: Persisted paper orders, broker submission flow, and reconciliation inputs that Phase 7 hardens
  - phase: 06-analytics-and-apis
    provides: Operator-facing audit surfaces that now consume canonical local lifecycle state
provides:
  - Closed enum-backed paper-order lifecycle with append-only transition events
  - Single `apply_order_transition()` boundary with typed illegal-transition failures
  - Shared lifecycle vocabulary across paper submission and reconciliation paths
affects:
  - phase 07-02 idempotent intent pipeline
  - phase 07-03 global kill switch
  - operator order drilldowns and audit history

tech-stack:
  added: []
  patterns:
    - "`PaperOrder.status` is the latest projection over append-only `order_events`"
    - "Lifecycle changes are applied only through `services/order_state_machine.py`"
    - "DB-backed verification covers both migration compatibility and lifecycle behavior"

key-files:
  created:
    - alembic/versions/0013_phase7_order_state_kernel.py
    - src/trading_platform/db/models/order_event.py
    - src/trading_platform/services/order_state_machine.py
    - tests/test_order_state_machine.py
  modified:
    - src/trading_platform/db/models/__init__.py
    - src/trading_platform/db/models/paper_order.py
    - src/trading_platform/services/paper_execution.py
    - src/trading_platform/services/reconciliation.py
    - tests/test_db_migrations.py
    - tests/test_paper_execution.py
    - tests/test_execution_reconciliation.py

key-decisions:
  - "Closed the lifecycle around `OrderLifecycleState` and `OrderTransitionEventType` enums so execution and reconciliation share one vocabulary"
  - "Persist rejected transitions as `order_events` instead of dropping them so illegal transition attempts remain operator-visible"
  - "Kept the transition boundary DB-only and broker-I/O-free so later idempotency and kill-switch work can compose on it safely"

patterns-established:
  - "Paper-order state is audit-first: transition history is primary, row state is the projection"
  - "Execution and reconciliation must express broker changes as lifecycle events, not direct status mutation"
  - "Module-boundary regression coverage protects the kernel from future bypasses"

requirements-completed:
  - ORDER-01
  - ORDER-02
  - ORDER-03
  - ORDER-04
  - ORDER-05
  - ORDER-06

duration: multi-session
completed: 2026-04-19
---

# Phase 07 Plan 01: Order State Kernel Summary

**Closed paper-order lifecycle with append-only transition history and a single DB-only mutation boundary**

## Performance

- **Duration:** Multi-session execution
- **Started:** 2026-04-18T16:05:31+03:00
- **Completed:** 2026-04-19T11:15:45Z
- **Tasks:** 3
- **Files modified:** 11

## Accomplishments

- Replaced the freeform paper-order status surface with closed lifecycle enums and a migration that preserves existing paper-order rows while backfilling append-only `order_events`.
- Added `apply_order_transition()` plus `IllegalOrderTransition` so accepted and rejected lifecycle changes are both durable and test-visible.
- Routed paper submission and reconciliation flows through the lifecycle kernel and added regression coverage that fails if those services start mutating order lifecycle state directly again.

## Task Commits

1. **Task 1: Introduce enum-backed order lifecycle persistence and append-only order events** - `23c5e43` (feat)
2. **Task 2: Implement the single transition boundary and illegal-transition enforcement** - `cf6fa67` (feat)
3. **Task 3: Route existing paper-execution and reconciliation flows through the kernel** - `a98211d` (feat)

## Files Created/Modified

- `alembic/versions/0013_phase7_order_state_kernel.py` - Migrates `paper_orders.status` to the closed enum vocabulary and backfills `order_events`.
- `src/trading_platform/db/models/order_event.py` - Defines lifecycle state, event, and outcome enums plus the append-only `OrderEvent` model.
- `src/trading_platform/db/models/paper_order.py` - Projects the current lifecycle state with enum-backed persistence and an `order_events` relationship.
- `src/trading_platform/services/order_state_machine.py` - Owns the legal transition map, typed failures, and durable event persistence.
- `src/trading_platform/services/paper_execution.py` - Applies local lifecycle changes through the kernel during submission planning and broker updates.
- `src/trading_platform/services/reconciliation.py` - Uses the same lifecycle boundary for broker recovery and sync-failure transitions.
- `tests/test_order_state_machine.py` - Verifies legal transitions, rejected transitions, and the no-broker-I/O boundary.
- `tests/test_db_migrations.py` - Verifies migration compatibility, enum coverage, and order-event backfill behavior.

## Decisions Made

- Promoted the local order lifecycle to a closed enum set so later idempotency and kill-switch work can target stable state names instead of legacy freeform strings.
- Treated rejected transitions as first-class audit records because operator visibility matters as much for illegal attempts as for accepted broker updates.
- Kept the kernel at the DB boundary only; broker adapters continue to describe external state, but they do not decide local lifecycle legality.

## Deviations from Plan

- None. The plan landed as written across three task commits.

## Issues Encountered

- The executor sessions completed the code work but did not write the summary artifact, so the closeout documentation was created manually after spot-checking the task commits.
- Full verification required elevated local PostgreSQL access because sandboxed localhost TCP connections are blocked in this environment.

## User Setup Required

- None.

## Next Phase Readiness

- Phase `07-01` is complete and verified.
- Phase `07-02` can now build deterministic intent identity on top of the shared lifecycle kernel without reopening order-state persistence.

## Self-Check: PASSED

- Verified `PYTHONPATH=src .venv/bin/pytest tests/test_order_state_machine.py tests/test_paper_execution.py tests/test_execution_reconciliation.py tests/test_db_migrations.py -q` passed with elevated local PostgreSQL access (`26 passed`).
- Verified paper execution and reconciliation now route lifecycle updates through `apply_order_transition()` instead of direct state mutation.

---
*Phase: 07-correctness-kernel*
*Completed: 2026-04-19*
