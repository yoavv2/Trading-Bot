from __future__ import annotations

import json
import os
import sys
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime
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
from trading_platform.db.models import (
    AccountSnapshot,
    DailyBar,
    ExecutionEvent,
    MarketSession,
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
from trading_platform.services.analytics import (
    StrategyAnalyticsRequest,
    StrategyAnalyticsService,
    build_strategy_analytics_report,
    render_strategy_analytics_report,
)
from trading_platform.services.backtesting import run_backtest
from trading_platform.services.bootstrap import ensure_strategy_record
from trading_platform.services.execution import OrderSide, build_client_order_id
from trading_platform.services.execution.idempotency import build_intent_hash
from trading_platform.services.operator_reads import OperatorReadFilters, OperatorReadService
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
def migrated_analytics_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    database_name = f"analytics_service_{uuid.uuid4().hex[:8]}"
    admin_params = _admin_connection_settings()

    try:
        with _connect_admin(admin_params) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f'CREATE DATABASE "{database_name}"')
    except psycopg.Error as exc:
        pytest.fail(
            "PostgreSQL is required for tests/test_analytics_service.py. "
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


def _seed_market_data(fixture: dict[str, dict[date, tuple[int, int]]]) -> None:
    settings = load_settings()

    with session_scope(settings) as session:
        for ticker in fixture:
            existing = session.execute(select(Symbol).where(Symbol.ticker == ticker)).scalar_one_or_none()
            if existing is None:
                session.add(Symbol(ticker=ticker, active=True))
        session.flush()

        for session_date in sorted({day for bars in fixture.values() for day in bars}):
            session.add(
                MarketSession(
                    exchange=settings.market_data.calendar.exchange,
                    session_date=session_date,
                    market_open=datetime.combine(session_date, datetime.min.time(), tzinfo=UTC).replace(hour=14, minute=30),
                    market_close=datetime.combine(session_date, datetime.min.time(), tzinfo=UTC).replace(hour=21, minute=0),
                    early_close=False,
                )
            )

        for ticker, bar_map in fixture.items():
            symbol = session.execute(select(Symbol).where(Symbol.ticker == ticker)).scalar_one()
            for session_date, prices in bar_map.items():
                open_price, close_price = prices
                session.add(
                    DailyBar(
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


def _seed_strategy_record() -> None:
    settings = load_settings()
    registry = build_default_registry(settings)
    strategy = registry.resolve("trend_following_daily")

    with session_scope(settings) as session:
        ensure_strategy_record(session, strategy.metadata)


def _seed_paper_operational_state(session_date: date = date(2024, 1, 5)) -> dict[str, str]:
    settings = load_settings()
    registry = build_default_registry(settings)
    strategy = registry.resolve("trend_following_daily")

    with session_scope(settings) as session:
        strategy_record = ensure_strategy_record(session, strategy.metadata)
        aapl = session.execute(select(Symbol).where(Symbol.ticker == "AAPL")).scalar_one()
        msft = session.execute(select(Symbol).where(Symbol.ticker == "MSFT")).scalar_one()

        risk_run = StrategyRun(
            strategy_id=strategy_record.id,
            run_type=StrategyRunType.RISK_EVALUATION,
            status=StrategyRunStatus.SUCCEEDED,
            trigger_source="test_suite",
            started_at=datetime(2024, 1, 5, 14, 30, tzinfo=UTC),
            parameters_snapshot={"as_of_session": session_date.isoformat()},
            result_summary={"stage": "completed", "as_of_session": session_date.isoformat()},
            completed_at=datetime(2024, 1, 5, 14, 32, tzinfo=UTC),
        )
        session.add(risk_run)
        session.flush()

        aapl_risk_event = RiskEvent(
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
        )
        msft_risk_event = RiskEvent(
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
        )
        session.add_all([aapl_risk_event, msft_risk_event])
        session.flush()

        paper_run = StrategyRun(
            strategy_id=strategy_record.id,
            run_type=StrategyRunType.PAPER_EXECUTION,
            status=StrategyRunStatus.SUCCEEDED,
            trigger_source="test_suite",
            started_at=datetime(2024, 1, 5, 14, 34, tzinfo=UTC),
            parameters_snapshot={"as_of_session": session_date.isoformat(), "requested_risk_run_id": str(risk_run.id)},
            result_summary={"stage": "completed", "as_of_session": session_date.isoformat()},
            completed_at=datetime(2024, 1, 5, 14, 40, tzinfo=UTC),
        )
        session.add(paper_run)
        session.flush()

        aapl_order = PaperOrder(
            strategy_run_id=paper_run.id,
            source_risk_event_id=aapl_risk_event.id,
            symbol_id=aapl.id,
            intended_session_date=session_date,
            side=OrderSide.BUY.value,
            quantity=Decimal("10.000000"),
            order_type="market",
            time_in_force="day",
            client_order_id=build_client_order_id(
                prefix=settings.execution.client_order_id_prefix,
                strategy_id="trend_following_daily",
                session_date=session_date,
                symbol="AAPL",
                side=OrderSide.BUY,
                quantity=Decimal("10.000000"),
            ),
            intent_hash=build_intent_hash(
                strategy_id="trend_following_daily",
                session_date=session_date,
                symbol="AAPL",
                side=OrderSide.BUY,
                quantity=Decimal("10.000000"),
            ),
            intent_version=1,
            broker_order_id="paper-aapl-001",
            status="filled",
            broker_status="filled",
            submitted_at=datetime(2024, 1, 5, 14, 35, tzinfo=UTC),
            filled_at=datetime(2024, 1, 5, 14, 36, tzinfo=UTC),
            broker_payload={"id": "paper-aapl-001"},
        )
        msft_order = PaperOrder(
            strategy_run_id=paper_run.id,
            source_risk_event_id=msft_risk_event.id,
            symbol_id=msft.id,
            intended_session_date=session_date,
            side=OrderSide.BUY.value,
            quantity=Decimal("5.000000"),
            order_type="market",
            time_in_force="day",
            client_order_id=build_client_order_id(
                prefix=settings.execution.client_order_id_prefix,
                strategy_id="trend_following_daily",
                session_date=session_date,
                symbol="MSFT",
                side=OrderSide.BUY,
                quantity=Decimal("5.000000"),
            ),
            intent_hash=build_intent_hash(
                strategy_id="trend_following_daily",
                session_date=session_date,
                symbol="MSFT",
                side=OrderSide.BUY,
                quantity=Decimal("5.000000"),
            ),
            intent_version=1,
            broker_order_id="paper-msft-001",
            status="submitted",
            broker_status="new",
            submitted_at=datetime(2024, 1, 5, 14, 37, tzinfo=UTC),
            broker_payload={"id": "paper-msft-001"},
        )
        session.add_all([aapl_order, msft_order])
        session.flush()

        session.add(
            PaperFill(
                paper_order_id=aapl_order.id,
                symbol_id=aapl.id,
                broker_fill_id="fill-aapl-001",
                broker_order_id="paper-aapl-001",
                side=OrderSide.BUY.value,
                quantity=Decimal("10.000000"),
                price=Decimal("120.250000"),
                filled_at=datetime(2024, 1, 5, 14, 36, tzinfo=UTC),
                broker_payload={"id": "fill-aapl-001"},
            )
        )
        session.add(
            Position(
                strategy_id=strategy_record.id,
                symbol_id=aapl.id,
                status="open",
                quantity=Decimal("10.000000"),
                average_entry_price=Decimal("120.250000"),
                cost_basis=Decimal("1202.500000"),
                opened_session_date=session_date,
                opened_at=datetime(2024, 1, 5, 14, 36, tzinfo=UTC),
            )
        )
        session.add(
            AccountSnapshot(
                strategy_id=strategy_record.id,
                source_run_id=paper_run.id,
                snapshot_source="broker_sync",
                snapshot_at=datetime(2024, 1, 5, 14, 45, tzinfo=UTC),
                cash=Decimal("98797.500000"),
                gross_exposure=Decimal("1215.000000"),
                total_equity=Decimal("100012.500000"),
                buying_power=Decimal("98797.500000"),
                open_positions=1,
            )
        )

        reconciliation_run = StrategyRun(
            strategy_id=strategy_record.id,
            run_type=StrategyRunType.RECONCILIATION,
            status=StrategyRunStatus.SUCCEEDED,
            trigger_source="test_suite",
            started_at=datetime(2024, 1, 5, 14, 48, tzinfo=UTC),
            parameters_snapshot={"as_of_session": session_date.isoformat()},
            result_summary={
                "stage": "completed",
                "as_of_session": session_date.isoformat(),
                "finding_count": 1,
                "blocking_count": 1,
                "blocks_execution": True,
                "findings": [],
            },
            completed_at=datetime(2024, 1, 5, 14, 50, tzinfo=UTC),
        )
        session.add(reconciliation_run)
        session.flush()

        session.add(
            ExecutionEvent(
                strategy_run_id=reconciliation_run.id,
                paper_order_id=msft_order.id,
                event_type="reconciliation_block",
                severity="error",
                blocks_execution=True,
                event_at=datetime(2024, 1, 5, 14, 49, tzinfo=UTC),
                message="Broker drift blocks execution.",
                details={"broker_status": "partially_filled", "local_status": "submitted"},
            )
        )

        return {
            "risk_run_id": str(risk_run.id),
            "paper_run_id": str(paper_run.id),
            "reconciliation_run_id": str(reconciliation_run.id),
        }


def test_strategy_analytics_service_summarizes_backtest_and_paper_state(
    migrated_analytics_db: str,
    strategy_config_override: None,
) -> None:
    _seed_market_data(_trading_fixture())
    settings = load_settings()
    backtest_report = run_backtest(
        "trend_following_daily",
        from_date=date(2024, 1, 2),
        to_date=date(2024, 1, 10),
        settings=settings,
        trigger_source="pytest",
    )
    paper_state = _seed_paper_operational_state()

    service = StrategyAnalyticsService(settings)
    summary = service.summarize(
        StrategyAnalyticsRequest(
            strategy_id="trend_following_daily",
            backtest_run_id=backtest_report.run_id,
            paper_run_id=paper_state["paper_run_id"],
            inspection_limit=3,
        )
    )

    assert summary["strategy"]["strategy_id"] == "trend_following_daily"
    assert summary["backtest"]["run_id"] == backtest_report.run_id
    assert summary["backtest"]["metrics"]["trade_count"] == 1
    assert summary["backtest"]["metrics"]["cagr_pct"] < 0.0
    assert summary["backtest"]["metrics"]["turnover_pct"] > 0.0
    assert "equity_curve" in summary["backtest"]
    assert len(summary["backtest"]["equity_curve"]) > 0
    assert "session_date" in summary["backtest"]["equity_curve"][0]
    assert "total_equity" in summary["backtest"]["equity_curve"][0]

    paper = summary["paper"]
    assert paper["latest_account_snapshot"]["total_equity"] == pytest.approx(100012.5)
    assert paper["submitted_order_count"] == 2
    assert paper["filled_order_count"] == 1
    assert paper["fill_count"] == 1
    assert paper["blocked_session_count"] == 1
    assert paper["open_position_count"] == 1
    assert paper["open_position_cost_basis"] == pytest.approx(1202.5)
    assert paper["current_exposure_pct"] == pytest.approx(1.214848, rel=1e-6)
    assert paper["latest_paper_run"]["run_id"] == paper_state["paper_run_id"]
    assert paper["latest_reconciliation"]["run_id"] == paper_state["reconciliation_run_id"]
    assert paper["latest_reconciliation"]["blocks_execution"] is True
    assert paper["recent_execution_findings"][0]["event_type"] == "reconciliation_block"


def test_strategy_analytics_service_handles_empty_paper_state(
    migrated_analytics_db: str,
    strategy_config_override: None,
) -> None:
    _seed_strategy_record()
    settings = load_settings()

    service = StrategyAnalyticsService(settings)
    summary = service.summarize({"strategy_id": "trend_following_daily"})

    assert summary["backtest"] is None
    paper = summary["paper"]
    assert paper["latest_account_snapshot"] is None
    assert paper["latest_paper_run"] is None
    assert paper["latest_reconciliation"] is None
    assert paper["submitted_order_count"] == 0
    assert paper["filled_order_count"] == 0
    assert paper["fill_count"] == 0
    assert paper["blocked_session_count"] == 0
    assert paper["open_position_count"] == 0
    assert paper["open_position_cost_basis"] == 0.0
    assert paper["current_exposure_pct"] == 0.0
    assert paper["recent_execution_findings"] == []


def test_operator_read_service_returns_filtered_serializable_payloads(
    migrated_analytics_db: str,
    strategy_config_override: None,
) -> None:
    _seed_market_data(_trading_fixture())
    settings = load_settings()
    run_backtest(
        "trend_following_daily",
        from_date=date(2024, 1, 2),
        to_date=date(2024, 1, 10),
        settings=settings,
        trigger_source="pytest",
    )
    paper_state = _seed_paper_operational_state()

    service = OperatorReadService(settings)

    paper_filters = OperatorReadFilters(
        strategy_id="trend_following_daily",
        run_type="paper_execution",
        status="succeeded",
        session_start=date(2024, 1, 5),
        session_end=date(2024, 1, 5),
        limit=10,
    )
    paper_runs = service.list_runs(paper_filters)
    paper_orders = service.list_paper_orders(paper_filters)
    paper_fills = service.list_paper_fills(paper_filters)
    positions = service.list_positions(paper_filters)
    snapshots = service.list_account_snapshots(paper_filters)

    assert len(paper_runs) == 1
    assert paper_runs[0]["run_id"] == paper_state["paper_run_id"]
    assert paper_runs[0]["as_of_session"] == "2024-01-05"

    assert [item["symbol"] for item in paper_orders] == ["MSFT", "AAPL"]
    assert paper_orders[0]["status"] == "submitted"
    assert paper_orders[1]["status"] == "filled"

    assert len(paper_fills) == 1
    assert paper_fills[0]["symbol"] == "AAPL"
    assert paper_fills[0]["price"] == pytest.approx(120.25)

    assert len(positions) == 1
    assert positions[0]["symbol"] == "AAPL"
    assert positions[0]["status"] == "open"

    assert len(snapshots) == 1
    assert snapshots[0]["source_run_id"] == paper_state["paper_run_id"]
    assert snapshots[0]["total_equity"] == pytest.approx(100012.5)

    risk_filters = OperatorReadFilters(
        strategy_id="trend_following_daily",
        run_type="risk_evaluation",
        status="succeeded",
        session_start=date(2024, 1, 5),
        session_end=date(2024, 1, 5),
        limit=10,
    )
    risk_events = service.list_risk_events(risk_filters)
    assert len(risk_events) == 2
    assert {item["symbol"] for item in risk_events} == {"AAPL", "MSFT"}

    reconciliation_filters = OperatorReadFilters(
        strategy_id="trend_following_daily",
        run_type="reconciliation",
        status="succeeded",
        session_start=date(2024, 1, 5),
        session_end=date(2024, 1, 5),
        limit=10,
    )
    execution_events = service.list_execution_events(reconciliation_filters)
    assert len(execution_events) == 1
    assert execution_events[0]["run_id"] == paper_state["reconciliation_run_id"]
    assert execution_events[0]["blocks_execution"] is True

    run_detail = service.get_run_detail(paper_state["paper_run_id"])
    assert run_detail["artifact_counts"]["paper_orders"] == 2
    assert run_detail["artifact_counts"]["paper_fills"] == 1


def test_operator_read_service_handles_empty_state(
    migrated_analytics_db: str,
    strategy_config_override: None,
) -> None:
    _seed_strategy_record()
    settings = load_settings()
    service = OperatorReadService(settings)

    inspection = service.inspect_strategy(
        OperatorReadFilters(strategy_id="trend_following_daily", limit=5)
    )

    assert inspection["runs"]["count"] == 0
    assert inspection["paper_orders"]["count"] == 0
    assert inspection["paper_fills"]["count"] == 0
    assert inspection["positions"]["count"] == 0
    assert inspection["account_snapshots"]["count"] == 0
    assert inspection["risk_events"]["count"] == 0
    assert inspection["execution_events"]["count"] == 0


def test_strategy_analytics_report_renders_markdown_and_json(
    migrated_analytics_db: str,
    strategy_config_override: None,
) -> None:
    _seed_market_data(_trading_fixture())
    settings = load_settings()
    run_backtest(
        "trend_following_daily",
        from_date=date(2024, 1, 2),
        to_date=date(2024, 1, 10),
        settings=settings,
        trigger_source="pytest",
    )
    _seed_paper_operational_state()

    report = build_strategy_analytics_report(
        strategy_id="trend_following_daily",
        inspection_limit=3,
        settings=settings,
    )
    markdown = render_strategy_analytics_report(report, summary_format="markdown")
    json_output = render_strategy_analytics_report(report, summary_format="json")
    parsed = json.loads(json_output)

    assert "# Strategy Analytics: trend_following_daily" in markdown
    assert "## Recent Paper Orders" in markdown
    assert "MSFT" in markdown
    assert parsed["strategy"]["strategy_id"] == "trend_following_daily"
    assert parsed["inspection"]["paper_orders"]["count"] == 2
