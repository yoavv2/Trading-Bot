from __future__ import annotations

import os
import sys
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from alembic import command
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.migrate import build_alembic_config
from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.db.models import Position, Symbol
from trading_platform.db.models.account_snapshot import AccountSnapshot
from trading_platform.db.models.daily_bar import DailyBar
from trading_platform.db.session import clear_engine_cache, session_scope
from trading_platform.services.bootstrap import ensure_strategy_record
from trading_platform.services.calendar import upsert_market_sessions
from trading_platform.core.settings import PortfolioSettings
from trading_platform.services.portfolio import PortfolioService, PortfolioState
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
def migrated_portfolio_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    database_name = f"portfolio_service_{uuid.uuid4().hex[:8]}"
    admin_params = _admin_connection_settings()

    try:
        with _connect_admin(admin_params) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f'CREATE DATABASE "{database_name}"')
    except psycopg.Error as exc:
        pytest.fail(
            "PostgreSQL is required for tests/test_portfolio_service.py. "
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
                      AND pid <> pg_backend_pid()
                    """,
                    (database_name,),
                )
                cursor.execute(f'DROP DATABASE IF EXISTS "{database_name}"')


def _seed_strategy(session: Session):
    settings = load_settings()
    strategy = build_default_registry(settings).resolve("trend_following_daily")
    return ensure_strategy_record(session, strategy.metadata)


def _seed_symbol(session: Session, ticker: str) -> Symbol:
    symbol = Symbol(ticker=ticker, active=True)
    session.add(symbol)
    session.flush()
    return symbol


def test_empty_state_uses_typed_starting_cash() -> None:
    service = PortfolioService(
        PortfolioSettings(
            starting_cash=250_000,
            max_strategy_allocation_pct=1.0,
            max_total_portfolio_allocation_pct=1.0,
        )
    )

    state = service.empty_state()

    assert state.cash == Decimal("250000.000000")
    assert state.total_equity == Decimal("250000.000000")
    assert state.gross_exposure == Decimal("0.000000")
    assert state.position_count == 0


def test_compute_entry_size_rounds_down_to_whole_shares() -> None:
    service = PortfolioService(
        PortfolioSettings(
            starting_cash=100_000,
            max_strategy_allocation_pct=1.0,
            max_total_portfolio_allocation_pct=1.0,
        )
    )
    state = service.empty_state()

    sizing = service.compute_entry_size(
        state,
        candidate_price=Decimal("123"),
        risk_per_trade=Decimal("0.01"),
    )

    assert sizing.target_notional == Decimal("1000.000000")
    assert sizing.quantity == Decimal("8")
    assert sizing.approved_notional == Decimal("984.000000")


def test_compute_entry_size_honors_strategy_and_total_allocation_caps() -> None:
    service = PortfolioService(
        PortfolioSettings(
            starting_cash=100_000,
            max_strategy_allocation_pct=0.10,
            max_total_portfolio_allocation_pct=0.08,
        )
    )
    state = PortfolioState(
        cash=Decimal("5000.000000"),
        gross_exposure=Decimal("7750.000000"),
        total_equity=Decimal("100000.000000"),
        strategy_exposure=Decimal("9500.000000"),
    )

    sizing = service.compute_entry_size(
        state,
        candidate_price=Decimal("100"),
        risk_per_trade=Decimal("0.02"),
    )

    assert sizing.remaining_strategy_capacity == Decimal("500.000000")
    assert sizing.remaining_total_capacity == Decimal("250.000000")
    assert sizing.quantity == Decimal("2")
    assert sizing.approved_notional == Decimal("200.000000")


def test_load_state_marks_open_positions_from_persisted_bars(migrated_portfolio_db: str) -> None:
    settings = load_settings()
    service = PortfolioService(settings)

    with session_scope(settings) as session:
        strategy = _seed_strategy(session)
        symbol = _seed_symbol(session, "AAPL")
        upsert_market_sessions(session, date(2024, 1, 5), date(2024, 1, 5))
        session.add(
            DailyBar(
                symbol_id=symbol.id,
                session_date=date(2024, 1, 5),
                open=Decimal("109"),
                high=Decimal("111"),
                low=Decimal("108"),
                close=Decimal("110"),
                volume=1_000_000,
                adjusted=True,
                provider="polygon",
            )
        )
        session.add(
            Position(
                strategy_id=strategy.id,
                symbol_id=symbol.id,
                status="open",
                quantity=Decimal("10"),
                average_entry_price=Decimal("100"),
                cost_basis=Decimal("1000"),
                opened_session_date=date(2024, 1, 4),
                opened_at=datetime(2024, 1, 4, tzinfo=UTC),
            )
        )
        session.add(
            AccountSnapshot(
                strategy_id=strategy.id,
                snapshot_source="seed",
                snapshot_at=datetime(2024, 1, 5, tzinfo=UTC),
                cash=Decimal("90000"),
                gross_exposure=Decimal("0"),
                total_equity=Decimal("90000"),
                buying_power=Decimal("90000"),
                open_positions=0,
            )
        )

    with session_scope(settings) as session:
        state = service.load_state(
            session,
            strategy_id="trend_following_daily",
            as_of_session=date(2024, 1, 5),
        )

    assert state.cash == Decimal("90000.000000")
    assert state.gross_exposure == Decimal("1100.000000")
    assert state.strategy_exposure == Decimal("1100.000000")
    assert state.total_equity == Decimal("91100.000000")
    assert state.position_count == 1
    assert state.total_open_positions == 1
    assert state.open_symbols == frozenset({"AAPL"})
    assert state.open_positions[0].market_value == Decimal("1100.000000")


def test_record_snapshot_persists_current_portfolio_state(migrated_portfolio_db: str) -> None:
    settings = load_settings()
    service = PortfolioService(settings)

    with session_scope(settings) as session:
        _seed_strategy(session)
        snapshot = service.record_snapshot(
            session,
            strategy_id="trend_following_daily",
            state=PortfolioState(
                cash=Decimal("95000.000000"),
                gross_exposure=Decimal("5000.000000"),
                total_equity=Decimal("100000.000000"),
                strategy_exposure=Decimal("5000.000000"),
                total_open_positions=2,
            ),
            snapshot_source="risk_evaluation",
        )
        persisted = session.get(AccountSnapshot, snapshot.id)

    assert persisted is not None
    assert persisted.snapshot_source == "risk_evaluation"
    assert persisted.open_positions == 2
    assert persisted.buying_power == Decimal("95000.000000")
