---
phase: 05-paper-execution
plan: 01
subsystem: api
tags: [alpaca, execution, postgres, cli, worker, httpx]

requires:
  - phase: 04-02
    provides: Persisted approved `risk_events`, account snapshots, and the `risk_evaluation` batch root under `strategy_runs`
provides:
  - Typed Alpaca broker settings plus provider-agnostic execution intents and results
  - Durable `paper_orders` persistence anchored to `strategy_runs`
  - CLI and worker entrypoints for one-time paper-order submission from approved risk decisions
affects:
  - phase 05-02 lifecycle sync
  - phase 05-03 reconciliation and restart safety
  - phase 06 analytics and API reads

tech-stack:
  added: []
  patterns:
    - Thin `httpx` broker clients normalize provider payloads into provider-agnostic execution results
    - `strategy_runs` remains the single batch root for paper execution just as it does for backtests and risk evaluations
    - Paper-order rows reserve deterministic client-order IDs before broker submission so reruns can skip already-seeded candidates safely

key-files:
  created:
    - src/trading_platform/services/alpaca.py
    - src/trading_platform/services/paper_execution.py
    - src/trading_platform/db/models/paper_order.py
    - alembic/versions/0008_phase5_paper_orders.py
    - scripts/submit_paper_orders.py
    - tests/test_alpaca_execution.py
  modified:
    - config/app.yaml
    - src/trading_platform/core/settings.py
    - src/trading_platform/services/execution.py
    - src/trading_platform/db/models/strategy_run.py
    - src/trading_platform/db/models/__init__.py
    - src/trading_platform/worker/__main__.py
    - Makefile
    - tests/test_db_migrations.py

key-decisions:
  - "Approved `risk_events` are the Phase 5 submission source; no separate execution-candidate table was introduced"
  - "Paper-order rows persist deterministic client-order IDs before broker submission so later reruns and reconciliation can reuse local idempotency anchors"
  - "Alpaca-specific HTTP mapping stays in `services/alpaca.py`, while batch orchestration and persistence live in `services/paper_execution.py`"

patterns-established:
  - "Broker adapter boundary: execution intents/results are provider-agnostic and Alpaca payload mapping is isolated behind a thin client"
  - "Execution batch audit trail: each paper submission run gets its own `strategy_run` root with submitted and already-existing orders summarized in `result_summary`"
  - "CLI-first operator flow: standalone scripts and worker subcommands both reuse the same submission service"

requirements-completed:
  - REQ-08
  - REQ-06
  - REQ-11

duration: 26min
completed: 2026-03-14
---

# Phase 05 Plan 01: Paper Execution Summary

**Alpaca paper-order submission with typed broker settings, durable local idempotency keys, and CLI-first execution batches anchored to `strategy_runs`**

## Performance

- **Duration:** 26 min
- **Started:** 2026-03-14T17:32:27Z
- **Completed:** 2026-03-14T17:57:57Z
- **Tasks:** 3
- **Files modified:** 14

## Accomplishments
- Added typed Alpaca broker and execution defaults plus a thin `httpx` client that normalizes broker responses into provider-agnostic execution results.
- Added the `paper_orders` schema and `paper_execution` run type so submitted orders persist with local client-order IDs, broker IDs, and status snapshots under a canonical execution batch root.
- Wired a CLI-first paper submission flow that consumes approved `risk_events`, persists deterministic order seeds before submission, exposes script and worker entrypoints, and verifies DB-backed idempotent behavior.

## Task Commits

Each task was committed atomically:

1. **Task 1: Add typed Alpaca paper settings and a thin broker client** - `76d865d` (feat)
2. **Task 2: Add paper-order persistence and Phase 5 batch anchoring** - `e645bd4` (feat)
3. **Task 3: Wire a CLI-first order-submission flow for approved candidates** - `05256f3` (feat)

## Files Created/Modified

- `src/trading_platform/services/alpaca.py` - Thin Alpaca client plus provider-agnostic execution adapter.
- `src/trading_platform/services/paper_execution.py` - Paper-order batch orchestration, candidate loading, deterministic client-order IDs, and persistence updates.
- `src/trading_platform/db/models/paper_order.py` - Durable paper-order model keyed by source risk event and local client-order ID.
- `alembic/versions/0008_phase5_paper_orders.py` - Phase 5 schema migration for `paper_orders` and the `paper_execution` enum value.
- `scripts/submit_paper_orders.py` - Standalone paper-order submission CLI.
- `src/trading_platform/worker/__main__.py` - Worker `submit-paper-orders` subcommand.
- `tests/test_alpaca_execution.py` - Broker-unit coverage plus DB-backed idempotent submission coverage.
- `tests/test_db_migrations.py` - Alembic-head assertions for the Phase 5 paper-order schema.

