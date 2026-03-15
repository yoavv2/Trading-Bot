# Phase 6: Analytics and APIs - Research

**Researched:** 2026-03-14
**Domain:** Strategy analytics, operator inspection reads, FastAPI read APIs, and kill-switch or observability controls on top of the completed paper-execution loop
**Confidence:** HIGH

<planning_inputs>
## Planning Inputs

### Available Context
- No `06-CONTEXT.md` exists for this phase. Planning therefore relies on `.planning/PROJECT.md`, `.planning/ROADMAP.md`, `.planning/STATE.md`, completed summaries from Phase 3 through Phase 5, and the current codebase.
- `.planning/REQUIREMENTS.md` is still absent in this repo, so requirement mapping must continue to come from `.planning/ROADMAP.md` and `.planning/PROJECT.md`.
- Phase 6 now has the durable artifacts it needs to read from rather than redesign: `backtest_metrics`, `risk_events`, `paper_orders`, `paper_fills`, `positions`, `account_snapshots`, `execution_events`, and `strategy_runs`.
- The current repository already has the shell of the Phase 6 surface:
  - `src/trading_platform/services/analytics.py` exists but is still a placeholder.
  - FastAPI boots successfully but only serves health, system, and strategy-list reads.
  - Worker or script entrypoints already exist for backtest reporting, risk evaluation, paper submission, lifecycle sync, and reconciliation.
  - Structured JSON logging already exists through `src/trading_platform/core/logging.py`, but the logs are not yet paired with a durable operator status or control surface.

### Locked Decisions From Project State
- The platform remains local-first, PostgreSQL-backed, and CLI-first in v1.
- `strategy_runs` remains the canonical orchestration root across dry bootstraps, backtests, risk evaluations, paper execution, and reconciliation.
- Broker state is the source of truth for live paper-trading state; analytics and inspection must not paper over unresolved broker drift.
- The operator must be able to inspect exactly what happened the next day without reconstructing facts from raw logs.
- The future dashboard should consume FastAPI reads, not direct database access.

### Claude's Discretion
- Whether to materialize additional analytics metrics in tables or compute them on read, so long as the values are derived from persisted artifacts and remain deterministic.
- How to package shared read logic so CLI and API surfaces reuse the same query layer instead of embedding SQL in route handlers.
- How to persist kill-switch actions and strategy disable or enable reasons while keeping the phase aligned with the current `strategy_runs` and `execution_events` pattern.

</planning_inputs>

<research_summary>
## Summary

Phase 6 should stay read-focused and operator-focused rather than introducing a dashboard-shaped abstraction too early.

1. The analytics foundation should become a real service layer, not a route-only implementation. The current codebase already has a placeholder `AnalyticsService`, a materialized `backtest_metrics` table, and durable paper-execution artifacts. Phase 6 should turn those into shared read models that both CLI and FastAPI can consume.
2. Backtest analytics should expand toward the project’s stated strategy-statistics profile, but paper-trading analytics must stay honest to the persistence that exists today. The codebase can confidently expose operational paper metrics such as latest equity, open exposure, submission and fill counts, blocked sessions, and recent execution findings. It should not invent closed-lot or realized trade metrics that are not durably persisted yet.
3. The phase should remain sequential:
   - `06-01` builds the analytics and inspection service foundation plus a CLI-first reporting surface.
   - `06-02` exposes stable FastAPI reads on top of that shared foundation.
   - `06-03` adds operator controls, kill-switch enforcement, and observability outputs for confident daily use.
4. The cleanest kill-switch path is to reuse the existing persisted `Strategy.status` field for current state while adding a durable audit trail for control changes and blocked execution attempts. Do not make the kill switch a transient config-only flag.

**Primary recommendation:** Implement a real analytics and operator-read service first, expand backtest metrics where the project explicitly calls for richer statistics, expose versioned FastAPI reads from that shared service layer, and finish the phase by wiring durable strategy disable or enable controls plus an operator status view that surfaces recent blocking conditions.

</research_summary>

<codebase_findings>
## Codebase Findings

### Existing Reusable Assets
- `src/trading_platform/services/backtest_reporting.py` already computes and materializes deterministic backtest metrics from persisted artifacts and exports markdown or JSON summaries plus CSV files.
- `src/trading_platform/db/models/backtest_metric.py` gives Phase 6 a natural place to extend richer backtest metrics without inventing a second backtest-summary store.
- `src/trading_platform/services/paper_execution.py` and `src/trading_platform/services/reconciliation.py` already persist the paper-trading artifacts Phase 6 needs to inspect: approved candidates, orders, fills, positions, account snapshots, reconciliation findings, and blocked execution conditions.
- `src/trading_platform/db/models/strategy.py` already has a persisted `status` enum with `active`, `disabled`, and `archived`, which is an obvious anchor for a kill-switch surface.
- `src/trading_platform/api/app.py` and the existing route modules establish a small but working FastAPI pattern that Phase 6 can extend without architectural churn.
- `src/trading_platform/core/logging.py` already provides machine-readable JSON logs; the missing piece is consistent run or strategy context and an operator-facing status surface that can summarize failures without tailing logs.

