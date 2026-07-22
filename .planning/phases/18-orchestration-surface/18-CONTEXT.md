# Phase 18: Orchestration Surface - Context

**Gathered:** 2026-07-21
**Status:** Ready for planning

<domain>
## Phase Boundary

Phase 18 establishes the generic operator orchestration contract: idempotent HTTP Job submission, operator-invocable cancellation, compact transport-agnostic Job references, and structural enforcement that route/CLI adapters contain no business logic. It proves submit → execute → observe end to end with a test-only registered handler. All production operation handlers and their console triggers remain in Phase 19.

Direct manual-operation CLI entry points are removed in this phase. Worker infrastructure commands, including `run-jobs`, remain because they execute queued Jobs rather than bypassing the HTTP submission surface.

</domain>

<decisions>
## Implementation Decisions

### Phase 18 / Phase 19 handoff
- **D-01:** Phase 18 MUST prove the complete `POST submission → queued Job → handler execution → observable terminal Job` flow with a test-only registered handler. The default production registry MUST remain free of Phase 19 production operation handlers.
- **D-02:** Production handlers for backtest, risk evaluation, paper trading, reconciliation, market-data sync, and broker order-lifecycle sync remain Phase 19 scope.
- **D-03:** Existing direct manual-operation CLI entry points for those operations MUST be removed from the operator command surface in Phase 18. `run-jobs` and other worker-infrastructure commands remain available.
- **D-04:** Phase 18 establishes `POST /api/v1/jobs` as the generic public submission endpoint. Requests identify a registered `job_type` and provide its validated payload.
- **D-05:** An unregistered `job_type` MUST be rejected before Job persistence with a typed HTTP `422` response. The system MUST NOT create a knowingly unexecutable Job.

### Idempotency contract
- **D-06:** Every mutating endpoint MUST require an `Idempotency-Key` request header. A missing key MUST return a typed HTTP `400` response and perform no mutation.
- **D-07:** Idempotency-key uniqueness is scoped per mutation endpoint. The same key MAY be used independently on different mutation routes.
- **D-08:** Within an endpoint, a key is bound to canonical operation identity: `job_type` plus normalized request payload for submission, and target Job plus normalized cancellation payload for cancellation.
- **D-09:** Exact replay with the same endpoint, key, and canonical operation identity MUST return the original Job and MUST NOT create or execute a second operation.
- **D-10:** Reusing an endpoint-scoped key with a different canonical operation identity MUST return typed HTTP `409`, include a stable machine-readable error code and original Job ID, and perform no mutation.

### Cancellation API
- **D-11:** Operator cancellation uses `POST /api/v1/jobs/{job_id}/cancel`; cancellation never deletes Job or audit history.
- **D-12:** Cancellation accepts an optional `reason` with a maximum length of 500 characters. Input is trimmed; blank input is stored as `null`; values over 500 characters are rejected before mutation.
- **D-13:** Cancellation MUST call `jobs.cancellation.request_cancellation` and preserve Phase 17 semantics: queued cancellation is immediate, running cancellation is cooperative, and first requester/reason remain immutable.
- **D-14:** Repeating cancellation while cancellation is already requested, or after the Job reached `CANCELLED`, MUST return the current Job reference without overwriting first-request audit facts. This applies to exact idempotency replay and a later fresh-key repeat.
- **D-15:** A fresh cancellation request against `SUCCEEDED` or `FAILED` MUST return typed HTTP `409` with current Job status. A missing Job returns `404`.

### Job reference contract
- **D-16:** Every successful mutation returns the same compact Job-reference shape with `job_id`, `job_type`, point-in-time `status`, and relative links named `self`, `progress`, `logs`, and `events`.
- **D-17:** Observation links MUST be stable relative paths under `/api/v1/jobs/{job_id}` so clients do not depend on request host, reverse-proxy configuration, polling, or push transport.
- **D-18:** Job references MUST NOT embed progress snapshots, log entries, or event entries. Clients obtain evolving state from the linked read endpoints.
- **D-19:** A newly accepted submission returns HTTP `202`. An exact idempotent replay returns HTTP `200`, the identical Job reference, and `Idempotency-Replayed: true`.
- **D-20:** Successful cancellation returns the updated compact Job reference, preserving one mutation-response contract across submission and cancellation.

