"""ORM model for normalized daily price bars."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from trading_platform.db.base import Base, TimestampedModel

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trading_platform.db.models.symbol import Symbol


class DailyBar(TimestampedModel, Base):
    """One normalized price bar per symbol, session date, adjusted flag, and provider."""

    __tablename__ = "daily_bars"
    __table_args__ = (
        UniqueConstraint(
            "symbol_id",
            "session_date",
            "adjusted",
            "provider",
            name="uq_daily_bars_symbol_session_adjusted_provider",
        ),
        Index("ix_daily_bars_symbol_id_session_date", "symbol_id", "session_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    symbol_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("symbols.id", ondelete="CASCADE"),
        nullable=False,
    )
    session_date: Mapped[date] = mapped_column(Date, nullable=False)
    open: Mapped[Decimal] = mapped_column(Numeric(precision=20, scale=6), nullable=False)
    high: Mapped[Decimal] = mapped_column(Numeric(precision=20, scale=6), nullable=False)
    low: Mapped[Decimal] = mapped_column(Numeric(precision=20, scale=6), nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric(precision=20, scale=6), nullable=False)
    volume: Mapped[int] = mapped_column(Integer, nullable=False)
    vwap: Mapped[Decimal | None] = mapped_column(Numeric(precision=20, scale=6), nullable=True)
    trade_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    adjusted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="polygon")
    provider_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    symbol_ref: Mapped["Symbol"] = relationship(
        back_populates="daily_bars",
        foreign_keys=[symbol_id],
    )
