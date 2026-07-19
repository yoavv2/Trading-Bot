# Requirements: Trading Strategy Platform — Milestone v1.3 Operator Platform

**Defined:** 2026-07-15
**Core Value:** Build a trustworthy, auditable trading platform that can reproducibly validate a strategy, run it in daily paper trading, and explain every action or blocked action without ambiguity.

**Milestone scope rule:** The Operator API is the single orchestration surface. Every manual operation executes through HTTP as a Job; Jobs orchestrate, services implement; scheduling produces Jobs through the same path; every action is idempotent and audited. No live trading, no auth surface, no external notification channels, no new queue infrastructure beyond PostgreSQL.

## v1.3 Requirements

Requirements for this milestone. Each maps to roadmap phases.

### Job Framework

- [ ] **JOB-01**: Every long-running operation executes as a Job with the closed lifecycle enum `QUEUED → RUNNING → SUCCEEDED / FAILED / CANCELLED`; no state outside the enum is representable
- [x] **JOB-02**: Jobs persist in PostgreSQL and survive restart — a queued job submitted before a worker restart executes after it; a running job interrupted by crash is detected and moved to a terminal state, never silently lost or duplicated
- [ ] **JOB-03**: New Job types are registered through a job-type registry without modifying queue infrastructure — adding a type touches zero queue-framework modules (enforcement test)
- [ ] **JOB-04**: Job handlers invoke domain services only; an import-boundary test asserts no domain service imports job, HTTP, scheduling, or UI modules
- [ ] **JOB-05**: A Job can declare explicit dependencies on other Jobs; a dependent Job starts only after all dependencies succeed, and a failed dependency moves dependents to a terminal non-executed state
- [ ] **JOB-06**: Operator can cancel a queued or running Job; cancellation transitions it to `CANCELLED` and is audited
- [ ] **JOB-07**: Every Job records progress and structured logs observable via the API during and after execution

### Orchestration Surface

- [ ] **ORCH-01**: Every manual operation is exposed as an HTTP API endpoint; the console invokes only the HTTP API — never business logic or CLI code directly
- [ ] **ORCH-02**: CLI worker commands are thin wrappers over the same service layer the API uses — no business logic exists in CLI or API route code (import/structure enforcement)
- [ ] **ORCH-03**: Every mutating endpoint is idempotent — resubmitting the same operation with the same idempotency key returns the existing Job instead of executing twice
- [ ] **ORCH-04**: Submitting an operation returns a Job reference whose state, progress, and logs the console observes via API reads — transport-agnostic, no architectural dependency on polling vs push

### Operation Triggers

- [ ] **OPS-01**: Operator can run a backtest from the UI
- [ ] **OPS-02**: Operator can run a risk evaluation from the UI
- [ ] **OPS-03**: Operator can run a paper trading session from the UI
- [ ] **OPS-04**: Operator can run reconciliation from the UI
- [ ] **OPS-05**: Operator can run market-data sync from the UI
- [ ] **OPS-06**: Operator can run broker order-lifecycle sync from the UI
- [ ] **OPS-07**: Operator can retry a failed run/Job from its detail view; retry explicitly creates a new Job linked to the original

### Operational Control

- [ ] **CTRL-01**: Operator can enable/disable the strategy from the UI via the API; the change is audited
- [ ] **CTRL-02**: Operator can toggle the kill switch from the UI behind an explicit confirmation; the change is audited

### Scheduling

- [ ] **SCHED-01**: The scheduler creates Jobs through the same public API path as manual submissions — an enforcement test asserts no separate scheduler execution path exists
- [ ] **SCHED-02**: Operator can view all schedules with their job type, cadence, last run, and next run
- [ ] **SCHED-03**: Operator can enable/disable/edit the Daily Paper Trading and Daily Market Data Sync schedules from the UI

### Audit

- [ ] **AUD-01**: Every operator action persists initiating operator, timestamp, operation type, request parameters, resulting Job, and final outcome
- [ ] **AUD-02**: Operator can view and filter the audit history in the console
- [ ] **AUD-03**: The audit schema carries an operator-identity field populated today with the local operator — adding multi-user later requires no persistence redesign

### Operational Status

- [ ] **NOTIF-01**: Console shows an operational status feed — job completions, failures, kill-switch trips — in-console only
- [ ] **NOTIF-02**: Failures are visible from any screen via a global indicator without navigating to a detail page

## Future Requirements

Deferred to later milestones (ATOS Stages 2–7). Tracked but not in current roadmap.

### Strategy Laboratory (Stage 2)

- **EXP-01**: Experiment domain (Strategy → Experiment → Run → Metrics → Comparison) with full reproducibility snapshots (parameters, dataset, date range, commit hash, config, environment)

### Portfolio Management (Stage 3)

- **PORT-01**: Multi-strategy capital allocation, risk budgets, correlation matrix as first-class metric

### Research Platform (Stage 4)

- **RSCH-01**: Persistent research objects — ideas, hypotheses, notes, experiments, conclusions

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
|---------|--------|
| Live trading or live-trading controls | Paper correctness must be proven first; live remains gated behind the promotion pipeline (Stage 6) |
| Multi-user auth/RBAC | Single operator in v1.x; audit model is multi-user-shaped but no auth surface ships |
| External notification channels (email/Telegram/Slack) | In-console feed suffices; broad integrations remain out of project scope |
| Redis/Celery or any new queue infrastructure | DB-backed queue in PostgreSQL — no new infra per complexity budget |
| Experiment domain | Stage 2 milestone; Job framework must be able to carry it later but it does not ship now |
| Real-time push transport commitment | Observation is transport-agnostic; a specific push mechanism is an implementation choice, not a requirement |
| Cron-style arbitrary scheduling UI | Scheduler architecture is generic, but UI exposes only the two initial schedules |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| JOB-01 | Phase 17 | Pending |
| JOB-02 | Phase 17 | Complete |
| JOB-03 | Phase 17 | Pending |
| JOB-04 | Phase 17 | Pending |
| JOB-05 | Phase 17 | Pending |
| JOB-06 | Phase 17 | Pending |
| JOB-07 | Phase 17 | Pending |
| ORCH-01 | Phase 18 | Pending |
| ORCH-02 | Phase 18 | Pending |
| ORCH-03 | Phase 18 | Pending |
| ORCH-04 | Phase 18 | Pending |
| OPS-01 | Phase 19 | Pending |
| OPS-02 | Phase 19 | Pending |
| OPS-03 | Phase 19 | Pending |
| OPS-04 | Phase 19 | Pending |
| OPS-05 | Phase 19 | Pending |
| OPS-06 | Phase 19 | Pending |
| OPS-07 | Phase 19 | Pending |
| CTRL-01 | Phase 19 | Pending |
| CTRL-02 | Phase 19 | Pending |
| SCHED-01 | Phase 20 | Pending |
| SCHED-02 | Phase 20 | Pending |
| SCHED-03 | Phase 20 | Pending |
| AUD-01 | Phase 21 | Pending |
| AUD-02 | Phase 21 | Pending |
| AUD-03 | Phase 21 | Pending |
| NOTIF-01 | Phase 21 | Pending |
| NOTIF-02 | Phase 21 | Pending |

**Coverage:**
- v1.3 requirements: 28 total
- Mapped to phases: 28 (Phases 17-21)
- Unmapped: 0 ✓

---
*Requirements defined: 2026-07-15*
*Last updated: 2026-07-15 after roadmap creation — all 28 requirements mapped to Phases 17-21*
