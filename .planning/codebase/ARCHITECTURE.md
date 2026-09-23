<!-- refreshed: 2026-09-23 -->
# Architecture

**Analysis Date:** 2026-09-23

## System Overview

```text
┌──────────────────────────────────────────────────────────────────────────┐
│                          Operator Console                                 │
│                     Next.js Frontend (React 19)                           │
│                  `console/src/app`, `console/src/components`              │
│                                                                            │
│                  - Status monitoring dashboard                            │
│                  - Run analytics and reporting                            │
│                  - Strategy & paper trading views                         │
│                  - Health and system readiness checks                     │
└─────────────────────────────────────────────────────────┬─────────────────┘
                                                           │ HTTP proxies to
                                                           │ /api/v1 endpoints
                                                           ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                        FastAPI Control Plane                              │
│              `src/trading_platform/api/app.py`                            │
│                                                                            │
│  ┌─────────────────────────────────────────────────────────────────┐     │
│  │  API Routes (Routers)                                           │     │
│  ├──────────────┬──────────────┬─────────────┬─────────────────────┤     │
│  │   Health     │   Jobs       │  Analytics  │  Operations         │     │
│  │  `health.py` │  `jobs.py`   │ `analytics` │  `operations.py`    │     │
│  ├──────────────┼──────────────┼─────────────┼─────────────────────┤     │
│  │  Strategies  │   System     │   Runs      │                     │     │
│  │ `strategies` │  `system.py` │  `runs.py`  │                     │     │
│  └──────────────┴──────────────┴─────────────┴─────────────────────┘     │
│                                                                            │
│  ┌─────────────────────────────────────────────────────────────────┐     │
│  │  Job Orchestration Service                                      │     │
│  │  `src/trading_platform/orchestration/job_mutations.py`          │     │
│  │  - Idempotent job submission                                    │     │
│  │  - Cancellation coordination                                    │     │
│  │  - Mutation result transport                                    │     │
│  └─────────────────────────────────────────────────────────────────┘     │
└────────────────────────┬─────────────────────────────────────────────────┘
                         │ (Read filters, job queries)
                         │
        ┌────────────────┼────────────────┐
        ▼                ▼                ▼
┌──────────────────┐ ┌──────────────┐ ┌──────────────────────┐
│  Job Registry    │ │ Job Manifest │ │ Job Status / Log     │
│  `registry.py`   │ │  `contracts` │ │  Job Query Service   │
│                  │ │              │ │  `job_reads.py`      │
│ - Register       │ │ - Payload    │ │                      │
│   handlers       │ │   validation │ │ - Filter / search    │
│ - Resolve        │ │ - Submission │ │ - Log retrieval      │
│   by type        │ │   specs      │ │ - Status tracking    │
└──────────────────┘ └──────────────┘ └──────────────────────┘
        │                                       │
        └───────────────┬───────────────────────┘
                        │
                        ▼
        ┌───────────────────────────────────────┐
        │    PostgreSQL (Event Store)           │
        │    - jobs table                       │
        │    - job_events table                 │
        │    - job_logs table                   │
        │    - job_mutations table (idempotency)│
        │    - domain tables (runs, strategies) │
        └───────────────────────────────────────┘
```

## Component Responsibilities

| Component | Responsibility | File |
|-----------|----------------|------|
| **API App** | FastAPI bootstrap, lifespan, route registration | `src/trading_platform/api/app.py` |
| **Health Router** | Liveness probes and readiness checks | `src/trading_platform/api/routes/health.py` |
| **Jobs Router** | Job submission, cancellation, observation endpoints | `src/trading_platform/api/routes/jobs.py` |
| **Operations Router** | System control endpoints (kill switch, operator controls) | `src/trading_platform/api/routes/operations.py` |
| **Job Orchestration Service** | Idempotent submission, cancellation, result transport | `src/trading_platform/orchestration/job_mutations.py` |
| **Job Registry** | In-memory registry for handler resolution | `src/trading_platform/jobs/registry.py` |
| **Job Read Service** | Query filters, status retrieval, log pagination | `src/trading_platform/services/job_reads.py` |
| **Database Models** | SQLAlchemy ORM for all domain entities | `src/trading_platform/db/models/` |
| **Settings** | Typed configuration from YAML and environment | `src/trading_platform/core/settings.py` |
| **Console Frontend** | React/Next.js operator UI with dashboard | `console/src/app/`, `console/src/components/` |

## Pattern Overview

**Overall:** Layered REST API with job orchestration framework at the core. The system separates read operations (observation) from write operations (mutations) into distinct services with explicit contracts.

**Key Characteristics:**
- **Job-centric architecture**: All long-running work flows through a generic Job framework with typed handlers
- **Idempotent mutations**: Every state change (submit, cancel) uses deterministic idempotency keys to ensure safe retries
- **Explicit contracts**: Job handlers validate payloads before state changes; registry prevents unknown types
- **Typed configuration**: YAML + environment overrides via Pydantic for runtime settings
- **PostgreSQL event sourcing**: All domain state changes persist to immutable event log
- **Single-user focus**: Current scope is local-first, single-operator system

