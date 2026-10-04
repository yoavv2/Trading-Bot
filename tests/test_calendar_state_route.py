"""GET /api/v1/market-data/calendar-state (interim runbook R4, COR-04).

Read-only: three calendar facts with explicit unknowns, calendar coverage and
per-symbol readiness (bars, history, metadata) per registered strategy.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from tests.support.calendar_facts import (
    FakeStrategy,
    et,
    seed_bars,
    seed_calendar,
    sessions_between,
)
from tests.support.migrated_db import migrated_database
from tests.support.query_counter import count_queries

from trading_platform.api.app import create_app
from trading_platform.api.dependencies import get_strategy_registry
from trading_platform.api.routes import market_data as market_data_route
from trading_platform.core.settings import Settings, load_settings
from trading_platform.db.models.daily_bar import DailyBar
from trading_platform.db.models.market_session import MarketSession
from trading_platform.db.models.symbol import Symbol
from trading_platform.db.session import get_engine, session_scope
from trading_platform.strategies.registry import UnknownStrategyError

OPEN_NOW = et(2025, 12, 2, 10, 0)


class _FakeRegistry:
    def __init__(self, *strategies: FakeStrategy) -> None:
        self._strategies = {s.strategy_id: s for s in strategies}

    def resolve(self, strategy_id: str) -> FakeStrategy:
        try:
            return self._strategies[strategy_id]
        except KeyError as exc:
            raise UnknownStrategyError(strategy_id) from exc

    def list_metadata(self) -> list[Any]:
        return [self._strategies[key].metadata for key in sorted(self._strategies)]


@pytest.fixture()
def state_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[Settings]:
    with migrated_database(monkeypatch, "calstate") as _name:
        yield load_settings()


def _client(
    monkeypatch: pytest.MonkeyPatch, now: datetime, *strategies: FakeStrategy
) -> TestClient:
    monkeypatch.setattr(market_data_route, "_now", lambda: now)
    app = create_app()
    app.dependency_overrides[get_strategy_registry] = lambda: _FakeRegistry(*strategies)
    return TestClient(app)


def _get(client: TestClient, path: str = "/api/v1/market-data/calendar-state") -> Any:
    return client.get(path)


def _seed_ready(tickers: list[str]) -> None:
    seed_calendar(date(2025, 11, 1), date(2026, 3, 31))
    seed_bars(tickers, sessions_between(date(2025, 11, 20), date(2025, 12, 1)))


def test_open_window_happy_path_with_a_ready_strategy(
    state_db: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_ready(["AAA", "BBB"])
    strategy = FakeStrategy(["AAA", "BBB"], warmup_periods=3, strategy_id="alpha")

    with _client(monkeypatch, OPEN_NOW, strategy) as client:
        response = _get(client)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["as_of"] == OPEN_NOW.isoformat()
    assert body["policy"] == {"name": "regular_hours_prev_session_v1", "cutoff_minutes": 15}
    assert body["trading_day"]["status"] == "known"
    assert body["trading_day"]["date"] == "2025-12-02"
    assert body["trading_day"]["phase"] == "open"
    evaluation = body["evaluation_sessions"]["alpha"]
    assert evaluation["status"] == "ready"
    assert evaluation["session_date"] == "2025-12-01"
    assert evaluation["reason"] is None
    assert [row["symbol"] for row in evaluation["symbols"]] == ["AAA", "BBB"]
    for row in evaluation["symbols"]:
        assert row["bars"] == "present"
        assert row["history"] == "sufficient"
        assert row["metadata"] == "ready"
        assert row["metadata_reason"] is None
    window = body["execution_windows"]["alpha"]
    assert window["status"] == "open"
    assert window["until"] == et(2025, 12, 2, 15, 45).isoformat()
    assert body["calendar_coverage"]["last_persisted_session"] == "2026-03-31"
    assert body["calendar_coverage"]["runway_low"] is False


def test_missing_metadata_symbol_is_not_ready_individually_while_session_stays_ready(
    state_db: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_calendar(date(2025, 11, 1), date(2026, 3, 31))
    days = sessions_between(date(2025, 11, 20), date(2025, 12, 1))
    seed_bars(["AAA", "CCC"], days)
    seed_bars(["BBB"], days, ready_metadata=False)  # bars present, no trading metadata
    strategy = FakeStrategy(["AAA", "BBB", "CCC"], warmup_periods=3, strategy_id="alpha")

    with _client(monkeypatch, OPEN_NOW, strategy) as client:
        body = _get(client).json()

    evaluation = body["evaluation_sessions"]["alpha"]
    assert evaluation["status"] == "ready"  # bar/history rule only
    rows = {row["symbol"]: row for row in evaluation["symbols"]}
    assert rows["BBB"]["metadata"] == "not_ready"
    assert rows["BBB"]["metadata_reason"] == "missing_metadata"
    assert rows["BBB"]["bars"] == "present" and rows["BBB"]["history"] == "sufficient"
    assert rows["AAA"]["metadata"] == "ready" and rows["CCC"]["metadata"] == "ready"


def test_calendar_state_is_unknown_when_calendar_ends_2026_03_13(
    state_db: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_calendar(date(2026, 1, 2), date(2026, 3, 13))
    seed_bars(["AAA"], [date(2026, 3, 13)])
    strategy = FakeStrategy(["AAA"], warmup_periods=1, strategy_id="alpha")

    with _client(monkeypatch, et(2026, 9, 29, 10, 0), strategy) as client:
        body = _get(client).json()

    unknown = {"status": "unknown", "reason": "calendar_data_unavailable"}
    assert body["trading_day"] == unknown
    assert body["evaluation_sessions"]["alpha"]["status"] == "unknown"
    assert body["evaluation_sessions"]["alpha"]["reason"] == "calendar_data_unavailable"
    assert body["evaluation_sessions"]["alpha"]["session_date"] is None
    assert body["execution_windows"]["alpha"] == unknown
    coverage = body["calendar_coverage"]
    assert coverage["last_persisted_session"] == "2026-03-13"
    assert coverage["runway_sessions"] == 0 and coverage["runway_low"] is True
    assert "2026-03-13" not in str(
        {key: body[key] for key in ("trading_day", "evaluation_sessions", "execution_windows")}
    )


def test_missing_bar_is_not_ready_missing_bars_with_the_symbol_listed(
    state_db: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_calendar(date(2025, 11, 1), date(2026, 3, 31))
    seed_bars(["AAA"], sessions_between(date(2025, 11, 20), date(2025, 12, 1)))
    seed_bars(["BBB"], sessions_between(date(2025, 11, 20), date(2025, 11, 28)))  # none on 12-01
    strategy = FakeStrategy(["AAA", "BBB"], warmup_periods=3, strategy_id="alpha")

    with _client(monkeypatch, OPEN_NOW, strategy) as client:
        body = _get(client).json()

    evaluation = body["evaluation_sessions"]["alpha"]
    assert evaluation["status"] == "not_ready"
    assert evaluation["reason"] == "missing_bars"
    assert evaluation["not_ready_symbols"] == ["BBB"]
    rows = {row["symbol"]: row for row in evaluation["symbols"]}
    assert rows["BBB"]["bars"] == "missing" and rows["AAA"]["bars"] == "present"


def test_strategy_filter_and_unknown_strategy_404(
    state_db: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_ready(["AAA"])
    alpha = FakeStrategy(["AAA"], 1, strategy_id="alpha")
    beta = FakeStrategy(["AAA"], 1, strategy_id="beta")

    with _client(monkeypatch, OPEN_NOW, alpha, beta) as client:
        both = _get(client).json()
        only = _get(client, "/api/v1/market-data/calendar-state?strategy_id=beta")
        missing = _get(client, "/api/v1/market-data/calendar-state?strategy_id=nope")

    assert sorted(both["evaluation_sessions"]) == ["alpha", "beta"]
    assert sorted(both["execution_windows"]) == ["alpha", "beta"]
    assert only.status_code == 200
    assert sorted(only.json()["evaluation_sessions"]) == ["beta"]
    assert missing.status_code == 404


def test_calendar_state_with_the_real_registry_covers_every_strategy(
    state_db: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_calendar(date(2026, 1, 2), date(2026, 3, 13))
    monkeypatch.setattr(market_data_route, "_now", lambda: et(2026, 9, 29, 10, 0))

    with TestClient(create_app()) as client:
        body = _get(client).json()

    assert sorted(body["evaluation_sessions"]) == [
        "donchian_breakout_daily",
        "rsi_mean_reversion_daily",
        "time_series_momentum_daily",
        "trend_following_daily",
    ]
    assert all(v["status"] == "unknown" for v in body["evaluation_sessions"].values())


def test_route_performs_no_write(state_db: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_ready(["AAA", "BBB"])
    strategy = FakeStrategy(["AAA", "BBB"], 3, strategy_id="alpha")
    writes: list[str] = []

    def _spy(conn: Any, cursor: Any, statement: str, *rest: Any) -> None:
        if statement.lstrip().split(None, 1)[0].upper() in {"INSERT", "UPDATE", "DELETE"}:
            writes.append(statement)

    def _counts() -> tuple[int, int, int]:
        with session_scope(state_db) as session:
            return tuple(  # type: ignore[return-value]
                int(session.scalar(select(func.count()).select_from(model)) or 0)
                for model in (MarketSession, DailyBar, Symbol)
            )

    before = _counts()
    with _client(monkeypatch, OPEN_NOW, strategy) as client:
        engine = get_engine()
        event.listen(engine, "before_cursor_execute", _spy)
        try:
            assert _get(client).status_code == 200
        finally:
            event.remove(engine, "before_cursor_execute", _spy)

    assert writes == []
    assert _counts() == before


def test_calendar_state_query_count_is_independent_of_symbol_count(
    state_db: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_calendar(date(2025, 11, 1), date(2026, 3, 31))
    days = sessions_between(date(2025, 11, 20), date(2025, 12, 1))
    ten = [f"S{i:02d}" for i in range(10)]
    twenty = [f"S{i:02d}" for i in range(20)]
    seed_bars(twenty, days)

    counts: list[int] = []
    for universe in (ten, twenty):
        strategy = FakeStrategy(universe, 3, strategy_id="alpha")
        with _client(monkeypatch, OPEN_NOW, strategy) as client:
            with count_queries(get_engine()) as counter:
                assert _get(client).status_code == 200
        counts.append(counter.count)

    assert counts[0] == counts[1]
    assert counts[0] <= 4  # window + missing bars + bar counts + symbol metadata


def test_query_count_depends_on_nothing_but_the_calendar_when_it_is_unknown(
    state_db: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    strategy = FakeStrategy([f"S{i:02d}" for i in range(20)], 3, strategy_id="alpha")
    with _client(monkeypatch, OPEN_NOW, strategy) as client:
        with count_queries(get_engine()) as counter:
            assert _get(client).status_code == 200
    assert counter.count == 1


def test_route_inventory_is_get_only() -> None:
    paths = create_app().openapi()["paths"]
    market = {path: set(ops) for path, ops in paths.items() if path.startswith("/api/v1/market-data")}
    assert market == {"/api/v1/market-data/calendar-state": {"get"}}
