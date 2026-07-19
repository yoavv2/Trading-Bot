# Phase 17: Job Framework - Pattern Map

**Mapped:** 2026-07-19
**Files analyzed:** 21 (inferred — no explicit file list in CONTEXT.md; derived from JOB-01..07, D-01..D-15, and the `<code_context>` Integration Points block)
**Analogs found:** 20 / 21 (1 filed under "No Analog Found" — genuinely novel mechanism)

**Derivation note:** CONTEXT.md contains no literal file list. Every row below is traced to a specific JOB-0x requirement or D-0x decision so the planner can audit the inference. Phase 17 scope is closed lifecycle + restart-safe execution + dependencies + cancellation + progress/logs + **read-only** observation (JOB-07). No submission endpoints (Phase 18), no scheduling (Phase 20), no retry *execution* (Phase 19 — only linkage capability is preserved here).

## File Classification

| New/Modified File | Role | Data Flow | Traces To | Closest Analog | Match Quality |
|---|---|---|---|---|---|
| `src/trading_platform/db/models/job.py` | model | CRUD | JOB-01, JOB-02, D-01–D-03 | `src/trading_platform/db/models/strategy_run.py` | exact |
| `src/trading_platform/db/models/job_dependency.py` | model | CRUD | JOB-05, D-04–D-06 | `src/trading_platform/db/models/paper_order.py` (`supersedes_paper_order_id` self-referential FK + relationship) | role-match |
| `src/trading_platform/db/models/job_log.py` | model | event-driven (append-only) | JOB-07, D-13, D-14 | `src/trading_platform/db/models/execution_event.py` | exact |
| `src/trading_platform/db/models/job_event.py` (lifecycle transition audit incl. cancellation history) | model | event-driven (append-only) | JOB-06, D-07–D-10 | `src/trading_platform/db/models/order_event.py` | exact |
| `src/trading_platform/db/models/__init__.py` (extend exports) | model (barrel) | — | all JOB-0x | `src/trading_platform/db/models/__init__.py` (existing) | exact |
| `src/trading_platform/jobs/registry.py` | service (registry) | CRUD (in-memory register/resolve) | JOB-03 | `src/trading_platform/strategies/registry.py` | exact |
| `src/trading_platform/jobs/lifecycle.py` (closed-transition table + guard) | service | transform | JOB-01, D-01, D-04, D-09 | `src/trading_platform/services/execution/transition.py` | exact |
| `src/trading_platform/jobs/queue.py` (claim/lease loop) | service | batch/event-driven | JOB-02, D-01 (agent's discretion: claim/lease mechanism) | `src/trading_platform/services/concurrency_guard.py` (advisory-lock primitive) + `src/trading_platform/services/stale_runs.py` (lazy stale detection/reclaim) | role-match (composite) |
| `src/trading_platform/jobs/dependencies.py` (cycle/topology validation) | service | transform | JOB-05, D-04–D-06 | none found | **no analog** |
| `src/trading_platform/jobs/cancellation.py` (cooperative token/grace period) | service | event-driven | JOB-06, D-07–D-10 | `src/trading_platform/services/stale_runs.py` (timeout-cutoff query pattern) | role-match |
| `src/trading_platform/jobs/progress.py` | service | transform | D-11, D-12 | `src/trading_platform/core/logging.py` (structured-context dict shape) | role-match |
| `src/trading_platform/worker/commands/run_jobs.py` (thin CLI wrapper) | route (CLI command) | request-response | JOB-02, Integration Points | `src/trading_platform/worker/commands/reconcile.py` | exact |
| `src/trading_platform/worker/__main__.py` (add one dispatch entry only) | controller (routing-only) | request-response | Integration Points ("without expanding worker/__main__.py") | `src/trading_platform/worker/__main__.py` (existing) | exact |
| `src/trading_platform/api/routes/jobs.py` | controller (FastAPI route) | request-response (read-only) | JOB-07 | `src/trading_platform/api/routes/runs.py` | exact |
| `src/trading_platform/api/dependencies.py` (extend with job-read deps) | middleware/provider | request-response | JOB-07 | `src/trading_platform/api/dependencies.py` (existing) | exact |
| `src/trading_platform/api/app.py` (register jobs router) | config (app wiring) | — | JOB-07 | `src/trading_platform/api/app.py` (existing) | exact |
| `alembic/versions/00XX_phase17_job_framework.py` | migration | batch | JOB-01, JOB-02, JOB-05, JOB-06 | `alembic/versions/0009_phase5_order_lifecycle.py` (multi-table create + enum + FK + index) and `alembic/versions/0017_...` (revision-id length constraint precedent) | exact |
| `tests/test_db_migrations.py` (add `test_alembic_upgrade_creates_phase17_job_tables`) | test | request-response | migration enforcement (Integration Points) | existing `test_alembic_upgrade_creates_phase5_paper_order_tables` in same file | exact |
| `tests/test_job_registry.py` | test | request-response | JOB-03 (enforcement test) | `tests/test_strategy_registry.py` | role-match |
| `tests/test_job_import_boundary.py` | test | request-response | JOB-04 (reverse import-boundary enforcement test) | `tests/test_log_enforcement.py` (AST-walk import-boundary scan) | role-match (assertions are novel — see note below) |
| `tests/test_stale_job_reclaim.py` | test | request-response | JOB-02, D-01, D-03 (crash recovery) | `tests/test_stale_run_reclaim.py` | exact |

**Match-quality honesty note:** `test_job_registry.py` and `test_job_import_boundary.py` are marked role-match/partial, not exact — `test_strategy_registry.py` and `test_log_enforcement.py` prove the *scanning technique* (AST walk, parametrized module list, `pytest.raises`), but JOB-03's "zero queue-framework modules touched" and JOB-04's "no domain service imports job/HTTP/scheduling/UI modules" are novel assertions the planner must write fresh, not copy.

---

## Pattern Assignments

### `src/trading_platform/db/models/job.py` (model, CRUD)

**Analog:** `src/trading_platform/db/models/strategy_run.py`

**Imports pattern** (lines 1-13):
```python
from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Index, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from trading_platform.db.base import Base, TimestampedModel
```

**Closed lifecycle StrEnum pattern** (lines 34-44, adapt to `QUEUED/RUNNING/SUCCEEDED/FAILED/CANCELLED`):
```python
class StrategyRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    STALE = "stale"


def _enum_values(enum_cls: type[StrEnum]) -> list[str]:
    return [member.value for member in enum_cls]
```

**Persisted Enum column pattern with `validate_strings=True`** (lines 69-78) — this is the mandatory idiom for JOB-01's "no state outside the enum is representable":
```python
status: Mapped[StrategyRunStatus] = mapped_column(
    Enum(
        StrategyRunStatus,
        name="strategy_run_status",
        values_callable=_enum_values,
        validate_strings=True,
    ),
    nullable=False,
    default=StrategyRunStatus.PENDING,
)
```

**UUID identity + timestamps + JSON snapshot + terminal-error fields** (lines 54-88) — directly reusable shape for Job's `outcome_uncertain`, `failure_reason`, `parameters_snapshot`/`result_summary`-equivalent columns:
```python
id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
...
started_at: Mapped[datetime] = mapped_column(
    DateTime(timezone=True),
    server_default=func.now(),
    nullable=False,
)
completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
parameters_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
result_summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
error_message: Mapped[str | None] = mapped_column(Text(), nullable=True)
```

**Index pattern** (lines 50-52) — apply the same `(fk_id, status)` composite index shape for Job's queue-claim query:
```python
__table_args__ = (
    Index("ix_strategy_runs_strategy_id_status", "strategy_id", "status"),
)
```

`TimestampedModel, Base` mixin (from `db/base.py`, lines 21-40) supplies `created_at`/`updated_at` — reuse verbatim rather than redeclaring.

---

### `src/trading_platform/db/models/job_dependency.py` (model, CRUD — JOB-05, D-04–D-06)

**Analog:** `src/trading_platform/db/models/paper_order.py` — self-referential FK + relationship pattern (lines 74-78, 121-129)

JOB-05/D-06's immutable dependency set is fundamentally a self-referential edge (both endpoints are Job IDs). `paper_order.py`'s `supersedes_paper_order_id` is the one existing precedent in this codebase for a FK that targets its *own* table, plus the paired `relationship(..., remote_side=..., foreign_keys=[...])` declaration needed to traverse it in both directions:

```python
supersedes_paper_order_id: Mapped[uuid.UUID | None] = mapped_column(
    Uuid(as_uuid=True),
    ForeignKey("paper_orders.id", ondelete="SET NULL"),
    nullable=True,
)
...
supersedes_paper_order: Mapped["PaperOrder | None"] = relationship(
    remote_side="PaperOrder.id",
    foreign_keys=[supersedes_paper_order_id],
    back_populates="superseded_by_orders",
)
superseded_by_orders: Mapped[list["PaperOrder"]] = relationship(
    back_populates="supersedes_paper_order",
    cascade="save-update, merge",
)
```
For `job_dependency.py`, model the dependency edge as its own table (not a nullable column on `jobs`, since a Job can depend on *multiple* other Jobs — a many-to-many shape `supersedes_paper_order_id` does not need to solve): `job_id` and `depends_on_job_id`, both `ForeignKey("jobs.id", ondelete="CASCADE")`, with a composite unique constraint on `(job_id, depends_on_job_id)` to make a duplicate edge a DB-level no-op. D-06's "reject self-dependencies and cycles before the Job is queued" is enforced in `jobs/dependencies.py` at submission time (no analog — see below); a `CheckConstraint` on `job_id != depends_on_job_id` is a reasonable DB-level backstop for the self-dependency half specifically, mirroring how this codebase pairs application-level guards with DB constraints elsewhere (e.g. `uq_paper_orders_intent_hash`).

`tests/test_db_migrations.py` exercises this exact self-referential shape end-to-end for `paper_orders.supersedes_paper_order_id` (`test_phase7_idempotent_intent_schema_supports_predecessor_links`): it asserts `persisted.supersedes_paper_order is not None` and `persisted.supersedes_paper_order.intent_version == 1` after a round trip through `session_scope` — the same assertion shape (`job.dependencies[0].depends_on_job.status == ...`) is the template for a `job_dependency.py` persistence test.

---

### `src/trading_platform/db/models/job_event.py` (model, event-driven append-only — cancellation/lifecycle audit for D-07–D-10)

**Analog:** `src/trading_platform/db/models/order_event.py`

**Full append-only transition-record shape** (lines 54-115) — this is the direct precedent for D-10's requirement (requester identity, reason, `requested_at`, `acknowledged_at`, terminal cause):
```python
class OrderEvent(TimestampedModel, Base):
    """Durable append-only record of every accepted or rejected order transition."""

    __tablename__ = "order_events"
    __table_args__ = (
        Index("ix_order_events_paper_order_id_event_at", "paper_order_id", "event_at"),
        Index("ix_order_events_strategy_run_id_event_at", "strategy_run_id", "event_at"),
        Index("ix_order_events_paper_order_id_outcome", "paper_order_id", "outcome"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    paper_order_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("paper_orders.id", ondelete="CASCADE"),
        nullable=False,
    )
    from_state: Mapped[OrderLifecycleState] = mapped_column(Enum(..., validate_strings=True), nullable=False)
    event_type: Mapped[OrderTransitionEventType] = mapped_column(Enum(..., validate_strings=True), nullable=False)
    to_state: Mapped[OrderLifecycleState] = mapped_column(Enum(..., validate_strings=True), nullable=False)
    outcome: Mapped[OrderTransitionOutcome] = mapped_column(Enum(..., validate_strings=True), nullable=False)
    event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
```
Map `from_state`/`to_state`/`event_type`/`outcome`/`event_at`/`details` directly onto Job's transition audit; add `requester`/`reason`/`acknowledged_at` columns per D-10 (no existing analog carries requester identity — this is new).

---

### `src/trading_platform/db/models/job_log.py` (model, event-driven append-only — JOB-07 structured logs)

**Analog:** `src/trading_platform/db/models/execution_event.py`

**Full model** (lines 19-48):
```python
class ExecutionEvent(TimestampedModel, Base):
    __tablename__ = "execution_events"
    __table_args__ = (
        Index("ix_execution_events_strategy_run_id_event_at", "strategy_run_id", "event_at"),
        Index("ix_execution_events_blocks_execution_event_at", "blocks_execution", "event_at"),
        Index("ix_execution_events_paper_order_id_event_type", "paper_order_id", "event_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    strategy_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("strategy_runs.id", ondelete="CASCADE"), nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    blocks_execution: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    message: Mapped[str] = mapped_column(Text(), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
```
D-13 requires timestamp, level, stable event code, human-readable message, Job ID, handler type, sanitized context — map `severity`→level, `event_type`→stable event code, `message`, `details` (JSON) directly; add `job_id` FK and `handler_type` column. D-13 also requires **deterministic ordering** — the `(fk_id, event_at)` composite index above is the ordering mechanism; consider adding a monotonic sequence column if `event_at` timestamp collisions are possible within one Job (same open question this codebase resolves via index-order + insertion order elsewhere).

---

### `src/trading_platform/jobs/registry.py` (service, registry — JOB-03)

**Analog:** `src/trading_platform/strategies/registry.py`

**Full register/resolve/error pattern** (lines 1-49) — copy this shape exactly; it is the existing "explicit registration and resolution" precedent the CONTEXT.md `<code_context>` block names directly:
```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UnknownStrategyError(KeyError):
    strategy_id: str

    def __str__(self) -> str:
        return f"Unknown strategy '{self.strategy_id}'."


class StrategyRegistry:
    """In-memory registry with explicit registration and resolution."""

    def __init__(self) -> None:
        self._strategies: dict[str, BaseStrategy] = {}

    def register(self, strategy: BaseStrategy) -> None:
        strategy_id = strategy.strategy_id
        if strategy_id in self._strategies:
            raise ValueError(f"Strategy '{strategy_id}' is already registered.")
        self._strategies[strategy_id] = strategy

    def resolve(self, strategy_id: str) -> BaseStrategy:
        try:
            return self._strategies[strategy_id]
        except KeyError as exc:
            raise UnknownStrategyError(strategy_id) from exc
```
For JOB-03 ("adding a Job type touches zero queue-framework modules"), the equivalent `JobRegistry.register(handler)` / `JobRegistry.resolve(job_type)` pair, with a duplicate-registration `ValueError` and an `UnknownJobTypeError(KeyError)` dataclass, satisfies the enforcement contract the same way `build_default_registry()` does for strategies (lines 45-49).

---

### `src/trading_platform/jobs/lifecycle.py` (service, transform — JOB-01 closed-transition enforcement)

**Analog:** `src/trading_platform/services/execution/transition.py`

**Legal-transition table + guard pattern** (lines 1-20, 92-140) — this is the strongest available precedent for enforcing "no state outside the enum is representable" as *code*, not just as a DB constraint:
```python
_LEGAL_TRANSITIONS: dict[
    OrderLifecycleState,
    dict[OrderTransitionEventType, OrderLifecycleState],
] = {
    OrderLifecycleState.PENDING_SUBMISSION: {
        OrderTransitionEventType.BROKER_ACKNOWLEDGED: OrderLifecycleState.SUBMITTED,
        ...
    },
    ...
}


class IllegalOrderTransition(RuntimeError):
    def __init__(self, *, order_id, from_state, event_type, details) -> None:
        ...
        super().__init__(
            f"Illegal order transition for {order_id}: {from_state.value} -> {event_type.value}"
        )


def resolve_transition_target(*, from_state, event_type) -> OrderLifecycleState | None:
    """Return the target state for an event, or None when the transition is illegal."""
    return _LEGAL_TRANSITIONS.get(from_state, {}).get(event_type)


def apply_order_transition(order_id, event, *, settings=None, session=None) -> OrderTransitionResult:
    ...
    next_state = resolve_transition_target(from_state=paper_order.status, event_type=event.event_type)
    if next_state is None:
        error = IllegalOrderTransition(...)
        _persist_rejected_transition(settings or load_settings(), order_id, event, error)
        raise error
    return _persist_transition_event(session, paper_order=paper_order, event=event, next_state=next_state, outcome=OrderTransitionOutcome.ACCEPTED)
```
Adapt `_LEGAL_TRANSITIONS` to the 5-state Job enum: `QUEUED → RUNNING`, `RUNNING → SUCCEEDED/FAILED/CANCELLED`, `QUEUED → CANCELLED` (D-07), with illegal transitions raising and persisting a rejected-transition record the same way `IllegalOrderTransition` does. This single dict is also the natural home for enforcing D-09 (`cancellation_timeout` → `FAILED`) as one more legal edge.

---

### `src/trading_platform/jobs/queue.py` (service, batch/event-driven — JOB-02 restart-safe claim/lease)

**Analog A — advisory-lock claim primitive:** `src/trading_platform/services/concurrency_guard.py`

**Non-blocking lock-or-fail contextmanager pattern** (lines 65-106):
```python
@contextmanager
def session_run_lock(*, strategy_id: str, session_date: date, settings: Settings | None = None) -> Iterator[None]:
    resolved_settings = settings if settings is not None else load_settings()
    key = advisory_lock_key(strategy_id, session_date)
    engine = get_engine(resolved_settings)
    connection = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    acquired = False
    try:
        acquired = bool(connection.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": key}).scalar_one())
        if not acquired:
            raise ConcurrentRunLockedError(strategy_id, session_date)
        yield
    finally:
        if acquired:
            connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
        connection.close()
```
Key property directly relevant to JOB-02: PostgreSQL auto-releases session-level advisory locks when the holding connection drops on crash — this is the "restart-safe, never silently lost or duplicated" guarantee referenced in JOB-02, achieved without any heartbeat. This is the strongest local precedent for whatever claim/lease primitive the planner chooses (`SELECT ... FOR UPDATE SKIP LOCKED` is the more common DB-queue idiom and is also compatible with `session_scope`, but this file is the closest **existing PostgreSQL locking pattern already proven in this codebase** and the natural starting point).

**Analog B — stale/crashed-run detection and terminal reclaim:** `src/trading_platform/services/stale_runs.py`

**Single-query stale detector** (lines 28-41) — direct precedent for JOB-02 "a running job interrupted by crash is detected and moved to a terminal state":
```python
def find_stale_runs(session: Session, *, timeout_minutes: int) -> list[StrategyRun]:
    cutoff = datetime.now(UTC) - timedelta(minutes=timeout_minutes)
    stmt = select(StrategyRun).where(
        StrategyRun.status == StrategyRunStatus.RUNNING,
        StrategyRun.run_type == StrategyRunType.PAPER_EXECUTION,
        StrategyRun.started_at < cutoff,
    )
    return list(session.execute(stmt).scalars().all())
```

**Audited, idempotent reclaim-to-terminal-state pattern** (lines 44-115) — maps directly onto D-01 (`worker_lost`/`lease_expired` → `FAILED`, never `CANCELLED`) and D-03 (`outcome_uncertain=true`):
```python
def reclaim_stale_runs(session, *, strategy_public_id, session_date, timeout_minutes, reclaiming_run_id=None) -> list[uuid.UUID]:
    ...
    for run in candidates:
        ...
        run.status = StrategyRunStatus.STALE
        run.completed_at = now
        session.add(
            ExecutionEvent(
                strategy_run_id=run.id,
                event_type="paper_run_reclaimed_stale",
                severity="warning",
                blocks_execution=False,
                event_at=now,
                message=f"Reclaimed stale running paper-execution run '{run.id}' ...",
                details={"reclaimed_run_id": str(run.id), "reclaiming_run_id": ..., "timeout_minutes": timeout_minutes},
            )
        )
        reclaimed_ids.append(run.id)
    session.flush()
    return reclaimed_ids
```
Idempotency property is load-bearing: once a row leaves `RUNNING` it no longer matches the detector query, so a second reclaim pass is a safe no-op — reuse this exact idempotency shape for Job worker-loss detection. Note this precedent is currently **lazy** (resolved only when a new run for the same tuple starts); JOB-02/D-01 may require an **active** lease-expiry sweep instead (a worker polling loop checking `lease_expires_at < now()`), which is agent's discretion per CONTEXT.md — `find_stale_runs`'s query shape and `reclaim_stale_runs`'s audited-reclaim shape both still apply directly to that design.

---

### `src/trading_platform/api/routes/jobs.py` (controller, request-response, read-only — JOB-07)

**Analog:** `src/trading_platform/api/routes/runs.py`

**Full list + detail route pattern** (lines 1-45) — copy verbatim, swap `OperatorReadService`/`operator_reads.list_runs` for the Job-read equivalent:
```python
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from trading_platform.api.dependencies import (
    build_collection_response,
    get_operator_read_filters,
    get_operator_read_service,
)

router = APIRouter(prefix="/api/v1/runs", tags=["runs"])


@router.get("")
def list_runs(
    filters: Annotated[OperatorReadFilters, Depends(get_operator_read_filters)],
    operator_reads: Annotated[OperatorReadService, Depends(get_operator_read_service)],
) -> dict[str, object]:
    return build_collection_response(filters=filters, items=operator_reads.list_runs(filters))


@router.get("/{run_id}")
def run_detail(run_id: UUID, operator_reads: Annotated[OperatorReadService, Depends(get_operator_read_service)]) -> dict[str, object]:
    try:
        return operator_reads.get_run_detail(str(run_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
```
JOB-07 additionally needs progress and logs endpoints — follow the same `@router.get("/{job_id}/progress")` / `@router.get("/{job_id}/logs")` shape, same `LookupError` → `HTTPException(404)` translation. **Scope boundary: this route file is read-only.** No `POST`/submission verbs belong here — those are Phase 18 (ORCH-01..04).

**Dependency-provider pattern to extend** — `src/trading_platform/api/dependencies.py` (lines 41-94): `get_operator_read_service`, `get_operator_read_filters`, `build_collection_response` are the exact shape to replicate for `get_job_read_service` / `get_job_read_filters` / job pagination. `build_operator_read_catalog` (lines 97-122) is the discoverability-catalog pattern to extend with a `jobs` section.

**Router registration pattern** — `src/trading_platform/api/app.py` (lines 10-15, 66-71): add `from trading_platform.api.routes.jobs import router as jobs_router` and `app.include_router(jobs_router)` alongside the existing six routers; no other change to `create_app()`/`lifespan()` needed.

---

### `src/trading_platform/worker/commands/run_jobs.py` (route/CLI command, request-response)

**Analog:** `src/trading_platform/worker/commands/reconcile.py`

**Full thin-wrapper pattern** (lines 1-43) — this file is the "worker entrypoint is routing-only; command modules delegate to services" precedent CONTEXT.md names directly:
```python
"""Worker CLI handler: `<command-name>` (STRUCT-03: extracted from __main__.py)."""

from __future__ import annotations

import argparse
import json

from trading_platform.core.logging import configure_logging, get_logger
from trading_platform.core.startup import enforce_startup_config
from trading_platform.services.config.validation import ExecutionMode


def run_<command>_command(args: argparse.Namespace) -> None:
    settings = enforce_startup_config(mode=ExecutionMode.PAPER)
    configure_logging(settings.logging)
    logger = get_logger("trading_platform.worker")
    report = <service_call>(...)
    logger.info("worker_<event>_completed", extra={"context": {...}})
    print(json.dumps(report.to_dict(), indent=(None if args.compact else 2), default=str))
```
`src/trading_platform/worker/__main__.py` stays strictly routing-only (lines 1-32) — add exactly one `DISPATCH` entry or one `if args.command == "run-jobs":` branch, no business logic in this file (STRUCT-03).

---

### `alembic/versions/00XX_phase17_job_framework.py` (migration, batch)

**Analog:** `alembic/versions/0009_phase5_order_lifecycle.py`

**Multi-table create with enum/FK/index pattern** (lines 15-63):
```python
def upgrade() -> None:
    op.create_table(
        "paper_fills",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("paper_order_id", sa.UUID(), nullable=False),
        ...
        sa.ForeignKeyConstraint(
            ["paper_order_id"], ["paper_orders.id"],
            name=op.f("fk_paper_fills_paper_order_id_paper_orders"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_paper_fills")),
        sa.UniqueConstraint("broker_fill_id", name="uq_paper_fills_broker_fill_id"),
    )
    op.create_index("ix_paper_fills_paper_order_id_filled_at", "paper_fills", ["paper_order_id", "filled_at"], unique=False)
```
`op.f(...)` names come from the shared `NAMING_CONVENTION` in `src/trading_platform/db/models/__init__.py` (i.e., `db/base.py`, which `db/models/__init__.py` does not re-export but which governs every model in that package — lines 10-16 of `db/base.py`) — the migration's constraint names must match what SQLAlchemy autogenerates for the ORM models above, or `test_alembic_upgrade_creates_phase17_job_tables` (and any future autogenerate diff check) will disagree with the live schema.

**Adding an enum value to an existing type (if extending, e.g., a future terminal-cause enum reuse):** `alembic/versions/0016_phase8_stale_run_status.py`:
```python
def upgrade() -> None:
    op.execute("ALTER TYPE strategy_run_status ADD VALUE IF NOT EXISTS 'stale'")

def downgrade() -> None:
    # PostgreSQL cannot drop a single enum value without recreating the type;
    # documented no-op downgrade.
    pass
```

**Critical constraint (documented failure, not stylistic):** `alembic/versions/0017_phase11_query_performance_indices.py` (lines 34-46) — `alembic_version.version_num` is `VARCHAR(32)`; every revision id in this repo is ≤ 29 characters. A Phase 17 migration's `revision = "..."` string **must** stay ≤ 32 chars or `psycopg.errors.StringDataRightTruncation` breaks every fixture that upgrades to head.

---

### `tests/test_db_migrations.py` (test, add one function to the existing file)

**Analog:** existing `test_alembic_upgrade_creates_phase5_paper_order_tables` in the same file (lines 324-443).

**Fixture reuse (do not duplicate):** the `migrated_database` fixture (lines 83-119) creates an isolated Postgres database per test, upgrades to head, and tears down — reuse it exactly:
```python
def test_alembic_upgrade_creates_phase17_job_tables(migrated_database: str) -> None:
    settings = load_settings()
    inspector = inspect(get_engine(settings))

    table_names = set(inspector.get_table_names())
    assert {"jobs", "job_dependencies", "job_events", "job_logs"}.issubset(table_names)

    job_cols = {column["name"] for column in inspector.get_columns("jobs")}
    assert job_cols >= {"id", "job_type", "status", "started_at", ...}

    enums = {enum["name"]: set(enum["labels"]) for enum in inspector.get_enums()}
    assert enums["job_status"] == {"queued", "running", "succeeded", "failed", "cancelled"}
```
Use `_upgrade_to_revision("<phase17-revision-id>")` (lines 73-77) the same way `test_phase7_order_kernel_migration_preserves_existing_paper_orders` does (lines 446-465) if a partial-upgrade preserved-data test is also needed.

---

### `tests/test_job_registry.py` (test, JOB-03 enforcement)

**Analog:** `tests/test_strategy_registry.py` (full file, lines 1-49)

```python
def test_registry_lists_and_resolves_default_strategy() -> None:
    registry = build_default_registry(load_settings())
    strategies = registry.list_public()
    assert len(strategies) == 1
    ...
    with pytest.raises(UnknownStrategyError):
        registry.resolve("missing_strategy")
```
For JOB-03's stronger claim ("adding a type touches zero queue-framework modules"), this file only proves register/resolve/duplicate/unknown behavior — the "zero modules touched" assertion needs a companion static check (see `test_job_import_boundary.py` below) since `test_strategy_registry.py` has no equivalent for that half of the requirement.

---

### `tests/test_job_import_boundary.py` (test, JOB-04 reverse import-boundary enforcement)

**Analog:** `tests/test_log_enforcement.py` (lines 1-125)

**AST-walk parametrized module-list pattern** — reusable technique, novel assertion:
```python
def _find_direct_getlogger_calls(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(), filename=str(path))
    hits: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (isinstance(func, ast.Attribute) and func.attr == "getLogger"
                and isinstance(func.value, ast.Name) and func.value.id == "logging"):
            hits.append(node.lineno)
    return hits


@pytest.mark.parametrize("module_path", IN_SCOPE_MODULES, ids=[...])
def test_import_boundary_no_direct_get_logger(module_path: Path) -> None:
    hits = _find_direct_getlogger_calls(module_path)
    assert not hits, f"{module_path} calls logging.getLogger(...) directly at line(s) {hits}; ..."


def test_import_boundary_module_list_is_not_empty() -> None:
    assert len(IN_SCOPE_MODULES) == 12
```
For JOB-04, invert the scan target: walk `ast.Import`/`ast.ImportFrom` nodes in every `src/trading_platform/services/**/*.py` file and assert none resolve to `trading_platform.jobs`, `trading_platform.api`, `trading_platform.worker` (or `fastapi`/scheduling modules). The "module list is not empty" guard (line 122-125) is worth copying verbatim — it is the regression-proofing idiom that stops a silently-emptied scope list from turning the parametrized test into a no-op.

---

### `tests/test_stale_job_reclaim.py` (test, JOB-02/D-01/D-03 crash recovery)

**Analog:** `tests/test_stale_run_reclaim.py`

Key test names to mirror (lines 131, 173, 251):
```python
def test_find_stale_runs_detects_only_running_past_timeout(...): ...
def test_reclaim_stale_runs_marks_all_past_threshold_rows_stale_with_audit(...): ...
def test_reclaim_stale_runs_is_idempotent(migrated_stale_reclaim_db: str) -> None: ...
```
The idempotency test (a second reclaim pass over already-reclaimed rows finds nothing) is the direct precedent for proving JOB-02's "never silently lost or duplicated" — write the Job equivalent the same way: seed a `RUNNING` Job past the lease/timeout cutoff, call the reclaim function twice, assert the second call returns an empty list and the audit trail has exactly one entry.

---

## Shared Patterns

### Synchronous session/transaction boundary
**Source:** `src/trading_platform/db/session.py` (module docstring, lines 1-31; `session_scope`, lines 95-105)
**Apply to:** every Job service/model file that writes to the database.
```python
@contextmanager
def session_scope(settings: Settings | DatabaseSettings | None = None) -> Iterator[Session]:
    session = get_session_factory(settings)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
```
This is the **only** authorized engine/session lifecycle in the codebase (DB-01/DB-02/DB-03, enforced by an import-boundary test per `PROJECT.md`'s Key Decisions table). Job queue/lease code must not construct its own engine or session factory — it may open one dedicated `engine.connect()` for an advisory lock (as `concurrency_guard.py` does), but ORM reads/writes go through `session_scope`.

### Structured-log sanitization chokepoint
**Source:** `src/trading_platform/core/logging.py` (`emit_structured_log`, lines 106-131; `JsonLogFormatter.format`, lines 21-46)
**Apply to:** `jobs/progress.py`, `jobs/queue.py`, `worker/commands/run_jobs.py`, and any Job-log persistence path (D-13 "sanitized context").
```python
def emit_structured_log(logger, level, message, *, strategy_id=None, run_id=None, ..., **extra) -> None:
    context = build_log_context(strategy_id=strategy_id, run_id=run_id, ..., **extra)
    logger.log(level, message, extra={"context": sanitize(context, unmask_ids=_DEBUG_UNMASK_IDS)})
```
Job log persistence (distinct from process stdout logging) must still route any context dict through `sanitize()` before writing to `job_logs.details` — the codebase's LOG-06 enforcement test (`tests/test_log_enforcement.py`) proves this is the one chokepoint; do not invent a second sanitization path for Job logs.

### Audited state-change report + append-only event pattern
**Source:** `src/trading_platform/services/operator_controls.py` (`_set_kill_switch_state`, lines 353-470)
**Apply to:** `jobs/cancellation.py` (D-07–D-10) and any Job status-change service.
```python
with session_scope(self.settings) as session:
    ...
    control.state = target_state
    control.last_changed_at = changed_at
    control.last_change_actor = actor
    control.last_change_reason = reason
    control.last_change_run_id = strategy_run.id
    session.flush()
    ...
    session.add(ExecutionEvent(..., event_type=event_type, severity=severity, message=..., details=result_summary))
```
This is the closest existing precedent for "persist a cancellation request, require handlers to check safe points" (D-08) combined with "requester identity, optional reason, `requested_at`, `acknowledged_at`, terminal cause" (D-10): one row updated for current state + one durable append-only event row per transition, both written in the same `session_scope` transaction.

### Closed StrEnum persistence idiom
**Source:** `src/trading_platform/db/models/strategy_run.py` (lines 25-44, 69-78) and `src/trading_platform/db/models/order_event.py` (lines 19-51)
**Apply to:** every new Job enum column (`JobStatus`, `JobFailureReason`, `JobCancellationCause`).
```python
class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


def _enum_values(enum_cls: type[StrEnum]) -> list[str]:
    return [member.value for member in enum_cls]

status: Mapped[JobStatus] = mapped_column(
    Enum(JobStatus, name="job_status", values_callable=_enum_values, validate_strings=True),
    nullable=False,
    default=JobStatus.QUEUED,
)
```
`values_callable=_enum_values` + `validate_strings=True` is used on every StrEnum column in this codebase — it is what makes JOB-01's "no state outside the enum is representable" true at both the Python and PostgreSQL layers (a native enum type in migrations, e.g. `alembic/versions/0009_phase5_order_lifecycle.py`'s `order_lifecycle_state`).

### `NAMING_CONVENTION` for constraint/index names
**Source:** `src/trading_platform/db/base.py`, lines 10-18
**Apply to:** every new ORM model and its corresponding migration's `op.f(...)` calls.
```python
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
```
Migration constraint names generated via `op.f("fk_jobs_..._...")` must match what SQLAlchemy would autogenerate for the ORM model, or the migration and the model definition silently diverge.

---

## No Analog Found

| File | Role | Data Flow | Reason |
|---|---|---|---|
| `src/trading_platform/jobs/dependencies.py` (cycle/topology validation, D-04–D-06) | service | transform | No existing DAG/cycle-detection code in this codebase — every current FK relationship (including the self-referential `paper_order.supersedes_paper_order_id` used as the `job_dependency.py` analog above) is a simple parent/predecessor link, never an N-ary graph requiring cycle detection. Planner should design fresh (e.g., DFS cycle check at submission time before any row is inserted) rather than copy an analog. |

**Note on the two rows re-classified as role-match (not true no-analog):** `jobs/cancellation.py` and `jobs/progress.py` are carried in the File Classification table above as role-match, each against a named partial precedent (`stale_runs.py`'s timeout-cutoff query for cancellation's grace-period detection half; `core/logging.py`'s structured-context dict shape for progress's snapshot shape). Neither precedent covers the *whole* mechanism — `jobs/cancellation.py` has no existing "handler checks a safe point and acknowledges" cooperative protocol anywhere in the codebase, and `jobs/progress.py` has no existing percentage/step/counter schema — so the planner should treat the named analog as a partial starting point, not a copy-paste source, for the remainder of each file.

---

## Metadata

**Analog search scope:** `src/trading_platform/{db/models,services,strategies,worker,api,core}`, `alembic/versions`, `tests`
**Files read in full or targeted-range:** `strategies/registry.py`, `db/models/strategy_run.py`, `db/models/execution_event.py`, `db/models/order_event.py`, `db/models/system_control.py`, `db/models/paper_order.py` (self-referential FK section), `db/models/__init__.py`, `db/base.py`, `db/session.py`, `core/logging.py`, `worker/__main__.py`, `worker/commands/reconcile.py`, `services/operator_controls.py`, `services/stale_runs.py`, `services/concurrency_guard.py`, `services/execution/transition.py` (partial), `api/app.py`, `api/dependencies.py`, `api/routes/runs.py`, `alembic/versions/0009_phase5_order_lifecycle.py`, `alembic/versions/0016_phase8_stale_run_status.py`, `alembic/versions/0017_phase11_query_performance_indices.py`, `tests/test_db_migrations.py`, `tests/test_log_enforcement.py`, `tests/test_strategy_registry.py`, `tests/test_stale_run_reclaim.py` (test-name grep only)
**Pattern extraction date:** 2026-07-19
