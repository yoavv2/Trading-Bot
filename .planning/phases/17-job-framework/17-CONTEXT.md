# Phase 17: Job Framework - Context

**Gathered:** 2026-07-19
**Status:** Ready for planning

<domain>
## Phase Boundary

Deliver the generic PostgreSQL-backed Job foundation: a closed five-state lifecycle, restart-safe worker execution, explicit dependencies, cooperative cancellation, progress, structured logs, and generic read access for observing Jobs during and after execution. Jobs orchestrate existing domain services and never contain domain behavior.

Phase 17 does not add operation-specific submission endpoints, console controls, scheduling, or the complete cross-operation audit surface. Phase 18 owns generic operation submission and idempotency; Phase 19 owns operation-specific triggers and explicit retry; Phases 20–21 own scheduling and full operator audit/status.

</domain>

<decisions>
## Implementation Decisions

### Crash Recovery
- **D-01:** A Job whose worker is lost or whose lease expires transitions from `RUNNING` to `FAILED`. Persist a structured failure reason such as `worker_lost` or `lease_expired`; `CANCELLED` must not represent infrastructure failure.
- **D-02:** Phase 17 never automatically requeues or retries a crashed Job. Recovery is deterministic and visible. A later explicit retry creates a new linked attempt while preserving the failed Job.
- **D-03:** If a worker disappears after a domain service may have produced an external side effect but before success was persisted, the Job remains `FAILED` and records `outcome_uncertain=true`; reconciliation or explicit operator review is then required.

### Dependency Outcomes
- **D-04:** When a dependency fails or is cancelled, every still-unstarted downstream descendant transitions transitively to `CANCELLED`. Dependency-driven cancellation is a legitimate use of `CANCELLED`; dependents must never remain stranded in `QUEUED`.
- **D-05:** A dependency-cancelled Job records the exact causal chain: the blocking Job ID, that Job's terminal state, and the root failed or cancelled ancestor.
- **D-06:** A Job's dependency set is immutable after submission. Reject self-dependencies and cycles before the Job is queued; cyclic graphs must never be representable.

### Cancellation
- **D-07:** Cancelling a `QUEUED` Job atomically transitions it immediately to `CANCELLED` and guarantees its handler is never invoked.
- **D-08:** Cancelling a `RUNNING` Job is cooperative. Persist a cancellation request, require handlers to check safe points, and transition to `CANCELLED` only after the handler acknowledges that it stopped.
- **D-09:** If a running handler ignores cancellation beyond the configured grace period, transition the Job to `FAILED` with reason `cancellation_timeout` and `outcome_uncertain=true`; do not falsely report successful cancellation.
- **D-10:** Phase 17 cancellation history records requester identity, optional reason, `requested_at`, `acknowledged_at`, and terminal cause.

### Progress, Logs, and Read Access
- **D-11:** The generic progress snapshot supports an optional integer percentage from 0–100, a current step/message, and optional current/total counters so different Job types can report useful progress without inventing separate schemas.
- **D-12:** `FAILED` and `CANCELLED` Jobs preserve their last reported progress. Only `SUCCEEDED` is guaranteed to finish at 100%.
- **D-13:** Job logs are append-only structured records with timestamp, level, stable event code, human-readable message, Job ID, handler type, and sanitized context. Ordering must be deterministic.
- **D-14:** Progress and logs remain queryable for the lifetime of the Job record. Phase 17 adds no automatic pruning or compaction.
- **D-15:** Phase 17 includes the generic read surface needed by JOB-07 to query lifecycle, progress, and logs during and after execution. Operation submission and the broader orchestration API remain Phase 18 scope.

### the agent's Discretion
- PostgreSQL claim/locking mechanism, lease and heartbeat intervals, worker polling strategy, and stale-worker detection implementation.
- Exact table decomposition, indexes, migration layout, and whether progress is stored on the Job row or in related records.
- Job-handler registry construction and discovery mechanism, provided new Job types touch zero queue-framework modules.
- Cooperative cancellation token/checkpoint interface and the default cancellation grace period.
- Generic read-route names, pagination/cursor format, polling metadata, and log-volume safeguards.
- Exact enforcement-test implementation for lifecycle constraints, registry extensibility, and import boundaries.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Product and Architecture Contract
- `.planning/PROJECT.md` — v1.3 architecture invariants: single orchestration surface, orchestration-only Jobs, registry extensibility, transport-agnostic observation, dependency support, service isolation, and PostgreSQL-only infrastructure.
- `.planning/REQUIREMENTS.md` — authoritative JOB-01 through JOB-07 acceptance requirements and later-phase scope boundaries.
- `.planning/ROADMAP.md` — Phase 17 goal, dependency position, and five measurable success criteria.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `src/trading_platform/strategies/registry.py`: existing explicit register/resolve pattern and duplicate/unknown-type errors provide a local registry precedent.
- `src/trading_platform/db/models/strategy_run.py`: SQLAlchemy `StrEnum` persistence with `validate_strings=True`, UUID identity, timestamps, structured JSON snapshots, and terminal error fields.
- `src/trading_platform/db/models/execution_event.py`: durable append-style event records with timestamps, severity, message, JSON details, and query-oriented indexes.
- `src/trading_platform/db/session.py`: the only authorized synchronous engine/session lifecycle and transaction boundary.
- `src/trading_platform/core/logging.py`: established structured-log sanitization chokepoint that Job log persistence must respect.

### Established Patterns
- Worker entrypoint `src/trading_platform/worker/__main__.py` is routing-only; command modules delegate to services rather than implementing business logic.
- Domain services are synchronous and SQLAlchemy-backed. Jobs must invoke those services without making services depend on Jobs, HTTP, scheduling, or UI.
- PostgreSQL enums and constraints are migration-backed and enforcement-tested.
- Existing structural tests use explicit import/path enforcement; Phase 17 should extend that style for handler and service boundaries.

### Integration Points
- New Job ORM models join `src/trading_platform/db/models/` and require an Alembic migration plus migration enforcement tests.
- Queue/worker orchestration should live in a bounded service package and connect to the worker routing layer without expanding `worker/__main__.py`.
- Generic observation connects to FastAPI through read-only Job routes; operation-specific submission routes remain outside this phase.
- Job handlers call existing service entry points and emit progress/log records through framework-owned context, without moving domain behavior into handlers.

</code_context>

<specifics>
## Specific Ideas

- Stable failure/cancellation codes discussed explicitly: `worker_lost`, `lease_expired`, `cancellation_timeout`, `dependency_failed`, and `dependency_cancelled`.
- Use `outcome_uncertain=true` whenever the framework cannot prove whether an external side effect occurred.
- Preserve causal links rather than flattening dependency failure into an untraceable message.

</specifics>

<deferred>
## Deferred Ideas

- Explicit retry that creates a new Job linked to the failed original belongs to Phase 19 (`OPS-07`); Phase 17 only preserves the history/linkage capability needed later.
- Full operator-action audit across all mutations belongs to Phase 21. Phase 17 persists the cancellation facts required by JOB-06 but does not build the complete audit-history product.
- Operation-specific submission endpoints, idempotency-key behavior, console controls, and scheduling remain in Phases 18–20.

</deferred>

---

*Phase: 17-job-framework*
*Context gathered: 2026-07-19*
