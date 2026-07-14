from __future__ import annotations

import os
import re
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from alembic import command
from sqlalchemy import event, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.migrate import build_alembic_config
from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.db.models import (
    AccountSnapshot,
    DailyBar,
    ExecutionEvent,
    KillSwitchState,
    MarketSession,
    OrderEvent,
    OrderLifecycleState,
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
from trading_platform.services.concurrency_guard import ConcurrentRunLockedError, session_run_lock
from trading_platform.services.execution import (
    ExecutionOrderStatus,
    ExecutionService,
    OrderIntent,
    OrderSide,
    OrderSubmissionResult,
    build_client_order_id,
    resolve_submission_session,
    run_paper_order_submission,
    run_paper_session,
    sync_paper_state,
)
from trading_platform.services.execution.idempotency import build_intent_hash
import trading_platform.services.execution.submit_orders as paper_submit_orders_module
import trading_platform.services.execution.sync_orders as paper_sync_orders_module
from trading_platform.services.operator_controls import OperatorControlService
from trading_platform.services.operator_reads import OperatorReadFilters, OperatorReadService
from trading_platform.strategies.registry import build_default_registry

_AUTO_BROKER_ORDER_ID = object()


@contextmanager
def _capture_paper_fill_dedup_queries(session):
    captured: list[tuple[str, object]] = []
    engine = session.get_bind()

    def _capture(conn, cursor, statement, parameters, context, executemany) -> None:
        if statement.lstrip().upper().startswith("SELECT") and "paper_fills" in statement:
            captured.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", _capture)
    try:
        yield captured
    finally:
        event.remove(engine, "before_cursor_execute", _capture)


def _bound_parameter_values(parameters: object) -> list[object]:
    if isinstance(parameters, dict):
        return list(parameters.values())
    if isinstance(parameters, (list, tuple)):
        return list(parameters)
    raise TypeError(f"Unsupported SQL parameter container: {type(parameters)!r}")


def _broker_fill_snapshot(
    broker_fill_id: str,
    *,
    filled_at: datetime | None = None,
) -> BrokerFillSnapshot:
    return BrokerFillSnapshot(
        broker_fill_id=broker_fill_id,
        broker_order_id="existing-aapl-001",
        symbol="AAPL",
        side=OrderSide.BUY,
        quantity=Decimal("1.000000"),
        price=Decimal("120.250000"),
        filled_at=filled_at or datetime(2024, 1, 5, 14, 40, tzinfo=UTC),
        raw_payload={"id": broker_fill_id, "order_id": "existing-aapl-001"},
    )


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


class ExplodingExecutionService(ExecutionService):
    def __init__(self) -> None:
        self.submitted_intents: list[OrderIntent] = []

    def describe(self) -> dict[str, object]:
        return {"service": "execution", "status": "available", "provider": "exploding"}

    def submit_order(self, intent: OrderIntent) -> OrderSubmissionResult:
        raise AssertionError(
            "execution service should not submit orders while kill switch is tripped"
        )


class MidRunTrippingExecutionService(ExecutionService):
    """Submits the first candidate successfully, then trips the kill switch."""

    def __init__(self, *, settings) -> None:
        self._settings = settings
        self.submitted_intents: list[OrderIntent] = []
        self._tripped = False

    def describe(self) -> dict[str, object]:
        return {"service": "execution", "status": "available", "provider": "mid_run_tripper"}

    def submit_order(self, intent: OrderIntent) -> OrderSubmissionResult:
        if self._tripped:
            raise AssertionError(
                "execution service should not submit more orders after mid-run kill switch trip"
            )
        self.submitted_intents.append(intent)
        OperatorControlService(settings=self._settings).trip_kill_switch(
            reason="mid-run trip in test",
            actor="pytest",
            trigger_source="pytest",
        )
        self._tripped = True
        return OrderSubmissionResult(
            client_order_id=intent.client_order_id,
            broker_order_id=f"mid-run-{intent.symbol.lower()}-001",
            symbol=intent.symbol,
            side=intent.side,
            quantity=intent.quantity,
            order_type=intent.order_type,
            time_in_force=intent.time_in_force,
            status=ExecutionOrderStatus.PENDING,
            broker_status="new",
            submitted_at=datetime(2024, 1, 5, 14, 35, tzinfo=UTC),
            raw_payload={
                "id": f"mid-run-{intent.symbol.lower()}-001",
                "client_order_id": intent.client_order_id,
                "symbol": intent.symbol,
                "status": "new",
            },
        )


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
    broker_order_id: str | None | object = _AUTO_BROKER_ORDER_ID,
    broker_status: str | None = "new",
    submission_attempt_count: int = 1,
    last_submission_error: str | None = None,
) -> uuid.UUID:
    settings = load_settings()
    registry = build_default_registry(settings)
    strategy = registry.resolve("trend_following_daily")

    with session_scope(settings) as session:
        strategy_record = ensure_strategy_record(session, strategy.metadata)
        symbol_row = session.execute(select(Symbol).where(Symbol.ticker == symbol)).scalar_one()
        risk_event = session.get(RiskEvent, risk_event_id)
        if risk_event is None:
            raise LookupError(f"Missing risk_event '{risk_event_id}'.")
        quantity = risk_event.proposed_quantity
        if quantity is None:
            raise ValueError(f"Risk event '{risk_event_id}' does not have a proposed quantity.")

        if broker_order_id is _AUTO_BROKER_ORDER_ID:
            resolved_broker_order_id = None if status == "pending_submission" else f"existing-{symbol.lower()}-001"
        else:
            resolved_broker_order_id = broker_order_id

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
                quantity=quantity,
                order_type="market",
                time_in_force="day",
                client_order_id=build_client_order_id(
                    prefix=load_settings().execution.client_order_id_prefix,
                    strategy_id=strategy.metadata.strategy_id,
                    session_date=session_date,
                    symbol=symbol,
                    side=OrderSide.BUY,
                    quantity=quantity,
                ),
                intent_hash=build_intent_hash(
                    strategy_id=strategy.metadata.strategy_id,
                    session_date=session_date,
                    symbol=symbol,
                    side=OrderSide.BUY,
                    quantity=quantity,
                ),
                intent_version=1,
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


