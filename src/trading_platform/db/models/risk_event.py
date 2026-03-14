"""ORM model for persisted risk-evaluation decisions."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import Date, ForeignKey, Index, JSON, Numeric, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from trading_platform.db.base import Base, TimestampedModel

if TYPE_CHECKING:
    from trading_platform.db.models.strategy_run import StrategyRun
    from trading_platform.db.models.symbol import Symbol


class RiskEvent(TimestampedModel, Base):
    """One persisted decision for a signal evaluated by the risk pipeline."""

    __tablename__ = "risk_events"
    __table_args__ = (
        UniqueConstraint(
            "strategy_run_id",
            "symbol_id",
            "session_date",
            "signal_direction",
            name="uq_risk_events_run_symbol_session_direction",
        ),
        Index("ix_risk_events_strategy_run_id_session_date", "strategy_run_id", "session_date"),
        Index("ix_risk_events_decision_code", "decision_code"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    strategy_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("strategy_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    symbol_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("symbols.id", ondelete="CASCADE"),
        nullable=False,
    )
    session_date: Mapped[date] = mapped_column(Date, nullable=False)
    signal_direction: Mapped[str] = mapped_column(String(16), nullable=False)
    signal_reason: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    decision_code: Mapped[str] = mapped_column(String(64), nullable=False)
    decision_reason: Mapped[str] = mapped_column(Text(), nullable=False)
    reference_price: Mapped[Decimal] = mapped_column(Numeric(precision=20, scale=6), nullable=False)
    proposed_quantity: Mapped[Decimal | None] = mapped_column(Numeric(precision=20, scale=6), nullable=True)
    proposed_notional: Mapped[Decimal | None] = mapped_column(Numeric(precision=20, scale=6), nullable=True)
    risk_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    strategy_run: Mapped["StrategyRun"] = relationship(back_populates="risk_events")
    symbol_ref: Mapped["Symbol"] = relationship()
