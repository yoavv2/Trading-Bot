"""Tests for ``services.symbol_metadata_sync`` (ORCH-02) and
``services.calendar.sync_market_sessions`` (OPS-05).

DB fixture (``migrated_metadata_db``) mirrors
``tests/test_market_data_ingestion.py``'s ``migrated_ingest_db`` fixture --
a throwaway Postgres database provisioned with migrations applied, torn down
after the test.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import date
from unittest.mock import patch

import psycopg
import pytest
from alembic import command
from scripts.migrate import build_alembic_config

from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.db.session import clear_engine_cache, session_scope
from trading_platform.services.calendar import MarketSessionSyncResult, sync_market_sessions
from trading_platform.services.symbol_metadata_sync import (
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


class TestSyncSymbolMetadata:
    def test_synced_skipped_failed_split(self, migrated_metadata_db: str) -> None:
        settings = load_settings()

        def fake_fetch(ticker: str, _settings: object) -> dict[str, object] | None:
            if ticker == "ZZZZ":
                return None
            return _overview(ticker)

        with patch(
            "trading_platform.services.symbol_metadata_sync.fetch_ticker_overview",
            side_effect=fake_fetch,
        ):
            result = sync_symbol_metadata(["AAPL", "MSFT", "ZZZZ"], settings=settings)

        assert result.synced == ["AAPL", "MSFT"]
        assert result.skipped == ["ZZZZ"]
        assert result.failed == []
        assert result.raise_for_failures() is None

        with session_scope(settings) as session:
            from sqlalchemy import select

            from trading_platform.db.models.symbol import Symbol

            rows = session.execute(select(Symbol)).scalars().all()
        assert len(rows) == 2

    def test_fetch_error_marks_failed_and_raises(self, migrated_metadata_db: str) -> None:
        settings = load_settings()

        def fake_fetch(ticker: str, _settings: object) -> dict[str, object] | None:
            if ticker == "BAD":
                raise RuntimeError("boom")
            return _overview(ticker)

        with patch(
            "trading_platform.services.symbol_metadata_sync.fetch_ticker_overview",
            side_effect=fake_fetch,
        ):
            result = sync_symbol_metadata(["AAPL", "BAD"], settings=settings)

        assert result.synced == ["AAPL"]
        assert result.failed == ["BAD"]
        assert result.succeeded is False

        with pytest.raises(SymbolMetadataSyncFailedError, match="BAD"):
            result.raise_for_failures()

    def test_calling_twice_upserts_without_duplicating(self, migrated_metadata_db: str) -> None:
        settings = load_settings()

        with patch(
            "trading_platform.services.symbol_metadata_sync.fetch_ticker_overview",
            side_effect=lambda ticker, _settings: _overview(ticker),
        ):
            sync_symbol_metadata(["AAPL"], settings=settings)

            with session_scope(settings) as session:
                from sqlalchemy import select

                from trading_platform.db.models.symbol import Symbol

                first_row = session.execute(select(Symbol).where(Symbol.ticker == "AAPL")).scalar_one()
                first_updated_at = first_row.updated_at

            sync_symbol_metadata(["AAPL"], settings=settings)

        with session_scope(settings) as session:
            from sqlalchemy import select

            from trading_platform.db.models.symbol import Symbol

            rows = session.execute(select(Symbol).where(Symbol.ticker == "AAPL")).scalars().all()
            second_row = session.execute(select(Symbol).where(Symbol.ticker == "AAPL")).scalar_one()

        assert len(rows) == 1
        assert second_row.updated_at >= first_updated_at

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

        assert isinstance(result, MarketSessionSyncResult)
        assert result.exchange == settings.market_data.calendar.exchange
        assert result.sessions_upserted > 0

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
