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
from trading_platform.services.reconciliation import (
    apply_reconciliation_corrections,
    reconcile_paper_execution,
)
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
    """Re-homed for RECON-04: reconcile is read-only (asserted first); the
    sync-failure-count mutation this test used to pin inside reconcile now happens only
    when ``apply_reconciliation_corrections`` is explicitly invoked afterward, proving
    the behavior moved rather than vanished.
    """
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
    assert {finding.event_type for finding in report.findings} >= {"MISSING_BROKER"}

    with session_scope(settings) as session:
        paper_order = session.get(PaperOrder, paper_order_id)
        assert paper_order is not None
        events = session.execute(select(ExecutionEvent).order_by(ExecutionEvent.event_at.asc())).scalars().all()
        reconciliation_runs = session.execute(
            select(StrategyRun).where(StrategyRun.run_type == StrategyRunType.RECONCILIATION)
        ).scalars().all()

    # Read-only reconcile alone leaves sync-failure state untouched (RECON-04 separation).
    assert paper_order.sync_failure_count == 0
    assert paper_order.last_sync_error is None
    assert len(events) >= 1
    assert len(reconciliation_runs) == 1
    assert reconciliation_runs[0].status == StrategyRunStatus.SUCCEEDED

    mutated_count = apply_reconciliation_corrections(
        "trend_following_daily",
        report=report,
        settings=settings,
    )

    with session_scope(settings) as session:
        corrected_order = session.get(PaperOrder, paper_order_id)
        assert corrected_order is not None

    # The mutation now happens only via the explicit corrective entrypoint.
    assert mutated_count >= 1
    assert corrected_order.sync_failure_count == 1
    assert corrected_order.last_sync_error is not None


def test_reconciliation_persists_clean_event_and_resets_sync_failures(
    migrated_reconciliation_db: str,
) -> None:
    """Re-homed for RECON-04: a clean reconcile (zero findings) leaves sync-failure
    state untouched by reconcile itself; calling ``apply_reconciliation_corrections``
    afterward is what resets any prior sync-failure count/error back to zero/None.
    """
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
    assert report.finding_count == 0
    assert report.findings == ()

    with session_scope(settings) as session:
        paper_order = session.get(PaperOrder, paper_order_id)
        assert paper_order is not None
        events = session.execute(select(ExecutionEvent).order_by(ExecutionEvent.event_at.asc())).scalars().all()

    # Reconcile alone (RECON-04) does NOT reset the pre-existing sync-failure state
    # seeded by _seed_local_fill -- no synthetic "reconciliation_clean" event either.
    assert paper_order.sync_failure_count == 3
    assert paper_order.last_sync_error == "stale mismatch"
    assert len(events) == 0

    mutated_count = apply_reconciliation_corrections(
        "trend_following_daily",
        report=report,
        settings=settings,
    )

    with session_scope(settings) as session:
        corrected_order = session.get(PaperOrder, paper_order_id)
        assert corrected_order is not None

    # The explicit corrective step resets the stale sync-failure state to zero/None
    # since no finding names this order this run.
    assert mutated_count == 1
    assert corrected_order.sync_failure_count == 0
    assert corrected_order.last_sync_error is None
    assert corrected_order.last_sync_failure_at is None


def test_reconciliation_blocks_after_repeated_submission_failures(
    migrated_reconciliation_db: str,
) -> None:
    """Threshold-still-blocks test (D2): a SUBMISSION_FAILED order past
    ``repeated_failure_threshold`` still trips ``blocks_execution`` via the read-only
    ``threshold_breach`` evaluation -- WITHOUT any sync_failure_count mutation (the
    increment relocates to the 09-04 corrective path).
    """
    paper_order_id, _client_order_id = _seed_existing_execution_state(
        status="submission_failed",
        broker_order_id=None,
        broker_status=None,
        submission_attempt_count=3,
        last_submission_error="timed out",
    )
    settings = load_settings()

    with session_scope(settings) as session:
        before_order = session.get(PaperOrder, paper_order_id)
        assert before_order is not None
        before_sync_failure_count = before_order.sync_failure_count
        before_last_sync_error = before_order.last_sync_error

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

    with session_scope(settings) as session:
        after_order = session.get(PaperOrder, paper_order_id)
        assert after_order is not None
        reconciliation_run = session.execute(
            select(StrategyRun).where(StrategyRun.run_type == StrategyRunType.RECONCILIATION)
        ).scalars().one()

    # No execution-state write: the submission-failure count/error are untouched by
    # reconcile (RECON-03) -- the block comes purely from the read-only evaluation.
    assert after_order.sync_failure_count == before_sync_failure_count
    assert after_order.last_sync_error == before_last_sync_error

    threshold_breach = reconciliation_run.result_summary["threshold_breach"]
    assert any(entry["reason"] == "submission_failure_threshold_exceeded" for entry in threshold_breach)


