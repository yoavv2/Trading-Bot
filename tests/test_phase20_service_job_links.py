"""Phase 20 Plan 05 tests (D-09/D-23): job_id threading for risk, reconciliation
and ingestion runs, plus the read-only risk-run eligibility check.

Mirrors tests/test_backtest_job_link.py's pattern: job_id is an opaque
originating-Job identifier, written in the same transaction that creates the
run. Each service module imports nothing from ``jobs``/.

Uses its own isolated migrated Postgres database (mirrors the exact
create/upgrade/teardown sequence established by tests/test_risk_pipeline.py's
``migrated_risk_db`` -- no shared conftest.py fixture exists for this shape).
"""

from __future__ import annotations

import os
import sys
import uuid
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import psycopg
import pytest
import yaml
from alembic import command
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.migrate import build_alembic_config  # noqa: E402

from trading_platform.core.settings import (  # noqa: E402
    IngestSettings,
    MarketDataSettings,
    PolygonProviderSettings,
    clear_settings_cache,
    load_settings,
)
from trading_platform.db.models import (  # noqa: E402
    MarketDataIngestionRun,
    Strategy,
    StrategyRun,
    StrategyRunStatus,
    StrategyRunType,
)
from trading_platform.db.session import clear_engine_cache, session_scope  # noqa: E402
from trading_platform.jobs.dependencies import submit_job  # noqa: E402
from trading_platform.services import risk  # noqa: E402
from trading_platform.services.ingestion import ingest_daily_bars  # noqa: E402
from trading_platform.services.reconciliation import reconcile_paper_execution  # noqa: E402
from trading_platform.services.risk import is_eligible_risk_run, run_risk_evaluation  # noqa: E402


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
def migrated_job_links_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    database_name = f"phase20_job_links_{uuid.uuid4().hex[:8]}"
    admin_params = _admin_connection_settings()

    try:
        with _connect_admin(admin_params) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f'CREATE DATABASE "{database_name}"')
    except psycopg.Error as exc:  # pragma: no cover - exercised when local Postgres is unavailable
        pytest.fail(
            "PostgreSQL is required for tests/test_phase20_service_job_links.py. "
            "Start the local db service first (for example `docker compose up -d db`). "
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


def _seed_symbol_and_bar(session, *, ticker: str, session_date: date, close: str) -> None:
    from trading_platform.db.models.daily_bar import DailyBar
    from trading_platform.db.models.symbol import Symbol

    symbol = session.execute(select(Symbol).where(Symbol.ticker == ticker)).scalar_one_or_none()
    if symbol is None:
        symbol = Symbol(ticker=ticker, active=True)
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


class FakeBrokerClient:
    """Minimal empty-book broker double, mirroring
    tests/test_execution_reconciliation.py's FakeBrokerClient."""

    def __init__(self, *, orders=(), fills=(), positions=(), account) -> None:
        self._orders = list(orders)
        self._fills = list(fills)
        self._positions = list(positions)
        self._account = account

    def close(self) -> None:
        return None

    def list_orders(self):
        return list(self._orders)

    def list_fills(self):
        return list(self._fills)

    def list_positions(self):
        return list(self._positions)

    def get_account(self):
        return self._account


def _empty_broker_client() -> FakeBrokerClient:
    from trading_platform.services.alpaca import BrokerAccountSnapshot

    return FakeBrokerClient(
        account=BrokerAccountSnapshot(
            cash=Decimal("100000.000000"),
            buying_power=Decimal("100000.000000"),
            equity=Decimal("100000.000000"),
            long_market_value=Decimal("0"),
            short_market_value=Decimal("0"),
            raw_payload={"equity": "100000.000000"},
        )
    )


def _make_market_data_settings(api_key: str = "test-key") -> MarketDataSettings:
    return MarketDataSettings(
        polygon=PolygonProviderSettings(
            base_url="https://api.polygon.io",
            api_key=api_key,
            adjusted=True,
            max_retries=0,
            retry_backoff_factor=0.0,
            timeout_seconds=5.0,
        ),
        ingest=IngestSettings(
            default_lookback_days=10,
            universe=("AAPL", "MSFT"),
        ),
    )


FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "polygon_daily_bars.json"


def _polygon_response() -> MagicMock:
    import json

    fixture = json.loads(FIXTURE_PATH.read_text())
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = fixture
    return mock_response


# ---------------------------------------------------------------------------
# risk-evaluation job_id threading
# ---------------------------------------------------------------------------


def test_run_risk_evaluation_links_job_id_while_pending(
    migrated_job_links_db: str,
    strategy_config_override: None,
) -> None:
    settings = load_settings()

    with session_scope(settings) as session:
        from trading_platform.services.calendar import upsert_market_sessions

        upsert_market_sessions(session, date(2024, 1, 3), date(2024, 1, 5))
        _seed_symbol_and_bar(session, ticker="AAPL", session_date=date(2024, 1, 3), close="100")
        _seed_symbol_and_bar(session, ticker="AAPL", session_date=date(2024, 1, 4), close="110")
        _seed_symbol_and_bar(session, ticker="AAPL", session_date=date(2024, 1, 5), close="120")
        _seed_symbol_and_bar(session, ticker="MSFT", session_date=date(2024, 1, 3), close="100")
        _seed_symbol_and_bar(session, ticker="MSFT", session_date=date(2024, 1, 4), close="100")
        _seed_symbol_and_bar(session, ticker="MSFT", session_date=date(2024, 1, 5), close="100")

    job_id = submit_job(job_type="risk-evaluation", payload={}, settings=settings)

    recorded: dict[str, Any] = {}
    original_update = risk._update_risk_run

    def _probe(settings_arg, run_id, **kwargs):
        with session_scope(settings_arg) as session:
            probed_run = session.execute(
                select(StrategyRun).where(StrategyRun.id == run_id)
            ).scalar_one()
            recorded.setdefault("status", probed_run.status)
            recorded.setdefault("job_id", probed_run.job_id)
        return original_update(settings_arg, run_id, **kwargs)

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(risk, "_update_risk_run", _probe)
        report = run_risk_evaluation(
            "trend_following_daily",
            as_of_session=date(2024, 1, 5),
            trigger_source="job",
            settings=settings,
            job_id=job_id,
        )

    assert recorded["status"] == StrategyRunStatus.PENDING
    assert recorded["job_id"] == job_id

    with session_scope(settings) as session:
        strategy_run = session.execute(
            select(StrategyRun).where(StrategyRun.id == uuid.UUID(report.run_id))
        ).scalar_one()
        assert strategy_run.job_id == job_id


def test_run_risk_evaluation_without_job_id_leaves_link_null(
    migrated_job_links_db: str,
    strategy_config_override: None,
) -> None:
    settings = load_settings()

    with session_scope(settings) as session:
        from trading_platform.services.calendar import upsert_market_sessions

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
        trigger_source="pytest",
        settings=settings,
    )

    with session_scope(settings) as session:
        strategy_run = session.execute(
            select(StrategyRun).where(StrategyRun.id == uuid.UUID(report.run_id))
        ).scalar_one()
        assert strategy_run.job_id is None


