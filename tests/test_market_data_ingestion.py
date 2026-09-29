"""Tests for Polygon client, normalization, and idempotent ingestion pipeline."""

from __future__ import annotations

import dataclasses
import json
import os
import sys
import typing
import uuid
from collections.abc import Iterator
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trading_platform.core.settings import (
    IngestSettings,
    MarketDataSettings,
    PolygonProviderSettings,
    clear_settings_cache,
)
from trading_platform.services.data import DailyBar, DailyBarRequest
from trading_platform.services.polygon import (
    PolygonAuthError,
    PolygonClient,
    _build_session_date,
    _normalize_timestamp,
    _result_to_bar,
)

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "polygon_daily_bars.json"


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _load_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text())


def _make_polygon_settings(api_key: str = "test-key") -> PolygonProviderSettings:
    return PolygonProviderSettings(
        base_url="https://api.polygon.io",
        api_key=api_key,
        adjusted=True,
        max_retries=0,
        retry_backoff_factor=0.0,
        timeout_seconds=5.0,
    )


def _make_market_data_settings(api_key: str = "test-key") -> MarketDataSettings:
    return MarketDataSettings(
        polygon=_make_polygon_settings(api_key=api_key),
        ingest=IngestSettings(
            default_lookback_days=10,
            universe=("AAPL", "MSFT"),
        ),
    )


# ---------------------------------------------------------------------------
# Unit tests: normalization helpers
# ---------------------------------------------------------------------------


class TestNormalizationHelpers:
    def test_normalize_timestamp_converts_ms_to_utc_datetime(self) -> None:
        ts_ms = 1704067200000  # 2024-01-01 00:00:00 UTC
        result = _normalize_timestamp(ts_ms)
        assert result is not None
        assert result.tzinfo is not None
        assert result == datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

    def test_normalize_timestamp_none_input(self) -> None:
        assert _normalize_timestamp(None) is None

    def test_build_session_date_produces_date(self) -> None:
        ts_ms = 1704067200000  # 2024-01-01 00:00:00 UTC
        session = _build_session_date(ts_ms, adjusted=True)
        assert session == date(2024, 1, 1)

    def test_result_to_bar_normalizes_full_result(self) -> None:
        result = {
            "v": 70790813.0,
            "vw": 182.9018,
            "o": 182.09,
            "c": 184.37,
            "h": 184.55,
            "l": 181.22,
            "t": 1704067200000,
            "n": 594632,
        }
        bar = _result_to_bar(result, "AAPL", adjusted=True)

        assert bar.symbol == "AAPL"
        assert bar.session_date == date(2024, 1, 1)
        assert bar.open == Decimal("182.09")
        assert bar.high == Decimal("184.55")
        assert bar.low == Decimal("181.22")
        assert bar.close == Decimal("184.37")
        assert bar.volume == 70790813
        assert bar.vwap == Decimal("182.9018")
        assert bar.trade_count == 594632
        assert bar.adjusted is True
        assert bar.provider == "polygon"
        assert bar.provider_timestamp is not None

    def test_result_to_bar_handles_missing_vwap_and_trade_count(self) -> None:
        result = {
            "v": 1000000.0,
            "o": 100.0,
            "c": 101.0,
            "h": 102.0,
            "l": 99.0,
            "t": 1704067200000,
        }
        bar = _result_to_bar(result, "SPY", adjusted=False)
        assert bar.vwap is None
        assert bar.trade_count is None
        assert bar.adjusted is False


# ---------------------------------------------------------------------------
# Unit tests: PolygonClient
# ---------------------------------------------------------------------------


class TestPolygonClientAuth:
    def test_raises_auth_error_when_api_key_is_empty(self) -> None:
        settings = _make_polygon_settings(api_key="")
        with pytest.raises(PolygonAuthError, match="API key"):
            PolygonClient(settings)

    def test_raises_auth_error_on_401_response(self) -> None:
        settings = _make_polygon_settings()
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.raise_for_status.return_value = None

        with patch("httpx.Client.get", return_value=mock_response):
            client = PolygonClient(settings)
            with pytest.raises(PolygonAuthError, match="401"):
                client.fetch_daily_bars(
                    DailyBarRequest(
                        symbol="AAPL",
                        from_date=date(2024, 1, 1),
                        to_date=date(2024, 1, 5),
                    )
                )

    def test_raises_auth_error_on_403_response(self) -> None:
        settings = _make_polygon_settings()
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.raise_for_status.return_value = None

        with patch("httpx.Client.get", return_value=mock_response):
            client = PolygonClient(settings)
            with pytest.raises(PolygonAuthError, match="403"):
                client.fetch_daily_bars(
                    DailyBarRequest(
                        symbol="AAPL",
                        from_date=date(2024, 1, 1),
                        to_date=date(2024, 1, 5),
                    )
                )


