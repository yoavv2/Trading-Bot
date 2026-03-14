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
import yaml
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alembic import command
from scripts.migrate import build_alembic_config
from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.db.models import BacktestEquitySnapshot, BacktestSignal, BacktestTrade, StrategyRun, StrategyRunType
from trading_platform.db.models.daily_bar import DailyBar as DailyBarModel
from trading_platform.db.models.symbol import Symbol
from trading_platform.db.session import clear_engine_cache, session_scope
from trading_platform.services.backtesting import run_backtest
from trading_platform.services.calendar import upsert_market_sessions


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
def migrated_backtest_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    database_name = f"backtest_runner_{uuid.uuid4().hex[:8]}"
    admin_params = _admin_connection_settings()

    try:
        with _connect_admin(admin_params) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f'CREATE DATABASE "{database_name}"')
    except psycopg.Error as exc:
        pytest.fail(
            "PostgreSQL is required for tests/test_backtest_runner.py. "
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


@pytest.fixture()
def strategy_config_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    strategy_dir = tmp_path / "strategies"
    strategy_dir.mkdir()
    strategy_path = strategy_dir / "trend_following_daily.yaml"
    strategy_path.write_text(
        yaml.safe_dump(
            {
                "strategy_id": "trend_following_daily",
                "display_name": "TrendFollowingDailyV1",
                "enabled": True,
                "universe": ["AAPL", "MSFT"],
                "indicators": {
                    "short_window": 2,
                    "long_window": 3,
                    "warmup_periods": 3,
                },
                "risk": {
                    "max_positions": 10,
                    "risk_per_trade": 0.01,
                },
                "exits": {
                    "close_below": "sma_2",
                    "exit_window": 2,
                },
            }
        )
    )
    monkeypatch.setenv("TRADING_PLATFORM_STRATEGY_CONFIG_DIR", str(strategy_dir))
    clear_settings_cache()
    try:
        yield
    finally:
        clear_settings_cache()


def _seed_symbol_and_bars(
    session,
    *,
    ticker: str,
    bar_map: dict[date, tuple[str, str]],
) -> Symbol:
    symbol = Symbol(id=uuid.uuid4(), ticker=ticker, active=True)
    session.add(symbol)
    session.flush()

    for session_date, prices in bar_map.items():
        open_price, close_price = prices
        session.add(
            DailyBarModel(
                id=uuid.uuid4(),
                symbol_id=symbol.id,
                session_date=session_date,
                open=Decimal(open_price),
                high=Decimal(close_price) + Decimal("1"),
                low=Decimal(open_price) - Decimal("1"),
                close=Decimal(close_price),
                volume=1_000_000,
                adjusted=True,
                provider="polygon",
            )
        )

    session.flush()
    return symbol


def _seed_backtest_fixture() -> dict[str, dict[date, tuple[str, str]]]:
    return {
        "AAPL": {
            date(2024, 1, 2): ("100", "100"),
            date(2024, 1, 3): ("110", "110"),
            date(2024, 1, 4): ("120", "120"),
            date(2024, 1, 5): ("125", "130"),
            date(2024, 1, 8): ("135", "140"),
            date(2024, 1, 9): ("142", "90"),
            date(2024, 1, 10): ("92", "92"),
        },
        "MSFT": {
            date(2024, 1, 2): ("100", "100"),
            date(2024, 1, 3): ("100", "100"),
            date(2024, 1, 4): ("100", "100"),
            date(2024, 1, 5): ("100", "100"),
            date(2024, 1, 8): ("100", "100"),
            date(2024, 1, 9): ("100", "100"),
            date(2024, 1, 10): ("100", "100"),
        },
    }


def _seed_market_data() -> None:
    settings = load_settings()
    with session_scope(settings) as session:
        upsert_market_sessions(session, date(2024, 1, 2), date(2024, 1, 10))
        fixture = _seed_backtest_fixture()
        _seed_symbol_and_bars(session, ticker="AAPL", bar_map=fixture["AAPL"])
        _seed_symbol_and_bars(session, ticker="MSFT", bar_map=fixture["MSFT"])


def _normalize_run(session, run_id: str) -> dict[str, object]:
    strategy_run = session.execute(
        select(StrategyRun).where(StrategyRun.id == uuid.UUID(run_id))
    ).scalar_one()
    signals = session.execute(
        select(BacktestSignal)
        .where(BacktestSignal.strategy_run_id == strategy_run.id)
        .order_by(BacktestSignal.session_date.asc(), BacktestSignal.symbol_id.asc())
    ).scalars().all()
    trades = session.execute(
        select(BacktestTrade)
        .where(BacktestTrade.strategy_run_id == strategy_run.id)
        .order_by(BacktestTrade.entry_fill_session.asc(), BacktestTrade.symbol_id.asc())
    ).scalars().all()
    equity = session.execute(
        select(BacktestEquitySnapshot)
        .where(BacktestEquitySnapshot.strategy_run_id == strategy_run.id)
        .order_by(BacktestEquitySnapshot.session_date.asc())
    ).scalars().all()

    return {
        "parameters_snapshot": strategy_run.parameters_snapshot,
        "result_summary": strategy_run.result_summary,
        "signals": [
            {
                "session_date": row.session_date.isoformat(),
                "symbol_id": str(row.symbol_id),
                "direction": row.direction,
                "reason": row.reason,
                "close": str(row.close),
                "bars_available": row.bars_available,
                "action": row.signal_metadata.get("action"),
                "fill_session": row.signal_metadata.get("fill_session"),
            }
            for row in signals
        ],
        "trades": [
            {
                "status": row.status,
                "symbol_id": str(row.symbol_id),
                "quantity": str(row.quantity),
                "entry_signal_session": row.entry_signal_session.isoformat(),
                "entry_fill_session": row.entry_fill_session.isoformat(),
                "entry_price": str(row.entry_price),
                "exit_signal_session": row.exit_signal_session.isoformat() if row.exit_signal_session else None,
                "exit_fill_session": row.exit_fill_session.isoformat() if row.exit_fill_session else None,
                "exit_price": str(row.exit_price) if row.exit_price is not None else None,
                "net_pnl": str(row.net_pnl) if row.net_pnl is not None else None,
            }
            for row in trades
        ],
        "equity": [
            {
                "session_date": row.session_date.isoformat(),
                "cash": str(row.cash),
                "gross_exposure": str(row.gross_exposure),
                "total_equity": str(row.total_equity),
                "open_positions": row.open_positions,
            }
            for row in equity
        ],
    }


def test_backtest_runner_persists_no_lookahead_trade_flow(
    migrated_backtest_db: str,
    strategy_config_override: None,
) -> None:
    _seed_market_data()
    settings = load_settings()

    report = run_backtest(
        "trend_following_daily",
        from_date=date(2024, 1, 2),
        to_date=date(2024, 1, 10),
        settings=settings,
        trigger_source="pytest",
    )

    with session_scope(settings) as session:
        strategy_run = session.execute(
            select(StrategyRun).where(StrategyRun.id == uuid.UUID(report.run_id))
        ).scalar_one()
        trade = session.execute(
            select(BacktestTrade).where(BacktestTrade.strategy_run_id == strategy_run.id)
        ).scalar_one()
        equity_rows = session.execute(
            select(BacktestEquitySnapshot)
            .where(BacktestEquitySnapshot.strategy_run_id == strategy_run.id)
            .order_by(BacktestEquitySnapshot.session_date.asc())
        ).scalars().all()

    assert strategy_run.run_type == StrategyRunType.BACKTEST
    assert trade.entry_signal_session == date(2024, 1, 4)
    assert trade.entry_fill_session == date(2024, 1, 5)
    assert trade.exit_signal_session == date(2024, 1, 9)
    assert trade.exit_fill_session == date(2024, 1, 10)
    assert trade.entry_fill_session > trade.entry_signal_session
    assert trade.exit_fill_session > trade.exit_signal_session
    assert len(equity_rows) == 7
    assert report.result_summary["signals_persisted"] == 14
    assert report.result_summary["trades_persisted"] == 1


def test_backtest_runner_ignores_duplicate_long_signals_while_position_is_open(
    migrated_backtest_db: str,
    strategy_config_override: None,
) -> None:
    _seed_market_data()
    settings = load_settings()

    report = run_backtest(
        "trend_following_daily",
        from_date=date(2024, 1, 2),
        to_date=date(2024, 1, 10),
        settings=settings,
        trigger_source="pytest",
    )

    with session_scope(settings) as session:
        strategy_run = session.execute(
            select(StrategyRun).where(StrategyRun.id == uuid.UUID(report.run_id))
        ).scalar_one()
        duplicate_signals = session.execute(
            select(BacktestSignal)
            .where(BacktestSignal.strategy_run_id == strategy_run.id)
            .where(BacktestSignal.direction == "long")
        ).scalars().all()
        trade_rows = session.execute(
            select(BacktestTrade).where(BacktestTrade.strategy_run_id == strategy_run.id)
        ).scalars().all()

    ignored_duplicate_actions = [
        row.signal_metadata["action"]
        for row in duplicate_signals
        if row.signal_metadata["action"] == "ignored_duplicate_entry"
    ]

    assert len(trade_rows) == 1
    assert ignored_duplicate_actions == [
        "ignored_duplicate_entry",
        "ignored_duplicate_entry",
    ]
    assert report.result_summary["ignored_duplicate_entries"] == 2


def test_backtest_runner_is_deterministic_across_repeat_runs(
    migrated_backtest_db: str,
    strategy_config_override: None,
) -> None:
    _seed_market_data()
    settings = load_settings()

    first = run_backtest(
        "trend_following_daily",
        from_date=date(2024, 1, 2),
        to_date=date(2024, 1, 10),
        settings=settings,
        trigger_source="pytest",
    )
    second = run_backtest(
        "trend_following_daily",
        from_date=date(2024, 1, 2),
        to_date=date(2024, 1, 10),
        settings=settings,
        trigger_source="pytest",
    )

    with session_scope(settings) as session:
        normalized_first = _normalize_run(session, first.run_id)
        normalized_second = _normalize_run(session, second.run_id)

    assert normalized_first == normalized_second
