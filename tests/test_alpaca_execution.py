from __future__ import annotations

import json
import os
import sys
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import psycopg
import pytest
from alembic import command
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.migrate import build_alembic_config
from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.core.settings import AlpacaBrokerSettings
from trading_platform.db.models import PaperOrder, RiskEvent, StrategyRun, StrategyRunStatus, StrategyRunType
from trading_platform.db.models.symbol import Symbol
from trading_platform.db.session import clear_engine_cache, session_scope
from trading_platform.services.alpaca import AlpacaAuthError, AlpacaClient, AlpacaExecutionService
from trading_platform.services.bootstrap import ensure_strategy_record
from trading_platform.services.execution import (
    ExecutionOrderStatus,
    ExecutionService,
    OrderIntent,
    OrderSide,
    OrderSubmissionResult,
    run_paper_order_submission,
)
from trading_platform.strategies.registry import build_default_registry


def _alpaca_settings() -> AlpacaBrokerSettings:
    return AlpacaBrokerSettings(
        base_url="https://paper-api.alpaca.markets",
        api_key="test-key",
        api_secret="test-secret",
        max_retries=2,
        retry_backoff_factor=0.01,
        timeout_seconds=5.0,
    )


def _order_intent() -> OrderIntent:
    return OrderIntent(
        strategy_id="trend_following_daily",
        symbol="AAPL",
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        intended_session=date(2024, 1, 5),
        client_order_id="tp-20240105-aapl-123456",
    )


def _success_payload() -> dict[str, object]:
    return {
        "id": "broker-order-123",
        "client_order_id": "tp-20240105-aapl-123456",
        "symbol": "AAPL",
        "side": "buy",
        "qty": "10",
        "type": "market",
        "time_in_force": "day",
        "status": "new",
        "submitted_at": "2024-01-05T14:31:00Z",
    }


def test_alpaca_client_requires_credentials() -> None:
    with pytest.raises(AlpacaAuthError):
        AlpacaClient(AlpacaBrokerSettings(api_key="", api_secret=""))

    with pytest.raises(AlpacaAuthError):
        AlpacaClient(AlpacaBrokerSettings(api_key="key", api_secret=""))


def test_alpaca_execution_maps_payload_and_normalizes_response() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = {
            "APCA-API-KEY-ID": request.headers["APCA-API-KEY-ID"],
            "APCA-API-SECRET-KEY": request.headers["APCA-API-SECRET-KEY"],
        }
        captured["path"] = request.url.path
        captured["json"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json=_success_payload())

    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport, base_url="https://paper-api.alpaca.markets")
    client = AlpacaClient(_alpaca_settings(), http_client=http_client)
    service = AlpacaExecutionService(_alpaca_settings(), client=client)

    result = service.submit_order(_order_intent())

    assert captured["path"] == "/v2/orders"
    assert captured["headers"] == {
        "APCA-API-KEY-ID": "test-key",
        "APCA-API-SECRET-KEY": "test-secret",
    }
    assert captured["json"] == {
        "symbol": "AAPL",
        "qty": "10",
        "side": "buy",
        "type": "market",
        "time_in_force": "day",
        "client_order_id": "tp-20240105-aapl-123456",
    }
    assert result.client_order_id == "tp-20240105-aapl-123456"
    assert result.broker_order_id == "broker-order-123"
    assert result.status == ExecutionOrderStatus.PENDING
    assert result.broker_status == "new"
    assert result.submitted_at is not None
    assert result.submitted_at.isoformat() == "2024-01-05T14:31:00+00:00"
    assert service.describe()["provider"] == "alpaca"

    service.close()
    http_client.close()


