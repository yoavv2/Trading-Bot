"""ORM model and enums for append-only Job lifecycle/cancellation audit records (JOB-06, D-07-D-10)."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Index, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from trading_platform.db.base import Base, TimestampedModel
from trading_platform.db.models.job import JobStatus, _enum_values

if TYPE_CHECKING:
    from trading_platform.db.models.job import Job


class JobEventType(StrEnum):
    SUBMITTED = "submitted"
    CLAIMED = "claimed"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLATION_REQUESTED = "cancellation_requested"
    CANCELLED = "cancelled"
    LEASE_EXPIRED = "lease_expired"
    WORKER_LOST = "worker_lost"
    CANCELLATION_TIMEOUT = "cancellation_timeout"
    DEPENDENCY_RESOLVED = "dependency_resolved"


class JobTransitionOutcome(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class JobEvent(TimestampedModel, Base):
    """Durable append-only record of every accepted or rejected Job transition."""

    __tablename__ = "job_events"
    __table_args__ = (
        Index("ix_job_events_job_id_event_at", "job_id", "event_at"),
        Index("ix_job_events_job_id_event_type", "job_id", "event_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    from_status: Mapped[JobStatus | None] = mapped_column(
        Enum(
            JobStatus,
            name="job_status",
            values_callable=_enum_values,
            validate_strings=True,
        ),
        nullable=True,
    )
    to_status: Mapped[JobStatus | None] = mapped_column(
        Enum(
            JobStatus,
            name="job_status",
            values_callable=_enum_values,
            validate_strings=True,
        ),
        nullable=True,
    )
    event_type: Mapped[JobEventType] = mapped_column(
        Enum(
            JobEventType,
            name="job_event_type",
            values_callable=_enum_values,
            validate_strings=True,
        ),
        nullable=False,
    )
    outcome: Mapped[JobTransitionOutcome] = mapped_column(
        Enum(
            JobTransitionOutcome,
            name="job_transition_outcome",
            values_callable=_enum_values,
            validate_strings=True,
        ),
        nullable=False,
    )
    event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    requested_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text(), nullable=True)
    requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    terminal_cause: Mapped[str | None] = mapped_column(String(64), nullable=True)

    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    job: Mapped["Job"] = relationship(foreign_keys=[job_id])
