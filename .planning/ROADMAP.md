# Roadmap: Trading Strategy Platform

## Milestones

- ✅ **v1.0 MVP Backtest & Paper Trading** - Phases 1-6 (shipped 2026-03-15)
- ✅ **v1.1 Execution Correctness & Hardening** - Phases 7-12 (shipped 2026-07-15; full detail archived in `.planning/milestones/v1.1-paused/`)
- ✅ **v1.2 Operator Console v0** - Phases 13-16 (shipped 2026-07-09; full detail archived in `.planning/milestones/v1.2-operator-console/`)
- 🚧 **v1.3 Operator Platform** - Phases 17-21, including inserted Phase 20.1 (in progress — 17–20 complete; 20.1 and 21 planned)
- 📋 **v1.4 Operator Console** - Phases 22-27 (planned; follows v1.3) — the console rebuild on the Phase 21 read models: design first (Phase 22), then the rebuild (23–27). Carries AUD-02 and NOTIF-02.
- 🔭 **v1.5 (next after v1.4): Strategy Research / Strategy Lab** — work moves toward strategy research after the Operator Console. Scheduling (SCHED-01..03) belongs to a later Paper Automation milestone.

## Overview

v1.3 (re-scoped 2026-09-23 after a repository audit) finishes the investment in the Job framework and HTTP orchestration surface by making them usable from the Operator Console. Every existing long-running manual operation runs as a Job that the operator submits, observes, cancels, and retries from generic Job surfaces; immediate safety controls work from the console without depending on the worker; and every mutation bypass (`scripts/`, dead CLI paths, Makefile targets) is eliminated. No Redis/Celery, no push transport, no auth/RBAC, no scheduling.

**Architecture invariant (two mutation paths, nothing else):**

- **Long-running operations:** Console → HTTP → `JobOrchestrationService` → Job registry → worker (`run-jobs`) → existing domain service. Jobs orchestrate; domain services keep all domain semantics.
- **Synchronous operator controls** (kill-switch trip/reset, strategy enable/disable; from Phase 20.1 also active-paper-strategy seeding/handover, End execution operation, Record broker statement): Console or API → HTTP → `OperatorControlService`, synchronous, idempotent by target state, audited, and **never making a broker call**. A kill-switch operation never depends on a healthy worker. _(Amended 2026-09-30: path 2 was "immediate safety controls"; widened to synchronous operator controls with no broker call. Any action that reads or writes the broker is a Job.)_
- No script, CLI command, or Makefile target invokes a mutating domain service outside a Job handler or the control service, except named deployment tooling (`migrate`, `seed`).

**Cancellation limitation (accepted for v1.3):** cancellation is cooperative at handler/service-call boundaries. Queued Jobs cancel immediately; running Jobs stop at the next handler step boundary, or land `FAILED`/`cancellation_timeout` with `outcome_uncertain` if a single service call outlives the 300s grace period. No cancellation/progress abstraction is introduced into domain services; fine-grained mid-service cancellation can be added later if real workloads require it.

**Re-scope 2026-09-30 (Operator Console IA programme; `.planning/research/operator-console-ia/`):**
- Phase 20.1 is INSERTED to make trading-safety semantics correct before any read model is built.
- Phase 21 is re-planned as the **Operator Read-Model Foundation** (no UI).
- AUD-02 and NOTIF-02 move to the new v1.4 Operator Console milestone.
- Until v1.4, the new paper-trading controls are operated **through the HTTP API only**; the existing console receives only truthfulness adjustments. The runbook is `research/operator-console-ia/05-INTERIM-API-OPERATIONS.md`.