# ---------------------------------------------------------------------------
# reconciliation job_id threading
# ---------------------------------------------------------------------------


def test_reconcile_paper_execution_links_job_id(migrated_job_links_db: str) -> None:
    settings = load_settings()
    job_id = submit_job(job_type="reconciliation", payload={}, settings=settings)

    report = reconcile_paper_execution(
        "trend_following_daily",
        as_of_session=date(2024, 1, 5),
        settings=settings,
        broker_client=_empty_broker_client(),
        job_id=job_id,
    )

    with session_scope(settings) as session:
        strategy_run = session.execute(
            select(StrategyRun).where(StrategyRun.id == uuid.UUID(report.run_id))
        ).scalar_one()
        assert strategy_run.job_id == job_id


def test_reconcile_paper_execution_without_job_id_leaves_link_null(
    migrated_job_links_db: str,
) -> None:
    settings = load_settings()

    report = reconcile_paper_execution(
        "trend_following_daily",
        as_of_session=date(2024, 1, 5),
        settings=settings,
        broker_client=_empty_broker_client(),
    )

    with session_scope(settings) as session:
        strategy_run = session.execute(
            select(StrategyRun).where(StrategyRun.id == uuid.UUID(report.run_id))
        ).scalar_one()
        assert strategy_run.job_id is None


