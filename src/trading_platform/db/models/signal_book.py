"""ORM models for signal books and publication decisions (migration ``0036_signal_books``).

Both tables are append-only. The publication guard of the migration computes the outcome
the rules require and refuses any other, so the closed vocabulary below is also the only
set of outcomes the database can hold.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from trading_platform.db.base import Base


class PublicationDecision(StrEnum):
    PUBLISHED = "published"
    HELD_BACK_OPEN_CAP = "held_back_open_cap"
    HELD_BACK_LATE = "held_back_late"


class SignalBook(Base):
    """A fixed set of (strategy version, assets) run daily under one cap on open
    published signals."""

    __tablename__ = "signal_books"
    __table_args__ = (UniqueConstraint("name", name="uq_signal_books_name"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("strategy_versions.id", ondelete="RESTRICT"), nullable=False
    )
    assets: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    max_open: Mapped[int] = mapped_column(Integer, nullable=False)
    first_session: Mapped[date] = mapped_column(Date, nullable=False)
    exchange: Mapped[str] = mapped_column(String(16), nullable=False)
    bar_provider: Mapped[str] = mapped_column(String(32), nullable=False)
    bar_adjusted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    config_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SignalPublication(Base):
    """The publication decision of one signal in one book, made once."""

    __tablename__ = "signal_publications"
    __table_args__ = (
        UniqueConstraint("book_id", "asset", "signal_session", name="uq_signal_publications_book_asset_session"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    book_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("signal_books.id", ondelete="RESTRICT"), nullable=False
    )
    asset: Mapped[str] = mapped_column(String(32), nullable=False)
    signal_session: Mapped[date] = mapped_column(Date, nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    # sha256("<signal session>:<asset>"): same-session signals are decided in this order.
    order_key: Mapped[str] = mapped_column(String(64), nullable=False)
    # Published signals of the book that had neither expired nor closed before this decision.
    open_before: Mapped[int] = mapped_column(Integer, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
