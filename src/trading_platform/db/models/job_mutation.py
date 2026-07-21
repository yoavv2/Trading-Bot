"""Persistence model for endpoint-scoped Job mutation idempotency."""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from trading_platform.db.base import Base, TimestampedModel


class JobMutation(TimestampedModel, Base):
    """Durable idempotency identity bound to the original Job mutation."""

    __tablename__ = "job_mutations"
    __table_args__ = (
        UniqueConstraint(
            "endpoint_id",
            "idempotency_key",
            name="uq_job_mutations_endpoint_key",
        ),
        Index("ix_job_mutations_job_id", "job_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    endpoint_id: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("jobs.id", ondelete="RESTRICT"),
        nullable=False,
    )