## Decisions Made

- Reused approved `risk_events` as the paper-submission source of truth so Phase 5 composes directly on the Phase 4 audit trail instead of introducing a second candidate model.
- Persisted placeholder `paper_orders` rows before broker submission so local idempotency keys survive failures and later runs can detect already-seeded execution candidates safely.
- Kept the broker adapter boundary explicit: Alpaca request/response translation lives in `alpaca.py`, while execution-service types and submission orchestration remain provider-agnostic.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Applied Alpaca auth headers to injected `httpx` clients**
- **Found during:** Task 1 (Add typed Alpaca paper settings and a thin broker client)
- **Issue:** The injected test transport path skipped the Alpaca auth headers, so test and production request paths diverged.
- **Fix:** Updated `AlpacaClient` to apply the auth headers even when an `httpx.Client` is injected.
- **Files modified:** `src/trading_platform/services/alpaca.py`
- **Verification:** `PYTHONPATH=src .venv/bin/pytest tests/test_alpaca_execution.py -q`
- **Committed in:** `76d865d`

**2. [Rule 1 - Bug] Tightened paper submission session and risk-run validation**
- **Found during:** Task 3 (Wire a CLI-first order-submission flow for approved candidates)
- **Issue:** The default submission session fell back to wall-clock yesterday instead of the latest persisted completed session, and explicit `risk_run_id` values were not validated against the requested strategy and session.
- **Fix:** Resolved the default session from persisted market-session data and validated explicit risk runs against both the target strategy and target session.
- **Files modified:** `src/trading_platform/services/paper_execution.py`
- **Verification:** `PYTHONPATH=src .venv/bin/python scripts/submit_paper_orders.py --help`, `PYTHONPATH=src .venv/bin/python -m trading_platform.worker submit-paper-orders --help`, and `PYTHONPATH=src .venv/bin/pytest tests/test_alpaca_execution.py tests/test_db_migrations.py -q`
- **Committed in:** `05256f3`

**3. [Rule 3 - Blocking] Hardened PostgreSQL test cleanup for forced database teardown**
- **Found during:** Task 3 verification
- **Issue:** The required DB-backed verification slice could fail during fixture teardown because the old cleanup path attempted to terminate superuser-owned backend processes it could not kill.
- **Fix:** Switched the temporary-database cleanup path to `DROP DATABASE ... WITH (FORCE)` in the shared migration and execution test fixtures.
- **Files modified:** `tests/test_db_migrations.py`, `tests/test_alpaca_execution.py`
- **Verification:** `PYTHONPATH=src .venv/bin/pytest tests/test_alpaca_execution.py tests/test_db_migrations.py -q`
- **Committed in:** `05256f3`

---

**Total deviations:** 3 auto-fixed (2 bug fixes, 1 blocking verification fix)
**Impact on plan:** All deviations were required for correctness or for the planned verification slice to pass. No scope creep beyond the Phase 05-01 execution foundation.

## Issues Encountered

- The required PostgreSQL-backed verification slice could not access the local database from the sandbox, so the planned combined test command was rerun with elevated local access.

## User Setup Required

- Live paper-order submission requires `TRADING_PLATFORM_BROKER__ALPACA__API_KEY` and `TRADING_PLATFORM_BROKER__ALPACA__API_SECRET` in the shell or `.env`. No additional repo-local setup file was required for plan completion.

## Next Phase Readiness

- Phase 05-02 can now schedule against a real submission seam, sync broker lifecycle state back into `paper_orders`, and reuse the `paper_execution` batch root without redesigning storage.
- Phase 05-03 can build restart safety and reconciliation on top of the persisted local client-order IDs, broker identifiers, and source `risk_event` anchors added here.

## Self-Check: PASSED

- Verified `.planning/phases/05-paper-execution/05-01-SUMMARY.md` exists.
- Verified task commits `76d865d`, `e645bd4`, and `05256f3` exist in Git history.

---
*Phase: 05-paper-execution*
*Completed: 2026-03-14*
