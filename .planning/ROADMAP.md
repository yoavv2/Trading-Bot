# Roadmap: Trading Strategy Platform

## Milestones

- ✅ **v1.0 MVP Backtest & Paper Trading** - Phases 1-6 (shipped 2026-03-15)
- ✅ **v1.1 Execution Correctness & Hardening** - Phases 7-12 (shipped 2026-07-15; full detail archived in `.planning/milestones/v1.1-paused/`)
- ✅ **v1.2 Operator Console v0** - Phases 13-16 (shipped 2026-07-09; full detail archived in `.planning/milestones/v1.2-operator-console/`)
- 🚧 **v1.3 Operator Platform** - Phases 17-21 (in progress — 17, 18 complete)
- 🔭 **Next (not yet defined): Strategy Research / Strategy Lab** — after v1.3 closes, work moves away from operator infrastructure toward strategy research. Scheduling (SCHED-01..03) belongs to a later Paper Automation milestone.

## Overview

v1.3 (re-scoped 2026-09-23 after a repository audit) finishes the investment in the Job framework and HTTP orchestration surface by making them usable from the Operator Console. Every existing long-running manual operation runs as a Job that the operator submits, observes, cancels, and retries from generic Job surfaces; immediate safety controls work from the console without depending on the worker; and every mutation bypass (`scripts/`, dead CLI paths, Makefile targets) is eliminated. No Redis/Celery, no push transport, no auth/RBAC, no scheduling.

**Architecture invariant (two mutation paths, nothing else):**

- **Long-running operations:** Console → HTTP → `JobOrchestrationService` → Job registry → worker (`run-jobs`) → existing domain service. Jobs orchestrate; domain services keep all domain semantics.
- **Immediate safety controls** (kill-switch trip/reset, strategy enable/disable): Console → HTTP → `OperatorControlService`, synchronous, idempotent by target state, audited. A kill-switch operation never depends on a healthy worker.
- No script, CLI command, or Makefile target invokes a mutating domain service outside a Job handler or the control service, except named deployment tooling (`migrate`, `seed`).

**Cancellation limitation (accepted for v1.3):** cancellation is cooperative at handler/service-call boundaries. Queued Jobs cancel immediately; running Jobs stop at the next handler step boundary, or land `FAILED`/`cancellation_timeout` with `outcome_uncertain` if a single service call outlives the 300s grace period. No cancellation/progress abstraction is introduced into domain services; fine-grained mid-service cancellation can be added later if real workloads require it.

