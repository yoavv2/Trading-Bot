# Roadmap: Trading Strategy Platform

> **Archive note (updated 2026-09-23):** This file was originally archived as a snapshot when v1.1 **paused at Phase 7 (2026-07-07)**. At that pause date, Phases 8–12 existed only as un-planned outlines (`Plans: TBD`, unchecked, "Not started"). v1.1 later resumed and shipped (Phases 8–12 completed 2026-07-13 → 2026-07-15), and the executed detail was maintained in the active `.planning/ROADMAP.md`. On 2026-09-23 the snapshot was **updated after the fact**: the Phase 8–12 detail blocks (final goals, success criteria, 26 executed plan entries), their checklist ticks, and their Progress rows were replaced with the final completed records moved from the active roadmap, which now keeps only a summary for shipped milestones. Everything else in this file (v1.0 content, Phase 7, requirement mapping) remains as it was at the pause date.

## Overview

This roadmap covers the first executable milestone for the project: a trustworthy single-user trading platform that can ingest daily U.S. equities data, run deterministic backtests for `TrendFollowingDailyV1`, execute the same strategy in paper trading, and let the operator inspect exactly what happened the next day. It intentionally front-loads platform structure, data integrity, risk controls, and auditability so the system can be validated with confidence before expanding into a broader multi-strategy platform and a dashboard in later milestones.

The roadmap is MVP-scoped. Multi-strategy expansion, richer portfolio analytics, and the eventual web dashboard remain part of the project direction in `.planning/PROJECT.md`, but they are deferred until the first research -> backtest -> paper-trading loop is trustworthy.

## Requirement Mapping

| ID | Requirement | Phase |
|----|-------------|-------|
| REQ-01 | Build a single-user operator platform with one operator, one brokerage account, one deployment owner, one credential set, and one portfolio in v1 | Phase 1 |
| REQ-02 | Design the core as a multi-strategy-ready platform with isolated strategy modules, per-strategy config boundaries, and future strategy-selection support | Phase 1 |
| REQ-03 | Implement `TrendFollowingDailyV1` for the initial daily U.S. equities universe | Phase 2 |
| REQ-04 | Ingest and persist reproducible historical daily OHLCV bars with normalization, symbol metadata, and calendar awareness | Phase 2 |
| REQ-05 | Run deterministic backtests, persist runs, trades, equity curves, and summary metrics, and make fee/slippage assumptions explicit | Phase 3 |
| REQ-06 | Persist candles, signals, strategy runs, orders, fills, positions, account snapshots, risk events, and performance summaries in PostgreSQL | Phases 1-6 |
| REQ-07 | Route every signal through a mandatory risk engine before execution | Phase 4 |
| REQ-08 | Support daily scheduled paper trading through Alpaca with persistent order lifecycle tracking | Phase 5 |
| REQ-09 | Produce trustworthy analytics for backtests and paper trading, including inspectable runs, orders, positions, and metrics | Phase 6 |
| REQ-10 | Make observability part of the product through structured logs, failure visibility, blocked-trade explanations, kill-switch support, and audit trails | Phase 6 |
| REQ-11 | Externalize and version strategy, risk, and runtime configuration | Phase 1 |
| REQ-12 | Keep the first implementation local-first and Dockerized, with FastAPI reserved for core APIs and future dashboard consumption | Phase 1 |

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

### Milestone v1.0 — Completed 2026-03-15

- [x] **Phase 1: Foundation Platform** - Stand up the Python, FastAPI, PostgreSQL, and Docker skeleton with the core trading-platform interfaces and persistence model. Completed 2026-03-12.
- [x] **Phase 2: Data and Strategy** - Build the daily-bar data pipeline and implement `TrendFollowingDailyV1` as the first isolated strategy module. (completed 2026-03-14)
- [x] **Phase 3: Backtest and Reporting** - Run deterministic daily-bar backtests and persist research outputs that are usable for strategy evaluation. Completed 2026-03-14.
- [x] **Phase 4: Risk and Portfolio** - Add the portfolio state and mandatory risk-validation pipeline that gates every signal before execution. Completed 2026-03-14.
- [x] **Phase 5: Paper Execution** - Turn approved daily signals into Alpaca paper orders with scheduling, lifecycle tracking, and reconciliation. Completed 2026-03-14.
- [x] **Phase 6: Analytics and APIs** - Make the system measurable and inspectable through analytics, operational controls, and operator-facing API reads. Completed 2026-03-15.

