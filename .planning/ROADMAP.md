# Roadmap: Trading Strategy Platform

## Milestones

- ✅ **v1.0 MVP Backtest & Paper Trading** - Phases 1-6 (shipped 2026-03-15)
- ✅ **v1.1 Execution Correctness & Hardening** - Phases 7-12 (shipped 2026-07-15; full detail archived in `.planning/milestones/v1.1-paused/`)
- ✅ **v1.2 Operator Console v0** - Phases 13-16 (shipped 2026-07-09; full detail archived in `.planning/milestones/v1.2-operator-console/`)
- 🚧 **v1.3 Operator Platform** - Phases 17-21 (in progress)

## Overview

v1.3 evolves the console from a read-only monitor into an operations control center. The Operator API becomes the single orchestration surface: every manual operation (backtest, risk evaluation, paper session, reconciliation, market-data sync, broker sync) executes through HTTP as a Job, backed by a generic, extensible, restart-safe DB-backed job framework — no Redis/Celery. Jobs orchestrate; the existing service layer keeps all domain logic. Scheduling becomes a Job producer over that same public API path, not a parallel execution mechanism. Every operator action, manual or scheduled, is idempotent and fully audited in a forward-compatible schema.

Phase order follows the architecture invariants directly: Phase 17 builds the generic Job framework in isolation (lifecycle, persistence, registry, dependencies, cancellation, progress/logs) — pure backend infrastructure with no operator-visible surface yet. Phase 18 builds the HTTP orchestration surface on top of it (idempotency, transport-agnostic observation, CLI-as-thin-wrapper enforcement) before any operation-specific UI exists. Phase 19 is the first phase with real console screens: every operation trigger, retry, strategy control, and the kill switch, built on the v1.2 console shell and wired through the Phase 18 surface. Phase 20 (Scheduling) depends on the specific Job types Phase 19 establishes (paper session, market-data sync) so the two initial schedules have something real to produce. Phase 21 (Audit & Operational Status) closes the milestone by retrofitting full audit persistence across every action built in Phases 19-20, plus the in-console status feed and global failure indicator.

## Phases

**Phase Numbering:**

- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)
- v1.3 continues numbering from 17 (v1.0 reserved 1-6, v1.1 reserved 7-12, v1.2 reserved 13-16)

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
<summary>✅ v1.1 Execution Correctness & Hardening (Phases 7-12) — SHIPPED 2026-07-15</summary>

- [x] **Phase 7: Correctness Kernel** - Closed order state machine, deterministic `client_order_id` idempotency, persistent global kill switch with operator CLI. Completed 2026-04-20.
- [x] **Phase 8: Concurrency Guard** - Advisory lock per (strategy_id, session_date), stale-run detection and reclaim. Completed 2026-07-13.
- [x] **Phase 9: Reconciliation Rewrite** - Typed snapshots, O(n) matcher, closed findings enum, materialized report, explicit corrective entrypoint. Completed 2026-07-13.
- [x] **Phase 10: Startup Hardening** - Fail-fast config validation, log sanitization, single canonical DB lifecycle. Completed 2026-07-13.
- [x] **Phase 11: Query Performance** - Preflight N+1 fix, linear reconciliation scaling, named covering indices with EXPLAIN proof. Completed 2026-07-14.
- [x] **Phase 12: Structural Refactor and Tooling** - Worker split into bounded command modules, service package reorganization, ruff + mypy blocking pre-commit gates. Completed 2026-07-15.

Full requirements, success criteria, and plan lists: `.planning/milestones/v1.1-paused/ROADMAP.md` and `.planning/milestones/v1.1-paused/REQUIREMENTS.md`.

</details>

<details>
<summary>✅ v1.2 Operator Console v0 (Phases 13-16) — SHIPPED 2026-07-09</summary>

- [x] **Phase 13: Console Foundation & System Status** - App shell, env-driven API client, shared error/as-of-timestamp pattern, health/system screen, kill-switch global banner. Completed 2026-07-08.
- [x] **Phase 14: Strategy & Runs Inspection** - Strategy overview, filterable runs table, and full run-detail audit trail (signals, risk decisions, orders/fills, metrics). Completed 2026-07-09.
- [x] **Phase 15: Paper Trading Status** - Positions, open orders, latest reconciliation result, latest account snapshot. Completed 2026-07-09.
- [x] **Phase 16: Analytics & Charting** - Equity curve chart and summary statistics for a selected backtest run. Completed 2026-07-09.

Full requirements, success criteria, and plan lists: `.planning/milestones/v1.2-operator-console/ROADMAP.md` and `.planning/milestones/v1.2-operator-console/REQUIREMENTS.md`.

