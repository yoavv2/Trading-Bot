from __future__ import annotations

import os
import socket
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
    OrderEvent,
    OrderLifecycleState,
    OrderTransitionEventType,
    OrderTransitionOutcome,
    PaperOrder,
    RiskEvent,
    StrategyRun,
    StrategyRunStatus,
    StrategyRunType,
    Symbol,
)
from trading_platform.db.session import clear_engine_cache, session_scope
from trading_platform.services.bootstrap import ensure_strategy_record
from trading_platform.services.execution.idempotency import build_intent_hash
from trading_platform.services.execution.transition import (
    IllegalOrderTransition,
    OrderTransitionRequest,
    apply_order_transition,
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
def migrated_order_state_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    database_name = f"order_state_machine_{uuid.uuid4().hex[:8]}"
    admin_params = _admin_connection_settings()

    try:
        with _connect_admin(admin_params) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f'CREATE DATABASE "{database_name}"')
    except psycopg.Error as exc:
        pytest.fail(
            "PostgreSQL is required for tests/test_order_state_machine.py. "
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


def _seed_paper_order(
    *,
    status: OrderLifecycleState = OrderLifecycleState.PENDING_SUBMISSION,
) -> tuple[uuid.UUID, uuid.UUID]:
    settings = load_settings()
    registry = build_default_registry(settings)
    strategy = registry.resolve("trend_following_daily")

    with session_scope(settings) as session:
        strategy_record = ensure_strategy_record(session, strategy.metadata)
        symbol = Symbol(ticker="AAPL", active=True)
        session.add(symbol)
        session.flush()

        risk_run = StrategyRun(
            strategy_id=strategy_record.id,
            run_type=StrategyRunType.RISK_EVALUATION,
            status=StrategyRunStatus.SUCCEEDED,
            trigger_source="test_suite",
            parameters_snapshot={"as_of_session": date(2024, 1, 5).isoformat()},
            result_summary={"stage": "completed"},
        )
        session.add(risk_run)
        session.flush()

        risk_event = RiskEvent(
            strategy_run_id=risk_run.id,
            symbol_id=symbol.id,
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
        session.add(risk_event)
        session.flush()

        execution_run = StrategyRun(
            strategy_id=strategy_record.id,
            run_type=StrategyRunType.PAPER_EXECUTION,
            status=StrategyRunStatus.RUNNING,
            trigger_source="test_suite",
            parameters_snapshot={"as_of_session": date(2024, 1, 5).isoformat()},
            result_summary={"stage": "running"},
        )
        session.add(execution_run)
        session.flush()

        paper_order = PaperOrder(
            strategy_run_id=execution_run.id,
            source_risk_event_id=risk_event.id,
            symbol_id=symbol.id,
            intended_session_date=date(2024, 1, 5),
            side="buy",
            quantity=Decimal("10.000000"),
            order_type="market",
            time_in_force="day",
            intent_hash=build_intent_hash(
                strategy_id=strategy.metadata.strategy_id,
                session_date=date(2024, 1, 5),
                symbol="AAPL",
                side="buy",
                quantity=Decimal("10.000000"),
            ),
            intent_version=1,
            client_order_id=f"test-aapl-{uuid.uuid4().hex[:8]}",
            broker_order_id=None,
            status=status,
            broker_payload={},
        )
        session.add(paper_order)
        session.flush()
        return paper_order.id, execution_run.id


def test_apply_order_transition_persists_accepted_transitions(
    migrated_order_state_db: str,
) -> None:
    order_id, run_id = _seed_paper_order()
    settings = load_settings()

    registered = apply_order_transition(
        order_id,
        OrderTransitionRequest(
            strategy_run_id=run_id,
            event_type=OrderTransitionEventType.INTENT_REGISTERED,
            event_at=datetime(2024, 1, 5, 14, 30, tzinfo=UTC),
            details={"source": "submission"},
        ),
        settings=settings,
    )
    acknowledged = apply_order_transition(
        order_id,
        OrderTransitionRequest(
            strategy_run_id=run_id,
            event_type=OrderTransitionEventType.BROKER_ACKNOWLEDGED,
            event_at=datetime(2024, 1, 5, 14, 35, tzinfo=UTC),
            details={"broker_status": "new"},
        ),
        settings=settings,
    )
    filled = apply_order_transition(
        order_id,
        OrderTransitionRequest(
            strategy_run_id=run_id,
            event_type=OrderTransitionEventType.BROKER_FILLED,
            event_at=datetime(2024, 1, 5, 14, 40, tzinfo=UTC),
            details={"broker_status": "filled"},
        ),
        settings=settings,
    )

    assert registered.outcome == OrderTransitionOutcome.ACCEPTED
    assert acknowledged.to_state == OrderLifecycleState.SUBMITTED
    assert filled.to_state == OrderLifecycleState.FILLED

    with session_scope(settings) as session:
        paper_order = session.get(PaperOrder, order_id)
        assert paper_order is not None
        events = session.execute(
            select(OrderEvent)
            .where(OrderEvent.paper_order_id == order_id)
            .order_by(OrderEvent.event_at.asc())
        ).scalars().all()

    assert paper_order.status == OrderLifecycleState.FILLED
    assert [event.event_type for event in events] == [
        OrderTransitionEventType.INTENT_REGISTERED,
        OrderTransitionEventType.BROKER_ACKNOWLEDGED,
        OrderTransitionEventType.BROKER_FILLED,
    ]
    assert all(event.outcome == OrderTransitionOutcome.ACCEPTED for event in events)


def test_apply_order_transition_persists_rejected_events_for_illegal_transitions(
    migrated_order_state_db: str,
) -> None:
    order_id, run_id = _seed_paper_order(status=OrderLifecycleState.FILLED)
    settings = load_settings()

    with pytest.raises(IllegalOrderTransition) as exc_info:
        apply_order_transition(
            order_id,
            OrderTransitionRequest(
                strategy_run_id=run_id,
                event_type=OrderTransitionEventType.BROKER_ACKNOWLEDGED,
                event_at=datetime(2024, 1, 5, 14, 45, tzinfo=UTC),
                details={"broker_status": "new"},
            ),
            settings=settings,
        )

    assert exc_info.value.from_state == OrderLifecycleState.FILLED
    assert exc_info.value.event_type == OrderTransitionEventType.BROKER_ACKNOWLEDGED

    with session_scope(settings) as session:
        paper_order = session.get(PaperOrder, order_id)
        assert paper_order is not None
        events = session.execute(
            select(OrderEvent)
            .where(OrderEvent.paper_order_id == order_id)
            .order_by(OrderEvent.event_at.asc())
        ).scalars().all()

    assert paper_order.status == OrderLifecycleState.FILLED
    assert len(events) == 1
    assert events[0].outcome == OrderTransitionOutcome.REJECTED
    assert events[0].from_state == OrderLifecycleState.FILLED
    assert events[0].to_state == OrderLifecycleState.FILLED
    assert events[0].details["rejected_transition"]["reason"].startswith("Illegal order transition")


def test_apply_order_transition_uses_only_the_db_boundary(
    migrated_order_state_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order_id, run_id = _seed_paper_order()
    settings = load_settings()

    with session_scope(settings) as session:
        monkeypatch.setattr(
            socket,
            "create_connection",
            lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network should not be used")),
        )
        result = apply_order_transition(
            order_id,
            OrderTransitionRequest(
                strategy_run_id=run_id,
                event_type=OrderTransitionEventType.INTENT_REGISTERED,
                event_at=datetime(2024, 1, 5, 14, 30, tzinfo=UTC),
                details={"source": "db-only"},
            ),
            session=session,
            settings=settings,
        )

    assert result.outcome == OrderTransitionOutcome.ACCEPTED
