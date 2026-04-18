"""ORM model and enums for append-only paper-order lifecycle transitions."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, Enum, ForeignKey, Index, JSON, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from trading_platform.db.base import Base, TimestampedModel

if TYPE_CHECKING:
    from trading_platform.db.models.paper_order import PaperOrder


class OrderLifecycleState(StrEnum):
    PENDING_SUBMISSION = "pending_submission"
    SUBMISSION_FAILED = "submission_failed"
    SUBMITTED = "submitted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


class OrderTransitionEventType(StrEnum):
    LEGACY_IMPORTED = "legacy_imported"
    INTENT_REGISTERED = "intent_registered"
    RETRY_REQUESTED = "retry_requested"
    SUBMISSION_FAILED = "submission_failed"
    BROKER_ACKNOWLEDGED = "broker_acknowledged"
    BROKER_PARTIALLY_FILLED = "broker_partially_filled"
    BROKER_FILLED = "broker_filled"
    BROKER_CANCELED = "broker_canceled"
    BROKER_REJECTED = "broker_rejected"
    BROKER_EXPIRED = "broker_expired"
    BROKER_STATUS_UNKNOWN = "broker_status_unknown"


class OrderTransitionOutcome(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


def _enum_values(enum_cls: type[StrEnum]) -> list[str]:
    return [member.value for member in enum_cls]


class OrderEvent(TimestampedModel, Base):
    """Durable append-only record of every accepted or rejected order transition."""

    __tablename__ = "order_events"
    __table_args__ = (
        Index("ix_order_events_paper_order_id_event_at", "paper_order_id", "event_at"),
        Index("ix_order_events_strategy_run_id_event_at", "strategy_run_id", "event_at"),
        Index("ix_order_events_paper_order_id_outcome", "paper_order_id", "outcome"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    paper_order_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("paper_orders.id", ondelete="CASCADE"),
        nullable=False,
    )
    strategy_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("strategy_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    from_state: Mapped[OrderLifecycleState] = mapped_column(
        Enum(
            OrderLifecycleState,
            name="order_lifecycle_state",
            values_callable=_enum_values,
            validate_strings=True,
        ),
        nullable=False,
    )
    event_type: Mapped[OrderTransitionEventType] = mapped_column(
        Enum(
            OrderTransitionEventType,
            name="order_event_type",
            values_callable=_enum_values,
            validate_strings=True,
        ),
        nullable=False,
    )
    to_state: Mapped[OrderLifecycleState] = mapped_column(
        Enum(
            OrderLifecycleState,
            name="order_lifecycle_state",
            values_callable=_enum_values,
            validate_strings=True,
        ),
        nullable=False,
    )
    outcome: Mapped[OrderTransitionOutcome] = mapped_column(
        Enum(
            OrderTransitionOutcome,
            name="order_event_outcome",
            values_callable=_enum_values,
            validate_strings=True,
        ),
        nullable=False,
    )
    event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    paper_order: Mapped["PaperOrder"] = relationship(back_populates="order_events")
