from __future__ import annotations

import os
import sys
import uuid
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alembic import command
from scripts.migrate import build_alembic_config
from scripts.seed_phase1 import seed_phase_one

from trading_platform.api.app import create_app
from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.db.models import (
    Job,
    JobDependency,
    OrderEvent,
    OrderLifecycleState,
    OrderTransitionEventType,
    OrderTransitionOutcome,
    PaperOrder,
    RiskEvent,
    Strategy,
    StrategyRun,
    StrategyRunStatus,
    StrategyRunType,
    Symbol,
)
from trading_platform.db.session import clear_engine_cache, get_engine, session_scope
from trading_platform.services.execution.idempotency import build_intent_hash


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


def _upgrade_to_revision(revision: str) -> None:
    clear_settings_cache()
    clear_engine_cache()
    command.upgrade(build_alembic_config(), revision)


def _upgrade_to_head() -> None:
    _upgrade_to_revision("head")


@pytest.fixture()
def migrated_database(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    database_name = f"phase1_test_{uuid.uuid4().hex[:8]}"
    admin_params = _admin_connection_settings()

    try:
        with _connect_admin(admin_params) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f'CREATE DATABASE "{database_name}"')
    except psycopg.Error as exc:  # pragma: no cover - exercised when local Postgres is unavailable
        pytest.fail(
            "PostgreSQL is required for tests/test_db_migrations.py. "
            "Start the local db service first (for example `docker compose up -d db`). "
            f"Connection error: {exc}"
        )

    _set_database_env(monkeypatch, database_name)
    _upgrade_to_head()

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


def test_alembic_upgrade_creates_phase1_tables(migrated_database: str) -> None:
    settings = load_settings()
    inspector = inspect(get_engine(settings))

    assert {"alembic_version", "strategies", "strategy_runs"}.issubset(inspector.get_table_names())
    assert {column["name"] for column in inspector.get_columns("strategies")} >= {
        "id",
        "strategy_id",
        "display_name",
        "status",
        "config_reference",
    }
    assert {column["name"] for column in inspector.get_columns("strategy_runs")} >= {
        "id",
        "strategy_id",
        "run_type",
        "status",
        "started_at",
        "parameters_snapshot",
    }

    enums = {enum["name"]: set(enum["labels"]) for enum in inspector.get_enums()}
    assert enums["strategy_run_type"] >= {"dry_bootstrap", "backtest"}


def test_alembic_upgrade_creates_phase2_market_data_tables(migrated_database: str) -> None:
    settings = load_settings()
    inspector = inspect(get_engine(settings))

    table_names = set(inspector.get_table_names())
    assert {"symbols", "daily_bars", "market_data_ingestion_runs"}.issubset(table_names)

    symbol_cols = {col["name"] for col in inspector.get_columns("symbols")}
    assert symbol_cols >= {"id", "ticker", "active", "created_at", "updated_at"}

    bar_cols = {col["name"] for col in inspector.get_columns("daily_bars")}
    assert bar_cols >= {
        "id",
        "symbol_id",
        "session_date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "adjusted",
        "provider",
    }

    run_cols = {col["name"] for col in inspector.get_columns("market_data_ingestion_runs")}
    assert run_cols >= {
        "id",
        "provider",
        "from_date",
        "to_date",
        "adjusted",
        "status",
        "symbols_requested",
        "bars_upserted",
        "started_at",
    }

    # Verify the natural uniqueness constraint on daily_bars
    uq_constraints = {uc["name"] for uc in inspector.get_unique_constraints("daily_bars")}
    assert "uq_daily_bars_symbol_session_adjusted_provider" in uq_constraints


