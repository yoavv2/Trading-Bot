# Phase 18: Orchestration Surface - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-07-21
**Phase:** 18-orchestration-surface
**Areas discussed:** Phase handoff, Key reuse, Cancellation API, Job reference

---

## Phase handoff

### Execution proof

| Option | Description | Selected |
|--------|-------------|----------|
| Test handler proof | Prove submit → execute → observe with a test-only registered handler; keep production handlers in Phase 19. | ✓ |
| One real handler | Move one production operation handler into Phase 18. | |
| Contract only | Verify persistence and response contracts without executing a handler. | |

**User's choice:** Test handler proof
**Notes:** Preserve Phase 18/19 roadmap boundary while requiring executable proof.

### Existing direct CLIs

| Option | Description | Selected |
|--------|-------------|----------|
| Remove direct CLIs | Remove direct manual-operation entry points; retain worker infrastructure such as `run-jobs`. | ✓ |
| Convert now | Keep names as HTTP clients before production handlers exist. | |
| Leave temporarily | Leave direct service invocation until Phase 19. | |

**User's choice:** Remove direct CLIs
**Notes:** No temporary bypass of ORCH-01.

### Public submission shape

| Option | Description | Selected |
|--------|-------------|----------|
| Generic submit | Establish `POST /api/v1/jobs` with registered `job_type` and validated payload. | ✓ |
| Per-operation routes | Wait for Phase 19 operation-specific routes; Phase 18 builds service only. | |
| Hybrid | Operation-specific public routes backed by generic internal submission service. | |

**User's choice:** Generic submit
**Notes:** Phase 19 registers production types against this stable surface.

### Unsupported Job type

| Option | Description | Selected |
|--------|-------------|----------|
| Reject with 422 | Reject before Job creation with typed validation error. | ✓ |
| Reject with 404 | Treat requested Job type as a missing resource. | |
| Fail in worker | Persist queued Job and fail it during execution. | |

**User's choice:** Reject with 422
**Notes:** Do not create knowingly unexecutable Jobs.

---

## Key reuse

### Request identity

| Option | Description | Selected |
|--------|-------------|----------|
| Type + payload | Bind key to canonical Job type and normalized payload. | ✓ |
| Endpoint only | First Job always wins even when payload changes. | |
| Global key | Require globally unique key regardless of operation. | |

**User's choice:** Type + payload
**Notes:** Exact replay returns original Job; changed request conflicts.

### Uniqueness scope

| Option | Description | Selected |
|--------|-------------|----------|
| Per endpoint | Uniqueness is endpoint plus key. | ✓ |
| API global | One key namespace covers every mutation route. | |
| Per operator | Scope by operator identity plus endpoint. | |

**User's choice:** Per endpoint
**Notes:** Same key may be reused independently on different mutation routes.

### Changed payload

| Option | Description | Selected |
|--------|-------------|----------|
| 409 conflict | Return typed conflict with stable code and original Job ID. | ✓ |
| 422 invalid | Treat key/payload mismatch as request validation failure. | |
| Return original | Ignore mismatch and return first Job. | |

**User's choice:** 409 conflict
**Notes:** Client mistakes must not be silently masked.

### Key transport

| Option | Description | Selected |
|--------|-------------|----------|
| Required header | Require `Idempotency-Key` on every mutation; missing key returns `400`. | ✓ |
| Optional header | Synthesize a key when client omits one. | |
| Body field | Put key in each JSON mutation body. | |

**User's choice:** Required header
**Notes:** All client retries carry explicit stable identity.

---

## Cancellation API

### HTTP contract

| Option | Description | Selected |
|--------|-------------|----------|
| POST cancel | `POST /api/v1/jobs/{job_id}/cancel` performs audited state transition. | ✓ |
| PATCH job | Patch Job status/action through generic update route. | |
| DELETE job | Use deletion semantics for cancellation. | |

**User's choice:** POST cancel
**Notes:** Cancellation never implies deletion of persisted Job history.

### Reason contract

| Option | Description | Selected |
|--------|-------------|----------|
| Optional, max 500 | Trim; blank becomes null; reject over 500 characters. | ✓ |
| Required, max 500 | Require nonblank reason on every request. | |
| No reason | Record no client-supplied reason. | |

**User's choice:** Optional, max 500
**Notes:** Matches Phase 17 optional reason while adding a testable bound.

### Repeated cancellation

| Option | Description | Selected |
|--------|-------------|----------|
| Return current Job | Same-key and fresh-key repeats return current Job; preserve first requester/reason. | ✓ |
| Fresh key conflicts | Same-key replay succeeds; later fresh key returns `409`. | |
| Return 204 | Repeat succeeds without Job reference. | |

**User's choice:** Return current Job
**Notes:** Cancellation is domain-idempotent while first-request audit facts remain immutable.

### Non-cancelled terminal Job

| Option | Description | Selected |
|--------|-------------|----------|
| 409 terminal | `SUCCEEDED`/`FAILED` return typed conflict with current status; missing Job returns `404`. | ✓ |
| 200 unchanged | Return current Job for every terminal status. | |
| 422 invalid | Treat state mismatch as request validation error. | |

**User's choice:** 409 terminal
**Notes:** A successful operation must not appear cancelled.

---

## Job reference

### Reference fields

| Option | Description | Selected |
|--------|-------------|----------|
| Compact + links | Return Job ID, type, status, and self/progress/logs/events links. | ✓ |
| ID only | Return only Job ID. | |
| Full snapshot | Embed Job detail, progress, logs, and events. | |

**User's choice:** Compact + links
**Notes:** Mutation responses identify and locate observations without duplicating read models.

### Link representation

| Option | Description | Selected |
|--------|-------------|----------|
| Relative paths | Stable paths under `/api/v1/jobs/{id}`. | ✓ |
| Absolute URLs | Construct URLs from current request host. | |
| No links | Require clients to construct paths from documentation. | |

**User's choice:** Relative paths
**Notes:** Avoid reverse-proxy and deployment-host coupling.

### New versus replay

| Option | Description | Selected |
|--------|-------------|----------|
| 202 then 200 | New submission returns `202`; replay returns `200` plus replay header. | ✓ |
| Always 202 | Both new and replay return `202`. | |
| Always 200 | Both return `200` with replay information in body. | |

**User's choice:** 202 then 200
**Notes:** Exact replay returns identical Job reference and `Idempotency-Replayed: true`.

### Evolving data

| Option | Description | Selected |
|--------|-------------|----------|
| Status only | Embed point-in-time status; progress/log/event data remains behind links. | ✓ |
| Include progress | Embed current progress summary in mutation response. | |
| Links only | Omit current status. | |

**User's choice:** Status only
**Notes:** Observation endpoints remain authoritative for evolving data.

---

## Claude's Discretion

- Internal service/module/class names.
- Canonical serialization and fingerprint implementation.
- Persistence table/column and typed exception names.
- Exact error-body field names beyond locked status codes and required machine-readable facts.

## Deferred Ideas

- Production operation handlers and console triggers — Phase 19.
- Failed-Job retry — Phase 19.
- Scheduler submission through public API — Phase 20.
- Full audit/status console surfaces — Phase 21.
