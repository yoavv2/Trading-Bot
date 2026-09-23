# Requirements: Trading Strategy Platform — Milestone v1.3 Operator Platform

**Defined:** 2026-07-15
**Re-scoped:** 2026-09-23 (repository audit — vertical-slice re-cut; scheduling and identity groundwork deferred)
**Core Value:** Build a trustworthy, auditable trading platform that can reproducibly validate a strategy, run it in daily paper trading, and explain every action or blocked action without ambiguity.

**Milestone scope rule:** Exactly two mutation paths exist. (1) Long-running operations: Console → HTTP → Job orchestration → worker → existing domain service; Jobs orchestrate, services implement. (2) Immediate safety controls (kill switch, strategy enable/disable): Console → HTTP → `OperatorControlService`, synchronous and independent of the worker. Every mutation is idempotent and audited — Job mutations by `Idempotency-Key` (ORCH-03), safety controls by explicit target state (CTRL-01/02). No script, CLI, or Makefile bypass. No live trading, no auth surface, no external notification channels, no scheduling, no new queue/transport infrastructure beyond PostgreSQL and polling.

**Accepted limitation:** cancellation is cooperative at handler/service-call boundaries; no cancellation/progress abstraction is introduced into domain services in v1.3.

## v1.3 Requirements

Requirements for this milestone. Each maps to roadmap phases.

### Job Framework

- [x] **JOB-01**: Every long-running operation executes as a Job with the closed lifecycle enum `QUEUED → RUNNING → SUCCEEDED / FAILED / CANCELLED`; no state outside the enum is representable
- [x] **JOB-02**: Jobs persist in PostgreSQL and survive restart — a queued job submitted before a worker restart executes after it; a running job interrupted by crash is detected and moved to a terminal state, never silently lost or duplicated
- [x] **JOB-03**: New Job types are registered through a job-type registry without modifying queue infrastructure — adding a type touches zero queue-framework modules (enforcement test)
- [x] **JOB-04**: Job handlers invoke domain services only; an import-boundary test asserts no domain service imports job, HTTP, scheduling, or UI modules
- [x] **JOB-05**: A Job can declare explicit dependencies on other Jobs; a dependent Job starts only after all dependencies succeed, and a failed dependency moves dependents to a terminal non-executed state
- [x] **JOB-06**: Operator can cancel a queued or running Job; cancellation transitions it to `CANCELLED` and is audited *(framework mechanism Phase 17; operator-invocable surface `POST /api/v1/jobs/{job_id}/cancel` delivered and verified in Phase 18 — the Phase 17 verification override is resolved. Console cancel control: JOBUI-04, Phase 19)*
- [x] **JOB-07**: Every Job records progress and structured logs observable via the API during and after execution

### Orchestration Surface

- [ ] **ORCH-01**: Every manual long-running operation is exposed only as an HTTP Job submission and every immediate safety control only as an HTTP control endpoint; the console invokes only the HTTP API — never business logic, scripts, or CLI code directly *(Partial: holds for the worker CLI and API adapters as of Phase 18; mutating `scripts/*.py` and Makefile targets still call domain services directly — closes in Phase 20 via ORCH-08)*
- [ ] **ORCH-02**: CLI worker commands and scripts are thin wrappers over the same service layer the API uses — no business logic exists in CLI, script, or API route code (import/structure enforcement) *(Partial: enforcement covers `worker/` and `api/` only; extended to `scripts/` and the Makefile in Phase 20)*
- [x] **ORCH-03**: Every Job mutation handled through `JobOrchestrationService` (submit, cancel, and — from Phase 20 — retry) is idempotent by `Idempotency-Key` — resubmitting the same operation with the same key returns the existing Job reference (Phase 18 replay contract) instead of executing twice. *(Scope: Job mutations only. Immediate safety controls are idempotent by explicit target state under CTRL-01/CTRL-02 and do not use `Idempotency-Key`.)*
- [x] **ORCH-04**: Submitting an operation returns a Job reference whose state, progress, and logs the console observes via API reads — transport-agnostic, no architectural dependency on polling vs push
- [ ] **ORCH-05**: The production worker process runs the Job runner — the compose worker service command is `run-jobs`, and no deploy configuration starts the placeholder `serve` loop (config-parsing test)
- [ ] **ORCH-06**: A read-only job-type catalog endpoint lists every registered Job type with a description and its cancellation mode; an enforcement test asserts every registered type appears in it. No JSON-Schema-to-form generation is required
- [ ] **ORCH-07**: A configuration flag disables all mutating routes; when disabled they return a typed 403 and write zero rows, and the public `render.yaml` deploy sets it disabled. No authentication is introduced
- [ ] **ORCH-08**: Exactly one mutation path per operation class — every mutating `scripts/*.py`, dead `worker/commands/*` function, and mutating/dead Makefile target is removed; a boundary test fails if any script, worker command, or Makefile target invokes a mutating domain service outside a Job handler or `OperatorControlService`, with a pinned exemption list limited to deployment tooling (`migrate`, `seed`) and read/report paths. **Unresolved classification items (Phase 20 must decide from actual behavior, default is NOT exemption):** `scripts/dry_run.py` and `scripts/generate_signals.py` — inspect whether each performs a state-changing/manual operation; if mutating, migrate to a Job or retire; only if genuinely read-only or development-only, add to the pinned exemption list with the reason