def test_alembic_upgrade_creates_phase3_backtest_tables(migrated_database: str) -> None:
    settings = load_settings()
    inspector = inspect(get_engine(settings))

    table_names = set(inspector.get_table_names())
    assert {
        "backtest_signals",
        "backtest_trades",
        "backtest_equity_snapshots",
        "backtest_metrics",
    }.issubset(table_names)

    signal_cols = {col["name"] for col in inspector.get_columns("backtest_signals")}
    assert signal_cols >= {
        "strategy_run_id",
        "symbol_id",
        "session_date",
        "direction",
        "reason",
        "close",
        "bars_available",
    }

    trade_cols = {col["name"] for col in inspector.get_columns("backtest_trades")}
    assert trade_cols >= {
        "strategy_run_id",
        "symbol_id",
        "status",
        "quantity",
        "entry_signal_session",
        "entry_fill_session",
        "entry_price",
    }

    equity_cols = {col["name"] for col in inspector.get_columns("backtest_equity_snapshots")}
    assert equity_cols >= {
        "strategy_run_id",
        "session_date",
        "cash",
        "gross_exposure",
        "total_equity",
        "open_positions",
    }

    metric_cols = {col["name"] for col in inspector.get_columns("backtest_metrics")}
    assert metric_cols >= {
        "strategy_run_id",
        "total_return_pct",
        "max_drawdown_pct",
        "trade_count",
        "win_rate_pct",
        "average_win",
        "average_loss",
        "profit_factor",
        "exposure_pct",
        "cagr_pct",
        "sharpe_ratio",
        "sortino_ratio",
        "expectancy",
        "turnover_pct",
        "best_trade",
        "worst_trade",
        "average_holding_period_sessions",
    }

    signal_constraints = {uc["name"] for uc in inspector.get_unique_constraints("backtest_signals")}
    assert "uq_backtest_signals_run_symbol_session" in signal_constraints

    equity_constraints = {uc["name"] for uc in inspector.get_unique_constraints("backtest_equity_snapshots")}
    assert "uq_backtest_equity_snapshots_run_session" in equity_constraints

    metric_constraints = {uc["name"] for uc in inspector.get_unique_constraints("backtest_metrics")}
    assert "uq_backtest_metrics_strategy_run_id" in metric_constraints


def test_alembic_upgrade_creates_phase4_portfolio_tables(migrated_database: str) -> None:
    settings = load_settings()
    inspector = inspect(get_engine(settings))

    table_names = set(inspector.get_table_names())
    assert {"positions", "account_snapshots"}.issubset(table_names)

    position_cols = {col["name"] for col in inspector.get_columns("positions")}
    assert position_cols >= {
        "strategy_id",
        "symbol_id",
        "status",
        "quantity",
        "average_entry_price",
        "cost_basis",
        "opened_session_date",
        "closed_session_date",
    }

    snapshot_cols = {col["name"] for col in inspector.get_columns("account_snapshots")}
    assert snapshot_cols >= {
        "strategy_id",
        "source_run_id",
        "snapshot_source",
        "snapshot_at",
        "cash",
        "gross_exposure",
        "total_equity",
        "buying_power",
        "open_positions",
    }

    enums = {enum["name"]: set(enum["labels"]) for enum in inspector.get_enums()}
    assert enums["strategy_run_type"] >= {"dry_bootstrap", "backtest", "risk_evaluation"}


def test_alembic_upgrade_creates_phase4_risk_tables(migrated_database: str) -> None:
    settings = load_settings()
    inspector = inspect(get_engine(settings))

    table_names = set(inspector.get_table_names())
    assert {"risk_events"}.issubset(table_names)

    risk_cols = {col["name"] for col in inspector.get_columns("risk_events")}
    assert risk_cols >= {
        "strategy_run_id",
        "symbol_id",
        "session_date",
        "signal_direction",
        "signal_reason",
        "outcome",
        "decision_code",
        "decision_reason",
        "reference_price",
        "proposed_quantity",
        "proposed_notional",
        "risk_metadata",
    }


