# Roadmap: Trading Strategy Platform

## Milestones

- ✅ **v1.0 MVP Backtest & Paper Trading** - Phases 1-6 (shipped 2026-03-15)
- ⏸️ **v1.1 Execution Correctness & Hardening** - Phases 7-12 (Phase 7 shipped 2026-04-20; Phases 8-12 PAUSED, resume after v1.2 — full detail archived in `.planning/milestones/v1.1-paused/`)
- 🚧 **v1.2 Operator Console v0** - Phases 13-16 (in progress)

## Overview

v1.2 gives the single operator a read-only Next.js console over the existing FastAPI read surface so every backtest, risk evaluation, and paper-trading run is inspectable — signals, blocked trades, orders, fills, positions, statistics, kill-switch state — without reading raw logs or querying the database directly. This is a debugging instrument, not a dashboard product: every screen increases inspectability of state that already exists; nothing in this milestone adds a new backend capability, mutation, or control surface. The console consumes only the FastAPI read routes shipped in Phase 6 (`operator_reads`, `analytics`, `system`, `health`) plus the strategy registry read surface.

Phase order follows foundation → screens → charting polish: Phase 13 builds the app shell, the shared fetch/error/as-of pattern every other screen reuses, and the system-status + kill-switch banner (the banner must exist in the shell before any other screen is built on top of it). Phases 14 and 15 build the two independent inspection surfaces (strategy/runs, and live paper-trading status). Phase 16 closes with the charting-heavy analytics view, which depends on the run-detail page built in Phase 14.

## Known Gaps (Backend Read-Surface)

Two v1.2 requirements reference operator-facing data that the existing FastAPI read surface does not yet expose end-to-end. Per the milestone rule ("no new backend capabilities"), these are surfaced here rather than silently patched during roadmap creation. Both are pure wiring/serialization gaps (no new business logic, no new computation) but each still requires a backend code change, which is why they are flagged for explicit operator decision before/at plan time rather than assumed away:

1. **Kill-switch live state has no HTTP route** (blocks STAT-03, KILL-01). `OperatorReadService.get_kill_switch_state()` exists and is fully implemented (`src/trading_platform/services/operator_reads.py:374`) but is currently called only from the worker CLI (`src/trading_platform/worker/__main__.py:517`) — no route in `api/routes/system.py` or elsewhere returns it. The console cannot show current kill-switch state without either (a) a narrow, explicitly-approved exception to add one thin GET route that calls this existing service method, or (b) rendering an honest "not available via current API" state per CONS-02 until that route exists.
2. **Equity curve series is computed but not serialized in the wired analytics response** (blocks ANLX-01). `materialize_backtest_report()` (`src/trading_platform/services/backtest_reporting.py:84`) computes `equity_curve`, but `StrategyAnalyticsService._summarize_backtest()` (`src/trading_platform/services/analytics.py:127-136`) explicitly filters the report down to `run_id, status, started_at, completed_at, summary, metrics` before it reaches `GET /api/v1/analytics/strategies/{strategy_id}` — `equity_curve` never leaves the service. Charting the equity curve requires either a narrow addition of that one field to the existing response, or an honest "not available" state on the chart panel.

**Resolution (operator decision, 2026-07-07): both approved as narrow exceptions.** Read-only exposure of already-computed state counts as inspectability, not new capability. Scope of the exception: (1) one thin GET route calling the existing `get_kill_switch_state()` service method — Phase 13; (2) adding the existing `equity_curve` field to the existing analytics response — Phase 16. No other backend change is authorized under this exception.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)
- v1.2 continues numbering from 13 (v1.1 reserved 7-12; Phases 8-12 paused, not part of this milestone)

<details>
<summary>✅ v1.0 MVP Backtest & Paper Trading (Phases 1-6) — SHIPPED 2026-03-15</summary>

- [x] **Phase 1: Foundation Platform** - Repo skeleton, config, PostgreSQL, migrations, logging, strategy base classes. Completed 2026-03-12.
- [x] **Phase 2: Data and Strategy** - Polygon daily-bar ingestion, market sessions, `TrendFollowingDailyV1`. Completed 2026-03-14.
- [x] **Phase 3: Backtest and Reporting** - Deterministic backtest runner, persisted trades/equity/metrics, reports and exports. Completed 2026-03-14.
- [x] **Phase 4: Risk and Portfolio** - Mandatory risk engine, sizing, blocked-signal audit trail. Completed 2026-03-14.
- [x] **Phase 5: Paper Execution** - Alpaca paper adapter, order lifecycle, fills, reconciliation, session runner. Completed 2026-03-14.
- [x] **Phase 6: Analytics and APIs** - Analytics services, operator-read service layer, versioned FastAPI read routes. Completed 2026-03-15.

Full phase-level goals, success criteria, and plan lists: `.planning/milestones/v1.1-paused/ROADMAP.md` (carries the same v1.0 phase text forward) or git history.

</details>

<details>
<summary>⏸️ v1.1 Execution Correctness & Hardening (Phases 7-12) — PAUSED 2026-07-07 at Phase 7/12</summary>