Phase order: Phase 17 built the generic Job framework; Phase 18 built the idempotent HTTP orchestration surface, followed by post-phase race/test hardening of the framework (PR #1). The production registry was intentionally left empty at the end of Phase 18. Phase 19 proves the whole chain end-to-end with one real operation (backtest) and builds the generic Job UI. Phase 20 migrates every remaining operation, restores safety controls, adds operator retry, and retires all bypasses. Phase 21 builds operational history and failure visibility from audit data that already exists, then v1.3 closes.

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
- [ ] **Phase 19: Job Operations Vertical Slice** - Backtest as the first real production Job, production worker wiring, generic Job list/detail/progress/logs/events/cancel UI, minimal submission UI, end-to-end Console → HTTP → Job → Worker → Service proof.
- [ ] **Phase 20: Complete Operation Migration & Safety Controls** - Remaining operations as Jobs, synchronous kill-switch/strategy controls, operator retry with lineage, retirement of every mutation bypass with boundary enforcement.
- [ ] **Phase 21: Operations History & Polish** - Unified operational history and global failure visibility built from existing Job/event/control audit data; operational UX cleanup; then v1.3 closes.

**Deferred out of v1.3:** SCHED-01..03 (→ future Paper Automation milestone), AUD-03 (multi-user identity groundwork — single operator). NOTIF-01 folded into AUD-02.

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

  1. An E2E test using the production registry (not a test-only handler) submits a backtest via the API, `run-jobs` claims and executes it through the existing backtest service, the Job lands `SUCCEEDED`, and progress, logs, events, and `result_summary.run_id` resolve through the Job reference links (OPS-01).
  2. Resubmitting with the same `Idempotency-Key` returns the same `job_id` and exactly one backtest run exists (OPS-01, ORCH-03 regression).
  3. A test parsing `docker-compose.yml` asserts the worker service command is `run-jobs`; no deploy configuration starts the placeholder `serve` loop (ORCH-05).
  4. `GET /api/v1/job-types` lists exactly the registered Job types, each with a description and cancellation mode; an enforcement test asserts every registered type appears in the catalog (ORCH-06).
  5. With mutations disabled by configuration, every mutating route returns a typed 403 and writes zero rows; `render.yaml` sets mutations disabled (ORCH-07).
  6. Console enforcement: no `fetch(` exists outside `console/src/lib/api.ts`; Job list/detail/log/event components contain no job-type-specific branches; a component test renders a test-only Job type through list and detail with zero UI changes (JOBUI-01..03).
  7. Cancelling a queued backtest lands `CANCELLED` and it never executes; cancelling a running backtest lands `CANCELLED` at the next handler step boundary or `FAILED`/`cancellation_timeout`, and the UI labels whichever outcome occurred (JOBUI-04).
  8. Job list and detail refresh automatically while a Job is non-terminal and stop polling at a terminal state (JOBUI-05).
  9. The Phase 18 registry tripwires (`test_default_registry_remains_empty_until_phase_19`, the `_PHASE19_OPERATION_TYPES` denylist, `test_phase18_diff_excludes_console_and_phase19_handler_registrations`) are deliberately replaced by a test pinning the exact registered Job-type set.

**Out of scope**: every operation other than backtest; safety controls; retry; `scripts/` retirement; history view; JSON-Schema-driven form generation; SSE/WebSockets; auth; `submitted_by`/identity fields.
**Plans**: TBD
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
  6. A boundary test fails if any file under `scripts/`, any worker command, or any Makefile target invokes a mutating domain service outside a Job handler or `OperatorControlService`; the exemption list (deployment tooling such as `migrate`, `seed`, and read/report scripts) is pinned in the test. Mutating scripts, dead `worker/commands/*` functions, and dead/mutating Makefile targets are deleted (ORCH-01, ORCH-02, ORCH-08). `scripts/dry_run.py` and `scripts/generate_signals.py` are **unresolved classification items**: each is classified from its actual behavior — migrated or retired if it changes state or performs a manual operation, exempted (with a recorded reason) only if genuinely read-only or development-only; exemption is not the default.
  7. The "exactly two mutating routes" test is replaced by an explicit mutating-route allowlist covering Job submit/cancel/retry (idempotent by `Idempotency-Key`, ORCH-03) and the control endpoints (idempotent by target state, CTRL-01/02), all subject to the ORCH-07 mutation guard.

**Out of scope**: new Job types beyond existing operations (e.g. parameter sweeps, walk-forward, strategy comparison); a composite "sync everything" market-data handler; retry policies/backoff/counters/automatic retry; in-service cancellation/progress abstractions; scheduling; auth/identity fields.
**Plans**: TBD
**UI hint**: yes

### Phase 21: Operations History & Polish

**Goal**: The operator inspects one filterable operational history of Jobs and safety-control changes and sees failures from any console screen — built entirely from audit data that already exists — and the operational UX is consistent. v1.3 closes after this phase.
**Depends on**: Phase 20 (every operation and control this phase surfaces already exists)
**Requirements**: AUD-01, AUD-02, NOTIF-02
**Success Criteria** (what must be TRUE):

  1. Every Job and every safety-control change is inspectable with timestamp, operation type, parameters, resulting Job or control record, and outcome, sourced from existing `Job`/`JobEvent`/`JobMutation` rows and `OPERATOR_CONTROL` runs + `ExecutionEvent` rows (AUD-01).
  2. A read-only history endpoint and console `/history` page filter by kind (Job/control), type, outcome, and date range, including failures and kill-switch trips (AUD-02, absorbs NOTIF-01).
  3. A test asserts this phase adds no new tables or columns; a test walking the mutating-route allowlist asserts every mutating route produces a history entry (AUD-01).
  4. A global failure indicator renders on every console route and links to the filtered history (NOTIF-02).
  5. Shared status badge replaces duplicated `statusColor` helpers; Jobs, History, and Controls are reachable from the nav.

**Out of scope**: identity/actor schema (AUD-03), external notifications, retention/pruning, new audit tables, scheduling.
**Plans**: TBD
**UI hint**: yes

## Progress

**Execution Order:**
v1.3 executes 17 → 18 → 19 → 20 → 21, strictly sequential. Phase 20 starts only after the Phase 19 vertical slice is verified. v1.3 closes after Phase 21.

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
| 19. Job Operations Vertical Slice | v1.3 | 0/TBD | Not started | - |
| 20. Complete Operation Migration & Safety Controls | v1.3 | 0/TBD | Not started | - |
| 21. Operations History & Polish | v1.3 | 0/TBD | Not started | - |

---
*Roadmap updated: 2026-09-23 — v1.3 re-scoped after repository audit: post-phase race/test hardening (PR #1) recorded under Phase 18, Phases 19–21 re-cut (vertical slice → migration & safety controls → history & polish), SCHED-01..03 and AUD-03 deferred, ORCH-01/02 marked Partial pending Phase 20. Previous update 2026-07-15.*
