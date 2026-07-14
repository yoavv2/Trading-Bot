"""ORM model for normalized paper-order fills synced from the broker."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Numeric, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from trading_platform.db.base import Base, TimestampedModel

if TYPE_CHECKING:
    from trading_platform.db.models.paper_order import PaperOrder
    from trading_platform.db.models.symbol import Symbol


class PaperFill(TimestampedModel, Base):
    """One broker-reported fill event mapped back to a persisted paper order."""

    __tablename__ = "paper_fills"
    __table_args__ = (
        UniqueConstraint("broker_fill_id", name="uq_paper_fills_broker_fill_id"),
        Index("ix_paper_fills_paper_order_id_filled_at", "paper_order_id", "filled_at"),
        Index("ix_paper_fills_symbol_id_filled_at", "symbol_id", "filled_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    paper_order_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("paper_orders.id", ondelete="CASCADE"),
        nullable=False,
    )
    symbol_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("symbols.id", ondelete="CASCADE"),
        nullable=False,
    )
    broker_fill_id: Mapped[str] = mapped_column(String(64), nullable=False)
    broker_order_id: Mapped[str] = mapped_column(String(64), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(precision=20, scale=6), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(precision=20, scale=6), nullable=False)
    filled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    broker_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    paper_order: Mapped["PaperOrder"] = relationship(back_populates="fills")
    symbol_ref: Mapped["Symbol"] = relationship()
