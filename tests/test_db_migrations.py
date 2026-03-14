from __future__ import annotations

import os
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alembic import command
from scripts.migrate import build_alembic_config
from scripts.seed_phase1 import seed_phase_one
from trading_platform.api.app import create_app
from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.db.models import Strategy
from trading_platform.db.session import clear_engine_cache, get_engine, session_scope


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


def _upgrade_to_head() -> None:
    clear_settings_cache()
    clear_engine_cache()
    command.upgrade(build_alembic_config(), "head")


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
        "average_holding_period_sessions",
    }

    signal_constraints = {uc["name"] for uc in inspector.get_unique_constraints("backtest_signals")}
    assert "uq_backtest_signals_run_symbol_session" in signal_constraints

    equity_constraints = {uc["name"] for uc in inspector.get_unique_constraints("backtest_equity_snapshots")}
    assert "uq_backtest_equity_snapshots_run_session" in equity_constraints

    metric_constraints = {uc["name"] for uc in inspector.get_unique_constraints("backtest_metrics")}
    assert "uq_backtest_metrics_strategy_run_id" in metric_constraints


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
