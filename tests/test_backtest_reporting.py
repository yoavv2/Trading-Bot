from __future__ import annotations

import csv
import os
import sys
import uuid
from collections.abc import Iterator
from datetime import date
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
from trading_platform.db.models import BacktestMetric, StrategyRun, StrategyRunStatus
from trading_platform.db.models.daily_bar import DailyBar as DailyBarModel
from trading_platform.db.models.symbol import Symbol
from trading_platform.db.session import clear_engine_cache, session_scope
from trading_platform.services import backtesting as backtesting_module
from trading_platform.services.backtest_reporting import (
    build_backtest_report,
    export_backtest_report,
)
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
def migrated_reporting_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    database_name = f"backtest_reporting_{uuid.uuid4().hex[:8]}"
    admin_params = _admin_connection_settings()

    try:
        with _connect_admin(admin_params) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f'CREATE DATABASE "{database_name}"')
    except psycopg.Error as exc:
        pytest.fail(
            "PostgreSQL is required for tests/test_backtest_reporting.py. "
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
) -> None:
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
                open=open_price,
                high=close_price + 1,
                low=open_price - 1,
                close=close_price,
                volume=1_000_000,
                adjusted=True,
                provider="polygon",
            )
        )


def _seed_market_data(fixture: dict[str, dict[date, tuple[int, int]]]) -> None:
    settings = load_settings()
    with session_scope(settings) as session:
        upsert_market_sessions(session, date(2024, 1, 2), date(2024, 1, 10))
        for ticker, bar_map in fixture.items():
            _seed_symbol_and_bars(session, ticker=ticker, bar_map=bar_map)


def _trading_fixture() -> dict[str, dict[date, tuple[int, int]]]:
    return {
        "AAPL": {
            date(2024, 1, 2): (100, 100),
            date(2024, 1, 3): (110, 110),
            date(2024, 1, 4): (120, 120),
            date(2024, 1, 5): (125, 130),
            date(2024, 1, 8): (135, 140),
            date(2024, 1, 9): (142, 90),
            date(2024, 1, 10): (92, 92),
        },
        "MSFT": {
            date(2024, 1, 2): (100, 100),
            date(2024, 1, 3): (100, 100),
            date(2024, 1, 4): (100, 100),
            date(2024, 1, 5): (100, 100),
            date(2024, 1, 8): (100, 100),
            date(2024, 1, 9): (100, 100),
            date(2024, 1, 10): (100, 100),
        },
    }


def _flat_fixture() -> dict[str, dict[date, tuple[int, int]]]:
    return {
        "AAPL": {
            date(2024, 1, 2): (100, 100),
            date(2024, 1, 3): (100, 100),
            date(2024, 1, 4): (100, 100),
            date(2024, 1, 5): (100, 100),
            date(2024, 1, 8): (100, 100),
            date(2024, 1, 9): (100, 100),
            date(2024, 1, 10): (100, 100),
        },
        "MSFT": {
            date(2024, 1, 2): (100, 100),
            date(2024, 1, 3): (100, 100),
            date(2024, 1, 4): (100, 100),
            date(2024, 1, 5): (100, 100),
            date(2024, 1, 8): (100, 100),
            date(2024, 1, 9): (100, 100),
            date(2024, 1, 10): (100, 100),
        },
    }