### Current Gaps Blocking Phase 6
- `src/trading_platform/services/analytics.py` is still a placeholder and is not connected to the persisted backtest or paper-execution data.
- There is no shared read service that returns strategy-level or run-level inspection payloads for routes and scripts to reuse.
- FastAPI currently exposes no versioned reads for runs, metrics, orders, fills, positions, account snapshots, risk events, or execution events.
- Strategy disable or enable state is persisted in the schema but is not enforced anywhere in paper execution or exposed as an operator control.
- Current structured logs are useful but insufficient as the primary operator inspection surface. The product direction calls for stable reads and durable audit trails, not log-only debugging.

### Planning Implications
- The Phase 6 plans should add service-layer reads before API routes so the CLI and API surfaces stay consistent.
- Paper analytics should be framed as operational and inspection-oriented until the persistence model can support deeper closed-trade accounting.
- Strategy disable or enable must become a real, persisted operator control that blocks new paper execution intentionally and records why.
- The API routes in `06-02` should stay read-only and versioned. Mutating or run-triggering endpoints can wait until a later milestone.

</codebase_findings>

<implementation_edge_cases>
## Implementation Edge Cases

### Metric Honesty
- The project’s desired analytics profile includes metrics such as CAGR, Sharpe, Sortino, expectancy, turnover, and best or worst trade. Those are realistic for backtests because the platform already has deterministic backtest trades and equity snapshots.
- The same depth is not yet trustworthy for paper trading because the current persistence emphasizes orders, fills, positions, account snapshots, and reconciliation findings rather than normalized closed paper trades. Phase 6 should report paper performance conservatively from what is durably stored today.

### Stable Inspection Reads
- API consumers and future dashboards need stable ordering, explicit limits, and simple filter semantics. Route handlers should not contain ad hoc SQL or inconsistent joins.
- Inspection queries need to join symbol tickers and run metadata for human-readable payloads without leaking ORM models directly into API responses.

### Kill-Switch Semantics
- The safest v1 behavior is to block new paper-order submission and scheduled paper sessions when a strategy is disabled, while leaving read-only inspection, reporting, and possibly backtests available.
- Kill-switch actions must be durable and reviewable. A mere config flag or a silent environment override is not enough for REQ-10.

### Failure Visibility
- The paper-trading loop already fails closed on reconciliation drift. Phase 6 should surface those blocking conditions through analytics, operator status, and API reads rather than only through worker output.
- Recent failures should be queryable by strategy and session, with clear reasons and blocking flags.

</implementation_edge_cases>

<recommendations>
## Recommended Architecture

### Analytics and Read Layer
- Replace `PlaceholderAnalyticsService` with a real analytics implementation in `src/trading_platform/services/analytics.py`.
- Add a shared read-service module such as `src/trading_platform/services/operator_reads.py` for:
  - strategy analytics summaries
  - recent strategy runs
  - run details
  - paper orders and fills
  - positions and account snapshots
  - risk events and execution events
- Keep the read layer serializable and route-agnostic so both scripts and FastAPI can call it directly.

### Backtest Metrics
- Extend `backtest_metrics` to cover the additional project-level statistics that are derivable from persisted backtest trades and equity snapshots.
- Continue to use Phase 3’s persisted-artifact approach rather than re-running simulations or recomputing from raw config alone.

### FastAPI Read Surface
- Add versioned route modules for analytics, run inspection, and operational inspection under `/api/v1`.
- Keep the Phase 6 API strictly read-only.
- Prefer a small number of coherent route modules over one giant catch-all handler.

### Operator Controls and Status
- Reuse `Strategy.status` as the source of truth for active versus disabled state.
- Persist control changes and blocked execution attempts through the existing audit primitives, likely by extending `strategy_runs` with a control-oriented run type and attaching durable events.
- Add CLI-first control and status entrypoints before or alongside any control-related API work.

### Observability
- Standardize structured log context around `strategy_id`, `run_id`, `session_date`, and blocking reason when available.
- Add a status summary that reports:
  - strategy enabled or disabled state
  - latest account snapshot
  - latest paper-session outcome
  - latest reconciliation block state
  - recent execution or control events