### Job Operations UI

- [ ] **JOBUI-01**: Operator can list Jobs in the console, filtered by status and job type, through a job-type-agnostic list view
- [ ] **JOBUI-02**: Operator can view a generic Job detail: status, progress, failure reason/message and `outcome_uncertain`, dependencies/blocking Job, cancellation fields, and `result_summary` rendered generically (a `run_id` key links to the existing run-detail page)
- [ ] **JOBUI-03**: Operator can view a Job's structured logs (cursor-based tail) and lifecycle events
- [ ] **JOBUI-04**: Operator can cancel a non-terminal Job from the console behind a confirmation; the UI labels the actual outcome (`CANCELLED`, or `FAILED`/`cancellation_timeout`) honestly
- [ ] **JOBUI-05**: Job list and detail refresh automatically while a Job is non-terminal and stop polling at a terminal state

### Operation Triggers

- [ ] **OPS-01**: Operator can run a backtest from the UI — `backtest` is the first registered production Job type, proven end-to-end Console → HTTP → Job → worker → existing backtest service
- [ ] **OPS-02**: Operator can run a risk evaluation from the UI as a registered Job
- [ ] **OPS-03**: Operator can run a paper trading session from the UI as a registered Job; it is cancellable only before broker submission begins, and the catalog states this
- [ ] **OPS-04**: Operator can run reconciliation from the UI as a registered Job
- [ ] **OPS-05**: Operator can run market-data operations from the UI as three separate, independently validated Job types — `ingest-bars`, `sync-symbol-metadata`, `sync-market-sessions`; no composite handler that switches behavior on flags
- [ ] **OPS-06**: Operator can run broker order-lifecycle sync from the UI as a registered Job
- [ ] **OPS-07**: Operator can retry a `FAILED` or `CANCELLED` Job from its detail view; retry creates a new Job with the same job type and payload, linked via one nullable `retry_of_job_id`; retry submission is idempotent by `Idempotency-Key` under the ORCH-03 contract; no automatic retries, retry policies, backoff, counters, or retry scheduler
- [ ] **OPS-08**: Typed domain conflicts (e.g. paper-session advisory lock already held) land as a distinct closed `failure_reason` value, not `handler_error`

### Operational Control

- [ ] **CTRL-01**: Operator can enable/disable the strategy from the UI through a synchronous HTTP control endpoint (Console → HTTP → `OperatorControlService`) that is idempotent by explicit target state — requesting the current state returns it unchanged and records the existing changed/unchanged audit semantics (`OPERATOR_CONTROL` run + `ExecutionEvent`); no `Idempotency-Key`, no Job, no worker dependency
- [ ] **CTRL-02**: Operator can trip/reset the kill switch from the UI behind an explicit confirmation through a synchronous HTTP control endpoint (Console → HTTP → `OperatorControlService`) that is idempotent by explicit target state — e.g. "trip" when already tripped returns the current state and records the existing changed/unchanged audit semantics; no `Idempotency-Key`, no Job; it succeeds with no worker process running

### Audit & Operational Status

- [ ] **AUD-01**: Every Job and every safety-control change is inspectable with timestamp, operation type, request parameters, resulting Job or control record, and final outcome — sourced from existing `Job`/`JobEvent`/`JobMutation` and control audit records, with no new audit tables
- [ ] **AUD-02**: Operator can view and filter the operational history (kind, type, outcome, date range) in the console, including job completions, failures, and kill-switch trips *(absorbs NOTIF-01)*
- [ ] **NOTIF-02**: Failures are visible from any screen via a global indicator without navigating to a detail page

## Deferred from v1.3

Re-scoped out on 2026-09-23. Not built in v1.3.

| Requirement | Disposition | Reason |
|-------------|-------------|--------|
| SCHED-01: The scheduler creates Jobs through the same public API path as manual submissions | Deferred → future Paper Automation milestone | Manual execution from the console suffices at the current stage; scheduling belongs with unattended strategy operation |
| SCHED-02: Operator can view all schedules with job type, cadence, last run, next run | Deferred → future Paper Automation milestone | Same |
| SCHED-03: Operator can enable/disable/edit the Daily Paper Trading and Daily Market Data Sync schedules | Deferred → future Paper Automation milestone | Same |
| AUD-03: Audit schema carries an operator-identity field for future multi-user | Deferred (no target milestone) | Single-operator platform; identity fields are added only when a concrete requirement needs them |
| NOTIF-01: In-console operational status feed | Merged into AUD-02 | The filterable history view (failures, completions, kill-switch trips) is the feed |

## Future Requirements

Deferred to later milestones (ATOS Stages 2–7). Tracked but not in current roadmap. **Next milestone direction after v1.3: Strategy Research / Strategy Lab.**

