---
phase: 05-paper-execution
plan: 03
subsystem: api
tags: [alpaca, reconciliation, recovery, postgres, cli, worker, guardrails]

requires:
  - phase: 05-02
    provides: Persisted `paper_orders`, `paper_fills`, broker lifecycle sync, and broker-derived live-state refreshes
provides:
  - Durable reconciliation runs and `execution_events` for broker drift, repeated failures, and unsafe execution stops
  - Restart-safe paper-session recovery that reuses persisted client-order IDs before any resubmission attempt
  - CLI and worker reconciliation entrypoints plus session-level blocking when broker state is unsafe
affects:
  - phase 06 analytics and API inspection
  - operator review of blocked, mismatched, or recovered paper-execution sessions

tech-stack:
  added: []
  patterns:
    - Reconciliation runs persist under `strategy_runs` with `run_type=reconciliation` and durable `execution_events`
    - Session execution preflights broker state, recovers in-flight local orders, and fails closed before submitting new orders when drift remains
    - Submission retries reuse persisted `paper_orders` rows and client-order IDs instead of reseeding duplicate broker intents

key-files:
  created:
    - src/trading_platform/services/reconciliation.py
    - src/trading_platform/db/models/execution_event.py
    - alembic/versions/0010_phase5_reconciliation_guards.py
    - scripts/reconcile_paper_execution.py
    - tests/test_execution_reconciliation.py
  modified:
    - config/app.yaml
    - src/trading_platform/core/settings.py
    - src/trading_platform/db/models/paper_order.py
    - src/trading_platform/db/models/strategy_run.py
    - src/trading_platform/services/paper_execution.py
    - src/trading_platform/worker/__main__.py
    - Makefile
    - tests/test_paper_execution.py
    - tests/test_db_migrations.py

key-decisions:
  - "Unsafe-state findings persist as `execution_events` under a dedicated reconciliation run instead of living only in logs or transient exceptions"
  - "The paper-session runner always prefers recovery and reconciliation before new submissions when broker preflight is available"
  - "Only `pending_submission` and below-threshold `submission_failed` orders are retryable; other broker-touched orders are treated as operator-review outcomes instead of being silently resubmitted"

patterns-established:
  - "Fail-closed paper execution: unresolved broker drift blocks new submissions and returns operator-visible stop reasons"
  - "Restart-safe recovery: in-flight local orders reconcile back to broker orders by broker ID first, then deterministic client-order ID"
  - "CLI-first inspection: reconciliation can run from a standalone script, worker command, or automatically as session preflight"

requirements-completed:
  - REQ-08
  - REQ-06

duration: 138min
completed: 2026-03-14
---

# Phase 05 Plan 03: Paper Execution Summary

**Broker reconciliation, restart-safe session recovery, and fail-closed execution guards for unsafe Alpaca paper state**

## Performance

- **Duration:** 138 min
- **Started:** 2026-03-14T18:28:31Z
- **Completed:** 2026-03-14T20:46:24Z
- **Tasks:** 3
- **Files modified:** 14

## Accomplishments
- Added durable reconciliation runs and `execution_events` that persist broker drift, repeated failure thresholds, and operator-visible execution-stop reasons.
- Hardened the paper-session runner so it recovers in-flight orders by persisted client-order ID, reuses retryable local `paper_orders`, and blocks new submissions when reconciliation still reports unsafe state.
- Exposed reconciliation through standalone and worker CLI entrypoints, Make targets, and deterministic regression coverage for restart recovery plus unsafe-state blocking.

## Task Commits

Each task was committed atomically:

1. **Task 1: Implement broker-to-local reconciliation and unsafe-state persistence** - `39c63e0` (feat)
2. **Task 2: Add restart-safe submission recovery and repeated-failure guardrails** - `1436944` (feat)
3. **Task 3: Expose reconciliation commands and lock the unsafe-state behavior with regression tests** - `708f63a` (feat)

## Files Created/Modified

- `src/trading_platform/services/reconciliation.py` - Broker-state loading, in-flight order recovery, mismatch detection, and durable reconciliation reporting.
- `src/trading_platform/db/models/execution_event.py` - Normalized persistence for blocking and non-blocking execution findings.
- `alembic/versions/0010_phase5_reconciliation_guards.py` - Added reconciliation run support, execution events, and per-order failure counters.
- `src/trading_platform/services/paper_execution.py` - Added preflight reconciliation, restart-safe retry reuse, and blocked-session reporting.
- `scripts/reconcile_paper_execution.py` - Standalone reconciliation CLI for operator inspection.
- `src/trading_platform/worker/__main__.py` - Added `reconcile-paper-execution` worker command.
- `tests/test_execution_reconciliation.py` - Added mismatch, clean-state, and failure-threshold coverage.
- `tests/test_paper_execution.py` - Added recovery-before-submit and unsafe-state blocking coverage.

## Decisions Made

- Persisted unsafe-state findings as normalized `execution_events` so Phase 6 inspection can query durable stop reasons without parsing logs.
- Reused `paper_orders` as the single restart anchor and only allowed automatic retries for rows that never received a confirmed broker order.
- Kept reconciliation in the paper-execution layer and CLI surface rather than pushing broker drift policy into strategy or risk-generation code.

## Deviations from Plan

None - plan executed as scoped. Verification required elevated access to the local PostgreSQL instance in this environment, but no product-scope changes were introduced.

## Issues Encountered

- The local PostgreSQL-backed verification slice is blocked by the sandbox and had to be rerun with elevated local DB access.

## User Setup Required

- Live reconciliation and paper-session preflight still require `TRADING_PLATFORM_BROKER__ALPACA__API_KEY` and `TRADING_PLATFORM_BROKER__ALPACA__API_SECRET`.

## Next Phase Readiness

- Phase 6 can now build analytics and API reads on top of durable order, fill, reconciliation, and blocked-execution records instead of raw logs.
- The MVP paper-trading loop now fails closed when broker state is unsafe and preserves enough audit detail for next-day inspection.

## Self-Check: PASSED

- Verified `.planning/phases/05-paper-execution/05-03-SUMMARY.md` exists.
- Verified task commits `39c63e0`, `1436944`, and `708f63a` exist in Git history.
- Verified `PYTHONPATH=src .venv/bin/pytest tests/test_execution_reconciliation.py tests/test_paper_execution.py tests/test_alpaca_execution.py tests/test_db_migrations.py -q` passes with local PostgreSQL access.

---
*Phase: 05-paper-execution*
*Completed: 2026-03-14*