- [x] **Phase 7: Correctness Kernel** - Closed order state machine, deterministic `client_order_id` idempotency, persistent global kill switch with operator CLI. Completed 2026-04-20.
- [ ] **Phase 8: Concurrency Guard** (paused) - Advisory lock per (strategy_id, session_date), stale-run detection.
- [ ] **Phase 9: Reconciliation Rewrite** (paused) - Typed snapshots, O(n) matcher, closed findings enum, materialized report.
- [ ] **Phase 10: Startup Hardening** (paused) - Fail-fast config validation, log sanitization, DB lifecycle consolidation.
- [ ] **Phase 11: Query Performance** (paused) - Preflight N+1 fix, reconciliation scaling, covering indices.
- [ ] **Phase 12: Structural Refactor and Tooling** (paused) - Worker split, service reorganization, lint/type-check gates.

Full remaining scope, requirements, and phase details: `.planning/milestones/v1.1-paused/ROADMAP.md` and `.planning/milestones/v1.1-paused/REQUIREMENTS.md`. Standing gate: `.planning/00-VERIFY.md` must be green before Phase 8+ resumes.

</details>

### 🚧 v1.2 Operator Console v0 (In Progress)

**Milestone Goal:** Read-only Next.js console over the existing FastAPI read surface — every run, decision, and system state inspectable without reading raw logs. No new backend capabilities.

- [x] **Phase 13: Console Foundation & System Status** - App shell, env-driven API client, shared error/as-of-timestamp pattern, health/system screen, kill-switch global banner. (completed 2026-07-08)
- [x] **Phase 14: Strategy & Runs Inspection** - Strategy overview, filterable runs table, and full run-detail audit trail (signals, risk decisions, orders/fills, metrics). (completed 2026-07-09)
- [x] **Phase 15: Paper Trading Status** - Positions, open orders, latest reconciliation result, latest account snapshot. (completed 2026-07-09)
- [ ] **Phase 16: Analytics & Charting** - Equity curve chart and summary statistics for a selected backtest run.

## Phase Details

### Phase 13: Console Foundation & System Status
**Goal**: Operator can start the console against a running API, and every screen inherits an honest fetch/error/freshness pattern plus a persistent kill-switch banner, before any inspection screen is built on top.
**Depends on**: Nothing (first phase of v1.2; consumes existing v1.0/v1.1 FastAPI read surface)
**Requirements**: CONS-01, CONS-02, CONS-03, STAT-01, STAT-02, STAT-03, KILL-01
**Success Criteria** (what must be TRUE):
  1. Operator starts the console locally with a single documented command, and it reads the FastAPI base URL from local env config (CONS-01).
  2. When the API is unreachable or any endpoint errors, the affected screen shows an explicit error state naming the failing endpoint and status — never an empty or fake-success render (CONS-02); every screen shows an as-of fetch timestamp with a manual refresh control (CONS-03).
  3. Operator can view health, environment name, and DB connection state, plus the latest run of any type with its status and errors, on a system status screen (STAT-01, STAT-02).
  4. Kill-switch state is visible on the system status screen and as a global banner on every console screen whenever tripped (STAT-03, KILL-01) — per Known Gaps #1 resolution, this phase includes the approved narrow exception: one thin GET route exposing the existing `get_kill_switch_state()` service method.
**Plans**: 4 plans

Plans:
- [ ] 13-01-PLAN.md — Thin GET /api/v1/system/kill-switch route + route-level test (approved narrow exception)
- [ ] 13-02-PLAN.md — Next.js console scaffold in console/, env-driven /backend proxy, app shell, documented start command
- [ ] 13-03-PLAN.md — Shared fetchApi/useApiQuery + ErrorState/FetchMeta pattern (vitest-covered) and global KillSwitchBanner in layout
- [ ] 13-04-PLAN.md — System status screen (health, readiness/DB, system info, kill-switch, latest run) + operator verification checkpoint

### Phase 14: Strategy & Runs Inspection
**Goal**: Operator can see the strategy's current state and drill from a runs table into any single run's complete audit trail without reading logs or querying the database.
**Depends on**: Phase 13 (app shell, fetch/error/as-of pattern, kill-switch banner)
**Requirements**: STRA-01, STRA-02, RUNS-01, RUNS-02, RUNS-03, RUNS-04, RUNS-05, RUNS-06
**Success Criteria** (what must be TRUE):
  1. Operator can view `TrendFollowingDailyV1` with its enabled/disabled status and config summary (universe, entry/exit rules, risk params) (STRA-01, STRA-02).
  2. Operator can view a runs table spanning backtest/risk/paper run types with status, session date, created_at, and error indication, and can filter it by run type and status (RUNS-01, RUNS-02).
  3. Operator can open a run detail page and see that run's signals, risk decisions including blocked trades with human-readable reasons, orders and fills with `client_order_id` intent lineage, and the run's persisted metrics (RUNS-03, RUNS-04, RUNS-05, RUNS-06).
**Plans**: 5 plans

