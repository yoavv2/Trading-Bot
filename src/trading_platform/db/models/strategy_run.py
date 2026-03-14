"""ORM model for persisted dry-run execution records."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, Enum, ForeignKey, Index, JSON, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from trading_platform.db.base import Base, TimestampedModel

if TYPE_CHECKING:
    from trading_platform.db.models.backtest_equity_snapshot import BacktestEquitySnapshot
    from trading_platform.db.models.backtest_signal import BacktestSignal
    from trading_platform.db.models.backtest_trade import BacktestTrade
    from trading_platform.db.models.strategy import Strategy


class StrategyRunType(StrEnum):
    DRY_BOOTSTRAP = "dry_bootstrap"
    BACKTEST = "backtest"


class StrategyRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


def _enum_values(enum_cls: type[StrEnum]) -> list[str]:
    return [member.value for member in enum_cls]


class StrategyRun(TimestampedModel, Base):
    """Minimal persisted execution record for Phase 1 dry runs."""

    __tablename__ = "strategy_runs"
    __table_args__ = (
        Index("ix_strategy_runs_strategy_id_status", "strategy_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    strategy_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_type: Mapped[StrategyRunType] = mapped_column(
        Enum(
            StrategyRunType,
            name="strategy_run_type",
            values_callable=_enum_values,
            validate_strings=True,
        ),
        nullable=False,
        default=StrategyRunType.DRY_BOOTSTRAP,
    )
    status: Mapped[StrategyRunStatus] = mapped_column(
        Enum(
            StrategyRunStatus,
            name="strategy_run_status",
            values_callable=_enum_values,
            validate_strings=True,
        ),
        nullable=False,
        default=StrategyRunStatus.PENDING,
    )
    trigger_source: Mapped[str] = mapped_column(String(64), nullable=False, default="operator_cli")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    parameters_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    result_summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text(), nullable=True)

    strategy: Mapped["Strategy"] = relationship(back_populates="runs")
    backtest_signals: Mapped[list["BacktestSignal"]] = relationship(
        back_populates="strategy_run",
        cascade="all, delete-orphan",
    )
    backtest_trades: Mapped[list["BacktestTrade"]] = relationship(
        back_populates="strategy_run",
        cascade="all, delete-orphan",
    )
    backtest_equity_snapshots: Mapped[list["BacktestEquitySnapshot"]] = relationship(
        back_populates="strategy_run",
        cascade="all, delete-orphan",
    )
