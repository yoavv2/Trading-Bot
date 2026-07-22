# Phase 18: Orchestration Surface - Pattern Map

**Mapped:** 2026-07-21
**Files analyzed:** 15
**Analogs found:** 15 / 15

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---|---|---|---|---|
| `src/trading_platform/api/routes/jobs.py` | route | request-response | `src/trading_platform/api/routes/jobs.py` | role-match (extend existing router) |
| `src/trading_platform/api/dependencies.py` | provider | request-response | `src/trading_platform/api/dependencies.py` | exact |
| `src/trading_platform/api/app.py` | config | request-response | `src/trading_platform/api/app.py` | exact |
| `src/trading_platform/db/models/job_mutation.py` | model | CRUD | `src/trading_platform/db/models/paper_order.py` | role-match |
| `src/trading_platform/db/models/__init__.py` | config | transform | `src/trading_platform/db/models/__init__.py` | exact |
| `src/trading_platform/jobs/dependencies.py` | utility | CRUD | `src/trading_platform/jobs/dependencies.py` | exact (extract session-owned insert) |
| `src/trading_platform/services/job_orchestration.py` | service | request-response | `jobs/dependencies.py`, `jobs/cancellation.py`, `services/execution/idempotency.py` | composite / partial |
| `src/trading_platform/worker/parser.py` | config | request-response | `src/trading_platform/worker/parser.py` | exact |
| `src/trading_platform/worker/commands/__init__.py` | config | request-response | `src/trading_platform/worker/commands/__init__.py` | exact |
| `alembic/versions/0019_phase18_job_idempotency.py` | migration | CRUD | `alembic/versions/0018_phase17_job_framework.py` | exact |
| `tests/test_job_orchestration.py` | test | CRUD | `tests/test_job_cancellation.py`, `tests/test_job_runner.py` | role-match |
| `tests/test_job_mutation_api.py` | test | request-response | `tests/test_job_api.py` | role-match |
| `tests/test_orchestration_boundaries.py` | test | transform | `tests/test_job_import_boundary.py` | exact |
| `tests/test_job_api.py` | test | request-response | `tests/test_job_api.py` | exact (replace read-only fence) |
| `tests/test_app_boot.py` | test | request-response | `tests/test_app_boot.py` | exact |

## Pattern Assignments

### `src/trading_platform/api/routes/jobs.py` (route, request-response)

**Analog:** `src/trading_platform/api/routes/jobs.py`

**Imports/router/dependency injection** (lines 10-35):
```python
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from trading_platform.api.dependencies import get_job_read_service
from trading_platform.services.job_reads import JobReadFilters, JobReadService

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])

@router.get("")
def list_jobs(
    filters: Annotated[JobReadFilters, Depends(get_job_read_filters)],
    job_reads: Annotated[JobReadService, Depends(get_job_read_service)],
) -> dict[str, object]:
```

**Route-to-service error translation** (lines 43-62):
```python
@router.get("/{job_id}")
def job_detail(
    job_id: UUID,
    job_reads: Annotated[JobReadService, Depends(get_job_read_service)],
) -> dict[str, object]:
    try:
        return job_reads.get_job_detail(str(job_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
```