def test_backtest_completion_persists_metrics_and_report_is_pure(
    migrated_reporting_db: str,
    strategy_config_override: None,
    tmp_path: Path,
) -> None:
    _seed_market_data(_trading_fixture())
    settings = load_settings()
    run_report = run_backtest(
        "trend_following_daily",
        from_date=date(2024, 1, 2),
        to_date=date(2024, 1, 10),
        settings=settings,
        trigger_source="pytest",
    )

    # A single BacktestMetric row is written at backtest completion (D-31).
    with session_scope(settings) as session:
        metric_rows_after_run = session.execute(
            select(BacktestMetric).where(
                BacktestMetric.strategy_run_id == uuid.UUID(run_report.run_id)
            )
        ).scalars().all()
    assert len(metric_rows_after_run) == 1
    persisted_metric = metric_rows_after_run[0]

    report = build_backtest_report(run_id=run_report.run_id, settings=settings)
    manifest = export_backtest_report(
        run_id=run_report.run_id,
        output_dir=tmp_path / "exports",
        summary_format="markdown",
        settings=settings,
    )

    # build_backtest_report and export_backtest_report (both read paths) must not
    # have changed the persisted metrics row count or any of its column values.
    with session_scope(settings) as session:
        metric_rows_after_report = session.execute(
            select(BacktestMetric).where(
                BacktestMetric.strategy_run_id == uuid.UUID(run_report.run_id)
            )
        ).scalars().all()
    assert len(metric_rows_after_report) == 1
    metrics_row = metric_rows_after_report[0]
    assert metrics_row.trade_count == persisted_metric.trade_count
    assert metrics_row.total_return_pct == persisted_metric.total_return_pct
    assert metrics_row.max_drawdown_pct == persisted_metric.max_drawdown_pct
    assert metrics_row.win_rate_pct == persisted_metric.win_rate_pct
    assert metrics_row.average_win == persisted_metric.average_win
    assert metrics_row.average_loss == persisted_metric.average_loss
    assert metrics_row.profit_factor == persisted_metric.profit_factor
    assert metrics_row.exposure_pct == persisted_metric.exposure_pct
    assert metrics_row.cagr_pct == persisted_metric.cagr_pct
    assert metrics_row.sharpe_ratio == persisted_metric.sharpe_ratio
    assert metrics_row.sortino_ratio == persisted_metric.sortino_ratio
    assert metrics_row.expectancy == persisted_metric.expectancy
    assert metrics_row.turnover_pct == persisted_metric.turnover_pct
    assert metrics_row.best_trade == persisted_metric.best_trade
    assert metrics_row.worst_trade == persisted_metric.worst_trade
    assert (
        metrics_row.average_holding_period_sessions
        == persisted_metric.average_holding_period_sessions
    )

    assert report["metrics"]["trade_count"] == metrics_row.trade_count
    assert report["metrics"]["total_return_pct"] == pytest.approx(float(metrics_row.total_return_pct))
    assert report["metrics"]["max_drawdown_pct"] == pytest.approx(float(metrics_row.max_drawdown_pct))
    assert report["metrics"]["win_rate_pct"] == pytest.approx(float(metrics_row.win_rate_pct))
    assert report["metrics"]["average_loss"] == pytest.approx(float(metrics_row.average_loss))
    assert report["metrics"]["average_holding_period_sessions"] == pytest.approx(
        float(metrics_row.average_holding_period_sessions)
    )

    assert report["metrics"]["trade_count"] == 1
    assert report["metrics"]["win_rate_pct"] == pytest.approx(0.0)
    assert report["metrics"]["average_loss"] == pytest.approx(-2615.5715, rel=1e-6)
    assert report["metrics"]["average_holding_period_sessions"] == pytest.approx(3.0)
    assert report["metrics"]["total_return_pct"] == pytest.approx(-2.615571, rel=1e-6)
    assert report["metrics"]["max_drawdown_pct"] == pytest.approx(-3.903931, rel=1e-6)
    assert report["metrics"]["cagr_pct"] < 0.0
    assert report["metrics"]["sharpe_ratio"] < 0.0
    assert report["metrics"]["sortino_ratio"] < 0.0
    assert report["metrics"]["expectancy"] == pytest.approx(-2615.5715, rel=1e-6)
    assert report["metrics"]["turnover_pct"] > 0.0
    assert report["metrics"]["best_trade"] == pytest.approx(-2615.5715, rel=1e-6)
    assert report["metrics"]["worst_trade"] == pytest.approx(-2615.5715, rel=1e-6)
    assert report["assumptions"]["backtest"]["fill_strategy"] == "next_session_open"
    assert "Initial capital: 100000.0" in manifest.rendered_summary
    assert "| CAGR |" in manifest.rendered_summary
    assert "| Turnover |" in manifest.rendered_summary

    with open(manifest.trades_csv_path, newline="") as handle:
        trade_rows = list(csv.DictReader(handle))
    with open(manifest.equity_csv_path, newline="") as handle:
        equity_rows = list(csv.DictReader(handle))

    assert len(trade_rows) == 1
    assert len(equity_rows) == 7


