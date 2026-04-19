from __future__ import annotations

import os
import re
import sys
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from alembic import command
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.migrate import build_alembic_config
from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.db.models import (
    AccountSnapshot,
    DailyBar,
    ExecutionEvent,
    MarketSession,
    OrderEvent,
    OrderTransitionEventType,
    PaperFill,
    PaperOrder,
    Position,
    RiskEvent,
    Strategy,
    StrategyRun,
    StrategyRunStatus,
    StrategyRunType,
    StrategyStatus,
)
from trading_platform.db.models.symbol import Symbol
from trading_platform.db.session import clear_engine_cache, session_scope
from trading_platform.services.alpaca import (
    BrokerAccountSnapshot,
    BrokerFillSnapshot,
    BrokerOrderSnapshot,
    BrokerPositionSnapshot,
)
from trading_platform.services.bootstrap import ensure_strategy_record
from trading_platform.services.execution import (
    ExecutionOrderStatus,
    ExecutionService,
    OrderIntent,
    OrderSide,
    OrderSubmissionResult,
)
from trading_platform.services.operator_controls import OperatorControlService
from trading_platform.services.paper_execution import (
    build_client_order_id,
    resolve_submission_session,
    run_paper_order_submission,
    run_paper_session,
    sync_paper_state,
)
from trading_platform.strategies.registry import build_default_registry


def _admin_connection_settings() -> dict[str, str]:
    return {
        "host": os.getenv("TRADING_PLATFORM_DATABASE__HOST", "localhost"),
        "port": os.getenv("TRADING_PLATFORM_DATABASE__PORT", "5432"),
        "user": os.getenv("TRADING_PLATFORM_DATABASE__USER", "trading_platform"),
        "password": os.getenv("TRADING_PLATFORM_DATABASE__PASSWORD", "trading_platform"),
        "dbname": os.getenv("TRADING_PLATFORM_ADMIN_DB", "postgres"),
    }


def _connect_admin(params: dict[str, str] | None = None) -> psycopg.Connection:
    params = params or _admin_connection_settings()
    return psycopg.connect(
        host=params["host"],
        port=params["port"],
        user=params["user"],
        password=params["password"],
        dbname=params["dbname"],
        autocommit=True,
    )


def _set_database_env(monkeypatch: pytest.MonkeyPatch, database_name: str) -> None:
    params = _admin_connection_settings()
    monkeypatch.setenv("TRADING_PLATFORM_DATABASE__HOST", params["host"])
    monkeypatch.setenv("TRADING_PLATFORM_DATABASE__PORT", params["port"])
    monkeypatch.setenv("TRADING_PLATFORM_DATABASE__USER", params["user"])
    monkeypatch.setenv("TRADING_PLATFORM_DATABASE__PASSWORD", params["password"])
    monkeypatch.setenv("TRADING_PLATFORM_DATABASE__NAME", database_name)


