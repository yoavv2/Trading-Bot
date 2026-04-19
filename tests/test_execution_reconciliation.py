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
    ExecutionEvent,
    PaperFill,
    PaperOrder,
    Position,
    RiskEvent,
    StrategyRun,
    StrategyRunStatus,
    StrategyRunType,
)
from trading_platform.db.models.symbol import Symbol
from trading_platform.db.session import clear_engine_cache, session_scope
from trading_platform.services.alpaca import (
    BrokerAccountSnapshot,
    BrokerFillSnapshot,
    BrokerOrderSnapshot,
    BrokerPositionSnapshot,
)
from trading_platform.services.execution import ExecutionOrderStatus, OrderSide
from trading_platform.services.order_identity import build_intent_hash
from trading_platform.services.paper_execution import build_client_order_id
from trading_platform.services.reconciliation import reconcile_paper_execution
from trading_platform.services.bootstrap import ensure_strategy_record
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
def migrated_reconciliation_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    database_name = f"execution_reconciliation_{uuid.uuid4().hex[:8]}"
    admin_params = _admin_connection_settings()

    try:
        with _connect_admin(admin_params) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f'CREATE DATABASE "{database_name}"')
    except psycopg.Error as exc:
        pytest.fail(
            "PostgreSQL is required for tests/test_execution_reconciliation.py. "
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


def _seed_existing_execution_state(
    *,
    session_date: date = date(2024, 1, 5),
    status: str = "submitted",
    broker_order_id: str | None = "existing-aapl-001",
    broker_status: str | None = "new",
    submission_attempt_count: int = 1,
    last_submission_error: str | None = None,
) -> tuple[uuid.UUID, str]:
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
            parameters_snapshot={"as_of_session": session_date.isoformat()},
            result_summary={"stage": "completed", "as_of_session": session_date.isoformat()},
        )
        session.add(risk_run)
        session.flush()

        risk_event = RiskEvent(
            strategy_run_id=risk_run.id,
            symbol_id=symbol.id,
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
        )
        session.add(risk_event)
        session.flush()

        execution_run = StrategyRun(
            strategy_id=strategy_record.id,
            run_type=StrategyRunType.PAPER_EXECUTION,
            status=StrategyRunStatus.SUCCEEDED,
            trigger_source="test_seed",
            parameters_snapshot={"as_of_session": session_date.isoformat()},
            result_summary={"stage": "completed", "as_of_session": session_date.isoformat()},
            completed_at=datetime(2024, 1, 5, 15, 0, tzinfo=UTC),
        )
        session.add(execution_run)
        session.flush()

        client_order_id = build_client_order_id(
            prefix=settings.execution.client_order_id_prefix,
            strategy_id="trend_following_daily",
            session_date=session_date,
            symbol="AAPL",
            side=OrderSide.BUY,
            quantity=Decimal("10.000000"),
        )
        paper_order = PaperOrder(
            strategy_run_id=execution_run.id,
            source_risk_event_id=risk_event.id,
            symbol_id=symbol.id,
            intended_session_date=session_date,
            side="buy",
            quantity=Decimal("10.000000"),
            order_type="market",
            time_in_force="day",
            intent_hash=build_intent_hash(
                strategy_id="trend_following_daily",
                session_date=session_date,
                symbol="AAPL",
                side=OrderSide.BUY,
                quantity=Decimal("10.000000"),
            ),
            intent_version=1,
            client_order_id=client_order_id,
            broker_order_id=broker_order_id,
            status=status,
            broker_status=broker_status,
            submitted_at=datetime(2024, 1, 5, 14, 35, tzinfo=UTC) if broker_order_id else None,
            submission_attempt_count=submission_attempt_count,
            last_submission_attempt_at=datetime(2024, 1, 5, 14, 35, tzinfo=UTC)
            if submission_attempt_count
            else None,
            last_submission_error=last_submission_error,
            broker_payload={"id": broker_order_id} if broker_order_id else {},
        )
        session.add(paper_order)
        session.flush()
        return paper_order.id, client_order_id


def _seed_open_position() -> None:
    settings = load_settings()
    registry = build_default_registry(settings)
    strategy = registry.resolve("trend_following_daily")

    with session_scope(settings) as session:
        strategy_record = ensure_strategy_record(session, strategy.metadata)
        symbol = session.execute(select(Symbol).where(Symbol.ticker == "AAPL")).scalar_one()
        session.add(
            Position(
                strategy_id=strategy_record.id,
                symbol_id=symbol.id,
                status="open",
                quantity=Decimal("10.000000"),
                average_entry_price=Decimal("120.250000"),
                cost_basis=Decimal("1202.500000"),
                opened_session_date=date(2024, 1, 5),
                opened_at=datetime(2024, 1, 5, 14, 40, tzinfo=UTC),
            )
        )


