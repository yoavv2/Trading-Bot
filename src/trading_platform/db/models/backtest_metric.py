"""ORM model for persisted run-level backtest metrics."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, Integer, Numeric, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from trading_platform.db.base import Base, TimestampedModel

if TYPE_CHECKING:
    from trading_platform.db.models.strategy_run import StrategyRun


class BacktestMetric(TimestampedModel, Base):
    """Materialized backtest metrics derived from persisted run artifacts."""

    __tablename__ = "backtest_metrics"
    __table_args__ = (
        UniqueConstraint("strategy_run_id", name="uq_backtest_metrics_strategy_run_id"),
        Index("ix_backtest_metrics_strategy_run_id", "strategy_run_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    strategy_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("strategy_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    total_return_pct: Mapped[Decimal] = mapped_column(Numeric(precision=20, scale=6), nullable=False)
    max_drawdown_pct: Mapped[Decimal] = mapped_column(Numeric(precision=20, scale=6), nullable=False)
    trade_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    win_rate_pct: Mapped[Decimal] = mapped_column(Numeric(precision=20, scale=6), nullable=False)
    average_win: Mapped[Decimal] = mapped_column(Numeric(precision=20, scale=6), nullable=False)
    average_loss: Mapped[Decimal] = mapped_column(Numeric(precision=20, scale=6), nullable=False)
    profit_factor: Mapped[Decimal] = mapped_column(Numeric(precision=20, scale=6), nullable=False)
    exposure_pct: Mapped[Decimal] = mapped_column(Numeric(precision=20, scale=6), nullable=False)
    average_holding_period_sessions: Mapped[Decimal] = mapped_column(
        Numeric(precision=20, scale=6),
        nullable=False,
    )

    strategy_run: Mapped["StrategyRun"] = relationship()
