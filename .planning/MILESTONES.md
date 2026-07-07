# Milestones: Trading Strategy Platform

## v1.0 — MVP Backtest & Paper Trading (Completed 2026-03-15)

**Shipped:** Phases 1–6.

- Phase 1: Foundation Platform — repo skeleton, config, PostgreSQL, migrations, logging, strategy base classes
- Phase 2: Data and Strategy — Polygon daily-bar ingestion, market sessions, `TrendFollowingDailyV1`
- Phase 3: Backtest and Reporting — deterministic backtest runner, persisted trades/equity/metrics, reports and exports
- Phase 4: Risk and Portfolio — mandatory risk engine, sizing, blocked-signal audit trail
- Phase 5: Paper Execution — Alpaca paper adapter, order lifecycle, fills, reconciliation, session runner
- Phase 6: Analytics and APIs — analytics services, operator-read service layer, versioned FastAPI read routes

**Outcome:** End-to-end backtest → risk → paper-execution → next-day inspection loop exists; external broker/data verification still pending (see 00-VERIFY).

## v1.1 — Execution Correctness & Hardening (PAUSED 2026-07-07 at Phase 7/12)

**Shipped:** Phase 7: Correctness Kernel (completed 2026-04-20) — closed order state machine with single `apply_order_transition` entry point, deterministic `client_order_id` idempotency, persistent global kill switch with operator CLI.

**Paused — remaining phases deferred, to resume after v1.2:**

- Phase 8: Concurrency Guard — advisory lock per (strategy_id, session_date), stale-run detection
- Phase 9: Reconciliation Rewrite — typed snapshots, O(n) matcher, closed findings enum, materialized report
- Phase 10: Startup Hardening — fail-fast config validation, log sanitization, DB lifecycle consolidation
- Phase 11: Query Performance — preflight N+1 fix, reconciliation scaling, covering indices
- Phase 12: Structural Refactor and Tooling — worker split, service reorganization, lint/type-check gates

**Archived planning docs:** `.planning/milestones/v1.1-paused/` (ROADMAP.md, REQUIREMENTS.md)

**Standing gate:** `.planning/00-VERIFY.md` must be green before Phase 8+ backend work resumes (env override bug, Polygon production-path read unverified, Alpaca credentials unconfigured).

## v1.2 — Operator Console v0 (Started 2026-07-07)

Read-only Next.js operator UI over existing FastAPI read endpoints. Inspectability only — no new backend capabilities. Phase numbering starts at 13.

---
*Last updated: 2026-07-07 — v1.1 paused, v1.2 started*
