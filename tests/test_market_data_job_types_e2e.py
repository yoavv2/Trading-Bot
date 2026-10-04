"""Phase 20 (Plan 21) production-path E2E for the three separate market-data
Job types (OPS-05): ``ingest-bars``, ``sync-symbol-metadata`` and
``sync-market-sessions``.

Every test drives the whole vertical slice through the **production** Job
registry: ``create_app()`` (lifespan builds ``build_default_registry``) ->
``POST /api/v1/jobs`` -> the real ``run-jobs --once`` CLI command -> the real
handler -> the real ``services.*`` function -> observable
``GET /api/v1/jobs/{id}``.

Polygon is faked only at the service seam (T-20-21-02): ``PolygonClient`` in
``services.ingestion`` and ``fetch_ticker_overview`` in
``services.symbol_metadata_sync`` are replaced with in-memory fakes, so no
network call is ever made and no real API key is needed. All three types
declare ``ExecutionMode.BACKTEST``, so no broker credentials are configured.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from tests.test_job_operations_e2e import (
    _counts,
    _run_worker_once,
    job_operations_env,
    migrated_backtest_db,
    strategy_config_override,
)

from trading_platform.api.app import create_app
from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.db.models import Job, MarketDataIngestionRun
from trading_platform.db.models.daily_bar import DailyBar as DailyBarModel
from trading_platform.db.models.market_session import MarketSession
from trading_platform.db.models.symbol import Symbol
from trading_platform.db.session import session_scope
from trading_platform.services import ingestion as ingestion_module
from trading_platform.services import symbol_metadata_sync as symbol_metadata_sync_module
from trading_platform.services.calendar import sessions_in_range
from trading_platform.services.data import DailyBar, DailyBarRequest
from trading_platform.services.polygon import PolygonAuthError

# Fixtures consumed by pytest name (market_jobs_env -> job_operations_env ->
# migrated_backtest_db/strategy_config_override); re-exported so ruff F401 passes.
__all__ = [
    "job_operations_env",
    "migrated_backtest_db",
    "strategy_config_override",
]

# The shared market-data fixture seeds bars through 2024-01-10, so this window
# only adds new sessions and never overwrites seeded AAPL bars.
INGEST_FROM = "2024-01-11"
INGEST_TO = "2024-01-12"
INGEST_SESSIONS = [date(2024, 1, 11), date(2024, 1, 12)]
INGEST_PAYLOAD: dict[str, Any] = {
    "from_date": INGEST_FROM,
    "to_date": INGEST_TO,
    "symbols": ["AAPL", "SPY"],
}

# Feb 2024 is not seeded by the shared fixture; Presidents Day (Feb 19) makes
# the session count differ from the weekday count.
SESSIONS_FROM = date(2024, 2, 1)
SESSIONS_TO = date(2024, 2, 29)
SESSIONS_PAYLOAD: dict[str, Any] = {
    "from_date": SESSIONS_FROM.isoformat(),
    "to_date": SESSIONS_TO.isoformat(),
}

METADATA_PAYLOAD: dict[str, Any] = {"symbols": ["QQQ", "SPY"]}

MARKET_DATA_TYPES = ["ingest-bars", "sync-symbol-metadata", "sync-market-sessions"]
VALID_PAYLOADS: dict[str, dict[str, Any]] = {
    "ingest-bars": INGEST_PAYLOAD,
    "sync-symbol-metadata": METADATA_PAYLOAD,
    "sync-market-sessions": SESSIONS_PAYLOAD,
}


class FakePolygonClient:
    """Stand-in for ``PolygonClient``: one deterministic bar per requested
    session, generated locally. Never touches the network."""

    def __init__(self, _settings: Any) -> None:
        pass

    def __enter__(self) -> FakePolygonClient:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def fetch_daily_bars(self, request: DailyBarRequest) -> list[DailyBar]:
        return [
            DailyBar(
                symbol=request.symbol,
                session_date=session_date,
                open=Decimal("100.000000"),
                high=Decimal("102.000000"),
                low=Decimal("99.000000"),
                close=Decimal("101.000000"),
                volume=1_000_000,
                adjusted=request.adjusted,
                provider=request.provider,
            )
            for session_date in INGEST_SESSIONS
            if request.from_date <= session_date <= request.to_date
        ]


def _failing_polygon_client(failing: set[str]) -> type[FakePolygonClient]:
    """Factory: a ``FakePolygonClient`` that raises ``PolygonAuthError`` for the
    given tickers and returns the normal fake bars for all others."""

    class _FailingPolygonClient(FakePolygonClient):
        def fetch_daily_bars(self, request: DailyBarRequest) -> list[DailyBar]:
            if request.symbol in failing:
                raise PolygonAuthError("simulated 401")
            return super().fetch_daily_bars(request)

    return _FailingPolygonClient


def _fake_overview(ticker: str, _settings: Any) -> dict[str, Any]:
    return {
        "name": f"{ticker} Fake Inc.",
        "market": "stocks",
        "locale": "us",
        "primary_exchange": "XNAS",
        "type": "CS",
        "active": True,
        "list_date": "2000-01-03",
    }


@pytest.fixture()
def market_jobs_env(job_operations_env: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """job_operations_env (mutations enabled, seeded sessions/bars, no broker
    credentials) plus the two Polygon seams faked."""

    monkeypatch.setattr(ingestion_module, "PolygonClient", FakePolygonClient)
    monkeypatch.setattr(symbol_metadata_sync_module, "fetch_ticker_overview", _fake_overview)
    # D-28: the metadata sync refuses to start without a configured key
    # (invalid_configuration); the fetch seam above is faked, so the value is
    # never used for a request.
    monkeypatch.setenv("TRADING_PLATFORM_MARKET_DATA__POLYGON__API_KEY", "test-key-not-real")
    clear_settings_cache()
    try:
        yield
    finally:
        clear_settings_cache()


def _submit(
    client: TestClient,
    key: str,
    job_type: str,
    payload: dict[str, Any],
):
    return client.post(
        "/api/v1/jobs",
        headers={"Idempotency-Key": key},
        json={"job_type": job_type, "payload": payload},
    )


def _submit_and_run(
    client: TestClient, key: str, job_type: str, payload: dict[str, Any]
) -> dict[str, Any]:
    submitted = _submit(client, key, job_type, payload)
    assert submitted.status_code == 202, submitted.text
    job_id = submitted.json()["job_id"]
    _run_worker_once()
    detail = client.get(f"/api/v1/jobs/{job_id}")
    assert detail.status_code == 200
    return detail.json()


# --- Task 1: ingest-bars ----------------------------------------------------


def test_ingest_bars_job_links_ingestion_run(market_jobs_env: None) -> None:
    """SC1/OPS-05 + D-07/D-08/D-09: the Job links exactly one
    market_data_ingestion_run whose id is result_summary.run_id and whose row
    carries the Job id."""

    with TestClient(create_app()) as client:
        detail = _submit_and_run(client, "e2e-ingest-happy", "ingest-bars", INGEST_PAYLOAD)

    assert detail["status"] == "succeeded", detail["failure_message"]
    assert detail["failure_reason"] is None
    assert detail["payload"] == INGEST_PAYLOAD

    resources = detail["resources"]
    assert len(resources) == 1
    resource = resources[0]
    assert resource["kind"] == "market_data_ingestion_run"
    assert resource["links"] == {}
    assert resource["id"] == detail["result_summary"]["run_id"]
    assert detail["result_summary"]["produced_run_ids"] == [resource["id"]]
    assert detail["result_summary"]["ingestion_succeeded"] is True
    assert detail["result_summary"]["bars_upserted"] == 4
    assert detail["result_summary"]["symbols_failed"] == []
    assert detail["outcome"] == "complete"

    with session_scope(load_settings()) as session:
        runs = session.execute(select(MarketDataIngestionRun)).scalars().all()
        spy_bars = session.scalar(
            select(func.count())
            .select_from(DailyBarModel)
            .join(Symbol, Symbol.id == DailyBarModel.symbol_id)
            .where(Symbol.ticker == "SPY")
        )

    assert len(runs) == 1
    run = runs[0]
    assert str(run.id) == resource["id"]
    assert resource["status"] == run.status
    assert run.job_id == uuid.UUID(detail["id"])
    assert run.trigger_source == "job"
    assert spy_bars == 2


def test_ingest_bars_all_symbols_failed_fails_job(
    market_jobs_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-08a / UAT gap 1: every symbol failing lands the run FAILED and the Job
    FAILED/handler_error (retryable), with the run still linked."""

    monkeypatch.setattr(ingestion_module, "PolygonClient", _failing_polygon_client({"AAPL", "SPY"}))
    expected = (
        "0 of 2 symbols succeeded; failed: AAPL (PolygonAuthError), SPY (PolygonAuthError)"
    )

    with TestClient(create_app()) as client:
        detail = _submit_and_run(client, "e2e-ingest-allfail", "ingest-bars", INGEST_PAYLOAD)

        assert detail["status"] == "failed"
        # COR-03: an all-symbols-failed Job reads outcome `failed`.
        assert detail["outcome"] == "failed"
        assert detail["failure_reason"] == "handler_error"
        assert detail["outcome_uncertain"] is False
        assert len(detail["resources"]) == 1
        resource = detail["resources"][0]
        assert resource["kind"] == "market_data_ingestion_run"
        assert resource["status"] == "failed"
        assert detail["failure_message"] == (
            "IngestionAllSymbolsFailedError: "
            f"Ingestion run {resource['id']} failed: {expected}"
        )

        with session_scope(load_settings()) as session:
            runs = session.execute(select(MarketDataIngestionRun)).scalars().all()
        assert len(runs) == 1
        assert runs[0].job_id == uuid.UUID(detail["id"])
        assert runs[0].status == "failed"
        assert runs[0].error_message == expected

        retried = client.post(
            f"/api/v1/jobs/{detail['id']}/retry",
            headers={"Idempotency-Key": "e2e-ingest-allfail-retry"},
        )
        assert retried.status_code == 202, retried.text


