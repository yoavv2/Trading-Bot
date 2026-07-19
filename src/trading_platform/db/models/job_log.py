"""ORM model for append-only structured Job logs (JOB-07, D-13, D-14).

D-13: log ordering is deterministic via the ``sequence`` column, not
``logged_at`` -- timestamps can collide within a single Job, so ``sequence``
is the only reliable per-Job ordering key.

D-14: Phase 17 adds no automatic pruning, compaction, or TTL. Rows remain
queryable for the lifetime of the owning Job record.

``context`` must only ever be written after passing through
``trading_platform.core.log_sanitizer.sanitize`` -- that obligation is
enforced at the single write path added in plan 17-04, not by this model.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from trading_platform.db.base import Base, TimestampedModel

if TYPE_CHECKING:
    from trading_platform.db.models.job import Job


class JobLog(TimestampedModel, Base):
    """A single append-only structured log line emitted during Job execution."""

    __tablename__ = "job_logs"
    __table_args__ = (
        UniqueConstraint("job_id", "sequence", name="uq_job_logs_job_id_sequence"),
        Index("ix_job_logs_job_id_sequence", "job_id", "sequence"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    logged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    event_code: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[str] = mapped_column(Text(), nullable=False)
    handler_type: Mapped[str] = mapped_column(String(64), nullable=False)
    context: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    job: Mapped["Job"] = relationship(foreign_keys=[job_id])
