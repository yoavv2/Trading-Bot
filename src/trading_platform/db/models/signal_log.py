"""ORM models for the internal signal log (migrations ``0034_signals_step1``,
``0035_signals_step2`` and ``0037_signal_inputs``).

Every table is append-only: a trigger refuses every UPDATE and DELETE. The closed
vocabularies below are enforced by CHECK constraints in the migrations, and the order of
a signal's events by the transition guard of 0035.
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
from sqlalchemy.dialects.postgresql import JSONB
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


class SessionSkipReason(StrEnum):
    """Why the daily engine could not process a session (it stays unprocessed)."""

    MISSING_BAR = "missing_bar"
    INTEGRITY_FAILED = "integrity_failed"


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
    # The levels in the price scale of the event session: the issued levels times
    # ``adjustment_factor`` (1 unless a dividend or a split followed the signal session).
    adjustment_factor: Mapped[Decimal | None] = mapped_column(Numeric(precision=24, scale=12), nullable=True)
    effective_stop_price: Mapped[Decimal | None] = mapped_column(Numeric(precision=20, scale=6), nullable=True)
    effective_target_price: Mapped[Decimal | None] = mapped_column(Numeric(precision=20, scale=6), nullable=True)
    # SHA-256 of the market-data reads of the session that wrote the event.
    input_data_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
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
    input_data_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SignalSessionSkip(Base):
    """A session the daily engine could not process, with the reason. The session stays
    unprocessed; one row per (strategy version, asset, session, reason)."""

    __tablename__ = "signal_session_skips"
    __table_args__ = (
        UniqueConstraint(
            "strategy_version_id",
            "asset",
            "session_date",
            "reason",
            name="uq_signal_session_skips_version_asset_session_reason",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    strategy_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("strategy_versions.id", ondelete="RESTRICT"), nullable=False
    )
    asset: Mapped[str] = mapped_column(String(32), nullable=False)
    session_date: Mapped[date] = mapped_column(Date, nullable=False)
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    detail: Mapped[str | None] = mapped_column(String(64), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SignalSessionInput(Base):
    """The bars one processed session was computed from (kept by the daily Job)."""

    __tablename__ = "signal_session_inputs"
    __table_args__ = (
        UniqueConstraint(
            "strategy_version_id", "asset", "session_date", name="uq_signal_session_inputs_version_asset_session"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    strategy_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("strategy_versions.id", ondelete="RESTRICT"), nullable=False
    )
    asset: Mapped[str] = mapped_column(String(32), nullable=False)
    session_date: Mapped[date] = mapped_column(Date, nullable=False)
    inputs: Mapped[dict] = mapped_column(JSONB, nullable=False)
    inputs_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