### Strategy Laboratory (Stage 2)

- **EXP-01**: Experiment domain (Strategy → Experiment → Run → Metrics → Comparison) with full reproducibility snapshots (parameters, dataset, date range, commit hash, config, environment)

### Paper Automation (later)

- **SCHED-01..03**: see "Deferred from v1.3"

### Portfolio Management (Stage 3)

- **PORT-01**: Multi-strategy capital allocation, risk budgets, correlation matrix as first-class metric

### Research Platform (Stage 4)

- **RSCH-01**: Persistent research objects — ideas, hypotheses, notes, experiments, conclusions

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
|---------|--------|
| Live trading or live-trading controls | Paper correctness must be proven first; live remains gated behind the promotion pipeline (Stage 6) |
| Multi-user auth/RBAC and identity fields | Single operator in v1.x; ORCH-07's mutation flag is the only deploy-exposure control |
| External notification channels (email/Telegram/Slack) | In-console history and failure indicator suffice |
| Redis/Celery/Kafka, distributed workers, or any new queue infrastructure | DB-backed queue in PostgreSQL — no new infra per complexity budget |
| SSE/WebSockets or any push transport | Observation is transport-agnostic; v1.3 uses polling |
| Scheduling of any kind (scheduler loop, schedules table, cron UI, missed-run semantics) | Deferred to a Paper Automation milestone |
| New Job types beyond existing operations (parameter sweeps, walk-forward, strategy comparison) | Strategy Lab scope; the generic Job UI must carry them without changes |
| Generic JSON-Schema-to-form framework | The generic abstraction is Job lifecycle/observation; submission forms are explicit per operation |
| In-service cancellation/progress abstraction | Accepted v1.3 limitation; add only if real workloads require it |
| Experiment domain | Stage 2 milestone |

## Traceability

Which phases cover which requirements. Updated 2026-09-23 re-scope.

| Requirement | Phase | Status |
|-------------|-------|--------|
| JOB-01 | Phase 17 | Complete |
| JOB-02 | Phase 17 | Complete |
| JOB-03 | Phase 17 | Complete |
| JOB-04 | Phase 17 | Complete |
| JOB-05 | Phase 17 (+ Phase 18 post-phase race hardening) | Complete |
| JOB-06 | Phase 17 (framework) + Phase 18 (API surface + post-phase race hardening) | Complete |
| JOB-07 | Phase 17 | Complete |
| ORCH-01 | Phase 18 → closes Phase 20 | Partial |
| ORCH-02 | Phase 18 → closes Phase 20 | Partial |
| ORCH-03 | Phase 18 (Job mutations; retry extends it in Phase 20) | Complete |
| ORCH-04 | Phase 18 | Complete |
| ORCH-05 | Phase 19 | Pending |
| ORCH-06 | Phase 19 | Pending |
| ORCH-07 | Phase 19 | Pending |
| JOBUI-01 | Phase 19 | Pending |
| JOBUI-02 | Phase 19 | Pending |
| JOBUI-03 | Phase 19 | Pending |
| JOBUI-04 | Phase 19 | Pending |
| JOBUI-05 | Phase 19 | Pending |
| OPS-01 | Phase 19 | Pending |
| OPS-02 | Phase 20 | Pending |
| OPS-03 | Phase 20 | Pending |
| OPS-04 | Phase 20 | Pending |
| OPS-05 | Phase 20 | Pending |
| OPS-06 | Phase 20 | Pending |
| OPS-07 | Phase 20 | Pending |
| OPS-08 | Phase 20 | Pending |
| CTRL-01 | Phase 20 | Pending |
| CTRL-02 | Phase 20 | Pending |
| ORCH-08 | Phase 20 | Pending |
| AUD-01 | Phase 21 | Pending |
| AUD-02 | Phase 21 | Pending |
| NOTIF-02 | Phase 21 | Pending |
| SCHED-01..03 | — | Deferred (Paper Automation) |
| AUD-03 | — | Deferred |
| NOTIF-01 | Phase 21 (via AUD-02) | Merged |

**Coverage:**
- Active v1.3 requirements: 33 (9 complete, 2 partial, 22 pending)
- Mapped to phases: 33 (Phases 17–21)
- Deferred: 4 (SCHED-01..03, AUD-03); merged: 1 (NOTIF-01)
- Unmapped: 0 ✓

---
*Requirements defined: 2026-07-15*
*Last updated: 2026-09-23 (final cleanup pass: ORCH-03 scoped to Job mutations, CTRL idempotency-by-target-state made explicit, dry_run/generate_signals recorded as unresolved ORCH-08 classification items) — v1.3 re-scope: ORCH-01/02 set Partial (scripts/ bypass), JOB-06 annotation resolved, ORCH-05..08 / JOBUI-01..05 / OPS-08 added, OPS-05 split into three Job types, OPS-07 constrained to operator retry with lineage, CTRL-01/02 moved to synchronous control endpoints, SCHED-01..03 and AUD-03 deferred, NOTIF-01 merged into AUD-02*