def test_alembic_upgrade_creates_phase5_paper_order_tables(migrated_database: str) -> None:
    settings = load_settings()
    inspector = inspect(get_engine(settings))

    table_names = set(inspector.get_table_names())
    assert {"paper_orders", "paper_fills", "execution_events", "order_events"}.issubset(table_names)

    paper_order_cols = {col["name"] for col in inspector.get_columns("paper_orders")}
    assert paper_order_cols >= {
        "strategy_run_id",
        "source_risk_event_id",
        "symbol_id",
        "intended_session_date",
        "side",
        "quantity",
        "order_type",
        "time_in_force",
        "intent_hash",
        "intent_version",
        "supersedes_paper_order_id",
        "client_order_id",
        "broker_order_id",
        "status",
        "broker_status",
        "submitted_at",
        "submission_attempt_count",
        "sync_failure_count",
        "last_submission_attempt_at",
        "last_sync_failure_at",
        "last_submission_error",
        "last_sync_error",
        "filled_at",
        "canceled_at",
        "last_broker_update_at",
        "last_synced_at",
        "broker_payload",
    }

    constraints = {uc["name"] for uc in inspector.get_unique_constraints("paper_orders")}
    assert constraints >= {
        "uq_paper_orders_source_risk_event_id",
        "uq_paper_orders_intent_hash",
        "uq_paper_orders_client_order_id",
        "uq_paper_orders_broker_order_id",
    }

    paper_fill_cols = {col["name"] for col in inspector.get_columns("paper_fills")}
    assert paper_fill_cols >= {
        "paper_order_id",
        "symbol_id",
        "broker_fill_id",
        "broker_order_id",
        "side",
        "quantity",
        "price",
        "filled_at",
        "broker_payload",
    }

    paper_fill_constraints = {uc["name"] for uc in inspector.get_unique_constraints("paper_fills")}
    assert paper_fill_constraints >= {"uq_paper_fills_broker_fill_id"}

    execution_event_cols = {col["name"] for col in inspector.get_columns("execution_events")}
    assert execution_event_cols >= {
        "strategy_run_id",
        "paper_order_id",
        "event_type",
        "severity",
        "blocks_execution",
        "event_at",
        "message",
        "details",
    }

    order_event_cols = {col["name"] for col in inspector.get_columns("order_events")}
    assert order_event_cols >= {
        "paper_order_id",
        "strategy_run_id",
        "from_state",
        "event_type",
        "to_state",
        "outcome",
        "event_at",
        "details",
    }

    enums = {enum["name"]: set(enum["labels"]) for enum in inspector.get_enums()}
    assert enums["strategy_run_type"] >= {
        "dry_bootstrap",
        "backtest",
        "risk_evaluation",
        "paper_execution",
        "reconciliation",
        "operator_control",
    }
    assert enums["order_lifecycle_state"] >= {
        "pending_submission",
        "submission_failed",
        "submitted",
        "partially_filled",
        "filled",
        "canceled",
        "rejected",
        "expired",
        "unknown",
    }
    assert enums["order_event_type"] >= {
        "legacy_imported",
        "intent_registered",
        "retry_requested",
        "submission_failed",
        "broker_acknowledged",
        "broker_partially_filled",
        "broker_filled",
        "broker_canceled",
        "broker_rejected",
        "broker_expired",
        "broker_status_unknown",
    }
    assert enums["order_event_outcome"] >= {"accepted", "rejected"}


