# Requirements: Trading Strategy Platform — Milestone v1.3 Operator Platform

**Defined:** 2026-07-15
**Re-scoped:** 2026-09-23 (repository audit — vertical-slice re-cut; scheduling and identity groundwork deferred)
**Core Value:** Build a trustworthy, auditable trading platform that can reproducibly validate a strategy, run it in daily paper trading, and explain every action or blocked action without ambiguity.

**Milestone scope rule:** Exactly two mutation paths exist. (1) Long-running operations: Console → HTTP → Job orchestration → worker → existing domain service; Jobs orchestrate, services implement. (2) Synchronous operator controls (kill switch, strategy enable/disable; from Phase 20.1 also active-paper-strategy seeding/handover, End execution operation, Record broker statement): Console or API → HTTP → `OperatorControlService`, synchronous, independent of the worker, and never making a broker call *(amended 2026-09-30: previously "immediate safety controls")*. Every mutation is idempotent and audited — Job mutations by `Idempotency-Key` (ORCH-03), safety controls by explicit target state (CTRL-01/02). No script, CLI, or Makefile bypass — sole exception: the trip-only break-glass `kill-switch-trip` worker subcommand (ORCH-01, amended 2026-09-27). No live trading, no auth surface, no external notification channels, no scheduling, no new queue/transport infrastructure beyond PostgreSQL and polling.

**Programme re-scope (2026-09-30):** Phase 20.1 (inserted) and a re-planned Phase 21 implement the Operator Console IA programme's correctness and read-model work (`.planning/research/operator-console-ia/03-PLANNING-CHANGES.md` rev. 8). AUD-02 and NOTIF-02 move to v1.4 Operator Console. Until v1.4, the new paper-trading controls are HTTP-API-only (`05-INTERIM-API-OPERATIONS.md`).

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

- [x] **ORCH-01**: Every manual long-running operation is exposed only as an HTTP Job submission and every immediate safety control only as an HTTP control endpoint — sole exception: one trip-only break-glass worker subcommand (`kill-switch-trip`, reason required, calls `OperatorControlService.trip_kill_switch`, same audit rows) that exists so the kill switch can be tripped when the API is down (amended 2026-09-27, Phase 20 discussion); the console invokes only the HTTP API — never business logic, scripts, or CLI code directly *(Closed in Phase 20: mutating scripts and Makefile targets deleted; closed-world boundary test in tests/test_orchestration_boundaries.py)*
- [x] **ORCH-02**: CLI worker commands and scripts are thin wrappers over the same service layer the API uses — no business logic exists in CLI, script, or API route code (import/structure enforcement) *(Closed in Phase 20: enforcement extended to `scripts/` and the Makefile)*
- [x] **ORCH-03**: Every Job mutation handled through `JobOrchestrationService` (submit, cancel, and — from Phase 20 — retry) is idempotent by `Idempotency-Key` — resubmitting the same operation with the same key returns the existing Job reference (Phase 18 replay contract) instead of executing twice. *(Scope: Job mutations only. Immediate safety controls are idempotent by explicit target state under CTRL-01/CTRL-02 and do not use `Idempotency-Key`.)*
- [x] **ORCH-04**: Submitting an operation returns a Job reference whose state, progress, and logs the console observes via API reads — transport-agnostic, no architectural dependency on polling vs push
- [x] **ORCH-05**: The production worker process runs the Job runner — the compose worker service command is `run-jobs`, and no deploy configuration starts the placeholder `serve` loop (config-parsing test)
- [x] **ORCH-06**: A read-only job-type catalog endpoint lists every registered Job type with a description and its cancellation mode; an enforcement test asserts every registered type appears in it. No JSON-Schema-to-form generation is required
- [x] **ORCH-07**: A configuration flag disables all mutating routes; when disabled they return a typed 403 and write zero rows, and the public `render.yaml` deploy sets it disabled. No authentication is introduced
- [x] **ORCH-08**: Exactly one mutation path per operation class — every mutating `scripts/*.py`, dead `worker/commands/*` function, and mutating/dead Makefile target is removed; a boundary test fails if any script, worker command, or Makefile target invokes a mutating domain service outside a Job handler or `OperatorControlService`, with a pinned exemption list limited to deployment tooling (`migrate`, `seed`), read/report paths, and the single trip-only break-glass `kill-switch-trip` worker subcommand (ORCH-01 exception; no reset/enable/disable CLI). **Unresolved classification items (Phase 20 must decide from actual behavior, default is NOT exemption):** `scripts/dry_run.py` and `scripts/generate_signals.py` — inspect whether each performs a state-changing/manual operation; if mutating, migrate to a Job or retire; only if genuinely read-only or development-only, add to the pinned exemption list with the reason

