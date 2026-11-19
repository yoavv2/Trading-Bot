"""ORM models for the internal signal log (migration ``0034_signals_step1``).

Both tables are append-only: a trigger refuses every UPDATE and DELETE. The closed
vocabularies below are enforced by CHECK constraints in the migration.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from trading_platform.db.base import Base


class SignalEventType(StrEnum):
    ISSUED = "issued"
    EXPIRED = "expired"
    ENTRY_FILLED = "entry_filled"
    RULE_EXIT_SIGNALLED = "rule_exit_signalled"
    CLOSED_STOP = "closed_stop"
    CLOSED_TARGET = "closed_target"
    CLOSED_RULE = "closed_rule"


CLOSING_EVENT_TYPES: frozenset[SignalEventType] = frozenset(
    {SignalEventType.CLOSED_STOP, SignalEventType.CLOSED_TARGET, SignalEventType.CLOSED_RULE}
)
TERMINAL_EVENT_TYPES: frozenset[SignalEventType] = CLOSING_EVENT_TYPES | {SignalEventType.EXPIRED}


class SignalLogEvent(Base):
    """One event of one signal. A signal is (strategy version, asset, signal session)."""

    __tablename__ = "signal_log"
    __table_args__ = (
        UniqueConstraint(
            "strategy_version_id", "asset", "signal_session", "event_type", name="uq_signal_log_signal_event"
        ),
        Index("ix_signal_log_version_asset_event_session", "strategy_version_id", "asset", "event_session"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    strategy_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("strategy_versions.id", ondelete="RESTRICT"), nullable=False
    )
    asset: Mapped[str] = mapped_column(String(32), nullable=False)
    signal_session: Mapped[date] = mapped_column(Date, nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    event_session: Mapped[date] = mapped_column(Date, nullable=False)
    spec_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    # The signal's levels, repeated on every event so a row is readable alone.
    reference_price: Mapped[Decimal] = mapped_column(Numeric(precision=20, scale=6), nullable=False)
    stop_price: Mapped[Decimal] = mapped_column(Numeric(precision=20, scale=6), nullable=False)
    target_price: Mapped[Decimal] = mapped_column(Numeric(precision=20, scale=6), nullable=False)
    price: Mapped[Decimal | None] = mapped_column(Numeric(precision=20, scale=6), nullable=True)
    expiry_reason: Mapped[str | None] = mapped_column(String(48), nullable=True)
    intrabar_order_unknown: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SignalSessionRun(Base):
    """A session the daily engine processed for one (strategy version, asset)."""

    __tablename__ = "signal_session_runs"
    __table_args__ = (
        UniqueConstraint(
            "strategy_version_id", "asset", "session_date", name="uq_signal_session_runs_version_asset_session"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    strategy_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("strategy_versions.id", ondelete="RESTRICT"), nullable=False
    )
    asset: Mapped[str] = mapped_column(String(32), nullable=False)
    session_date: Mapped[date] = mapped_column(Date, nullable=False)
    spec_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