def test_failed_backtest_persists_no_metrics(
    migrated_reporting_db: str,
    strategy_config_override: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_market_data(_trading_fixture())
    settings = load_settings()

    def _raise_execution(*args: object, **kwargs: object) -> dict[str, object]:
        raise RuntimeError("forced backtest execution failure")

    monkeypatch.setattr(backtesting_module, "_execute_backtest_run", _raise_execution)

    with pytest.raises(RuntimeError, match="forced backtest execution failure"):
        run_backtest(
            "trend_following_daily",
            from_date=date(2024, 1, 2),
            to_date=date(2024, 1, 10),
            settings=settings,
            trigger_source="pytest",
        )

    with session_scope(settings) as session:
        metric_count = session.execute(select(BacktestMetric)).scalars().all()
    assert len(metric_count) == 0


def test_metric_persistence_failure_lands_run_on_failed_status(
    migrated_reporting_db: str,
    strategy_config_override: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure inside persist_backtest_metrics (called from the SUCCEEDED
    branch of _update_backtest_run) must not leave the run stuck RUNNING --
    it must land the run on FAILED with zero backtest_metrics rows, the same
    as an _execute_backtest_run failure (regression guard for D-31)."""
    _seed_market_data(_trading_fixture())
    settings = load_settings()

    def _raise_persist(*args: object, **kwargs: object) -> None:
        raise RuntimeError("forced metric persistence failure")

    monkeypatch.setattr(backtesting_module, "persist_backtest_metrics", _raise_persist)

    with pytest.raises(RuntimeError, match="forced metric persistence failure"):
        run_backtest(
            "trend_following_daily",
            from_date=date(2024, 1, 2),
            to_date=date(2024, 1, 10),
            settings=settings,
            trigger_source="pytest",
        )

    with session_scope(settings) as session:
        strategy_run = session.execute(select(StrategyRun)).scalar_one()
        metric_rows = session.execute(select(BacktestMetric)).scalars().all()

    assert strategy_run.status == StrategyRunStatus.FAILED
    assert len(metric_rows) == 0


def test_reporting_handles_no_trade_runs_without_divide_by_zero(
    migrated_reporting_db: str,
    strategy_config_override: None,
    tmp_path: Path,
) -> None:
    _seed_market_data(_flat_fixture())
    settings = load_settings()
    run_report = run_backtest(
        "trend_following_daily",
        from_date=date(2024, 1, 2),
        to_date=date(2024, 1, 10),
        settings=settings,
        trigger_source="pytest",
    )

    report = build_backtest_report(run_id=run_report.run_id, settings=settings)
    manifest = export_backtest_report(
        run_id=run_report.run_id,
        output_dir=tmp_path / "flat-exports",
        summary_format="markdown",
        settings=settings,
    )

    assert report["metrics"]["trade_count"] == 0
    assert report["metrics"]["win_rate_pct"] == 0.0
    assert report["metrics"]["average_win"] == 0.0
    assert report["metrics"]["average_loss"] == 0.0
    assert report["metrics"]["profit_factor"] == 0.0
    assert report["metrics"]["cagr_pct"] == 0.0
    assert report["metrics"]["sharpe_ratio"] == 0.0
    assert report["metrics"]["sortino_ratio"] == 0.0
    assert report["metrics"]["expectancy"] == 0.0
    assert report["metrics"]["turnover_pct"] == 0.0
    assert report["metrics"]["best_trade"] == 0.0
    assert report["metrics"]["worst_trade"] == 0.0
    assert "Trades persisted: 0" in manifest.rendered_summary