def _seed_followup_risk_event(
    *,
    symbol: str,
    session_date: date,
    quantity: str,
    signal_reason: str = "trend_entry",
) -> tuple[uuid.UUID, uuid.UUID]:
    settings = load_settings()
    registry = build_default_registry(settings)
    strategy = registry.resolve("trend_following_daily")

    with session_scope(settings) as session:
        strategy_record = ensure_strategy_record(session, strategy.metadata)
        symbol_row = session.execute(select(Symbol).where(Symbol.ticker == symbol)).scalar_one()
        risk_run = StrategyRun(
            strategy_id=strategy_record.id,
            run_type=StrategyRunType.RISK_EVALUATION,
            status=StrategyRunStatus.SUCCEEDED,
            trigger_source="followup_risk_seed",
            parameters_snapshot={"as_of_session": session_date.isoformat()},
            result_summary={"stage": "completed", "as_of_session": session_date.isoformat()},
            completed_at=datetime(2024, 1, 5, 16, 0, tzinfo=UTC),
        )
        session.add(risk_run)
        session.flush()

        risk_event = RiskEvent(
            strategy_run_id=risk_run.id,
            symbol_id=symbol_row.id,
            session_date=session_date,
            signal_direction="long",
            signal_reason=signal_reason,
            outcome="approved",
            decision_code="approved",
            decision_reason="Approved for paper execution.",
            reference_price=Decimal("120.000000"),
            proposed_quantity=Decimal(quantity),
            proposed_notional=Decimal(quantity) * Decimal("120.000000"),
            risk_metadata={"remaining_cash": 98800.0},
        )
        session.add(risk_event)
        session.flush()
        return risk_run.id, risk_event.id


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


def test_run_paper_order_submission_retries_same_intent_across_followup_risk_runs(
    migrated_paper_db: str,
) -> None:
    risk_run_id, approved_event_ids = _seed_approved_risk_batch()
    _seed_existing_paper_order(
        risk_run_id=risk_run_id,
        risk_event_id=approved_event_ids["AAPL"],
        symbol="AAPL",
        session_date=date(2024, 1, 5),
        status="submission_failed",
        broker_order_id=None,
        broker_status=None,
        submission_attempt_count=1,
        last_submission_error="timed out",
    )
    followup_risk_run_id, _followup_event_id = _seed_followup_risk_event(
        symbol="AAPL",
        session_date=date(2024, 1, 5),
        quantity="10.000000",
        signal_reason="rerun_retry",
    )
    settings = load_settings()
    execution_service = FakeExecutionService()

    with session_scope(settings) as session:
        existing_order = session.execute(
            select(PaperOrder).where(PaperOrder.source_risk_event_id == approved_event_ids["AAPL"])
        ).scalar_one()
        existing_order_id = existing_order.id
        existing_client_order_id = existing_order.client_order_id

    report = run_paper_order_submission(
        "trend_following_daily",
        as_of_session=date(2024, 1, 5),
        risk_run_id=str(followup_risk_run_id),
        settings=settings,
        execution_service=execution_service,
        trigger_source="followup_retry",
    )

    assert report.result_summary["submitted_count"] == 1
    assert report.result_summary["existing_count"] == 0
    assert report.result_summary["reused_count"] == 1
    assert report.result_summary["versioned_count"] == 0
    assert len(execution_service.submitted_intents) == 1
    assert execution_service.submitted_intents[0].client_order_id == existing_client_order_id
    assert execution_service.submitted_intents[0].intent_version == 1
    assert report.result_summary["reused_orders"][0]["intent_decision"]["action"] == "retry_existing"

    with session_scope(settings) as session:
        paper_orders = session.execute(select(PaperOrder).order_by(PaperOrder.created_at.asc())).scalars().all()

    assert len(paper_orders) == 1
    assert paper_orders[0].id == existing_order_id
    assert paper_orders[0].client_order_id == existing_client_order_id
    assert paper_orders[0].source_risk_event_id == approved_event_ids["AAPL"]