def test_phase7_order_kernel_migration_preserves_existing_paper_orders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_name = f"phase7_order_kernel_{uuid.uuid4().hex[:8]}"
    admin_params = _admin_connection_settings()

    try:
        with _connect_admin(admin_params) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f'CREATE DATABASE "{database_name}"')
    except psycopg.Error as exc:  # pragma: no cover - exercised when local Postgres is unavailable
        pytest.fail(
            "PostgreSQL is required for tests/test_db_migrations.py. "
            "Start the local db service first (for example `docker compose up -d db`). "
            f"Connection error: {exc}"
        )

    _set_database_env(monkeypatch, database_name)
    try:
        _upgrade_to_revision("0012_phase6_operator_controls")

        with session_scope(load_settings()) as session:
            strategy = Strategy(
                strategy_id="trend_following_daily",
                display_name="Trend Following Daily",
                status="active",
                config_reference="config/strategies/trend_following_daily.yaml",
            )
            session.add(strategy)
            session.flush()

            run_id = uuid.uuid4()
            symbol_a = uuid.uuid4()
            symbol_b = uuid.uuid4()
            risk_event_a = uuid.uuid4()
            risk_event_b = uuid.uuid4()

            session.execute(
                text(
                    """
                    INSERT INTO strategy_runs (
                        id,
                        strategy_id,
                        run_type,
                        status,
                        trigger_source,
                        parameters_snapshot,
                        result_summary,
                        error_message
                    ) VALUES (
                        :id,
                        :strategy_id,
                        'paper_execution',
                        'succeeded',
                        'pytest',
                        '{}'::json,
                        '{}'::json,
                        NULL
                    )
                    """
                ),
                {"id": run_id, "strategy_id": strategy.id},
            )
            session.execute(
                text(
                    """
                    INSERT INTO symbols (id, ticker, active)
                    VALUES
                        (:symbol_a, 'AAPL', true),
                        (:symbol_b, 'MSFT', true)
                    """
                ),
                {"symbol_a": symbol_a, "symbol_b": symbol_b},
            )
            session.execute(
                text(
                    """
                    INSERT INTO risk_events (
                        id,
                        strategy_run_id,
                        symbol_id,
                        session_date,
                        signal_direction,
                        signal_reason,
                        outcome,
                        decision_code,
                        decision_reason,
                        reference_price,
                        proposed_quantity,
                        proposed_notional,
                        risk_metadata
                    ) VALUES
                        (
                            :risk_event_a,
                            :run_id,
                            :symbol_a,
                            DATE '2024-01-05',
                            'long',
                            'trend_entry',
                            'approved',
                            'approved',
                            'Approved for paper execution.',
                            120.0,
                            10.0,
                            1200.0,
                            '{}'::json
                        ),
                        (
                            :risk_event_b,
                            :run_id,
                            :symbol_b,
                            DATE '2024-01-05',
                            'long',
                            'trend_entry',
                            'approved',
                            'approved',
                            'Approved for paper execution.',
                            300.0,
                            5.0,
                            1500.0,
                            '{}'::json
                        )
                    """
                ),
                {
                    "risk_event_a": risk_event_a,
                    "risk_event_b": risk_event_b,
                    "run_id": run_id,
                    "symbol_a": symbol_a,
                    "symbol_b": symbol_b,
                },
            )
            session.execute(
                text(
                    """
                    INSERT INTO paper_orders (
                        id,
                        strategy_run_id,
                        source_risk_event_id,
                        symbol_id,
                        intended_session_date,
                        side,
                        quantity,
                        order_type,
                        time_in_force,
                        client_order_id,
                        broker_order_id,
                        status,
                        broker_status,
                        submitted_at,
                        submission_attempt_count,
                        sync_failure_count,
                        last_submission_attempt_at,
                        last_sync_failure_at,
                        last_submission_error,
                        last_sync_error,
                        filled_at,
                        canceled_at,
                        last_broker_update_at,
                        last_synced_at,
                        broker_payload
                    ) VALUES
                        (
                            :order_a,
                            :run_id,
                            :risk_event_a,
                            :symbol_a,
                            DATE '2024-01-05',
                            'buy',
                            10.0,
                            'market',
                            'day',
                            'legacy-aapl-001',
                            NULL,
                            'submission_rejected',
                            'rejected',
                            TIMESTAMPTZ '2024-01-05T14:35:00Z',
                            1,
                            0,
                            TIMESTAMPTZ '2024-01-05T14:35:00Z',
                            NULL,
                            'broker reject',
                            NULL,
                            NULL,
                            NULL,
                            TIMESTAMPTZ '2024-01-05T14:35:10Z',
                            NULL,
                            '{"id": "legacy-aapl-001"}'::json
                        ),
                        (
                            :order_b,
                            :run_id,
                            :risk_event_b,
                            :symbol_b,
                            DATE '2024-01-05',
                            'buy',
                            5.0,
                            'market',
                            'day',
                            'legacy-msft-001',
                            'broker-msft-001',
                            'submitted',
                            'new',
                            TIMESTAMPTZ '2024-01-05T14:36:00Z',
                            1,
                            0,
                            TIMESTAMPTZ '2024-01-05T14:36:00Z',
                            NULL,
                            NULL,
                            NULL,
                            NULL,
                            NULL,
                            TIMESTAMPTZ '2024-01-05T14:36:10Z',
                            NULL,
                            '{"id": "broker-msft-001"}'::json
                        )
                    """
                ),
                {
                    "order_a": uuid.uuid4(),
                    "order_b": uuid.uuid4(),
                    "run_id": run_id,
                    "risk_event_a": risk_event_a,
                    "risk_event_b": risk_event_b,
                    "symbol_a": symbol_a,
                    "symbol_b": symbol_b,
                },
            )

        _upgrade_to_head()

        with session_scope(load_settings()) as session:
            orders = session.execute(select(PaperOrder).order_by(PaperOrder.client_order_id.asc())).scalars().all()
            events = session.execute(select(OrderEvent).order_by(OrderEvent.event_at.asc())).scalars().all()

        assert [order.status for order in orders] == ["rejected", "submitted"]
        assert [order.intent_version for order in orders] == [1, 1]
        assert all(order.supersedes_paper_order_id is None for order in orders)
        assert [order.intent_hash for order in orders] == [
            build_intent_hash(
                strategy_id="trend_following_daily",
                session_date=date(2024, 1, 5),
                symbol="AAPL",
                side="buy",
                quantity=Decimal("10.0"),
            ),
            build_intent_hash(
                strategy_id="trend_following_daily",
                session_date=date(2024, 1, 5),
                symbol="MSFT",
                side="buy",
                quantity=Decimal("5.0"),
            ),
        ]
        assert len(events) == 2
        assert {event.event_type for event in events} == {OrderTransitionEventType.LEGACY_IMPORTED}
        assert {event.outcome for event in events} == {OrderTransitionOutcome.ACCEPTED}
        assert {event.to_state for event in events} == {order.status for order in orders}
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


