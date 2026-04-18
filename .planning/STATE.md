---
gsd_state_version: 1.0
milestone: v1.1
milestone_name: Execution Correctness & Hardening
current_phase: null
current_phase_name: null
current_plan: null
status: Defining requirements
stopped_at: null
last_updated: "2026-04-18T00:00:00Z"
last_activity: 2026-04-18
progress:
  total_phases: 0
  completed_phases: 0
  total_plans: 0
  completed_plans: 0
  percent: 0
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-04-18)

**Core value:** Build a trustworthy, auditable trading platform that can reproducibly validate a strategy, run it in daily paper trading, and explain every action or blocked action without ambiguity.
**Current focus:** Milestone v1.1 — Execution Correctness & Hardening (defining requirements)

## Current Position

Phase: Not started (defining requirements)
Plan: —
Status: Defining requirements
Last activity: 2026-04-18 — Milestone v1.1 started (Execution Correctness & Hardening)

Progress: [░░░░░░░░░░] 0%

## Performance Metrics

**Velocity:**
- Total plans completed: 16
- Average duration: ~7 min
- Total execution time: -

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 1 | 3 of 3 | - | - |
| 2 | 3 of 3 | - | - |
| 3 | 3 of 3 | - | - |
| 4 | 2 of 2 | - | - |
| 5 | 3 of 3 | - | - |
| 6 | 3 of 3 | - | - |

**Recent Trend:**
- Last 5 plans: 05-02, 05-03, 06-01, 06-02, 06-03 completed
- Trend: Milestone execution scope is complete; the next action is milestone audit and closure

