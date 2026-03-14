"""ORM model for persisted live account snapshots."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from trading_platform.db.base import Base, TimestampedModel

if TYPE_CHECKING:
    from trading_platform.db.models.strategy import Strategy
    from trading_platform.db.models.strategy_run import StrategyRun


class AccountSnapshot(TimestampedModel, Base):
    """One persisted snapshot of account cash and exposure state."""

    __tablename__ = "account_snapshots"
    __table_args__ = (
        Index("ix_account_snapshots_snapshot_at", "snapshot_at"),
        Index("ix_account_snapshots_strategy_id_snapshot_at", "strategy_id", "snapshot_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    strategy_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("strategies.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("strategy_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    snapshot_source: Mapped[str] = mapped_column(String(32), nullable=False, default="derived")
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    cash: Mapped[Decimal] = mapped_column(Numeric(precision=20, scale=6), nullable=False)
    gross_exposure: Mapped[Decimal] = mapped_column(Numeric(precision=20, scale=6), nullable=False)
    total_equity: Mapped[Decimal] = mapped_column(Numeric(precision=20, scale=6), nullable=False)
    buying_power: Mapped[Decimal] = mapped_column(Numeric(precision=20, scale=6), nullable=False)
    open_positions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    strategy: Mapped["Strategy | None"] = relationship()
    source_run: Mapped["StrategyRun | None"] = relationship()