**Apply:** Keep reads unchanged; add `POST ""` and `POST "/{job_id}/cancel"` as parsing/status/header adapters only. Use optional `Header()` then explicitly return typed 400 for no key (not FastAPI's automatic required-header 422). Catch only typed orchestration outcomes for 404/409/422; never call `submit_job` or mutate ORM rows from the route.

---

### `src/trading_platform/api/dependencies.py` (provider, request-response)

**Analog:** `src/trading_platform/api/dependencies.py`

**App-state settings and per-request service factory** (lines 45-65):
```python
def get_settings(request: Request) -> Settings:
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        raise HTTPException(status_code=503, detail="Application settings not loaded yet.")
    return settings  # type: ignore[return-value]


def get_job_read_service(request: Request) -> JobReadService:
    return JobReadService(get_settings(request))
```

**Apply:** Add a `get_job_orchestration_service(request)` factory which resolves settings and the injected/default `JobRegistry`. Preserve a test override point so the API and `run_worker_loop` can receive the same test-only registry. Do not put orchestration decisions in the dependency factory.

---

### `src/trading_platform/api/app.py` and `tests/test_app_boot.py` (config/test, request-response)

**Analogs:** `src/trading_platform/api/app.py`; `tests/test_app_boot.py`

**Lifespan startup convention** (`src/trading_platform/api/app.py`, lines 23-39):
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = enforce_startup_config(mode=ExecutionMode.BACKTEST, require_database=False)
    configure_logging(settings.logging)
    logger = get_logger("trading_platform.bootstrap")

    app.state.settings = settings
    app.state.started_at = datetime.now(UTC).isoformat()
    app.state.bootstrapped = True
```

**Boot test lifecycle pattern** (`tests/test_app_boot.py`, lines 124-145):
```python
clear_settings_cache()
app = create_app()
registered_paths = {route.path for route in app.routes}

with TestClient(app) as client:
    health = client.get("/health")
    ready = client.get("/ready")

assert health.status_code == 200
assert ready.status_code == 200
assert ready.json()["checks"]["database"]["status"] == "skipped"
```

**Apply:** Intentionally replace the read-only `require_database=False` policy with the locked mutation-ready behavior and update the existing DB-optional boot assertion rather than silently retaining it. Retain state initialization, logging, and router registration order.

---

### `src/trading_platform/db/models/job_mutation.py` and `src/trading_platform/db/models/__init__.py` (model/config, CRUD/transform)

**Analogs:** `src/trading_platform/db/models/paper_order.py`; `src/trading_platform/db/models/__init__.py`

**ORM typed columns and named database uniqueness** (`src/trading_platform/db/models/paper_order.py`, lines 37-80):
```python
class PaperOrder(TimestampedModel, Base):
    __tablename__ = "paper_orders"
    __table_args__ = (
        UniqueConstraint("source_risk_event_id", name="uq_paper_orders_source_risk_event_id"),
        UniqueConstraint("intent_hash", name="uq_paper_orders_intent_hash"),
        Index("ix_paper_orders_strategy_run_id_status", "strategy_run_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    strategy_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("strategy_runs.id", ondelete="CASCADE"), nullable=False
    )
    intent_hash: Mapped[str] = mapped_column(String(64), nullable=False)
```

**Central model export convention** (`src/trading_platform/db/models/__init__.py`, lines 3-13 and 35-51):
```python
from trading_platform.db.models.job import Job, JobCancellationCause, JobFailureReason, JobStatus
from trading_platform.db.models.job_dependency import JobDependency

__all__ = [
    "Job",
    "JobCancellationCause",
    "JobDependency",
    "JobFailureReason",
    "JobStatus",
]
```

**Apply:** Model a mutation/idempotency record with a UUID key, `endpoint_id`, bounded `idempotency_key`, 64-character fingerprint, and non-null `job_id` foreign key. Name `UniqueConstraint("endpoint_id", "idempotency_key", name="uq_job_mutations_endpoint_key")` explicitly; it is the concurrency backstop. Export the model from the package initializer so Alembic metadata imports it.

---

### `src/trading_platform/jobs/dependencies.py` (utility, CRUD)

**Analog:** `src/trading_platform/jobs/dependencies.py`

**Current submission transaction to extract** (lines 174-237):
```python
def submit_job(*, job_type: str, payload: Mapping[str, Any],
               depends_on: Sequence[uuid.UUID] = (),
               settings: Settings | DatabaseSettings | None = None) -> uuid.UUID:
    unique_depends_on = list(dict.fromkeys(depends_on))
    with session_scope(settings) as session:
        new_job_id = uuid.uuid4()
        validate_dependency_set(
            session, new_job_id=new_job_id, depends_on=unique_depends_on, job_type=job_type
        )
        job = Job(id=new_job_id, job_type=job_type, payload=dict(payload), status=JobStatus.QUEUED)
        session.add(job)
        for dependency_id in unique_depends_on:
            session.add(JobDependency(job_id=new_job_id, depends_on_job_id=dependency_id))
        session.add(JobEvent(
            job_id=new_job_id, from_status=None, to_status=JobStatus.QUEUED,
            event_type=JobEventType.SUBMITTED, outcome=JobTransitionOutcome.ACCEPTED,
            event_at=datetime.now(UTC),
        ))
        session.flush()
    return new_job_id
```

**Apply:** Extract this row/event creation into a `Session`-accepting helper that flushes but never commits. Retain `submit_job()` as the compatibility wrapper around that helper. The orchestration transaction must compose the idempotency reservation and this insert atomically.

---

### `src/trading_platform/services/job_orchestration.py` (service, request-response)

**Analogs:** `src/trading_platform/jobs/dependencies.py`, `src/trading_platform/jobs/cancellation.py`, `src/trading_platform/services/execution/idempotency.py`

**Canonical digest convention** (`src/trading_platform/services/execution/idempotency.py`, lines 30-64):
```python
def build_intent_hash(... ) -> str:
    material = build_material_order_identity(...)
    serialized = json.dumps(asdict(material), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
```

**Mandatory cancellation primitive and semantics** (`src/trading_platform/jobs/cancellation.py`, lines 98-113 and 162-207):
```python
def request_cancellation(*, job_id: uuid.UUID, requested_by: str,
                         reason: str | None = None, settings: Settings | DatabaseSettings | None = None
) -> CancellationResult:
    """Raises LookupError if the Job does not exist, and JobNotCancellableError if terminal."""
    ...
    if job.status is JobStatus.RUNNING:
        if job.cancellation_requested_at is not None:
            return CancellationResult(
                job_id=job_id, status=job.status, accepted=False,
                already_terminal=False, mode="cooperative",
            )
        ...
    raise JobNotCancellableError(job_id=job_id, status=job.status)
```

**Apply:** Own validation/normalization, endpoint-scoped canonical fingerprint, atomic idempotency record resolution/create, registry validation before persistence, typed outcomes, and compact relative Job-reference construction here. Normalize cancellation reason once (`strip`, blank to `None`, reject >500) before fingerprinting or writing. For cancellation, special-case `CANCELLED` before calling the primitive; map `SUCCEEDED`/`FAILED` to a typed 409 and preserve first-request facts.

**Boundary note:** `tests/test_job_import_boundary.py` currently forbids every `services/` module from importing `trading_platform.jobs` (lines 34-42). The proposed service needs the Job primitives. Planner must either place orchestration outside `services/` or narrowly revise the AST rule to distinguish orchestration infrastructure from infrastructure-independent domain services; do not weaken the domain-service boundary globally.

---

### `src/trading_platform/worker/parser.py` and `src/trading_platform/worker/commands/__init__.py` (config, request-response)

**Analogs:** same files

**Retained worker-infrastructure parser pattern** (`src/trading_platform/worker/parser.py`, lines 211-234):
```python
run_jobs_parser = subparsers.add_parser(
    "run-jobs",
    help="Run the restart-safe generic Job worker loop: claim, execute, sweep.",
)
run_jobs_parser.add_argument("--worker-id", default=None)
run_jobs_parser.add_argument("--max-jobs", type=int, default=None)
run_jobs_parser.add_argument("--once", action="store_true", default=False)
run_jobs_parser.add_argument("--compact", action="store_true", default=False)
```

**Explicit uniform dispatch mapping** (`src/trading_platform/worker/commands/__init__.py`, lines 43-60):
```python
DISPATCH: dict[str, Callable[[argparse.Namespace], None]] = {
    "backtest": run_backtest_command,
    "evaluate-risk": run_evaluate_risk_command,
    "run-jobs": run_jobs_command,
}

__all__ = ["DISPATCH", "run_dry_bootstrap", "run_placeholder_worker"]
```

**Apply:** Remove Phase-19 direct-operation registrations from both parser and `DISPATCH` together; preserve `run-jobs` and only other explicitly classified infrastructure/read-only commands. Do not delete `run-jobs` or move execution logic into a CLI command.

---

### `alembic/versions/0019_phase18_job_idempotency.py` (migration, CRUD)

**Analog:** `alembic/versions/0018_phase17_job_framework.py`

**Revision and reversible table convention** (lines 9-13, 50-119, and 229-245):
```python
revision = "0018_phase17_job_framework"
down_revision = "0017_phase11_query_perf_indices"
branch_labels = None
depends_on = None

op.create_table(
    "jobs",
    sa.Column("id", sa.UUID(), nullable=False),
    ...
    sa.PrimaryKeyConstraint("id", name=op.f("pk_jobs")),
)
...
def downgrade() -> None:
    op.drop_index("ix_jobs_job_type_status", table_name="jobs")
    op.drop_table("jobs")
```

**Apply:** Advance from revision `0018_phase17_job_framework`, create the mutation table and named endpoint/key unique constraint, use `op.f()` for generated PK/FK names, and drop constraints/indexes before the table in downgrade. This is a new empty table, so do not add a data backfill.

---

### `tests/test_job_orchestration.py` (test, CRUD)

**Analog:** `tests/test_job_runner.py`

**Test-only handler and registry injection** (lines 125-170 and 233-237):
```python
class _ProgressReportingHandler:
    job_type = "phase17_runner_progress"

    def run(self, context: JobContext) -> Mapping[str, Any]:
        context.report_progress(percent=42, step="halfway", current=4, total=10)
        return {"steps_done": 4}


def _registry(*handlers: JobHandler) -> JobRegistry:
    registry = JobRegistry()
    for handler in handlers:
        registry.register(handler)
    return registry
```

**Worker execution proof** (lines 255-274):
```python
success_registry = _registry(_SuccessHandler())
report = run_worker_loop(
    worker_id="worker-lifetime-two", registry=success_registry, max_jobs=1
)
assert report["jobs_executed"] == 1
assert _get_job(job_id).status is JobStatus.SUCCEEDED
```

**Apply:** Use a local test-only handler/registry. Cover deterministic payload fingerprinting, new/replay/mismatch paths, endpoint scoping, concurrent duplicate submission, no-write validation failures, cancellation terminal/pending semantics, and verify default registry remains empty.

---

### `tests/test_job_mutation_api.py` and `tests/test_job_api.py` (test, request-response)

**Analog:** `tests/test_job_api.py`

**Real migrated-Postgres fixture and TestClient helper** (lines 75-124):
```python
@pytest.fixture()
def migrated_job_api_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    database_name = f"job_api_{uuid.uuid4().hex[:8]}"
    ...
    _set_database_env(monkeypatch, database_name)
    clear_settings_cache()
    clear_engine_cache()
    command.upgrade(build_alembic_config(), "head")
    try:
        yield database_name
    finally:
        clear_settings_cache()
        clear_engine_cache()
        ...

def _build_client() -> TestClient:
    clear_settings_cache()
    return TestClient(create_app())
```

**Existing route-scope test to replace** (lines 535-542):
```python
def test_jobs_router_exposes_no_mutating_verbs() -> None:
    app = create_app()
    for route in app.routes:
        path = str(getattr(route, "path", ""))
        if path.startswith("/api/v1/jobs"):
            methods = set(getattr(route, "methods", set()))
            assert methods <= {"GET", "HEAD"}, (path, methods)
```

**Apply:** Reuse the isolated migrated database and client lifecycle. Replace the obsolete read-only fence with an exact route-method contract. Assert 202 new submit, 200/replay header, compact reference-only body/relative links, typed 400/404/409/422 outcomes with zero-write guarantees, and submit → `run_worker_loop` with the same injected test registry → linked GET terminal observation.

---

### `tests/test_orchestration_boundaries.py` (test, transform)

**Analog:** `tests/test_job_import_boundary.py`

**AST import-scanning pattern** (lines 19-42 and 98-119):
```python
_ROOT = Path(__file__).resolve().parents[1]
_SRC_ROOT = _ROOT / "src"
_SERVICES_DIR = _SRC_ROOT / "trading_platform" / "services"

FORBIDDEN_IMPORT_ROOTS = (
    "trading_platform.jobs",
    "trading_platform.api",
    "trading_platform.worker",
    "fastapi",
    "starlette",
)

@pytest.mark.parametrize("module_path", SERVICE_MODULES, ids=[...])
def test_domain_service_does_not_import_job_http_or_scheduling_modules(module_path: Path) -> None:
    offenders = [
        (module_name, lineno, root)
        for module_name, lineno in _collect_imported_modules(module_path)
        if (root := _forbidden_root_hit(module_name)) is not None
    ]
    assert not offenders, f"{module_path.relative_to(_ROOT)} imports forbidden module(s): ..."
```

**Apply:** AST/source-check parser and dispatch removals/retained `run-jobs`; assert API adapters do not import operation-domain services; assert the orchestration layer does not import FastAPI or worker adapters; retain JOB-04 enforcement for domain services with an explicit, narrow classification if the orchestration module is placed under `services/`.

## Shared Patterns

### Typed adapter errors
**Sources:** `src/trading_platform/api/routes/jobs.py` lines 43-62; `src/trading_platform/api/routes/analytics.py` lines 31-41.

```python
try:
    return service_call(...)
except LookupError as exc:
    raise HTTPException(status_code=404, detail=str(exc)) from exc
except ValueError as exc:
    raise HTTPException(status_code=400, detail=str(exc)) from exc
```

**Apply to:** New mutation routes only after the service exposes dedicated exception types. Use deliberate mappings for missing key (400), unknown type (422), fingerprint/terminal conflicts (409), and missing Job (404). Keep the route free of persistence and lifecycle logic.

### Transaction boundaries
**Sources:** `src/trading_platform/jobs/dependencies.py` lines 200-237; `src/trading_platform/jobs/cancellation.py` lines 120-153.

```python
with session_scope(settings) as session:
    ...
    session.flush()
```

**Apply to:** Job submission/idempotency reservation must share one service-owned transaction. Do not call the current self-committing `submit_job()` after a separate idempotency lookup. Cancellation must preserve Phase-17's locked primitive and first-request audit behavior.

### Deterministic identity
**Source:** `src/trading_platform/services/execution/idempotency.py` lines 47-64.

```python
serialized = json.dumps(asdict(material), sort_keys=True, separators=(",", ":"))
return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
```

**Apply to:** Endpoint-scoped submission/cancellation fingerprints after validation and normalization. Persist only the fixed-size digest and enforce it with the named unique endpoint/key constraint.

### Test isolation and injected registry
**Sources:** `tests/test_job_api.py` lines 75-124; `tests/test_job_runner.py` lines 125-170 and 233-237.

**Apply to:** All persistence/API/E2E tests. Use a real migrated temporary PostgreSQL database, a test-only handler, and the same registry for API submission and worker execution. Keep `build_default_registry()` empty.

## No Exact Analog Found

| File | Role | Data Flow | Reason / planner direction |
|---|---|---|---|
| `src/trading_platform/services/job_orchestration.py` | service | request-response | No existing module composes registry validation, DB-conflict idempotency, queued Job creation, cancellation, and Job-reference responses. Combine the listed primitives and resolve the current services→jobs AST boundary explicitly. |
| `tests/test_job_orchestration.py` | test | CRUD | Existing tests cover each Job primitive but not transactional endpoint-scoped idempotency/concurrent conflict handling. Use the runner fixture/handler patterns plus the Phase 18 research contract. |

## Metadata

**Analog search scope:** `src/trading_platform/api`, `db/models`, `jobs`, `services`, `worker`, `alembic/versions`, and `tests`
**Files scanned:** 18
**Pattern extraction date:** 2026-07-21