class TestPolygonClientFetch:
    def _make_response(self, payload: dict) -> MagicMock:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = payload
        return mock_response

    def test_fetch_returns_normalized_bars_from_fixture(self) -> None:
        fixture = _load_fixture()
        settings = _make_polygon_settings()

        with patch("httpx.Client.get", return_value=self._make_response(fixture)):
            client = PolygonClient(settings)
            bars = client.fetch_daily_bars(
                DailyBarRequest(
                    symbol="AAPL",
                    from_date=date(2024, 1, 1),
                    to_date=date(2024, 1, 3),
                )
            )

        assert len(bars) == 3
        assert all(isinstance(b, DailyBar) for b in bars)
        assert bars[0].symbol == "AAPL"
        assert bars[0].session_date == date(2024, 1, 1)

    def test_fetch_handles_pagination(self) -> None:
        """Client must follow next_url to collect all pages."""
        page1 = {
            "status": "OK",
            "results": [
                {
                    "v": 1000.0,
                    "o": 100.0,
                    "c": 101.0,
                    "h": 102.0,
                    "l": 99.0,
                    "t": 1704067200000,
                }
            ],
            "next_url": "https://api.polygon.io/v2/aggs/ticker/SPY/range/1/day/2024-01-01/2024-01-02?cursor=abc",
        }
        page2 = {
            "status": "OK",
            "results": [
                {
                    "v": 2000.0,
                    "o": 200.0,
                    "c": 201.0,
                    "h": 202.0,
                    "l": 199.0,
                    "t": 1704153600000,
                }
            ],
        }
        responses = [self._make_response(page1), self._make_response(page2)]
        call_count = 0

        def mock_get(url, **kwargs):
            nonlocal call_count
            result = responses[call_count]
            call_count += 1
            return result

        settings = _make_polygon_settings()
        with patch("httpx.Client.get", side_effect=mock_get):
            client = PolygonClient(settings)
            bars = client.fetch_daily_bars(
                DailyBarRequest(
                    symbol="SPY",
                    from_date=date(2024, 1, 1),
                    to_date=date(2024, 1, 2),
                )
            )

        assert len(bars) == 2
        assert call_count == 2

    def test_fetch_returns_empty_list_when_no_results(self) -> None:
        payload = {"status": "OK", "results": [], "resultsCount": 0}
        settings = _make_polygon_settings()

        with patch("httpx.Client.get", return_value=self._make_response(payload)):
            client = PolygonClient(settings)
            bars = client.fetch_daily_bars(
                DailyBarRequest(
                    symbol="AAPL",
                    from_date=date(2024, 1, 1),
                    to_date=date(2024, 1, 5),
                )
            )

        assert bars == []

    def test_fetch_returns_empty_list_when_results_key_missing(self) -> None:
        payload = {"status": "OK"}
        settings = _make_polygon_settings()

        with patch("httpx.Client.get", return_value=self._make_response(payload)):
            client = PolygonClient(settings)
            bars = client.fetch_daily_bars(
                DailyBarRequest(
                    symbol="AAPL",
                    from_date=date(2024, 1, 1),
                    to_date=date(2024, 1, 5),
                )
            )

        assert bars == []


# ---------------------------------------------------------------------------
# Integration-level tests: ingestion pipeline (requires Postgres)
# ---------------------------------------------------------------------------

import psycopg  # noqa: E402
from alembic import command  # noqa: E402
from scripts.migrate import build_alembic_config  # noqa: E402

