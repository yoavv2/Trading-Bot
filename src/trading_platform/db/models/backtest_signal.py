"""ORM model for persisted per-symbol backtest signal evaluations."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import Date, ForeignKey, Index, Integer, JSON, Numeric, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from trading_platform.db.base import Base, TimestampedModel

if TYPE_CHECKING:
    from trading_platform.db.models.strategy_run import StrategyRun
    from trading_platform.db.models.symbol import Symbol


class BacktestSignal(TimestampedModel, Base):
    """One persisted signal evaluation per symbol and session within a backtest run."""

    __tablename__ = "backtest_signals"
    __table_args__ = (
        UniqueConstraint(
            "strategy_run_id",
            "symbol_id",
            "session_date",
            name="uq_backtest_signals_run_symbol_session",
        ),
        Index(
            "ix_backtest_signals_strategy_run_id_session_date",
            "strategy_run_id",
            "session_date",
        ),
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
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric(precision=20, scale=6), nullable=False)
    sma_short: Mapped[Decimal | None] = mapped_column(Numeric(precision=20, scale=6), nullable=True)
    sma_long: Mapped[Decimal | None] = mapped_column(Numeric(precision=20, scale=6), nullable=True)
    bars_available: Mapped[int] = mapped_column(Integer, nullable=False)
    signal_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    strategy_run: Mapped["StrategyRun"] = relationship(back_populates="backtest_signals")
    symbol_ref: Mapped["Symbol"] = relationship()
