"""29 Sep replay and canonical stage order (D-27, COR-01, 20.1-03).

On 29 Sep a risk evaluation persisted a derived ``AccountSnapshot`` with
``buying_power = cash``. It became the "latest" local account state, so the next
reconciliation diverged from a broker whose buying power is a multiple of cash
and blocked execution. These tests replay that sequence against the fixed code.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.support.migrated_db import migrated_database

from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.db.models import (
    AccountSnapshot,
    StrategyRun,
    StrategyRunType,
)
from trading_platform.db.models.daily_bar import DailyBar
from trading_platform.db.models.symbol import Symbol
from trading_platform.db.session import session_scope
from trading_platform.services.alpaca import BrokerAccountSnapshot
from trading_platform.services.analytics import StrategyAnalyticsService
from trading_platform.services.bootstrap import ensure_strategy_record
from trading_platform.services.calendar import upsert_market_sessions
from trading_platform.services.reconciliation import reconcile_paper_execution
from trading_platform.services.risk import run_risk_evaluation
from trading_platform.strategies.registry import build_default_registry

CASH = Decimal("100000")
BUYING_POWER = CASH * 4
SESSION = date(2024, 1, 5)


@pytest.fixture()
def replay_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    with migrated_database(monkeypatch, "account_truth_replay") as name:
        yield name


@pytest.fixture()
def strategy_config_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    strategy_dir = tmp_path / "strategies"
    strategy_dir.mkdir()
    (strategy_dir / "trend_following_daily.yaml").write_text(
        yaml.safe_dump(
            {
                "strategy_id": "trend_following_daily",
                "display_name": "TrendFollowingDailyV1",
                "enabled": True,
                "universe": ["AAPL", "MSFT"],
                "indicators": {"short_window": 2, "long_window": 3, "warmup_periods": 3},
                "risk": {"max_positions": 10, "risk_per_trade": 0.01},
                "exits": {"close_below": "sma_2", "exit_window": 2},
            }
        )
    )
    monkeypatch.setenv("TRADING_PLATFORM_STRATEGY_CONFIG_DIR", str(strategy_dir))
    clear_settings_cache()
    try:
        yield
    finally:
        clear_settings_cache()


class _UnchangedBroker:
    """Fake broker whose account never changes: flat book, buying power 4x cash."""

    def close(self) -> None:
        return None

    def list_orders(self) -> list:
        return []

    def list_fills(self) -> list:
        return []

    def list_positions(self) -> list:
        return []

    def get_account(self) -> BrokerAccountSnapshot:
        return BrokerAccountSnapshot(
            cash=CASH,
            buying_power=BUYING_POWER,
            equity=CASH,
            long_market_value=Decimal("0"),
            short_market_value=Decimal("0"),
            raw_payload={"equity": str(CASH)},
        )


def _seed_bars_and_broker_snapshot() -> None:
    settings = load_settings()
    strategy = build_default_registry(settings).resolve("trend_following_daily")
    with session_scope(settings) as session:
        strategy_record = ensure_strategy_record(session, strategy.metadata)
        upsert_market_sessions(session, date(2024, 1, 3), SESSION)
        for ticker, closes in (("AAPL", ("100", "110", "120")), ("MSFT", ("100", "100", "100"))):
            symbol = Symbol(ticker=ticker, active=True)
            session.add(symbol)
            session.flush()
            for day, close in zip((3, 4, 5), closes, strict=True):
                session.add(
                    DailyBar(
                        symbol_id=symbol.id,
                        session_date=date(2024, 1, day),
                        open=Decimal(close),
                        high=Decimal(close),
                        low=Decimal(close),
                        close=Decimal(close),
                        volume=1_000_000,
                        adjusted=True,
                        provider="polygon",
                    )
                )
        session.add(
            AccountSnapshot(
                strategy_id=strategy_record.id,
                snapshot_source="broker_sync",
                snapshot_at=datetime(2024, 1, 5, 14, 45, tzinfo=UTC),
                cash=CASH,
                gross_exposure=Decimal("0"),
                total_equity=CASH,
                buying_power=BUYING_POWER,
                open_positions=0,
            )
        )


def _evaluate_then_reconcile():
    settings = load_settings()
    report = run_risk_evaluation(
        "trend_following_daily",
        as_of_session=SESSION,
        trigger_source="test_suite",
        settings=settings,
    )
    reconciliation = reconcile_paper_execution(
        "trend_following_daily",
        as_of_session=SESSION,
        settings=settings,
        broker_client=_UnchangedBroker(),
    )
    with session_scope(settings) as session:
        run = session.execute(
            select(StrategyRun).where(StrategyRun.run_type == StrategyRunType.RECONCILIATION)
        ).scalars().one()
        divergence = run.result_summary["account_divergence"]
    return report, reconciliation, divergence


def _count_snapshots(source: str | None = None) -> int:
    with session_scope(load_settings()) as session:
        statement = select(func.count()).select_from(AccountSnapshot)
        if source is not None:
            statement = statement.where(AccountSnapshot.snapshot_source == source)
        return session.execute(statement).scalar_one()


def test_29_sep_replay_evaluate_then_reconcile_is_clean(
    replay_db: str, strategy_config_override: None
) -> None:
    _seed_bars_and_broker_snapshot()

    report, reconciliation, divergence = _evaluate_then_reconcile()

    assert report.result_summary["portfolio_basis"]["source"] == "broker_sync"
    assert report.result_summary["portfolio_basis"]["cash"] == "100000.000000"
    assert divergence == {}
    assert reconciliation.blocks_execution is False
    # The pre-fix behaviour wrote a risk_evaluation snapshot (buying_power == cash)
    # that became the baseline; none may exist now.
    assert _count_snapshots("risk_evaluation") == 0
    assert _count_snapshots() == 1


def test_latest_account_read_after_evaluation_shows_broker_snapshot(
    replay_db: str, strategy_config_override: None
) -> None:
    _seed_bars_and_broker_snapshot()
    run_risk_evaluation(
        "trend_following_daily",
        as_of_session=SESSION,
        trigger_source="test_suite",
        settings=load_settings(),
    )

    summary = StrategyAnalyticsService(load_settings()).summarize(
        {"strategy_id": "trend_following_daily"}
    )

    snapshot = summary["paper"]["latest_account_snapshot"]
    assert snapshot["snapshot_source"] == "broker_sync"
    assert snapshot["buying_power"] == pytest.approx(float(BUYING_POWER))


def test_trade_needs_no_sync_after_decide_stage_order(
    replay_db: str, strategy_config_override: None
) -> None:
    """Canonical stage order: Ingest -> Decide (evaluate) -> Trade (paper-session)
    -> Sync -> Reconcile.

    Decide must leave account truth untouched, so a standalone reconciliation
    directly after it (no Sync in between) against an unchanged broker is clean.
    There is no Sync-before-Trade workaround in the product (S3).
    """
    _seed_bars_and_broker_snapshot()
    before = _count_snapshots()

    _report, reconciliation, divergence = _evaluate_then_reconcile()

    assert _count_snapshots() == before  # Decide wrote no account truth
    assert divergence == {}
    assert reconciliation.blocks_execution is False

