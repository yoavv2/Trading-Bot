"""Shared provider request budget: atomic admission across threads and OS processes,
survival across restarts, attempt accounting through the adapter, ingestion and name
enrichment, closed exhaustion codes, and the entitlement limits."""

from __future__ import annotations

import multiprocessing
import os
import threading
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
import sqlalchemy as sa
from tests.support.budget_worker import admit_many
from tests.support.migrated_db import migrated_database

from trading_platform.core.settings import (
    TiingoProviderSettings,
    clear_settings_cache,
    load_settings,
)
from trading_platform.db.models.research import AssetCatalogEntry, ProviderRequestLedgerEntry
from trading_platform.db.session import session_scope
from trading_platform.services.calendar import upsert_market_sessions
from trading_platform.services.research import catalog as catalog_module
from trading_platform.services.research.budget import (
    BudgetExhaustedError,
    BudgetLimits,
    DatabaseRequestBudget,
)
from trading_platform.services.research.tiingo_ingestion import ingest_tiingo_daily_bars
from trading_platform.services.tiingo import (
    TiingoClient,
    TiingoRateLimitError,
    TiingoRequestBudgetExceededError,
)


@pytest.fixture()
def budget_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    with migrated_database(monkeypatch, "research_budget") as name:
        clear_settings_cache()
        yield name


def _ledger_rows() -> list[ProviderRequestLedgerEntry]:
    with session_scope(load_settings()) as session:
        rows = list(session.execute(sa.select(ProviderRequestLedgerEntry).order_by(ProviderRequestLedgerEntry.attempted_at)).scalars())
        session.expunge_all()
        return rows


def _budget(limit: int = 5, **kwargs) -> DatabaseRequestBudget:
    return DatabaseRequestBudget(
        load_settings(),
        provider="tiingo",
        limits=BudgetLimits(requests_per_hour=limit, requests_per_day=10_000, unique_symbols_per_month=10_000),
        **kwargs,
    )


def test_defaults_are_the_verified_free_plan_entitlement_and_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_settings_cache()
    tiingo = load_settings().research.tiingo
    assert (tiingo.requests_per_hour, tiingo.requests_per_day, tiingo.unique_symbols_per_month) == (50, 1000, 500)
    monkeypatch.setenv("TRADING_PLATFORM_RESEARCH__TIINGO__REQUESTS_PER_HOUR", "20")
    clear_settings_cache()
    budget = DatabaseRequestBudget(load_settings(), provider="tiingo")
    assert budget.limits.requests_per_hour == 20


