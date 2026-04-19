---
phase: 07-correctness-kernel
plan: 02
subsystem: execution-identity
tags: [paper-orders, idempotency, client-order-id, reconciliation, postgres, audit-trail]

requires:
  - phase: 07-correctness-kernel
    provides: Closed order lifecycle kernel and append-only transition history from 07-01
provides:
  - Deterministic material-intent identity and `client_order_id` derivation
  - Retry/reuse/version semantics for unchanged versus broker-touched order intents
  - Client-order-first reconciliation and operator-visible intent lineage
affects:
  - phase 07-03 global kill switch
  - phase 09 reconciliation rewrite
  - operator order drilldowns and execution summaries

tech-stack:
  added: []
  patterns:
    - "Material order identity is derived from strategy, session, symbol, side, and quantity"
    - "Same-intent retries reuse the persisted row and client order ID instead of inserting duplicates"
    - "Broker-touched material changes create explicit successor intent versions linked to their predecessor"

key-files:
  created:
    - alembic/versions/0014_phase7_idempotent_intents.py
    - src/trading_platform/services/order_identity.py
  modified:
    - src/trading_platform/db/models/paper_order.py
    - src/trading_platform/services/execution.py
    - src/trading_platform/services/paper_execution.py
    - src/trading_platform/services/reconciliation.py
    - src/trading_platform/services/operator_reads.py
    - tests/test_alpaca_execution.py
    - tests/test_paper_execution.py
    - tests/test_execution_reconciliation.py
    - tests/test_order_state_machine.py
    - tests/test_analytics_service.py
    - tests/test_db_migrations.py

key-decisions:
  - "Derived `intent_hash` and `client_order_id` from material order fields instead of `risk_event_id` so identity survives reruns and restarts"
  - "Reused the persisted order row for same-intent retries, but forced explicit successor versions once the broker had touched the prior order"
  - "Made reconciliation and operator reads prefer `client_order_id` while still retaining `broker_order_id` as fallback context"

patterns-established:
  - "Intent identity is stable and deterministic before any broker call occurs"
  - "Submission summaries surface reuse and versioning explicitly instead of silently skipping duplicate work"
  - "Order drilldowns expose predecessor lineage so reruns remain auditable"

requirements-completed:
  - ORDER-07
  - IDEM-01
  - IDEM-02
  - IDEM-03
  - IDEM-04

duration: 32 min
completed: 2026-04-19
---

# Phase 07 Plan 02: Idempotent Intents Summary

**Deterministic order identity with safe retry reuse, explicit successor versions, and client-order-first reconciliation**

## Performance

- **Duration:** 32 min
- **Started:** 2026-04-19T14:29:41+03:00
- **Completed:** 2026-04-19T12:02:08Z
- **Tasks:** 3
- **Files modified:** 13

## Accomplishments

- Added deterministic material-order identity helpers plus `intent_hash`, `intent_version`, and predecessor-link persistence for paper orders.
- Reworked paper submission planning so identical intents reuse or retry the existing row, while broker-touched material changes create explicit new versions with lineage metadata.
- Updated reconciliation and operator reads to treat `client_order_id` as the primary identity surface and expose intent lineage in summaries and drilldowns.

## Task Commits

1. **Task 1: Replace risk-event-derived identity with material intent identity** - `ddb46db` (feat)
2. **Task 2: Make paper submission and retry behavior idempotent by design** - `d1168ad` (feat)
3. **Task 3: Prioritize `client_order_id` in broker matching and operator drilldown** - `f8f51f7` (feat)

## Files Created/Modified

- `src/trading_platform/services/order_identity.py` - Derives material order identity, deterministic intent hashes, and stable `client_order_id` values.
- `alembic/versions/0014_phase7_idempotent_intents.py` - Adds intent identity/version columns and backfills existing orders from material order data.
- `src/trading_platform/db/models/paper_order.py` - Persists deterministic identity fields plus predecessor links for version chains.
- `src/trading_platform/services/paper_execution.py` - Reuses same-intent rows, versions broker-touched material changes, and surfaces intent decisions in run summaries.
- `src/trading_platform/services/reconciliation.py` - Prefers `client_order_id` matching before broker-order fallback.
- `src/trading_platform/services/operator_reads.py` - Includes intent lineage context in operator order payloads.
- `tests/test_paper_execution.py` - Covers same-intent retry reuse, version chaining, and intent-aware session planning.
- `tests/test_alpaca_execution.py` - Verifies versioned intents keep broker submission behavior explicit in the Alpaca execution flow.
- `tests/test_execution_reconciliation.py` - Verifies reconciliation follows `client_order_id` even when predecessor chains share broker IDs.

## Decisions Made

- Identity is now anchored to material trade intent rather than ephemeral risk-event identity, because retries must survive reruns and process restarts.
- Same-intent retries continue on the persisted row and client order ID when the prior order is still retryable, preserving idempotency.
- Once the broker has touched an order, later material changes become explicit successor versions so the audit trail shows intent evolution rather than silent reuse.

## Deviations from Plan

- None. The planned behavior landed across three task commits.

## Issues Encountered

- Two executor interruptions left the worktree mid-task during implementation, so the final Task 2 and Task 3 verification and commits were completed manually from the preserved edits.
- Full verification required elevated local PostgreSQL access because sandboxed localhost TCP access is blocked in this environment.

## User Setup Required

- None.

## Next Phase Readiness

- Phase `07-02` is complete and verified.
- Phase `07-03` can now enforce the global kill switch on top of stable lifecycle and idempotent intent behavior without redefining submission identity rules.

## Self-Check: PASSED

- Verified `PYTHONPATH=src .venv/bin/pytest tests/test_alpaca_execution.py tests/test_paper_execution.py tests/test_execution_reconciliation.py tests/test_db_migrations.py -q` passed with elevated local PostgreSQL access (`32 passed`).
- Verified the narrowed integration slice `tests/test_paper_execution.py tests/test_alpaca_execution.py tests/test_execution_reconciliation.py` passed before splitting the Task 2 and Task 3 commits (`22 passed`).

---
*Phase: 07-correctness-kernel*
*Completed: 2026-04-19*