### Milestone v1.1 — Execution Correctness & Hardening

- [x] **Phase 7: Correctness Kernel** - Enforce a closed order state machine with a single transition entry point, deterministic client_order_id idempotency, and a persistent kill switch. (completed 2026-04-20)
- [x] **Phase 8: Concurrency Guard** - Enforce at-most-one active run per (strategy_id, session_date) via PostgreSQL advisory lock with stale-run detection and crash-safe release. (completed 2026-07-13)
- [x] **Phase 9: Reconciliation Rewrite** - Replace string-classified reconciliation with typed snapshots, an indexed O(n) matcher, a closed findings enum, and a materialized report. (completed 2026-07-13)
- [x] **Phase 10: Startup Hardening** - Gate process startup on validated config, sanitized logs with no credential leakage, and a single canonical DB connection lifecycle. (completed 2026-07-13)
- [x] **Phase 11: Query Performance** - Eliminate the paper-preflight N+1 query, assert O(n) reconciliation scaling, and add covering indices verified by EXPLAIN. (completed 2026-07-14)
- [x] **Phase 12: Structural Refactor and Tooling** - Split worker orchestration, reorganize service modules under declared boundaries, consolidate settings, and wire lint/type-check CI gates. (completed 2026-07-15)

## Phase Details

### Phase 1: Foundation Platform
**Goal:** Stand up the local-first platform skeleton, minimal persistence foundation, and extensibility contracts so the project starts as a strategy platform instead of a one-off script.
**Depends on:** Nothing (first phase)
**Requirements**: REQ-01, REQ-02, REQ-11, REQ-12
**Research:** Low — mostly internal architecture choices; no dedicated research phase is required before planning.
**Success Criteria** (what must be TRUE):
  1. The operator can boot the local stack with Docker and connect the API and worker processes to PostgreSQL using externalized configuration.
  2. The repository layout, configuration model, and runtime entrypoints clearly model one operator, one account, and one portfolio without introducing multi-user complexity.
  3. A minimal schema and migration flow can be added cleanly in Phase 1 without forcing later plans to undo the foundation work.
  4. Core interfaces and service boundaries exist for strategies, market data providers, broker adapters, execution, and risk evaluation so later strategies or adapters do not require structural rewrites.
**Plans:** 3 plans

Plans:
- [x] 01-01: Scaffold the repo layout, Docker Compose stack, FastAPI app, worker entrypoint, and config loading
- [x] 01-02: Define core domain models, SQLAlchemy or SQLModel persistence, and database migrations
- [x] 01-03: Implement strategy, provider, execution, and risk interfaces plus the initial strategy registry shell

### Phase 2: Data and Strategy
**Goal:** Create a reliable daily-bar research input and the first isolated strategy implementation for the target universe.
**Depends on:** Phase 1
**Requirements**: REQ-03, REQ-04
**Research:** Medium — confirm Polygon daily-bar semantics, trading-calendar handling, and required data normalization boundaries.
**Success Criteria** (what must be TRUE):
  1. Historical daily bars for the initial universe can be ingested from Polygon and persisted reproducibly without manual cleanup.
  2. The platform stores normalized bar data, symbol metadata, and enough calendar context to run daily workflows consistently.
  3. `TrendFollowingDailyV1` computes its moving-average indicators and emits deterministic entry and exit signals for configured symbols.
  4. Strategy parameters such as universe, moving-average windows, and exits live in external config rather than inside strategy code.
**Plans:** 3/3 plans complete

