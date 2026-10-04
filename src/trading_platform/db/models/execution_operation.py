"""ORM models for the execution operation (REC-02, D-16..D-21, S-6).

An execution operation is ONE execution of ONE evaluation for ONE strategy, pinned to one
risk run, that may span several Jobs (a start Job plus Continue Jobs). It persists:

* the closed operation ``state`` and the per-state closed ``reason`` (DB CHECKs);
* the pinned intents, including those never registered as orders (``planned``) and the
  two stored unsent terminal dispositions (``expired_unsent`` / ``cancelled_unsent``);
* the linked Jobs and the S1-R3 fencing columns (``executor_job_id``, ``execution_epoch``,
  ``last_guarded_at``).

Database invariants (each rejected by PostgreSQL, tested):

* ``uq_execution_operations_one_open_per_strategy``: at most one operation per strategy in
  an OPEN state (``running``, ``paused``, ``requires_reevaluation``);
* ``uq_execution_operations_risk_run``: one operation per (strategy, pinned risk run);
* ``ck_execution_operations_state`` / ``ck_execution_operations_reason_by_state``: the
  closed state set and the closed reason set of each state;
* ``uq_execution_operation_intents_operation_client_order_id``: a client order id is unique
  inside one operation only (identities are derived without the risk run, so a new
  operation on a re-evaluation of the same session re-derives the same id;
  ``paper_orders.intent_hash`` stays the only global duplicate guard).

Reason enums live in ``services/execution/operations.py``; the value sets below are the
database-side twins and a test pins that both agree.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from trading_platform.db.base import Base, TimestampedModel


class OperationState(StrEnum):
    """Closed operation states (D-18, 03 sec.3.7 A2)."""

    RUNNING = "running"
    PAUSED = "paused"
    REQUIRES_REEVALUATION = "requires_reevaluation"
    TERMINATED = "terminated"
    COMPLETED = "completed"


OPEN_OPERATION_STATES: tuple[OperationState, ...] = (
    OperationState.RUNNING,
    OperationState.PAUSED,
    OperationState.REQUIRES_REEVALUATION,
)


class IntentDisposition(StrEnum):
    """Stored disposition of a pinned intent; only the two unsent terminals are terminal."""

    OPEN = "open"
    EXPIRED_UNSENT = "expired_unsent"
    CANCELLED_UNSENT = "cancelled_unsent"


class OperationJobMode(StrEnum):
    START = "start"
    CONTINUE = "continue"


#: Paused reasons (D-18 plus the three review amendments).
PAUSED_REASON_VALUES: tuple[str, ...] = (
    "working_order_commitments_unaccounted",
    "awaiting_reconciliation",
    "kill_switch_tripped",
    "strategy_disabled",
    "reconciliation_blocking",
    "unrecognized_broker_activity",
    "outcome_unresolved",
    "broker_unavailable",
    "execution_window_not_open",
    "price_unavailable",
    "not_active_paper_strategy",
    "price_moved_beyond_tolerance",
)
#: Fixed re-evaluation reasons; ``risk_limit_failed:<code>`` is checked by pattern.
REEVALUATION_FIXED_REASON_VALUES: tuple[str, ...] = (
    "evaluation_data_changed",
    "strategy_settings_changed",
)
RISK_LIMIT_FAILED_PATTERN = "^risk_limit_failed:[a-z_]+$"
TERMINATED_REASON_VALUES: tuple[str, ...] = (
    "execution_window_elapsed",
    "cancelled_by_operator",
    "evaluation_superseded",
)


def _in(column: str, values: tuple[str, ...]) -> str:
    rendered = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({rendered})"


_STATE_VALUES = tuple(member.value for member in OperationState)
_OPEN_STATE_VALUES = tuple(member.value for member in OPEN_OPERATION_STATES)

REASON_BY_STATE_SQL = (
    "(state IN ('running', 'completed') AND reason IS NULL)"
    f" OR (state = 'paused' AND reason IS NOT NULL AND {_in('reason', PAUSED_REASON_VALUES)})"
    " OR (state = 'requires_reevaluation' AND reason IS NOT NULL AND ("
    f"{_in('reason', REEVALUATION_FIXED_REASON_VALUES)}"
    f" OR reason ~ '{RISK_LIMIT_FAILED_PATTERN}'))"
    f" OR (state = 'terminated' AND reason IS NOT NULL AND {_in('reason', TERMINATED_REASON_VALUES)})"
)


class ExecutionOperation(TimestampedModel, Base):
    """One execution of one evaluation of one strategy, spanning several Jobs."""

    __tablename__ = "execution_operations"
    __table_args__ = (
        CheckConstraint(_in("state", _STATE_VALUES), name="state"),
        CheckConstraint(REASON_BY_STATE_SQL, name="reason_by_state"),
        CheckConstraint("execution_epoch >= 0", name="execution_epoch_nonnegative"),
        UniqueConstraint("strategy_id", "risk_run_id", name="uq_execution_operations_risk_run"),
        Index(
            "uq_execution_operations_one_open_per_strategy",
            "strategy_id",
            unique=True,
            postgresql_where=text(f"state IN ({', '.join(repr(v) for v in _OPEN_STATE_VALUES)})"),
        ),
        Index("ix_execution_operations_strategy_id_created_at", "strategy_id", "created_at"),
        Index("ix_execution_operations_state", "state"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    strategy_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("strategies.id", ondelete="RESTRICT"), nullable=False
    )
    as_of_session: Mapped[date] = mapped_column(Date, nullable=False)
    risk_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("strategy_runs.id", ondelete="RESTRICT"), nullable=False
    )
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: Closed-vocabulary detail of the reason (for example ``price_lookup_failed``).
    reason_detail: Mapped[str | None] = mapped_column(String(64), nullable=True)
    state_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    #: S3-R4: the verified basis of the pinned risk run (sync job, snapshot, reconciliation).
    basis_verification: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    #: S1-R3: the Job that currently holds execution authority (NULL when none).
    executor_job_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    #: S1-R3: monotonic fencing token, incremented by every takeover, End and expiry.
    execution_epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: S1-R3: time of the latest successful send authorization (diagnostics).
    last_guarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    end_reason: Mapped[str | None] = mapped_column(Text(), nullable=True)


class ExecutionOperationIntent(Base):
    """One pinned intent of an operation, in plan order."""

    __tablename__ = "execution_operation_intents"
    __table_args__ = (
        CheckConstraint(
            _in("disposition", tuple(member.value for member in IntentDisposition)),
            name="disposition",
        ),
        CheckConstraint("sequence >= 1", name="sequence_positive"),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        UniqueConstraint("operation_id", "sequence"),
        UniqueConstraint(
            "operation_id",
            "client_order_id",
            name="uq_execution_operation_intents_operation_client_order_id",
        ),
        Index("ix_execution_operation_intents_paper_order_id", "paper_order_id"),
        Index("ix_execution_operation_intents_client_order_id", "client_order_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    operation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("execution_operations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    risk_event_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("risk_events.id", ondelete="RESTRICT"), nullable=True
    )
    symbol_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("symbols.id", ondelete="RESTRICT"), nullable=False
    )
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(precision=20, scale=6), nullable=False)
    #: Evaluation-time signal close: sizing basis and S2 deviation baseline, never a current price.
    reference_price: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=20, scale=6), nullable=True
    )
    client_order_id: Mapped[str] = mapped_column(String(64), nullable=False)
    paper_order_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("paper_orders.id", ondelete="SET NULL"), nullable=True
    )
    decision_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    prior_execution_refs: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    disposition: Mapped[str] = mapped_column(
        String(24), nullable=False, default=IntentDisposition.OPEN.value
    )
    disposition_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ExecutionOperationJob(Base):
    """A Job linked to an operation (the start Job or a Continue Job)."""

    __tablename__ = "execution_operation_jobs"
    __table_args__ = (
        CheckConstraint(
            _in("mode", tuple(member.value for member in OperationJobMode)), name="mode"
        ),
        UniqueConstraint("operation_id", "job_id"),
        Index("ix_execution_operation_jobs_job_id", "job_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    operation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("execution_operations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True
    )
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