def test_alpaca_client_retries_transient_transport_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise httpx.ReadTimeout("timed out", request=request)
        return httpx.Response(200, json=_success_payload())

    monkeypatch.setattr("trading_platform.services.alpaca.time.sleep", lambda *_: None)

    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport, base_url="https://paper-api.alpaca.markets")
    client = AlpacaClient(_alpaca_settings(), http_client=http_client)

    result = client.submit_order(_order_intent())

    assert attempts["count"] == 2
    assert result.broker_order_id == "broker-order-123"

    client.close()
    http_client.close()


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
def migrated_execution_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    database_name = f"alpaca_execution_{uuid.uuid4().hex[:8]}"
    admin_params = _admin_connection_settings()

    try:
        with _connect_admin(admin_params) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f'CREATE DATABASE "{database_name}"')
    except psycopg.Error as exc:
        pytest.fail(
            "PostgreSQL is required for tests/test_alpaca_execution.py. "
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
                cursor.execute(f'DROP DATABASE IF EXISTS "{database_name}" WITH (FORCE)')


class FakeExecutionService(ExecutionService):
    def __init__(self) -> None:
        self.submitted_intents: list[OrderIntent] = []

    def describe(self) -> dict[str, object]:
        return {"service": "execution", "status": "available", "provider": "fake"}

    def submit_order(self, intent: OrderIntent) -> OrderSubmissionResult:
        self.submitted_intents.append(intent)
        return OrderSubmissionResult(
            client_order_id=intent.client_order_id,
            broker_order_id=f"alpaca-{intent.symbol.lower()}-001",
            symbol=intent.symbol,
            side=intent.side,
            quantity=intent.quantity,
            order_type=intent.order_type,
            time_in_force=intent.time_in_force,
            status=ExecutionOrderStatus.PENDING,
            broker_status="new",
            submitted_at=datetime(2024, 1, 5, 14, 35, tzinfo=UTC),
            raw_payload={
                "id": f"alpaca-{intent.symbol.lower()}-001",
                "client_order_id": intent.client_order_id,
                "symbol": intent.symbol,
                "side": intent.side.value,
                "qty": str(intent.quantity),
                "type": intent.order_type.value,
                "time_in_force": intent.time_in_force.value,
                "status": "new",
                "submitted_at": "2024-01-05T14:35:00Z",
            },
        )


def _seed_approved_risk_batch() -> tuple[uuid.UUID, uuid.UUID]:
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
            parameters_snapshot={"as_of_session": "2024-01-05"},
            result_summary={"stage": "completed", "as_of_session": "2024-01-05"},
        )
        session.add(risk_run)
        session.flush()

        approved_event = RiskEvent(
            strategy_run_id=risk_run.id,
            symbol_id=aapl.id,
            session_date=date(2024, 1, 5),
            signal_direction="long",
            signal_reason="trend_entry",
            outcome="approved",
            decision_code="approved",
            decision_reason="Approved for paper execution.",
            reference_price=Decimal("120.000000"),
            proposed_quantity=Decimal("10.000000"),
            proposed_notional=Decimal("1200.000000"),
            risk_metadata={"remaining_cash": 98800.0},
        )
        rejected_event = RiskEvent(
            strategy_run_id=risk_run.id,
            symbol_id=msft.id,
            session_date=date(2024, 1, 5),
            signal_direction="long",
            signal_reason="trend_entry",
            outcome="rejected",
            decision_code="max_positions",
            decision_reason="Strategy max positions reached.",
            reference_price=Decimal("300.000000"),
            proposed_quantity=None,
            proposed_notional=None,
            risk_metadata={},
        )
        session.add_all([approved_event, rejected_event])
        session.flush()
        return risk_run.id, approved_event.id


def _seed_followup_risk_event(*, quantity: str) -> tuple[uuid.UUID, uuid.UUID]:
    settings = load_settings()
    registry = build_default_registry(settings)
    strategy = registry.resolve("trend_following_daily")

    with session_scope(settings) as session:
        strategy_record = ensure_strategy_record(session, strategy.metadata)
        aapl = session.execute(select(Symbol).where(Symbol.ticker == "AAPL")).scalar_one()
        risk_run = StrategyRun(
            strategy_id=strategy_record.id,
            run_type=StrategyRunType.RISK_EVALUATION,
            status=StrategyRunStatus.SUCCEEDED,
            trigger_source="followup_risk_seed",
            parameters_snapshot={"as_of_session": "2024-01-05"},
            result_summary={"stage": "completed", "as_of_session": "2024-01-05"},
        )
        session.add(risk_run)
        session.flush()

        approved_event = RiskEvent(
            strategy_run_id=risk_run.id,
            symbol_id=aapl.id,
            session_date=date(2024, 1, 5),
            signal_direction="long",
            signal_reason="scaled_entry",
            outcome="approved",
            decision_code="approved",
            decision_reason="Approved for paper execution.",
            reference_price=Decimal("120.000000"),
            proposed_quantity=Decimal(quantity),
            proposed_notional=Decimal(quantity) * Decimal("120.000000"),
            risk_metadata={"remaining_cash": 98800.0},
        )
        session.add(approved_event)
        session.flush()
        return risk_run.id, approved_event.id