def test_seed_script_is_idempotent(migrated_database: str) -> None:
    first_record, first_created = seed_phase_one()
    second_record, second_created = seed_phase_one()

    assert first_created is True
    assert second_created is False
    assert first_record.id == second_record.id

    with session_scope(load_settings()) as session:
        persisted = session.execute(select(Strategy)).scalars().all()

    assert len(persisted) == 1
    assert persisted[0].strategy_id == "trend_following_daily"
    assert persisted[0].config_reference == "config/strategies/trend_following_daily.yaml"


def test_phase7_idempotent_intent_schema_supports_predecessor_links(migrated_database: str) -> None:
    settings = load_settings()

    with session_scope(settings) as session:
        strategy = Strategy(
            strategy_id="trend_following_daily",
            display_name="Trend Following Daily",
            status="active",
            config_reference="config/strategies/trend_following_daily.yaml",
        )
        session.add(strategy)
        session.flush()

        symbol = Symbol(ticker="AAPL", active=True)
        session.add(symbol)
        session.flush()

        risk_run = StrategyRun(
            strategy_id=strategy.id,
            run_type=StrategyRunType.RISK_EVALUATION,
            status=StrategyRunStatus.SUCCEEDED,
            trigger_source="pytest",
            parameters_snapshot={"as_of_session": "2024-01-05"},
            result_summary={"stage": "completed", "as_of_session": "2024-01-05"},
        )
        second_risk_run = StrategyRun(
            strategy_id=strategy.id,
            run_type=StrategyRunType.RISK_EVALUATION,
            status=StrategyRunStatus.SUCCEEDED,
            trigger_source="pytest",
            parameters_snapshot={"as_of_session": "2024-01-05", "wave": "retry"},
            result_summary={"stage": "completed", "as_of_session": "2024-01-05"},
        )
        execution_run = StrategyRun(
            strategy_id=strategy.id,
            run_type=StrategyRunType.PAPER_EXECUTION,
            status=StrategyRunStatus.SUCCEEDED,
            trigger_source="pytest",
            parameters_snapshot={"as_of_session": "2024-01-05"},
            result_summary={"stage": "completed", "as_of_session": "2024-01-05"},
        )
        session.add_all([risk_run, second_risk_run, execution_run])
        session.flush()

        first_risk_event = RiskEvent(
            strategy_run_id=risk_run.id,
            symbol_id=symbol.id,
            session_date=date(2024, 1, 5),
            signal_direction="long",
            signal_reason="trend_entry",
            outcome="approved",
            decision_code="approved",
            decision_reason="Approved for paper execution.",
            reference_price=Decimal("120.0"),
            proposed_quantity=Decimal("10.0"),
            proposed_notional=Decimal("1200.0"),
            risk_metadata={},
        )
        second_risk_event = RiskEvent(
            strategy_run_id=second_risk_run.id,
            symbol_id=symbol.id,
            session_date=date(2024, 1, 5),
            signal_direction="long",
            signal_reason="trend_entry",
            outcome="approved",
            decision_code="approved",
            decision_reason="Approved for paper execution.",
            reference_price=Decimal("120.0"),
            proposed_quantity=Decimal("12.0"),
            proposed_notional=Decimal("1440.0"),
            risk_metadata={},
        )
        session.add_all([first_risk_event, second_risk_event])
        session.flush()

        first_order = PaperOrder(
            strategy_run_id=execution_run.id,
            source_risk_event_id=first_risk_event.id,
            symbol_id=symbol.id,
            intended_session_date=date(2024, 1, 5),
            side="buy",
            quantity=Decimal("10.0"),
            order_type="market",
            time_in_force="day",
            intent_hash=build_intent_hash(
                strategy_id="trend_following_daily",
                session_date=date(2024, 1, 5),
                symbol="AAPL",
                side="buy",
                quantity=Decimal("10.0"),
            ),
            intent_version=1,
            client_order_id="tp-20240105-aapl-first",
            broker_order_id="broker-aapl-001",
            status=OrderLifecycleState.SUBMITTED,
            broker_status="new",
            broker_payload={"id": "broker-aapl-001"},
        )
        session.add(first_order)
        session.flush()

        second_order = PaperOrder(
            strategy_run_id=execution_run.id,
            source_risk_event_id=second_risk_event.id,
            symbol_id=symbol.id,
            intended_session_date=date(2024, 1, 5),
            side="buy",
            quantity=Decimal("12.0"),
            order_type="market",
            time_in_force="day",
            intent_hash=build_intent_hash(
                strategy_id="trend_following_daily",
                session_date=date(2024, 1, 5),
                symbol="AAPL",
                side="buy",
                quantity=Decimal("12.0"),
            ),
            intent_version=2,
            supersedes_paper_order_id=first_order.id,
            client_order_id="tp-20240105-aapl-second",
            broker_order_id=None,
            status=OrderLifecycleState.PENDING_SUBMISSION,
            broker_payload={},
        )
        session.add(second_order)
        session.flush()
        second_order_id = second_order.id
        first_order_id = first_order.id

    with session_scope(settings) as session:
        persisted = session.get(PaperOrder, second_order_id)

        assert persisted is not None
        assert persisted.intent_version == 2
        assert persisted.supersedes_paper_order_id == first_order_id
        assert persisted.supersedes_paper_order is not None
        assert persisted.supersedes_paper_order.intent_version == 1


