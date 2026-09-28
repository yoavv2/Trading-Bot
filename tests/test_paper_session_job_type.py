"""Tests for the ``paper-session`` Job type (OPS-03, OPS-08, Phase 20 Plan 09).

Task 1 covers the service-level ``job_id`` threading behavior of
``run_paper_session``/``run_paper_order_submission`` (D-08/D-09). Task 2
(appended below) covers ``PaperSessionSubmissionSpec``/``PaperSessionJobHandler``.

Fixtures and fakes are reused from ``tests.test_paper_execution`` (the DB
migration fixture, ``FakeExecutionService``/``FakeBrokerClient`` and the
strategy/risk-batch/paper-order seed helpers) rather than duplicated --
``migrated_paper_db`` is re-exported via ``__all__`` following the
``tests/test_job_operations_e2e.py`` precedent so pytest resolves the fixture
from this module's namespace.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select
from tests.test_paper_execution import (
    ExplodingBrokerClient,
    FakeBrokerClient,
    FakeExecutionService,
    _seed_approved_risk_batch,
    _seed_existing_paper_order,
    migrated_paper_db,
)

from trading_platform.core.settings import load_settings
from trading_platform.db.models import Job, StrategyRun, StrategyRunType
from trading_platform.db.session import session_scope
from trading_platform.services.alpaca import BrokerAccountSnapshot, BrokerOrderSnapshot
from trading_platform.services.execution import (
    ExecutionOrderStatus,
    OrderSide,
    build_client_order_id,
    run_paper_session,
)
from trading_platform.services.operator_controls import OperatorControlService

__all__ = ["migrated_paper_db"]


def _seed_job(*, job_type: str = "paper-session") -> uuid.UUID:
    """FK-satisfying ``jobs`` row (``strategy_runs.job_id`` references
    ``jobs.id``, migration 0021) -- a bare ``uuid.uuid4()`` would violate the
    foreign key."""
    with session_scope(load_settings()) as session:
        job = Job(job_type=job_type, payload={})
        session.add(job)
        session.flush()
        return job.id


def _clean_broker_account() -> BrokerAccountSnapshot:
    return BrokerAccountSnapshot(
        cash=Decimal("100000.000000"),
        buying_power=Decimal("100000.000000"),
        equity=Decimal("100000.000000"),
        long_market_value=Decimal("0"),
        short_market_value=Decimal("0"),
        raw_payload={"equity": "100000.000000"},
    )


def test_run_paper_session_threads_job_id_to_both_created_runs(
    migrated_paper_db: str,
) -> None:
    risk_run_id, approved_event_ids = _seed_approved_risk_batch()
    settings = load_settings()
    _seed_existing_paper_order(
        risk_run_id=risk_run_id,
        risk_event_id=approved_event_ids["AAPL"],
        symbol="AAPL",
        session_date=date(2024, 1, 5),
        status="pending_submission",
        broker_order_id=None,
        broker_status=None,
    )
    execution_service = FakeExecutionService()
    job_id = _seed_job()

    report = run_paper_session(
        "trend_following_daily",
        as_of_session=date(2024, 1, 5),
        settings=settings,
        execution_service=execution_service,
        job_id=job_id,
        broker_client=FakeBrokerClient(
            orders=[
                BrokerOrderSnapshot(
                    broker_order_id="recovered-aapl-001",
                    client_order_id=build_client_order_id(
                        prefix=settings.execution.client_order_id_prefix,
                        strategy_id="trend_following_daily",
                        session_date=date(2024, 1, 5),
                        symbol="AAPL",
                        side=OrderSide.BUY,
                        quantity=Decimal("10.000000"),
                    ),
                    symbol="AAPL",
                    side=OrderSide.BUY,
                    quantity=Decimal("10.000000"),
                    status=ExecutionOrderStatus.PENDING,
                    broker_status="new",
                    submitted_at=datetime(2024, 1, 5, 14, 35, tzinfo=UTC),
                    filled_at=None,
                    canceled_at=None,
                    updated_at=datetime(2024, 1, 5, 14, 35, tzinfo=UTC),
                    raw_payload={"id": "recovered-aapl-001", "status": "new"},
                )
            ],
            fills=[],
            positions=[],
            account=_clean_broker_account(),
        ),
    )

    assert report.action == "submitted_missing_orders"
    assert report.execution_run_id is not None
    assert report.reconciliation_run_id is not None

    with session_scope(settings) as session:
        job_linked_runs = (
            session.execute(select(StrategyRun).where(StrategyRun.job_id == job_id))
            .scalars()
            .all()
        )

    assert {run.id for run in job_linked_runs} == {
        uuid.UUID(report.reconciliation_run_id),
        uuid.UUID(report.execution_run_id),
    }
    assert {run.run_type for run in job_linked_runs} == {
        StrategyRunType.RECONCILIATION,
        StrategyRunType.PAPER_EXECUTION,
    }


def test_run_paper_session_blocked_strategy_disabled_threads_job_id_to_execution_run_only(
    migrated_paper_db: str,
) -> None:
    _seed_approved_risk_batch()
    settings = load_settings()
    execution_service = FakeExecutionService()
    control_service = OperatorControlService(settings=settings)
    control_service.disable_strategy(
        "trend_following_daily",
        reason="maintenance window",
        actor="pytest",
        trigger_source="pytest",
    )
    job_id = _seed_job()

    report = run_paper_session(
        "trend_following_daily",
        as_of_session=date(2024, 1, 5),
        settings=settings,
        execution_service=execution_service,
        broker_client=ExplodingBrokerClient(),
        trigger_source="pytest",
        job_id=job_id,
    )

    assert report.action == "blocked_strategy_disabled"
    assert report.reconciliation_run_id is None
    assert report.execution_run_id is not None

    with session_scope(settings) as session:
        job_linked_runs = (
            session.execute(select(StrategyRun).where(StrategyRun.job_id == job_id))
            .scalars()
            .all()
        )

    assert len(job_linked_runs) == 1
    assert job_linked_runs[0].id == uuid.UUID(report.execution_run_id)
    assert job_linked_runs[0].run_type == StrategyRunType.PAPER_EXECUTION


def test_run_paper_session_omitted_job_id_leaves_no_run_linked(
    migrated_paper_db: str,
) -> None:
    _seed_approved_risk_batch()
    settings = load_settings()
    execution_service = FakeExecutionService()

    report = run_paper_session(
        "trend_following_daily",
        as_of_session=date(2024, 1, 5),
        settings=settings,
        execution_service=execution_service,
    )

    assert report.action == "submitted_missing_orders"

    with session_scope(settings) as session:
        runs = session.execute(select(StrategyRun)).scalars().all()

    assert runs
    assert all(run.job_id is None for run in runs)