def test_ingest_bars_one_ok_one_fail_stays_succeeded(
    market_jobs_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-08a: at least one ok and at least one failed stays a SUCCEEDED Job with
    a PARTIAL run."""

    monkeypatch.setattr(ingestion_module, "PolygonClient", _failing_polygon_client({"SPY"}))

    with TestClient(create_app()) as client:
        detail = _submit_and_run(client, "e2e-ingest-partial", "ingest-bars", INGEST_PAYLOAD)

    assert detail["status"] == "succeeded", detail["failure_message"]
    assert detail["failure_reason"] is None
    # COR-03: a partial run is never read as plain success.
    assert detail["outcome"] == "partial"
    assert detail["result_summary"]["outcome"] == "partial"
    assert detail["resources"][0]["status"] == "partial"
    assert detail["result_summary"]["symbols_failed"] == ["SPY"]
    assert detail["result_summary"]["ingestion_succeeded"] is False


def test_ingest_bars_symbols_normalized_and_fingerprint_stable(market_jobs_env: None) -> None:
    """D-24/D-25/T-20-21-01: symbols are trimmed, upper-cased, de-duplicated
    and sorted before persistence, and a differently-ordered resubmission with
    the same Idempotency-Key replays because the normalized fingerprint is
    identical."""

    with TestClient(create_app()) as client:
        first = _submit(
            client,
            "e2e-ingest-normalize",
            "ingest-bars",
            {**INGEST_PAYLOAD, "symbols": [" spy", "aapl", "SPY"]},
        )
        assert first.status_code == 202, first.text
        job_id = first.json()["job_id"]

        replay = _submit(
            client,
            "e2e-ingest-normalize",
            "ingest-bars",
            {**INGEST_PAYLOAD, "symbols": ["SPY", "AAPL"]},
        )
        detail = client.get(f"/api/v1/jobs/{job_id}").json()

    assert replay.status_code == 200, replay.text
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert replay.json()["job_id"] == job_id
    assert detail["payload"]["symbols"] == ["AAPL", "SPY"]

    with session_scope(load_settings()) as session:
        persisted: dict[str, Any] = session.execute(
            select(Job.payload).where(Job.id == uuid.UUID(job_id))
        ).scalar_one()
        job_count = session.scalar(
            select(func.count()).select_from(Job).where(Job.job_type == "ingest-bars")
        )

    assert persisted["symbols"] == ["AAPL", "SPY"]
    assert job_count == 1


def test_catalog_lists_three_separate_market_data_types(market_jobs_env: None) -> None:
    """OPS-05: the three types are separate catalog entries, each
    step_boundary cancellable."""

    with TestClient(create_app()) as client:
        response = client.get("/api/v1/job-types")

    assert response.status_code == 200
    items = {item["job_type"]: item for item in response.json()["items"]}
    for job_type in MARKET_DATA_TYPES:
        assert job_type in items
        assert items[job_type]["cancellation_mode"] == "step_boundary"
        assert items[job_type]["description"]
    assert len({items[job_type]["description"] for job_type in MARKET_DATA_TYPES}) == 3


# --- Task 2: sync-symbol-metadata, sync-market-sessions, strict payloads ----


def test_sync_symbol_metadata_job_upserts_symbols(market_jobs_env: None) -> None:
    """SC1/OPS-05: symbols rows are upserted, resources[] is empty and
    synced_count equals the number of requested symbols."""

    with TestClient(create_app()) as client:
        detail = _submit_and_run(
            client, "e2e-metadata-happy", "sync-symbol-metadata", METADATA_PAYLOAD
        )

    assert detail["status"] == "succeeded", detail["failure_message"]
    assert detail["resources"] == []
    summary = detail["result_summary"]
    assert summary["synced_count"] == len(METADATA_PAYLOAD["symbols"])
    assert summary["synced"] == ["QQQ", "SPY"]
    assert summary["skipped_count"] == 0
    assert summary["failed_count"] == 0

    with session_scope(load_settings()) as session:
        rows = {
            row.ticker: row
            for row in session.execute(
                select(Symbol).where(Symbol.ticker.in_(["QQQ", "SPY"]))
            ).scalars()
        }

    assert set(rows) == {"QQQ", "SPY"}
    assert rows["QQQ"].name == "QQQ Fake Inc."
    assert rows["SPY"].primary_exchange == "XNAS"
    assert rows["SPY"].metadata_provider == "polygon"


def test_sync_symbol_metadata_one_unknown_ticker_is_succeeded_partial(
    market_jobs_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-28 (replaces the old fail-on-any-failed-ticker test): one unknown
    ticker is the per-symbol reason `not_found`; the other symbol is synced,
    the Job SUCCEEDED and the read outcome is `partial`."""

    def _fetch_unknown_spy(ticker: str, settings: Any) -> dict[str, Any] | None:
        if ticker == "SPY":
            return None
        return _fake_overview(ticker, settings)

    monkeypatch.setattr(symbol_metadata_sync_module, "fetch_ticker_overview", _fetch_unknown_spy)

    with TestClient(create_app()) as client:
        detail = _submit_and_run(
            client, "e2e-metadata-partial", "sync-symbol-metadata", METADATA_PAYLOAD
        )

    assert detail["status"] == "succeeded", detail["failure_message"]
    assert detail["outcome"] == "partial"
    summary = detail["result_summary"]
    assert summary["outcome"] == "partial"
    assert summary["synced"] == ["QQQ"]
    assert summary["failed"] == ["SPY"]
    assert summary["failures"] == [{"symbol": "SPY", "reason": "not_found"}]
    assert summary["operation_failure"] is None
    assert detail["resources"] == []

    with session_scope(load_settings()) as session:
        synced = {
            row.ticker
            for row in session.execute(
                select(Symbol).where(Symbol.metadata_provider == "polygon")
            ).scalars()
        }
    assert synced == {"QQQ"}


def test_sync_symbol_metadata_auth_failure_fails_job_and_marks_nothing(
    market_jobs_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-28: an auth failure is operation-level: Job FAILED, read outcome
    `failed`, and no Symbol row is marked with a metadata provider."""

    def _fetch_auth_on_spy(ticker: str, settings: Any) -> dict[str, Any] | None:
        if ticker == "SPY":
            raise PolygonAuthError("simulated 401")
        return _fake_overview(ticker, settings)

    monkeypatch.setattr(symbol_metadata_sync_module, "fetch_ticker_overview", _fetch_auth_on_spy)

    with TestClient(create_app()) as client:
        detail = _submit_and_run(
            client, "e2e-metadata-auth", "sync-symbol-metadata", METADATA_PAYLOAD
        )

    assert detail["status"] == "failed"
    assert detail["failure_reason"] == "handler_error"
    assert "provider_auth" in detail["failure_message"]
    assert detail["outcome"] == "failed"
    assert detail["resources"] == []

    with session_scope(load_settings()) as session:
        marked = session.execute(
            select(Symbol).where(Symbol.metadata_provider.is_not(None))
        ).scalars().all()
    assert marked == []


def test_sync_symbol_metadata_all_tickers_failing_fails_job(
    market_jobs_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-28: when no ticker can be synced the Job lands FAILED (outcome
    `failed`), naming the failed tickers."""

    monkeypatch.setattr(
        symbol_metadata_sync_module, "fetch_ticker_overview", lambda ticker, settings: None
    )

    with TestClient(create_app()) as client:
        detail = _submit_and_run(
            client, "e2e-metadata-allfail", "sync-symbol-metadata", METADATA_PAYLOAD
        )

    assert detail["status"] == "failed"
    assert detail["failure_reason"] == "handler_error"
    assert "QQQ" in detail["failure_message"] and "SPY" in detail["failure_message"]
    assert detail["outcome"] == "failed"


def test_sync_market_sessions_job_upserts_sessions(market_jobs_env: None) -> None:
    """SC1/OPS-05: market_sessions rows exist for the range and
    sessions_upserted equals the number of exchange sessions in it."""

    exchange = load_settings().market_data.calendar.exchange
    expected = sessions_in_range(SESSIONS_FROM, SESSIONS_TO, exchange)
    # 21 weekdays in Feb 2024 minus Presidents Day; guards against a vacuous range.
    assert len(expected) == 20

    with session_scope(load_settings()) as session:
        before = session.scalar(
            select(func.count())
            .select_from(MarketSession)
            .where(MarketSession.session_date.between(SESSIONS_FROM, SESSIONS_TO))
        )
    assert before == 0

    with TestClient(create_app()) as client:
        detail = _submit_and_run(
            client, "e2e-sessions-happy", "sync-market-sessions", SESSIONS_PAYLOAD
        )

    assert detail["status"] == "succeeded", detail["failure_message"]
    assert detail["resources"] == []
    assert detail["result_summary"]["sessions_upserted"] == len(expected)
    assert detail["result_summary"]["exchange"] == exchange

    with session_scope(load_settings()) as session:
        persisted = (
            session.execute(
                select(MarketSession.session_date)
                .where(MarketSession.exchange == exchange)
                .where(MarketSession.session_date.between(SESSIONS_FROM, SESSIONS_TO))
                .order_by(MarketSession.session_date)
            )
            .scalars()
            .all()
        )

    assert persisted == expected


@pytest.mark.parametrize("job_type", MARKET_DATA_TYPES)
def test_unknown_payload_key_rejected_for_each_market_data_type(
    market_jobs_env: None, job_type: str
) -> None:
    """D-25: an unknown key (e.g. a mode/behavior flag) is a typed 422 with
    zero rows written."""

    before = _counts()
    with TestClient(create_app()) as client:
        response = _submit(
            client,
            f"e2e-unknown-key-{job_type}",
            job_type,
            {**VALID_PAYLOADS[job_type], "dry_run": True},
        )
    after = _counts()

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "code": "invalid_job_payload",
        "job_type": job_type,
        "reason": "unknown_payload_keys",
    }
    assert after == before