Plans:
- [x] 02-01: Build the Polygon data provider, ingestion pipeline, and daily-bar normalization flow
- [x] 02-02: Model symbol metadata, trading-calendar awareness, and reusable market-data access patterns
- [x] 02-03: Implement `TrendFollowingDailyV1` with isolated config, indicators, and signal generation

### Phase 3: Backtest and Reporting
**Goal:** Validate the first strategy offline through deterministic backtests with persisted outputs and explicit assumptions.
**Depends on:** Phase 2
**Requirements**: REQ-05
**Research:** Medium — confirm fee and slippage conventions, deterministic run boundaries, and the simplest daily-bar simulation assumptions that remain credible.
**Success Criteria** (what must be TRUE):
  1. Running the same backtest twice with the same data and config produces the same trades, equity curve, and summary metrics.
  2. Backtest runs persist their configuration, trades, positions or equity snapshots, and performance summaries in the database.
  3. Fee and slippage assumptions are explicit, versioned with the run, and visible in the generated results.
  4. The operator can inspect a trustworthy report or export for a run without reading raw logs.
**Plans:** 3/3 plans complete

Plans:
- [x] 03-01: Add typed backtest settings and persistence foundation for Phase 3 artifacts
- [x] 03-02: Build the deterministic daily-bar backtest runner and execution flow
- [x] 03-03: Generate run reports, metrics summaries, and exports for research inspection

### Phase 4: Risk and Portfolio
**Goal:** Add deterministic sizing, portfolio state, and hard execution guardrails so no signal can bypass risk controls.
**Depends on:** Phase 3
**Requirements**: REQ-07
**Research:** Low — policy thresholds are already defined; research is limited to implementation edge cases around stale data and duplicate protection.
**Success Criteria** (what must be TRUE):
  1. Every strategy signal flows through a risk-validation pipeline before it can become an executable order intent.
  2. Position sizing, max position limits, allocation caps, stale-data checks, and duplicate-position prevention are deterministic and testable.
  3. Rejected signals are persisted with human-readable reasons that explain which risk rule blocked them.
  4. Portfolio state tracks the cash, exposure, and open-position context needed by the daily strategy and later paper execution.
**Plans:** 2/2 plans complete

Plans:
- [x] 04-01: Build portfolio state, sizing logic, and exposure accounting
- [x] 04-02: Implement the risk-validation pipeline, rejection logging, and execution gating rules

### Phase 5: Paper Execution
**Goal:** Convert approved daily signals into Alpaca paper orders on a schedule while keeping broker state and internal state aligned.
**Depends on:** Phase 4
**Requirements**: REQ-08
**Research:** High — Alpaca order-state behavior, scheduling constraints, reconciliation edge cases, and restart/idempotency failure modes need dedicated planning attention.
**Success Criteria** (what must be TRUE):
  1. The strategy can run on a fixed daily schedule and turn approved signals into Alpaca paper orders without manual intervention.
  2. Orders, fills, positions, and account snapshots are persisted and remain consistent across process restarts.
  3. Broker reconciliation detects mismatches or repeated order failures and blocks new execution when state is unsafe.
  4. The operator can inspect which orders were submitted, filled, blocked, or retried on the next trading day.
**Plans:** 3/3 plans complete

Plans:
- [x] 05-01: Implement the Alpaca broker adapter and paper-order submission flow
- [x] 05-02: Add scheduled daily execution, order lifecycle updates, and fill ingestion
- [x] 05-03: Build reconciliation, restart safety, and execution-stop conditions for unsafe broker state

