"""ORM model for session-level backtest equity history."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Date, ForeignKey, Index, Integer, Numeric, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from trading_platform.db.base import Base, TimestampedModel

if TYPE_CHECKING:
    from trading_platform.db.models.strategy_run import StrategyRun


class BacktestEquitySnapshot(TimestampedModel, Base):
    """One session-level portfolio snapshot for a backtest run."""

    __tablename__ = "backtest_equity_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "strategy_run_id",
            "session_date",
            name="uq_backtest_equity_snapshots_run_session",
        ),
        Index(
            "ix_backtest_equity_snapshots_strategy_run_id_session_date",
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
    session_date: Mapped[date] = mapped_column(Date, nullable=False)
    cash: Mapped[Decimal] = mapped_column(Numeric(precision=20, scale=6), nullable=False)
    gross_exposure: Mapped[Decimal] = mapped_column(Numeric(precision=20, scale=6), nullable=False)
    total_equity: Mapped[Decimal] = mapped_column(Numeric(precision=20, scale=6), nullable=False)
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric(precision=20, scale=6), nullable=False)
    unrealized_pnl: Mapped[Decimal] = mapped_column(Numeric(precision=20, scale=6), nullable=False)
    open_positions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    strategy_run: Mapped["StrategyRun"] = relationship(back_populates="backtest_equity_snapshots")