@pytest.fixture()
def migrated_paper_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    database_name = f"paper_execution_{uuid.uuid4().hex[:8]}"
    admin_params = _admin_connection_settings()

    try:
        with _connect_admin(admin_params) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f'CREATE DATABASE "{database_name}"')
    except psycopg.Error as exc:
        pytest.fail(
            "PostgreSQL is required for tests/test_paper_execution.py. "
            f"Connection error: {exc}"
        )

    _set_database_env(monkeypatch, database_name)
    clear_settings_cache()
    clear_engine_cache()
    command.upgrade(build_alembic_config(), "head")

    try:
        yield database_name
    finally:
        clear_settings_cache()
        clear_engine_cache()
        with _connect_admin(admin_params) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT pg_terminate_backend(pid)
                    FROM pg_stat_activity
                    WHERE datname = %s
                      AND usename = current_user
                      AND pid <> pg_backend_pid()
                    """,
                    (database_name,),
                )
                cursor.execute(f'DROP DATABASE IF EXISTS "{database_name}"')


class FakeExecutionService(ExecutionService):
    def __init__(self) -> None:
        self.submitted_intents: list[OrderIntent] = []

    def describe(self) -> dict[str, object]:
        return {"service": "execution", "status": "available", "provider": "fake"}

    def submit_order(self, intent: OrderIntent) -> OrderSubmissionResult:
        self.submitted_intents.append(intent)
        return OrderSubmissionResult(
            client_order_id=intent.client_order_id,
            broker_order_id=f"fake-{intent.symbol.lower()}-001",
            symbol=intent.symbol,
            side=intent.side,
            quantity=intent.quantity,
            order_type=intent.order_type,
            time_in_force=intent.time_in_force,
            status=ExecutionOrderStatus.PENDING,
            broker_status="new",
            submitted_at=datetime(2024, 1, 5, 14, 35, tzinfo=UTC),
            raw_payload={
                "id": f"fake-{intent.symbol.lower()}-001",
                "client_order_id": intent.client_order_id,
                "symbol": intent.symbol,
                "status": "new",
                "submitted_at": "2024-01-05T14:35:00Z",
            },
        )


class FakeBrokerClient:
    def __init__(
        self,
        *,
        orders: list[BrokerOrderSnapshot],
        fills: list[BrokerFillSnapshot],
        positions: list[BrokerPositionSnapshot],
        account: BrokerAccountSnapshot,
    ) -> None:
        self._orders = orders
        self._fills = fills
        self._positions = positions
        self._account = account

    def close(self) -> None:
        return None

    def list_orders(self) -> list[BrokerOrderSnapshot]:
        return list(self._orders)

    def list_fills(self) -> list[BrokerFillSnapshot]:
        return list(self._fills)

    def list_positions(self) -> list[BrokerPositionSnapshot]:
        return list(self._positions)

    def get_account(self) -> BrokerAccountSnapshot:
        return self._account


class ExplodingBrokerClient:
    def close(self) -> None:
        return None

    def list_orders(self) -> list[BrokerOrderSnapshot]:
        raise AssertionError("paper session should not read broker orders while strategy is disabled")

    def list_fills(self) -> list[BrokerFillSnapshot]:
        raise AssertionError("paper session should not read broker fills while strategy is disabled")

    def list_positions(self) -> list[BrokerPositionSnapshot]:
        raise AssertionError("paper session should not read broker positions while strategy is disabled")

    def get_account(self) -> BrokerAccountSnapshot:
        raise AssertionError("paper session should not read broker account while strategy is disabled")


def _seed_market_data(session_date: date) -> None:
    settings = load_settings()

    with session_scope(settings) as session:
        symbol = Symbol(ticker="AAPL", active=True)
        session.add(symbol)
        session.flush()
        session.add(
            MarketSession(
                exchange=settings.market_data.calendar.exchange,
                session_date=session_date,
                market_open=datetime(2024, 1, 5, 14, 30, tzinfo=UTC),
                market_close=datetime(2024, 1, 5, 21, 0, tzinfo=UTC),
                early_close=False,
            )
        )
        session.add(
            DailyBar(
                symbol_id=symbol.id,
                session_date=session_date,
                open=Decimal("120.000000"),
                high=Decimal("121.000000"),
                low=Decimal("119.000000"),
                close=Decimal("120.500000"),
                volume=1_000_000,
                adjusted=True,
                provider="polygon",
            )
        )


def _seed_approved_risk_batch(*, session_date: date = date(2024, 1, 5)) -> tuple[uuid.UUID, dict[str, uuid.UUID]]:
    settings = load_settings()
    registry = build_default_registry(settings)
    strategy = registry.resolve("trend_following_daily")

    with session_scope(settings) as session:
        strategy_record = ensure_strategy_record(session, strategy.metadata)
        aapl = Symbol(ticker="AAPL", active=True)
        msft = Symbol(ticker="MSFT", active=True)
        session.add_all([aapl, msft])
        session.flush()

        risk_run = StrategyRun(
            strategy_id=strategy_record.id,
            run_type=StrategyRunType.RISK_EVALUATION,
            status=StrategyRunStatus.SUCCEEDED,
            trigger_source="test_suite",
            parameters_snapshot={"as_of_session": session_date.isoformat()},
            result_summary={"stage": "completed", "as_of_session": session_date.isoformat()},
        )
        session.add(risk_run)
        session.flush()

        approved_events = {
            "AAPL": RiskEvent(
                strategy_run_id=risk_run.id,
                symbol_id=aapl.id,
                session_date=session_date,
                signal_direction="long",
                signal_reason="trend_entry",
                outcome="approved",
                decision_code="approved",
                decision_reason="Approved for paper execution.",
                reference_price=Decimal("120.000000"),
                proposed_quantity=Decimal("10.000000"),
                proposed_notional=Decimal("1200.000000"),
                risk_metadata={"remaining_cash": 98800.0},
            ),
            "MSFT": RiskEvent(
                strategy_run_id=risk_run.id,
                symbol_id=msft.id,
                session_date=session_date,
                signal_direction="long",
                signal_reason="trend_entry",
                outcome="approved",
                decision_code="approved",
                decision_reason="Approved for paper execution.",
                reference_price=Decimal("300.000000"),
                proposed_quantity=Decimal("5.000000"),
                proposed_notional=Decimal("1500.000000"),
                risk_metadata={"remaining_cash": 97300.0},
            ),
        }
        session.add_all(list(approved_events.values()))
        session.flush()
        return risk_run.id, {symbol: event.id for symbol, event in approved_events.items()}


def _seed_existing_paper_order(
    *,
    risk_run_id: uuid.UUID,
    risk_event_id: uuid.UUID,
    symbol: str,
    session_date: date,
    status: str = "submitted",
    broker_order_id: str | None = None,
    broker_status: str | None = "new",
    submission_attempt_count: int = 1,
    last_submission_error: str | None = None,
) -> uuid.UUID:
    settings = load_settings()
    registry = build_default_registry(settings)
    strategy = registry.resolve("trend_following_daily")

    resolved_broker_order_id = (
        broker_order_id
        if broker_order_id is not None or status == "pending_submission"
        else f"existing-{symbol.lower()}-001"
    )

    with session_scope(settings) as session:
        strategy_record = ensure_strategy_record(session, strategy.metadata)
        symbol_row = session.execute(select(Symbol).where(Symbol.ticker == symbol)).scalar_one()
        execution_run = StrategyRun(
            strategy_id=strategy_record.id,
            run_type=StrategyRunType.PAPER_EXECUTION,
            status=StrategyRunStatus.SUCCEEDED,
            trigger_source="seed_existing_order",
            parameters_snapshot={"as_of_session": session_date.isoformat(), "requested_risk_run_id": str(risk_run_id)},
            result_summary={"stage": "completed", "as_of_session": session_date.isoformat()},
            completed_at=datetime(2024, 1, 5, 15, 0, tzinfo=UTC),
        )
        session.add(execution_run)
        session.flush()

        session.add(
            PaperOrder(
                strategy_run_id=execution_run.id,
                source_risk_event_id=risk_event_id,
                symbol_id=symbol_row.id,
                intended_session_date=session_date,
                side="buy",
                quantity=Decimal("10.000000"),
                order_type="market",
                time_in_force="day",
                client_order_id=build_client_order_id(
                    prefix=load_settings().execution.client_order_id_prefix,
                    session_date=session_date,
                    symbol=symbol,
                    risk_event_id=risk_event_id,
                ),
                broker_order_id=resolved_broker_order_id,
                status=status,
                broker_status=broker_status,
                submitted_at=datetime(2024, 1, 5, 14, 35, tzinfo=UTC)
                if status != "pending_submission"
                else None,
                submission_attempt_count=submission_attempt_count,
                last_submission_attempt_at=datetime(2024, 1, 5, 14, 35, tzinfo=UTC)
                if submission_attempt_count
                else None,
                last_submission_error=last_submission_error,
                broker_payload={"id": resolved_broker_order_id}
                if resolved_broker_order_id is not None
                else {},
            )
        )
        session.flush()
        return execution_run.id


def _seed_open_position(*, symbol: str, quantity: str) -> None:
    settings = load_settings()
    registry = build_default_registry(settings)
    strategy = registry.resolve("trend_following_daily")

    with session_scope(settings) as session:
        strategy_record = ensure_strategy_record(session, strategy.metadata)
        symbol_row = session.execute(select(Symbol).where(Symbol.ticker == symbol)).scalar_one_or_none()
        if symbol_row is None:
            symbol_row = Symbol(ticker=symbol, active=True)
            session.add(symbol_row)
            session.flush()

        session.add(
            Position(
                strategy_id=strategy_record.id,
                symbol_id=symbol_row.id,
                status="open",
                quantity=Decimal(quantity),
                average_entry_price=Decimal("250.000000"),
                cost_basis=Decimal("1250.000000"),
                opened_session_date=date(2024, 1, 4),
                opened_at=datetime(2024, 1, 4, 14, 35, tzinfo=UTC),
            )
        )


def test_resolve_submission_session_prefers_latest_completed_persisted_session(
    migrated_paper_db: str,
) -> None:
    _seed_market_data(date(2024, 1, 5))
    settings = load_settings()

    resolved = resolve_submission_session(settings=settings, as_of_arg=None)

    assert resolved == date(2024, 1, 5)


def test_run_paper_session_noops_when_all_candidates_already_seeded(
    migrated_paper_db: str,
) -> None:
    risk_run_id, approved_event_ids = _seed_approved_risk_batch()
    existing_run_id = _seed_existing_paper_order(
        risk_run_id=risk_run_id,
        risk_event_id=approved_event_ids["AAPL"],
        symbol="AAPL",
        session_date=date(2024, 1, 5),
    )
    _seed_existing_paper_order(
        risk_run_id=risk_run_id,
        risk_event_id=approved_event_ids["MSFT"],
        symbol="MSFT",
        session_date=date(2024, 1, 5),
    )
    settings = load_settings()
    execution_service = FakeExecutionService()

    report = run_paper_session(
        "trend_following_daily",
        as_of_session=date(2024, 1, 5),
        settings=settings,
        execution_service=execution_service,
    )

    assert report.action == "noop_existing_orders"
    assert report.execution_run_id is None
    assert report.result_summary["existing_count"] == 2
    assert execution_service.submitted_intents == []

    with session_scope(settings) as session:
        execution_runs = session.execute(
            select(StrategyRun).where(StrategyRun.run_type == StrategyRunType.PAPER_EXECUTION)
        ).scalars().all()

    assert len(execution_runs) == 2
    assert {run.id for run in execution_runs} >= {existing_run_id}


def test_run_paper_session_submits_only_missing_orders(
    migrated_paper_db: str,
) -> None:
    risk_run_id, approved_event_ids = _seed_approved_risk_batch()
    _seed_existing_paper_order(
        risk_run_id=risk_run_id,
        risk_event_id=approved_event_ids["AAPL"],
        symbol="AAPL",
        session_date=date(2024, 1, 5),
    )
    settings = load_settings()
    execution_service = FakeExecutionService()

    report = run_paper_session(
        "trend_following_daily",
        as_of_session=date(2024, 1, 5),
        settings=settings,
        execution_service=execution_service,
    )

    assert report.action == "submitted_missing_orders"
    assert report.execution_run_id is not None
    assert report.result_summary["submitted_count"] == 1
    assert report.result_summary["existing_count"] == 1
    assert report.result_summary["session_preflight"]["missing_count"] == 1
    assert len(execution_service.submitted_intents) == 1
    assert execution_service.submitted_intents[0].symbol == "MSFT"

    with session_scope(settings) as session:
        paper_orders = session.execute(select(PaperOrder).order_by(PaperOrder.client_order_id.asc())).scalars().all()
        execution_runs = session.execute(
            select(StrategyRun).where(StrategyRun.run_type == StrategyRunType.PAPER_EXECUTION)
        ).scalars().all()
        symbols = {order.symbol_ref.ticker for order in paper_orders}
        msft_order = next(order for order in paper_orders if order.symbol_ref.ticker == "MSFT")
        order_events = session.execute(
            select(OrderEvent)
            .where(OrderEvent.paper_order_id == msft_order.id)
            .order_by(OrderEvent.event_at.asc())
        ).scalars().all()

    assert len(paper_orders) == 2
    assert symbols == {"AAPL", "MSFT"}
    assert len(execution_runs) == 2
    assert [event.event_type for event in order_events] == [
        OrderTransitionEventType.INTENT_REGISTERED,
        OrderTransitionEventType.BROKER_ACKNOWLEDGED,
    ]


def test_run_paper_session_recovers_inflight_orders_before_submitting_missing_candidates(
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

    report = run_paper_session(
        "trend_following_daily",
        as_of_session=date(2024, 1, 5),
        settings=settings,
        execution_service=execution_service,
        broker_client=FakeBrokerClient(
            orders=[
                BrokerOrderSnapshot(
                    broker_order_id="recovered-aapl-001",
                    client_order_id=build_client_order_id(
                        prefix=settings.execution.client_order_id_prefix,
                        session_date=date(2024, 1, 5),
                        symbol="AAPL",
                        risk_event_id=approved_event_ids["AAPL"],
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
            account=BrokerAccountSnapshot(
                cash=Decimal("100000.000000"),
                buying_power=Decimal("100000.000000"),
                equity=Decimal("100000.000000"),
                long_market_value=Decimal("0"),
                short_market_value=Decimal("0"),
                raw_payload={"equity": "100000.000000"},
            ),
        ),
    )

    assert report.action == "submitted_missing_orders"
    assert report.result_summary["session_preflight"]["reconciliation"]["recovered_order_count"] == 1
    assert len(execution_service.submitted_intents) == 1
    assert execution_service.submitted_intents[0].symbol == "MSFT"

    with session_scope(settings) as session:
        paper_orders = session.execute(select(PaperOrder).order_by(PaperOrder.client_order_id.asc())).scalars().all()
        aapl_order = next(order for order in paper_orders if order.symbol_ref.ticker == "AAPL")

    assert len(paper_orders) == 2
    assert aapl_order.broker_order_id == "recovered-aapl-001"
    assert aapl_order.status == "submitted"


def test_run_paper_session_blocks_when_reconciliation_finds_unsafe_drift(
    migrated_paper_db: str,
) -> None:
    risk_run_id, approved_event_ids = _seed_approved_risk_batch()
    settings = load_settings()
    _seed_existing_paper_order(
        risk_run_id=risk_run_id,
        risk_event_id=approved_event_ids["AAPL"],
        symbol="AAPL",
        session_date=date(2024, 1, 5),
    )
    execution_service = FakeExecutionService()

    report = run_paper_session(
        "trend_following_daily",
        as_of_session=date(2024, 1, 5),
        settings=settings,
        execution_service=execution_service,
        broker_client=FakeBrokerClient(
            orders=[],
            fills=[],
            positions=[],
            account=BrokerAccountSnapshot(
                cash=Decimal("100000.000000"),
                buying_power=Decimal("100000.000000"),
                equity=Decimal("100000.000000"),
                long_market_value=Decimal("0"),
                short_market_value=Decimal("0"),
                raw_payload={"equity": "100000.000000"},
            ),
        ),
    )

    assert report.action == "blocked_reconciliation"
    assert report.execution_run_id is None
    assert report.result_summary["reconciliation"]["blocks_execution"] is True
    assert len(execution_service.submitted_intents) == 0


def test_run_paper_order_submission_records_blocked_attempt_when_strategy_disabled(
    migrated_paper_db: str,
) -> None:
    _seed_approved_risk_batch()
    settings = load_settings()
    execution_service = FakeExecutionService()
    control_service = OperatorControlService(settings=settings)
    control_service.disable_strategy(
        "trend_following_daily",
        reason="manual kill switch",
        actor="pytest",
        trigger_source="pytest",
    )

    report = run_paper_order_submission(
        "trend_following_daily",
        as_of_session=date(2024, 1, 5),
        settings=settings,
        execution_service=execution_service,
        trigger_source="pytest",
    )

    assert report.status == StrategyRunStatus.FAILED.value
    assert report.result_summary["action"] == "blocked_strategy_disabled"
    assert report.result_summary["blocked_reason"] == "strategy_disabled"
    assert execution_service.submitted_intents == []

    with session_scope(settings) as session:
        strategy = session.execute(select(Strategy)).scalar_one()
        blocked_run = session.get(StrategyRun, uuid.UUID(report.run_id))
        execution_events = session.execute(
            select(ExecutionEvent)
            .where(ExecutionEvent.strategy_run_id == uuid.UUID(report.run_id))
            .order_by(ExecutionEvent.event_at.desc())
        ).scalars().all()
        paper_orders = session.execute(select(PaperOrder)).scalars().all()

    assert strategy.status == StrategyStatus.DISABLED
    assert blocked_run is not None
    assert blocked_run.run_type == StrategyRunType.PAPER_EXECUTION
    assert blocked_run.status == StrategyRunStatus.FAILED
    assert len(execution_events) == 1
    assert execution_events[0].event_type == "paper_execution_blocked"
    assert execution_events[0].blocks_execution is True
    assert paper_orders == []


def test_run_paper_session_blocks_before_broker_reads_when_strategy_disabled(
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

    report = run_paper_session(
        "trend_following_daily",
        as_of_session=date(2024, 1, 5),
        settings=settings,
        execution_service=execution_service,
        broker_client=ExplodingBrokerClient(),
        trigger_source="pytest",
    )

    assert report.action == "blocked_strategy_disabled"
    assert report.execution_run_id is not None
    assert report.execution_status == StrategyRunStatus.FAILED.value
    assert report.result_summary["blocked_reason"] == "strategy_disabled"
    assert report.result_summary["session_preflight"]["missing_count"] == 2
    assert execution_service.submitted_intents == []


def test_sync_paper_state_persists_fills_positions_and_account_snapshot(
    migrated_paper_db: str,
) -> None:
    risk_run_id, approved_event_ids = _seed_approved_risk_batch()
    _seed_existing_paper_order(
        risk_run_id=risk_run_id,
        risk_event_id=approved_event_ids["AAPL"],
        symbol="AAPL",
        session_date=date(2024, 1, 5),
    )
    _seed_open_position(symbol="MSFT", quantity="5.000000")
    settings = load_settings()

    broker_client = FakeBrokerClient(
        orders=[
            BrokerOrderSnapshot(
                broker_order_id="existing-aapl-001",
                client_order_id=build_client_order_id(
                    prefix=settings.execution.client_order_id_prefix,
                    session_date=date(2024, 1, 5),
                    symbol="AAPL",
                    risk_event_id=approved_event_ids["AAPL"],
                ),
                symbol="AAPL",
                side=OrderSide.BUY,
                quantity=Decimal("10.000000"),
                status=ExecutionOrderStatus.FILLED,
                broker_status="filled",
                submitted_at=datetime(2024, 1, 5, 14, 35, tzinfo=UTC),
                filled_at=datetime(2024, 1, 5, 14, 40, tzinfo=UTC),
                canceled_at=None,
                updated_at=datetime(2024, 1, 5, 14, 40, tzinfo=UTC),
                raw_payload={"id": "existing-aapl-001", "status": "filled"},
            )
        ],
        fills=[
            BrokerFillSnapshot(
                broker_fill_id="fill-aapl-001",
                broker_order_id="existing-aapl-001",
                symbol="AAPL",
                side=OrderSide.BUY,
                quantity=Decimal("10.000000"),
                price=Decimal("120.250000"),
                filled_at=datetime(2024, 1, 5, 14, 40, tzinfo=UTC),
                raw_payload={"id": "fill-aapl-001", "order_id": "existing-aapl-001"},
            )
        ],
        positions=[
            BrokerPositionSnapshot(
                symbol="AAPL",
                quantity=Decimal("10.000000"),
                average_entry_price=Decimal("120.250000"),
                cost_basis=Decimal("1202.500000"),
                market_value=Decimal("1215.000000"),
                current_price=Decimal("121.500000"),
                raw_payload={"symbol": "AAPL"},
            )
        ],
        account=BrokerAccountSnapshot(
            cash=Decimal("98797.500000"),
            buying_power=Decimal("98797.500000"),
            equity=Decimal("100012.500000"),
            long_market_value=Decimal("1215.000000"),
            short_market_value=Decimal("0"),
            raw_payload={"equity": "100012.500000"},
        ),
    )

    report = sync_paper_state(
        "trend_following_daily",
        as_of_session=date(2024, 1, 5),
        settings=settings,
        broker_client=broker_client,
    )

    assert report.orders_synced == 1
    assert report.fills_ingested == 1
    assert report.positions_opened == 1
    assert report.positions_closed == 1
    assert report.open_positions == 1

    with session_scope(settings) as session:
        paper_order = session.execute(select(PaperOrder)).scalar_one()
        paper_fill = session.execute(select(PaperFill)).scalar_one()
        positions = session.execute(select(Position).order_by(Position.status.desc(), Position.created_at.asc())).scalars().all()
        snapshot = session.execute(select(AccountSnapshot).order_by(AccountSnapshot.snapshot_at.desc())).scalar_one()
        position_states = {position.symbol_ref.ticker: position.status for position in positions}

    assert paper_order.status == "filled"
    assert paper_order.filled_at is not None
    assert paper_order.last_synced_at is not None
    assert paper_fill.broker_fill_id == "fill-aapl-001"
    assert paper_fill.price == Decimal("120.250000")
    assert position_states == {"AAPL": "open", "MSFT": "closed"}
    assert snapshot.snapshot_source == "broker_sync"
    assert snapshot.open_positions == 1


def test_sync_paper_state_advances_partial_lifecycle_without_duplicate_fills(
    migrated_paper_db: str,
) -> None:
    risk_run_id, approved_event_ids = _seed_approved_risk_batch()
    _seed_existing_paper_order(
        risk_run_id=risk_run_id,
        risk_event_id=approved_event_ids["AAPL"],
        symbol="AAPL",
        session_date=date(2024, 1, 5),
    )
    settings = load_settings()
    client_order_id = build_client_order_id(
        prefix=settings.execution.client_order_id_prefix,
        session_date=date(2024, 1, 5),
        symbol="AAPL",
        risk_event_id=approved_event_ids["AAPL"],
    )

    partial_report = sync_paper_state(
        "trend_following_daily",
        as_of_session=date(2024, 1, 5),
        settings=settings,
        broker_client=FakeBrokerClient(
            orders=[
                BrokerOrderSnapshot(
                    broker_order_id="existing-aapl-001",
                    client_order_id=client_order_id,
                    symbol="AAPL",
                    side=OrderSide.BUY,
                    quantity=Decimal("10.000000"),
                    status=ExecutionOrderStatus.PARTIALLY_FILLED,
                    broker_status="partially_filled",
                    submitted_at=datetime(2024, 1, 5, 14, 35, tzinfo=UTC),
                    filled_at=datetime(2024, 1, 5, 14, 38, tzinfo=UTC),
                    canceled_at=None,
                    updated_at=datetime(2024, 1, 5, 14, 38, tzinfo=UTC),
                    raw_payload={"id": "existing-aapl-001", "status": "partially_filled"},
                )
            ],
            fills=[
                BrokerFillSnapshot(
                    broker_fill_id="fill-aapl-001",
                    broker_order_id="existing-aapl-001",
                    symbol="AAPL",
                    side=OrderSide.BUY,
                    quantity=Decimal("4.000000"),
                    price=Decimal("120.100000"),
                    filled_at=datetime(2024, 1, 5, 14, 38, tzinfo=UTC),
                    raw_payload={"id": "fill-aapl-001", "order_id": "existing-aapl-001"},
                )
            ],
            positions=[
                BrokerPositionSnapshot(
                    symbol="AAPL",
                    quantity=Decimal("4.000000"),
                    average_entry_price=Decimal("120.100000"),
                    cost_basis=Decimal("480.400000"),
                    market_value=Decimal("486.000000"),
                    current_price=Decimal("121.500000"),
                    raw_payload={"symbol": "AAPL"},
                )
            ],
            account=BrokerAccountSnapshot(
                cash=Decimal("99519.600000"),
                buying_power=Decimal("99519.600000"),
                equity=Decimal("100005.600000"),
                long_market_value=Decimal("486.000000"),
                short_market_value=Decimal("0"),
                raw_payload={"equity": "100005.600000"},
            ),
        ),
    )

    assert partial_report.fills_ingested == 1

    filled_report = sync_paper_state(
        "trend_following_daily",
        as_of_session=date(2024, 1, 5),
        settings=settings,
        broker_client=FakeBrokerClient(
            orders=[
                BrokerOrderSnapshot(
                    broker_order_id="existing-aapl-001",
                    client_order_id=client_order_id,
                    symbol="AAPL",
                    side=OrderSide.BUY,
                    quantity=Decimal("10.000000"),
                    status=ExecutionOrderStatus.FILLED,
                    broker_status="filled",
                    submitted_at=datetime(2024, 1, 5, 14, 35, tzinfo=UTC),
                    filled_at=datetime(2024, 1, 5, 14, 42, tzinfo=UTC),
                    canceled_at=None,
                    updated_at=datetime(2024, 1, 5, 14, 42, tzinfo=UTC),
                    raw_payload={"id": "existing-aapl-001", "status": "filled"},
                )
            ],
            fills=[
                BrokerFillSnapshot(
                    broker_fill_id="fill-aapl-001",
                    broker_order_id="existing-aapl-001",
                    symbol="AAPL",
                    side=OrderSide.BUY,
                    quantity=Decimal("4.000000"),
                    price=Decimal("120.100000"),
                    filled_at=datetime(2024, 1, 5, 14, 38, tzinfo=UTC),
                    raw_payload={"id": "fill-aapl-001", "order_id": "existing-aapl-001"},
                ),
                BrokerFillSnapshot(
                    broker_fill_id="fill-aapl-002",
                    broker_order_id="existing-aapl-001",
                    symbol="AAPL",
                    side=OrderSide.BUY,
                    quantity=Decimal("6.000000"),
                    price=Decimal("120.300000"),
                    filled_at=datetime(2024, 1, 5, 14, 42, tzinfo=UTC),
                    raw_payload={"id": "fill-aapl-002", "order_id": "existing-aapl-001"},
                ),
            ],
            positions=[
                BrokerPositionSnapshot(
                    symbol="AAPL",
                    quantity=Decimal("10.000000"),
                    average_entry_price=Decimal("120.220000"),
                    cost_basis=Decimal("1202.200000"),
                    market_value=Decimal("1215.000000"),
                    current_price=Decimal("121.500000"),
                    raw_payload={"symbol": "AAPL"},
                )
            ],
            account=BrokerAccountSnapshot(
                cash=Decimal("98797.800000"),
                buying_power=Decimal("98797.800000"),
                equity=Decimal("100012.800000"),
                long_market_value=Decimal("1215.000000"),
                short_market_value=Decimal("0"),
                raw_payload={"equity": "100012.800000"},
            ),
        ),
    )

    assert filled_report.fills_ingested == 1

    with session_scope(settings) as session:
        paper_order = session.execute(select(PaperOrder)).scalar_one()
        paper_fills = session.execute(select(PaperFill).order_by(PaperFill.filled_at.asc())).scalars().all()
        position = session.execute(select(Position).where(Position.status == "open")).scalar_one()
        order_events = session.execute(
            select(OrderEvent)
            .where(OrderEvent.paper_order_id == paper_order.id)
            .order_by(OrderEvent.event_at.asc())
        ).scalars().all()

    assert paper_order.status == "filled"
    assert len(paper_fills) == 2
    assert [fill.broker_fill_id for fill in paper_fills] == ["fill-aapl-001", "fill-aapl-002"]
    assert position.quantity == Decimal("10.000000")
    assert [event.event_type for event in order_events] == [
        OrderTransitionEventType.BROKER_PARTIALLY_FILLED,
        OrderTransitionEventType.BROKER_FILLED,
    ]


def test_paper_execution_module_routes_lifecycle_through_order_state_machine() -> None:
    source = (Path(__file__).resolve().parents[1] / "src/trading_platform/services/paper_execution.py").read_text()

    assert "apply_order_transition" in source
    assert re.search(r"\b(?:pending_order|persisted_order|existing_order|local_order|paper_order)\.status\s*=(?!=)", source) is None
