"""ORM model for simulated backtest trades."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Date, ForeignKey, Index, Integer, Numeric, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from trading_platform.db.base import Base, TimestampedModel

if TYPE_CHECKING:
    from trading_platform.db.models.strategy_run import StrategyRun
    from trading_platform.db.models.symbol import Symbol


class BacktestTrade(TimestampedModel, Base):
    """A simulated trade opened and optionally closed within a backtest run."""

    __tablename__ = "backtest_trades"
    __table_args__ = (
        Index("ix_backtest_trades_strategy_run_id_status", "strategy_run_id", "status"),
        Index("ix_backtest_trades_strategy_run_id_symbol_id", "strategy_run_id", "symbol_id"),
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
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    quantity: Mapped[Decimal] = mapped_column(Numeric(precision=20, scale=6), nullable=False)
    entry_signal_session: Mapped[date] = mapped_column(Date, nullable=False)
    entry_fill_session: Mapped[date] = mapped_column(Date, nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(precision=20, scale=6), nullable=False)
    entry_commission: Mapped[Decimal] = mapped_column(
        Numeric(precision=20, scale=6),
        nullable=False,
        default=Decimal("0"),
    )
    entry_slippage: Mapped[Decimal] = mapped_column(
        Numeric(precision=20, scale=6),
        nullable=False,
        default=Decimal("0"),
    )
    exit_signal_session: Mapped[date | None] = mapped_column(Date, nullable=True)
    exit_fill_session: Mapped[date | None] = mapped_column(Date, nullable=True)
    exit_price: Mapped[Decimal | None] = mapped_column(Numeric(precision=20, scale=6), nullable=True)
    exit_commission: Mapped[Decimal] = mapped_column(
        Numeric(precision=20, scale=6),
        nullable=False,
        default=Decimal("0"),
    )
    exit_slippage: Mapped[Decimal] = mapped_column(
        Numeric(precision=20, scale=6),
        nullable=False,
        default=Decimal("0"),
    )
    realized_pnl: Mapped[Decimal | None] = mapped_column(Numeric(precision=20, scale=6), nullable=True)
    net_pnl: Mapped[Decimal | None] = mapped_column(Numeric(precision=20, scale=6), nullable=True)
    holding_period_sessions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Research (0031): the actual rounding residual of the entry fill under the run's
    # quantity policy; ``None`` on trading-path rows.
    rounding_slack: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=20, scale=6), nullable=True
    )
    # Signals Step 1 (0034): set only when the strategy declares a risk block (spec v2).
    # The levels belong to the signal that opened the trade and never change.
    reference_price: Mapped[Decimal | None] = mapped_column(Numeric(precision=20, scale=6), nullable=True)
    stop_price: Mapped[Decimal | None] = mapped_column(Numeric(precision=20, scale=6), nullable=True)
    target_price: Mapped[Decimal | None] = mapped_column(Numeric(precision=20, scale=6), nullable=True)
    # The prices the shared trade rules gave, before slippage: the open, the stop or the target.
    rule_entry_price: Mapped[Decimal | None] = mapped_column(Numeric(precision=20, scale=6), nullable=True)
    rule_exit_price: Mapped[Decimal | None] = mapped_column(Numeric(precision=20, scale=6), nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(String(16), nullable=True)  # stop | target | rule
    intrabar_order_unknown: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    strategy_run: Mapped["StrategyRun"] = relationship(back_populates="backtest_trades")
    symbol_ref: Mapped["Symbol"] = relationship()