Plans:
- [ ] 14-01-PLAN.md — Strategy Overview screen (enabled/disabled + config summary) + nav links (STRA-01, STRA-02)
- [ ] 14-02-PLAN.md — Filterable Runs table with drill-down links (RUNS-01, RUNS-02)
- [ ] 14-03-PLAN.md — Run detail shell + Signals & Risk Decisions + run-scoped filter/CappedDisclosure primitives (RUNS-03, RUNS-04)
- [ ] 14-04-PLAN.md — Run detail Orders/Fills (client_order_id lineage) + persisted Metrics (RUNS-05, RUNS-06)
- [ ] 14-05-PLAN.md — Operator live-verify checkpoint: end-to-end strategy/runs drill-down + truncation-disclosure honesty (verifies STRA-01/02, RUNS-01..06)

### Phase 15: Paper Trading Status
**Goal**: Operator can check the live paper-trading state — what's open, what the broker says, what the account looks like — on one screen.
**Depends on**: Phase 13 (app shell, fetch/error/as-of pattern)
**Requirements**: PAPR-01, PAPR-02, PAPR-03, PAPR-04
**Success Criteria** (what must be TRUE):
  1. Operator can view current positions and open orders (PAPR-01, PAPR-02).
  2. Operator can view the latest reconciliation result and its findings (PAPR-03).
  3. Operator can view the latest account snapshot — equity, cash, buying power (PAPR-04).
**Plans**: 3 plans

Plans:
- [ ] 15-01-PLAN.md — Paper Trading screen: account snapshot + reconciliation from the shared analytics fetch + nav link (PAPR-03, PAPR-04)
- [ ] 15-02-PLAN.md — Current positions + open orders panels composed into /paper (PAPR-01, PAPR-02)
- [ ] 15-03-PLAN.md — Operator live-verify checkpoint: /paper honest empty states + filter disclosure + endpoint-named errors (verifies PAPR-01..04)

### Phase 16: Analytics & Charting
**Goal**: Operator can visually assess a backtest run's performance with an equity curve chart and its standard summary statistics.
**Depends on**: Phase 14 (run selection / run-detail page)
**Requirements**: ANLX-01, ANLX-02
**Success Criteria** (what must be TRUE):
  1. Operator can select a backtest run and view its equity curve as a chart using the locked charting library (ANLX-01) — per Known Gaps #2 resolution, this phase includes the approved narrow exception: the already-computed `equity_curve` field is added to the existing analytics response.
  2. Operator can view summary metrics for a selected run — Sharpe, max drawdown, win rate, P&L, and trade count (ANLX-02).
**Plans**: 3 plans

Plans:
- [ ] 16-01-PLAN.md — Serialize the already-computed `equity_curve` into the analytics response + service test (approved narrow backend exception) (ANLX-01)
- [ ] 16-02-PLAN.md — Recharts equity curve + curated summary-metrics panel on the backtest run-detail surface, single-fetch owner (ANLX-01, ANLX-02)
- [ ] 16-03-PLAN.md — Operator live-verify checkpoint: equity curve + summary metrics, backtest-only gating, honest not-available/endpoint-named errors (verifies ANLX-01, ANLX-02)

## Progress

**Execution Order:**
Phases execute in numeric order. v1.1 Phases 8-12 are paused and excluded from active execution until resumed after v1.2. v1.2 executes 13 → 14 → 15 → 16 (14 and 15 may parallelize once Phase 13 is complete; 16 depends on 14).

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 1. Foundation Platform | v1.0 | 3/3 | Complete | 2026-03-12 |
| 2. Data and Strategy | v1.0 | 3/3 | Complete | 2026-03-14 |
| 3. Backtest and Reporting | v1.0 | 3/3 | Complete | 2026-03-14 |
| 4. Risk and Portfolio | v1.0 | 2/2 | Complete | 2026-03-14 |
| 5. Paper Execution | v1.0 | 3/3 | Complete | 2026-03-14 |
| 6. Analytics and APIs | v1.0 | 3/3 | Complete | 2026-03-15 |
| 7. Correctness Kernel | v1.1 | 3/3 | Complete | 2026-04-20 |
| 8. Concurrency Guard | v1.1 | 0/TBD | Paused | - |
| 9. Reconciliation Rewrite | v1.1 | 0/TBD | Paused | - |
| 10. Startup Hardening | v1.1 | 0/TBD | Paused | - |
| 11. Query Performance | v1.1 | 0/TBD | Paused | - |
| 12. Structural Refactor and Tooling | v1.1 | 0/TBD | Paused | - |
| 13. Console Foundation & System Status | v1.2 | 4/4 | Complete | 2026-07-08 |
| 14. Strategy & Runs Inspection | v1.2 | 5/5 | Complete | 2026-07-09 |
| 15. Paper Trading Status | v1.2 | 3/3 | Complete | 2026-07-09 |
| 16. Analytics & Charting | v1.2 | 0/3 | Not started | - |

---
*Roadmap updated: 2026-07-07 — v1.2 Operator Console v0 phases 13-16 added; v1.0/v1.1 collapsed to historical summary; full v1.1 detail archived in `.planning/milestones/v1.1-paused/`.*
