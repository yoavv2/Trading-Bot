"""ORM model for explicit Job dependency edges (JOB-05, D-04-D-06).

Per D-06, a Job's dependency set is immutable after submission. Cycle
rejection (and duplicate-edge topology validation beyond the DB-level unique
constraint) lives in ``jobs/dependencies.py`` at submission time, not here.
The ``ck_job_dependencies_job_not_self_dependent`` CheckConstraint on this
model is only the self-dependency backstop -- the last line of defense at
the database layer, not the primary enforcement mechanism.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Index, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from trading_platform.db.base import Base, TimestampedModel

if TYPE_CHECKING:
    from trading_platform.db.models.job import Job


class JobDependency(TimestampedModel, Base):
    """A directed edge: ``job`` depends on ``depends_on_job`` completing first."""

    __tablename__ = "job_dependencies"
    __table_args__ = (
        UniqueConstraint(
            "job_id",
            "depends_on_job_id",
            name="uq_job_dependencies_job_id_depends_on_job_id",
        ),
        CheckConstraint("job_id <> depends_on_job_id", name="job_not_self_dependent"),
        Index("ix_job_dependencies_depends_on_job_id", "depends_on_job_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    depends_on_job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
    )

    job: Mapped["Job"] = relationship(foreign_keys=[job_id])
    depends_on_job: Mapped["Job"] = relationship(foreign_keys=[depends_on_job_id])