def _seed_account_snapshot(
    *,
    cash: str,
    buying_power: str,
    total_equity: str,
    gross_exposure: str,
    open_positions: int,
) -> None:
    settings = load_settings()
    registry = build_default_registry(settings)
    strategy = registry.resolve("trend_following_daily")

    with session_scope(settings) as session:
        strategy_record = ensure_strategy_record(session, strategy.metadata)
        session.add(
            AccountSnapshot(
                strategy_id=strategy_record.id,
                source_run_id=None,
                snapshot_source="broker_sync",
                snapshot_at=datetime(2024, 1, 5, 14, 45, tzinfo=UTC),
                cash=Decimal(cash),
                gross_exposure=Decimal(gross_exposure),
                total_equity=Decimal(total_equity),
                buying_power=Decimal(buying_power),
                open_positions=open_positions,
            )
        )


def _seed_local_fill(*, paper_order_id: uuid.UUID) -> None:
    settings = load_settings()

    with session_scope(settings) as session:
        paper_order = session.get(PaperOrder, paper_order_id)
        assert paper_order is not None
        session.add(
            PaperFill(
                paper_order_id=paper_order.id,
                symbol_id=paper_order.symbol_id,
                broker_fill_id="fill-aapl-001",
                broker_order_id="existing-aapl-001",
                side="buy",
                quantity=Decimal("10.000000"),
                price=Decimal("120.250000"),
                filled_at=datetime(2024, 1, 5, 14, 40, tzinfo=UTC),
                broker_payload={"id": "fill-aapl-001"},
            )
        )
        paper_order.status = "filled"
        paper_order.broker_status = "filled"
        paper_order.filled_at = datetime(2024, 1, 5, 14, 40, tzinfo=UTC)
        paper_order.sync_failure_count = 3
        paper_order.last_sync_error = "stale mismatch"


def test_reconciliation_persists_blocking_findings_and_updates_sync_failure_state(
    migrated_reconciliation_db: str,
) -> None:
    paper_order_id, _ = _seed_existing_execution_state()
    _seed_open_position()
    _seed_account_snapshot(
        cash="98797.500000",
        buying_power="98797.500000",
        total_equity="100012.500000",
        gross_exposure="1215.000000",
        open_positions=1,
    )
    settings = load_settings()

    report = reconcile_paper_execution(
        "trend_following_daily",
        as_of_session=date(2024, 1, 5),
        settings=settings,
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

    assert report.blocks_execution is True
    assert {finding.event_type for finding in report.findings} >= {
        "order_missing_from_broker",
        "position_missing_from_broker",
        "account_snapshot_mismatch",
    }

    with session_scope(settings) as session:
        paper_order = session.get(PaperOrder, paper_order_id)
        assert paper_order is not None
        events = session.execute(select(ExecutionEvent).order_by(ExecutionEvent.event_at.asc())).scalars().all()
        reconciliation_runs = session.execute(
            select(StrategyRun).where(StrategyRun.run_type == StrategyRunType.RECONCILIATION)
        ).scalars().all()

    assert paper_order.sync_failure_count == 1
    assert paper_order.last_sync_error is not None
    assert len(events) >= 3
    assert len(reconciliation_runs) == 1
    assert reconciliation_runs[0].status == StrategyRunStatus.SUCCEEDED


def test_reconciliation_persists_clean_event_and_resets_sync_failures(
    migrated_reconciliation_db: str,
) -> None:
    paper_order_id, client_order_id = _seed_existing_execution_state()
    _seed_local_fill(paper_order_id=paper_order_id)
    _seed_open_position()
    _seed_account_snapshot(
        cash="98797.500000",
        buying_power="98797.500000",
        total_equity="100012.500000",
        gross_exposure="1215.000000",
        open_positions=1,
    )
    settings = load_settings()

    report = reconcile_paper_execution(
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
        ),
    )

    assert report.blocks_execution is False
    assert [finding.event_type for finding in report.findings] == ["reconciliation_clean"]

    with session_scope(settings) as session:
        paper_order = session.get(PaperOrder, paper_order_id)
        assert paper_order is not None
        events = session.execute(select(ExecutionEvent).order_by(ExecutionEvent.event_at.asc())).scalars().all()

    assert paper_order.sync_failure_count == 0
    assert paper_order.last_sync_error is None
    assert len(events) == 1
    assert events[0].event_type == "reconciliation_clean"