def test_run_paper_order_submission_versions_material_change_after_broker_touch(
    migrated_paper_db: str,
) -> None:
    risk_run_id, approved_event_ids = _seed_approved_risk_batch()
    _seed_existing_paper_order(
        risk_run_id=risk_run_id,
        risk_event_id=approved_event_ids["AAPL"],
        symbol="AAPL",
        session_date=date(2024, 1, 5),
    )
    followup_risk_run_id, followup_event_id = _seed_followup_risk_event(
        symbol="AAPL",
        session_date=date(2024, 1, 5),
        quantity="12.000000",
        signal_reason="scaled_entry",
    )
    settings = load_settings()
    execution_service = FakeExecutionService()

    report = run_paper_order_submission(
        "trend_following_daily",
        as_of_session=date(2024, 1, 5),
        risk_run_id=str(followup_risk_run_id),
        settings=settings,
        execution_service=execution_service,
        trigger_source="followup_version",
    )

    assert report.result_summary["submitted_count"] == 1
    assert report.result_summary["reused_count"] == 0
    assert report.result_summary["versioned_count"] == 1
    assert len(execution_service.submitted_intents) == 1
    assert execution_service.submitted_intents[0].intent_version == 2

    with session_scope(settings) as session:
        paper_orders = session.execute(
            select(PaperOrder).order_by(PaperOrder.intent_version.asc(), PaperOrder.created_at.asc())
        ).scalars().all()

    assert len(paper_orders) == 2
    first_order, second_order = paper_orders
    assert first_order.intent_version == 1
    assert second_order.intent_version == 2
    assert second_order.supersedes_paper_order_id == first_order.id
    assert second_order.source_risk_event_id == followup_event_id
    assert second_order.client_order_id != first_order.client_order_id
    assert report.result_summary["versioned_orders"][0]["intent_decision"]["action"] == "create_new_version"

    service = OperatorReadService(settings)
    order_reads = service.list_paper_orders(
        OperatorReadFilters(
            strategy_id="trend_following_daily",
            run_type="paper_execution",
            status="succeeded",
            session_start=date(2024, 1, 5),
            session_end=date(2024, 1, 5),
            limit=10,
        )
    )
    versioned_payload = next(
        item for item in order_reads if item["intent_context"]["intent_version"] == 2
    )

    assert versioned_payload["intent_context"]["supersedes_paper_order_id"] == str(first_order.id)
    assert versioned_payload["intent_context"]["supersedes_client_order_id"] == first_order.client_order_id


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
                    strategy_id="trend_following_daily",
                    session_date=date(2024, 1, 5),
                    symbol="AAPL",
                    side=OrderSide.BUY,
                    quantity=Decimal("10.000000"),
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
        strategy_id="trend_following_daily",
        session_date=date(2024, 1, 5),
        symbol="AAPL",
        side=OrderSide.BUY,
        quantity=Decimal("10.000000"),
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


def test_paper_fill_dedup_empty_batch_executes_no_select(
    migrated_paper_db: str,
) -> None:
    settings = load_settings()

    with session_scope(settings) as session:
        with _capture_paper_fill_dedup_queries(session) as dedup_queries:
            ingested = paper_sync_orders_module._ingest_paper_fills(
                session,
                [],
                local_orders_by_broker_id={},
            )

    assert ingested == 0
    assert dedup_queries == []


