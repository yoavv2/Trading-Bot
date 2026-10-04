"""Test-only helpers for paper orders and submission attempt rows (20.1-02).

Direct ORM writes: production code never seeds orders or attempts this way.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from trading_platform.core.settings import Settings
from trading_platform.db.models import (
    AttemptOutcomeClass,
    OrderLifecycleState,
    OrderSubmissionAttempt,
    PaperOrder,
    RiskEvent,
    Strategy,
    StrategyRun,
    StrategyRunType,
    Symbol,
)
from trading_platform.db.session import session_scope


def seed_paper_order_row(
    session: Session,
    *,
    status: OrderLifecycleState = OrderLifecycleState.PENDING_SUBMISSION,
    broker_order_id: str | None = None,
    ticker: str | None = None,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Insert a minimal strategy/run/risk event/order; return (order_id, run_id)."""

    strategy = Strategy(
        strategy_id=f"attempt-probe-{uuid.uuid4().hex[:8]}",
        display_name="Attempt Probe",
        config_reference="attempt_probe.yaml",
    )
    session.add(strategy)
    session.flush()
    run = StrategyRun(strategy_id=strategy.id, run_type=StrategyRunType.PAPER_EXECUTION)
    session.add(run)
    session.flush()
    symbol = Symbol(ticker=ticker or f"T{uuid.uuid4().hex[:6].upper()}", active=True)
    session.add(symbol)
    session.flush()
    session_date = date(2024, 1, 5)
    risk_event = RiskEvent(
        strategy_run_id=run.id,
        symbol_id=symbol.id,
        session_date=session_date,
        signal_direction="long",
        signal_reason="trend_entry",
        outcome="approved",
        decision_code="approved",
        decision_reason="Approved for paper execution.",
        reference_price=Decimal("100.000000"),
        proposed_quantity=Decimal("1.000000"),
        proposed_notional=Decimal("100.000000"),
        risk_metadata={},
    )
    session.add(risk_event)
    session.flush()
    order = PaperOrder(
        strategy_run_id=run.id,
        source_risk_event_id=risk_event.id,
        symbol_id=symbol.id,
        intended_session_date=session_date,
        side="buy",
        quantity=Decimal("1"),
        intent_hash=uuid.uuid4().hex,
        client_order_id=f"tp-{uuid.uuid4().hex[:20]}",
        status=status,
        broker_order_id=broker_order_id,
        broker_payload={},
    )
    session.add(order)
    session.flush()
    return order.id, run.id


def seed_attempt_row(
    settings: Settings,
    paper_order_id: uuid.UUID,
    *,
    number: int,
    outcome: AttemptOutcomeClass | None,
    strategy_run_id: uuid.UUID | None = None,
    http_status: int | None = None,
) -> None:
    """Insert one attempt row directly (outcome None = a crash-left incomplete row)."""

    now = datetime.now(UTC)
    with session_scope(settings) as session:
        session.add(
            OrderSubmissionAttempt(
                paper_order_id=paper_order_id,
                strategy_run_id=strategy_run_id,
                attempt_number=number,
                started_at=now,
                completed_at=None if outcome is None else now,
                outcome_class=None if outcome is None else outcome.value,
                http_status=http_status,
            )
        )