def test_reconciliation_blocks_after_repeated_submission_failures(
    migrated_reconciliation_db: str,
) -> None:
    _paper_order_id, _client_order_id = _seed_existing_execution_state(
        status="submission_failed",
        broker_order_id=None,
        broker_status=None,
        submission_attempt_count=3,
        last_submission_error="timed out",
    )
    settings = load_settings()

    report = reconcile_paper_execution(
        "trend_following_daily",
        as_of_session=date(2024, 1, 5),
        settings=settings,
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

    assert report.blocks_execution is True
    assert {finding.event_type for finding in report.findings} >= {
        "submission_failure_threshold_exceeded",
    }


def test_reconciliation_prefers_client_order_id_when_version_chain_exists(
    migrated_reconciliation_db: str,
) -> None:
    predecessor_order_id, _predecessor_client_order_id = _seed_existing_execution_state(
        status="canceled",
        broker_order_id="shared-broker-id",
        broker_status="canceled",
    )
    settings = load_settings()

    with session_scope(settings) as session:
        predecessor = session.get(PaperOrder, predecessor_order_id)
        assert predecessor is not None
        predecessor_run = session.get(StrategyRun, predecessor.strategy_run_id)
        assert predecessor_run is not None

        followup_risk_run = StrategyRun(
            strategy_id=predecessor_run.strategy_id,
            run_type=StrategyRunType.RISK_EVALUATION,
            status=StrategyRunStatus.SUCCEEDED,
            trigger_source="test_followup",
            parameters_snapshot={"as_of_session": "2024-01-05"},
            result_summary={"stage": "completed", "as_of_session": "2024-01-05"},
        )
        followup_execution_run = StrategyRun(
            strategy_id=predecessor_run.strategy_id,
            run_type=StrategyRunType.PAPER_EXECUTION,
            status=StrategyRunStatus.SUCCEEDED,
            trigger_source="test_followup",
            parameters_snapshot={"as_of_session": "2024-01-05"},
            result_summary={"stage": "completed", "as_of_session": "2024-01-05"},
        )
        session.add_all([followup_risk_run, followup_execution_run])
        session.flush()

        followup_risk_event = RiskEvent(
            strategy_run_id=followup_risk_run.id,
            symbol_id=predecessor.symbol_id,
            session_date=date(2024, 1, 5),
            signal_direction="long",
            signal_reason="scaled_entry",
            outcome="approved",
            decision_code="approved",
            decision_reason="Approved for paper execution.",
            reference_price=Decimal("120.000000"),
            proposed_quantity=Decimal("12.000000"),
            proposed_notional=Decimal("1440.000000"),
            risk_metadata={"remaining_cash": 98560.0},
        )
        session.add(followup_risk_event)
        session.flush()

        successor_client_order_id = build_client_order_id(
            prefix=settings.execution.client_order_id_prefix,
            strategy_id="trend_following_daily",
            session_date=date(2024, 1, 5),
            symbol="AAPL",
            side=OrderSide.BUY,
            quantity=Decimal("12.000000"),
        )
        session.add(
            PaperOrder(
                strategy_run_id=followup_execution_run.id,
                source_risk_event_id=followup_risk_event.id,
                symbol_id=predecessor.symbol_id,
                intended_session_date=date(2024, 1, 5),
                side="buy",
                quantity=Decimal("12.000000"),
                order_type="market",
                time_in_force="day",
                intent_hash=build_intent_hash(
                    strategy_id="trend_following_daily",
                    session_date=date(2024, 1, 5),
                    symbol="AAPL",
                    side=OrderSide.BUY,
                    quantity=Decimal("12.000000"),
                ),
                intent_version=2,
                supersedes_paper_order_id=predecessor.id,
                client_order_id=successor_client_order_id,
                broker_order_id=None,
                status="submitted",
                broker_status="new",
                submitted_at=datetime(2024, 1, 5, 14, 45, tzinfo=UTC),
                broker_payload={},
            )
        )

    report = reconcile_paper_execution(
        "trend_following_daily",
        as_of_session=date(2024, 1, 5),
        settings=settings,
        broker_client=FakeBrokerClient(
            orders=[
                BrokerOrderSnapshot(
                    broker_order_id="shared-broker-id",
                    client_order_id=successor_client_order_id,
                    symbol="AAPL",
                    side=OrderSide.BUY,
                    quantity=Decimal("12.000000"),
                    status=ExecutionOrderStatus.PENDING,
                    broker_status="new",
                    submitted_at=datetime(2024, 1, 5, 14, 45, tzinfo=UTC),
                    filled_at=None,
                    canceled_at=None,
                    updated_at=datetime(2024, 1, 5, 14, 45, tzinfo=UTC),
                    raw_payload={"id": "shared-broker-id", "status": "new"},
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

    assert report.blocks_execution is False
    assert "order_status_mismatch" not in {finding.event_type for finding in report.findings}


def test_reconciliation_module_routes_lifecycle_through_order_state_machine() -> None:
    source = (Path(__file__).resolve().parents[1] / "src/trading_platform/services/reconciliation.py").read_text()

    assert "apply_order_transition" in source
    assert re.search(r"\b(?:pending_order|persisted_order|existing_order|local_order|paper_order)\.status\s*=(?!=)", source) is None