# ---------------------------------------------------------------------------
# ingest-bars job_id threading
# ---------------------------------------------------------------------------


def test_ingest_daily_bars_links_job_id(migrated_job_links_db: str) -> None:
    settings = load_settings()
    job_id = submit_job(job_type="ingest-bars", payload={}, settings=settings)
    md_settings = _make_market_data_settings()

    with patch("httpx.Client.get", return_value=_polygon_response()):
        result = ingest_daily_bars(
            from_date=date(2024, 1, 1),
            to_date=date(2024, 1, 3),
            symbols=["AAPL"],
            settings=md_settings,
            trigger_source="job",
            db_settings=settings,
            job_id=job_id,
        )

    assert result.succeeded

    with session_scope(settings) as session:
        run = session.execute(select(MarketDataIngestionRun)).scalars().one()
        assert run.job_id == job_id


def test_ingest_daily_bars_without_job_id_leaves_link_null(migrated_job_links_db: str) -> None:
    settings = load_settings()
    md_settings = _make_market_data_settings()

    with patch("httpx.Client.get", return_value=_polygon_response()):
        ingest_daily_bars(
            from_date=date(2024, 1, 1),
            to_date=date(2024, 1, 3),
            symbols=["AAPL"],
            settings=md_settings,
            trigger_source="test",
            db_settings=settings,
        )

    with session_scope(settings) as session:
        run = session.execute(select(MarketDataIngestionRun)).scalars().one()
        assert run.job_id is None


def test_failing_ingest_leaves_failed_run_linked_to_job(migrated_job_links_db: str) -> None:
    """CR-B-01: the run row (with job_id) is committed before the work and
    finalized FAILED in its own transaction, so a failing ingest keeps it."""
    settings = load_settings()
    job_id = submit_job(job_type="ingest-bars", payload={}, settings=settings)
    md_settings = _make_market_data_settings()

    with patch(
        "trading_platform.services.ingestion.PolygonClient",
        side_effect=RuntimeError("polygon client exploded"),
    ):
        with pytest.raises(RuntimeError, match="polygon client exploded"):
            ingest_daily_bars(
                from_date=date(2024, 1, 1),
                to_date=date(2024, 1, 3),
                symbols=["AAPL"],
                settings=md_settings,
                trigger_source="job",
                db_settings=settings,
                job_id=job_id,
            )

    with session_scope(settings) as session:
        run = session.execute(select(MarketDataIngestionRun)).scalars().one()
        assert run.job_id == job_id
        assert run.status == "failed"
        assert run.error_message == "polygon client exploded"
        assert run.completed_at is not None


def test_ingest_run_is_visible_while_running(migrated_job_links_db: str) -> None:
    """CR-B-01: the running row is committed (visible to other sessions)
    before any bar is fetched."""
    settings = load_settings()
    job_id = submit_job(job_type="ingest-bars", payload={}, settings=settings)
    md_settings = _make_market_data_settings()
    seen: dict[str, Any] = {}

    def probing_get(*args: Any, **kwargs: Any) -> MagicMock:
        with session_scope(settings) as probe:
            run = probe.execute(select(MarketDataIngestionRun)).scalars().one()
            seen["job_id"] = run.job_id
            seen["status"] = run.status
        return _polygon_response()

    with patch("httpx.Client.get", side_effect=probing_get):
        ingest_daily_bars(
            from_date=date(2024, 1, 1),
            to_date=date(2024, 1, 3),
            symbols=["AAPL"],
            settings=md_settings,
            trigger_source="job",
            db_settings=settings,
            job_id=job_id,
        )

    assert seen == {"job_id": job_id, "status": "running"}


# ---------------------------------------------------------------------------
# is_eligible_risk_run (D-23)
# ---------------------------------------------------------------------------


def _seed_strategy(session, *, strategy_id: str) -> Strategy:
    strategy = Strategy(
        strategy_id=strategy_id,
        display_name=f"Strategy {strategy_id}",
        config_reference=f"{strategy_id}.yaml",
    )
    session.add(strategy)
    session.flush()
    return strategy