def test_concurrent_threads_cannot_exceed_the_hourly_limit(budget_db: str) -> None:
    budget = _budget(limit=5)
    results: list[object] = []
    barrier = threading.Barrier(12)

    def attempt() -> None:
        barrier.wait()
        try:
            results.append(budget.admit(symbol="AAPL", purpose="prices"))
        except BudgetExhaustedError as exc:
            results.append(exc)

    threads = [threading.Thread(target=attempt) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    admitted = [r for r in results if not isinstance(r, Exception)]
    refused = [r for r in results if isinstance(r, BudgetExhaustedError)]
    assert (len(admitted), len(refused)) == (5, 7)
    assert all(r.code == "provider_budget_exhausted" and r.limit_name == "requests_per_hour" for r in refused)
    assert len(_ledger_rows()) == 5


def test_separate_processes_share_one_budget(budget_db: str) -> None:
    """Two spawned OS processes, each with its own engine and budget instance, jointly
    admit exactly the limit."""

    context = multiprocessing.get_context("spawn")
    with context.Pool(processes=2) as pool:
        outcomes = pool.starmap(admit_many, [(budget_db, 6, 8, "a"), (budget_db, 6, 8, "b")])
    assert {o["pid"] for o in outcomes} != {os.getpid()} and len({o["pid"] for o in outcomes}) == 2
    assert sum(o["admitted"] for o in outcomes) == 8
    assert sum(o["refused"] for o in outcomes) == 4
    rows = _ledger_rows()
    assert len(rows) == 8 and {r.process_id.split("-")[1] for r in rows} <= {"a", "b"}


def test_a_restarted_process_sees_the_attempts_of_the_one_it_replaced(budget_db: str) -> None:
    first = _budget(limit=3, process_id="first-process")
    for _ in range(3):
        first.admit(symbol="MSFT", purpose="metadata")
    restarted = _budget(limit=3, process_id="second-process")
    with pytest.raises(BudgetExhaustedError) as info:
        restarted.admit(symbol="MSFT", purpose="metadata")
    assert info.value.used == 3
    # The window slides: an hour later the restarted process is admitted.
    later = _budget(limit=3, process_id="second-process", clock=lambda: datetime.now(UTC) + timedelta(hours=1, seconds=1))
    assert later.admit(symbol="MSFT", purpose="metadata").entry_id is not None
    assert later.usage()["hour"] == 1


def test_daily_and_monthly_symbol_limits(budget_db: str) -> None:
    budget = DatabaseRequestBudget(
        load_settings(),
        provider="tiingo",
        limits=BudgetLimits(requests_per_hour=100, requests_per_day=100, unique_symbols_per_month=2),
    )
    budget.admit(symbol="AAA", purpose="prices")
    budget.admit(symbol="BBB", purpose="prices")
    budget.admit(symbol="aaa", purpose="metadata")  # a known symbol is still admitted
    with pytest.raises(BudgetExhaustedError) as info:
        budget.admit(symbol="CCC", purpose="prices")
    assert info.value.limit_name == "unique_symbols_per_month"
    assert budget.admit(symbol=None, purpose="catalog").symbol is None  # symbol-less requests only count against hour/day
    daily = DatabaseRequestBudget(
        load_settings(), provider="tiingo", limits=BudgetLimits(requests_per_hour=100, requests_per_day=4, unique_symbols_per_month=100)
    )
    with pytest.raises(BudgetExhaustedError) as info:
        daily.admit(symbol="AAA", purpose="prices")
    assert info.value.limit_name == "requests_per_day"


def _mock_transport(prices_status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/prices"):
            if prices_status != 200:
                return httpx.Response(prices_status, json={"detail": "x"})
            return httpx.Response(200, json=[{"date": "2024-03-04T00:00:00.000Z", "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 100, "adjOpen": 10, "adjHigh": 11, "adjLow": 9, "adjClose": 10.5, "adjVolume": 100, "divCash": 0, "splitFactor": 1}])
        return httpx.Response(200, json={"ticker": request.url.path.rsplit("/", 1)[-1], "name": "Name", "exchangeCode": "NASDAQ", "startDate": "1990-01-02", "endDate": "2026-10-06"})

    return httpx.MockTransport(handler)


def test_ingestion_charges_every_attempt_and_stops_at_the_shared_limit(budget_db: str) -> None:
    settings = load_settings()
    with session_scope(settings) as session:
        upsert_market_sessions(session, date(2024, 3, 4), date(2024, 3, 4))
    budget = _budget(limit=5, process_id="ingest-worker")
    client = TiingoClient(TiingoProviderSettings(api_key="k", max_retries=0), budget=budget, transport=_mock_transport())
    result = ingest_tiingo_daily_bars(assets=["AAA", "BBB"], from_date=date(2024, 3, 4), to_date=date(2024, 3, 4), settings=settings, client=client)
    assert result.run_status == "succeeded"
    rows = _ledger_rows()
    assert [(r.symbol, r.purpose, r.outcome, r.status_code) for r in rows] == [
        ("AAA", "metadata", "ok", 200), ("AAA", "prices", "ok", 200), ("BBB", "metadata", "ok", 200), ("BBB", "prices", "ok", 200)
    ]
    # One attempt left: the third asset's metadata is admitted, its prices call is refused,
    # the run aborts with the closed code and no further asset is attempted.
    client2 = TiingoClient(TiingoProviderSettings(api_key="k", max_retries=0), budget=budget, transport=_mock_transport())
    with pytest.raises(TiingoRequestBudgetExceededError):
        ingest_tiingo_daily_bars(assets=["CCC", "DDD"], from_date=date(2024, 3, 4), to_date=date(2024, 3, 4), settings=settings, client=client2)
    assert len(_ledger_rows()) == 5
    with session_scope(settings) as session:
        from trading_platform.db.models.market_data_ingestion_run import MarketDataIngestionRun

        runs = list(session.execute(sa.select(MarketDataIngestionRun).order_by(MarketDataIngestionRun.started_at)).scalars())
        assert runs[-1].status == "failed" and runs[-1].error_message == "provider_budget_exhausted"


def test_rate_limit_is_recorded_on_the_ledger_and_not_retried(budget_db: str) -> None:
    settings = load_settings()
    budget = _budget(limit=50)
    client = TiingoClient(TiingoProviderSettings(api_key="k", max_retries=3), budget=budget, transport=_mock_transport(prices_status=429))
    with pytest.raises(TiingoRateLimitError):
        ingest_tiingo_daily_bars(assets=["AAA"], from_date=date(2024, 3, 4), to_date=date(2024, 3, 4), settings=settings, client=client)
    rows = _ledger_rows()
    assert [(r.purpose, r.outcome, r.status_code) for r in rows] == [("metadata", "ok", 200), ("prices", "rate_limited", 429)]


def test_name_enrichment_goes_through_the_same_budget(budget_db: str) -> None:
    settings = load_settings()
    with session_scope(settings) as session:
        session.add(AssetCatalogEntry(id=uuid.uuid4(), provider="tiingo", ticker="NONAME", exchange="NASDAQ", asset_type="Stock", currency="USD", synced_at=datetime.now(UTC)))
        session.flush()
        budget = _budget(limit=1)
        client = TiingoClient(TiingoProviderSettings(api_key="k", max_retries=0), budget=budget, transport=_mock_transport())
        enriched = catalog_module.enrich_asset_name(session, client, "NONAME")
        assert enriched is not None and enriched.name == "Name"
        session.add(AssetCatalogEntry(id=uuid.uuid4(), provider="tiingo", ticker="OTHER", exchange="NASDAQ", asset_type="Stock", currency="USD", synced_at=datetime.now(UTC)))
        session.flush()
        with pytest.raises(BudgetExhaustedError):
            catalog_module.enrich_asset_name(session, client, "OTHER")
    assert [(r.symbol, r.purpose) for r in _ledger_rows()] == [("NONAME", "metadata")]
