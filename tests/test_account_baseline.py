"""Broker-observed account baseline helper (D-27, 20.1-03)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from tests.support.migrated_db import migrated_database
from tests.support.query_counter import count_queries

from trading_platform.core.settings import load_settings
from trading_platform.db.models.account_snapshot import AccountSnapshot
from trading_platform.db.session import session_scope
from trading_platform.services.account_baseline import (
    BROKER_OBSERVED_SNAPSHOT_SOURCE,
    latest_broker_observed_account_snapshot,
)

T0 = datetime(2024, 1, 5, 12, 0, tzinfo=UTC)


@pytest.fixture()
def baseline_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    with migrated_database(monkeypatch, "account_baseline") as name:
        yield name


def _snapshot(source: str, *, at: datetime, cash: str = "100", strategy_id=None) -> AccountSnapshot:
    return AccountSnapshot(
        strategy_id=strategy_id,
        snapshot_source=source,
        snapshot_at=at,
        cash=Decimal(cash),
        gross_exposure=Decimal("0"),
        total_equity=Decimal(cash),
        buying_power=Decimal(cash),
        open_positions=0,
    )


def test_source_constant_is_broker_sync() -> None:
    assert BROKER_OBSERVED_SNAPSHOT_SOURCE == "broker_sync"


def test_returns_none_when_no_broker_observed_snapshot(baseline_db: str) -> None:
    settings = load_settings()
    with session_scope(settings) as session:
        session.add(_snapshot("risk_evaluation", at=T0))
        session.add(_snapshot("seed", at=T0))
        session.flush()
        assert latest_broker_observed_account_snapshot(session) is None


def test_broker_sync_wins_over_newer_non_broker_sources(baseline_db: str) -> None:
    settings = load_settings()
    with session_scope(settings) as session:
        session.add(_snapshot("broker_sync", at=T0, cash="111"))
        session.add(_snapshot("risk_evaluation", at=T0 + timedelta(hours=1), cash="1"))
        session.add(_snapshot("seed", at=T0 + timedelta(hours=2), cash="2"))
        session.add(_snapshot("derived", at=T0 + timedelta(hours=3), cash="3"))
        session.flush()
        latest = latest_broker_observed_account_snapshot(session)
        assert latest is not None
        assert latest.cash == Decimal("111")
        assert latest.snapshot_source == "broker_sync"


def test_newest_broker_snapshot_wins_and_null_strategy_is_allowed(baseline_db: str) -> None:
    settings = load_settings()
    with session_scope(settings) as session:
        session.add(_snapshot("broker_sync", at=T0, cash="10", strategy_id=None))
        session.add(_snapshot("broker_sync", at=T0 + timedelta(minutes=5), cash="20", strategy_id=None))
        session.flush()
        latest = latest_broker_observed_account_snapshot(session)
        assert latest is not None
        assert latest.strategy_id is None
        assert latest.cash == Decimal("20")


def test_helper_issues_exactly_one_statement(baseline_db: str) -> None:
    settings = load_settings()
    with session_scope(settings) as session:
        session.add(_snapshot("broker_sync", at=T0))
        session.flush()
        with count_queries(session) as counter:
            latest_broker_observed_account_snapshot(session)
        assert counter.count == 1