</details>

### 🚧 v1.3 Operator Platform (In Progress)

**Milestone Goal:** Console evolves from read-only monitor to operations control center — every manual operation executes through HTTP as a Job, backed by a generic, extensible, restart-safe DB job framework with full lifecycle, progress, logs, scheduling, and audit.

- [ ] **Phase 17: Job Framework** - Generic DB-backed job queue: closed lifecycle enum, restart-safe persistence, registry-based extensibility, import-boundary enforcement, dependencies, cancellation, progress and structured logs.
- [ ] **Phase 18: Orchestration Surface** - HTTP API as the single orchestration surface: idempotent mutating endpoints, transport-agnostic Job observation, CLI-as-thin-wrapper enforcement.
- [ ] **Phase 19: Operation Triggers & Control** - Operator runs every operation (backtest, risk evaluation, paper session, reconciliation, market-data sync, broker sync), retries failed Jobs, and controls strategy enable/disable and the kill switch, all from the console.
- [ ] **Phase 20: Scheduling** - Scheduler as a Job producer over the same public API path; operator manages the Daily Paper Trading and Daily Market Data Sync schedules from the console.
- [ ] **Phase 21: Audit & Operational Status** - Full operator-action audit trail (forward-compatible for multi-user), inspectable and filterable in console, plus an in-console status feed and global failure indicator.

## Phase Details

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

### Phase 17: Job Framework

**Goal**: A generic, extensible, restart-safe DB-backed Job framework exists in PostgreSQL — every long-running operation can run as a Job with a closed lifecycle, explicit dependencies, cancellation, progress, and structured logs, with zero Redis/Celery infrastructure.
**Depends on**: Nothing (first phase of v1.3; builds on the existing PostgreSQL persistence layer)
**Requirements**: JOB-01, JOB-02, JOB-03, JOB-04, JOB-05, JOB-06, JOB-07
**Success Criteria** (what must be TRUE):

  1. A Job's state is always one of `QUEUED`, `RUNNING`, `SUCCEEDED`, `FAILED`, or `CANCELLED` — no other state is representable, proven by an enforcement test (JOB-01).
  2. A Job queued before a worker restart executes after it; a running Job whose worker crashes is detected and moved to a terminal state, never silently lost or duplicated (JOB-02).
  3. Registering a new Job type touches zero existing queue-framework modules, and an import-boundary test proves Job handlers invoke only domain services — never HTTP, scheduling, or UI modules (JOB-03, JOB-04).
  4. A Job with declared dependencies starts only after all dependencies succeed; a failed dependency moves dependents to a terminal non-executed state without running them (JOB-05).
  5. Operator can cancel a queued or running Job, transitioning it to `CANCELLED` with an audit record; every Job's progress and structured logs are queryable via the API during and after execution (JOB-06, JOB-07).

**Plans**: TBD

### Phase 18: Orchestration Surface

**Goal**: The HTTP API becomes the single orchestration surface for manual operations — every mutating endpoint is idempotent, returns a transport-agnostic Job reference, and CLI worker commands are proven to be thin wrappers over the identical service layer.
**Depends on**: Phase 17 (Job framework)
**Requirements**: ORCH-01, ORCH-02, ORCH-03, ORCH-04
**Success Criteria** (what must be TRUE):

  1. Every manual operation is invoked only through an HTTP API endpoint — no direct business-logic or CLI-only execution path exists for it (ORCH-01).
  2. An import/structure enforcement test proves CLI commands and API routes call the identical service layer with zero duplicated business logic (ORCH-02).
  3. Resubmitting a mutating request with the same idempotency key returns the original Job instead of executing the operation twice (ORCH-03).
  4. Submitting an operation returns a Job reference whose state, progress, and logs are observable via API reads alone — no architectural dependency on polling vs. push (ORCH-04).

**Plans**: TBD

### Phase 19: Operation Triggers & Control

**Goal**: Operator triggers every manual platform operation and controls strategy/kill-switch state directly from the console, each executing as an audited, idempotent Job through the orchestration surface built in Phase 18.
**Depends on**: Phase 18 (orchestration surface); builds on the v1.2 console shell
**Requirements**: OPS-01, OPS-02, OPS-03, OPS-04, OPS-05, OPS-06, OPS-07, CTRL-01, CTRL-02
**Success Criteria** (what must be TRUE):

  1. Operator can trigger a backtest, risk evaluation, paper trading session, reconciliation, market-data sync, or broker order-lifecycle sync from the console, each submitting a Job and showing its resulting state (OPS-01, OPS-02, OPS-03, OPS-04, OPS-05, OPS-06).
  2. Operator can retry a failed run/Job from its detail view; retry explicitly creates a new Job linked to the original (OPS-07).
  3. Operator can enable/disable the strategy from the console via the API, and the change is audited (CTRL-01).
  4. Operator can toggle the kill switch from the console behind an explicit confirmation step, and the change is audited (CTRL-02).