### Job Operations UI

- [x] **JOBUI-01**: Operator can list Jobs in the console, filtered by status and job type, through a job-type-agnostic list view
- [x] **JOBUI-02**: Operator can view a generic Job detail: status, progress, failure reason/message and `outcome_uncertain`, dependencies/blocking Job, cancellation fields, `result_summary` rendered generically, and linked domain resources via a generic `resources[]` list (a `strategy_run` resource links to the existing run-detail page; persisted `strategy_runs.job_id` FK — Phase 19 CONTEXT D-01..D-06)
- [x] **JOBUI-03**: Operator can view a Job's structured logs (cursor-based tail) and lifecycle events
- [x] **JOBUI-04**: Operator can cancel a non-terminal Job from the console behind a confirmation; the UI labels the actual outcome (`CANCELLED`, or `FAILED`/`cancellation_timeout`) honestly
- [x] **JOBUI-05**: Job list and detail refresh automatically while a Job is non-terminal and stop polling at a terminal state

### Operation Triggers

- [x] **OPS-01**: Operator can run a backtest from the UI — `backtest` is the first registered production Job type, proven end-to-end Console → HTTP → Job → worker → existing backtest service
- [x] **OPS-02**: Operator can run a risk evaluation from the UI as a registered Job
- [x] **OPS-03**: Operator can run a paper trading session from the UI as a registered Job; it is cancellable only before broker submission begins, and the catalog states this *(amended 2026-09-30, delivered by Phase 20.1: execution is eligible only for the fresh evaluation session inside its execution window — historical execution is rejected (COR-04); during the API-only interim (COMPAT-01) sessions are started through the HTTP API, not the legacy console)*
- [x] **OPS-04**: Operator can run reconciliation from the UI as a registered Job
- [x] **OPS-05**: Operator can run market-data operations from the UI as three separate, independently validated Job types — `ingest-bars`, `sync-symbol-metadata`, `sync-market-sessions`; no composite handler that switches behavior on flags
- [x] **OPS-06**: Operator can run broker order-lifecycle sync from the UI as a registered Job
- [x] **OPS-07** *(unchanged; note 2026-09-30: continuing a paused execution operation is a separate paper-session mode, REC-02, not an OPS-07 retry; retry of a `paper-session` is gated by REC-01)*: Operator can retry a `FAILED` or `CANCELLED` Job from its detail view; retry creates a new Job with the same job type and payload, linked via one nullable `retry_of_job_id`; retry submission is idempotent by `Idempotency-Key` under the ORCH-03 contract; no automatic retries, retry policies, backoff, counters, or retry scheduler
- [x] **OPS-08**: Typed domain conflicts (e.g. paper-session advisory lock already held) land as a distinct closed `failure_reason` value, not `handler_error`

### Operational Control