Phase order: Phase 17 built the generic Job framework; Phase 18 built the idempotent HTTP orchestration surface, followed by post-phase race/test hardening of the framework (PR #1). The production registry was intentionally left empty at the end of Phase 18. Phase 19 proves the whole chain end-to-end with one real operation (backtest) and builds the generic Job UI. Phase 20 migrates every remaining operation, restores safety controls, adds operator retry, and retires all bypasses. Phase 20.1 (inserted) fixes the operator-state semantics: paper-account ownership, attribution, submission uncertainty, recovery, execution operations, calendar/evaluation/execution facts, and data provenance. Phase 21 builds the read models (and the worker heartbeat) the new console needs, then v1.3 closes.

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

**Milestone Goal:** Every existing long-running operation executes as a Job that the operator submits, observes, cancels, and retries from generic Job surfaces in the console; immediate safety controls work from the console without depending on the worker; and exactly one mutation path exists per operation class — no bypasses.

- [x] **Phase 17: Job Framework** - Generic DB-backed job queue: closed lifecycle enum, restart-safe persistence, registry-based extensibility, import-boundary enforcement, dependencies, cancellation, progress and structured logs. (completed 2026-07-20)
- [x] **Phase 18: Orchestration Surface** - Idempotent Job submit/cancel HTTP endpoints, transport-agnostic Job observation, worker CLI reduced to thin adapters. (completed 2026-07-21; ORCH-01/02 Partial — `scripts/` bypass not covered, closes in Phase 20; post-phase race/test hardening completed 2026-07-22 in PR #1 / `2b88d49`)
- [x] **Phase 19: Job Operations Vertical Slice** - Backtest as the first real production Job, production worker wiring, generic Job list/detail/progress/logs/events/cancel UI, minimal submission UI, end-to-end Console → HTTP → Job → Worker → Service proof. (completed 2026-09-26)
- [x] **Phase 20: Complete Operation Migration & Safety Controls** - Remaining operations as Jobs, synchronous kill-switch/strategy controls, operator retry with lineage, retirement of every mutation bypass with boundary enforcement. (completed 2026-09-29)
- [ ] **Phase 20.1: Operator-State Correctness & Paper-Account Ownership** (INSERTED) - Single active paper strategy (starting with none), evidence-based attribution with owner-less account checks, audited external-activity recording, unambiguous order submission and uncertain-outcome recovery, a correct account baseline, honest batch/metadata outcomes, calendar/evaluation/execution facts, evaluation data provenance, a pausable execution operation, and truthful legacy-console behavior. New controls are API-only.
- [ ] **Phase 21: Operator Read-Model Foundation** - Read-only, bounded, closed-enum read models for the new console (overview, issues, sessions/operations, coverage, reconciliation detail, activity, catalog) plus the persisted worker heartbeat; then v1.3 closes.

**Deferred out of v1.3:** SCHED-01..03 (→ future Paper Automation milestone), AUD-03 (multi-user identity groundwork — single operator). NOTIF-01 folded into AUD-02. **Moved to v1.4 (2026-09-30):** AUD-02, NOTIF-02.

### 📋 v1.4 Operator Console (Planned)

**Milestone Goal:** An operator understands at a glance whether trading is safe and working, what needs attention, and what to do next. The console is rebuilt on the Phase 21 read models using the hybrid information architecture (posture + session pipeline + attention; `research/operator-console-ia/02-HYBRID-IA-PROPOSAL.md`). Desktop and mobile; mobile limited to monitoring and emergency controls until an auth/network model exists.

- [ ] **Phase 22: UX & Design Language** - Inspiration → design-system exploration → representative screens → iteration → finalized design language → remaining screens (Paper; no code).
- [ ] **Phase 23: Shell & Overview** - Header (environment, posture, attention badge = NOTIF-02, operations tray, Stop), navigation, Overview.
- [ ] **Phase 24: Trading** - Session pipeline, execution operation (Continue/End), permission & controls incl. seeding/handover, recovery screens.
- [ ] **Phase 25: Portfolio · Market data · Research**
- [ ] **Phase 26: Activity & System** - Activity (AUD-02), System › Technical; retire legacy routes against the "nothing lost" table.
- [ ] **Phase 27: Mobile** - Monitoring + emergency controls (network/auth decision required first).

Detailed requirements and plans for v1.4 are defined after Phase 21.

## Phase Details

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

**Plans**: 9 plans

Plans:

**Wave 1**

- [x] 17-01-PLAN.md — Job ORM models, closed status/failure/cancellation enums, migration 0018, migration enforcement tests (JOB-01, JOB-05, JOB-06, JOB-07)
- [x] 17-02-PLAN.md — JobContext/JobHandler contracts, JobRegistry, JOB-03 extensibility test, JOB-04 import-boundary test

**Wave 2** *(blocked on Wave 1 completion)*

- [x] 17-03-PLAN.md — Closed transition table and guarded apply_job_transition with per-transition audit (JOB-01, JOB-06)
- [x] 17-04-PLAN.md — DatabaseJobContext: progress snapshots, sanitized deterministic log writes, cancellation checkpoint (JOB-07, JOB-06)

**Wave 3** *(blocked on Wave 2 completion)*

- [x] 17-05-PLAN.md — Dependency validation, cycle rejection, readiness gating, transitive cancellation cascade (JOB-05)
- [x] 17-06-PLAN.md — Atomic queued cancel, cooperative running cancel, grace-period timeout sweep (JOB-06)

**Wave 4** *(blocked on Wave 3 completion)*

- [x] 17-07-PLAN.md — Claim/lease queue with SKIP LOCKED, lease-expiry crash reclaim, idempotency tests (JOB-02)

**Wave 5** *(blocked on Wave 4 completion)*

- [x] 17-08-PLAN.md — JobReadService and read-only /api/v1/jobs routes for state, progress, logs, events (JOB-07, JOB-06, JOB-05)
- [x] 17-09-PLAN.md — Job runner: handler execution, outcome landing, worker loop, run-jobs CLI command (JOB-02, JOB-03)

### Phase 18: Orchestration Surface

**Goal**: The HTTP API becomes the single orchestration surface for manual operations — every mutating endpoint is idempotent, returns a transport-agnostic Job reference, and CLI worker commands are proven to be thin wrappers over the identical service layer.
**Depends on**: Phase 17 (Job framework)
**Requirements**: ORCH-01, ORCH-02, ORCH-03, ORCH-04
**Success Criteria** (what must be TRUE):

  1. Every manual operation is invoked only through an HTTP API endpoint — no direct business-logic or CLI-only execution path exists for it (ORCH-01). *(Post-verification correction 2026-09-23: holds for the worker CLI and API adapters only. Mutating `scripts/*.py` and Makefile targets still call domain services directly — ORCH-01/02 are Partial until Phase 20 (ORCH-08). See 18-VERIFICATION.md "Post-Verification Correction".)*
  2. An import/structure enforcement test proves CLI commands and API routes call the identical service layer with zero duplicated business logic (ORCH-02).
  3. Resubmitting a mutating request with the same idempotency key returns the original Job instead of executing the operation twice (ORCH-03).
  4. Submitting an operation returns a Job reference whose state, progress, and logs are observable via API reads alone — no architectural dependency on polling vs. push (ORCH-04).
  5. A mutating cancellation endpoint exposes JOB-06's cancellation framework (built and tested in Phase 17) as an operator-invocable surface — the endpoint calls `jobs.cancellation.request_cancellation` for a QUEUED/RUNNING Job and returns the updated Job reference. *(Operator-surface owner for JOB-06; the framework mechanism was completed in Phase 17.)*

**Plans**: 6 plans

Plans:

**Wave 1**

- [x] 18-01-PLAN.md — Durable endpoint-scoped Job mutation idempotency schema and migration proof (ORCH-03)
- [x] 18-02-PLAN.md — Caller-session Job submission/cancellation primitives for atomic orchestration (ORCH-02, ORCH-03)

**Wave 2** *(blocked on Wave 1)*

- [x] 18-03-PLAN.md — Transport-independent orchestration service with race-safe replay, cancellation, and compact references (ORCH-03, ORCH-04)

**Wave 3** *(blocked on Wave 2)*

- [x] 18-04-PLAN.md — Thin idempotent Job submission/cancellation HTTP adapters and contract tests (ORCH-01, ORCH-03, ORCH-04)

**Wave 4** *(blocked on Wave 3)*

- [x] 18-05-PLAN.md — Remove direct mutating CLI paths and enforce adapter/application/domain boundaries (ORCH-01, ORCH-02)

**Wave 5** *(blocked on Wave 4)*

- [x] 18-06-PLAN.md — DB-ready API startup and test-only submit → execute → linked-observe E2E proof (ORCH-01–ORCH-04)

**Post-phase hardening — Job framework race & test hardening** (completed 2026-07-22; PR #1, commit `2b88d49`; recorded under Phase 18, no separate phase, plans, or verification report). Closed the three pre-existing Phase 17 concurrency/test-harness concerns that Phase 18 verification listed as anti-patterns; no new requirements (hardens JOB-05, JOB-06):

  1. Dependency-cascade race: each descendant is locked and re-read before transition.
  2. Cancellation-timeout sweep race: each candidate is locked and revalidated against one shared cutoff.
  3. Real-PostgreSQL race regressions (barrier/hold-lock pattern) in the job cancellation/dependency test suites, plus PG test-teardown hardening.

### Phase 19: Job Operations Vertical Slice

**Goal**: A backtest submitted from the console travels the full production path — `POST /api/v1/jobs` → `JobOrchestrationService` → registered `backtest` handler → production worker (`run-jobs`) → existing backtest service — and its progress, logs, events, result, failure state, and cancellation are observable in generic, job-type-agnostic Job UI.
**Depends on**: Phase 18 (orchestration surface, incl. post-phase race/test hardening); builds on the v1.2 console shell
**Requirements**: OPS-01, ORCH-05, ORCH-06, ORCH-07, JOBUI-01, JOBUI-02, JOBUI-03, JOBUI-04, JOBUI-05
**Success Criteria** (what must be TRUE):

  1. An E2E test using the production registry (not a test-only handler) submits a backtest via the API, `run-jobs` claims and executes it through the existing backtest service, the Job lands `SUCCEEDED`, progress, logs, and events resolve through the Job reference links, and the Job detail's `resources[]` contains the created `strategy_run` (persisted `strategy_runs.job_id` FK; `result_summary.run_id` equals it) (OPS-01).
  2. Resubmitting with the same `Idempotency-Key` returns the same `job_id` and exactly one backtest run exists (OPS-01, ORCH-03 regression).
  3. A test parsing `docker-compose.yml` asserts the worker service command is `run-jobs`; no deploy configuration starts the placeholder `serve` loop (ORCH-05).
  4. `GET /api/v1/job-types` lists exactly the registered Job types, each with a description and cancellation mode; an enforcement test asserts every registered type appears in the catalog (ORCH-06).
  5. With mutations disabled by configuration, every mutating route returns a typed 403 and writes zero rows; `render.yaml` sets mutations disabled (ORCH-07).
  6. Console enforcement: no `fetch(` exists outside `console/src/lib/api.ts`; Job list/detail/log/event components contain no job-type-specific branches; a component test renders a test-only Job type through list and detail with zero UI changes (JOBUI-01..03).
  7. Cancelling a queued backtest lands `CANCELLED` and it never executes; cancelling a running backtest lands `CANCELLED` at the next handler step boundary or `FAILED`/`cancellation_timeout`, and the UI labels whichever outcome occurred (JOBUI-04).
  8. Job list and detail refresh automatically while a Job is non-terminal and stop polling at a terminal state (JOBUI-05).
  9. The Phase 18 registry tripwires (`test_default_registry_remains_empty_until_phase_19`, the `_PHASE19_OPERATION_TYPES` denylist, `test_phase18_diff_excludes_console_and_phase19_handler_registrations`) are deliberately replaced by a test pinning the exact registered Job-type set.

**Out of scope**: every operation other than backtest; safety controls; retry; `scripts/` retirement; history view; JSON-Schema-driven form generation; SSE/WebSockets; auth; `submitted_by`/identity fields.
**Plans**: 12 plans

Plans:
**Wave 1**

- [x] 19-01-PLAN.md — Migration 0020: strategy_runs.job_id FK+UNIQUE, config_invalid enum; migration tests [BLOCKING upgrade]
- [x] 19-02-PLAN.md — ORCH-07 mutation flag (default disabled) + 403 guard, reason in 422 body, render.yaml/.env.example

**Wave 2** *(blocked on Wave 1 completion)*

- [x] 19-03-PLAN.md — job_id threading in run_backtest; resources[] on Job detail; job_id on run reads
- [x] 19-04-PLAN.md — Job-type catalog GET /api/v1/job-types + registry catalog contract (ORCH-06)
- [x] 19-05-PLAN.md — run-jobs worker: BACKTEST-level boot, per-type mode preflight (config_invalid), compose switch (ORCH-05)

**Wave 3** *(blocked on Wave 2 completion)*

- [x] 19-06-PLAN.md — backtest submission spec + handler, registration, SC9 tripwire replacement

**Wave 4** *(blocked on Wave 3 completion)*

- [x] 19-07-PLAN.md — Production-path E2E: submit, worker, idempotency, cancellation outcomes
- [x] 19-08-PLAN.md — Console foundation: mutating client, polling, capability hook, D-14 label, lookup map 2, SC6 fences

**Wave 5** *(blocked on Wave 4 completion)*

- [x] 19-09-PLAN.md — Jobs list screen with filters, polling, New Job entry, nav link
- [x] 19-10-PLAN.md — Job logs tail, events panel, cancel confirmation dialog
- [x] 19-11-PLAN.md — New Job flow + backtest form, /strategy shortcut, run-header back-link

**Wave 6** *(blocked on Wave 5 completion)*

- [x] 19-12-PLAN.md — Job detail screen composition, SC6 test-only type test

**UI hint**: yes

### Phase 20: Complete Operation Migration & Safety Controls

**Goal**: Every remaining existing long-running manual operation executes as a registered Job, immediate safety controls work from the console through synchronous HTTP endpoints that never depend on the worker, the operator can explicitly retry a failed or cancelled Job with lineage, and every mutation bypass is removed and prevented from returning.
**Depends on**: Phase 19 (vertical slice verified end-to-end)
**Requirements**: OPS-02, OPS-03, OPS-04, OPS-05, OPS-06, OPS-07, OPS-08, CTRL-01, CTRL-02, ORCH-01, ORCH-02, ORCH-08
**Success Criteria** (what must be TRUE):

  1. Risk evaluation, paper session, reconciliation, `ingest-bars`, `sync-symbol-metadata`, `sync-market-sessions`, and broker order-lifecycle sync are each a separately registered, independently validated Job type with an API → worker → service E2E test and an explicit console submission form (OPS-02..06).
  2. The paper-session Job is cancellable only before broker submission begins; the catalog states this and a test proves a cancellation request after submission starts does not interrupt broker submission (OPS-03).
  3. A domain concurrency conflict (paper-session advisory lock held) lands as a distinct closed `failure_reason` value, not `handler_error` (OPS-08).
  4. Retrying a `FAILED` or `CANCELLED` Job creates a new Job with the same job type and payload and `retry_of_job_id` set to the original; replaying the retry `Idempotency-Key` returns the same retry Job (ORCH-03 contract); retrying a non-terminal or `SUCCEEDED` Job is rejected; no automatic retry path exists (OPS-07).
  5. Kill-switch trip/reset and strategy enable/disable succeed with no worker process running; each call goes Console → HTTP → `OperatorControlService` (no Job), is idempotent by explicit target state without `Idempotency-Key` — requesting the current state (e.g. "trip" when already tripped) returns it and records the existing changed/unchanged audit semantics — and writes the existing `OPERATOR_CONTROL` run + `ExecutionEvent` audit rows; the console requires explicit confirmation and a reason (CTRL-01, CTRL-02).
  6. A boundary test fails if any file under `scripts/`, any worker command, or any Makefile target invokes a mutating domain service outside a Job handler or `OperatorControlService`; the exemption list (deployment tooling such as `migrate`, `seed`, read/report scripts, and the single trip-only break-glass `kill-switch-trip` worker subcommand — amended 2026-09-27) is pinned in the test. Mutating scripts, dead `worker/commands/*` functions, and dead/mutating Makefile targets are deleted (ORCH-01, ORCH-02, ORCH-08). `scripts/dry_run.py` and `scripts/generate_signals.py` are **unresolved classification items**: each is classified from its actual behavior — migrated or retired if it changes state or performs a manual operation, exempted (with a recorded reason) only if genuinely read-only or development-only; exemption is not the default.
  7. The "exactly two mutating routes" test is replaced by an explicit mutating-route allowlist covering Job submit/cancel/retry (idempotent by `Idempotency-Key`, ORCH-03) and the control endpoints (idempotent by target state, CTRL-01/02), all subject to the ORCH-07 mutation guard.

**Out of scope**: new Job types beyond existing operations (e.g. parameter sweeps, walk-forward, strategy comparison); a composite "sync everything" market-data handler; retry policies/backoff/counters/automatic retry; in-service cancellation/progress abstractions; scheduling; auth/identity fields.
**Plans**: 24 plans

Plans:

**Wave 1**

- [x] 20-01-PLAN.md — Migration 0021 + models: domain_conflict, non-unique strategy_runs.job_id, market_data_ingestion_runs.job_id, UNIQUE jobs.retry_of_job_id; submit_job lineage
- [x] 20-02-PLAN.md — D-31 read-path purity: pure backtest report builder + metric persist at completion, pure strategy-control-state read, zero-write proof
- [x] 20-03-PLAN.md — Shared strict payload validators; symbol-metadata sync extracted to services; calendar.sync_market_sessions

**Wave 2**

- [x] 20-04-PLAN.md — Framework: QUEUED_ONLY cancellation mode, JobDomainConflictError + runner domain_conflict branch, D-19 retry-prerequisite declaration
- [x] 20-05-PLAN.md — Read model (multi-run resources, market-data kind, payload + retry lineage) + job_id threading for risk/reconciliation/ingestion
- [x] 20-06-PLAN.md — Console contract layer: Job detail types, retry + control clients, error copy, shared job-form kit

**Wave 3**

- [x] 20-07-PLAN.md — risk-evaluation Job type + form
- [x] 20-08-PLAN.md — reconciliation Job type (report-only, queued-only) + form
- [x] 20-09-PLAN.md — paper-session Job type (queued-only, domain_conflict, two linked runs) + form

**Wave 4**

- [x] 20-10-PLAN.md — Orchestration: queued-only cancel rejection + idempotent retry with D-18/D-19
- [x] 20-11-PLAN.md — broker-order-sync Job type + form
- [x] 20-12-PLAN.md — Worker surface: delete dead commands and serve; add kill-switch-trip break-glass

**Wave 5**

- [x] 20-13-PLAN.md — HTTP: control routes, retry route, cancel 409, detail composition, five-route allowlist
- [x] 20-14-PLAN.md — ingest-bars Job type + form
- [x] 20-15-PLAN.md — sync-symbol-metadata + sync-market-sessions Job types + forms

**Wave 6**

- [x] 20-16-PLAN.md — Register 7 types + console form map; pin per-type registry contract
- [x] 20-17-PLAN.md — Job detail retry UI, lineage, queued-only cancel gating
- [x] 20-18-PLAN.md — Control UI kit: shared confirmation dialog, triggers, sync events, status badge

**Wave 7**

- [x] 20-19-PLAN.md — E2E: risk-evaluation, reconciliation, broker-order-sync, operator retry
- [x] 20-20-PLAN.md — E2E: paper-session cancellation honesty, domain_conflict, reconcile-first retry
- [x] 20-21-PLAN.md — E2E: three market-data Job types

**Wave 8**

- [x] 20-22-PLAN.md — /controls page + nav link
- [x] 20-23-PLAN.md — Inline controls on KillSwitchBanner and /strategy; Job shortcuts on /strategy and /paper
- [x] 20-24-PLAN.md — Delete bypass scripts/Makefile targets; closed-world boundary test
**UI hint**: yes

### Phase 20.1: Operator-State Correctness & Paper-Account Ownership (INSERTED)

**Goal**: Trading-safety semantics are correct and explicit before any read model or console is built. At most one active paper strategy owns the Alpaca account, starting with none; attribution is evidence-based with an owner-less account-level check; external activity is recorded through an audited path; order submissions are never re-sent ambiguously and uncertain outcomes are recovered by verified broker state; evaluation never corrupts account truth; batch and symbol-metadata outcomes are honest; trading day, evaluation session and execution window are separate facts; evaluations carry data provenance; and a multi-order session is an explicit, pausable execution operation. New controls are API-only until v1.4, and the existing console stays truthful.
**Depends on**: Phase 20
**Requirements**: PAPER-01, PAPER-02, ACCT-01, EXT-01, COR-01, COR-03, COR-04, COR-05, COR-06, PROV-01, REC-01, REC-02, COMPAT-01
**Success Criteria** (what must be TRUE):

  1. Two active paper strategies are unrepresentable; the initial state is no owner; `paper-session` and strategy-scoped reconciliation for a non-owner are rejected at submit and re-checked before every broker action; seeding and handover pass checks A1–A7 from persisted evidence and the new owner starts disabled (PAPER-01, PAPER-02).
  2. Broker orders and fills are classified `owned` / `recorded_external` / `unrecognized` from local registration evidence (never an ID format alone); unexplained exposure blocks; broker sync never creates positions; owner-less account-level sync and reconciliation run with no strategy and store results in dedicated storage (COR-05, ACCT-01).
  3. Recording external activity requires terminal orders and zero net external exposure, preserves external origin, and runs a fresh account-level reconciliation in the same Job; recording alone never lifts a block (EXT-01).
  4. An order POST is never re-sent while the original may still produce an execution; every attempt is logged before it can leave the process; a found order resolves uncertainty by its verified state; an intent is "proven not sent" only by positive evidence over its whole attempt history (a missing outcome is uncertainty); a never-found order stays unresolved until the broker shows it or its attempt history proves it not sent — a broker statement of non-receipt is audited evidence only (amended 2026-10-04; supersedes the earlier non-receipt release rule); every paper-session submission (fresh, retry or Continue) and every ownership change is gated while uncertainty is unresolved, and End, expiry, re-evaluation or a new order version never bypass it (COR-06, REC-01; supersedes Phase 20 D-19).
  5. A multi-order session pauses at the first order whose effects are not accounted; Continue re-checks sync, reconciliation, provenance, the fresh price and fresh risk and never resubmits or re-versions an order (a price deviation beyond tolerance pauses and sends nothing; an explicit Continue sends the same pinned intent once the price is back within tolerance and every freshness, permission, provenance, recovery, reconciliation, risk and TL-10 check passes again — changed inputs or settings still require re-evaluation, window expiry still terminates, and an unresolved earlier submission still blocks; PD-1 approved 2026-10-04); a new evaluation creates an order only when the action is justified by verified state and strategy rules — a changed fingerprint alone never does — and at most one broker-reaching action per strategy, evaluation session, symbol and side (TL-10); End or expiry terminates unsent intents only and never cancels broker orders, resolves uncertainty or changes trading permission (REC-02).
  6. Risk evaluation writes no account snapshot and sizes on broker-observed cash; batch and symbol-metadata operations report complete / partial / failed; symbols missing required metadata are never ready for trading (COR-01, COR-03).
  7. Trading day, evaluation session and execution window are separate calendar facts with explicit unknowns; historical execution is rejected; the calendar can sync ahead to a horizon; evaluations carry an input manifest that detects corrected data but not expected portfolio changes (COR-04, PROV-01).
  8. The existing console no longer starts paper sessions, shows Outcome next to Job status, defaults reconciliation and sync to account scope, and never states "does not block execution" while trading is blocked; API changes are additive and console contract tests stay green (COMPAT-01).
  9. The API end-to-end scenarios E1–E15 pass over HTTP against a scripted fake broker.

**Temporary limitations (accepted)**: TL-1 repeated Continue within a session; TL-2 a working order blocks further orders; TL-3 single regular-hours execution policy; TL-4 a never-found ambiguous order blocks the strategy and ownership changes until the broker shows it or it is proven not sent (no product-level release; a broker statement is evidence only; amended 2026-10-04); TL-5 external activity only when terminal and net-zero; TL-6 handover only when flat; TL-7 full broker-history re-read; TL-8 lazy window expiry; TL-9 API-only operation until v1.4; TL-10 one broker-reaching action per strategy, evaluation session, symbol and side (initial product limitation; a partially filled exit leaves no second sell that session); TL-11 partial-fill remainders are not pursued automatically (remaining position preserved, exposed and risk-checked; per-strategy follow-up behaviour in 20.1-15 S3-R4).
**Schema changes**: migrations 0022–0027 — active-paper-strategy singleton, order-submission attempt log, dedicated `account_reconciliation_runs` (architectural recommendation R-31), `external_broker_activity`, recovery records, execution-operation state + one-open-operation partial unique index. `RiskDecisionCode.symbol_not_ready` is code-only. Inventory: `research/operator-console-ia/03-PLANNING-CHANGES.md` §3.11.
**Out of scope**: new console screens or controls (v1.4); open-order-aware risk accounting; flatten / exits-only; adopting external positions; concurrent multi-strategy paper trading; scheduling; auth.
**Plans**: 25 plans (16 executed + 9 gap closure, 2026-10-05)

Plans (execution waves follow true dependency depth: the Alembic chain 0021→0027 is serialized, and plans that edit the same files never share a wave):

**Wave 1**

- [x] 20.1-01-PLAN.md — Single active paper strategy: singleton + migration 0022, submit/run-time gates, no-owner seed, new strategies disabled (PAPER-01)
- [x] 20.1-03-PLAN.md — Evaluation writes no account snapshot; broker-observed baseline; cash sizing (COR-01)

**Wave 2**

- [x] 20.1-02-PLAN.md — Order-submission attempt log (migration 0023), failure taxonomy, no ambiguous re-send, status mapping, client-order-id lookup (COR-06)
- [x] 20.1-04-PLAN.md — Batch outcomes complete/partial/failed incl. symbol metadata; symbol readiness + `symbol_not_ready` (COR-03)

**Wave 3**

- [x] 20.1-05-PLAN.md — Trading day / evaluation session / execution window; execution policy; calendar sync horizon; session defaults (COR-04)
- [x] 20.1-07-PLAN.md — Evidence-based attribution, unexplained exposure, no position adoption (COR-05)

**Wave 4**

- [x] 20.1-06-PLAN.md — Evaluation input manifest (data provenance) + accessor boundary test (PROV-01)
- [x] 20.1-08-PLAN.md — Owner-less account-level sync and reconciliation; dedicated `account_reconciliation_runs` (migration 0024) (ACCT-01)

**Wave 5**

- [x] 20.1-09-PLAN.md — Record external activity Job + `external_broker_activity` (migration 0025) (EXT-01)

**Wave 6**

- [x] 20.1-10-PLAN.md — Uncertain-outcome recovery, resolution predicate, submission gates, broker statement control, D-19 supersession (migration 0026) (REC-01)

**Wave 7**

- [x] 20.1-11-PLAN.md — Execution operation storage and state machine: migration 0027, closed states/reasons, one open operation per strategy, S1 fencing and takeover primitives, real OperationView, End operation and R2 reads (REC-02)

**Wave 8**

- [x] 20.1-12-PLAN.md — Seeding and handover controls with account checks A1–A7 (PAPER-02)
- [x] 20.1-15-PLAN.md — Execution operation submission: sequential loop with pause points, per-intent permission, fresh price and risk checks (`revalidate_pinned_intent`), S3 executed-key guard, start-mode submit-time gates (REC-02)

**Wave 9**

- [x] 20.1-16-PLAN.md — Execution operation Continue: paper-session `continue` mode, S1 takeover and concurrency acceptance, no resend of in-doubt intents, retry resolution (REC-02)

**Wave 10**

- [x] 20.1-14-PLAN.md — Legacy-console truthfulness adjustments (COMPAT-01)

**Wave 11**

- [x] 20.1-13-PLAN.md — Phase gate: API end-to-end scenarios E1–E15 (all 20.1 requirements)

**Gap closure (2026-10-05; VERIFICATION gap SC4/REC-01 + REVIEW SAF-01..SAF-12 + W-1/W-2 runbook; waves are relative to the gap set)**

**Gap wave 1**

- [x] 20.1-17-PLAN.md — Shared submission-evidence classifier used by every consumer (G2, takeover, predicate both branches, A5, submit/Continue gate, basis verification); closes SC4/REC-01 gap and SAF-01 (REC-01, REC-02, PAPER-02, COR-06)

**Gap wave 2**

- [x] 20.1-18-PLAN.md — Migration 0028: attempt log append-only/complete-once trigger + RESTRICT FK (SAF-10); session_run_lock cleanup (SAF-12) (COR-06, REC-02)
- [ ] 20.1-19-PLAN.md — Continue Jobs attributed to their operation's strategy (SAF-05); TL-4 terminal states tested and visible (SAF-04, legacy never-found order) (REC-01, REC-02, PAPER-02)
- [ ] 20.1-20-PLAN.md — T1 re-checks kill switch/owner/enabled/window per HTTP attempt, fetch-time price age (SAF-02); dead CAS helpers removed (SAF-11) (PAPER-01, REC-02, COR-06)

**Gap wave 3**

- [ ] 20.1-21-PLAN.md — One D-07 identity check for every broker binder (SAF-07); exit_quantity_mismatch disposition (SAF-08) (COR-05, REC-01, REC-02)
- [ ] 20.1-22-PLAN.md — Continue pinned-identity assertion (SAF-03); unparseable accepted reply is ambiguous (SAF-06) (REC-02, COR-06)

**Gap wave 4**

- [ ] 20.1-23-PLAN.md — Broker-observed, fresh cash basis enforced for execution; snapshot stamped after the account read; E2E harness sync step (SAF-09) (COR-01, REC-02)

**Gap wave 5**

- [ ] 20.1-24-PLAN.md — SAF-09 test rollout: `seed_fresh_broker_snapshot` arranged in the 14 session-running test modules, arrangement only (COR-01, REC-02)
- [ ] 20.1-25-PLAN.md — Runbook 05 + HUMAN-UAT amended: W-1 owner-scope sync, W-2 run-time refusal, TL-4 terminal states, SAF-02/03/09 operator consequences (docs only) (ACCT-01, EXT-01, REC-01, COR-01, COMPAT-01)

**UI hint**: yes (legacy-console compatibility only)

### Phase 21: Operator Read-Model Foundation

**Goal**: The backend exposes every operator-facing fact the v1.4 console needs as read-only, bounded, tested APIs whose meaning is carried in closed enums (trading permission, posture, verdict and next action, issues, session pipeline and execution operations, market-data coverage, reconciliation detail, activity, worker health, catalog), so the console rebuild does no domain interpretation. Worker health is backed by a persisted heartbeat. v1.3 closes after this phase.
**Depends on**: Phase 20.1
**Requirements**: AUD-01, OPR-01, OPR-02, OPR-03, OPR-04, OPR-05, OPR-06, OPR-07, OPR-08, WRK-01, WRK-02
**Success Criteria** (what must be TRUE):

  1. Walking the mutating-route allowlist, every Job and every control change (including seeding/handover, operation End and broker statements) yields exactly one Activity item with timestamp, operation, parameters, resulting record and outcome; control changes are never presented as runs (AUD-01, OPR-06).
  2. Trading permission uses the same gate functions as the paper session; the books gate is the latest persisted reconciliation of either scope, labelled with its time (OPR-01).
  3. Issues are a closed rule enum, each mapped to exactly one lane and severity; the 2026-09-29 fixture yields exactly `calendar_out_of_range`, `worker_unknown`, `no_active_paper_strategy` and `outcome_uncertain_unverified` (OPR-02).
  4. Sessions and operations expose trading day, evaluation session and execution window separately, never mark a historical session executable, and show paused operations with preserved unsent intents (OPR-03).
  5. Coverage and reconciliation detail cover both owner and account scope with classification, origin tags and unexplained exposure (OPR-04, OPR-05).
  6. Worker health reports exactly `idle`, `busy`, `unavailable` or `unknown`; no active Job is never evidence of health; worker health is not part of `/ready` (WRK-01, WRK-02).
  7. Every read returns server `as_of`, writes nothing, and meets the 02 §10 total-request query bound (overview ≤ 15, issues ≤ 12, sessions ≤ 10, coverage ≤ 5, reconciliation list ≤ 1 / detail ≤ 3, activity ≤ 4, reused components included); the overview's verdict and next action come from one decision table and it carries the evaluation-session pipeline and recent activity; the catalog declares each job type's operator mapping (OPR-01, OPR-07, OPR-08).
  8. A schema-delta test pins exactly one new table (`worker_heartbeats`, migration 0028); no files under `console/` change.

**Out of scope**: any console UI (AUD-02, NOTIF-02 → v1.4); issue persistence; auth; control-change storage separation; scheduling.
**Plans**: 8 plans

Plans:

**Wave 1**

- [ ] 21-01-PLAN.md — Worker heartbeat table (migration 0028), throttled writer, worker-health read (WRK-01, WRK-02)

**Wave 2** *(after 21-01: the Operations-engine lane reads `services/worker_health.py`)*

- [ ] 21-02-PLAN.md — Overview read + `as_of`/query-count harness (OPR-01, OPR-08)

**Wave 3**

- [ ] 21-03-PLAN.md — Issues read (OPR-02)
- [ ] 21-04-PLAN.md — Sessions and execution operations read (OPR-03)
- [ ] 21-05-PLAN.md — Coverage + reconciliation detail reads (OPR-04, OPR-05)
- [ ] 21-06-PLAN.md — Activity read (OPR-06, AUD-01)
- [ ] 21-07-PLAN.md — Catalog and job-list extensions (OPR-07)

**Wave 4** *(after 21-03, 21-04 and 21-06: the Overview's recent activity reuses the Activity read)*

- [ ] 21-08-PLAN.md — Overview completion: recent activity, complete contract, cross-read parity, complete request cost (OPR-01, OPR-08)

## Progress

**Execution Order:**
v1.3 executes 17 → 18 → 19 → 20 → 20.1 → 21, strictly sequential. Phase 20 starts only after the Phase 19 vertical slice is verified. v1.3 closes after Phase 21. v1.4 (22 → 23 → 24/25/26 → 27) follows, then v1.5 Strategy Lab.

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
| 11. Query Performance | v1.1 | 4/4 | Complete | 2026-07-14 |
| 12. Structural Refactor and Tooling | v1.1 | 7/7 | Complete | 2026-07-15 |
| 13. Console Foundation & System Status | v1.2 | 4/4 | Complete | 2026-07-08 |
| 14. Strategy & Runs Inspection | v1.2 | 5/5 | Complete | 2026-07-09 |
| 15. Paper Trading Status | v1.2 | 3/3 | Complete | 2026-07-09 |
| 16. Analytics & Charting | v1.2 | 3/3 | Complete | 2026-07-09 |
| 17. Job Framework | v1.3 | 9/9 | Complete | 2026-07-20 |
| 18. Orchestration Surface | v1.3 | 6/6 | Complete (ORCH-01/02 Partial → Phase 20) | 2026-07-21 |
| 19. Job Operations Vertical Slice | v1.3 | 12/12 | Complete | 2026-09-26 |
| 20. Complete Operation Migration & Safety Controls | v1.3 | 28/28 | Complete | 2026-09-29 |
| 20.1. Operator-State Correctness & Paper-Account Ownership (INSERTED) | v1.3 | 18/25 | In Progress (gap closure 17-25 executing) | - |
| 21. Operator Read-Model Foundation | v1.3 | 0/7 | Planned | - |
| 22. UX & Design Language | v1.4 | 0/TBD | Not started | - |
| 23. Shell & Overview | v1.4 | 0/TBD | Not started | - |
| 24. Trading | v1.4 | 0/TBD | Not started | - |
| 25. Portfolio · Market data · Research | v1.4 | 0/TBD | Not started | - |
| 26. Activity & System | v1.4 | 0/TBD | Not started | - |
| 27. Mobile | v1.4 | 0/TBD | Not started | - |

---
*Roadmap updated: 2026-09-30 — Operator Console IA programme: Phase 20.1 INSERTED (operator-state correctness & paper-account ownership, 14 plans); Phase 21 re-planned as Operator Read-Model Foundation (7 plans, worker heartbeat included); AUD-02/NOTIF-02 moved to the new v1.4 Operator Console milestone (Phases 22–27); Strategy Lab becomes v1.5; mutation path 2 widened to synchronous operator controls with no broker call. Sources: `research/operator-console-ia/03-PLANNING-CHANGES.md` rev. 8, `04-IMPLEMENTATION-PLANS.md`, `05-INTERIM-API-OPERATIONS.md`.*
*Previous update 2026-09-23 — v1.3 re-scoped after repository audit: post-phase race/test hardening (PR #1) recorded under Phase 18, Phases 19–21 re-cut (vertical slice → migration & safety controls → history & polish), SCHED-01..03 and AUD-03 deferred, ORCH-01/02 marked Partial pending Phase 20. Previous update 2026-07-15.*
*Roadmap updated: 2026-10-03 — Phase 20.1 plan 20.1-11 split by responsibility into 20.1-11 (storage, state machine, reads, End), 20.1-15 (submission execution) and 20.1-16 (Continue); 16 plans; 14 moved to wave 10 and 13 to wave 11.*