**Plans**: TBD
**UI hint**: yes

### Phase 20: Scheduling

**Goal**: Scheduled executions create Jobs through the identical public API path manual submissions use, and the operator manages the two initial daily schedules directly from the console.
**Depends on**: Phase 19 (the paper-session and market-data-sync Job types the schedules target)
**Requirements**: SCHED-01, SCHED-02, SCHED-03
**Success Criteria** (what must be TRUE):

  1. An enforcement test proves the scheduler has no separate execution path — every scheduled run creates a Job through the same public API manual submissions use (SCHED-01).
  2. Operator can view all schedules with their job type, cadence, last run, and next run (SCHED-02).
  3. Operator can enable, disable, and edit the Daily Paper Trading and Daily Market Data Sync schedules from the console (SCHED-03).

**Plans**: TBD
**UI hint**: yes

### Phase 21: Audit & Operational Status

**Goal**: Every operator action — manual or scheduled — is fully audited in a forward-compatible schema and inspectable in the console, and console-wide failure/status visibility exists without navigating to a detail page.
**Depends on**: Phase 20 (all operation, control, and scheduling actions this phase must audit already exist)
**Requirements**: AUD-01, AUD-02, AUD-03, NOTIF-01, NOTIF-02
**Success Criteria** (what must be TRUE):

  1. Every operator action persists initiating operator, timestamp, operation type, request parameters, resulting Job, and final outcome (AUD-01).
  2. Operator can view and filter the full audit history in the console (AUD-02).
  3. The audit schema carries an operator-identity field populated today with the local operator; adding multi-user support later requires no persistence redesign (AUD-03).
  4. Console shows an in-console operational status feed of job completions, failures, and kill-switch trips (NOTIF-01).
  5. A global failure indicator is visible from any console screen without navigating to a detail page (NOTIF-02).

**Plans**: TBD
**UI hint**: yes

## Progress

**Execution Order:**
Phases execute in numeric order. v1.3 executes 17 → 18 → 19 → 20 → 21 (strictly sequential — each phase builds on the orchestration surface or Job types the prior phase establishes).

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 1. Foundation Platform | v1.0 | 3/3 | Complete | 2026-03-12 |
| 2. Data and Strategy | v1.0 | 3/3 | Complete | 2026-03-14 |
| 3. Backtest and Reporting | v1.0 | 3/3 | Complete | 2026-03-14 |
| 4. Risk and Portfolio | v1.0 | 2/2 | Complete | 2026-03-14 |
| 5. Paper Execution | v1.0 | 3/3 | Complete | 2026-03-14 |
| 6. Analytics and APIs | v1.0 | 3/3 | Complete | 2026-03-15 |
| 7. Correctness Kernel | v1.1 | 3/3 | Complete | 2026-04-20 |
| 8. Concurrency Guard | v1.1 | 5/5 | Complete | 2026-07-13 |
| 9. Reconciliation Rewrite | v1.1 | 4/4 | Complete | 2026-07-13 |
| 10. Startup Hardening | v1.1 | 6/6 | Complete | 2026-07-13 |
| 11. Query Performance | v1.1 | 4/4 | Complete    | 2026-07-14 |
| 12. Structural Refactor and Tooling | v1.1 | 7/7 | Complete    | 2026-07-15 |
| 13. Console Foundation & System Status | v1.2 | 4/4 | Complete | 2026-07-08 |
| 14. Strategy & Runs Inspection | v1.2 | 5/5 | Complete | 2026-07-09 |
| 15. Paper Trading Status | v1.2 | 3/3 | Complete | 2026-07-09 |
| 16. Analytics & Charting | v1.2 | 3/3 | Complete | 2026-07-09 |
| 17. Job Framework | v1.3 | 0/TBD | Not started | - |
| 18. Orchestration Surface | v1.3 | 0/TBD | Not started | - |
| 19. Operation Triggers & Control | v1.3 | 0/TBD | Not started | - |
| 20. Scheduling | v1.3 | 0/TBD | Not started | - |
| 21. Audit & Operational Status | v1.3 | 0/TBD | Not started | - |

---
*Roadmap updated: 2026-07-15 — v1.3 Operator Platform phases 17-21 added; v1.0/v1.1/v1.2 collapsed to historical summary; full v1.1 detail archived in `.planning/milestones/v1.1-paused/`, full v1.2 detail archived in `.planning/milestones/v1.2-operator-console/`.*
