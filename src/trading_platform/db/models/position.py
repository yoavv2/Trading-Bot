"""ORM model for persisted live portfolio positions."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Date, DateTime, ForeignKey, Index, Numeric, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from trading_platform.db.base import Base, TimestampedModel

if TYPE_CHECKING:
    from trading_platform.db.models.strategy import Strategy
    from trading_platform.db.models.symbol import Symbol


class Position(TimestampedModel, Base):
    """Current or historical live position for a strategy and symbol."""

    __tablename__ = "positions"
    __table_args__ = (
        Index("ix_positions_strategy_id_status", "strategy_id", "status"),
        Index("ix_positions_symbol_id_status", "symbol_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    strategy_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("strategies.id", ondelete="CASCADE"),
        nullable=False,
    )
    symbol_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("symbols.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    quantity: Mapped[Decimal] = mapped_column(Numeric(precision=20, scale=6), nullable=False)
    average_entry_price: Mapped[Decimal] = mapped_column(Numeric(precision=20, scale=6), nullable=False)
    cost_basis: Mapped[Decimal] = mapped_column(Numeric(precision=20, scale=6), nullable=False)
    opened_session_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    closed_session_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    strategy: Mapped["Strategy"] = relationship()
    symbol_ref: Mapped["Symbol"] = relationship()
