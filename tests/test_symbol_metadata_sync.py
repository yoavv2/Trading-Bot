"""Tests for ``services.symbol_metadata_sync`` (ORCH-02) and
``services.calendar.sync_market_sessions`` (OPS-05).

DB fixture (``migrated_metadata_db``) mirrors
``tests/test_market_data_ingestion.py``'s ``migrated_ingest_db`` fixture --
a throwaway Postgres database provisioned with migrations applied, torn down
after the test.
"""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import Iterator
from datetime import date
from unittest.mock import patch

import httpx
import psycopg
import pytest
from alembic import command
from scripts.migrate import build_alembic_config
from sqlalchemy import select

from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.db.models.symbol import Symbol
from trading_platform.db.session import clear_engine_cache, session_scope
from trading_platform.services import symbol_metadata_sync as symbol_metadata_sync_module
from trading_platform.services.batch_outcomes import (
    BatchOutcome,
    OperationFailureReason,
    SymbolFailureReason,
)
from trading_platform.services.calendar import (
    MarketSessionSyncResult,
    sessions_in_range,
    sync_market_sessions,
)
from trading_platform.services.polygon import PolygonAuthError
from trading_platform.services.symbol_metadata_sync import (
    InvalidOverviewResponseError,
    MetadataSyncResult,
    SymbolMetadataSyncFailedError,
    sync_symbol_metadata,
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
def migrated_metadata_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Provision a temporary PostgreSQL database with migrations applied."""
    database_name = f"metadata_test_{uuid.uuid4().hex[:8]}"
    admin_params = _admin_connection_settings()

    try:
        with _connect_admin(admin_params) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f'CREATE DATABASE "{database_name}"')
    except psycopg.Error as exc:
        pytest.fail(f"PostgreSQL is required for this test. Connection error: {exc}")

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


def _overview(name: str) -> dict[str, object]:
    return {
        "name": name,
        "market": "stocks",
        "locale": "us",
        "primary_exchange": "XNAS",
        "type": "CS",
        "active": True,
        "description": f"{name} description",
        "list_date": "2000-01-01",
        "currency_name": "usd",
        "cik": "0000000000",
        "composite_figi": "BBG000000000",
        "share_class_figi": "BBG000000001",
    }


# ---------------------------------------------------------------------------
# sync_symbol_metadata
# ---------------------------------------------------------------------------


def _symbol_rows(settings) -> dict[str, tuple]:
    """Snapshot of every Symbol row (ticker -> comparable tuple)."""

    with session_scope(settings) as session:
        rows = session.execute(select(Symbol)).scalars().all()
        return {
            row.ticker: (
                row.name,
                row.market,
                row.symbol_type,
                row.primary_exchange,
                row.active,
                row.metadata_provider,
                row.updated_at,
            )
            for row in rows
        }


def _http_status_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://provider.invalid/v3/reference/tickers/X")
    return httpx.HTTPStatusError(
        "status", request=request, response=httpx.Response(status, request=request)
    )


@pytest.fixture()
def metadata_env(migrated_metadata_db: str, monkeypatch: pytest.MonkeyPatch) -> str:
    """Migrated DB plus a (fake) Polygon API key so the configuration check
    passes; every fetch in these tests goes through a patched seam."""

    monkeypatch.setenv("TRADING_PLATFORM_MARKET_DATA__POLYGON__API_KEY", "test-key-not-real")
    clear_settings_cache()
    return migrated_metadata_db


def _patch_fetch(side_effect):
    return patch(
        "trading_platform.services.symbol_metadata_sync.fetch_ticker_overview",
        side_effect=side_effect,
    )


class TestSyncSymbolMetadata:
    def test_one_unknown_ticker_is_partial_with_not_found(self, metadata_env: str) -> None:
        # D-28 replaces the old skipped bucket and any-failure raise: a 404 is the
        # per-symbol reason `not_found`; the other symbols are synced.
        settings = load_settings()

        def fake_fetch(ticker: str, _settings: object) -> dict[str, object] | None:
            if ticker == "ZZZZ":
                return None
            return _overview(ticker)

        with _patch_fetch(fake_fetch):
            result = sync_symbol_metadata(["AAPL", "MSFT", "ZZZZ"], settings=settings)

        assert result.synced == ["AAPL", "MSFT"]
        assert result.skipped == []
        assert result.failed == ["ZZZZ"]
        assert result.failures == [{"symbol": "ZZZZ", "reason": "not_found"}]
        assert result.operation_failure is None
        assert result.outcome is BatchOutcome.PARTIAL
        assert result.raise_if_failed() is None

        assert set(_symbol_rows(settings)) == {"AAPL", "MSFT"}

    def test_fetch_error_among_successes_is_partial(self, metadata_env: str) -> None:
        # D-28: replaces test_fetch_error_marks_failed_and_raises. One transient
        # fetch error is a per-symbol failure and no longer fails the run.
        settings = load_settings()

        def fake_fetch(ticker: str, _settings: object) -> dict[str, object] | None:
            if ticker == "BAD":
                raise _http_status_error(503)
            return _overview(ticker)

        with _patch_fetch(fake_fetch):
            result = sync_symbol_metadata(["AAPL", "BAD"], settings=settings)

        assert result.synced == ["AAPL"]
        assert result.failed == ["BAD"]
        assert result.failures == [{"symbol": "BAD", "reason": "fetch_error"}]
        assert result.outcome is BatchOutcome.PARTIAL
        assert result.succeeded is False
        result.raise_if_failed()

    def test_missing_required_fields_failed_and_not_upserted(self, metadata_env: str) -> None:
        settings = load_settings()

        def fake_fetch(ticker: str, _settings: object) -> dict[str, object] | None:
            overview = _overview(ticker)
            if ticker == "NOEX":
                overview["primary_exchange"] = ""
            if ticker == "NOTYPE":
                del overview["type"]
            return overview

        with _patch_fetch(fake_fetch):
            result = sync_symbol_metadata(["AAPL", "NOEX", "NOTYPE"], settings=settings)

        assert result.synced == ["AAPL"]
        assert result.failures == [
            {"symbol": "NOEX", "reason": "missing_required_fields"},
            {"symbol": "NOTYPE", "reason": "missing_required_fields"},
        ]
        assert set(_symbol_rows(settings)) == {"AAPL"}

    def test_invalid_payload_is_invalid_response(self, metadata_env: str) -> None:
        settings = load_settings()

        def fake_fetch(ticker: str, _settings: object) -> object:
            if ticker == "WEIRD":
                return ["not", "an", "object"]
            if ticker == "BROKEN":
                raise InvalidOverviewResponseError("bad json")
            return _overview(ticker)

        with _patch_fetch(fake_fetch):
            result = sync_symbol_metadata(["AAPL", "WEIRD", "BROKEN"], settings=settings)

        assert result.synced == ["AAPL"]
        assert result.failures == [
            {"symbol": "WEIRD", "reason": "invalid_response"},
            {"symbol": "BROKEN", "reason": "invalid_response"},
        ]
        assert result.outcome is BatchOutcome.PARTIAL

    @pytest.mark.parametrize("position", ["first", "later"])
    def test_auth_failure_is_failed_and_no_symbol_marked_synced(
        self, metadata_env: str, position: str
    ) -> None:
        settings = load_settings()
        # An existing row proves the failed run touches ZERO Symbol rows.
        with _patch_fetch(lambda ticker, _s: _overview(ticker)):
            sync_symbol_metadata(["SPY"], settings=settings)
        before = _symbol_rows(settings)
        auth_ticker = "AAPL" if position == "first" else "QQQ"

        def fake_fetch(ticker: str, _settings: object) -> dict[str, object] | None:
            if ticker == auth_ticker:
                raise PolygonAuthError("simulated 401")
            return _overview(ticker)

        with _patch_fetch(fake_fetch):
            result = sync_symbol_metadata(["AAPL", "MSFT", "QQQ", "SPY"], settings=settings)

        assert result.outcome is BatchOutcome.FAILED
        assert result.operation_failure is OperationFailureReason.PROVIDER_AUTH
        assert result.synced == []
        assert _symbol_rows(settings) == before
        with pytest.raises(SymbolMetadataSyncFailedError) as excinfo:
            result.raise_if_failed()
        assert excinfo.value.operation_failure is OperationFailureReason.PROVIDER_AUTH
        assert "provider_auth" in str(excinfo.value)

    def test_no_api_key_is_invalid_configuration_and_no_http_call(
        self, migrated_metadata_db: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TRADING_PLATFORM_MARKET_DATA__POLYGON__API_KEY", "")
        clear_settings_cache()
        settings = load_settings()
        assert not settings.market_data.polygon.api_key

        with _patch_fetch(lambda ticker, _s: _overview(ticker)) as fake, patch(
            "trading_platform.services.symbol_metadata_sync.httpx.get"
        ) as http_get:
            result = sync_symbol_metadata(["AAPL"], settings=settings)

        assert fake.call_count == 0
        assert http_get.call_count == 0
        assert result.operation_failure is OperationFailureReason.INVALID_CONFIGURATION
        assert result.outcome is BatchOutcome.FAILED
        assert result.synced == []
        assert _symbol_rows(settings) == {}

    def test_every_ticker_transient_fetch_error_is_provider_unavailable(
        self, metadata_env: str
    ) -> None:
        settings = load_settings()

        def fake_fetch(ticker: str, _settings: object) -> dict[str, object] | None:
            if ticker == "AAPL":
                raise RuntimeError("Network error fetching AAPL")
            raise _http_status_error(429)

        with _patch_fetch(fake_fetch):
            result = sync_symbol_metadata(["AAPL", "MSFT"], settings=settings)

        assert result.operation_failure is OperationFailureReason.PROVIDER_UNAVAILABLE
        assert result.outcome is BatchOutcome.FAILED
        assert result.synced == []

    def test_all_tickers_failing_is_failed_lifecycle(self, metadata_env: str) -> None:
        # Every ticker not_found: outcome failed with per-symbol reasons (NOT an
        # operation-level provider_unavailable), and the handler raises.
        settings = load_settings()

        with _patch_fetch(lambda ticker, _s: None):
            result = sync_symbol_metadata(["AAA", "BBB"], settings=settings)

        assert result.outcome is BatchOutcome.FAILED
        assert result.operation_failure is None
        assert result.failures == [
            {"symbol": "AAA", "reason": "not_found"},
            {"symbol": "BBB", "reason": "not_found"},
        ]
        with pytest.raises(SymbolMetadataSyncFailedError, match="AAA"):
            result.raise_if_failed()

    def test_database_write_failure_marks_nothing_synced(self, metadata_env: str) -> None:
        settings = load_settings()
        real_upsert = symbol_metadata_sync_module.upsert_symbol_metadata

        def flaky_upsert(session, ticker, overview):
            if ticker == "MSFT":
                raise RuntimeError("simulated database error")
            return real_upsert(session, ticker, overview)

        with _patch_fetch(lambda ticker, _s: _overview(ticker)), patch(
            "trading_platform.services.symbol_metadata_sync.upsert_symbol_metadata",
            side_effect=flaky_upsert,
        ):
            result = sync_symbol_metadata(["AAPL", "MSFT"], settings=settings)

        assert result.operation_failure is OperationFailureReason.DATABASE_WRITE
        assert result.outcome is BatchOutcome.FAILED
        assert result.synced == []
        # one transaction: AAPL's upsert was rolled back with MSFT's failure
        assert _symbol_rows(settings) == {}

    def test_closed_reason_sets(self) -> None:
        assert {m.value for m in SymbolFailureReason} == {
            "not_found",
            "missing_required_fields",
            "invalid_response",
            "fetch_error",
        }
        assert {m.value for m in OperationFailureReason} == {
            "provider_auth",
            "invalid_configuration",
            "database_write",
            "provider_unavailable",
        }

    def test_any_failure_raise_is_replaced_by_outcome_based_raise(self) -> None:
        legacy_name = "raise_for_" + "failures"
        assert not hasattr(MetadataSyncResult, legacy_name)
        assert hasattr(MetadataSyncResult, "raise_if_failed")

    def test_calling_twice_upserts_without_duplicating(self, metadata_env: str) -> None:
        settings = load_settings()

        with _patch_fetch(lambda ticker, _settings: _overview(ticker)):
            sync_symbol_metadata(["AAPL"], settings=settings)

            with session_scope(settings) as session:
                first_row = session.execute(select(Symbol).where(Symbol.ticker == "AAPL")).scalar_one()
                first_updated_at = first_row.updated_at

            time.sleep(0.01)
            sync_symbol_metadata(["AAPL"], settings=settings)

        with session_scope(settings) as session:
            rows = session.execute(select(Symbol).where(Symbol.ticker == "AAPL")).scalars().all()
            second_row = session.execute(select(Symbol).where(Symbol.ticker == "AAPL")).scalar_one()

        assert len(rows) == 1
        assert second_row.updated_at > first_updated_at

    def test_to_dict_has_no_dry_run_field(self) -> None:
        result = MetadataSyncResult(synced=["AAPL"], skipped=[], failed=[])
        payload = result.to_dict()
        assert "dry_run" not in payload
        assert payload == {
            "synced": ["AAPL"],
            "skipped": [],
            "failed": [],
            "synced_count": 1,
            "skipped_count": 0,
            "failed_count": 0,
            "succeeded": True,
            "outcome": "complete",
            "failures": [],
            "operation_failure": None,
        }


# ---------------------------------------------------------------------------
# sync_market_sessions
# ---------------------------------------------------------------------------


class TestSyncMarketSessions:
    def test_upserts_sessions_and_reports_count(self, migrated_metadata_db: str) -> None:
        settings = load_settings()

        result = sync_market_sessions(
            from_date=date(2024, 1, 1), to_date=date(2024, 1, 10), settings=settings
        )

        expected_session_count = len(
            sessions_in_range(
                date(2024, 1, 1), date(2024, 1, 10), settings.market_data.calendar.exchange
            )
        )

        assert isinstance(result, MarketSessionSyncResult)
        assert result.exchange == settings.market_data.calendar.exchange
        assert result.sessions_upserted == expected_session_count

        from sqlalchemy import select

        from trading_platform.db.models.market_session import MarketSession

        with session_scope(settings) as session:
            rows = session.execute(
                select(MarketSession)
                .where(MarketSession.session_date >= date(2024, 1, 1))
                .where(MarketSession.session_date <= date(2024, 1, 10))
            ).scalars().all()
        assert len(rows) == result.sessions_upserted

    def test_calling_again_does_not_duplicate_rows(self, migrated_metadata_db: str) -> None:
        settings = load_settings()

        first = sync_market_sessions(
            from_date=date(2024, 1, 1), to_date=date(2024, 1, 10), settings=settings
        )
        second = sync_market_sessions(
            from_date=date(2024, 1, 1), to_date=date(2024, 1, 10), settings=settings
        )

        assert first.sessions_upserted == second.sessions_upserted

        from sqlalchemy import select

        from trading_platform.db.models.market_session import MarketSession

        with session_scope(settings) as session:
            rows = session.execute(
                select(MarketSession)
                .where(MarketSession.session_date >= date(2024, 1, 1))
                .where(MarketSession.session_date <= date(2024, 1, 10))
            ).scalars().all()
        assert len(rows) == first.sessions_upserted
