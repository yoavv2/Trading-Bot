"""ORM model for the generic Job framework.

This file is the single definition site for the Phase 17 job status,
failure-reason, and cancellation-cause vocabulary. Every other Phase 17
module imports these names from here rather than redefining them.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    false,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from trading_platform.db.base import Base, TimestampedModel


class JobStatus(StrEnum):
    """The closed five-state Job lifecycle (JOB-01). No other member may exist."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobFailureReason(StrEnum):
    """Stable FAILED-outcome reasons.

    Per D-01, ``worker_lost`` and ``lease_expired`` are FAILED reasons, never a
    cancellation cause. Per D-09, ``cancellation_timeout`` is a FAILED reason.
    """

    HANDLER_ERROR = "handler_error"
    WORKER_LOST = "worker_lost"
    LEASE_EXPIRED = "lease_expired"
    CANCELLATION_TIMEOUT = "cancellation_timeout"


class JobCancellationCause(StrEnum):
    """Stable CANCELLED-outcome causes. Per D-04 these are the only legitimate paths to CANCELLED."""

    OPERATOR_REQUEST = "operator_request"
    DEPENDENCY_FAILED = "dependency_failed"
    DEPENDENCY_CANCELLED = "dependency_cancelled"


def _enum_values(enum_cls: type[StrEnum]) -> list[str]:
    return [member.value for member in enum_cls]


class Job(TimestampedModel, Base):
    """Generic, operation-agnostic unit of orchestrated work."""

    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(
            "progress_percent IS NULL OR (progress_percent >= 0 AND progress_percent <= 100)",
            name="job_progress_percent_range",
        ),
        Index("ix_jobs_status_queued_at", "status", "queued_at"),
        Index("ix_jobs_status_lease_expires_at", "status", "lease_expires_at"),
        Index("ix_jobs_job_type_status", "job_type", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    status: Mapped[JobStatus] = mapped_column(
        Enum(
            JobStatus,
            name="job_status",
            values_callable=_enum_values,
            validate_strings=True,
        ),
        nullable=False,
        default=JobStatus.QUEUED,
    )
    queued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    failure_reason: Mapped[JobFailureReason | None] = mapped_column(
        Enum(
            JobFailureReason,
            name="job_failure_reason",
            values_callable=_enum_values,
            validate_strings=True,
        ),
        nullable=True,
    )
    failure_message: Mapped[str | None] = mapped_column(Text(), nullable=True)
    outcome_uncertain: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=false(),
    )
    result_summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    cancellation_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancellation_requested_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    cancellation_reason: Mapped[str | None] = mapped_column(Text(), nullable=True)
    cancellation_acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancellation_cause: Mapped[JobCancellationCause | None] = mapped_column(
        Enum(
            JobCancellationCause,
            name="job_cancellation_cause",
            values_callable=_enum_values,
            validate_strings=True,
        ),
        nullable=True,
    )

    blocking_job_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("jobs.id", ondelete="SET NULL"),
        nullable=True,
    )
    blocking_job_status: Mapped[JobStatus | None] = mapped_column(
        Enum(
            JobStatus,
            name="job_status",
            values_callable=_enum_values,
            validate_strings=True,
        ),
        nullable=True,
    )
    root_cause_job_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("jobs.id", ondelete="SET NULL"),
        nullable=True,
    )

    progress_percent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    progress_step: Mapped[str | None] = mapped_column(String(255), nullable=True)
    progress_current: Mapped[int | None] = mapped_column(Integer, nullable=True)
    progress_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    progress_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    blocking_job: Mapped["Job | None"] = relationship(
        remote_side="Job.id",
        foreign_keys=[blocking_job_id],
    )
    root_cause_job: Mapped["Job | None"] = relationship(
        remote_side="Job.id",
        foreign_keys=[root_cause_job_id],
    )
