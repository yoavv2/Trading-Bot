---
phase: 05-paper-execution
plan: 02
subsystem: api
tags: [alpaca, paper-execution, postgres, cli, worker, lifecycle, sync]

requires:
  - phase: 05-01
    provides: Persisted `paper_orders`, deterministic client-order IDs, and the reusable Alpaca submission seam
provides:
  - Idempotent paper-session orchestration that submits only missing approved orders for one session
  - Broker lifecycle sync that updates `paper_orders`, ingests normalized `paper_fills`, and refreshes live positions plus account snapshots
  - CLI and worker entrypoints for running the daily paper session and syncing broker state
affects:
  - phase 05-03 reconciliation and restart safety
  - phase 06 analytics and API reads

tech-stack:
  added: []
  patterns:
    - Preflighted session orchestration resolves one target session and no-ops when all approved candidates are already seeded locally
    - Broker reads reconcile back into the Phase 5 order ledger and the Phase 4 live-state tables instead of maintaining a parallel state store
    - Fill ingestion is idempotent via normalized `paper_fills` rows keyed by broker fill ID

key-files:
  created:
    - scripts/run_paper_session.py
    - scripts/sync_paper_state.py
    - src/trading_platform/db/models/paper_fill.py
    - alembic/versions/0009_phase5_order_lifecycle.py
    - tests/test_paper_execution.py
  modified:
    - config/app.yaml
    - src/trading_platform/core/settings.py
    - src/trading_platform/services/paper_execution.py
    - src/trading_platform/services/alpaca.py
    - src/trading_platform/db/models/paper_order.py
    - src/trading_platform/db/models/__init__.py
    - src/trading_platform/worker/__main__.py
    - Makefile
    - tests/test_db_migrations.py

key-decisions:
  - "The session runner preflights the target risk batch and returns a no-op report when every approved candidate already has a seeded `paper_order`, so scheduler reruns stay safe without creating extra execution batches"
  - "Broker lifecycle sync reuses `paper_orders` and matches by broker order ID first, then deterministic client-order ID, so the read path composes directly on the 05-01 submission seam"
  - "Repeated broker syncs dedupe fill ingestion on `paper_fills.broker_fill_id` while live `positions` and `account_snapshots` remain the durable source for downstream portfolio and risk reads"

patterns-established:
  - "Daily paper loop: `run_paper_session` resolves one persisted session, loads approved risk decisions, and submits only missing orders"
  - "Broker-derived state refresh: `sync_paper_state` is the only path that mutates durable order lifecycle, fill, position, and cash state from broker responses"

requirements-completed:
  - REQ-08
  - REQ-06

duration: 24min
completed: 2026-03-14
---

# Phase 05 Plan 02: Paper Execution Summary

**An idempotent daily paper-session runner with broker lifecycle sync, normalized fill persistence, and broker-derived live-state refreshes**

## Performance

- **Duration:** 24 min
- **Started:** 2026-03-14T18:03:00Z
- **Completed:** 2026-03-14T18:27:05Z
- **Tasks:** 3
- **Files modified:** 14

## Accomplishments
- Added a reusable paper-session runner that resolves one persisted target session, preflights the approved risk batch, and only invokes broker submission when orders are still missing.
- Added broker lifecycle sync with normalized `paper_fills`, broker timestamp columns on `paper_orders`, and broker-derived refreshes of `positions` plus `account_snapshots`.
- Exposed the scheduled-loop operator surface through standalone scripts, worker subcommands, Make targets, and deterministic regression tests for reruns and partial fills.

## Task Commits

Each task was committed atomically:

1. **Task 1: Build an idempotent paper-session runner and scheduling surface** - `c784a81` (feat)
2. **Task 2: Persist lifecycle updates, fills, positions, and account snapshots from broker sync** - `a3e5822` (feat)
3. **Task 3: Expose operator entrypoints and regression coverage for the scheduled loop** - `6eefd95` (feat)

## Files Created/Modified

- `src/trading_platform/services/paper_execution.py` - Added session preflight/no-op orchestration plus broker-to-local lifecycle sync.
- `src/trading_platform/services/alpaca.py` - Expanded the Alpaca client with order, fill, position, and account reads normalized for local sync.
- `src/trading_platform/db/models/paper_fill.py` - Added normalized fill persistence keyed by broker fill ID.
- `src/trading_platform/db/models/paper_order.py` - Added broker lifecycle timestamp fields and the fill relationship.
- `alembic/versions/0009_phase5_order_lifecycle.py` - Added the lifecycle migration for `paper_fills` and broker-sync columns.
- `scripts/run_paper_session.py` - Added the standalone idempotent paper-session CLI.
- `scripts/sync_paper_state.py` - Added the standalone broker-state sync CLI.
- `src/trading_platform/worker/__main__.py` - Added `run-paper-session` and `sync-paper-state` worker commands.
- `tests/test_paper_execution.py` - Added DB-backed coverage for session reruns, broker-derived state sync, and partial fill progression.
- `tests/test_db_migrations.py` - Extended Alembic-head assertions for the lifecycle schema.

## Decisions Made

- Used a preflight plan instead of calling submission blindly so the scheduled runner can skip already-seeded sessions without emitting duplicate execution batches.
- Kept the broker sync path in `paper_execution.py` and widened `alpaca.py` rather than introducing a second provider-specific lifecycle service.
- Reused the Phase 4 `positions` and `account_snapshots` tables as the durable broker-derived live state so downstream risk logic stays on one portfolio path.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

- PostgreSQL-backed verification could not reach the local database from the sandbox, so the planned test slices were rerun with local database access enabled.
- A transient `.git/index.lock` was created while staging files in parallel; the lock cleared and staging was rerun sequentially before committing.

## User Setup Required

None - no additional repo-local setup was required beyond the existing Alpaca broker credentials used for live paper execution.

## Next Phase Readiness

- Phase 05-03 can build reconciliation and unsafe-state stop conditions on top of persisted order lifecycle timestamps, normalized fills, and broker-derived live state.
- The operator can now schedule the daily paper-execution loop with CLI-first entrypoints and resync broker state deterministically after restarts.

## Self-Check: PASSED

- Verified `.planning/phases/05-paper-execution/05-02-SUMMARY.md` exists.
- Verified task commits `c784a81`, `a3e5822`, and `6eefd95` exist in Git history.

---
*Phase: 05-paper-execution*
*Completed: 2026-03-14*