### Phase 6: Analytics and APIs
**Goal:** Make the platform measurable, inspectable, and operator-friendly enough to decide whether the MVP is trustworthy.
**Depends on:** Phase 5
**Requirements**: REQ-09, REQ-10
**Research:** Medium — metric conventions and operator-facing API reads benefit from research, but the phase is driven mostly by the platform behavior already built.
**Success Criteria** (what must be TRUE):
  1. Per-run and current-strategy metrics are queryable and trustworthy enough for the operator to compare runs and review paper performance.
  2. The operator can inspect recent signals, orders, fills, positions, account snapshots, and risk events through stable reads instead of raw logs alone.
  3. Structured logs, visible failures, kill-switch behavior, and audit trails make ambiguous state transitions observable and reviewable.
  4. The MVP verdict is satisfied: the operator can backtest the strategy, enable daily paper trading, and inspect exactly what happened with confidence.
**Plans:** 3/3 plans complete

Plans:
- [x] 06-01: Build analytics summaries, per-run metrics, and historical inspection views
- [x] 06-02: Expose operator-facing FastAPI read endpoints for runs, trades, positions, metrics, and risk events
- [x] 06-03: Add operational controls, kill-switch flows, and observability outputs needed for confident daily use

---

## Milestone v1.1 — Execution Correctness & Hardening

**Milestone goal:** Prove every order intent has exactly one legal lifecycle, one broker identity, and one audit trace before extending platform capabilities.

**Tier ordering constraint:** STRUCT-01 mandates that no Tier 3 structural refactor lands before all Tier 0 requirements are verified complete. Phases 7-9 close Tier 0; Phase 10 closes Tier 1; Phase 11 closes Tier 2; Phase 12 closes Tier 3.

### Phase 7: Correctness Kernel
**Goal:** Every order state transition is governed by a closed enum state machine with a single entry point, every submission carries a deterministic broker identity, and the kill switch is a durable persisted invariant — not an in-process flag.
**Depends on:** Phase 6 (v1.0 complete)
**Requirements**: ORDER-01, ORDER-02, ORDER-03, ORDER-04, ORDER-05, ORDER-06, ORDER-07, IDEM-01, IDEM-02, IDEM-03, IDEM-04, SAFE-01, SAFE-02, SAFE-03, SAFE-04, SAFE-05
**Success Criteria** (what must be TRUE):
  1. Calling `apply_order_transition(order_id, event)` with an illegal `(from_state, event)` pair raises `IllegalOrderTransition` — no other code path can mutate order state, and the test suite asserts this with a module-boundary check.
  2. Every accepted or rejected transition appends a new `OrderEvent` row; the `orders` table row is never the sole record of a transition.
  3. Given identical `(strategy_id, session_date, symbol, side, intent_hash)` inputs, `client_order_id` produces the same byte-for-byte value across separate processes and restarts; a DB `UNIQUE` constraint enforces one row per intent.
  4. Retry of an existing intent returns the persisted row rather than inserting a duplicate; broker-response matching resolves by `client_order_id` first.
  5. Kill-switch state is persisted in the DB, checked before every broker submission, survives a worker restart without manual reset, and when tripped allows reconciliation and logging to continue while blocking only new submissions.
**Plans:** 3/3 plans complete

Plans:
- [x] 07-01: Build the closed order-lifecycle kernel
- [x] 07-02: Turn the paper-submission path into a deterministic idempotent intent pipeline
- [ ] 07-03: Add the restart-safe global kill switch

### Phase 8: Concurrency Guard

**Milestone**: v1.1 Execution Correctness & Hardening (resumed 2026-07-12 after v1.2 shipped and the `00-VERIFY` gate went green)
**Goal:** At most one active run per `(strategy_id, session_date)` can execute side effects; the lock is acquired before any broker call or state-affecting write, released on all exit paths including crash, and stale runs are detectable and cleanly handled.
**Depends on:** Phase 7 (Correctness Kernel — complete)
**Requirements**: LOCK-01, LOCK-02, LOCK-03, LOCK-04, LOCK-05, LOCK-06
**Success Criteria** (what must be TRUE):

  1. A second process attempting to start the same `(strategy_id, session_date)` run while the first holds the advisory lock exits cleanly with a typed message — no broker calls or DB writes occur before the lock is confirmed.
  2. A run that holds the lock writes `run_status=running` and `run_started_at` as its first persisted action; a single query can identify any run past the declared heartbeat/timeout threshold as stale.
  3. When the lock is free but a stale `running` row exists, the new run marks that row `stale` and continues; it does not silently overwrite or ignore it.
  4. A restart/crash test confirms the session-scoped advisory lock is released automatically on crash, and a subsequent run can acquire it cleanly without manual intervention.