### Claude's Discretion
- Internal names and module boundaries for submission/idempotency services, provided route and CLI adapters share that layer and contain no business logic.
- Canonical JSON serialization and request-fingerprint implementation, provided equivalent payloads produce the same fingerprint deterministically and mismatches are enforced transactionally.
- Persistence table/column names and typed exception class names, provided database uniqueness prevents concurrent duplicate Job creation and all HTTP outcomes above are covered by tests.
- Exact response-model class names and error-body field names beyond the locked status codes, stable machine-readable code, original Job ID, and Job-reference fields.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Milestone contracts
- `.planning/PROJECT.md` — Architecture invariants: HTTP-only orchestration, Jobs orchestrate services, CLI wrappers contain no business logic, and observation stays transport-agnostic.
- `.planning/REQUIREMENTS.md` §§ Orchestration Surface, Out of Scope, Traceability — ORCH-01 through ORCH-04, no auth or new queue infrastructure, and Phase 18 ownership.
- `.planning/ROADMAP.md` §§ Phase 18–19 — Phase boundary, success criteria, dependency, and production-handler handoff.
- `.planning/STATE.md` — Current milestone state and JOB-06 operator-surface handoff note.

### Phase 17 contracts
- `.planning/phases/17-job-framework/17-CONTEXT.md` — Locked Job lifecycle, cancellation, progress, log, event, dependency, and handler-boundary decisions.
- `.planning/phases/17-job-framework/17-06-SUMMARY.md` — Implemented cancellation behavior and test evidence.
- `.planning/phases/17-job-framework/17-09-SUMMARY.md` — Runner/registry implementation and unresolved operator-surface handoff.

### Existing implementation anchors
- `src/trading_platform/jobs/cancellation.py` — Required cancellation function and Phase 17 state-transition semantics.
- `src/trading_platform/jobs/dependencies.py` — Existing Job submission primitive and dependency validation.
- `src/trading_platform/jobs/registry.py` — Closed registered-type lookup and intentionally empty default registry.
- `src/trading_platform/api/routes/jobs.py` — Existing read-only Job router and observation endpoint paths.
- `src/trading_platform/services/job_reads.py` — Existing Job detail, progress, logs, and events service contracts.
- `src/trading_platform/db/models/job.py` — Current Job persistence shape; no idempotency contract exists yet.
- `src/trading_platform/jobs/runner.py` — Existing claim/execute/outcome flow used by end-to-end proof.
- `src/trading_platform/api/app.py` — Current read-only, DB-optional API startup behavior that Phase 18 must intentionally update.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `jobs.dependencies.submit_job`: existing queued-Job persistence and submitted-event primitive; extend behind shared orchestration service rather than duplicate in routes.
- `jobs.cancellation.request_cancellation`: mandatory cancellation mechanism for queued/running state handling.
- `services.job_reads.JobReadService`: existing detail, progress, logs, and event reads used by Job-reference links.
- `api.routes.jobs`: existing `/api/v1/jobs` router and canonical read endpoint structure.
- `jobs.registry.JobRegistry`: registered-type authority for rejecting unsupported submission types.
- `jobs.runner`: existing worker execution path for test-handler end-to-end proof.

### Established Patterns
- API routes delegate to service modules; business logic does not belong in route or CLI adapters.
- Job handlers call domain services and execute outside an open database transaction.
- Job state, progress, logs, and events are persisted and JSON-safe before API serialization.
- Cancellation preserves first-request facts and uses cooperative checkpoints for running Jobs.
- Default Job registry is intentionally empty until production types arrive in Phase 19.

### Integration Points
- Add mutation routes to existing `api.routes.jobs` router while preserving existing read contracts.
- Update API application startup from intentionally read-only behavior to the Phase 18 mutation surface without introducing auth or new infrastructure.
- Route submission through a shared orchestration/idempotency service that owns registry validation, atomic key lookup/create, and Job-reference construction.
- Remove direct manual-operation Typer registrations while retaining queue-worker commands.
- Add import/structure enforcement covering API adapters, retained CLI adapters, shared service, Job framework, and domain-service boundaries.

</code_context>

<specifics>
## Specific Ideas

- Use a test-only registered handler as executable proof rather than pulling any Phase 19 production handler forward.
- Treat unsupported `job_type` as request validation failure (`422`), not as a Job that fails later in the worker.
- Keep Job mutation responses compact and link-driven; evolving progress/log/event data remains owned by read endpoints.
- Distinguish new submission from replay through `202` versus `200` plus `Idempotency-Replayed: true`.

</specifics>

<deferred>
## Deferred Ideas

- Production operation handlers and console triggers remain Phase 19 scope.
- Retry of a failed Job remains OPS-07 in Phase 19.
- Scheduler use of the same public submission path remains Phase 20 scope.
- Full operator audit schema and console audit/status views remain Phase 21 scope.

</deferred>

---

*Phase: 18-orchestration-surface*
*Context gathered: 2026-07-21*