def test_phase7_global_kill_switch_migration_creates_table_and_seeds_armed_state(
    migrated_database: str,
) -> None:
    settings = load_settings()
    inspector = inspect(get_engine(settings))

    table_names = set(inspector.get_table_names())
    assert "system_controls" in table_names

    cols = {col["name"] for col in inspector.get_columns("system_controls")}
    assert cols >= {
        "id",
        "name",
        "state",
        "last_changed_at",
        "last_change_actor",
        "last_change_reason",
        "last_change_run_id",
        "created_at",
        "updated_at",
    }

    uniques = {uc["name"] for uc in inspector.get_unique_constraints("system_controls")}
    assert "uq_system_controls_name" in uniques

    enums = {enum["name"]: set(enum["labels"]) for enum in inspector.get_enums()}
    assert enums["kill_switch_state"] == {"armed", "tripped"}

    with session_scope(settings) as session:
        rows = session.execute(
            text("SELECT name, state, last_change_actor FROM system_controls")
        ).all()

    assert len(rows) == 1
    assert rows[0][0] == "global_kill_switch"
    assert rows[0][1] == "armed"
    assert rows[0][2] == "system_bootstrap"


def test_ready_endpoint_reflects_database_connectivity(
    monkeypatch: pytest.MonkeyPatch,
    migrated_database: str,
) -> None:
    clear_settings_cache()
    clear_engine_cache()
    with TestClient(create_app()) as client:
        ready = client.get("/ready")

    assert ready.status_code == 200
    assert ready.json()["checks"]["database"]["status"] == "ok"

    monkeypatch.setenv("TRADING_PLATFORM_DATABASE__PORT", "6543")
    clear_settings_cache()
    clear_engine_cache()
    with TestClient(create_app()) as client:
        degraded = client.get("/ready")

    assert degraded.status_code == 503
    degraded_body = degraded.json()
    assert degraded_body["ready"] is False
    assert degraded_body["checks"]["database"]["status"] == "error"