def _seed_risk_run(
    session,
    *,
    strategy: Strategy,
    status: StrategyRunStatus,
    as_of_session: date,
    run_type: StrategyRunType = StrategyRunType.RISK_EVALUATION,
) -> StrategyRun:
    run = StrategyRun(
        strategy_id=strategy.id,
        run_type=run_type,
        status=status,
        trigger_source="test_suite",
        parameters_snapshot={"as_of_session": as_of_session.isoformat()},
        result_summary={},
    )
    session.add(run)
    session.flush()
    return run


def _run_count(session) -> int:
    return len(session.execute(select(StrategyRun)).scalars().all())


def test_is_eligible_risk_run_true_for_matching_succeeded_run(
    migrated_job_links_db: str,
) -> None:
    settings = load_settings()

    with session_scope(settings) as session:
        strategy = _seed_strategy(session, strategy_id="trend_following_daily")
        run = _seed_risk_run(
            session,
            strategy=strategy,
            status=StrategyRunStatus.SUCCEEDED,
            as_of_session=date(2024, 1, 5),
        )
        run_id = run.id
        before_count = _run_count(session)

    result = is_eligible_risk_run(
        risk_run_id=run_id,
        strategy_id="trend_following_daily",
        as_of_session=date(2024, 1, 5),
        settings=settings,
    )

    assert result is True

    with session_scope(settings) as session:
        assert _run_count(session) == before_count


def test_is_eligible_risk_run_false_for_wrong_strategy(migrated_job_links_db: str) -> None:
    settings = load_settings()

    with session_scope(settings) as session:
        strategy = _seed_strategy(session, strategy_id="trend_following_daily")
        run = _seed_risk_run(
            session,
            strategy=strategy,
            status=StrategyRunStatus.SUCCEEDED,
            as_of_session=date(2024, 1, 5),
        )
        run_id = run.id

    result = is_eligible_risk_run(
        risk_run_id=run_id,
        strategy_id="some_other_strategy",
        as_of_session=date(2024, 1, 5),
        settings=settings,
    )

    assert result is False


def test_is_eligible_risk_run_false_for_wrong_session(migrated_job_links_db: str) -> None:
    settings = load_settings()

    with session_scope(settings) as session:
        strategy = _seed_strategy(session, strategy_id="trend_following_daily")
        run = _seed_risk_run(
            session,
            strategy=strategy,
            status=StrategyRunStatus.SUCCEEDED,
            as_of_session=date(2024, 1, 5),
        )
        run_id = run.id

    result = is_eligible_risk_run(
        risk_run_id=run_id,
        strategy_id="trend_following_daily",
        as_of_session=date(2024, 1, 4),
        settings=settings,
    )

    assert result is False


def test_is_eligible_risk_run_false_for_non_succeeded_status(
    migrated_job_links_db: str,
) -> None:
    settings = load_settings()

    with session_scope(settings) as session:
        strategy = _seed_strategy(session, strategy_id="trend_following_daily")
        run = _seed_risk_run(
            session,
            strategy=strategy,
            status=StrategyRunStatus.FAILED,
            as_of_session=date(2024, 1, 5),
        )
        run_id = run.id

    result = is_eligible_risk_run(
        risk_run_id=run_id,
        strategy_id="trend_following_daily",
        as_of_session=date(2024, 1, 5),
        settings=settings,
    )

    assert result is False


def test_is_eligible_risk_run_false_for_non_risk_run_type(migrated_job_links_db: str) -> None:
    settings = load_settings()

    with session_scope(settings) as session:
        strategy = _seed_strategy(session, strategy_id="trend_following_daily")
        run = _seed_risk_run(
            session,
            strategy=strategy,
            status=StrategyRunStatus.SUCCEEDED,
            as_of_session=date(2024, 1, 5),
            run_type=StrategyRunType.RECONCILIATION,
        )
        run_id = run.id

    result = is_eligible_risk_run(
        risk_run_id=run_id,
        strategy_id="trend_following_daily",
        as_of_session=date(2024, 1, 5),
        settings=settings,
    )

    assert result is False


def test_is_eligible_risk_run_false_for_unknown_uuid(migrated_job_links_db: str) -> None:
    settings = load_settings()

    result = is_eligible_risk_run(
        risk_run_id=uuid.uuid4(),
        strategy_id="trend_following_daily",
        as_of_session=date(2024, 1, 5),
        settings=settings,
    )

    assert result is False