def test_run_paper_order_submission_persists_idempotent_paper_orders(
    migrated_execution_db: str,
) -> None:
    _risk_run_id, approved_event_id = _seed_approved_risk_batch()
    settings = load_settings()
    execution_service = FakeExecutionService()

    report = run_paper_order_submission(
        "trend_following_daily",
        as_of_session=date(2024, 1, 5),
        trigger_source="test_suite",
        settings=settings,
        execution_service=execution_service,
    )

    assert report.status == StrategyRunStatus.SUCCEEDED.value
    assert report.result_summary["approved_candidate_count"] == 1
    assert report.result_summary["submitted_count"] == 1
    assert len(execution_service.submitted_intents) == 1

    first_intent = execution_service.submitted_intents[0]
    assert first_intent.client_order_id.startswith("tp-20240105-aapl-")

    with session_scope(settings) as session:
        paper_orders = session.execute(select(PaperOrder)).scalars().all()
        strategy_run = session.execute(
            select(StrategyRun).where(StrategyRun.id == uuid.UUID(report.run_id))
        ).scalar_one()

    assert strategy_run.run_type == StrategyRunType.PAPER_EXECUTION
    assert len(paper_orders) == 1
    assert paper_orders[0].source_risk_event_id == approved_event_id
    assert paper_orders[0].broker_order_id == "alpaca-aapl-001"
    assert paper_orders[0].status == "submitted"
    assert paper_orders[0].client_order_id == first_intent.client_order_id

    second_execution_service = FakeExecutionService()
    second_report = run_paper_order_submission(
        "trend_following_daily",
        as_of_session=date(2024, 1, 5),
        trigger_source="test_suite_repeat",
        settings=settings,
        execution_service=second_execution_service,
    )

    assert second_report.result_summary["submitted_count"] == 0
    assert second_report.result_summary["existing_count"] == 1
    assert second_execution_service.submitted_intents == []


def test_run_paper_order_submission_versions_material_change_in_alpaca_flow(
    migrated_execution_db: str,
) -> None:
    _risk_run_id, _approved_event_id = _seed_approved_risk_batch()
    settings = load_settings()

    class UniqueExecutionService(FakeExecutionService):
        def submit_order(self, intent: OrderIntent) -> OrderSubmissionResult:
            self.submitted_intents.append(intent)
            broker_order_id = f"alpaca-{intent.symbol.lower()}-{intent.intent_version:03d}"
            return OrderSubmissionResult(
                client_order_id=intent.client_order_id,
                broker_order_id=broker_order_id,
                symbol=intent.symbol,
                side=intent.side,
                quantity=intent.quantity,
                order_type=intent.order_type,
                time_in_force=intent.time_in_force,
                status=ExecutionOrderStatus.PENDING,
                broker_status="new",
                submitted_at=datetime(2024, 1, 5, 14, 35, tzinfo=UTC),
                raw_payload={
                    "id": broker_order_id,
                    "client_order_id": intent.client_order_id,
                    "symbol": intent.symbol,
                    "side": intent.side.value,
                    "qty": str(intent.quantity),
                    "type": intent.order_type.value,
                    "time_in_force": intent.time_in_force.value,
                    "status": "new",
                    "submitted_at": "2024-01-05T14:35:00Z",
                },
            )

    initial_execution_service = UniqueExecutionService()
    initial_report = run_paper_order_submission(
        "trend_following_daily",
        as_of_session=date(2024, 1, 5),
        trigger_source="initial_submit",
        settings=settings,
        execution_service=initial_execution_service,
    )
    followup_risk_run_id, followup_event_id = _seed_followup_risk_event(quantity="12.000000")

    versioned_execution_service = UniqueExecutionService()
    versioned_report = run_paper_order_submission(
        "trend_following_daily",
        as_of_session=date(2024, 1, 5),
        risk_run_id=str(followup_risk_run_id),
        trigger_source="version_submit",
        settings=settings,
        execution_service=versioned_execution_service,
    )

    assert initial_report.result_summary["submitted_count"] == 1
    assert versioned_report.result_summary["submitted_count"] == 1
    assert versioned_report.result_summary["versioned_count"] == 1
    assert len(versioned_execution_service.submitted_intents) == 1
    assert versioned_execution_service.submitted_intents[0].intent_version == 2

    with session_scope(settings) as session:
        paper_orders = session.execute(
            select(PaperOrder).order_by(PaperOrder.intent_version.asc(), PaperOrder.created_at.asc())
        ).scalars().all()

    assert len(paper_orders) == 2
    assert paper_orders[1].intent_version == 2
    assert paper_orders[1].supersedes_paper_order_id == paper_orders[0].id
    assert paper_orders[1].source_risk_event_id == followup_event_id
