# Phase 17: Job Framework - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-07-19
**Phase:** 17-job-framework
**Areas discussed:** Crash recovery, Dependency outcomes, Cancellation, Observability

---

## Crash Recovery

| Decision | Options considered | Selected |
|----------|--------------------|----------|
| Lost worker or expired lease | `FAILED`; `CANCELLED`; agent discretion | `FAILED` |
| Automatic retry | No automatic retry; automatic retry; agent discretion | No automatic retry |
| Recovery visibility | Structured reason; message only; agent discretion | Structured reason |
| Possible external side effect | `FAILED` + uncertain; plain `FAILED`; agent discretion | `FAILED` + `outcome_uncertain=true` |

**User's choice:** Lost workers and expired leases become visibly `FAILED` with structured reasons. Phase 17 never automatically requeues or retries them.

**Notes:** `CANCELLED` is reserved for explicit operator or dependency-driven cancellation. Later retry creates a new linked attempt and preserves the original history.

---

## Dependency Outcomes

| Decision | Options considered | Selected |
|----------|--------------------|----------|
| Failure propagation | Transitive cascade; direct children only; agent discretion | Transitive cascade |
| Causal detail | Exact cause chain; direct cause only; reason code only | Exact cause chain |
| Dependency mutability | Immutable; editable while queued; agent discretion | Immutable |
| Cycles/self-dependency | Reject submission; create then cancel; agent discretion | Reject submission |

**User's choice:** Failed or cancelled prerequisites transitively cancel all unstarted descendants, preserving the exact root-cause chain.

**Notes:** Dependency graphs are frozen at submission and must be acyclic before a Job becomes queued.

---

## Cancellation

| Decision | Options considered | Selected |
|----------|--------------------|----------|
| Queued cancellation | Immediate `CANCELLED`; worker acknowledgement; agent discretion | Immediate `CANCELLED` |
| Running cancellation | Cooperative stop; immediate state change; force terminate | Cooperative stop |
| Ignored cancellation | `FAILED` + timeout; keep `RUNNING`; mark `CANCELLED` | `FAILED` + timeout |
| Persisted audit facts | Actor/reason/times; actor + timestamp; minimal event | Actor, reason, and times |

**User's choice:** Queued Jobs cancel atomically without handler invocation. Running Jobs become cancelled only after cooperative acknowledgement.

**Notes:** A handler that fails to stop within its grace period becomes `FAILED` with `cancellation_timeout` and `outcome_uncertain=true`.

---

## Observability

| Decision | Options considered | Selected |
|----------|--------------------|----------|
| Progress shape | Percent + step + counters; percent only; events only | Percent + step + counters |
| Unsuccessful terminal progress | Preserve last progress; reset to zero; force 100% | Preserve last progress |
| Log contract | Full structured envelope; level + message; domain events only | Full structured envelope |
| Retention | Retain with Job; configurable TTL; terminal summary only | Retain with Job |

**User's choice:** Expose truthful structured progress and append-only sanitized logs throughout the Job record's lifetime.

**Notes:** Only successful Jobs are guaranteed to reach 100%. JOB-07's generic read surface ships in Phase 17; operation submission remains Phase 18.

---

## the agent's Discretion

- PostgreSQL locking/lease mechanics, polling cadence, table/index layout, registry implementation, route naming, pagination, and cancellation grace-period default.
- Exact enforcement-test structure, provided the locked lifecycle, isolation, registry, dependency, cancellation, and observability behaviors are proven.

## Deferred Ideas

- Explicit retry as a new linked Job attempt — Phase 19 (`OPS-07`).
- Full operator audit history — Phase 21.
- Operation submission/idempotency, console controls, and scheduling — Phases 18–20.
