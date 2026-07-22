# Phase 18: Orchestration Surface - Research

**Researched:** 2026-07-21
**Domain:** FastAPI/SQLAlchemy/PostgreSQL idempotent Job mutation API
**Confidence:** HIGH

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

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

### Deferred Ideas (OUT OF SCOPE)
- Production operation handlers and console triggers remain Phase 19 scope.
- Retry of a failed Job remains OPS-07 in Phase 19.
- Scheduler use of the same public submission path remains Phase 20 scope.
- Full operator audit schema and console audit/status views remain Phase 21 scope.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|---|---|---|
| ORCH-01 | Every manual operation is exposed through HTTP; console never invokes business logic/CLI directly. | Remove the named direct-operation CLI registrations; add only generic `POST /api/v1/jobs`; prove submit → worker → read-API with a test-only handler. [VERIFIED: codebase grep] |
| ORCH-02 | CLI worker commands and API routes are thin adapters over shared logic, with structural enforcement. | Keep `run-jobs` as the process adapter over `jobs.runner`; route handlers delegate to one orchestration service; add AST/source-boundary tests and a retained-command whitelist. [VERIFIED: codebase grep] |
| ORCH-03 | Each mutating endpoint is idempotent. | Persist endpoint-scoped keys plus canonical fingerprints under a PostgreSQL unique constraint; test sequential and concurrent duplicate submission, replay, mismatch, and cancellation repeat paths. [CITED: https://docs.sqlalchemy.org/21/dialects/postgresql.html] |
| ORCH-04 | Submission returns a transport-agnostic Job reference observable through API reads. | Construct one compact reference from existing Phase-17 read paths; test only relative links and verify progress/log/event payloads are absent. [VERIFIED: codebase grep] |
</phase_requirements>

## Summary

Phase 17 already supplies the persistence-backed lifecycle, `submit_job`, registry, cancellation primitive, runner, and read-only `/api/v1/jobs` observation routes. [VERIFIED: codebase grep] Phase 18 should add a narrow orchestration layer, not operation handlers or console UI: validate a registered type before a write, atomically resolve-or-create one Job for an endpoint-scoped idempotency key, and return a compact link-driven reference. [VERIFIED: codebase grep]

The key design constraint is atomicity. The existing `jobs.dependencies.submit_job()` opens and commits its own `session_scope`; using it unchanged after a separate idempotency lookup cannot make “key reservation plus Job creation” atomic. [VERIFIED: codebase grep] Refactor or supplement it with a session-owned primitive, then have the orchestration service own one transaction containing idempotency record creation/lookup and Job persistence. PostgreSQL's named unique constraint must be the concurrency backstop; application-level lookup alone is insufficient. [CITED: https://docs.sqlalchemy.org/21/dialects/postgresql.html]

No production handler belongs in this phase: `build_default_registry()` is deliberately empty, and the end-to-end proof must inject one test-only handler into both the API submission dependency and `run_worker_loop`. [VERIFIED: codebase grep] Remove direct CLI triggers for the named Phase-19 operations while retaining worker infrastructure, especially `run-jobs`. [VERIFIED: codebase grep]

**Primary recommendation:** Add a DB-constrained `JobMutation`/idempotency record and one synchronous `JobOrchestrationService`; make `POST /api/v1/jobs` and `POST /api/v1/jobs/{job_id}/cancel` pure HTTP adapters over it, and prove the contract with real migrated-PostgreSQL tests before deleting direct operation CLI registrations.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|---|---|---|---|
| Generic Job submission and idempotency | API / Backend | Database / Storage | The HTTP adapter receives the header/body; the shared service owns canonical identity and one DB transaction; the database enforces concurrency uniqueness. [VERIFIED: codebase grep] |
| Idempotency record/key conflict | Database / Storage | API / Backend | A unique endpoint/key constraint prevents concurrent duplicate Job creation; the service translates replay/mismatch outcomes to typed route responses. [CITED: https://docs.sqlalchemy.org/21/dialects/postgresql.html] |
| Cancellation request | API / Backend | Database / Storage | The route delegates to orchestration service, which calls Phase-17 `request_cancellation`; that function row-locks the Job and persists cancellation facts. [VERIFIED: codebase grep] |
| Job execution | API / Backend | Database / Storage | `run-jobs` starts `run_worker_loop`; the runner claims persisted Jobs and invokes a registered handler outside an open DB transaction. [VERIFIED: codebase grep] |
| Observation | API / Backend | Browser / Client | Existing read routes expose detail/progress/logs/events; mutation responses supply stable relative links rather than transport-specific updates. [VERIFIED: codebase grep] |
| Console trigger UI | Browser / Client | API / Backend | It is explicitly Phase 19 scope, so Phase 18 supplies only the generic HTTP contract it will consume. [VERIFIED: codebase grep] |

## Standard Stack

### Core

| Library / component | Version constraint | Purpose | Why standard here |
|---|---:|---|---|
| FastAPI | `>=0.131.0,<1.0.0` | Route declaration, header/body validation, response status/header setting. | Already the API framework; `Header` maps `idempotency_key` to `Idempotency-Key`, and `Response` permits dynamic replay status/header output. [CITED: https://fastapi.tiangolo.com/tutorial/header-params/] [CITED: https://fastapi.tiangolo.com/advanced/response-change-status-code/] |
| SQLAlchemy + PostgreSQL dialect | `>=2.0.0,<3.0.0` | ORM models, session transaction, PostgreSQL `ON CONFLICT` keyed by a named unique constraint. | Already the persistence stack and its PostgreSQL dialect supports conflict handling against a named `UniqueConstraint`. [CITED: https://docs.sqlalchemy.org/21/dialects/postgresql.html] |
| Alembic | `>=1.18.0,<2.0.0` | Add/reverse the idempotency persistence schema. | The repository uses sequential reversible migrations through `0018_phase17_job_framework.py`. [VERIFIED: codebase grep] |
| Phase-17 Jobs framework | repository code | Job persistence, registry, cancellation, execution, and observation. | Reuse `submit_job` logic behind the shared transaction, `request_cancellation`, `JobRegistry`, `run_worker_loop`, and `JobReadService`; do not rebuild lifecycle/queue behavior. [VERIFIED: codebase grep] |

### Supporting

| Component | Purpose | When to use |
|---|---|---|
| Pydantic/FastAPI request models | Constrain the transport envelope and normalized cancellation reason before service mutation. | Use route input models only for `job_type`, JSON-object shape, and `reason` length/type. Type-specific payload validation is transport-neutral and registry-adjacent through `JobSubmissionSpec`; normalize trim-to-null in the shared service. [VERIFIED: codebase grep] |
| `hashlib` + stdlib `json` | Produce a deterministic fixed-size fingerprint from canonical endpoint operation identity. | Use only in the shared service after validation/normalization; persist the resulting digest, not an unbounded raw canonical string. [ASSUMED] |
| pytest + FastAPI `TestClient` + real temporary PostgreSQL databases | HTTP contract, migration, concurrency, runner E2E, and source-boundary tests. | Follow `tests/test_job_api.py` and other Phase-17 modules' local create/upgrade/drop fixture pattern. [VERIFIED: codebase grep] |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|---|---|---|
| DB unique endpoint/key constraint plus transactional service | Process-local key cache | Reject: multiple API workers/restarts make in-memory state non-durable and it cannot enforce concurrent uniqueness. [ASSUMED] |
| PostgreSQL `INSERT ... ON CONFLICT` / named constraint | Lookup then insert without a constraint | Reject: two concurrent requests can both observe no existing row and create duplicate Jobs. [CITED: https://docs.sqlalchemy.org/21/dialects/postgresql.html] |
| Relative Job links | Absolute URLs built from request host | Reject: D-17 forbids coupling clients to host/reverse-proxy configuration. |
| Test-only injected registry entry | Registering an operation handler in `build_default_registry()` | Reject: D-01/D-02 require the production default registry to remain free of Phase-19 handlers. [VERIFIED: codebase grep] |

**Installation:** No new external package is needed or recommended. [VERIFIED: codebase grep]

## Architecture Patterns

### System Architecture Diagram

```text
HTTP client
  │ POST /api/v1/jobs + Idempotency-Key
  ▼
FastAPI jobs route (parse/header only)
  │
  ▼
JobOrchestrationService.submit()
  ├─ JobRegistry.resolve(job_type) ──unregistered──> typed 422; no write
  ├─ resolve JobSubmissionSpec + validate_payload(payload) ──invalid──> typed 422; no write
  ├─ canonical fingerprint from the validated/normalized payload
  └─ one DB transaction
       ├─ reserve/read endpoint-scoped idempotency record
       ├─ exact key+fingerprint replay ──> existing Job reference (200 + header)
       ├─ same key, different fingerprint ──> typed 409 + original Job ID
       └─ new key ──> persist idempotency record + QUEUED Job + SUBMITTED event (202)
                                      │
                                      ▼
                          run-jobs → run_worker_loop → registered handler
                                      │
                                      ▼
                             terminal persisted Job / logs / events
                                      │
                                      ▼
GET /api/v1/jobs/{id}, /progress, /logs, /events

HTTP client
  │ POST /api/v1/jobs/{id}/cancel + Idempotency-Key
  ▼
FastAPI jobs route → JobOrchestrationService.cancel()
  │                    └─ request_cancellation(job_id, requested_by, reason)
  ▼
compact Job reference; Phase-17 queued-immediate/running-cooperative semantics
```

The existing route module is intentionally read-only today, `request_cancellation()` is the required state-change entry point, and the runner owns execution outside an open database transaction. [VERIFIED: codebase grep]

### Recommended Project Structure

```text
src/trading_platform/
├── api/
│   ├── dependencies.py             # build/inject orchestration service and registry
│   └── routes/jobs.py              # HTTP-only GET + POST adapters and typed exception mapping
├── db/models/
│   └── job_mutation.py             # endpoint key, fingerprint, referenced Job (recommended)
├── jobs/
│   ├── dependencies.py             # session-owned submit primitive extracted from submit_job
│   ├── cancellation.py             # unchanged cancellation state semantics
│   └── registry.py                 # default remains empty; test registry injected only in tests
├── services/
│   └── job_orchestration.py        # submission/cancellation/idempotency/reference construction
└── worker/
    ├── parser.py                   # remove direct operation subcommands
    └── commands/run_jobs.py        # retained queue-worker process adapter

alembic/versions/
└── 0019_phase18_job_idempotency.py # reversible table/constraint migration

tests/
├── test_job_orchestration.py       # service/idempotency concurrency and state invariants
├── test_job_mutation_api.py        # HTTP response contracts + full E2E proof
└── test_orchestration_boundaries.py # AST/source CLI/API/service boundary assertions
```

The exact module/table names are discretionary; the dependency direction is not: API/worker adapters may import orchestration/framework code, while `services/` must remain free of `api`, `worker`, `fastapi`, and `jobs` imports under the existing JOB-04 boundary test. [VERIFIED: codebase grep]

### Pattern 1: Transactional idempotency reservation plus Job creation

**What:** Persist one record containing an endpoint identifier, client key, operation fingerprint, and Job foreign key; enforce `UNIQUE(endpoint_id, idempotency_key)`. [CITED: https://docs.sqlalchemy.org/21/dialects/postgresql.html]

**When to use:** Every mutation route, including cancellation. A new cancellation key can point at the same existing Job; it must not synthesize a second cancellation audit request if cancellation is already pending or terminal-cancelled. [VERIFIED: codebase grep]

**Implementation requirements:**
- Use endpoint identifiers that distinguish submission and cancellation; do not key only by client header. [VERIFIED: codebase grep]
- Normalize/validate first, then fingerprint `job_type + payload` for submission and `target job ID + normalized reason` for cancellation. [ASSUMED]
- Reserve the key and create/link the Job in one transaction; on conflict read the original record and compare its persisted fingerprint before returning replay or mismatch. [CITED: https://docs.sqlalchemy.org/21/dialects/postgresql.html]
- Extract a `Session`-accepting Job insert helper from `submit_job`; retain `submit_job()` as a compatibility wrapper if Phase-17 tests still call it. Calling the current self-committing function from the service would split the atomic boundary. [VERIFIED: codebase grep]
- Treat a database conflict as normal control flow, never as an unhandled `IntegrityError`/500. [ASSUMED]

### Pattern 2: One compact reference builder

**What:** One service-owned function converts a `Job` into exactly this mutation result shape:

```python
# Source: repository route paths in api/routes/jobs.py [VERIFIED: codebase grep]
{
    "job_id": str(job.id),
    "job_type": job.job_type,
    "status": job.status.value,
    "links": {
        "self": f"/api/v1/jobs/{job.id}",
        "progress": f"/api/v1/jobs/{job.id}/progress",
        "logs": f"/api/v1/jobs/{job.id}/logs",
        "events": f"/api/v1/jobs/{job.id}/events",
    },
}
```

**When to use:** Every successful submit, replay, cancellation request, and permitted repeated cancellation. [VERIFIED: codebase grep]

**Rules:** Do not call `JobReadService` to construct this smaller mutation contract and do not embed `progress`, `logs`, or `events`; the existing GET routes remain the source for evolving state. [VERIFIED: codebase grep]

### Pattern 3: HTTP adapter maps typed service outcomes only

**What:** Keep FastAPI code responsible for parsing `Idempotency-Key`, producing documented statuses/headers, and translating service exceptions; keep all registry, fingerprint, idempotency, persistence, and cancellation decisions in the shared service. [CITED: https://fastapi.tiangolo.com/tutorial/header-params/] [CITED: https://fastapi.tiangolo.com/advanced/response-change-status-code/]

**Example:**

```python
# Source: FastAPI Header + dynamic Response docs; repository route/dependency pattern
@router.post("", status_code=202)
def submit_job(
    body: SubmitJobRequest,
    idempotency_key: Annotated[str | None, Header()] = None,
    response: Response,
    service: Annotated[JobOrchestrationService, Depends(get_job_orchestration_service)],
) -> JobReference:
    if idempotency_key is None:
        raise MissingIdempotencyKeyError()
    result = service.submit(body, idempotency_key=idempotency_key)
    if result.replayed:
        response.status_code = 200
        response.headers["Idempotency-Replayed"] = "true"
    return result.reference
```

FastAPI would normally emit `422` for a required missing `Header`; D-06 instead locks a typed `400`, so make the header optional at parsing and explicitly translate its absence before invoking the service. [CITED: https://fastapi.tiangolo.com/tutorial/header-params/] [ASSUMED]

### Pattern 4: Test-only E2E handler plus submission-spec injection

**What:** Define a minimal handler and transport-neutral `JobSubmissionSpec` in the test module, register them together only in a test `JobRegistry`, inject that registry into the API dependency, then pass the same registry to `run_worker_loop`. The spec exposes `job_type` and `validate_payload(payload: Mapping[str, Any]) -> Mapping[str, Any]`; it returns the normalized JSON-safe payload or raises typed `InvalidJobPayloadError`. [VERIFIED: codebase grep]

**When to use:** The required Phase-18 submit → validate → execute → observe proof. It keeps `build_default_registry()` empty, preserves the frozen `JobHandler` protocol, and gives Phase 19 an extension point for production handler/spec pairs. [VERIFIED: codebase grep]

**Test handler/spec behavior:** the spec accepts exactly `{"message": "hello"}` and rejects `{"message": "goodbye"}` before session/transaction entry; the handler reports progress, writes one log, and returns a JSON-safe mapping so linked GET endpoints prove terminal execution. [ASSUMED]

### Anti-Patterns to Avoid

- **Route calls `submit_job()` directly after a key lookup:** Its own `session_scope` commits independently, so key reservation and Job creation are not one atomic unit. [VERIFIED: codebase grep]
- **Key-only uniqueness without endpoint scope:** Violates D-07 because the same client key must work independently on submission and cancellation. [VERIFIED: codebase grep]
- **Unregistered or unvalidated type becomes a queued Job:** The runner currently turns an unknown type into `FAILED`; Phase 18 must resolve the handler and its `JobSubmissionSpec`, then run type-specific validation before opening the persistence transaction. [VERIFIED: codebase grep]
- **Use `request_cancellation()` blindly for already-CANCELLED Jobs:** It raises `JobNotCancellableError`, while D-14 requires a successful current reference for repeat cancellation. The orchestration service must read/branch before calling it for that allowed repeat case. [VERIFIED: codebase grep]
- **Overwrite cancellation requester/reason on a fresh-key repeat:** The Phase-17 running-repeat branch intentionally preserves the original fields; retain that behavior for all D-14 repeats. [VERIFIED: codebase grep]
- **Put production handler literals in default registry:** It breaks the Phase-18/19 handoff. [VERIFIED: codebase grep]
- **Leave manual operation parser/DISPATCH entries live:** They retain a bypass around HTTP Job submission. [VERIFIED: codebase grep]

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---|---|---|---|
| Job lifecycle/claim/lease/runner | Another queue, thread executor, or custom status setter | Existing `jobs.lifecycle`, `jobs.queue`, and `jobs.runner` | Phase 17 already has the closed state machine, `SKIP LOCKED` claim path, cancellation sweep, and restart proof. [VERIFIED: codebase grep] |
| Cancellation state transition | Route-local status mutation | `jobs.cancellation.request_cancellation` | It row-locks queued jobs against claims, preserves first-request facts, and implements cooperative running cancellation. [VERIFIED: codebase grep] |
| Job observation payloads | Embedded progress/log/event snapshots in mutation response | Existing `/progress`, `/logs`, `/events` GET endpoints | D-18 requires link-driven observation and existing routes already serialize these resources. [VERIFIED: codebase grep] |
| Concurrency idempotency | Python lock/cache or best-effort preflight lookup | PostgreSQL named unique constraint plus transactional conflict handling | Only the database can coordinate concurrent request processes durably. [CITED: https://docs.sqlalchemy.org/21/dialects/postgresql.html] |
| Canonical payload identity | `str(dict)`/insertion-order-dependent serialization | Explicit normalized JSON serialization plus cryptographic digest | Equivalent payloads need deterministic identity across request ordering/processes. [ASSUMED] |
| API validation/response plumbing | Custom WSGI parsing | FastAPI request models, `Header`, `Response`, and typed `HTTPException` mapping | Existing API stack and official APIs already provide parsing/status/header controls. [CITED: https://fastapi.tiangolo.com/tutorial/header-params/] [CITED: https://fastapi.tiangolo.com/advanced/response-change-status-code/] |

**Key insight:** Phase 18 is a composition boundary. Its durable value comes from transactionally composing the existing Job framework with HTTP idempotency, not from adding a second execution framework. [VERIFIED: codebase grep]

## Common Pitfalls

### Pitfall 1: A check-then-create race produces two Jobs

**What goes wrong:** Two same-key submissions both find no idempotency row and each create a queued Job. [ASSUMED]

**Why it happens:** A service does `SELECT` then invokes the current independent-transaction `submit_job()` without a database unique conflict target. [VERIFIED: codebase grep]

**How to avoid:** Add `UNIQUE(endpoint_id, idempotency_key)`, reserve/read the record inside the same transaction as Job insertion, and test two independent sessions/threads submitting the same operation. [CITED: https://docs.sqlalchemy.org/21/dialects/postgresql.html]

**Warning signs:** Two `jobs` rows with the same endpoint/key relationship, two `submitted` events, or a worker executes the handler twice. [ASSUMED]

### Pitfall 2: `Idempotency-Key` missing maps to FastAPI's default 422

**What goes wrong:** Declaring a non-optional `Header()` gives framework validation behavior instead of the locked typed `400`. [CITED: https://fastapi.tiangolo.com/tutorial/header-params/]

**How to avoid:** Accept optional header input, explicitly raise a typed missing-key exception before mutation, and assert `400`, stable error code, zero Job rows, and zero idempotency rows. [ASSUMED]

**Warning signs:** An OpenAPI/default validation payload or status `422` for a missing header. [ASSUMED]

### Pitfall 3: Repeated cancellation incorrectly returns 409 after CANCELLED

**What goes wrong:** The service delegates terminal Jobs to `request_cancellation`, which raises `JobNotCancellableError` for `CANCELLED`, but D-14 requires a successful reference for that specific terminal status. [VERIFIED: codebase grep]

**How to avoid:** Distinguish `CANCELLED` (return current reference; preserve audit fields) from `SUCCEEDED`/`FAILED` (fresh request gets typed `409` with current status), and test both exact replay and fresh-key repeat. [VERIFIED: codebase grep]

**Warning signs:** Existing CANCELLED Job has a changed `cancellation_requested_by`, `cancellation_reason`, or timestamp after a second request. [VERIFIED: codebase grep]

### Pitfall 4: Invalid cancellation reason creates audit state

**What goes wrong:** A >500-character or blank-untrimmed reason is passed into the cancellation function before normalization/validation. [ASSUMED]

**How to avoid:** Normalize reason once before fingerprinting and before any transaction write: trim, convert blank to `None`, reject length >500; test no new event/request fields/key record on rejection. [ASSUMED]

**Warning signs:** Whitespace-only reason persists as whitespace, or a rejected request still has an idempotency row. [ASSUMED]

### Pitfall 5: Structural tests only inspect routes, not parser and dispatch

**What goes wrong:** A direct operation command stays callable via `worker/parser.py` or `commands.DISPATCH` even if a route exists. [VERIFIED: codebase grep]

**How to avoid:** AST-parse both parser and dispatch; assert the removed operation command names/functions are absent, retained `run-jobs` remains, routes import no domain-operation services, and the orchestration service imports no FastAPI/worker adapter module. [VERIFIED: codebase grep]

**Warning signs:** `python -m trading_platform.worker <operation>` still invokes `run_backtest`, `run_risk_evaluation`, paper execution, reconciliation, ingestion, or broker sync services directly. [VERIFIED: codebase grep]

### Pitfall 6: Startup remains DB-optional while mutation endpoints need persistence

**What goes wrong:** `api.app.lifespan` intentionally calls `enforce_startup_config(..., require_database=False)` because Phase 17 routes were read-only; mutation requests then fail only when first used. [VERIFIED: codebase grep]

**How to avoid:** API lifespan must call `enforce_startup_config(..., require_database=True)` and fail before setting bootstrapped state, constructing the default registry, or serving routes. No read-only partial API boot remains supported in Phase 18 (Plan 18-06 Task 1). [VERIFIED: codebase grep]

**Warning signs:** A deployment reports API healthy while `POST /api/v1/jobs` cannot persist. [ASSUMED]

## Code Examples

### Canonical fingerprint boundary

```python
# Source: stdlib approach; exact helper/API is Phase-18 discretion [ASSUMED]
def canonical_fingerprint(*, endpoint: str, operation: dict[str, object]) -> str:
    canonical_json = json.dumps(
        {"endpoint": endpoint, "operation": operation},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
```

Use the normalized `reason` value and validated payload object as `operation` inputs; never include volatile request metadata such as host, client IP, or timestamp. [ASSUMED]

### Postgres conflict target

```python
# Source: SQLAlchemy PostgreSQL dialect documentation
from sqlalchemy.dialects.postgresql import insert

reservation = insert(JobMutation).values(
    endpoint_id=endpoint_id,
    idempotency_key=idempotency_key,
    fingerprint=fingerprint,
    job_id=job_id,
).on_conflict_do_nothing(constraint="uq_job_mutations_endpoint_key")
```

A no-row-insert outcome must be followed by a read of the original record and fingerprint comparison in the same service-level transaction flow. [CITED: https://docs.sqlalchemy.org/21/dialects/postgresql.html]

### Cancellation service outcome mapping

```python
# Source: Phase-17 cancellation API in jobs/cancellation.py [VERIFIED: codebase grep]
if job.status is JobStatus.CANCELLED:
    return current_reference(job), False  # allowed D-14 repeat; no framework call
if job.status in (JobStatus.SUCCEEDED, JobStatus.FAILED):
    raise JobTerminalConflict(job_id=job.id, status=job.status)

request_cancellation(
    job_id=job.id,
    requested_by=LOCAL_OPERATOR,
    reason=normalized_reason,
    settings=settings,
)
return current_reference(reload_job(job.id)), True
```

The production implementation must preserve atomic idempotency-key behavior around this branch; the snippet shows only the Phase-17 status-semantic split. [ASSUMED]

## State of the Art

| Old approach in repository | Required Phase-18 approach | Impact |
|---|---|---|
| `/api/v1/jobs` router has only GET/HEAD routes and a test asserts no mutating verbs. | Add generic POST submission and POST cancellation while retaining all existing GET observation contracts. | Update/replace the old read-only scope-fence test with explicit route-method and behavior assertions. [VERIFIED: codebase grep] |
| Direct CLI command modules call domain services (`run_backtest`, `run_risk_evaluation`, paper execution, reconciliation, ingestion) directly. | Remove direct CLI registrations for the named Phase-19 operations; worker executes only persisted Jobs through `run-jobs`. | Prevents a bypass around HTTP submission/idempotency. [VERIFIED: codebase grep] |
| `build_default_registry()` returns an empty registry; unknown types fail only if a worker later runs a Job. | Service resolves the registered handler and registry-adjacent `JobSubmissionSpec`, validates payload before transaction entry, and keeps the production default registry empty in Phase 18. | Unsupported or type-invalid submissions receive typed `422` with no Job/mutation/event persistence. [VERIFIED: codebase grep] |
| API startup deliberately skips DB preflight because it is read-only. | API lifespan requires `enforce_startup_config(..., require_database=True)`. | The mutation surface never serves through a read-only partial boot without database readiness. [VERIFIED: codebase grep] |

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|---|---|---|
| A1 | A SHA-256 digest over sorted compact JSON with `allow_nan=False` is the selected canonical-fingerprint implementation. | Supporting / Code Examples | Equivalent requests may not replay consistently if project policy needs a different JSON normalization contract. |
| A2 | An idempotency record should be a separate `JobMutation`-style table rather than columns on `jobs`. | Architecture Patterns | A different persistence layout can still satisfy the lock, but plan tasks/migration names would change. |
| A3 | FastAPI should parse an optional header and service-map absence to 400 to meet D-06. | Pattern 3 | Error-body/OpenAPI behavior may need a project-specific exception handler. |
| A4 | The test-only handler should write progress/log output as part of E2E evidence. | Pattern 4 | A handler that returns only a result still proves execution but gives weaker linked-observation coverage. |

## Open Questions (RESOLVED)

1. **RESOLVED — exact CLI whitelist and denylist.** Per D-02/D-03, Plan 18-05 Task 1 retains the parser whitelist `serve`, `run-jobs`, `report-backtest`, `report-strategy-analytics`, and `operator-status`; `DISPATCH` contains the same set except the `serve` special case. It removes `dry-run`, `backtest`, `evaluate-risk`, `submit-paper-orders`, `run-paper-session`, `sync-paper-state`, `reconcile-paper-execution`, `operator-control`, `ingest-bars`, `sync-metadata`, and `sync-sessions` from parser, dispatch, and `__main__` reachability. This is an exact closed policy, not a category-based implementation choice.

2. **RESOLVED — replay identity and status semantics.** Per D-09/D-16/D-19, replay returns the same Job identity and exact compact schema, rebuilt with the Job's point-in-time current `status`; no serialized response snapshot is persisted. Byte-equal response bodies are asserted only when replay occurs before worker transition (Plan 18-06 Task 2). Current-status replay after a transition is asserted as same `job_id`, `job_type`, and links rather than stale body equality (Plan 18-03 Task 2).

3. **RESOLVED — registry-adjacent type-specific payload validation.** Plan 18-03 Task 1 adds transport-neutral `JobSubmissionSpec` adjacent to `JobRegistry`, with `job_type: str` and `validate_payload(payload: Mapping[str, Any]) -> Mapping[str, Any]`; invalid input raises typed `InvalidJobPayloadError(job_type, reason)`. `JobOrchestrationService.submit` first resolves the handler, then resolves and invokes its spec before entering `session_scope` or writing Job, JobMutation, JobEvent, or request facts. The validated/normalized mapping is the payload fingerprinted and persisted. FastAPI/Pydantic types are forbidden from this contract, and the frozen Phase-17 `JobHandler` protocol remains unchanged. Plan 18-04 Task 2 maps invalid payload to HTTP `422` with `detail.code == "invalid_job_payload"` and zero-row assertions; Plan 18-06 Task 2 registers the test handler/spec pair. Phase 19 registers each production handler with its production `JobSubmissionSpec` without changing the handler protocol.

4. **RESOLVED — database-ready API startup.** Plan 18-06 Task 1 requires API lifespan to call `enforce_startup_config(mode=ExecutionMode.BACKTEST, require_database=True)` before bootstrapped state, default-registry construction, or route serving. PostgreSQL startup failure aborts the application; there is no read-only partial API boot for the Phase-18 mutation-capable process.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|---|---|---:|---|---|
| PostgreSQL | Migration, transactional idempotency, Phase-17 Job persistence/tests | ✓ | 14.18, local socket `/tmp:5432` accepts connections. [VERIFIED: codebase grep] | None for the required real-DB behavior. |
| Python runtime | Application and tests | ✓ | system Python 3.14.6; repository requires Python `>=3.12`. [VERIFIED: codebase grep] | Use a repaired project virtual environment. |
| Project `.venv` test tools | Focused/full test commands | ✗ | Wrapper scripts point at a nonexistent path containing `Trading Bot Project`; `pytest`/`mypy` cannot launch. [VERIFIED: codebase grep] | Recreate `.venv` from `pyproject.toml` before execution. |
| Ruff | Static checks | ✓ | 0.15.21 shell command. [VERIFIED: codebase grep] | — |
| Alembic | Migration fixture | Unknown | Not probed separately because its project-installed wrapper is unavailable with the broken `.venv`. [ASSUMED] | Recreate `.venv`, then run the existing migration fixture. |

**Missing dependencies with no fallback:**
- A working project virtual environment is needed to run the repository's pinned pytest/mypy/Alembic tooling. [VERIFIED: codebase grep]

**Missing dependencies with fallback:**
- None. [VERIFIED: codebase grep]

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---|---|---|
| V2 Authentication | No Phase-18 auth implementation | Authentication/RBAC is explicitly out of scope; do not introduce it incidentally. [VERIFIED: codebase grep] |
| V3 Session Management | No | No session/auth mechanism is in Phase 18 scope. [VERIFIED: codebase grep] |
| V4 Access Control | No new authorization policy | Preserve the single-local-operator scope; do not treat `requested_by` as authentication. [ASSUMED] |
| V5 Input Validation | Yes | Pydantic/FastAPI validates the transport envelope; `JobSubmissionSpec` performs registered type-specific payload validation before transaction entry; the service also enforces nonblank idempotency key, known `job_type`, JSON-safe normalized payload, UUID path, and 500-character trimmed cancellation reason. [CITED: https://fastapi.tiangolo.com/tutorial/header-params/] |
| V6 Cryptography | Yes, limited | Use a standard-library cryptographic digest if fingerprints are stored; do not implement a custom hash. [ASSUMED] |

### Known Threat Patterns for this stack

| Pattern | STRIDE | Standard Mitigation |
|---|---|---|
| Duplicate request races execute a financial operation twice | Tampering | Transactional endpoint/key unique constraint, fingerprint mismatch conflict, and concurrent integration test. [CITED: https://docs.sqlalchemy.org/21/dialects/postgresql.html] |
| Same key reused for a different payload | Tampering | Persist fingerprint; return typed `409` with stable code and original Job ID without mutation. |
| Header omitted, handler payload invalid, or reason malformed | Tampering | Resolve and invoke the transport-neutral submission spec before session/transaction entry; validate all other inputs before idempotency/Job/cancellation writes; assert zero-write rejection. [ASSUMED] |
| Replay links use attacker-controlled Host/proxy information | Spoofing | Construct fixed relative `/api/v1/jobs/{id}` paths only. |
| Cancellation audit facts overwritten | Repudiation | Route through `request_cancellation` for eligible states and branch D-14 repeats without modifying first-request facts. [VERIFIED: codebase grep] |

## Sources

### Primary (HIGH confidence)
- [FastAPI Header Parameters](https://fastapi.tiangolo.com/tutorial/header-params/) - `Header` parameter behavior and underscore-to-hyphen mapping.
- [FastAPI change response status code](https://fastapi.tiangolo.com/advanced/response-change-status-code/) - dynamic `Response.status_code` and response headers.
- [SQLAlchemy PostgreSQL dialect](https://docs.sqlalchemy.org/21/dialects/postgresql.html) - named constraint targets for PostgreSQL `ON CONFLICT`.
- `src/trading_platform/jobs/cancellation.py` - required cancellation function and terminal/pending behavior. [VERIFIED: codebase grep]
- `src/trading_platform/jobs/dependencies.py` - current self-committing submission primitive. [VERIFIED: codebase grep]
- `src/trading_platform/jobs/registry.py`, `runner.py`, and `api/routes/jobs.py` - empty default registry, worker execution, and existing read paths. [VERIFIED: codebase grep]
- `src/trading_platform/worker/parser.py`, `worker/commands/__init__.py`, and command modules - direct manual CLI command surface. [VERIFIED: codebase grep]
- `tests/test_job_api.py`, `tests/test_job_runner.py`, and `tests/test_job_import_boundary.py` - real-PostgreSQL fixture and AST-boundary precedents. [VERIFIED: codebase grep]

### Secondary (MEDIUM confidence)
- `pyproject.toml` and `alembic/versions/0018_phase17_job_framework.py` - pinned stack and migration conventions. [VERIFIED: codebase grep]

### Tertiary (LOW confidence)
- None beyond items recorded in the Assumptions Log.

## Metadata

**Confidence breakdown:**
- Standard stack: **HIGH** - Uses repository-pinned FastAPI/SQLAlchemy/Alembic and official FastAPI/SQLAlchemy documentation. [CITED: https://fastapi.tiangolo.com/tutorial/header-params/] [CITED: https://docs.sqlalchemy.org/21/dialects/postgresql.html]
- Architecture: **HIGH** - Existing Phase-17 code and locked phase decisions identify exact extension points and the required atomicity gap. [VERIFIED: codebase grep]
- Pitfalls: **HIGH** - The transaction boundary, cancellation exceptions, read-only router fence, CLI bypasses, and DB-optional boot behavior are all present in source. [VERIFIED: codebase grep]

**Research date:** 2026-07-21
**Valid until:** 2026-08-20