- [x] **CTRL-01**: Operator can enable/disable the strategy from the UI through a synchronous HTTP control endpoint (Console → HTTP → `OperatorControlService`) that is idempotent by explicit target state — requesting the current state returns it unchanged and records the existing changed/unchanged audit semantics (`OPERATOR_CONTROL` run + `ExecutionEvent`); no `Idempotency-Key`, no Job, no worker dependency
- [x] **CTRL-02**: Operator can trip/reset the kill switch from the UI behind an explicit confirmation through a synchronous HTTP control endpoint (Console → HTTP → `OperatorControlService`) that is idempotent by explicit target state — e.g. "trip" when already tripped returns the current state and records the existing changed/unchanged audit semantics; no `Idempotency-Key`, no Job; it succeeds with no worker process running

### Audit & Operational Status

- [ ] **AUD-01**: Every Job and every safety-control change is inspectable with timestamp, operation type, request parameters, resulting Job or control record, and final outcome — sourced from existing `Job`/`JobEvent`/`JobMutation` and control audit records, with no new audit tables
- _AUD-02 and NOTIF-02 moved to v1.4 Operator Console (2026-09-30) — see "v1.4 Requirements"._

### Paper-Account Ownership (Phase 20.1)

- [x] **PAPER-01**: At most one active paper strategy owns the Alpaca paper account (persisted singleton; two owners unrepresentable at the DB level); the initial state is **no owner**; `paper-session` and strategy-scoped `reconciliation` for any other strategy are rejected at submit with a typed conflict and re-checked immediately before every broker action; registered, research-available, enabled and active are distinct states; new strategies are created disabled
- [ ] **PAPER-02**: Seeding (from none) and handover (A → B or none) are synchronous, audited, idempotent controls that succeed only when checks A1–A7 pass from persisted evidence (no broker-touching work in flight and no open operation; all broker orders terminal; flat with zero unexplained exposure; no unrecognized items; no unresolved outcome for any strategy; a fresh clean account-level reconciliation after the latest broker-touching Job; outgoing owner disabled); the new owner starts disabled; each failing check yields a typed refusal naming it

### Attribution & Account Checks (Phase 20.1)

- [ ] **ACCT-01**: Account-level broker sync and reconciliation run with no owner and while trading is blocked; they submit nothing, change no ownership, lift no gate by themselves, never create positions or attributions, and store results in dedicated account-level storage — never attached to an arbitrary strategy
- [x] **COR-05**: Every broker order/fill is classified `owned` / `recorded_external` / `unrecognized` from local registration evidence (a recognizable ID alone is never ownership evidence); unexplained exposure (broker quantity minus attributed net fills) blocks new trading wherever it occurs in history; broker sync never adopts positions; page-cap overflow surfaces as unresolved, never truncation
- [ ] **EXT-01**: "Record external activity" is an audited Job that re-fetches verified broker records, requires every listed external order terminal and net external exposure zero, stores immutable snapshots preserving external origin with no strategy or position, and runs a fresh account-level reconciliation in the same Job; recording alone never lifts a block; recorded items are re-verified on every check

### Submission Uncertainty & Recovery (Phase 20.1)