*Updated after each plan completion*
| Phase 02-data-and-strategy P02 | 6 | 3 tasks | 14 files |
| Phase 02-data-and-strategy P03 | 5min | 3 tasks | 8 files |
| Phase 03-backtest-and-reporting P01 | 12min | 3 tasks | 10 files |
| Phase 03-backtest-and-reporting P02 | 17min | 3 tasks | 6 files |
| Phase 03-backtest-and-reporting P03 | 18min | 3 tasks | 9 files |
| Phase 04-risk-and-portfolio P01 | 3min | 3 tasks | 9 files |
| Phase 04-risk-and-portfolio P02 | 8min | 3 tasks | 10 files |
| Phase 05-paper-execution P01 | 26min | 3 tasks | 14 files |
| Phase 05-paper-execution P02 | 24min | 3 tasks | 14 files |
| Phase 05-paper-execution P03 | 138min | 3 tasks | 14 files |
| Phase 06-analytics-and-apis P01 | 15min | 3 tasks | 10 files |
| Phase 06-analytics-and-apis P02 | 20min | 3 tasks | 9 files |

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- [Init]: Scope the first roadmap to the MVP milestone only; multi-strategy expansion and dashboard work stay in PROJECT.md as later milestones.
- [Init]: Front-load platform structure, data integrity, and auditability before adding broker automation.
- [Init]: Treat the backtest-to-paper-trading daily loop as the definition of MVP readiness.
- [02-01]: Promoted httpx from dev to runtime dependency for the Polygon client.
- [02-01]: Shortened Alembic revision ID to 0002_phase2_mdf to stay within alembic_version varchar(32) limit.
- [02-01]: Used INSERT ON CONFLICT DO UPDATE RETURNING id instead of rowcount for reliable upsert counts.
- [02-01]: Symbol rows are minimal ticker-only stubs during bar ingestion to avoid requiring a separate symbol-sync prerequisite.
- [02-01]: DailyBar uniqueness spans symbol_id + session_date + adjusted + provider for future multi-provider support.
- [Phase 02-data-and-strategy]: date_to_session(direction=previous) used instead of previous_session() — the latter requires valid session input
- [Phase 02-data-and-strategy]: Sessions persisted to market_sessions table so read queries are SQL joins, not calendar library calls
- [Phase 02-data-and-strategy]: Symbol enrichment columns nullable to allow ticker-stub rows to coexist with fully-synced rows
- [Phase 02-data-and-strategy]: Module-level bars_for_sessions import in strategy enables patch-based test isolation without DB
- [Phase 02-data-and-strategy]: TrendFollowingExitSettings typed model replaces dict exits so exit_window is a validated int
- [Phase 03-backtest-and-reporting]: `strategy_runs` remains the single run root; Phase 3 distinguishes backtests via `run_type=backtest`
- [Phase 03-backtest-and-reporting]: Run assumptions persist in `parameters_snapshot` so execution and reporting stay tied to one exact config payload
- [Phase 03-backtest-and-reporting]: Signals, trades, and equity history live in normalized child tables rather than opaque JSON blobs
- [Phase 03-backtest-and-reporting]: Backtests compose on `strategy.generate_signals()` and only fill on the next persisted session open
- [Phase 03-backtest-and-reporting]: Pending exits are processed before entries on a fill session so equal-weight slot rotation stays deterministic
- [Phase 03-backtest-and-reporting]: Duplicate LONG signals while a position is open are persisted for inspection but ignored for execution
- [Phase 03-backtest-and-reporting]: Run-level metrics materialize into `backtest_metrics` from persisted artifacts instead of ad hoc re-simulation
- [Phase 03-backtest-and-reporting]: Report/export commands default to the latest successful backtest for a strategy when no run ID is provided
- [Phase 03-backtest-and-reporting]: No-trade backtest reports return zero-safe metrics rather than null-heavy or divide-by-zero output
- [Phase 04-risk-and-portfolio]: `risk_per_trade` is treated as a deterministic notional budget fraction until the strategy model has explicit stop-distance-based risk
- [Phase 04-risk-and-portfolio]: Live `positions` and `account_snapshots` remain separate from Phase 3 backtest tables
- [Phase 04-risk-and-portfolio]: Flat signals persist as `non_actionable_signal` rejections so every evaluated symbol is visible in the risk audit trail
- [Phase 04-risk-and-portfolio]: `strategy_runs` remains the single execution root; Phase 4 adds `run_type=risk_evaluation`
- [Phase 04-risk-and-portfolio]: The operator risk gate is CLI-first via `scripts/evaluate_risk.py` and `trading-platform-worker evaluate-risk`
- [Phase 05-paper-execution]: Approved risk_events are the paper-submission source; no separate execution-candidate table was introduced
- [Phase 05-paper-execution]: Paper-order rows persist deterministic client-order IDs before broker submission so reruns can safely detect already-seeded candidates
- [Phase 05-paper-execution]: Alpaca HTTP mapping stays in services/alpaca.py while submission orchestration and persistence live in services/paper_execution.py
- [Phase 05-paper-execution]: The session runner preflights approved risk candidates and returns a no-op report when all paper orders for a session are already seeded.
- [Phase 05-paper-execution]: Broker lifecycle sync reuses paper_orders and matches broker reads by broker order ID first, then deterministic client_order_id.
- [Phase 05-paper-execution]: Repeated broker syncs persist normalized paper_fills keyed by broker_fill_id while positions and account_snapshots remain the durable live-state source.
- [Phase 05-paper-execution]: Reconciliation findings persist as execution_events under strategy_runs with run_type=reconciliation for next-day inspection.
- [Phase 05-paper-execution]: Paper-session preflight recovers in-flight orders, reconciles broker state, and blocks new submissions when unresolved drift remains.
- [Phase 05-paper-execution]: Only pending_submission and below-threshold submission_failed orders are retryable; broker-touched orders fail closed for operator review.
- [Phase 06-analytics-and-apis]: Backtest analytics remain derived from persisted trades and equity snapshots; Phase 6 expands the materialized metric surface instead of re-simulating results ad hoc.
- [Phase 06-analytics-and-apis]: Paper analytics stay honest to persisted state by exposing account, order, fill, position, and blocking-event summaries without inventing unsupported closed-trade PnL metrics.
- [Phase 06-analytics-and-apis]: Operator inspection reads live behind one shared service layer that returns serializable payloads for runs, orders, fills, positions, snapshots, risk events, and execution events.
- [Phase 06-analytics-and-apis]: Versioned FastAPI read routes reuse the shared analytics and operator-read services directly instead of embedding route-local SQL.
- [Phase 06-analytics-and-apis]: Strategy and system responses expose an operator-read API catalog so future dashboard clients can discover the stable read surface without database knowledge.
- [Phase 06-analytics-and-apis]: Persisted strategy status is authoritative operator-control state, and metadata refreshes preserve it instead of resetting it to `active`.
- [Phase 06-analytics-and-apis]: Disabled strategies fail closed before broker work and record blocked attempts as durable `paper_execution` runs plus `execution_events`.

### Pending Todos

- None in the current milestone execution scope

### Blockers/Concerns

- Docker daemon was unavailable during Phase 1 and 2-01 verification; local PostgreSQL@14 (Homebrew) used instead of Docker Compose.
- Local PostgreSQL-backed verification requires elevated sandbox access in this environment, but the Phase 4 verification slice passed once rerun against the local database.
- The Postgres-backed Phase 06 verification slice also required elevated local access, and the temporary-database fixtures now terminate same-user sessions explicitly before dropping test databases.

## Session Continuity

Last session: 2026-04-18T00:00:00Z
Stopped at: Milestone v1.1 initialization — requirements phase pending
Resume file: None
