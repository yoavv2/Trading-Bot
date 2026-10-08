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

## v1.1 — Execution Correctness & Hardening (Completed 2026-07-15)

**Shipped:** Phases 7–12. Paused 2026-07-07 at Phase 7/12 to build v1.2; resumed 2026-07-12 after the `00-VERIFY` gate went green; Phases 8–12 completed 2026-07-13 through 2026-07-15.

- Phase 7: Correctness Kernel (2026-04-20) — closed order state machine with single `apply_order_transition` entry point, deterministic `client_order_id` idempotency, persistent global kill switch with operator CLI
- Phase 8: Concurrency Guard (2026-07-13) — advisory lock per (strategy_id, session_date), stale-run detection and reclaim
- Phase 9: Reconciliation Rewrite (2026-07-13) — typed snapshots, O(n) matcher, closed findings enum, materialized report, explicit corrective entrypoint
- Phase 10: Startup Hardening (2026-07-13) — fail-fast config validation, log sanitization, single canonical DB lifecycle
- Phase 11: Query Performance (2026-07-14) — preflight bounded to 2 queries, linear reconciliation benchmarks, named-index EXPLAIN proof
- Phase 12: Structural Refactor and Tooling (2026-07-15) — worker split into `worker/commands/*`, service package reorganization, ruff + mypy blocking pre-commit gates; zero behavior change (306-pass baseline held)

**Archived planning docs:** `.planning/milestones/v1.1-paused/` (ROADMAP.md, REQUIREMENTS.md)

## v1.2 — Operator Console v0 (Completed 2026-07-09)

**Shipped:** Phases 13–16. Read-only Next.js operator UI over existing FastAPI read endpoints; inspectability only.

- Phase 13: Console Foundation & System Status (2026-07-08) — app shell, env-driven API client, shared fetch/error/as-of pattern, kill-switch banner + thin GET route (approved exception)
- Phase 14: Strategy & Runs Inspection (2026-07-09) — strategy overview, filterable runs table, run-detail audit trail
- Phase 15: Paper Trading Status (2026-07-09) — positions, open orders, reconciliation result, account snapshot
- Phase 16: Analytics & Charting (2026-07-09) — equity curve chart (`equity_curve` serialization, approved exception) + summary statistics

**Outcome:** Every backtest/risk/paper run inspectable without reading raw logs; console is the surface v1.3 builds operations onto.

## v1.3 — Operator Platform (Started 2026-07-15; FROZEN 2026-10-06, incomplete)

Console evolves from read-only monitor to operations control center. Operator API becomes the single orchestration surface; generic DB-backed Job framework (lifecycle, progress, logs, dependencies, audit); kill-switch and strategy control from UI. First step of the Autonomous Trading Operating System direction. Phase numbering continues from 17.

**Re-scoped 2026-09-23** (repository audit): two mutation paths — long-running operations as Jobs (Console → HTTP → Job orchestration → worker → service) and synchronous safety controls (Console → HTTP → control service); scheduling deferred to a later Paper Automation milestone; identity groundwork (AUD-03) deferred.

- Phase 17: Job Framework (2026-07-20) — complete
- Phase 18: Orchestration Surface (2026-07-21) — complete; ORCH-01/02 Partial (`scripts/` bypass) until Phase 20; post-phase race/test hardening (dependency-cascade and cancellation-timeout sweep races, real-PostgreSQL race tests) completed 2026-07-22 in PR #1
- Phase 19: Job Operations Vertical Slice (2026-09-26) — complete
- Phase 20: Complete Operation Migration & Safety Controls (2026-09-29) — complete
- Phase 20.1: Operator-State Correctness & Paper-Account Ownership (INSERTED 2026-09-30) — 38 plans executed; **not complete** at the freeze (re-verification round 5 `gaps_found`: G-2 partial with E2–E4, E-5, D-1, OD-1 open; CR-01 prohibition in force; human UAT not run); REC-01/REC-02 partial
- Phase 21: Operator Read-Model Foundation (re-planned 2026-09-30) — not started; frozen

**Outcome:** frozen incomplete on 2026-10-06 by the research pivot; the open Phase 20.1 findings are not resolved. v1.4 Operator Console (Phases 22–27) never started. Checkpoint tag `checkpoint/pre-research-pivot-2026-10-06` → `f4ab1a5`.

## Strategy Research — first increment (Implemented 2026-10-07 → 2026-10-08)

**Shipped:** stages S0–S6 of `.planning/research/strategy-research-pivot/01-IMPLEMENTATION-PLAN.md` in commits `9050515`, `ca199b6`, `f167f55`, `519e73f` (pushed).

- S0 research foundation (isolated `trading_research`, Tiingo adapter, catalog, integrity, freeze) · S1 specification v1 and interpreter with parity to the four originals · S2 drafts and immutable versions over HTTP · S3 studies, readiness, Job graph, metrics/evidence/ranking, freeze and once-only final test, exposures · S4 research console and static report · S6 smoke run on real Tiingo data · research as the primary application (`make dev`, `make research-bootstrap`) · S5 bounded assistant (`claude-haiku-5-5`, contract `s5-v2`, live-validated)

**Outcome:** the author → approve → study → compare → freeze → final-test workflow works end to end locally; limitations recorded in `ROADMAP.md` and `README.md`.

**Next agreed order (not authorized):** Pine export and equivalence checking → visual rule builder → portfolio mode.

---
*Last updated: 2026-10-08 — v1.3 recorded as frozen incomplete (Phases 19–20 complete, 20.1 not complete, 21 not started); Strategy Research first increment recorded as implemented.*
*Previous: 2026-09-23 — v1.3 re-scoped (post-phase race/test hardening recorded under Phase 18, Phases 19–21 re-cut, scheduling deferred)*
