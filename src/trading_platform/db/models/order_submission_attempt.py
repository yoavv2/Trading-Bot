"""ORM model for the durable per-HTTP-attempt order submission log (COR-06, D-12).

One row per HTTP attempt of an order POST. A row is committed before the
attempt (outcome NULL) and completed exactly once after it. The table is
append-only in code: there is no delete path and an outcome is written once.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from trading_platform.db.base import Base, TimestampedModel


class AttemptOutcomeClass(StrEnum):
    """Closed per-attempt outcome set (D-12); the database CHECK enforces exactly these."""

    PRE_CONNECTION = "pre_connection"
    DEADLINE_EXPIRED = "deadline_expired"
    AMBIGUOUS = "ambiguous"
    DUPLICATE_REPORTED = "duplicate_reported"
    REJECTED = "rejected"
    ACCEPTED = "accepted"


_OUTCOME_VALUES_SQL = ", ".join(f"'{member.value}'" for member in AttemptOutcomeClass)


class OrderSubmissionAttempt(TimestampedModel, Base):
    """One HTTP attempt of an order POST; incomplete (NULL outcome) reads as ambiguous."""

    __tablename__ = "order_submission_attempts"
    __table_args__ = (
        UniqueConstraint("paper_order_id", "attempt_number"),
        # Naming convention renders ck_order_submission_attempts_<name>.
        CheckConstraint(
            f"outcome_class IS NULL OR outcome_class IN ({_OUTCOME_VALUES_SQL})",
            name="outcome_class",
        ),
        CheckConstraint("(completed_at IS NULL) = (outcome_class IS NULL)", name="outcome_pair"),
        CheckConstraint("attempt_number >= 1", name="attempt_number_positive"),
        Index("ix_order_submission_attempts_strategy_run_id", "strategy_run_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    paper_order_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("paper_orders.id", ondelete="CASCADE"),
        nullable=False,
    )
    strategy_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("strategy_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outcome_class: Mapped[str | None] = mapped_column(String(24), nullable=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    broker_message: Mapped[str | None] = mapped_column(Text(), nullable=True)