from trading_platform.db.models import DailyBar as DailyBarModel  # noqa: E402
from trading_platform.db.models import MarketDataIngestionRun  # noqa: E402
from trading_platform.db.session import clear_engine_cache, session_scope  # noqa: E402
from trading_platform.services.data import (  # noqa: E402
    IngestionAllSymbolsFailedError,
    IngestionResult,
    IngestionRunStatus,
)
from trading_platform.services.ingestion import (  # noqa: E402
    _derive_run_status,
    ingest_daily_bars,
    upsert_daily_bars,
    upsert_symbol,
)
from trading_platform.services.polygon import PolygonClientError  # noqa: E402


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
def migrated_ingest_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Provision a temporary PostgreSQL database with Phase 2 migrations applied."""
    database_name = f"ingest_test_{uuid.uuid4().hex[:8]}"
    admin_params = _admin_connection_settings()

    try:
        with _connect_admin(admin_params) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f'CREATE DATABASE "{database_name}"')
    except psycopg.Error as exc:
        pytest.fail(
            "PostgreSQL is required for ingestion integration tests. "
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


def _fixture_bars(symbol: str = "AAPL", adjusted: bool = True) -> list[DailyBar]:
    """Return a list of normalized DailyBar objects from the fixture file."""
    fixture = _load_fixture()
    from trading_platform.services.polygon import _result_to_bar as r2b
    return [r2b(r, symbol, adjusted) for r in fixture["results"]]


class TestIngestionPipeline:
    def _polygon_response(self) -> MagicMock:
        fixture = _load_fixture()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = fixture
        return mock_response

    def test_upsert_symbol_creates_new_record(self, migrated_ingest_db: str) -> None:
        from trading_platform.core.settings import load_settings

        settings = load_settings()
        with session_scope(settings) as session:
            symbol = upsert_symbol(session, "AAPL")

        assert symbol.ticker == "AAPL"
        assert symbol.id is not None

    def test_upsert_symbol_is_idempotent(self, migrated_ingest_db: str) -> None:
        from trading_platform.core.settings import load_settings

        settings = load_settings()
        with session_scope(settings) as session:
            first = upsert_symbol(session, "SPY")
            second = upsert_symbol(session, "SPY")

        assert first.id == second.id

    def test_upsert_daily_bars_persists_rows(self, migrated_ingest_db: str) -> None:
        from sqlalchemy import select

        from trading_platform.core.settings import load_settings

        settings = load_settings()
        bars = _fixture_bars("AAPL")

        with session_scope(settings) as session:
            symbol = upsert_symbol(session, "AAPL")
            count = upsert_daily_bars(session, bars, symbol.id)

        assert count == len(bars)

        with session_scope(settings) as session:
            persisted = session.execute(select(DailyBarModel)).scalars().all()

        assert len(persisted) == len(bars)

    def test_upsert_daily_bars_is_idempotent(self, migrated_ingest_db: str) -> None:
        """Re-running with the same bars must not create duplicate rows."""
        from sqlalchemy import select

        from trading_platform.core.settings import load_settings

        settings = load_settings()
        bars = _fixture_bars("AAPL")

        with session_scope(settings) as session:
            symbol = upsert_symbol(session, "AAPL")
            upsert_daily_bars(session, bars, symbol.id)

        with session_scope(settings) as session:
            symbol = upsert_symbol(session, "AAPL")
            upsert_daily_bars(session, bars, symbol.id)

        with session_scope(settings) as session:
            persisted = session.execute(select(DailyBarModel)).scalars().all()

        assert len(persisted) == len(bars), "Duplicate bars created by repeated upsert"

    def test_ingest_daily_bars_records_run_and_bars(self, migrated_ingest_db: str) -> None:
        from sqlalchemy import select

        from trading_platform.core.settings import load_settings

        settings = load_settings()
        md_settings = _make_market_data_settings()

        with patch("httpx.Client.get", return_value=self._polygon_response()):
            result = ingest_daily_bars(
                from_date=date(2024, 1, 1),
                to_date=date(2024, 1, 3),
                symbols=["AAPL"],
                settings=md_settings,
                trigger_source="test",
                db_settings=settings,
            )

        assert result.succeeded
        assert result.bars_upserted == 3
        assert result.failed_count == 0

        with session_scope(settings) as session:
            runs = session.execute(select(MarketDataIngestionRun)).scalars().all()
            bars = session.execute(select(DailyBarModel)).scalars().all()

        assert len(runs) == 1
        assert runs[0].status == "succeeded"
        assert result.run_status == "succeeded" == runs[0].status
        assert len(bars) == 3

    def test_ingest_daily_bars_idempotent_repeat(self, migrated_ingest_db: str) -> None:
        """Repeating the exact same ingest window must not duplicate bars."""
        from sqlalchemy import select

        from trading_platform.core.settings import load_settings

        settings = load_settings()
        md_settings = _make_market_data_settings()

        with patch("httpx.Client.get", return_value=self._polygon_response()):
            ingest_daily_bars(
                from_date=date(2024, 1, 1),
                to_date=date(2024, 1, 3),
                symbols=["AAPL"],
                settings=md_settings,
                trigger_source="test",
                db_settings=settings,
            )

        with patch("httpx.Client.get", return_value=self._polygon_response()):
            ingest_daily_bars(
                from_date=date(2024, 1, 1),
                to_date=date(2024, 1, 3),
                symbols=["AAPL"],
                settings=md_settings,
                trigger_source="test",
                db_settings=settings,
            )

        with session_scope(settings) as session:
            bars = session.execute(select(DailyBarModel)).scalars().all()
            runs = session.execute(select(MarketDataIngestionRun)).scalars().all()

        assert len(bars) == 3, "Duplicate bars created by repeated ingest"
        assert len(runs) == 2, "Each ingest should create its own run record"

    def test_ingest_records_failed_symbol(self, migrated_ingest_db: str) -> None:
        """Failed symbols are recorded in the run without aborting others."""
        import httpx
        from sqlalchemy import select

        from trading_platform.core.settings import load_settings

        settings = load_settings()
        md_settings = _make_market_data_settings()

        good_response = self._polygon_response()
        bad_response = MagicMock()
        bad_response.status_code = 500
        bad_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Server Error", request=MagicMock(), response=bad_response
        )

        call_index = 0

        def mock_get(url, **kwargs):
            nonlocal call_index
            # First call = AAPL (good), second call = MSFT (bad)
            result = good_response if call_index == 0 else bad_response
            call_index += 1
            return result

        with patch("httpx.Client.get", side_effect=mock_get):
            result = ingest_daily_bars(
                from_date=date(2024, 1, 1),
                to_date=date(2024, 1, 3),
                symbols=["AAPL", "MSFT"],
                settings=md_settings,
                trigger_source="test",
                db_settings=settings,
            )

        assert "MSFT" in result.symbols_failed
        assert result.bars_upserted == 3  # AAPL bars still ingested
        assert result.succeeded is False

        with session_scope(settings) as session:
            runs = session.execute(select(MarketDataIngestionRun)).scalars().all()

        assert runs[0].status == "partial"
        assert "MSFT" in runs[0].symbols_failed
        assert result.run_status == "partial"
        assert runs[0].error_message is None
        assert result.raise_for_all_symbols_failed() is None


# ---------------------------------------------------------------------------
# D-08a: service-owned run status predicate and all-fail semantics
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("succeeded", "failed", "run_error", "expected"),
    [
        (0, 0, None, "failed"),
        (0, 1, None, "failed"),
        (0, 2, None, "failed"),
        (1, 1, None, "partial"),
        (2, 1, None, "partial"),
        (1, 0, None, "succeeded"),
        (3, 0, None, "succeeded"),
        (1, 0, "boom", "failed"),
        (0, 0, "boom", "failed"),
        (2, 2, "boom", "failed"),
    ],
)
def test_derive_run_status_truth_table(
    succeeded: int, failed: int, run_error: str | None, expected: str
) -> None:
    assert (
        _derive_run_status(
            succeeded_count=succeeded, failed_count=failed, run_error=run_error
        )
        == expected
    )


@pytest.mark.parametrize(("succeeded", "failed"), [(-1, 0), (0, -1), (-1, -1)])
def test_derive_run_status_rejects_negative_counts(succeeded: int, failed: int) -> None:
    with pytest.raises(ValueError):
        _derive_run_status(succeeded_count=succeeded, failed_count=failed, run_error=None)


def test_ingestion_run_status_literal_is_closed() -> None:
    assert typing.get_args(IngestionRunStatus) == ("succeeded", "partial", "failed")


def test_ingestion_result_run_status_is_required() -> None:
    field = {f.name: f for f in dataclasses.fields(IngestionResult)}["run_status"]
    assert field.kw_only is True
    assert field.default is dataclasses.MISSING
    assert field.default_factory is dataclasses.MISSING
    with pytest.raises(TypeError):
        IngestionResult(  # type: ignore[call-arg]
            provider="polygon", from_date=date(2024, 1, 1), to_date=date(2024, 1, 3)
        )


def test_all_symbols_failed_error_shape() -> None:
    err = IngestionAllSymbolsFailedError(run_id="r1", symbols_failed=("A",), detail="d")
    assert isinstance(err, RuntimeError)
    assert str(err) == "Ingestion run r1 failed: d"
    assert err.run_id == "r1"
    assert err.symbols_failed == ("A",)


class _FakeIngestPolygonClient:
    """Context-manager Polygon fake: per-ticker exception or bars."""

    behaviours: dict[str, Exception | list[DailyBar]] = {}

    def __init__(self, _settings: object) -> None:
        pass

    def __enter__(self) -> "_FakeIngestPolygonClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def fetch_daily_bars(self, request: DailyBarRequest) -> list[DailyBar]:
        outcome = self.behaviours[request.symbol]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _install_fake_polygon(
    monkeypatch: pytest.MonkeyPatch, behaviours: dict[str, Exception | list[DailyBar]]
) -> None:
    import trading_platform.services.ingestion as ingestion_module

    fake = type("_Fake", (_FakeIngestPolygonClient,), {"behaviours": behaviours})
    monkeypatch.setattr(ingestion_module, "PolygonClient", fake)


def _run_ingest(symbols: list[str]) -> IngestionResult:
    from trading_platform.core.settings import load_settings

    return ingest_daily_bars(
        from_date=date(2024, 1, 1),
        to_date=date(2024, 1, 3),
        symbols=symbols,
        settings=_make_market_data_settings(),
        trigger_source="test",
        db_settings=load_settings(),
    )


def _only_run() -> MarketDataIngestionRun:
    from sqlalchemy import select

    from trading_platform.core.settings import load_settings

    with session_scope(load_settings()) as session:
        runs = session.execute(select(MarketDataIngestionRun)).scalars().all()
        assert len(runs) == 1
        run = runs[0]
        session.expunge(run)
        return run


class TestIngestionAllFailSemantics:
    def test_ingest_all_symbols_failed_finalizes_failed_run(
        self, migrated_ingest_db: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_fake_polygon(
            monkeypatch,
            {
                "AAPL": PolygonAuthError("secret-token-XYZ"),
                "MSFT": PolygonAuthError("secret-token-XYZ"),
            },
        )

        result = _run_ingest(["AAPL", "MSFT"])

        expected = (
            "0 of 2 symbols succeeded; failed: AAPL (PolygonAuthError), "
            "MSFT (PolygonAuthError)"
        )
        assert result.run_status == "failed"
        run = _only_run()
        assert run.status == "failed"
        assert list(run.symbols_failed) == ["AAPL", "MSFT"]
        assert run.bars_upserted == 0
        assert run.completed_at is not None
        assert run.error_message == expected
        assert "secret-token-XYZ" not in run.error_message

        with pytest.raises(IngestionAllSymbolsFailedError) as excinfo:
            result.raise_for_all_symbols_failed()
        assert str(excinfo.value) == f"Ingestion run {result.run_id} failed: {expected}"
        assert excinfo.value.run_id == result.run_id
        assert excinfo.value.symbols_failed == ("AAPL", "MSFT")

    def test_ingest_mixed_exception_classes_named_per_symbol(
        self, migrated_ingest_db: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_fake_polygon(
            monkeypatch,
            {
                "AAPL": PolygonAuthError("nope"),
                "MSFT": PolygonClientError("nope"),
            },
        )

        _run_ingest(["AAPL", "MSFT"])

        assert _only_run().error_message == (
            "0 of 2 symbols succeeded; failed: AAPL (PolygonAuthError), "
            "MSFT (PolygonClientError)"
        )

    def test_ingest_empty_bars_counts_as_success(
        self, migrated_ingest_db: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_fake_polygon(monkeypatch, {"AAPL": [], "MSFT": []})

        result = _run_ingest(["AAPL", "MSFT"])

        run = _only_run()
        assert run.status == "succeeded"
        assert result.run_status == "succeeded"
        assert result.bars_upserted == 0
        assert run.error_message is None
        assert result.raise_for_all_symbols_failed() is None

    def test_ingest_one_ok_one_failed_is_partial_without_error(
        self, migrated_ingest_db: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_fake_polygon(
            monkeypatch,
            {"AAPL": _fixture_bars("AAPL"), "MSFT": PolygonAuthError("nope")},
        )

        result = _run_ingest(["AAPL", "MSFT"])

        run = _only_run()
        assert run.status == "partial"
        assert run.error_message is None
        assert result.run_status == "partial"
        assert result.raise_for_all_symbols_failed() is None