**Plans**: 5 plans

Plans:

- [x] 08-01-PLAN.md — Add STALE to StrategyRunStatus enum (+ migration 0016) and externalize stale_run_timeout_minutes (LOCK-04)
- [x] 08-02-PLAN.md — Advisory-lock primitive: ConcurrentRunLockedError, key derivation, non-blocking session_run_lock() with crash-release (LOCK-01, LOCK-06)
- [x] 08-03-PLAN.md — Stale-run single-query detection + tuple-scoped STALE reclaim with ExecutionEvent audit (LOCK-04, LOCK-05)
- [x] 08-04-PLAN.md — Lock-guard + reorder run_paper_order_submission: lock-before-side-effects, running-row-first, reclaim-on-entry (LOCK-01, LOCK-02, LOCK-03, LOCK-05)
- [x] 08-05-PLAN.md — Worker CLI reserved exit code for lock denial + crash/restart e2e proof (LOCK-01, LOCK-06)

### Phase 9: Reconciliation Rewrite

**Milestone**: v1.1 Execution Correctness & Hardening (resumed 2026-07-13 after Phase 8 completed)
**Goal:** Reconciliation produces typed findings from normalized snapshots via an O(n) indexed matcher, is strictly read-only, and emits one materialized report tied to the source snapshots — string-classified findings and nested-scan matching are eliminated.
**Depends on:** Phase 7 (Correctness Kernel — complete)
**Requirements**: RECON-01, RECON-02, RECON-03, RECON-04, RECON-05, RECON-06, RECON-07, RECON-08, RECON-09
**Success Criteria** (what must be TRUE):

  1. Broker and local snapshots cross the reconciliation boundary as typed dataclasses — no `dict[str, Any]` or raw string field passes the snapshot boundary.
  2. The matcher resolves positions by a keyed map on `(symbol, account, side)`; a benchmark test asserts linear (not quadratic) scaling as entity count grows.
  3. Every finding is a value from the closed `ReconciliationFinding` enum: `MISSING_LOCAL`, `MISSING_BROKER`, `QUANTITY_MISMATCH`, `PRICE_MISMATCH`, `STATE_MISMATCH` — no string-classified finding reaches the report.
  4. Running reconciliation produces zero DB writes to execution state (order rows, positions, account snapshots); corrective action is a separate explicit step on a different code path.
  5. Flat positions (zero quantity on both sides) produce zero findings; a materialized report is always emitted with findings tied to their source snapshots.

**Plans**: 4 plans

Plans:

- [x] 09-01-PLAN.md — Typed reconciliation contracts: closed ReconciliationFinding enum + typed local snapshots + (symbol, account, side) identity key (RECON-05, RECON-07)
- [x] 09-02-PLAN.md — Pure O(n) indexed matcher (flat positions -> zero findings) + count-based linear-scaling benchmark (RECON-06, RECON-08)
- [x] 09-03-PLAN.md — Read-only reconcile orchestrator over typed snapshots + one materialized report tied to source snapshots (RECON-01, RECON-02, RECON-03, RECON-09)
- [x] 09-04-PLAN.md — Explicit corrective entrypoint separated from reconcile + session-runner rewire + closed-enum consumer migration (RECON-04)

### Phase 10: Startup Hardening