## Layers

**API Layer:**
- Purpose: Accept client requests, validate inputs, coordinate responses
- Location: `src/trading_platform/api/`
- Contains: FastAPI app, route handlers, endpoint definitions
- Depends on: Settings, orchestration services, read services
- Used by: Frontend console, CLI/scripts, external API consumers

**Orchestration Layer:**
- Purpose: Implement idempotent state mutations, job submission, and cancellation
- Location: `src/trading_platform/orchestration/job_mutations.py`, `src/trading_platform/jobs/`
- Contains: Job registry, handler contracts, submission logic, dependency resolution
- Depends on: Database session, settings, job lifecycle management
- Used by: API routes, worker processes

**Domain/Service Layer:**
- Purpose: Encapsulate business logic for jobs, analytics, operator controls
- Location: `src/trading_platform/services/`
- Contains: Job reads, analytics, risk evaluation, execution, reconciliation, operator status
- Depends on: Database models, external APIs (Alpaca, Polygon)
- Used by: API routes, orchestration layer

**Persistence Layer:**
- Purpose: Define ORM models and manage database sessions
- Location: `src/trading_platform/db/`
- Contains: SQLAlchemy models, session factory, base classes
- Depends on: PostgreSQL database
- Used by: All services and orchestration logic

**Configuration Layer:**
- Purpose: Load and resolve typed settings from multiple sources
- Location: `src/trading_platform/core/settings.py`
- Contains: Pydantic settings classes, YAML loading, environment override logic
- Depends on: File system, environment variables
- Used by: App bootstrap, service initialization

## Data Flow

### Primary Request Path (Job Submission)

1. **Client submits job** → POST `/api/v1/jobs` with job type and payload (`src/trading_platform/api/routes/jobs.py:submit_job()`)
2. **Endpoint validates** → Check idempotency key format, normalize job type (`SubmitJobRequest` validator)
3. **Orchestration service** → Call `JobOrchestrationService.submit()` with idempotency key (`src/trading_platform/orchestration/job_mutations.py:JobOrchestrationService.submit()`)
4. **Registry validation** → Resolve handler; validate payload via submission spec (`src/trading_platform/jobs/registry.py:resolve()`)
5. **Job creation** → Create Job record in QUEUED status, persist to PostgreSQL
6. **Idempotency tracking** → Store mutation in `job_mutations` table with idempotency key hash
7. **Response** → Return `JobReference` with job_id, type, status, and self-links

### Job Observation Path

1. **Client queries job** → GET `/api/v1/jobs/{job_id}` or filtered list
2. **Read service** → `JobReadService.fetch()` applies filters, hydrates from database
3. **Database query** → SELECT from jobs table with status/type/time indexes
4. **Response** → Return job metadata: status, timestamps, progress, result summary

### Job Cancellation Path

1. **Client requests cancellation** → POST `/api/v1/jobs/{job_id}/cancel` with reason
2. **Endpoint validation** → Check reason length, format idempotency key
3. **Orchestration service** → Call `JobOrchestrationService.cancel()` with idempotency
4. **State validation** → Verify job exists and is cancellable (not already terminal)
5. **Cancellation request** → Set `cancellation_requested_at`, `cancellation_requested_by`, `reason`
6. **Worker signal** → Worker polls job state, sees cancellation flag, transitions to CANCELLED
7. **Acknowledgment** → Set `cancellation_acknowledged_at` and `cancellation_cause`

**State Management:**
- **Job lifecycle**: QUEUED → RUNNING → {SUCCEEDED|FAILED|CANCELLED}
- **Blocking jobs**: Job can block on another job; if blocker fails, blocked job is cancelled with `DEPENDENCY_FAILED`
- **Progress tracking**: Job can report `progress_percent`, `progress_step`, `progress_current/total`
- **Failure tracking**: Terminal FAILED jobs record `failure_reason` and `failure_message`

## Key Abstractions

**Job:**
- Purpose: Generic unit of orchestrated work with typed handler resolution
- Examples: `src/trading_platform/db/models/job.py`
- Pattern: Enum-based status machine (5 states), progress tracking, failure reasons, blocking relationships

**JobHandler (Protocol):**
- Purpose: Contract for job execution logic
- Examples: Handler modules in `src/trading_platform/jobs/`
- Pattern: Implement `execute(context) -> result` with proper error propagation and heartbeat

**JobRegistry:**
- Purpose: In-memory handler resolution by job_type
- Examples: `src/trading_platform/jobs/registry.py`
- Pattern: Typed register/resolve with validation, prevents duplicates, optional submission specs

