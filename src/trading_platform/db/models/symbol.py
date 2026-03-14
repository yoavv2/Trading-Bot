"""ORM model for the persisted symbol catalog."""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, Index, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from trading_platform.db.base import Base, TimestampedModel

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trading_platform.db.models.daily_bar import DailyBar


class Symbol(TimestampedModel, Base):
    """Minimal symbol catalog entry backed by provider metadata."""

    __tablename__ = "symbols"
    __table_args__ = (
        Index("ix_symbols_ticker", "ticker"),
        Index("ix_symbols_active", "active"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ticker: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    market: Mapped[str | None] = mapped_column(String(64), nullable=True)
    locale: Mapped[str | None] = mapped_column(String(16), nullable=True)
    primary_exchange: Mapped[str | None] = mapped_column(String(32), nullable=True)
    symbol_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    daily_bars: Mapped[list["DailyBar"]] = relationship(
        back_populates="symbol_ref",
        cascade="all, delete-orphan",
    )
