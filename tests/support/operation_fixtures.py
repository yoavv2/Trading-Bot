"""Test-only builders for execution operations (REC-02, 20.1-11).

Direct ORM writes: production code never seeds operations this way. Every function takes an
open ``Session`` (the caller owns the transaction).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.orm import Session
from tests.support.paper_ownership import set_active_paper_strategy
from tests.support.recovery_fixtures import OWNER, SESSION_DATE, strategy_row

from trading_platform.core.settings import load_settings
from trading_platform.db.models import (
    AttemptOutcomeClass,
    ExecutionOperation,
    ExecutionOperationIntent,
    ExecutionOperationJob,
    Job,
    JobStatus,
    OrderLifecycleState,
    OrderSubmissionAttempt,
    PaperOrder,
    RiskEvent,
    StrategyRun,
    StrategyRunStatus,
    StrategyRunType,
    Symbol,
    SystemControl,
)
from trading_platform.db.models.system_control import (
    GLOBAL_KILL_SWITCH_NAME,
    KillSwitchState,
)
from trading_platform.services.calendar import upsert_market_sessions


def seed_risk_run(
    session: Session, strategy_id: str = OWNER, *, session_date: date = SESSION_DATE
) -> StrategyRun:
    """A succeeded risk-evaluation run (the pinned risk run of an operation)."""

    strategy = strategy_row(session, strategy_id)
    run = StrategyRun(
        strategy_id=strategy.id,
        run_type=StrategyRunType.RISK_EVALUATION,
        status=StrategyRunStatus.SUCCEEDED,
        trigger_source="tests",
        parameters_snapshot={"as_of_session": session_date.isoformat()},
        result_summary={},
    )
    session.add(run)
    session.flush()
    return run


def seed_symbol(session: Session, ticker: str = "AAPL") -> Symbol:
    symbol = session.execute(select(Symbol).where(Symbol.ticker == ticker)).scalar_one_or_none()
    if symbol is None:
        symbol = Symbol(ticker=ticker, active=True)
        session.add(symbol)
        session.flush()
    return symbol


def seed_operation_job(
    session: Session,
    *,
    strategy_id: str | None = OWNER,
    status: JobStatus = JobStatus.SUCCEEDED,
    payload: dict[str, object] | None = None,
    lease_owner: str | None = None,
    lease_expires_at: datetime | None = None,
) -> Job:
    body: dict[str, object] = dict(payload or {})
    if strategy_id is not None:
        body.setdefault("strategy_id", strategy_id)
    job = Job(
        job_type="paper-session",
        payload=body,
        status=status,
        lease_owner=lease_owner,
        lease_expires_at=lease_expires_at,
    )
    session.add(job)
    session.flush()
    return job


@dataclass(frozen=True)
class SeededIntent:
    row: ExecutionOperationIntent
    order: PaperOrder | None


def seed_operation(
    session: Session,
    *,
    strategy_id: str = OWNER,
    state: str = "paused",
    reason: str | None = "awaiting_reconciliation",
    session_date: date = SESSION_DATE,
    risk_run: StrategyRun | None = None,
    epoch: int = 0,
    executor_job: Job | None = None,
    jobs: Sequence[tuple[Job, str]] = (),
) -> ExecutionOperation:
    strategy = strategy_row(session, strategy_id)
    run = risk_run or seed_risk_run(session, strategy_id, session_date=session_date)
    operation = ExecutionOperation(
        strategy_id=strategy.id,
        as_of_session=session_date,
        risk_run_id=run.id,
        state=state,
        reason=reason,
        execution_epoch=epoch,
        executor_job_id=executor_job.id if executor_job is not None else None,
    )
    session.add(operation)
    session.flush()
    for job, mode in jobs:
        session.add(ExecutionOperationJob(operation_id=operation.id, job_id=job.id, mode=mode))
    session.flush()
    return operation


def seed_operation_intent(
    session: Session,
    operation: ExecutionOperation,
    *,
    sequence: int = 1,
    ticker: str = "AAPL",
    side: str = "buy",
    quantity: str = "10",
    order_status: OrderLifecycleState | None = None,
    attempts: Sequence[AttemptOutcomeClass | None] = (),
    broker_order_id: str | None = None,
    broker_status: str | None = None,
    disposition: str = "open",
    with_order: bool | None = None,
) -> SeededIntent:
    """A pinned intent; a PaperOrder is created when ``order_status`` or attempts are given."""

    symbol = seed_symbol(session, ticker)
    order: PaperOrder | None = None
    if with_order is None:
        with_order = order_status is not None or bool(attempts) or broker_order_id is not None
    client_order_id = f"tp-{uuid.uuid4().hex[:20]}"
    if with_order:
        paper_run = StrategyRun(
            strategy_id=operation.strategy_id,
            run_type=StrategyRunType.PAPER_EXECUTION,
            status=StrategyRunStatus.RUNNING,
            trigger_source="tests",
            parameters_snapshot={},
            result_summary={},
        )
        session.add(paper_run)
        session.flush()
        risk_event = RiskEvent(
            strategy_run_id=operation.risk_run_id,
            symbol_id=symbol.id,
            session_date=operation.as_of_session,
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
            strategy_run_id=paper_run.id,
            source_risk_event_id=risk_event.id,
            symbol_id=symbol.id,
            intended_session_date=operation.as_of_session,
            side=side,
            quantity=Decimal(quantity),
            intent_hash=uuid.uuid4().hex,
            client_order_id=client_order_id,
            status=order_status or OrderLifecycleState.PENDING_SUBMISSION,
            broker_order_id=broker_order_id,
            broker_status=broker_status,
            broker_payload={},
        )
        session.add(order)
        session.flush()
        stamp = datetime(2026, 10, 1, 12, 1, tzinfo=UTC)
        for number, outcome in enumerate(attempts, start=1):
            session.add(
                OrderSubmissionAttempt(
                    paper_order_id=order.id,
                    strategy_run_id=paper_run.id,
                    attempt_number=number,
                    started_at=stamp,
                    completed_at=None if outcome is None else stamp,
                    outcome_class=None if outcome is None else outcome.value,
                )
            )
        session.flush()
    row = ExecutionOperationIntent(
        operation_id=operation.id,
        sequence=sequence,
        symbol_id=symbol.id,
        side=side,
        quantity=Decimal(quantity),
        reference_price=Decimal("100"),
        client_order_id=order.client_order_id if order is not None else client_order_id,
        paper_order_id=order.id if order is not None else None,
        decision_fingerprint=uuid.uuid4().hex + uuid.uuid4().hex,
        prior_execution_refs=[],
        disposition=disposition,
    )
    session.add(row)
    session.flush()
    return SeededIntent(row=row, order=order)


def arrange_sendable_gate(session: Session, *, strategy_id: str = OWNER, now: datetime) -> None:
    """Arrange everything transaction T1 re-reads (20.1-20) so a send is authorized at ``now``.

    The strategy row is enabled and is the explicit paper owner, the global kill switch is
    armed, and persisted calendar rows cover ``now`` (the evaluation session's execution window
    is open when ``now`` lies inside it). The CALLER patches ``clock.now_utc`` to ``now``: T1
    judges the window against the application clock. Direct writes, tests only.
    """

    strategy_row(session, strategy_id, enabled=True)
    set_active_paper_strategy(session, strategy_id)
    session.execute(
        update(SystemControl)
        .where(SystemControl.name == GLOBAL_KILL_SWITCH_NAME)
        .values(state=KillSwitchState.ARMED)
    )
    exchange = load_settings().market_data.calendar.exchange
    day = now.date()
    upsert_market_sessions(session, day - timedelta(days=14), day + timedelta(days=14), exchange)
    session.flush()