**Configuration Tree:**
- Purpose: Immutable typed settings from YAML + environment
- Examples: `AppSettings`, `ApiSettings`, `DatabaseSettings`, `StrategySettings`
- Pattern: Pydantic `BaseSettings` with environment prefix, nested dot-notation access

**Strategy:**
- Purpose: Named, versioned algorithm with configuration contract
- Examples: `src/trading_platform/strategies/base.py`, `src/trading_platform/strategies/trend_following_daily/`
- Pattern: Registry-based lookup, config-driven parameters, signal generation

## Entry Points

**API Server:**
- Location: `src/trading_platform/api/app.py:main()`
- Triggers: `trading-platform-api` command, Uvicorn startup
- Responsibilities: Bootstrap FastAPI with lifespan, load settings, mount routes, configure logging

**Worker CLI:**
- Location: `src/trading_platform/worker/__main__.py:main()`
- Triggers: `trading-platform-worker serve` or `trading-platform-worker {command}`
- Responsibilities: Parse command-line arguments, dispatch to handler (currently serve/dry-run/etc)

**Database Migrations:**
- Location: `alembic/` with entrypoint via `scripts/migrate.py`
- Triggers: `PYTHONPATH=src python scripts/migrate.py upgrade head`
- Responsibilities: Apply schema changes using Alembic

## Architectural Constraints

- **Threading:** FastAPI runs on async event loop (Uvicorn); database queries use sync SQLAlchemy with thread pooling
- **Global state:** `app.state` holds job_registry and settings during lifespan; must not be mutated after bootstrap
- **Circular imports:** Import services in route handlers via Depends() dependency injection; avoid top-level imports in api/routes/
- **Idempotency:** Every mutation API requires `Idempotency-Key` header; same key + endpoint = same result (replay detected)
- **Leasing:** Job workers use optimistic locking (lease_owner, lease_expires_at) to prevent concurrent execution
- **Single-user scope:** All operations assume one operator; no multi-user authorization layer

## Anti-Patterns

### Circular Service Dependencies

**What happens:** Service A imports Service B which imports Service A.
**Why it's wrong:** Python module initialization order becomes fragile; hard to test in isolation; refactoring becomes brittle.
**Do this instead:** Use dependency injection via FastAPI `Depends()` to inject services into route handlers. Define service interfaces (Protocols) in a contracts module and import those instead. Avoid importing concrete service classes at module level. Example: `src/trading_platform/api/dependencies.py` constructs services and injects them, not `src/trading_platform/api/routes/jobs.py` importing directly.

### God Services

**What happens:** A single service class grows to handle jobs, analytics, risk, and execution — one file with 1000+ lines.
**Why it's wrong:** Violates single responsibility; makes testing harder; increases cognitive load; harder to reuse pieces.
**Do this instead:** Separate concerns by domain (JobReadService handles queries, JobOrchestrationService handles mutations). Create thin orchestration services that compose smaller, focused services. Example: `src/trading_platform/services/job_reads.py` handles only queries; `src/trading_platform/orchestration/job_mutations.py` handles only mutations.

### Silent Failures

**What happens:** A service catches an exception, logs it, and returns a default value (e.g., empty list) instead of surfacing the error.
**Why it's wrong:** Operator doesn't know what failed; silent data loss; hard to debug in production.
**Do this instead:** Let exceptions propagate to the API layer where they're explicitly mapped to HTTP error codes with details. Use typed exception classes (subclass ValueError, KeyError, etc.) in orchestration and service layers. Example: `src/trading_platform/orchestration/job_mutations.py` raises `UnknownJobTypeForSubmissionError`, which the route handler catches and converts to 422.

## Error Handling

**Strategy:** Exceptions are raised in service and orchestration layers with specific exception types (dataclass-based ValueError subclasses). The API layer catches these and converts them to HTTP status codes with error details.

**Patterns:**
- **Type mismatch:** Raise `UnknownJobTypeError` (KeyError) from registry lookup failure
- **Validation failure:** Raise `InvalidJobPayloadError` (ValueError) when job payload doesn't match spec
- **State machine violation:** Raise `JobTerminalConflictError` when trying to cancel a terminal job
- **Idempotency conflict:** Raise `IdempotencyConflictError` when same key used with different payload
- **Missing target:** Raise `JobMutationNotFoundError` (LookupError) when job ID not found

## Cross-Cutting Concerns

**Logging:** Structured JSON logging configured via `src/trading_platform/core/logging.py`. Every API route logs request entry/exit with context (job_id, user, environment). Worker logs job execution progress via periodic updates.

**Validation:** Payload validation happens twice: (1) Pydantic models at API boundary, (2) JobSubmissionSpec in registry before persistence. Database-level constraints enforce job_progress_percent [0-100] and enum values.

**Authentication:** Not yet implemented. Current scope is single-user local system. Future expansion would add operator identity and audit trail.

---

*Architecture analysis: 2026-09-23*