def test_reconciliation_does_not_mutate_execution_state(
    migrated_reconciliation_db: str,
) -> None:
    """No-mutation invariant test (RECON-03): reconcile is read-only over execution
    state. Runs against a DIVERGENT broker fixture (empty broker orders/fills/
    positions and a divergent account) so findings AND account_divergence both fire,
    then asserts PaperOrder/Position/AccountSnapshot rows are byte-for-byte unchanged
    while the StrategyRun report + ExecutionEvent findings WERE written.
    """
    paper_order_id, _client_order_id = _seed_existing_execution_state()
    _seed_open_position()
    _seed_account_snapshot(
        cash="98797.500000",
        buying_power="98797.500000",
        total_equity="100012.500000",
        gross_exposure="1215.000000",
        open_positions=1,
    )
    settings = load_settings()

    with session_scope(settings) as session:
        before_order = session.get(PaperOrder, paper_order_id)
        assert before_order is not None
        before_order_state = (
            before_order.sync_failure_count,
            before_order.last_sync_error,
            before_order.last_sync_failure_at,
            before_order.status,
            before_order.broker_status,
        )
        before_positions = session.execute(select(Position)).scalars().all()
        before_position_state = [
            (position.id, position.quantity, position.average_entry_price, position.cost_basis, position.status)
            for position in before_positions
        ]
        before_snapshots = session.execute(select(AccountSnapshot)).scalars().all()
        before_snapshot_state = [
            (
                snapshot.id,
                snapshot.cash,
                snapshot.buying_power,
                snapshot.total_equity,
                snapshot.gross_exposure,
                snapshot.open_positions,
            )
            for snapshot in before_snapshots
        ]

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

    # Divergence WAS detected and the report blocks -- this is not a vacuous no-op run.
    assert report.blocks_execution is True
    assert report.finding_count > 0

    with session_scope(settings) as session:
        after_order = session.get(PaperOrder, paper_order_id)
        assert after_order is not None
        after_order_state = (
            after_order.sync_failure_count,
            after_order.last_sync_error,
            after_order.last_sync_failure_at,
            after_order.status,
            after_order.broker_status,
        )
        after_positions = session.execute(select(Position)).scalars().all()
        after_position_state = [
            (position.id, position.quantity, position.average_entry_price, position.cost_basis, position.status)
            for position in after_positions
        ]
        after_snapshots = session.execute(select(AccountSnapshot)).scalars().all()
        after_snapshot_state = [
            (
                snapshot.id,
                snapshot.cash,
                snapshot.buying_power,
                snapshot.total_equity,
                snapshot.gross_exposure,
                snapshot.open_positions,
            )
            for snapshot in after_snapshots
        ]
        events = session.execute(select(ExecutionEvent)).scalars().all()
        reconciliation_runs = session.execute(
            select(StrategyRun).where(StrategyRun.run_type == StrategyRunType.RECONCILIATION)
        ).scalars().all()

    assert after_order_state == before_order_state
    assert after_position_state == before_position_state
    assert after_snapshot_state == before_snapshot_state

    # But the materialized report itself WAS written, with closed-enum event_types
    # (RECON-09) tied back to their source snapshot identity/ids in `details`.
    assert len(events) > 0
    assert {event.event_type for event in events} == {"MISSING_BROKER"}
    assert len(reconciliation_runs) == 1
    assert reconciliation_runs[0].status == StrategyRunStatus.SUCCEEDED

    order_events = [event for event in events if event.paper_order_id is not None]
    position_events = [event for event in events if event.paper_order_id is None]
    assert len(order_events) == 1
    assert len(position_events) == 1

    # Order finding ties back to its source PaperOrder id.
    assert order_events[0].paper_order_id == paper_order_id
    assert order_events[0].details["paper_order_id"] == str(paper_order_id)

    # Position finding ties back to its source identity (symbol, account, side) --
    # "account" only appears here via _finding_event_dict's identity augmentation,
    # since the matcher's own position-finding builders never set it.
    assert position_events[0].details["symbol"] == "AAPL"
    assert position_events[0].details["account"] == "paper"
    assert position_events[0].details["side"] == "LONG"


