"""Closed paper-order lifecycle transition boundary."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from trading_platform.core.settings import Settings, load_settings
from trading_platform.db.models import (
    OrderEvent,
    OrderLifecycleState,
    OrderTransitionEventType,
    OrderTransitionOutcome,
    PaperOrder,
)
from trading_platform.db.session import session_scope

_LEGAL_TRANSITIONS: dict[
    OrderLifecycleState,
    dict[OrderTransitionEventType, OrderLifecycleState],
] = {
    OrderLifecycleState.PENDING_SUBMISSION: {
        OrderTransitionEventType.LEGACY_IMPORTED: OrderLifecycleState.PENDING_SUBMISSION,
        OrderTransitionEventType.INTENT_REGISTERED: OrderLifecycleState.PENDING_SUBMISSION,
        OrderTransitionEventType.RETRY_REQUESTED: OrderLifecycleState.PENDING_SUBMISSION,
        OrderTransitionEventType.SUBMISSION_FAILED: OrderLifecycleState.SUBMISSION_FAILED,
        OrderTransitionEventType.BROKER_ACKNOWLEDGED: OrderLifecycleState.SUBMITTED,
        OrderTransitionEventType.BROKER_PARTIALLY_FILLED: OrderLifecycleState.PARTIALLY_FILLED,
        OrderTransitionEventType.BROKER_FILLED: OrderLifecycleState.FILLED,
        OrderTransitionEventType.BROKER_CANCELED: OrderLifecycleState.CANCELED,
        OrderTransitionEventType.BROKER_REJECTED: OrderLifecycleState.REJECTED,
        OrderTransitionEventType.BROKER_EXPIRED: OrderLifecycleState.EXPIRED,
        OrderTransitionEventType.BROKER_STATUS_UNKNOWN: OrderLifecycleState.UNKNOWN,
    },
    OrderLifecycleState.SUBMISSION_FAILED: {
        OrderTransitionEventType.LEGACY_IMPORTED: OrderLifecycleState.SUBMISSION_FAILED,
        OrderTransitionEventType.RETRY_REQUESTED: OrderLifecycleState.PENDING_SUBMISSION,
        OrderTransitionEventType.BROKER_ACKNOWLEDGED: OrderLifecycleState.SUBMITTED,
        OrderTransitionEventType.BROKER_PARTIALLY_FILLED: OrderLifecycleState.PARTIALLY_FILLED,
        OrderTransitionEventType.BROKER_FILLED: OrderLifecycleState.FILLED,
        OrderTransitionEventType.BROKER_CANCELED: OrderLifecycleState.CANCELED,
        OrderTransitionEventType.BROKER_REJECTED: OrderLifecycleState.REJECTED,
        OrderTransitionEventType.BROKER_EXPIRED: OrderLifecycleState.EXPIRED,
        OrderTransitionEventType.BROKER_STATUS_UNKNOWN: OrderLifecycleState.UNKNOWN,
    },
    OrderLifecycleState.SUBMITTED: {
        OrderTransitionEventType.LEGACY_IMPORTED: OrderLifecycleState.SUBMITTED,
        OrderTransitionEventType.BROKER_ACKNOWLEDGED: OrderLifecycleState.SUBMITTED,
        OrderTransitionEventType.BROKER_PARTIALLY_FILLED: OrderLifecycleState.PARTIALLY_FILLED,
        OrderTransitionEventType.BROKER_FILLED: OrderLifecycleState.FILLED,
        OrderTransitionEventType.BROKER_CANCELED: OrderLifecycleState.CANCELED,
        OrderTransitionEventType.BROKER_REJECTED: OrderLifecycleState.REJECTED,
        OrderTransitionEventType.BROKER_EXPIRED: OrderLifecycleState.EXPIRED,
        OrderTransitionEventType.BROKER_STATUS_UNKNOWN: OrderLifecycleState.UNKNOWN,
    },
    OrderLifecycleState.PARTIALLY_FILLED: {
        OrderTransitionEventType.LEGACY_IMPORTED: OrderLifecycleState.PARTIALLY_FILLED,
        OrderTransitionEventType.BROKER_PARTIALLY_FILLED: OrderLifecycleState.PARTIALLY_FILLED,
        OrderTransitionEventType.BROKER_FILLED: OrderLifecycleState.FILLED,
        OrderTransitionEventType.BROKER_CANCELED: OrderLifecycleState.CANCELED,
        OrderTransitionEventType.BROKER_EXPIRED: OrderLifecycleState.EXPIRED,
        OrderTransitionEventType.BROKER_STATUS_UNKNOWN: OrderLifecycleState.UNKNOWN,
    },
    OrderLifecycleState.FILLED: {
        OrderTransitionEventType.LEGACY_IMPORTED: OrderLifecycleState.FILLED,
    },
    OrderLifecycleState.CANCELED: {
        OrderTransitionEventType.LEGACY_IMPORTED: OrderLifecycleState.CANCELED,
    },
    OrderLifecycleState.REJECTED: {
        OrderTransitionEventType.LEGACY_IMPORTED: OrderLifecycleState.REJECTED,
    },
    OrderLifecycleState.EXPIRED: {
        OrderTransitionEventType.LEGACY_IMPORTED: OrderLifecycleState.EXPIRED,
    },
    OrderLifecycleState.UNKNOWN: {
        OrderTransitionEventType.LEGACY_IMPORTED: OrderLifecycleState.UNKNOWN,
        OrderTransitionEventType.BROKER_ACKNOWLEDGED: OrderLifecycleState.SUBMITTED,
        OrderTransitionEventType.BROKER_PARTIALLY_FILLED: OrderLifecycleState.PARTIALLY_FILLED,
        OrderTransitionEventType.BROKER_FILLED: OrderLifecycleState.FILLED,
        OrderTransitionEventType.BROKER_CANCELED: OrderLifecycleState.CANCELED,
        OrderTransitionEventType.BROKER_REJECTED: OrderLifecycleState.REJECTED,
        OrderTransitionEventType.BROKER_EXPIRED: OrderLifecycleState.EXPIRED,
        OrderTransitionEventType.BROKER_STATUS_UNKNOWN: OrderLifecycleState.UNKNOWN,
    },
}


@dataclass(frozen=True)
class OrderTransitionRequest:
    strategy_run_id: uuid.UUID
    event_type: OrderTransitionEventType
    details: dict[str, Any] = field(default_factory=dict)
    event_at: datetime | None = None


@dataclass(frozen=True)
class OrderTransitionResult:
    order_id: uuid.UUID
    event_id: uuid.UUID
    event_type: OrderTransitionEventType
    from_state: OrderLifecycleState
    to_state: OrderLifecycleState
    outcome: OrderTransitionOutcome


class IllegalOrderTransition(RuntimeError):
    """Raised when an order receives an event that is illegal for its current state."""

    def __init__(
        self,
        *,
        order_id: uuid.UUID,
        from_state: OrderLifecycleState,
        event_type: OrderTransitionEventType,
        details: dict[str, Any],
    ) -> None:
        self.order_id = order_id
        self.from_state = from_state
        self.event_type = event_type
        self.details = details
        super().__init__(
            f"Illegal order transition for {order_id}: {from_state.value} -> {event_type.value}"
        )


def resolve_transition_target(
    *,
    from_state: OrderLifecycleState,
    event_type: OrderTransitionEventType,
) -> OrderLifecycleState | None:
    """Return the target state for an event, or None when the transition is illegal."""

    return _LEGAL_TRANSITIONS.get(from_state, {}).get(event_type)


def apply_order_transition(
    order_id: uuid.UUID,
    event: OrderTransitionRequest,
    *,
    settings: Settings | None = None,
    session: Session | None = None,
) -> OrderTransitionResult:
    """Persist one accepted or rejected transition for a paper order."""

    if session is None:
        with session_scope(settings or load_settings()) as owned_session:
            result, error = _apply_transition_in_session(owned_session, order_id, event)
        if error is not None:
            raise error
        return result

    paper_order = session.get(PaperOrder, order_id)
    if paper_order is None:
        raise LookupError(f"Paper order '{order_id}' was not found.")

    next_state = resolve_transition_target(
        from_state=paper_order.status,
        event_type=event.event_type,
    )
    if next_state is None:
        error = IllegalOrderTransition(
            order_id=paper_order.id,
            from_state=paper_order.status,
            event_type=event.event_type,
            details=dict(event.details),
        )
        _persist_rejected_transition(settings or load_settings(), order_id, event, error)
        raise error

    return _persist_transition_event(
        session,
        paper_order=paper_order,
        event=event,
        next_state=next_state,
        outcome=OrderTransitionOutcome.ACCEPTED,
    )


def _apply_transition_in_session(
    session: Session,
    order_id: uuid.UUID,
    event: OrderTransitionRequest,
) -> tuple[OrderTransitionResult, IllegalOrderTransition | None]:
    paper_order = session.get(PaperOrder, order_id)
    if paper_order is None:
        raise LookupError(f"Paper order '{order_id}' was not found.")

    next_state = resolve_transition_target(
        from_state=paper_order.status,
        event_type=event.event_type,
    )
    if next_state is None:
        error = IllegalOrderTransition(
            order_id=paper_order.id,
            from_state=paper_order.status,
            event_type=event.event_type,
            details=dict(event.details),
        )
        result = _persist_transition_event(
            session,
            paper_order=paper_order,
            event=event,
            next_state=paper_order.status,
            outcome=OrderTransitionOutcome.REJECTED,
            rejection_reason=str(error),
        )
        return result, error

    return (
        _persist_transition_event(
            session,
            paper_order=paper_order,
            event=event,
            next_state=next_state,
            outcome=OrderTransitionOutcome.ACCEPTED,
        ),
        None,
    )


def _persist_rejected_transition(
    settings: Settings,
    order_id: uuid.UUID,
    event: OrderTransitionRequest,
    error: IllegalOrderTransition,
) -> None:
    with session_scope(settings) as rejection_session:
        paper_order = rejection_session.get(PaperOrder, order_id)
        if paper_order is None:
            raise LookupError(f"Paper order '{order_id}' was not found.")

        _persist_transition_event(
            rejection_session,
            paper_order=paper_order,
            event=event,
            next_state=paper_order.status,
            outcome=OrderTransitionOutcome.REJECTED,
            rejection_reason=str(error),
        )


def _persist_transition_event(
    session: Session,
    *,
    paper_order: PaperOrder,
    event: OrderTransitionRequest,
    next_state: OrderLifecycleState,
    outcome: OrderTransitionOutcome,
    rejection_reason: str | None = None,
) -> OrderTransitionResult:
    event_at = event.event_at or datetime.now(UTC)
    from_state = paper_order.status
    details = dict(event.details)
    if rejection_reason is not None:
        details["rejected_transition"] = {
            "order_id": str(paper_order.id),
            "from_state": from_state.value,
            "event_type": event.event_type.value,
            "reason": rejection_reason,
        }

    event_row = OrderEvent(
        paper_order_id=paper_order.id,
        strategy_run_id=event.strategy_run_id,
        from_state=from_state,
        event_type=event.event_type,
        to_state=next_state,
        outcome=outcome,
        event_at=event_at,
        details=details,
    )
    session.add(event_row)
    if outcome == OrderTransitionOutcome.ACCEPTED:
        paper_order.status = next_state
    session.flush()

    return OrderTransitionResult(
        order_id=paper_order.id,
        event_id=event_row.id,
        event_type=event.event_type,
        from_state=from_state,
        to_state=next_state,
        outcome=outcome,
    )
