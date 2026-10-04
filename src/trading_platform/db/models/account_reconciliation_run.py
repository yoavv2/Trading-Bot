"""ORM model for owner-less account-level reconciliation results (ACCT-01, R-31, J-3).

The result of reconciling the WHOLE broker account is not any one owner's, so
this table has no owner reference of any kind. The only relationship is the
optional originating Job.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from trading_platform.db.base import Base, TimestampedModel


class AccountReconciliationScope(StrEnum):
    """Closed scope set; the database CHECK enforces exactly this."""

    ACCOUNT = "account"


class AccountReconciliationStatus(StrEnum):
    """Closed status set; the database CHECK enforces exactly these."""

    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


_SCOPE_VALUES_SQL = ", ".join(f"'{member.value}'" for member in AccountReconciliationScope)
_STATUS_VALUES_SQL = ", ".join(f"'{member.value}'" for member in AccountReconciliationStatus)


class AccountReconciliationRun(TimestampedModel, Base):
    """One stored account-level reconciliation result (no owner reference)."""

    __tablename__ = "account_reconciliation_runs"
    __table_args__ = (
        # Naming convention renders ck_account_reconciliation_runs_<name>.
        CheckConstraint(f"scope IN ({_SCOPE_VALUES_SQL})", name="scope"),
        CheckConstraint(f"status IN ({_STATUS_VALUES_SQL})", name="status"),
        Index("ix_account_reconciliation_runs_completed_at", "completed_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("jobs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    scope: Mapped[str] = mapped_column(
        String(16), nullable=False, default="account", server_default="account"
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default="pending"
    )
    trigger_source: Mapped[str] = mapped_column(String(64), nullable=False)
    as_of_session: Mapped[date | None] = mapped_column(Date(), nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    blocks_execution: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    finding_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    blocking_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    findings: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    account_divergence: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    unexplained_exposure: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    classification_summary: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    unresolved_reasons: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    result_summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text(), nullable=True)