def test_reconciliation_clean_run_emits_empty_report(
    migrated_reconciliation_db: str,
) -> None:
    """Clean-run test (RECON-09/D3): a clean/flat reconcile still emits the
    materialized StrategyRun report with result_summary.finding_count == 0, ZERO
    ExecutionEvent findings, and blocks_execution == False -- no synthetic
    'reconciliation_clean' finding.
    """
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
    assert report.finding_count == 0
    assert report.findings == ()

    with session_scope(settings) as session:
        events = session.execute(select(ExecutionEvent)).scalars().all()
        reconciliation_run = session.execute(
            select(StrategyRun).where(StrategyRun.run_type == StrategyRunType.RECONCILIATION)
        ).scalars().one()

    assert len(events) == 0
    assert reconciliation_run.result_summary["finding_count"] == 0
    assert reconciliation_run.result_summary["account_divergence"] == {}
    assert reconciliation_run.result_summary["threshold_breach"] == []


def test_reconciliation_blocks_when_account_snapshot_missing_with_positions(
    migrated_reconciliation_db: str,
) -> None:
    """Account-missing-blocks test (D1/B1): local and broker positions/orders/fills
    match cleanly (the matcher emits NO findings) but NO AccountSnapshot row has ever
    been persisted for the strategy. reconcile_paper_execution must still return
    blocks_execution=True via the account_snapshot_missing_locally sub-flag, with
    zero execution-state writes -- this is the branch a literal reading of D1 would
    silently drop.
    """
    paper_order_id, client_order_id = _seed_existing_execution_state()
    _seed_local_fill(paper_order_id=paper_order_id)
    _seed_open_position()
    # Deliberately NOT calling _seed_account_snapshot: no AccountSnapshot row exists.
    settings = load_settings()

    with session_scope(settings) as session:
        before_order = session.get(PaperOrder, paper_order_id)
        assert before_order is not None
        before_order_state = (before_order.sync_failure_count, before_order.last_sync_error)

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

    # The matcher itself finds nothing to report -- positions/orders/fills all match.
    assert report.finding_count == 0
    # But the account has never been synced locally, and positions exist -- blocks.
    assert report.blocks_execution is True

    with session_scope(settings) as session:
        after_order = session.get(PaperOrder, paper_order_id)
        assert after_order is not None
        after_order_state = (after_order.sync_failure_count, after_order.last_sync_error)
        reconciliation_run = session.execute(
            select(StrategyRun).where(StrategyRun.run_type == StrategyRunType.RECONCILIATION)
        ).scalars().one()

    assert after_order_state == before_order_state
    assert reconciliation_run.result_summary["account_divergence"]["account_snapshot_missing_locally"] is True


def test_reconciliation_does_not_block_when_never_synced_and_flat(
    migrated_reconciliation_db: str,
) -> None:
    """Complementary non-blocking case (D1/B2): the account has never been synced
    locally, but the book is flat (no broker or local positions) -- reconcile must
    NOT block. This pins the branch alongside B1 so a literal D1 reading cannot
    silently drop either half of the contract.
    """
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

    assert report.blocks_execution is False
    assert report.finding_count == 0

    with session_scope(settings) as session:
        reconciliation_run = session.execute(
            select(StrategyRun).where(StrategyRun.run_type == StrategyRunType.RECONCILIATION)
        ).scalars().one()

    assert reconciliation_run.result_summary["account_divergence"] == {}


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
    assert "STATE_MISMATCH" not in {finding.event_type for finding in report.findings}


def test_reconciliation_module_routes_lifecycle_through_order_state_machine() -> None:
    source = (Path(__file__).resolve().parents[1] / "src/trading_platform/services/reconciliation.py").read_text()

    assert "apply_order_transition" in source
    assert re.search(r"\b(?:pending_order|persisted_order|existing_order|local_order|paper_order)\.status\s*=(?!=)", source) is None


def test_reconcile_paper_execution_never_calls_apply_reconciliation_corrections() -> None:
    """RECON-04 static invariant: reconcile_paper_execution's own body never references
    apply_reconciliation_corrections -- the two functions share no call path.
    """
    source = (Path(__file__).resolve().parents[1] / "src/trading_platform/services/reconciliation.py").read_text()

    reconcile_start = source.index("def reconcile_paper_execution(")
    next_def_start = source.index("\ndef ", reconcile_start + 1)
    reconcile_body = source[reconcile_start:next_def_start]

    assert "def apply_reconciliation_corrections" in source
    assert "apply_reconciliation_corrections" not in reconcile_body
