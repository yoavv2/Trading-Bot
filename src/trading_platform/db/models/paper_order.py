"""ORM model for persisted paper-order submissions."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Index, Integer, JSON, Numeric, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from trading_platform.db.base import Base, TimestampedModel
from trading_platform.db.models.order_event import OrderEvent, OrderLifecycleState, _enum_values

if TYPE_CHECKING:
    from trading_platform.db.models.execution_event import ExecutionEvent
    from trading_platform.db.models.paper_fill import PaperFill
    from trading_platform.db.models.risk_event import RiskEvent
    from trading_platform.db.models.strategy_run import StrategyRun
    from trading_platform.db.models.symbol import Symbol


class PaperOrder(TimestampedModel, Base):
    """Submitted paper order anchored to one execution batch and one approved risk event."""

    __tablename__ = "paper_orders"
    __table_args__ = (
        UniqueConstraint("source_risk_event_id", name="uq_paper_orders_source_risk_event_id"),
        UniqueConstraint("client_order_id", name="uq_paper_orders_client_order_id"),
        UniqueConstraint("broker_order_id", name="uq_paper_orders_broker_order_id"),
        Index("ix_paper_orders_strategy_run_id_status", "strategy_run_id", "status"),
        Index("ix_paper_orders_strategy_run_id_symbol_id", "strategy_run_id", "symbol_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    strategy_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("strategy_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_risk_event_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("risk_events.id", ondelete="RESTRICT"),
        nullable=False,
    )
    symbol_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("symbols.id", ondelete="CASCADE"),
        nullable=False,
    )
    intended_session_date: Mapped[date] = mapped_column(Date, nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(precision=20, scale=6), nullable=False)
    order_type: Mapped[str] = mapped_column(String(16), nullable=False, default="market")
    time_in_force: Mapped[str] = mapped_column(String(16), nullable=False, default="day")
    client_order_id: Mapped[str] = mapped_column(String(64), nullable=False)
    broker_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[OrderLifecycleState] = mapped_column(
        Enum(
            OrderLifecycleState,
            name="order_lifecycle_state",
            values_callable=_enum_values,
            validate_strings=True,
        ),
        nullable=False,
        default=OrderLifecycleState.PENDING_SUBMISSION,
    )
    broker_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submission_attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sync_failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_submission_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_submission_error: Mapped[str | None] = mapped_column(Text(), nullable=True)
    last_sync_error: Mapped[str | None] = mapped_column(Text(), nullable=True)
    filled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_broker_update_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    broker_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    strategy_run: Mapped["StrategyRun"] = relationship(back_populates="paper_orders")
    source_risk_event: Mapped["RiskEvent"] = relationship()
    symbol_ref: Mapped["Symbol"] = relationship()
    fills: Mapped[list["PaperFill"]] = relationship(
        back_populates="paper_order",
        cascade="all, delete-orphan",
    )
    execution_events: Mapped[list["ExecutionEvent"]] = relationship(
        back_populates="paper_order",
        cascade="save-update, merge",
    )
    order_events: Mapped[list["OrderEvent"]] = relationship(
        back_populates="paper_order",
        cascade="all, delete-orphan",
        order_by="OrderEvent.event_at.asc()",
    )
