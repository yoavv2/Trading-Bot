---
phase: 04-risk-and-portfolio
plan: 02
subsystem: api
tags: [risk, portfolio, postgres, cli, worker, audit]

requires:
  - phase: 04-01
    provides: Typed portfolio settings, live positions, account snapshots, and DB-backed portfolio state loading
provides:
  - Deterministic risk validation for every generated strategy signal
  - Persisted `risk_events` audit records under `strategy_runs`
  - CLI and worker entrypoints for risk evaluation
affects:
  - phase 05 paper execution
  - future analytics and API reads in phase 06

tech-stack:
  added: []
  patterns:
    - Every signal is evaluated through one deterministic risk service before execution
    - Risk decisions persist under `strategy_runs` with account snapshots for auditability
    - Operator-facing execution gates use script and worker entrypoints, not ad hoc one-off code

key-files:
  created:
    - src/trading_platform/db/models/risk_event.py
    - alembic/versions/0007_phase4_risk_pipeline.py
    - scripts/evaluate_risk.py
    - tests/test_risk_pipeline.py
  modified:
    - src/trading_platform/services/risk.py
    - src/trading_platform/services/market_data_access.py
    - src/trading_platform/db/models/strategy_run.py
    - src/trading_platform/worker/__main__.py
    - tests/test_db_migrations.py

key-decisions:
  - "Flat signals are persisted as rejected `non_actionable_signal` decisions so the audit trail covers every evaluated symbol in a batch"
  - "Stale-data rejection is batch-wide and blocks actionable signals when session coverage or freshness is unsafe"
  - "Risk evaluations reuse `strategy_runs` with a new `risk_evaluation` run type instead of inventing a second orchestration root"

patterns-established:
  - "Risk audit trail: one `risk_events` row per evaluated signal with machine-readable code and human-readable reason"
  - "CLI-first gating flow: resolve session, generate signals, load portfolio state, persist account snapshot, persist risk decisions"

requirements-completed:
  - REQ-07
  - REQ-06

duration: 8min
completed: 2026-03-14
---

# Phase 4 Plan 02: Risk Validation Pipeline Summary

**A persisted signal-to-risk gate with stale-data blocking, deterministic sizing, and CLI-first evaluation entrypoints**

## Performance

- **Duration:** ~8 min
- **Started:** 2026-03-14T17:11:54Z
- **Completed:** 2026-03-14T17:20:00Z
- **Tasks:** 3
- **Files modified:** 10

## Accomplishments
- Replaced the placeholder risk contract with a real deterministic validation pipeline covering stale data, duplicate positions, max positions, allocation caps, and approved whole-share sizing
- Added `risk_events` persistence plus the `risk_evaluation` run type so every evaluated signal is durable and inspectable under `strategy_runs`
- Exposed the full risk-evaluation loop through both `scripts/evaluate_risk.py` and `trading-platform-worker evaluate-risk`

## Task Commits

Each task was committed atomically:

1. **Task 1: Replace the placeholder risk contract with a real validation pipeline** - `ba2f3fc` (feat)
2. **Task 2: Persist approved and rejected signal decisions with audit-ready context** - `e487724` (feat)
3. **Task 3: Wire a CLI-first signal-to-risk evaluation flow and regression coverage** - `068e981` (feat)

## Files Created/Modified

- `src/trading_platform/services/risk.py` - Deterministic risk service, run orchestration, and persistence flow
- `src/trading_platform/services/market_data_access.py` - Session coverage helper for stale-data validation
- `src/trading_platform/db/models/risk_event.py` - Persisted risk decision model
- `src/trading_platform/db/models/strategy_run.py` - Added `risk_evaluation` run type and risk-event relationship
- `alembic/versions/0007_phase4_risk_pipeline.py` - Risk-event schema migration
- `scripts/evaluate_risk.py` - Standalone CLI for the risk-evaluation loop
- `src/trading_platform/worker/__main__.py` - Added `evaluate-risk` worker command
- `tests/test_risk_pipeline.py` - Core validation and persistence coverage
- `tests/test_db_migrations.py` - Verified the risk-event schema and enum shape at Alembic head

## Decisions Made

- Persisted flat signals as `non_actionable_signal` rejections so the audit trail covers every evaluated symbol in a strategy batch.
- Applied stale-data blocking across actionable signals when either session freshness or universe bar coverage is unsafe.
- Reused `strategy_runs` for risk-evaluation batches to keep dry runs, backtests, and risk decisions under one orchestration root.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

- The full Phase 04 verification command required access to the local PostgreSQL instance outside the sandbox. The exact planned regression slice passed once rerun against the local database with that access.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Phase 05 can now consume approved execution candidates and persisted blocked-trade reasons without redesigning the audit trail.
- The operator can inspect the risk gate from the terminal before any broker submission path exists.

---
*Phase: 04-risk-and-portfolio*
*Completed: 2026-03-14*
