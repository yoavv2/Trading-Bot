"""Test-only builders for uncertain-outcome recovery state (REC-01, 20.1-10).

Direct ORM writes: production code never seeds Jobs, runs, orders, attempts or
reconciliation results this way. Every function takes an open ``Session`` (the caller owns
the transaction) so a test can arrange a whole scenario in one ``session_scope``.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session
from tests.support.paper_ownership import seed_strategy

from trading_platform.core.settings import load_settings
from trading_platform.db.models import (
    AccountReconciliationRun,
    AttemptOutcomeClass,
    ExternalBrokerActivity,
    Job,
    JobStatus,
    OrderLifecycleState,
    OrderSubmissionAttempt,
    PaperOrder,
    RiskEvent,
    Strategy,
    StrategyRun,
    StrategyRunStatus,
    StrategyRunType,
    Symbol,
)
from trading_platform.strategies.registry import build_default_registry

OWNER = "trend_following_daily"
OTHER = "donchian_breakout_daily"
SESSION_DATE = date(2024, 1, 5)

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def at(minutes: int) -> datetime:
    """``T0`` plus ``minutes`` (a readable, deterministic timeline for scenarios)."""

    from datetime import timedelta

    return T0 + timedelta(minutes=minutes)


def strategy_row(session: Session, strategy_id: str = OWNER, *, enabled: bool = True) -> Strategy:
    metadata = build_default_registry(load_settings()).resolve(strategy_id).metadata
    return seed_strategy(session, metadata, enabled=enabled)


def seed_job(
    session: Session,
    *,
    job_type: str = "paper-session",
    strategy_id: str | None = OWNER,
    completed_at: datetime | None = None,
    uncertain: bool = True,
    status: JobStatus = JobStatus.FAILED,
    payload: dict[str, Any] | None = None,
) -> Job:
    """A terminal Job. ``strategy_id=None`` seeds an account-level Job (no payload strategy)."""

    body: dict[str, Any] = dict(payload or {})
    if strategy_id is not None:
        body.setdefault("strategy_id", strategy_id)
    job = Job(
        job_type=job_type,
        payload=body,
        status=status,
        completed_at=completed_at if completed_at is not None else at(0),
        outcome_uncertain=uncertain,
    )
    session.add(job)
    session.flush()
    return job


def seed_paper_run(
    session: Session, job: Job | None, strategy_id: str = OWNER, *, trigger_source: str = "job"
) -> StrategyRun:
    """A ``paper_execution`` run linked to ``job`` (the first persisted write of a session)."""

    strategy = strategy_row(session, strategy_id)
    run = StrategyRun(
        strategy_id=strategy.id,
        job_id=job.id if job is not None else None,
        run_type=StrategyRunType.PAPER_EXECUTION,
        status=StrategyRunStatus.FAILED if job is not None else StrategyRunStatus.SUCCEEDED,
        trigger_source=trigger_source,
        parameters_snapshot={},
        result_summary={},
    )
    session.add(run)
    session.flush()
    return run


def seed_intent(
    session: Session,
    run: StrategyRun,
    *,
    status: OrderLifecycleState = OrderLifecycleState.UNKNOWN,
    attempts: Sequence[AttemptOutcomeClass | None] = (AttemptOutcomeClass.AMBIGUOUS,),
    broker_order_id: str | None = None,
    broker_status: str | None = None,
    ticker: str = "AAPL",
    quantity: str = "10",
    side: str = "buy",
    created_at: datetime | None = None,
) -> PaperOrder:
    """A registered intent with an explicit attempt history (``None`` = a crash-left row)."""

    symbol = session.execute(select(Symbol).where(Symbol.ticker == ticker)).scalar_one_or_none()
    if symbol is None:
        symbol = Symbol(ticker=ticker, active=True)
        session.add(symbol)
        session.flush()
    risk_event = RiskEvent(
        strategy_run_id=run.id,
        symbol_id=symbol.id,
        session_date=SESSION_DATE,
        signal_direction="long",
        signal_reason="trend_entry",
        outcome="approved",
        decision_code="approved",
        decision_reason="Approved for paper execution.",
        reference_price=Decimal("100.000000"),
        proposed_quantity=Decimal(quantity),
        proposed_notional=Decimal("1000.000000"),
        risk_metadata={},
    )
    session.add(risk_event)
    session.flush()
    order = PaperOrder(
        strategy_run_id=run.id,
        source_risk_event_id=risk_event.id,
        symbol_id=symbol.id,
        intended_session_date=SESSION_DATE,
        side=side,
        quantity=Decimal(quantity),
        intent_hash=uuid.uuid4().hex,
        client_order_id=f"tp-{uuid.uuid4().hex[:20]}",
        status=status,
        broker_order_id=broker_order_id,
        broker_status=broker_status,
        broker_payload={},
    )
    session.add(order)
    session.flush()
    if created_at is not None:
        order.created_at = created_at
        session.flush()
    stamp = at(1)
    for number, outcome in enumerate(attempts, start=1):
        session.add(
            OrderSubmissionAttempt(
                paper_order_id=order.id,
                strategy_run_id=run.id,
                attempt_number=number,
                started_at=stamp,
                completed_at=None if outcome is None else stamp,
                outcome_class=None if outcome is None else outcome.value,
            )
        )
    session.flush()
    return order


def seed_uncertain_session(
    session: Session,
    *,
    strategy_id: str = OWNER,
    status: OrderLifecycleState = OrderLifecycleState.UNKNOWN,
    attempts: Sequence[AttemptOutcomeClass | None] = (AttemptOutcomeClass.AMBIGUOUS,),
    broker_order_id: str | None = None,
    broker_status: str | None = None,
    completed_at: datetime | None = None,
) -> tuple[Job, StrategyRun, PaperOrder]:
    """The canonical ambiguous-POST scenario: an uncertain paper-session Job, its run, one intent."""

    job = seed_job(session, strategy_id=strategy_id, completed_at=completed_at)
    run = seed_paper_run(session, job, strategy_id)
    order = seed_intent(
        session,
        run,
        status=status,
        attempts=attempts,
        broker_order_id=broker_order_id,
        broker_status=broker_status,
    )
    return job, run, order


def seed_account_run(
    session: Session,
    *,
    completed_at: datetime | None,
    blocks: bool = False,
    status: str = "succeeded",
    unresolved_reasons: Sequence[str] = (),
) -> AccountReconciliationRun:
    run = AccountReconciliationRun(
        trigger_source="job",
        status=status if completed_at is not None else "pending",
        completed_at=completed_at,
        blocks_execution=blocks,
        unresolved_reasons=list(unresolved_reasons),
    )
    session.add(run)
    session.flush()
    return run


def seed_strategy_reconciliation(
    session: Session,
    *,
    strategy_id: str = OWNER,
    completed_at: datetime,
    blocks: bool = False,
    trigger_source: str = "job",
    job_type: str | None = "reconciliation",
) -> StrategyRun:
    """A strategy-scope reconciliation run. ``job_type='paper-session'`` + a
    ``<x>_reconciliation`` trigger is the in-session check that never qualifies (R-5)."""

    strategy = strategy_row(session, strategy_id)
    job = None
    if job_type is not None:
        job = Job(
            job_type=job_type,
            payload={"strategy_id": strategy_id},
            status=JobStatus.SUCCEEDED,
            completed_at=completed_at,
        )
        session.add(job)
        session.flush()
    run = StrategyRun(
        strategy_id=strategy.id,
        job_id=job.id if job is not None else None,
        run_type=StrategyRunType.RECONCILIATION,
        status=StrategyRunStatus.SUCCEEDED,
        trigger_source=trigger_source,
        completed_at=completed_at,
        parameters_snapshot={},
        result_summary={"blocks_execution": blocks, "unresolved_reasons": []},
    )
    session.add(run)
    session.flush()
    return run


def seed_recording(session: Session, *, created_at: datetime) -> ExternalBrokerActivity:
    row = ExternalBrokerActivity(
        broker_order_id=f"ext-{uuid.uuid4().hex[:8]}",
        symbol="AAPL",
        side="buy",
        status="filled",
        filled_qty=Decimal("1"),
        origin_tag="external_format",
        reason="recorded",
        order_snapshot={},
        fills=[],
        content_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        created_at=created_at,
    )
    session.add(row)
    session.flush()
    return row
