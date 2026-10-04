from __future__ import annotations

import inspect
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
from alembic import command
from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.migrate import build_alembic_config
from tests.support.query_counter import count_queries
from tests.support.symbol_metadata import ready_symbol_fields

from trading_platform.core.settings import Settings, clear_settings_cache, load_settings
from trading_platform.db.models import (
    AccountSnapshot,
    RiskEvent,
    StrategyRun,
    StrategyRunStatus,
    StrategyRunType,
)
from trading_platform.db.models.daily_bar import DailyBar
from trading_platform.db.models.symbol import Symbol
from trading_platform.db.session import clear_engine_cache, session_scope
from trading_platform.services import risk as risk_module
from trading_platform.services.calendar import upsert_market_sessions
from trading_platform.services.portfolio import PortfolioState, PositionSnapshot
from trading_platform.services.risk import (
    PortfolioRiskService,
    RiskDecisionCode,
    RiskEvaluationRequest,
    run_risk_evaluation,
)
from trading_platform.strategies.signals import (
    IndicatorSnapshot,
    Signal,
    SignalBatch,
    SignalDirection,
    SignalReason,
)


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
def migrated_risk_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    database_name = f"risk_pipeline_{uuid.uuid4().hex[:8]}"
    admin_params = _admin_connection_settings()

    try:
        with _connect_admin(admin_params) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f'CREATE DATABASE "{database_name}"')
    except psycopg.Error as exc:
        pytest.fail(
            "PostgreSQL is required for tests/test_risk_pipeline.py. "
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


def _test_settings(*, max_positions: int = 10) -> Settings:
    settings = Settings.model_validate(load_settings().model_dump(mode="python"))
    settings.strategies.trend_following_daily.universe = ("AAPL", "MSFT")
    settings.strategies.trend_following_daily.risk.max_positions = max_positions
    return settings


@pytest.fixture()
def strategy_config_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
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


def _seed_symbol_and_bar(
    session, *, ticker: str, session_date: date, close: str, ready: bool = True
) -> Symbol:
    symbol = session.execute(select(Symbol).where(Symbol.ticker == ticker)).scalar_one_or_none()
    if symbol is None:
        # D-29: fixtures must model ready symbols (required metadata present);
        # ready=False seeds the legacy metadata-less row on purpose.
        fields = ready_symbol_fields() if ready else {"active": True}
        symbol = Symbol(ticker=ticker, **fields)
        session.add(symbol)
        session.flush()
    session.add(
        DailyBar(
            symbol_id=symbol.id,
            session_date=session_date,
            open=Decimal(close),
            high=Decimal(close),
            low=Decimal(close),
            close=Decimal(close),
            volume=1_000_000,
            adjusted=True,
            provider="polygon",
        )
    )
    session.flush()
    return symbol


def _signal(symbol: str, *, direction: SignalDirection, close: str = "100") -> Signal:
    return Signal(
        strategy_id="trend_following_daily",
        symbol=symbol,
        session_date=date(2024, 1, 5),
        direction=direction,
        reason=SignalReason.TREND_ENTRY if direction == SignalDirection.LONG else SignalReason.CLOSE_BELOW_EXIT_MA,
        indicators=IndicatorSnapshot(
            symbol=symbol,
            session_date=date(2024, 1, 5),
            close=Decimal(close),
            sma_short=Decimal(close),
            sma_long=Decimal(str(Decimal(close) - Decimal("1"))),
            bars_available=3,
        ),
    )


def test_risk_service_rejects_stale_market_data(migrated_risk_db: str) -> None:
    settings = _test_settings()
    service = PortfolioRiskService(settings)

    with session_scope(settings) as session:
        upsert_market_sessions(session, date(2024, 1, 5), date(2024, 1, 5))
        _seed_symbol_and_bar(session, ticker="AAPL", session_date=date(2024, 1, 5), close="100")
        # MSFT: ready symbol WITHOUT a bar for the session (stale-data path);
        # D-29 makes a metadata-less symbol symbol_not_ready instead.
        session.add(Symbol(ticker="MSFT", **ready_symbol_fields()))
        session.flush()

        result = service.validate(
            RiskEvaluationRequest(
                db_session=session,
                signal_batch=SignalBatch(
                    strategy_id="trend_following_daily",
                    as_of_session=date(2024, 1, 5),
                    signals=(
                        _signal("AAPL", direction=SignalDirection.LONG),
                        _signal("MSFT", direction=SignalDirection.LONG),
                    ),
                ),
                portfolio_state=PortfolioState(
                    cash=Decimal("100000.000000"),
                    gross_exposure=Decimal("0.000000"),
                    total_equity=Decimal("100000.000000"),
                    strategy_exposure=Decimal("0.000000"),
                    as_of_session=date(2024, 1, 5),
                ),
            )
        )

    assert {decision.code for decision in result.decisions} == {RiskDecisionCode.STALE_MARKET_DATA}


def test_risk_service_rejects_duplicate_position_and_max_positions(migrated_risk_db: str) -> None:
    settings = _test_settings(max_positions=1)
    service = PortfolioRiskService(settings)
    state = PortfolioState(
        cash=Decimal("90000.000000"),
        gross_exposure=Decimal("10000.000000"),
        total_equity=Decimal("100000.000000"),
        strategy_exposure=Decimal("10000.000000"),
        as_of_session=date(2024, 1, 5),
        open_positions=(
            PositionSnapshot(
                position_id=str(uuid.uuid4()),
                strategy_id="trend_following_daily",
                symbol="AAPL",
                quantity=Decimal("100"),
                average_entry_price=Decimal("100"),
                market_price=Decimal("100"),
                market_value=Decimal("10000.000000"),
            ),
        ),
        open_symbols=frozenset({"AAPL"}),
        total_open_positions=1,
    )

    with session_scope(settings) as session:
        upsert_market_sessions(session, date(2024, 1, 5), date(2024, 1, 5))
        _seed_symbol_and_bar(session, ticker="AAPL", session_date=date(2024, 1, 5), close="100")
        _seed_symbol_and_bar(session, ticker="MSFT", session_date=date(2024, 1, 5), close="100")

        result = service.validate(
            RiskEvaluationRequest(
                db_session=session,
                signal_batch=SignalBatch(
                    strategy_id="trend_following_daily",
                    as_of_session=date(2024, 1, 5),
                    signals=(
                        _signal("AAPL", direction=SignalDirection.LONG),
                        _signal("MSFT", direction=SignalDirection.LONG),
                    ),
                ),
                portfolio_state=state,
            )
        )

    by_symbol = {decision.symbol: decision for decision in result.decisions}
    assert by_symbol["AAPL"].code == RiskDecisionCode.DUPLICATE_OPEN_POSITION
    assert by_symbol["MSFT"].code == RiskDecisionCode.MAX_POSITIONS


def test_risk_service_rejects_entry_when_strategy_allocation_is_exhausted(migrated_risk_db: str) -> None:
    settings = _test_settings(max_positions=10)
    settings.portfolio.max_strategy_allocation_pct = 0.10
    service = PortfolioRiskService(settings)

    with session_scope(settings) as session:
        upsert_market_sessions(session, date(2024, 1, 5), date(2024, 1, 5))
        _seed_symbol_and_bar(session, ticker="AAPL", session_date=date(2024, 1, 5), close="100")
        _seed_symbol_and_bar(session, ticker="MSFT", session_date=date(2024, 1, 5), close="100")

        result = service.validate(
            RiskEvaluationRequest(
                db_session=session,
                signal_batch=SignalBatch(
                    strategy_id="trend_following_daily",
                    as_of_session=date(2024, 1, 5),
                    signals=(_signal("AAPL", direction=SignalDirection.LONG),),
                ),
                portfolio_state=PortfolioState(
                    cash=Decimal("5000.000000"),
                    gross_exposure=Decimal("10000.000000"),
                    total_equity=Decimal("100000.000000"),
                    strategy_exposure=Decimal("10000.000000"),
                    as_of_session=date(2024, 1, 5),
                    open_positions=(
                        PositionSnapshot(
                            position_id=str(uuid.uuid4()),
                            strategy_id="trend_following_daily",
                            symbol="MSFT",
                            quantity=Decimal("100"),
                            average_entry_price=Decimal("100"),
                            market_price=Decimal("100"),
                            market_value=Decimal("10000.000000"),
                        ),
                    ),
                    open_symbols=frozenset({"MSFT"}),
                    total_open_positions=1,
                ),
            )
        )

    assert result.decisions[0].code == RiskDecisionCode.STRATEGY_ALLOCATION_CAP


def test_risk_service_approves_entry_with_deterministic_whole_share_size(migrated_risk_db: str) -> None:
    settings = _test_settings(max_positions=10)
    service = PortfolioRiskService(settings)

    with session_scope(settings) as session:
        upsert_market_sessions(session, date(2024, 1, 5), date(2024, 1, 5))
        _seed_symbol_and_bar(session, ticker="AAPL", session_date=date(2024, 1, 5), close="100")
        _seed_symbol_and_bar(session, ticker="MSFT", session_date=date(2024, 1, 5), close="100")

        result = service.validate(
            RiskEvaluationRequest(
                db_session=session,
                signal_batch=SignalBatch(
                    strategy_id="trend_following_daily",
                    as_of_session=date(2024, 1, 5),
                    signals=(_signal("AAPL", direction=SignalDirection.LONG),),
                ),
                portfolio_state=PortfolioState(
                    cash=Decimal("100000.000000"),
                    gross_exposure=Decimal("0.000000"),
                    total_equity=Decimal("100000.000000"),
                    strategy_exposure=Decimal("0.000000"),
                    as_of_session=date(2024, 1, 5),
                ),
            )
        )

    decision = result.decisions[0]
    assert decision.code == RiskDecisionCode.APPROVED
    assert decision.proposed_quantity == Decimal("10")
    assert decision.proposed_notional == Decimal("1000.000000")


def _flat_state() -> PortfolioState:
    return PortfolioState(
        cash=Decimal("100000.000000"),
        gross_exposure=Decimal("0.000000"),
        total_equity=Decimal("100000.000000"),
        strategy_exposure=Decimal("0.000000"),
        as_of_session=date(2024, 1, 5),
    )


def test_risk_decision_code_set_is_closed_with_symbol_not_ready() -> None:
    # D-29: the previous ten values plus exactly one new member. decision_code
    # is String(64), so adding the member needs no migration.
    previous_ten = {
        "approved",
        "non_actionable_signal",
        "stale_market_data",
        "duplicate_open_position",
        "no_open_position",
        "max_positions",
        "strategy_allocation_cap",
        "total_allocation_cap",
        "insufficient_cash",
        "order_rounds_to_zero",
    }
    assert len(previous_ten) == 10
    assert {code.value for code in RiskDecisionCode} == previous_ten | {"symbol_not_ready"}
    assert RiskDecisionCode.SYMBOL_NOT_READY.value == "symbol_not_ready"


def test_not_ready_symbol_candidate_is_rejected_symbol_not_ready_and_others_unaffected(
    migrated_risk_db: str,
) -> None:
    settings = _test_settings(max_positions=10)
    service = PortfolioRiskService(settings)

    with session_scope(settings) as session:
        upsert_market_sessions(session, date(2024, 1, 5), date(2024, 1, 5))
        _seed_symbol_and_bar(session, ticker="AAPL", session_date=date(2024, 1, 5), close="100")
        _seed_symbol_and_bar(
            session, ticker="MSFT", session_date=date(2024, 1, 5), close="100", ready=False
        )

        result = service.validate(
            RiskEvaluationRequest(
                db_session=session,
                signal_batch=SignalBatch(
                    strategy_id="trend_following_daily",
                    as_of_session=date(2024, 1, 5),
                    signals=(
                        _signal("MSFT", direction=SignalDirection.LONG),
                        _signal("AAPL", direction=SignalDirection.LONG),
                    ),
                ),
                portfolio_state=_flat_state(),
            )
        )

    by_symbol = {decision.symbol: decision for decision in result.decisions}
    not_ready = by_symbol["MSFT"]
    assert not_ready.code == RiskDecisionCode.SYMBOL_NOT_READY
    assert not_ready.outcome.value == "rejected"
    assert not_ready.proposed_quantity is None
    assert "metadata_provider" in not_ready.reason
    # same evaluation, unaffected: sized exactly as the single-symbol case
    approved = by_symbol["AAPL"]
    assert approved.code == RiskDecisionCode.APPROVED
    assert approved.proposed_quantity == Decimal("10")
    assert approved.proposed_notional == Decimal("1000.000000")


def test_not_ready_symbol_exit_signal_is_rejected_symbol_not_ready(migrated_risk_db: str) -> None:
    settings = _test_settings(max_positions=10)
    service = PortfolioRiskService(settings)
    state = PortfolioState(
        cash=Decimal("80000.000000"),
        gross_exposure=Decimal("20000.000000"),
        total_equity=Decimal("100000.000000"),
        strategy_exposure=Decimal("20000.000000"),
        as_of_session=date(2024, 1, 5),
        open_positions=tuple(
            PositionSnapshot(
                position_id=str(uuid.uuid4()),
                strategy_id="trend_following_daily",
                symbol=ticker,
                quantity=Decimal("100"),
                average_entry_price=Decimal("100"),
                market_price=Decimal("100"),
                market_value=Decimal("10000.000000"),
            )
            for ticker in ("AAPL", "MSFT")
        ),
        open_symbols=frozenset({"AAPL", "MSFT"}),
        total_open_positions=2,
    )

    with session_scope(settings) as session:
        upsert_market_sessions(session, date(2024, 1, 5), date(2024, 1, 5))
        _seed_symbol_and_bar(session, ticker="AAPL", session_date=date(2024, 1, 5), close="100")
        _seed_symbol_and_bar(
            session, ticker="MSFT", session_date=date(2024, 1, 5), close="100", ready=False
        )

        result = service.validate(
            RiskEvaluationRequest(
                db_session=session,
                signal_batch=SignalBatch(
                    strategy_id="trend_following_daily",
                    as_of_session=date(2024, 1, 5),
                    signals=(
                        _signal("AAPL", direction=SignalDirection.EXIT),
                        _signal("MSFT", direction=SignalDirection.EXIT),
                    ),
                ),
                portfolio_state=state,
            )
        )

    by_symbol = {decision.symbol: decision for decision in result.decisions}
    assert by_symbol["MSFT"].code == RiskDecisionCode.SYMBOL_NOT_READY
    assert by_symbol["AAPL"].code == RiskDecisionCode.APPROVED
    assert by_symbol["AAPL"].proposed_quantity == Decimal("100")


def test_validate_reads_symbol_readiness_in_one_statement(
    migrated_risk_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _test_settings(max_positions=10)
    service = PortfolioRiskService(settings)
    observed: list[int] = []
    real = risk_module.symbols_readiness

    def counting(session, tickers):
        with count_queries(session) as counter:
            value = real(session, tickers)
        observed.append(counter.count)
        return value

    monkeypatch.setattr(risk_module, "symbols_readiness", counting)

    with session_scope(settings) as session:
        upsert_market_sessions(session, date(2024, 1, 5), date(2024, 1, 5))
        for ticker in ("AAPL", "MSFT", "NVDA"):
            _seed_symbol_and_bar(session, ticker=ticker, session_date=date(2024, 1, 5), close="100")
        session.flush()

        service.validate(
            RiskEvaluationRequest(
                db_session=session,
                signal_batch=SignalBatch(
                    strategy_id="trend_following_daily",
                    as_of_session=date(2024, 1, 5),
                    signals=tuple(
                        _signal(ticker, direction=SignalDirection.LONG)
                        for ticker in ("AAPL", "MSFT", "NVDA")
                    ),
                ),
                portfolio_state=_flat_state(),
            )
        )

    assert observed == [1]


def test_run_risk_evaluation_persists_strategy_run_and_risk_events(
    migrated_risk_db: str,
    strategy_config_override: None,
) -> None:
    settings = load_settings()

    with session_scope(settings) as session:
        upsert_market_sessions(session, date(2024, 1, 3), date(2024, 1, 5))
        _seed_symbol_and_bar(session, ticker="AAPL", session_date=date(2024, 1, 3), close="100")
        _seed_symbol_and_bar(session, ticker="AAPL", session_date=date(2024, 1, 4), close="110")
        _seed_symbol_and_bar(session, ticker="AAPL", session_date=date(2024, 1, 5), close="120")
        _seed_symbol_and_bar(session, ticker="MSFT", session_date=date(2024, 1, 3), close="100")
        _seed_symbol_and_bar(session, ticker="MSFT", session_date=date(2024, 1, 4), close="100")
        _seed_symbol_and_bar(session, ticker="MSFT", session_date=date(2024, 1, 5), close="100")

    report = run_risk_evaluation(
        "trend_following_daily",
        as_of_session=date(2024, 1, 5),
        trigger_source="test_suite",
        settings=settings,
    )

    assert report.status == StrategyRunStatus.SUCCEEDED.value
    assert report.result_summary["approved_count"] == 1
    assert report.result_summary["rejected_count"] == 1

    with session_scope(settings) as session:
        strategy_run = session.execute(
            select(StrategyRun).where(StrategyRun.id == uuid.UUID(report.run_id))
        ).scalar_one()
        risk_events = session.execute(
            select(RiskEvent).where(RiskEvent.strategy_run_id == strategy_run.id).order_by(RiskEvent.signal_direction)
        ).scalars().all()

    assert strategy_run.run_type == StrategyRunType.RISK_EVALUATION
    assert len(risk_events) == 2
    assert {event.decision_code for event in risk_events} == {"approved", "non_actionable_signal"}


def test_run_risk_evaluation_writes_no_account_snapshot_and_records_portfolio_basis(
    migrated_risk_db: str,
    strategy_config_override: None,
) -> None:
    """D-27: evaluation persists no AccountSnapshot; the basis rides on the run."""

    settings = load_settings()

    with session_scope(settings) as session:
        upsert_market_sessions(session, date(2024, 1, 3), date(2024, 1, 5))
        for ticker, closes in (("AAPL", ("100", "110", "120")), ("MSFT", ("100", "100", "100"))):
            for day, close in zip((3, 4, 5), closes, strict=True):
                _seed_symbol_and_bar(session, ticker=ticker, session_date=date(2024, 1, day), close=close)

    def _snapshot_count() -> int:
        with session_scope(settings) as session:
            return session.execute(select(func.count()).select_from(AccountSnapshot)).scalar_one()

    before = _snapshot_count()
    report = run_risk_evaluation(
        "trend_following_daily",
        as_of_session=date(2024, 1, 5),
        trigger_source="test_suite",
        settings=settings,
    )
    assert _snapshot_count() == before

    basis = report.result_summary["portfolio_basis"]
    assert set(basis) == {
        "source",
        "cash",
        "gross_exposure",
        "total_equity",
        "positions",
        "total_open_positions",
        "as_of_session",
        "snapshot_id",
        "snapshot_at",
        "age_seconds",
    }
    assert basis["source"] == "configured_starting_cash"
    assert basis["snapshot_id"] is None
    assert basis["positions"] == []
    assert basis["as_of_session"] == "2024-01-05"
    # Existing summary keys are unchanged (additive key only).
    assert report.result_summary["approved_count"] == 1


def test_run_risk_evaluation_source_does_not_write_account_snapshots() -> None:
    source = inspect.getsource(run_risk_evaluation)
    assert "record_snapshot" not in source
    assert "AccountSnapshot" not in source
