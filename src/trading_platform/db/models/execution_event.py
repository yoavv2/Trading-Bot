"""ORM model for durable execution and reconciliation findings."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from trading_platform.db.base import Base, TimestampedModel

if TYPE_CHECKING:
    from trading_platform.db.models.paper_order import PaperOrder
    from trading_platform.db.models.strategy_run import StrategyRun


class ExecutionEvent(TimestampedModel, Base):
    """A durable operator-visible event for unsafe execution or broker drift."""

    __tablename__ = "execution_events"
    __table_args__ = (
        Index("ix_execution_events_strategy_run_id_event_at", "strategy_run_id", "event_at"),
        Index("ix_execution_events_blocks_execution_event_at", "blocks_execution", "event_at"),
        Index("ix_execution_events_paper_order_id_event_type", "paper_order_id", "event_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    strategy_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("strategy_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    paper_order_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("paper_orders.id", ondelete="SET NULL"),
        nullable=True,
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    blocks_execution: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    message: Mapped[str] = mapped_column(Text(), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    strategy_run: Mapped["StrategyRun"] = relationship(back_populates="execution_events")
    paper_order: Mapped["PaperOrder | None"] = relationship(back_populates="execution_events")