def test_alembic_upgrade_creates_phase17_job_tables(migrated_database: str) -> None:
    settings = load_settings()
    inspector = inspect(get_engine(settings))

    table_names = set(inspector.get_table_names())
    assert {"jobs", "job_dependencies", "job_events", "job_logs"}.issubset(table_names)

    enums = {enum["name"]: set(enum["labels"]) for enum in inspector.get_enums()}
    assert enums["job_status"] == {"queued", "running", "succeeded", "failed", "cancelled"}
    assert enums["job_failure_reason"] == {
        "handler_error",
        "worker_lost",
        "lease_expired",
        "cancellation_timeout",
    }
    assert enums["job_cancellation_cause"] == {
        "operator_request",
        "dependency_failed",
        "dependency_cancelled",
    }

    job_cols = {col["name"] for col in inspector.get_columns("jobs")}
    assert job_cols >= {
        "id",
        "job_type",
        "payload",
        "status",
        "queued_at",
        "started_at",
        "completed_at",
        "lease_owner",
        "lease_expires_at",
        "heartbeat_at",
        "failure_reason",
        "failure_message",
        "outcome_uncertain",
        "result_summary",
        "cancellation_requested_at",
        "cancellation_requested_by",
        "cancellation_reason",
        "cancellation_acknowledged_at",
        "cancellation_cause",
        "blocking_job_id",
        "blocking_job_status",
        "root_cause_job_id",
        "progress_percent",
        "progress_step",
        "progress_current",
        "progress_total",
        "progress_updated_at",
    }

    job_log_constraints = {uc["name"] for uc in inspector.get_unique_constraints("job_logs")}
    assert "uq_job_logs_job_id_sequence" in job_log_constraints

    # JOB-01 database-level proof: a status literal outside the closed five-member
    # enum set is rejected by PostgreSQL itself, not merely by Python-side validation.
    with pytest.raises(DBAPIError):
        with session_scope(settings) as session:
            session.execute(
                text(
                    "INSERT INTO jobs (id, job_type, payload, status, result_summary) "
                    "VALUES (:id, :job_type, CAST(:payload AS JSON), CAST(:status AS job_status), "
                    "CAST(:result_summary AS JSON))"
                ),
                {
                    "id": uuid.uuid4(),
                    "job_type": "phase17_enum_isolation_probe",
                    "payload": "{}",
                    # "stale" is a valid StrategyRunStatus value but not a JobStatus
                    # value -- deliberately chosen to prove enum type isolation.
                    "status": "stale",
                    "result_summary": "{}",
                },
            )


def test_phase17_job_dependency_rejects_self_edge(migrated_database: str) -> None:
    settings = load_settings()

    with session_scope(settings) as session:
        job = Job(job_type="phase17_self_dependency_probe", payload={})
        session.add(job)
        session.flush()
        job_id = job.id

    with pytest.raises(IntegrityError):
        with session_scope(settings) as session:
            session.add(JobDependency(job_id=job_id, depends_on_job_id=job_id))
            session.flush()
