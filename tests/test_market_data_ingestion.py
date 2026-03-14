"""Tests for Polygon client, normalization, and idempotent ingestion pipeline."""

from __future__ import annotations

import json
import os
import sys
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
    PolygonClientError,
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

import psycopg

from alembic import command
from scripts.migrate import build_alembic_config
from trading_platform.db.models import DailyBar as DailyBarModel
from trading_platform.db.models import MarketDataIngestionRun, Symbol
from trading_platform.db.session import clear_engine_cache, session_scope
from trading_platform.services.ingestion import ingest_daily_bars, upsert_daily_bars, upsert_symbol


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
        from sqlalchemy import select

        from trading_platform.core.settings import load_settings
        import httpx

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