**Milestone**: v1.1 Execution Correctness & Hardening (resumed 2026-07-13 after Phase 9 completed)
**Goal:** The process refuses to boot on invalid config, logs never emit credentials or unmasked broker order IDs under default config, and one canonical DB connection lifecycle governs all execution flows.
**Depends on:** Phases 7-9 (Tier 0 complete — Correctness Kernel, Concurrency Guard, Reconciliation Rewrite all done)
**Requirements**: CFG-01, CFG-02, CFG-03, CFG-04, CFG-05, CFG-06, CFG-07, LOG-01, LOG-02, LOG-03, LOG-04, LOG-05, LOG-06, DB-01, DB-02, DB-03, DB-04, DB-05, DB-06
**Success Criteria** (what must be TRUE):

  1. Starting the process with a missing required secret, an unreachable DB, an out-of-range tolerance value, or a conflicting mode combination exits with a non-zero code and a single actionable error message naming the failed field — no domain service initializes before all validations pass.
  2. An enforcement test asserts that no emitted log line under default config contains `password=`, `api_key=`, `Authorization:` header values, or a full broker order ID.
  3. One connection-lifecycle model is in code (the competing `@lru_cache` / `_ENGINE_CACHE` duality is removed); all execution flows use the single canonical session import path.
  4. Every execution flow runs within an explicit transaction boundary; a commit occurs only after both the broker call and the state transition persist successfully.
  5. When a rollback occurs after a broker side effect has already happened, a reconciliation task is scheduled — rollback alone is never the complete response.

**Plans**: 6 plans

Plans:

- [x] 10-01-PLAN.md — Config validation core: ExecutionMode enum + typed ConfigValidationError + validate_config (CFG-01, CFG-02, CFG-03, CFG-05, CFG-07)
- [x] 10-02-PLAN.md — Log sanitization core: sanitize() redaction + broker-id last-6 masking + get_logger wrapper (LOG-02, LOG-03, LOG-04, LOG-05)
- [x] 10-03-PLAN.md — DB lifecycle: formalize the one reloadable manager, resolve caching duality, single canonical import path (DB-01, DB-02, DB-03)
- [x] 10-04-PLAN.md — Paper-execution transaction integrity: explicit boundary, commit-after-both, rollback schedules reconciliation (DB-04, DB-05, DB-06)
- [x] 10-05-PLAN.md — Startup gate wired into every entrypoint: DB preflight + non-zero exit before service init (CFG-04, CFG-06)
- [x] 10-06-PLAN.md — Logger migration + formatter backstop + import-boundary & emitted-line enforcement tests (LOG-01, LOG-06)

### Phase 11: Query Performance

**Milestone**: v1.1 Execution Correctness & Hardening (resumed 2026-07-13 after Phase 10 completed)
**Goal:** Paper preflight issues at most 2 queries regardless of portfolio size, reconciliation scales linearly with entity count, and every critical query path has a named covering index confirmed by EXPLAIN.
**Depends on:** Phase 10 (Startup Hardening — complete)
**Requirements**: PERF-01, PERF-02, PERF-03
**Success Criteria** (what must be TRUE):

  1. An integration test asserts that paper preflight issues at most 2 queries total regardless of the number of positions or approved candidates — the N+1 pattern does not reappear.
  2. A benchmark test confirms reconciliation runtime scales linearly (not quadratically) with input size; the test fails if O(n²) behavior is detected.
  3. `EXPLAIN` output for operator reads, reconciliation queries, and order lifecycle sync queries shows the named covering index is used — full sequential scans on large tables are absent.

**Plans**: 4 plans

Plans:
**Wave 1**

- [x] 11-01-PLAN.md — Eliminate paper-preflight N+1: query-count harness + batched intent resolution + query-count invariant test (PERF-01)
- [x] 11-02-PLAN.md — Verify/extend the O(n) reconciliation matcher benchmark to positions+orders+fills and the public entry point (PERF-02)
- [x] 11-03-PLAN.md — EXPLAIN-confirmed named covering indices for operator-read/reconciliation/order-sync paths + migration 0017 (PERF-03) — 4/5 critical paths verified as index scans; PERF-03 left Pending in REQUIREMENTS.md (broker-fill dedup query is a genuine, out-of-scope, non-index-fixable gap, see deferred-items.md)

