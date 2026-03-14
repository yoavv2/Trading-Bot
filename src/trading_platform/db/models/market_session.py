"""ORM model for persisted exchange trading sessions."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Index, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from trading_platform.db.base import Base, TimestampedModel


class MarketSession(TimestampedModel, Base):
    """One persisted trading session per exchange and date.

    The exchange calendar service (XNYS) is the authoritative source of truth.
    Persisting sessions here allows downstream consumers to detect missing bars
    and reason about expected data completeness without querying the calendar
    library every time.
    """

    __tablename__ = "market_sessions"
    __table_args__ = (
        UniqueConstraint(
            "exchange",
            "session_date",
            name="uq_market_sessions_exchange_date",
        ),
        Index("ix_market_sessions_exchange_date", "exchange", "session_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    exchange: Mapped[str] = mapped_column(String(16), nullable=False)
    session_date: Mapped[date] = mapped_column(Date, nullable=False)
    market_open: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    market_close: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    early_close: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
