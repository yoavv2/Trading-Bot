# Roadmap: Trading Strategy Platform

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

- [x] **Phase 1: Foundation Platform** - Stand up the Python, FastAPI, PostgreSQL, and Docker skeleton with the core trading-platform interfaces and persistence model. Completed 2026-03-12.
- [ ] **Phase 2: Data and Strategy** - Build the daily-bar data pipeline and implement `TrendFollowingDailyV1` as the first isolated strategy module.
- [ ] **Phase 3: Backtest and Reporting** - Run deterministic daily-bar backtests and persist research outputs that are usable for strategy evaluation.
- [ ] **Phase 4: Risk and Portfolio** - Add the portfolio state and mandatory risk-validation pipeline that gates every signal before execution.
- [ ] **Phase 5: Paper Execution** - Turn approved daily signals into Alpaca paper orders with scheduling, lifecycle tracking, and reconciliation.
- [ ] **Phase 6: Analytics and APIs** - Make the system measurable and inspectable through analytics, operational controls, and operator-facing API reads.

## Phase Details

### Phase 1: Foundation Platform
**Goal:** Stand up the local-first platform skeleton, minimal persistence foundation, and extensibility contracts so the project starts as a strategy platform instead of a one-off script.
**Depends on:** Nothing (first phase)
**Requirements**: [REQ-01, REQ-02, REQ-11, REQ-12]
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
**Requirements**: [REQ-03, REQ-04]
**Research:** Medium — confirm Polygon daily-bar semantics, trading-calendar handling, and required data normalization boundaries.
**Success Criteria** (what must be TRUE):
  1. Historical daily bars for the initial universe can be ingested from Polygon and persisted reproducibly without manual cleanup.
  2. The platform stores normalized bar data, symbol metadata, and enough calendar context to run daily workflows consistently.
  3. `TrendFollowingDailyV1` computes its moving-average indicators and emits deterministic entry and exit signals for configured symbols.
  4. Strategy parameters such as universe, moving-average windows, and exits live in external config rather than inside strategy code.
**Plans:** 2/3 plans executed

Plans:
- [x] 02-01: Build the Polygon data provider, ingestion pipeline, and daily-bar normalization flow
- [ ] 02-02: Model symbol metadata, trading-calendar awareness, and reusable market-data access patterns
- [ ] 02-03: Implement `TrendFollowingDailyV1` with isolated config, indicators, and signal generation

### Phase 3: Backtest and Reporting
**Goal:** Validate the first strategy offline through deterministic backtests with persisted outputs and explicit assumptions.
**Depends on:** Phase 2
**Requirements**: [REQ-05]
**Research:** Medium — confirm fee and slippage conventions, deterministic run boundaries, and the simplest daily-bar simulation assumptions that remain credible.
**Success Criteria** (what must be TRUE):
  1. Running the same backtest twice with the same data and config produces the same trades, equity curve, and summary metrics.
  2. Backtest runs persist their configuration, trades, positions or equity snapshots, and performance summaries in the database.
  3. Fee and slippage assumptions are explicit, versioned with the run, and visible in the generated results.
  4. The operator can inspect a trustworthy report or export for a run without reading raw logs.
**Plans:** 2 plans

Plans:
- [ ] 03-01: Build the lightweight internal daily-bar backtest runner and run-persistence flow
- [ ] 03-02: Generate run reports, metrics summaries, and exports for research inspection

### Phase 4: Risk and Portfolio
**Goal:** Add deterministic sizing, portfolio state, and hard execution guardrails so no signal can bypass risk controls.
**Depends on:** Phase 3
**Requirements**: [REQ-07]
**Research:** Low — policy thresholds are already defined; research is limited to implementation edge cases around stale data and duplicate protection.
**Success Criteria** (what must be TRUE):
  1. Every strategy signal flows through a risk-validation pipeline before it can become an executable order intent.
  2. Position sizing, max position limits, allocation caps, stale-data checks, and duplicate-position prevention are deterministic and testable.
  3. Rejected signals are persisted with human-readable reasons that explain which risk rule blocked them.
  4. Portfolio state tracks the cash, exposure, and open-position context needed by the daily strategy and later paper execution.
**Plans:** 2 plans

Plans:
- [ ] 04-01: Build portfolio state, sizing logic, and exposure accounting
- [ ] 04-02: Implement the risk-validation pipeline, rejection logging, and execution gating rules

### Phase 5: Paper Execution
**Goal:** Convert approved daily signals into Alpaca paper orders on a schedule while keeping broker state and internal state aligned.
**Depends on:** Phase 4
**Requirements**: [REQ-08]
**Research:** High — Alpaca order-state behavior, scheduling constraints, reconciliation edge cases, and restart/idempotency failure modes need dedicated planning attention.
**Success Criteria** (what must be TRUE):
  1. The strategy can run on a fixed daily schedule and turn approved signals into Alpaca paper orders without manual intervention.
  2. Orders, fills, positions, and account snapshots are persisted and remain consistent across process restarts.
  3. Broker reconciliation detects mismatches or repeated order failures and blocks new execution when state is unsafe.
  4. The operator can inspect which orders were submitted, filled, blocked, or retried on the next trading day.
**Plans:** 3 plans

Plans:
- [ ] 05-01: Implement the Alpaca broker adapter and paper-order submission flow
- [ ] 05-02: Add scheduled daily execution, order lifecycle updates, and fill ingestion
- [ ] 05-03: Build reconciliation, restart safety, and execution-stop conditions for unsafe broker state

### Phase 6: Analytics and APIs
**Goal:** Make the platform measurable, inspectable, and operator-friendly enough to decide whether the MVP is trustworthy.
**Depends on:** Phase 5
**Requirements**: [REQ-09, REQ-10]
**Research:** Medium — metric conventions and operator-facing API reads benefit from research, but the phase is driven mostly by the platform behavior already built.
**Success Criteria** (what must be TRUE):
  1. Per-run and current-strategy metrics are queryable and trustworthy enough for the operator to compare runs and review paper performance.
  2. The operator can inspect recent signals, orders, fills, positions, account snapshots, and risk events through stable reads instead of raw logs alone.
  3. Structured logs, visible failures, kill-switch behavior, and audit trails make ambiguous state transitions observable and reviewable.
  4. The MVP verdict is satisfied: the operator can backtest the strategy, enable daily paper trading, and inspect exactly what happened with confidence.
**Plans:** 3 plans

Plans:
- [ ] 06-01: Build analytics summaries, per-run metrics, and historical inspection views
- [ ] 06-02: Expose operator-facing FastAPI read endpoints for runs, trades, positions, metrics, and risk events
- [ ] 06-03: Add operational controls, kill-switch flows, and observability outputs needed for confident daily use

## Progress

**Execution Order:**
Phases execute in numeric order: 1 -> 2 -> 3 -> 4 -> 5 -> 6

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Foundation Platform | 3/3 | Complete | 2026-03-12 |
| 2. Data and Strategy | 2/3 | In Progress|  |
| 3. Backtest and Reporting | 0/2 | Not started | - |
| 4. Risk and Portfolio | 0/2 | Not started | - |
| 5. Paper Execution | 0/3 | Not started | - |
| 6. Analytics and APIs | 0/3 | Not started | - |