**Wave 2** *(blocked on Wave 1 completion)*

- [x] 11-04-PLAN.md — Gap closure: batch-scoped broker-fill dedup lookup + regression and named-index EXPLAIN proof (PERF-03)

### Phase 12: Structural Refactor and Tooling

**Milestone**: v1.1 Execution Correctness & Hardening (resumed 2026-07-14 after Phase 11 completed)
**Goal:** Worker orchestration is split into bounded command modules, service logic is reorganized under declared boundaries, settings are consolidated, and lint/type-check gates block merge on failure — all with zero behavior change.
**Depends on:** Phase 11 (Tier 3 cannot land before Tier 0 is verified complete per STRUCT-01; all prior phases must be done)
**Requirements**: STRUCT-01, STRUCT-02, STRUCT-03, STRUCT-04, STRUCT-05, STRUCT-06, STRUCT-07, STRUCT-08, TOOL-01, TOOL-02
**Success Criteria** (what must be TRUE):

  1. `worker/__main__.py` contains only routing logic (under ~100 lines); domain commands live in `worker/commands/{bootstrap,ingest,backtest,risk_check,paper_execute,reconcile}.py` with no domain semantics in the entrypoint.
  2. Execution, reconciliation, and config logic each live under their declared service sub-paths; old scattered module definitions are deleted and all imports resolve through the new paths.
  3. The full existing test suite passes before and after the refactor with zero new or modified assertions — no behavior change is introduced.
  4. A pre-commit or CI gate blocks merge when ruff (or equivalent) lint/format check fails; mypy or pyright blocks merge on type errors in execution, reconciliation, and config modules.

**Plans**: 7 plans (sequential waves 1-7; each depends on the prior to avoid shared-working-tree collisions documented in STATE.md — may be collapsed if run single-threaded)

- [x] 12-01-PLAN.md — STRUCT-01 Tier-0 gate + baseline capture; STRUCT-07 tolerance consolidation
- [x] 12-02-PLAN.md — STRUCT-06 config -> services/config/{validation,secrets}; STRUCT-08 single settings surface
- [x] 12-03-PLAN.md — STRUCT-04 (part 1) execution package: transition + idempotency + contracts
- [x] 12-04-PLAN.md — STRUCT-04 (part 2) split paper_execution.py into execution/{submit_orders,sync_orders}
- [x] 12-05-PLAN.md — STRUCT-05 reconciliation -> services/reconciliation/{snapshot,matcher,findings,report}
- [x] 12-06-PLAN.md — STRUCT-03 worker split (routing-only __main__ <100 lines); STRUCT-02 zero-behavior-change proof
- [x] 12-07-PLAN.md — TOOL-01 ruff + TOOL-02 mypy via blocking pre-commit hook

## Progress

**Execution Order:**
Phases execute in numeric order: 1 -> 2 -> 3 -> 4 -> 5 -> 6 -> 7 -> 8 -> 9 -> 10 -> 11 -> 12

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Foundation Platform | 3/3 | Complete | 2026-03-12 |
| 2. Data and Strategy | 3/3 | Complete | 2026-03-14 |
| 3. Backtest and Reporting | 3/3 | Complete | 2026-03-14 |
| 4. Risk and Portfolio | 2/2 | Complete | 2026-03-14 |
| 5. Paper Execution | 3/3 | Complete | 2026-03-14 |
| 6. Analytics and APIs | 3/3 | Complete | 2026-03-15 |
| 7. Correctness Kernel | 3/3 | Complete    | 2026-04-20 |
| 8. Concurrency Guard | 5/5 | Complete | 2026-07-13 |
| 9. Reconciliation Rewrite | 4/4 | Complete | 2026-07-13 |
| 10. Startup Hardening | 6/6 | Complete | 2026-07-13 |
| 11. Query Performance | 4/4 | Complete | 2026-07-14 |
| 12. Structural Refactor and Tooling | 7/7 | Complete | 2026-07-15 |