### Testing Strategy
- Add `tests/test_analytics_service.py` for richer backtest metrics plus paper-inspection summaries.
- Add `tests/test_api_reads.py` for seeded FastAPI reads and empty-state or not-found behavior.
- Add `tests/test_operator_controls.py` for strategy disable or enable flows and blocked paper execution.
- Extend `tests/test_backtest_reporting.py`, `tests/test_paper_execution.py`, `tests/test_app_boot.py`, and `tests/test_db_migrations.py` where the new read or control surfaces overlap existing patterns.

</recommendations>

## Validation Architecture

- `tests/test_analytics_service.py`
  - verify richer backtest metrics, paper summary behavior, and typed inspection reads
- `tests/test_api_reads.py`
  - verify FastAPI analytics, runs, orders, fills, positions, account snapshots, risk events, and execution-event reads
- `tests/test_operator_controls.py`
  - verify durable strategy disable or enable flows, blocked paper execution, and status reporting
- Extend `tests/test_backtest_reporting.py`
  - verify expanded metric rendering stays deterministic and zero-safe
- Extend `tests/test_paper_execution.py`
  - verify kill-switch enforcement does not permit new paper execution when disabled
- Extend `tests/test_db_migrations.py`
  - verify any new analytics fields or control-run enum values land at Alembic head
- Extend `tests/test_app_boot.py`
  - verify the FastAPI app boots with the new routes registered

**Quick command:** `PYTHONPATH=src .venv/bin/pytest tests/test_analytics_service.py tests/test_api_reads.py tests/test_operator_controls.py tests/test_backtest_reporting.py tests/test_paper_execution.py tests/test_app_boot.py tests/test_db_migrations.py -q`

**Full command:** `PYTHONPATH=src .venv/bin/pytest tests -q`

## Plan Split Recommendation

### 06-01: Build analytics summaries, per-run metrics, and historical inspection views
- Replace the placeholder analytics contract with real service-layer reads and richer backtest metrics.
- Add a shared inspection layer for run history and operational entities.
- Expose a CLI-first analytics or inspection report surface that later API routes can mirror.

### 06-02: Expose operator-facing FastAPI read endpoints for runs, trades, positions, metrics, and risk events
- Add versioned, read-only FastAPI routes for strategy analytics, run inspection, and recent operational state.
- Keep route handlers thin by delegating to the shared Phase 6 service layer.
- Lock the surface with seeded API tests rather than relying on manual curl checks.

### 06-03: Add operational controls, kill-switch flows, and observability outputs needed for confident daily use
- Turn strategy disable or enable into a real persisted operator control with durable audit records.
- Block new paper execution when the strategy is disabled and expose the blocked reason clearly.
- Add an operator status surface plus richer structured logging for recent failures and current control state.

## Sources

No external sources were required for this phase beyond the current project planning docs and codebase.

Current project sources:
- `.planning/PROJECT.md`
- `.planning/ROADMAP.md`
- `.planning/STATE.md`
- `.planning/phases/03-backtest-and-reporting/03-03-SUMMARY.md`
- `.planning/phases/04-risk-and-portfolio/04-02-SUMMARY.md`
- `.planning/phases/05-paper-execution/05-03-SUMMARY.md`
- `.planning/phases/05-paper-execution/05-RESEARCH.md`
- `config/app.yaml`
- `src/trading_platform/api/app.py`
- `src/trading_platform/api/routes/health.py`
- `src/trading_platform/api/routes/strategies.py`
- `src/trading_platform/api/routes/system.py`
- `src/trading_platform/core/logging.py`
- `src/trading_platform/core/settings.py`
- `src/trading_platform/db/models/account_snapshot.py`
- `src/trading_platform/db/models/backtest_metric.py`
- `src/trading_platform/db/models/execution_event.py`
- `src/trading_platform/db/models/paper_fill.py`
- `src/trading_platform/db/models/paper_order.py`
- `src/trading_platform/db/models/position.py`
- `src/trading_platform/db/models/risk_event.py`
- `src/trading_platform/db/models/strategy.py`
- `src/trading_platform/db/models/strategy_run.py`
- `src/trading_platform/services/analytics.py`
- `src/trading_platform/services/backtest_reporting.py`
- `src/trading_platform/services/bootstrap.py`
- `src/trading_platform/services/paper_execution.py`
- `src/trading_platform/services/reconciliation.py`
- `src/trading_platform/worker/__main__.py`
- `tests/test_app_boot.py`
- `tests/test_backtest_reporting.py`
- `tests/test_db_migrations.py`
- `tests/test_execution_reconciliation.py`
- `tests/test_paper_execution.py`

---
*Phase: 06-analytics-and-apis*
*Research completed: 2026-03-14*