def test_paper_fill_dedup_work_is_independent_of_historical_size(
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
    current_batch = [
        _broker_fill_snapshot("fill-current-existing"),
        _broker_fill_snapshot("fill-current-new"),
    ]

    with session_scope(settings) as session:
        local_order = session.execute(select(PaperOrder)).scalar_one()
        session.add(
            PaperFill(
                paper_order_id=local_order.id,
                symbol_id=local_order.symbol_id,
                broker_fill_id="fill-current-existing",
                broker_order_id=local_order.broker_order_id,
                side="buy",
                quantity=Decimal("1.000000"),
                price=Decimal("120.000000"),
                filled_at=datetime(2024, 1, 4, 14, 40, tzinfo=UTC),
                broker_payload={},
            )
        )
        session.flush()

        with _capture_paper_fill_dedup_queries(session) as small_history_queries:
            small_history_matches = paper_sync_orders_module._load_existing_paper_fill_ids(
                session, current_batch
            )

        session.add_all(
            [
                PaperFill(
                    paper_order_id=local_order.id,
                    symbol_id=local_order.symbol_id,
                    broker_fill_id=f"fill-history-{index:04d}",
                    broker_order_id=local_order.broker_order_id,
                    side="buy",
                    quantity=Decimal("1.000000"),
                    price=Decimal("100.000000"),
                    filled_at=datetime(2023, 1, 1, tzinfo=UTC) + timedelta(minutes=index),
                    broker_payload={},
                )
                for index in range(250)
            ]
        )
        session.flush()

        with _capture_paper_fill_dedup_queries(session) as large_history_queries:
            large_history_matches = paper_sync_orders_module._load_existing_paper_fill_ids(
                session, current_batch
            )

    assert small_history_matches == large_history_matches == {"fill-current-existing"}
    assert len(small_history_queries) == len(large_history_queries) == 1
    for statement, parameters in (*small_history_queries, *large_history_queries):
        assert re.search(r"\bWHERE\b", statement, re.IGNORECASE)
        assert " IN (" in statement.upper()
        assert sorted(_bound_parameter_values(parameters)) == [
            "fill-current-existing",
            "fill-current-new",
        ]


def test_paper_fill_dedup_chunks_current_ids_deterministically(
    migrated_paper_db: str,
) -> None:
    settings = load_settings()
    unique_count = paper_sync_orders_module._PAPER_FILL_DEDUP_CHUNK_SIZE + 1
    distinct_ids = [f"fill-current-{index:04d}" for index in reversed(range(unique_count))]
    broker_fills = [_broker_fill_snapshot(fill_id) for fill_id in distinct_ids]
    broker_fills.append(_broker_fill_snapshot(distinct_ids[0]))

    with session_scope(settings) as session:
        with _capture_paper_fill_dedup_queries(session) as dedup_queries:
            matches = paper_sync_orders_module._load_existing_paper_fill_ids(session, broker_fills)

    assert matches == set()
    assert len(dedup_queries) == 2
    bound_chunks = [_bound_parameter_values(parameters) for _, parameters in dedup_queries]
    assert [len(chunk) for chunk in bound_chunks] == [
        paper_sync_orders_module._PAPER_FILL_DEDUP_CHUNK_SIZE,
        1,
    ]
    assert bound_chunks[0] == sorted(distinct_ids)[
        : paper_sync_orders_module._PAPER_FILL_DEDUP_CHUNK_SIZE
    ]
    assert bound_chunks[1] == sorted(distinct_ids)[
        paper_sync_orders_module._PAPER_FILL_DEDUP_CHUNK_SIZE :
    ]
    assert all(
        re.search(r"\bWHERE\b", statement, re.IGNORECASE)
        and " IN (" in statement.upper()
        for statement, _ in dedup_queries
    )


def test_paper_fill_duplicate_ingestion_preserves_idempotency_and_filled_at(
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
    first_new_fill_at = datetime(2024, 1, 5, 14, 42, tzinfo=UTC)

    with session_scope(settings) as session:
        local_order = session.execute(select(PaperOrder)).scalar_one()
        session.add(
            PaperFill(
                paper_order_id=local_order.id,
                symbol_id=local_order.symbol_id,
                broker_fill_id="fill-already-stored",
                broker_order_id=local_order.broker_order_id,
                side="buy",
                quantity=Decimal("1.000000"),
                price=Decimal("120.000000"),
                filled_at=datetime(2024, 1, 5, 14, 38, tzinfo=UTC),
                broker_payload={},
            )
        )
        session.flush()

        ingested = paper_sync_orders_module._ingest_paper_fills(
            session,
            [
                _broker_fill_snapshot("fill-already-stored"),
                _broker_fill_snapshot("fill-new", filled_at=first_new_fill_at),
                _broker_fill_snapshot(
                    "fill-new", filled_at=datetime(2024, 1, 5, 14, 45, tzinfo=UTC)
                ),
            ],
            local_orders_by_broker_id={local_order.broker_order_id: local_order},
        )
        session.flush()
        persisted_ids = session.execute(
            select(PaperFill.broker_fill_id).order_by(PaperFill.broker_fill_id)
        ).scalars().all()

    assert ingested == 1
    assert persisted_ids == ["fill-already-stored", "fill-new"]
    assert local_order.filled_at == first_new_fill_at


def test_paper_execution_module_routes_lifecycle_through_order_state_machine() -> None:
    # STRUCT-04 (12-04): submission and broker-state-sync lifecycle writes now
    # live in two separate modules (execution package split); check both.
    execution_dir = Path(__file__).resolve().parents[1] / "src/trading_platform/services/execution"
    source = "".join(
        (execution_dir / name).read_text() for name in ("submit_orders.py", "sync_orders.py")
    )

    assert "apply_order_transition" in source
    assert re.search(r"\b(?:pending_order|persisted_order|existing_order|local_order|paper_order)\.status\s*=(?!=)", source) is None


def test_run_paper_order_submission_blocks_when_global_kill_switch_is_tripped(
    migrated_paper_db: str,
) -> None:
    _seed_approved_risk_batch()
    settings = load_settings()
    control_service = OperatorControlService(settings=settings)
    control_service.trip_kill_switch(
        reason="global halt before submission",
        actor="pytest",
        trigger_source="pytest",
    )
    execution_service = ExplodingExecutionService()

    report = run_paper_order_submission(
        "trend_following_daily",
        as_of_session=date(2024, 1, 5),
        settings=settings,
        execution_service=execution_service,
        trigger_source="pytest",
    )

    assert report.status == StrategyRunStatus.FAILED.value
    assert report.result_summary["action"] == "blocked_global_kill_switch"
    assert report.result_summary["blocked_reason"] == "global_kill_switch_tripped"
    assert report.result_summary["kill_switch"]["state"] == KillSwitchState.TRIPPED.value
    assert report.result_summary["kill_switch"]["is_tripped"] is True

    with session_scope(settings) as session:
        blocked_run = session.get(StrategyRun, uuid.UUID(report.run_id))
        blocked_events = session.execute(
            select(ExecutionEvent)
            .where(ExecutionEvent.strategy_run_id == uuid.UUID(report.run_id))
        ).scalars().all()
        paper_orders = session.execute(select(PaperOrder)).scalars().all()

    assert blocked_run is not None
    assert blocked_run.run_type == StrategyRunType.PAPER_EXECUTION
    assert blocked_run.status == StrategyRunStatus.FAILED
    assert len(blocked_events) == 1
    assert blocked_events[0].event_type == "paper_execution_blocked"
    assert blocked_events[0].blocks_execution is True
    assert blocked_events[0].details["blocked_reason"] == "global_kill_switch_tripped"
    assert paper_orders == []


def test_run_paper_order_submission_persists_block_until_manual_kill_switch_reset(
    migrated_paper_db: str,
) -> None:
    _seed_approved_risk_batch()
    settings = load_settings()
    control_service = OperatorControlService(settings=settings)
    control_service.trip_kill_switch(
        reason="global halt pending manual reset",
        actor="pytest",
        trigger_source="pytest",
    )
    first_attempt = run_paper_order_submission(
        "trend_following_daily",
        as_of_session=date(2024, 1, 5),
        settings=settings,
        execution_service=ExplodingExecutionService(),
        trigger_source="pytest",
    )
    second_attempt = run_paper_order_submission(
        "trend_following_daily",
        as_of_session=date(2024, 1, 5),
        settings=settings,
        execution_service=ExplodingExecutionService(),
        trigger_source="pytest",
    )

    assert first_attempt.result_summary["blocked_reason"] == "global_kill_switch_tripped"
    assert second_attempt.result_summary["blocked_reason"] == "global_kill_switch_tripped"

    control_service.reset_kill_switch(
        reason="incident resolved",
        actor="pytest",
        trigger_source="pytest",
    )
    allowed_execution_service = FakeExecutionService()
    resumed_report = run_paper_order_submission(
        "trend_following_daily",
        as_of_session=date(2024, 1, 5),
        settings=settings,
        execution_service=allowed_execution_service,
        trigger_source="pytest",
    )

    assert resumed_report.status == StrategyRunStatus.SUCCEEDED.value
    assert resumed_report.result_summary.get("blocked_reason") is None
    assert resumed_report.result_summary["submitted_count"] == 2
    assert {intent.symbol for intent in allowed_execution_service.submitted_intents} == {
        "AAPL",
        "MSFT",
    }


def test_run_paper_order_submission_halts_mid_run_when_kill_switch_trips_between_submissions(
    migrated_paper_db: str,
) -> None:
    _seed_approved_risk_batch()
    settings = load_settings()
    execution_service = MidRunTrippingExecutionService(settings=settings)

    report = run_paper_order_submission(
        "trend_following_daily",
        as_of_session=date(2024, 1, 5),
        settings=settings,
        execution_service=execution_service,
        trigger_source="pytest",
    )

    assert report.status == StrategyRunStatus.FAILED.value
    assert report.result_summary["action"] == "blocked_mid_run_global_kill_switch"
    assert report.result_summary["blocked_reason"] == "global_kill_switch_tripped"
    assert report.result_summary["stage"] == "blocked_mid_run"
    assert report.result_summary["submitted_count"] == 1
    assert report.result_summary["skipped_by_kill_switch_count"] == 1
    assert len(execution_service.submitted_intents) == 1

    submitted_client_order_ids = {
        entry["client_order_id"] for entry in report.result_summary["submitted_orders"]
    }
    skipped_symbols = {
        entry["symbol"] for entry in report.result_summary["skipped_by_kill_switch"]
    }
    assert len(submitted_client_order_ids) == 1
    assert skipped_symbols.issubset({"AAPL", "MSFT"})
    assert len(skipped_symbols) == 1
    submitted_symbol = execution_service.submitted_intents[0].symbol
    assert submitted_symbol not in skipped_symbols
    assert {submitted_symbol} | skipped_symbols == {"AAPL", "MSFT"}

    with session_scope(settings) as session:
        paper_orders = session.execute(select(PaperOrder)).scalars().all()
        persisted_symbols = {order.symbol_ref.ticker for order in paper_orders}
        blocked_events = session.execute(
            select(ExecutionEvent)
            .where(ExecutionEvent.strategy_run_id == uuid.UUID(report.run_id))
            .where(ExecutionEvent.event_type == "paper_execution_blocked")
        ).scalars().all()
        blocked_event_details = [dict(event.details) for event in blocked_events]
        blocked_event_blocks = [event.blocks_execution for event in blocked_events]

    assert persisted_symbols == {submitted_symbol}
    assert len(blocked_events) == 1
    assert blocked_event_blocks == [True]
    assert blocked_event_details[0]["blocked_reason"] == "global_kill_switch_tripped"


def test_run_paper_session_runs_reconciliation_before_blocking_on_tripped_kill_switch(
    migrated_paper_db: str,
) -> None:
    risk_run_id, approved_event_ids = _seed_approved_risk_batch()
    _seed_existing_paper_order(
        risk_run_id=risk_run_id,
        risk_event_id=approved_event_ids["AAPL"],
        symbol="AAPL",
        session_date=date(2024, 1, 5),
        status="pending_submission",
        broker_order_id=None,
        broker_status=None,
    )
    settings = load_settings()
    control_service = OperatorControlService(settings=settings)
    control_service.trip_kill_switch(
        reason="ensure reconciliation still runs while tripped",
        actor="pytest",
        trigger_source="pytest",
    )
    execution_service = ExplodingExecutionService()
    broker_client = FakeBrokerClient(
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
        account=BrokerAccountSnapshot(
            cash=Decimal("100000.000000"),
            buying_power=Decimal("100000.000000"),
            equity=Decimal("100000.000000"),
            long_market_value=Decimal("0"),
            short_market_value=Decimal("0"),
            raw_payload={"equity": "100000.000000"},
        ),
    )

    report = run_paper_session(
        "trend_following_daily",
        as_of_session=date(2024, 1, 5),
        settings=settings,
        execution_service=execution_service,
        broker_client=broker_client,
        trigger_source="pytest",
    )

    assert report.action == "blocked_global_kill_switch"
    assert report.result_summary["session_preflight"]["reconciliation"][
        "recovered_order_count"
    ] == 1
    assert report.result_summary["session_preflight"]["kill_switch"]["is_tripped"] is True
    assert report.result_summary["blocked_reason"] == "global_kill_switch_tripped"
    assert execution_service.submitted_intents == []

    with session_scope(settings) as session:
        aapl_order = session.execute(
            select(PaperOrder).where(PaperOrder.client_order_id.like("%"))
        ).scalar_one()

    assert aapl_order.broker_order_id == "recovered-aapl-001"
    assert aapl_order.status == "submitted"


def test_sync_paper_state_continues_reading_broker_state_while_kill_switch_is_tripped(
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
    control_service = OperatorControlService(settings=settings)
    control_service.trip_kill_switch(
        reason="read-only flow must remain available",
        actor="pytest",
        trigger_source="pytest",
    )

    client_order_id = build_client_order_id(
        prefix=settings.execution.client_order_id_prefix,
        strategy_id="trend_following_daily",
        session_date=date(2024, 1, 5),
        symbol="AAPL",
        side=OrderSide.BUY,
        quantity=Decimal("10.000000"),
    )
    broker_client = FakeBrokerClient(
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
    assert report.open_positions == 1

    with session_scope(settings) as session:
        paper_order = session.execute(select(PaperOrder)).scalar_one()
        snapshot = session.execute(
            select(AccountSnapshot).order_by(AccountSnapshot.snapshot_at.desc())
        ).scalar_one()

    assert paper_order.status == "filled"
    assert snapshot.snapshot_source == "broker_sync"


# ---------------------------------------------------------------------------
# Phase 8 (Concurrency Guard) — 08-04: lock-guarded run_paper_order_submission
# ---------------------------------------------------------------------------


def test_run_paper_order_submission_loser_writes_nothing_and_makes_no_broker_calls(
    migrated_paper_db: str,
) -> None:
    _seed_approved_risk_batch()
    settings = load_settings()
    strategy_id = "trend_following_daily"
    session_date = date(2024, 1, 5)
    execution_service = FakeExecutionService()

    with session_scope(settings) as session:
        pre_run_ids = {
            run.id
            for run in session.execute(
                select(StrategyRun).where(StrategyRun.run_type == StrategyRunType.PAPER_EXECUTION)
            ).scalars()
        }

    # Hold the tuple's advisory lock from the test itself, then attempt a
    # second, concurrent submission for the SAME (strategy_id, session_date).
    with session_run_lock(strategy_id=strategy_id, session_date=session_date, settings=settings):
        with pytest.raises(ConcurrentRunLockedError) as exc_info:
            run_paper_order_submission(
                strategy_id,
                as_of_session=session_date,
                settings=settings,
                execution_service=execution_service,
                trigger_source="pytest_loser",
            )

    assert exc_info.value.strategy_id == strategy_id
    assert exc_info.value.session_date == session_date
    assert execution_service.submitted_intents == []

    with session_scope(settings) as session:
        post_run_ids = {
            run.id
            for run in session.execute(
                select(StrategyRun).where(StrategyRun.run_type == StrategyRunType.PAPER_EXECUTION)
            ).scalars()
        }
        paper_orders = session.execute(select(PaperOrder)).scalars().all()

    assert post_run_ids == pre_run_ids
    assert paper_orders == []


def test_run_paper_order_submission_running_row_first_and_reclaims_stale_predecessor(
    migrated_paper_db: str,
) -> None:
    _seed_approved_risk_batch()
    settings = load_settings()
    registry = build_default_registry(settings)
    strategy = registry.resolve("trend_following_daily")
    session_date = date(2024, 1, 5)

    with session_scope(settings) as session:
        strategy_record = ensure_strategy_record(session, strategy.metadata)
        stale_predecessor = StrategyRun(
            strategy_id=strategy_record.id,
            run_type=StrategyRunType.PAPER_EXECUTION,
            status=StrategyRunStatus.RUNNING,
            trigger_source="crashed_predecessor",
            started_at=datetime.now(UTC) - timedelta(minutes=40),
            parameters_snapshot={"as_of_session": session_date.isoformat()},
            result_summary={"stage": "running", "as_of_session": session_date.isoformat()},
        )
        session.add(stale_predecessor)
        session.flush()
        stale_run_id = stale_predecessor.id

    execution_service = FakeExecutionService()

    report = run_paper_order_submission(
        "trend_following_daily",
        as_of_session=session_date,
        settings=settings,
        execution_service=execution_service,
        trigger_source="pytest_reclaim",
    )

    # Fresh run completed normally -- reclaim of the predecessor did not
    # interfere with the new run's own progress.
    assert report.status == StrategyRunStatus.SUCCEEDED.value
    fresh_run_id = uuid.UUID(report.run_id)

    with session_scope(settings) as session:
        stale_after = session.get(StrategyRun, stale_run_id)
        fresh_after = session.get(StrategyRun, fresh_run_id)
        reclaim_events = session.execute(
            select(ExecutionEvent).where(ExecutionEvent.strategy_run_id == stale_run_id)
        ).scalars().all()

    # The pre-existing 40-minute-old running row was reclaimed to STALE...
    assert stale_after.status == StrategyRunStatus.STALE
    assert len(reclaim_events) == 1
    assert reclaim_events[0].event_type == "paper_run_reclaimed_stale"
    assert reclaim_events[0].details["reclaiming_run_id"] == str(fresh_run_id)
    # ...while the fresh run's own row -- created inside the timeout window
    # moments earlier -- was never a reclaim candidate and reached succeeded.
    assert fresh_after.status == StrategyRunStatus.SUCCEEDED


def test_run_paper_order_submission_kill_switch_blocks_after_lock_and_releases_lock_on_exit(
    migrated_paper_db: str,
) -> None:
    _seed_approved_risk_batch()
    settings = load_settings()
    strategy_id = "trend_following_daily"
    session_date = date(2024, 1, 5)
    control_service = OperatorControlService(settings=settings)
    control_service.trip_kill_switch(
        reason="post-lock check test",
        actor="pytest",
        trigger_source="pytest",
    )
    execution_service = ExplodingExecutionService()

    report = run_paper_order_submission(
        strategy_id,
        as_of_session=session_date,
        settings=settings,
        execution_service=execution_service,
        trigger_source="pytest_kill_switch_lock",
    )

    # The run row was created (RUNNING-first, per LOCK-03) and only THEN
    # finalized blocked/FAILED -- proving the kill-switch check ran after
    # lock acquisition and after the row existed, not before either.
    assert report.status == StrategyRunStatus.FAILED.value
    assert report.result_summary["action"] == "blocked_global_kill_switch"
    assert report.result_summary["blocked_reason"] == "global_kill_switch_tripped"
    assert execution_service.submitted_intents == []

    with session_scope(settings) as session:
        blocked_run = session.get(StrategyRun, uuid.UUID(report.run_id))
        blocked_events = session.execute(
            select(ExecutionEvent).where(ExecutionEvent.strategy_run_id == uuid.UUID(report.run_id))
        ).scalars().all()
        paper_orders = session.execute(select(PaperOrder)).scalars().all()

    assert blocked_run is not None
    assert blocked_run.run_type == StrategyRunType.PAPER_EXECUTION
    assert blocked_run.status == StrategyRunStatus.FAILED
    assert len(blocked_events) == 1
    assert blocked_events[0].event_type == "paper_execution_blocked"
    assert paper_orders == []

    # LOCK-06: the lock must be free for a subsequent acquisition on the
    # SAME tuple now that the kill-switch-blocked run has finalized and
    # exited its guarded `with session_run_lock(...)` region.
    with session_run_lock(strategy_id=strategy_id, session_date=session_date, settings=settings):
        pass


class _BrokerFailsOnSubmitExecutionService(ExecutionService):
    """Broker call always raises before producing any side effect."""

    def describe(self) -> dict[str, object]:
        return {"service": "execution", "status": "available", "provider": "broker_fails_on_submit"}

    def submit_order(self, intent: OrderIntent) -> OrderSubmissionResult:
        raise RuntimeError("broker unavailable")


def test_run_paper_order_submission_commits_broker_result_only_after_success(
    migrated_paper_db: str,
) -> None:
    """DB-05: the broker-success state transition commits only after BOTH
    the broker call succeeded AND the state-transition persist flushed."""
    _seed_approved_risk_batch()
    settings = load_settings()
    execution_service = FakeExecutionService()

    report = run_paper_order_submission(
        "trend_following_daily",
        as_of_session=date(2024, 1, 5),
        settings=settings,
        execution_service=execution_service,
        trigger_source="commit_after_both_test",
    )

    assert report.result_summary["submitted_count"] == 2

    with session_scope(settings) as session:
        paper_orders = session.execute(select(PaperOrder)).scalars().all()
        assert len(paper_orders) == 2
        for order in paper_orders:
            assert order.broker_order_id is not None
            assert order.broker_status == "new"
            events = session.execute(
                select(OrderEvent)
                .where(OrderEvent.paper_order_id == order.id)
                .order_by(OrderEvent.event_at.asc())
            ).scalars().all()
            assert events[-1].event_type == OrderTransitionEventType.BROKER_ACKNOWLEDGED


def test_run_paper_order_submission_broker_raise_leaves_no_success_commit(
    migrated_paper_db: str,
) -> None:
    """DB-05: when the broker call raises, the success-persist session is
    never entered -- no success transition is committed (partial success is
    never treated as success)."""
    _seed_approved_risk_batch()
    settings = load_settings()
    execution_service = _BrokerFailsOnSubmitExecutionService()

    with pytest.raises(RuntimeError, match="broker unavailable"):
        run_paper_order_submission(
            "trend_following_daily",
            as_of_session=date(2024, 1, 5),
            settings=settings,
            execution_service=execution_service,
            trigger_source="broker_raise_test",
        )

    with session_scope(settings) as session:
        paper_orders = session.execute(select(PaperOrder)).scalars().all()
        # Only the first candidate reaches the broker before the exception
        # propagates out of the loop; the second candidate's PaperOrder row
        # is never created.
        assert len(paper_orders) == 1
        failed_order = paper_orders[0]
        assert failed_order.broker_order_id is None
        assert failed_order.status == OrderLifecycleState.SUBMISSION_FAILED
        assert failed_order.last_submission_error == "broker unavailable"
        events = session.execute(
            select(OrderEvent)
            .where(OrderEvent.paper_order_id == failed_order.id)
            .order_by(OrderEvent.event_at.asc())
        ).scalars().all()

    assert [event.event_type for event in events] == [
        OrderTransitionEventType.INTENT_REGISTERED,
        OrderTransitionEventType.SUBMISSION_FAILED,
    ]


def test_broker_submit_call_runs_outside_open_transaction_boundary(
    migrated_paper_db: str,
) -> None:
    """DB-04: the broker call is made with no open session/transaction
    holding the order it is submitting -- an independent connection can
    already see the pre-broker intent write as committed."""

    class _VisibilityProbeExecutionService(ExecutionService):
        def __init__(self, *, settings) -> None:
            self._settings = settings
            self.observed_statuses: list[OrderLifecycleState] = []

        def describe(self) -> dict[str, object]:
            return {"service": "execution", "status": "available", "provider": "visibility_probe"}

        def submit_order(self, intent: OrderIntent) -> OrderSubmissionResult:
            # A fresh, independent session/connection must already see the
            # pending order as committed -- proving no open transaction from
            # the calling code is holding it uncommitted right now.
            with session_scope(self._settings) as probe_session:
                order = probe_session.execute(
                    select(PaperOrder).where(PaperOrder.client_order_id == intent.client_order_id)
                ).scalar_one()
                self.observed_statuses.append(order.status)
            return OrderSubmissionResult(
                client_order_id=intent.client_order_id,
                broker_order_id=f"probe-{intent.symbol.lower()}-001",
                symbol=intent.symbol,
                side=intent.side,
                quantity=intent.quantity,
                order_type=intent.order_type,
                time_in_force=intent.time_in_force,
                status=ExecutionOrderStatus.PENDING,
                broker_status="new",
                submitted_at=datetime(2024, 1, 5, 14, 35, tzinfo=UTC),
                raw_payload={"id": f"probe-{intent.symbol.lower()}-001"},
            )

    _seed_approved_risk_batch()
    settings = load_settings()
    execution_service = _VisibilityProbeExecutionService(settings=settings)

    report = run_paper_order_submission(
        "trend_following_daily",
        as_of_session=date(2024, 1, 5),
        settings=settings,
        execution_service=execution_service,
        trigger_source="broker_outside_transaction_test",
    )

    assert report.result_summary["submitted_count"] == 2
    assert len(execution_service.observed_statuses) == 2
    assert all(
        status == OrderLifecycleState.PENDING_SUBMISSION
        for status in execution_service.observed_statuses
    )


def test_partial_failure_after_broker_success_schedules_reconciliation(
    migrated_paper_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DB-06: when the broker already succeeded but the post-broker persist
    rolls back, `schedule_reconciliation_after_partial_failure` is invoked
    with correct attribution and the original exception still propagates."""
    _seed_approved_risk_batch()
    settings = load_settings()
    execution_service = FakeExecutionService()

    broker_success_event_types = {
        OrderTransitionEventType.BROKER_ACKNOWLEDGED,
        OrderTransitionEventType.BROKER_PARTIALLY_FILLED,
        OrderTransitionEventType.BROKER_FILLED,
        OrderTransitionEventType.BROKER_CANCELED,
        OrderTransitionEventType.BROKER_REJECTED,
        OrderTransitionEventType.BROKER_EXPIRED,
        OrderTransitionEventType.BROKER_STATUS_UNKNOWN,
    }
    original_apply_order_transition = paper_submit_orders_module.apply_order_transition

    def _raising_apply_order_transition(order_id, request, *, session=None, settings=None):
        if request.event_type in broker_success_event_types:
            raise RuntimeError("simulated post-broker persist failure")
        return original_apply_order_transition(order_id, request, session=session, settings=settings)

    monkeypatch.setattr(paper_submit_orders_module, "apply_order_transition", _raising_apply_order_transition)

    scheduled_calls: list[dict[str, object]] = []
    original_schedule = paper_submit_orders_module.schedule_reconciliation_after_partial_failure

    def _spy_schedule(*args, **kwargs):
        scheduled_calls.append(kwargs)
        return original_schedule(*args, **kwargs)

    monkeypatch.setattr(
        paper_submit_orders_module, "schedule_reconciliation_after_partial_failure", _spy_schedule
    )

    with pytest.raises(RuntimeError, match="simulated post-broker persist failure"):
        run_paper_order_submission(
            "trend_following_daily",
            as_of_session=date(2024, 1, 5),
            settings=settings,
            execution_service=execution_service,
            trigger_source="partial_failure_test",
        )

    assert len(execution_service.submitted_intents) == 1
    submitted_intent = execution_service.submitted_intents[0]

    assert len(scheduled_calls) == 1
    call = scheduled_calls[0]
    assert call["strategy_id"] == "trend_following_daily"
    assert call["client_order_id"] == submitted_intent.client_order_id
    assert call["broker_order_id"] == f"fake-{submitted_intent.symbol.lower()}-001"
    assert call["trigger_source"] == "partial_failure_test"
    assert isinstance(call["error"], RuntimeError)

    # The helper's own durable hand-off (independent session_scope) landed
    # even though the triggering transaction rolled back.
    with session_scope(settings) as session:
        paper_orders = session.execute(select(PaperOrder)).scalars().all()
        scheduled_events = session.execute(
            select(ExecutionEvent).where(ExecutionEvent.event_type == "reconciliation_scheduled")
        ).scalars().all()

    assert len(paper_orders) == 1
    # The rolled-back write never landed: broker_order_id stays unset even
    # though the broker itself already accepted the order.
    assert paper_orders[0].broker_order_id is None
    assert len(scheduled_events) == 1
    assert scheduled_events[0].details["client_order_id"] == submitted_intent.client_order_id
    assert scheduled_events[0].details["broker_order_id"] == f"fake-{submitted_intent.symbol.lower()}-001"


def test_broker_call_failure_has_no_rollback_divergence_and_skips_reconciliation_scheduling(
    migrated_paper_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DB-06: if the broker call itself failed (no side effect), this is a
    clean failure, not a broker/local divergence -- reconciliation must NOT
    be scheduled on that path."""
    _seed_approved_risk_batch()
    settings = load_settings()
    execution_service = _BrokerFailsOnSubmitExecutionService()

    scheduled_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        paper_submit_orders_module,
        "schedule_reconciliation_after_partial_failure",
        lambda *args, **kwargs: scheduled_calls.append(kwargs),
    )

    with pytest.raises(RuntimeError, match="broker unavailable"):
        run_paper_order_submission(
            "trend_following_daily",
            as_of_session=date(2024, 1, 5),
            settings=settings,
            execution_service=execution_service,
            trigger_source="broker_failure_no_reconciliation_test",
        )

    assert scheduled_calls == []