- [x] **COR-06**: Order submissions are never automatically re-sent after an ambiguous failure; every HTTP attempt is logged durably; a submission is `not_sent` only if every attempt failed before connection with complete outcomes; any ambiguous attempt makes the intent `UNKNOWN` and the Job outcome uncertain; broker statuses map completely (`done_for_day` working, `replaced` terminal-with-successor, unmapped → unresolved)
- [ ] **REC-01**: An uncertain outcome is resolved only when every registered intent is classified — found and verified at the broker (any state), `nothing_submitted` with execution-path evidence, or proven not sent (every attempt in the intent's whole history carries positive evidence, recorded by the sending executor, that the request never left the process; a missing outcome is never proof) — and a fresh clean standalone reconciliation follows the latest broker-touching Job; a never-found order stays unresolved regardless of elapsed time, absence evidence, executor termination or a recorded broker statement of non-receipt (retained as audited evidence only); no order request is resent while the original may still produce an execution; while unresolved, every `paper-session` submission (fresh, retry or Continue, under any operation, evaluation or order version) for the strategy is rejected and ownership seeding/handover/release is refused; recovery stays available after End; broker sync is never gated and never resolves by itself *(supersedes Phase 20 D-19; amended 2026-10-04: the earlier non-receipt release rule is superseded, and a never-found order blocks with no product-level release — temporary limitation TL-4)*
- [ ] **REC-02**: A paper session is an execution operation (at most one open per strategy, DB-enforced) that submits sequentially and pauses whenever a submitted order's effects are not yet accounted; explicit Continue (its own mode and Idempotency-Key) re-checks terminal orders, sync, a fresh clean reconciliation, provenance and fresh per-intent risk, skips submitted intents and never creates new order versions; a price deviation beyond tolerance pauses and sends nothing, and an explicit Continue sends the same pinned intent (same identity and quantity; no replanning, resizing or new version) only after rerunning every applicable freshness, permission, provenance, recovery, reconciliation, price, risk and TL-10 allowance check, while changed evaluation inputs or strategy settings still require re-evaluation, window expiry still terminates the operation and an unresolved earlier submission still blocks (PD-1 approved 2026-10-04); End and window expiry terminate unsent intents only and never cancel broker orders, resolve uncertainty, erase recovery records or change trading permission

### Account Truth, Outcomes, Calendar & Provenance (Phase 20.1)

- [x] **COR-01**: Risk evaluation persists no account snapshot; the reconciliation baseline is the latest broker-observed snapshot; sizing uses broker-observed cash, never margin buying power (replaying evaluate → reconcile against an unchanged broker account yields no divergence)
- [x] **COR-03**: Batch operations (`ingest-bars`, `sync-symbol-metadata`) report `complete | partial | failed` from the domain result (never stored on the Job); symbol-level vs operation-level failures are distinguished; a symbol missing required metadata is `not_ready(missing_metadata)` and its candidates are rejected `symbol_not_ready`
- [x] **COR-04**: Trading day, evaluation session (latest completed session with ready data) and execution window are separate facts with explicit `unknown(calendar_data_unavailable)`; the initial execution policy (regular hours of the session after the evaluation session) is a named setting; historical execution is rejected while research/backtests/evaluation of past sessions remain; the calendar may sync ahead to a configurable horizon; session-scoped defaults never fall back to "latest session with bars"
- [ ] **PROV-01**: A risk evaluation records an input manifest (every read request through the shared accessors with result digests, including empty results and resolved as-of bounds, plus the signal-settings digest); corrected, added or removed source data or changed signal settings make it stale; expected portfolio changes (including earlier fills in the same operation) never do

### Legacy Console Compatibility (Phase 20.1)

- [ ] **COMPAT-01**: During the API-only interim the existing console stays truthful with the smallest changes: no paper-session start or paper-session Retry; Outcome shown distinct from Job status (a paused operation is never a plain success); reconciliation/sync shortcuts and forms default to account scope; the reconciliation panel never states "does not block execution" while trading is blocked; a read-only active-strategy line; no new pages, routes or controls; API changes additive; console contract tests green

### Operator Read Models (Phase 21)

- [ ] **OPR-01**: Overview read — trading permission (closed blockers, same gate functions as the paper session), four posture lanes, verdict and next action from one closed decision table, evaluation-session pipeline summary, recent activity, environment (mode, broker, mutations flag, active strategy or none), the three calendar facts separately, account with source and as-of
- [ ] **OPR-02**: Issues read — a closed rule enum computed on read (never persisted), each rule mapped to exactly one lane, severity and resolution code; session-dependent rules suppressed when the evaluation session is unknown
- [ ] **OPR-03**: Sessions and execution-operation read — pipeline per evaluation session with closed stage statuses, attempts and records; operation state, reason, next action and intent states; historical sessions never executable
- [ ] **OPR-04**: Market-data coverage read — per-symbol readiness (bars, history, metadata), calendar horizon and runway, recent batch outcomes
- [ ] **OPR-05**: Reconciliation read — list/detail across owner-scope and account-scope checks with classes, origin tags, unexplained exposure and recorded external items
- [ ] **OPR-06**: Activity read — one keyset-paginated stream with closed kind/domain/outcome covering operations, control changes (translated from storage, never runs), external recordings, session outcomes and reconciliations of both scopes
- [ ] **OPR-07**: Catalog and job-list extensions — `broker_effect`, `session_scoped`, machine-readable prerequisites, `console_submission`; job list carries outcome/operation and truncated failure message without N+1; every job type declares its operator mapping (test-enforced)
- [ ] **OPR-08**: Every operator read returns server `as_of`, writes nothing, and has an asserted query-count bound

### Worker Health (Phase 21)

- [ ] **WRK-01**: The run-jobs worker persists its own heartbeat (throttled upsert, graceful stop recorded, stale rows pruned) in `worker_heartbeats`, written from the jobs infrastructure layer
- [ ] **WRK-02**: Worker health read reports exactly `idle | busy | unavailable | unknown` (with reason); the absence of an active Job is never evidence of health; worker health is not part of `/ready`

## v1.4 Requirements (Operator Console — planned)

- [ ] **AUD-02** *(moved from v1.3, 2026-09-30)*: Operator can view and filter the operational history (kind, type, outcome, date range) in the console, including job completions, failures, and kill-switch trips *(absorbs NOTIF-01; built on OPR-06)*
- [ ] **NOTIF-02** *(moved from v1.3, 2026-09-30)*: Failures, uncertainty and anything needing attention are visible from any screen via a global indicator without navigating to a detail page *(built on OPR-02)*
- Further v1.4 requirements (areas, mobile) are defined after Phase 21.

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

Deferred to later milestones (ATOS Stages 2–7). Tracked but not in current roadmap. **Milestone order (2026-09-30): v1.3 → v1.4 Operator Console → v1.5 Strategy Research / Strategy Lab.**

### Operator programme follow-ups (deferred; not prerequisites for v1.3/v1.4)

- **RISK-COMMIT-01**: Open-order-aware (commitment-aware) risk accounting — removes TL-1/TL-2 pauses
- **FLAT-01**: Flatten / exits-only wind-down for handover when not flat
- **INT-01**: Intervention control to adopt external positions into a strategy
- **LEDGER-01**: Attribution ledger / scan watermark (volume-triggered; removes TL-7)
- **CONC-01**: Concurrent multi-strategy paper trading (fill-level attribution, capital allocation, cross-strategy caps)

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
| ORCH-01 | Phase 18 → closed Phase 20 | Complete |
| ORCH-02 | Phase 18 → closed Phase 20 | Complete |
| ORCH-03 | Phase 18 (Job mutations; retry extends it in Phase 20) | Complete |
| ORCH-04 | Phase 18 | Complete |
| ORCH-05 | Phase 19 | Complete |
| ORCH-06 | Phase 19 | Complete |
| ORCH-07 | Phase 19 | Complete |
| JOBUI-01 | Phase 19 | Complete |
| JOBUI-02 | Phase 19 | Complete |
| JOBUI-03 | Phase 19 | Complete |
| JOBUI-04 | Phase 19 | Complete |
| JOBUI-05 | Phase 19 | Complete |
| OPS-01 | Phase 19 | Complete |
| OPS-02 | Phase 20 | Complete |
| OPS-03 | Phase 20 | Complete (code; 20-27 Alpaca pagination fix); live re-run passed 2026-09-29 (20-HUMAN-UAT test 6) |
| OPS-04 | Phase 20 | Complete (code; 20-27 Alpaca pagination fix); live re-run passed 2026-09-29 (20-HUMAN-UAT test 6) |
| OPS-05 | Phase 20 | Complete |
| OPS-06 | Phase 20 | Complete (code; 20-27 Alpaca pagination fix); live re-run passed 2026-09-29 (20-HUMAN-UAT test 6) |
| OPS-07 | Phase 20 | Complete |
| OPS-08 | Phase 20 | Complete |
| CTRL-01 | Phase 20 | Complete |
| CTRL-02 | Phase 20 | Complete |
| ORCH-08 | Phase 20 | Complete |
| PAPER-01 | Phase 20.1 | Complete |
| PAPER-02 | Phase 20.1 | Pending |
| ACCT-01 | Phase 20.1 | Pending |
| COR-05 | Phase 20.1 | Complete |
| EXT-01 | Phase 20.1 | Pending |
| COR-06 | Phase 20.1 | Complete |
| REC-01 | Phase 20.1 | Pending |
| REC-02 | Phase 20.1 (plans 20.1-11, 20.1-15, 20.1-16) | Pending |
| COR-01 | Phase 20.1 | Complete |
| COR-03 | Phase 20.1 | Complete |
| COR-04 | Phase 20.1 | Complete |
| PROV-01 | Phase 20.1 | Pending |
| COMPAT-01 | Phase 20.1 | Pending |
| AUD-01 | Phase 21 | Pending |
| OPR-01 | Phase 21 | Pending |
| OPR-02 | Phase 21 | Pending |
| OPR-03 | Phase 21 | Pending |
| OPR-04 | Phase 21 | Pending |
| OPR-05 | Phase 21 | Pending |
| OPR-06 | Phase 21 | Pending |
| OPR-07 | Phase 21 | Pending |
| OPR-08 | Phase 21 | Pending |
| WRK-01 | Phase 21 | Pending |
| WRK-02 | Phase 21 | Pending |
| AUD-02 | v1.4 (Phase 26, Activity) | Pending (moved 2026-09-30) |
| NOTIF-02 | v1.4 (Phase 23, Shell) | Pending (moved 2026-09-30) |
| SCHED-01..03 | — | Deferred (Paper Automation) |
| AUD-03 | — | Deferred |
| NOTIF-01 | v1.4 (via AUD-02) | Merged |

**Coverage:**
- Active v1.3 requirements: 54 (30 complete, 24 pending — AUD-01 plus 23 added 2026-09-30); COR-02 of the programme is realized by REC-01 + REC-02
- Mapped to phases: 54 (Phases 17–21, incl. inserted 20.1)
- v1.4 requirements mapped so far: 2 (AUD-02, NOTIF-02; moved from v1.3)
- Deferred: 4 (SCHED-01..03, AUD-03); merged: 1 (NOTIF-01)
- Unmapped: 0 ✓

---
*Requirements defined: 2026-07-15*
*Last updated: 2026-09-30 — Operator Console IA programme: 23 requirements added for Phases 20.1/21 (PAPER-01/02, ACCT-01, COR-01/03/04/05/06, EXT-01, REC-01/02, PROV-01, COMPAT-01, OPR-01..08, WRK-01/02); AUD-02 and NOTIF-02 moved to v1.4; mutation path 2 wording amended; OPS-03/OPS-07 annotated; programme follow-ups recorded. 2026-10-04 — REC-01 amended (non-receipt statement is evidence only; no resend while the original may still execute; proven-not-sent defined); REC-02 price pause (PD-1) approved 2026-10-04 (04 "Planning correction, round 6").*
*Previous update 2026-09-23 (final cleanup pass: ORCH-03 scoped to Job mutations, CTRL idempotency-by-target-state made explicit, dry_run/generate_signals recorded as unresolved ORCH-08 classification items) — v1.3 re-scope: ORCH-01/02 set Partial (scripts/ bypass), JOB-06 annotation resolved, ORCH-05..08 / JOBUI-01..05 / OPS-08 added, OPS-05 split into three Job types, OPS-07 constrained to operator retry with lineage, CTRL-01/02 moved to synchronous control endpoints, SCHED-01..03 and AUD-03 deferred, NOTIF-01 merged into AUD-02*
*Traceability note 2026-10-03: REC-02 is delivered by three plans (20.1-11 storage/state machine/End, 20.1-15 submission execution, 20.1-16 Continue); no other requirement row changed.*
