"""Tests for symbol metadata upsert, calendar session persistence,
session-aware market-data reads, and missing-session detection."""

from __future__ import annotations

import os
import sys
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alembic import command
from scripts.migrate import build_alembic_config
from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.db.models.daily_bar import DailyBar as DailyBarModel
from trading_platform.db.models.market_session import MarketSession
from trading_platform.db.models.symbol import Symbol
from trading_platform.db.session import clear_engine_cache, session_scope
from trading_platform.services.calendar import (
    get_persisted_sessions,
    is_trading_session,
    latest_session_before,
    sessions_in_range,
    upsert_market_sessions,
)
from trading_platform.services.market_data_access import (
    MissingSessionInfo,
    SessionBar,
    bars_for_sessions,
    latest_completed_session,
    latest_persisted_session,
    missing_sessions_for_symbol,
)


# ---------------------------------------------------------------------------
# Database fixtures (temporary PostgreSQL databases)
# ---------------------------------------------------------------------------


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
def migrated_access_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Provision a temporary PostgreSQL database with all migrations applied."""
    database_name = f"access_test_{uuid.uuid4().hex[:8]}"
    admin_params = _admin_connection_settings()

    try:
        with _connect_admin(admin_params) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f'CREATE DATABASE "{database_name}"')
    except psycopg.Error as exc:
        pytest.fail(
            "PostgreSQL is required for market-data access tests. "
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


# ---------------------------------------------------------------------------
# Unit tests: calendar service (no DB required)
# ---------------------------------------------------------------------------


class TestCalendarService:
    def test_sessions_in_range_excludes_weekends(self) -> None:
        # 2024-01-13 is Saturday, 2024-01-14 is Sunday
        sessions = sessions_in_range(date(2024, 1, 13), date(2024, 1, 14))
        assert sessions == []

    def test_sessions_in_range_includes_trading_days(self) -> None:
        # 2024-01-02 to 2024-01-05 are Tue-Fri (2024-01-01 is New Year's Day holiday)
        sessions = sessions_in_range(date(2024, 1, 2), date(2024, 1, 5))
        assert date(2024, 1, 2) in sessions
        assert date(2024, 1, 5) in sessions
        # No weekends
        for s in sessions:
            assert s.weekday() < 5

    def test_sessions_in_range_excludes_holidays(self) -> None:
        # 2024-01-01 is New Year's Day — not a session
        sessions = sessions_in_range(date(2024, 1, 1), date(2024, 1, 1))
        assert sessions == []

    def test_latest_session_before_returns_friday_for_saturday(self) -> None:
        # 2024-01-06 is Saturday; latest session should be Friday 2024-01-05
        result = latest_session_before(date(2024, 1, 6))
        assert result == date(2024, 1, 5)

    def test_latest_session_before_returns_same_day_for_session(self) -> None:
        # 2024-01-02 is a trading day
        result = latest_session_before(date(2024, 1, 2))
        assert result == date(2024, 1, 2)

    def test_is_trading_session_true_for_weekday(self) -> None:
        assert is_trading_session(date(2024, 1, 2)) is True

    def test_is_trading_session_false_for_weekend(self) -> None:
        assert is_trading_session(date(2024, 1, 6)) is False

    def test_is_trading_session_false_for_holiday(self) -> None:
        assert is_trading_session(date(2024, 1, 1)) is False


# ---------------------------------------------------------------------------
# Integration tests: session persistence
# ---------------------------------------------------------------------------


class TestSessionPersistence:
    def test_upsert_market_sessions_creates_rows(self, migrated_access_db: str) -> None:
        settings = load_settings()
        # Seed a narrow window: Mon-Fri 2024-01-02 to 2024-01-05 (4 sessions)
        with session_scope(settings) as session:
            count = upsert_market_sessions(session, date(2024, 1, 2), date(2024, 1, 5))

        assert count == 4

        with session_scope(settings) as session:
            rows = get_persisted_sessions(session, date(2024, 1, 2), date(2024, 1, 5))

        assert len(rows) == 4
        dates = [r.session_date for r in rows]
        assert date(2024, 1, 2) in dates
        assert date(2024, 1, 5) in dates

    def test_upsert_market_sessions_is_idempotent(self, migrated_access_db: str) -> None:
        settings = load_settings()
        with session_scope(settings) as session:
            count1 = upsert_market_sessions(session, date(2024, 1, 2), date(2024, 1, 5))
        with session_scope(settings) as session:
            count2 = upsert_market_sessions(session, date(2024, 1, 2), date(2024, 1, 5))

        assert count1 == 4
        assert count2 == 4  # same 4 rows updated, not duplicated

        with session_scope(settings) as session:
            rows = get_persisted_sessions(session, date(2024, 1, 2), date(2024, 1, 5))
        assert len(rows) == 4

    def test_upsert_market_sessions_stores_open_close(self, migrated_access_db: str) -> None:
        settings = load_settings()
        with session_scope(settings) as session:
            upsert_market_sessions(session, date(2024, 1, 2), date(2024, 1, 2))
            rows = get_persisted_sessions(session, date(2024, 1, 2), date(2024, 1, 2))

        assert len(rows) == 1
        row = rows[0]
        assert row.market_open is not None
        assert row.market_close is not None
        assert row.market_open < row.market_close

    def test_upsert_market_sessions_excludes_weekends(self, migrated_access_db: str) -> None:
        settings = load_settings()
        with session_scope(settings) as session:
            count = upsert_market_sessions(session, date(2024, 1, 6), date(2024, 1, 7))

        assert count == 0

    def test_market_session_early_close_flag(self, migrated_access_db: str) -> None:
        """Black Friday 2024-11-29 is a standard early-close day for NYSE."""
        settings = load_settings()
        with session_scope(settings) as session:
            upsert_market_sessions(session, date(2024, 11, 29), date(2024, 11, 29))
            rows = get_persisted_sessions(session, date(2024, 11, 29), date(2024, 11, 29))

        assert len(rows) == 1
        assert rows[0].early_close is True


# ---------------------------------------------------------------------------
# Integration tests: symbol metadata upsert
# ---------------------------------------------------------------------------


class TestSymbolMetadataUpsert:
    def _make_overview(self, ticker: str) -> dict:
        return {
            "ticker": ticker,
            "name": f"{ticker} Corp",
            "market": "stocks",
            "locale": "us",
            "primary_exchange": "XNYS",
            "type": "CS",
            "active": True,
            "description": f"Test description for {ticker}",
            "list_date": "2000-01-01",
            "currency_name": "usd",
            "cik": "0000000001",
            "composite_figi": "BBG000FAKE01",
            "share_class_figi": "BBG001FAKE01",
        }

    def test_metadata_upsert_creates_symbol_with_enriched_fields(
        self, migrated_access_db: str
    ) -> None:
        sys.path.insert(
            0,
            str(Path(__file__).resolve().parents[1] / "scripts"),
        )
        from sync_symbol_metadata import _upsert_symbol_metadata

        settings = load_settings()
        overview = self._make_overview("AAPL")

        with session_scope(settings) as session:
            sym = _upsert_symbol_metadata(session, "AAPL", overview)
            sym_id = sym.id

        with session_scope(settings) as session:
            from sqlalchemy import select

            persisted = session.execute(
                select(Symbol).where(Symbol.ticker == "AAPL")
            ).scalar_one()

        assert persisted.id == sym_id
        assert persisted.name == "AAPL Corp"
        assert persisted.market == "stocks"
        assert persisted.list_date == date(2000, 1, 1)
        assert persisted.currency_name == "usd"
        assert persisted.composite_figi == "BBG000FAKE01"
        assert persisted.metadata_provider == "polygon"

    def test_metadata_upsert_is_idempotent(self, migrated_access_db: str) -> None:
        sys.path.insert(
            0,
            str(Path(__file__).resolve().parents[1] / "scripts"),
        )
        from sync_symbol_metadata import _upsert_symbol_metadata

        settings = load_settings()
        overview = self._make_overview("SPY")

        with session_scope(settings) as session:
            sym1 = _upsert_symbol_metadata(session, "SPY", overview)
            id1 = sym1.id

        overview["name"] = "SPY Updated"
        with session_scope(settings) as session:
            sym2 = _upsert_symbol_metadata(session, "SPY", overview)
            id2 = sym2.id

        assert id1 == id2  # same row

        with session_scope(settings) as session:
            from sqlalchemy import select

            persisted = session.execute(
                select(Symbol).where(Symbol.ticker == "SPY")
            ).scalar_one()
        assert persisted.name == "SPY Updated"


# ---------------------------------------------------------------------------
# Integration tests: market-data access layer
# ---------------------------------------------------------------------------


def _seed_symbol_and_bars(
    session,
    ticker: str,
    bar_dates: list[date],
    adjusted: bool = True,
    provider: str = "polygon",
) -> Symbol:
    """Seed a symbol and daily bars for testing."""
    from sqlalchemy import select

    existing = session.execute(
        select(Symbol).where(Symbol.ticker == ticker)
    ).scalar_one_or_none()
    if existing is None:
        sym = Symbol(id=uuid.uuid4(), ticker=ticker, active=True)
        session.add(sym)
        session.flush()
    else:
        sym = existing

    for d in bar_dates:
        bar = DailyBarModel(
            id=uuid.uuid4(),
            symbol_id=sym.id,
            session_date=d,
            open=Decimal("100.00"),
            high=Decimal("105.00"),
            low=Decimal("99.00"),
            close=Decimal("103.00"),
            volume=1_000_000,
            adjusted=adjusted,
            provider=provider,
        )
        session.add(bar)
    session.flush()
    return sym


class TestLatestCompletedSession:
    def test_returns_none_when_no_bars_exist(self, migrated_access_db: str) -> None:
        settings = load_settings()
        with session_scope(settings) as session:
            upsert_market_sessions(session, date(2024, 1, 2), date(2024, 1, 5))
            result = latest_completed_session(session)
        assert result is None

    def test_returns_latest_session_with_bars(self, migrated_access_db: str) -> None:
        settings = load_settings()
        bar_dates = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]

        with session_scope(settings) as session:
            upsert_market_sessions(session, date(2024, 1, 2), date(2024, 1, 5))
            _seed_symbol_and_bars(session, "AAPL", bar_dates)
            result = latest_completed_session(session)

        assert result == date(2024, 1, 4)

    def test_respects_as_of_boundary(self, migrated_access_db: str) -> None:
        settings = load_settings()
        bar_dates = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]

        with session_scope(settings) as session:
            upsert_market_sessions(session, date(2024, 1, 2), date(2024, 1, 5))
            _seed_symbol_and_bars(session, "AAPL", bar_dates)
            result = latest_completed_session(session, as_of=date(2024, 1, 3))

        assert result == date(2024, 1, 3)


class TestBarsForSessions:
    def test_returns_empty_for_unknown_symbol(self, migrated_access_db: str) -> None:
        settings = load_settings()
        with session_scope(settings) as session:
            upsert_market_sessions(session, date(2024, 1, 2), date(2024, 1, 5))
            bars = bars_for_sessions(session, "UNKNOWN", n_sessions=5)
        assert bars == []

    def test_returns_last_n_sessions_in_order(self, migrated_access_db: str) -> None:
        settings = load_settings()
        # Seed 4 trading days: Jan 2, 3, 4, 5
        bar_dates = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5)]

        with session_scope(settings) as session:
            upsert_market_sessions(session, date(2024, 1, 2), date(2024, 1, 5))
            _seed_symbol_and_bars(session, "AAPL", bar_dates)

        with session_scope(settings) as session:
            bars = bars_for_sessions(
                session, "AAPL", n_sessions=3, as_of=date(2024, 1, 5)
            )

        assert len(bars) == 3
        assert bars[0].session_date == date(2024, 1, 3)
        assert bars[1].session_date == date(2024, 1, 4)
        assert bars[2].session_date == date(2024, 1, 5)
        assert all(isinstance(b, SessionBar) for b in bars)

    def test_bars_span_weekend_correctly(self, migrated_access_db: str) -> None:
        """Session window crosses a weekend — should return only trading days."""
        settings = load_settings()
        # Seed Fri Jan 5 and Mon Jan 8 (weekend in between)
        bar_dates = [date(2024, 1, 5), date(2024, 1, 8)]

        with session_scope(settings) as session:
            upsert_market_sessions(session, date(2024, 1, 5), date(2024, 1, 8))
            _seed_symbol_and_bars(session, "MSFT", bar_dates)

        with session_scope(settings) as session:
            bars = bars_for_sessions(
                session, "MSFT", n_sessions=2, as_of=date(2024, 1, 8)
            )

        assert len(bars) == 2
        session_dates = [b.session_date for b in bars]
        assert date(2024, 1, 5) in session_dates
        assert date(2024, 1, 8) in session_dates

    def test_returns_only_up_to_as_of(self, migrated_access_db: str) -> None:
        settings = load_settings()
        bar_dates = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5)]

        with session_scope(settings) as session:
            upsert_market_sessions(session, date(2024, 1, 2), date(2024, 1, 5))
            _seed_symbol_and_bars(session, "SPY", bar_dates)

        with session_scope(settings) as session:
            bars = bars_for_sessions(
                session, "SPY", n_sessions=10, as_of=date(2024, 1, 3)
            )

        assert all(b.session_date <= date(2024, 1, 3) for b in bars)
        assert len(bars) == 2


class TestMissingSessionDetection:
    def test_no_missing_sessions_when_all_bars_present(
        self, migrated_access_db: str
    ) -> None:
        settings = load_settings()
        bar_dates = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5)]

        with session_scope(settings) as session:
            upsert_market_sessions(session, date(2024, 1, 2), date(2024, 1, 5))
            _seed_symbol_and_bars(session, "AAPL", bar_dates)
            missing = missing_sessions_for_symbol(
                session, "AAPL", date(2024, 1, 2), date(2024, 1, 5)
            )

        assert missing == []

    def test_detects_missing_sessions_for_partial_symbol(
        self, migrated_access_db: str
    ) -> None:
        settings = load_settings()
        # Only seed Jan 2 and Jan 5 — Jan 3 and 4 are missing
        bar_dates = [date(2024, 1, 2), date(2024, 1, 5)]

        with session_scope(settings) as session:
            upsert_market_sessions(session, date(2024, 1, 2), date(2024, 1, 5))
            _seed_symbol_and_bars(session, "NVDA", bar_dates)
            missing = missing_sessions_for_symbol(
                session, "NVDA", date(2024, 1, 2), date(2024, 1, 5)
            )

        missing_dates = {m.session_date for m in missing}
        assert date(2024, 1, 3) in missing_dates
        assert date(2024, 1, 4) in missing_dates
        assert date(2024, 1, 2) not in missing_dates
        assert date(2024, 1, 5) not in missing_dates
        assert all(isinstance(m, MissingSessionInfo) for m in missing)

    def test_all_sessions_missing_for_symbol_with_no_bars(
        self, migrated_access_db: str
    ) -> None:
        settings = load_settings()

        with session_scope(settings) as session:
            upsert_market_sessions(session, date(2024, 1, 2), date(2024, 1, 5))
            missing = missing_sessions_for_symbol(
                session, "TSLA", date(2024, 1, 2), date(2024, 1, 5)
            )

        assert len(missing) == 4  # 4 trading days, all missing

    def test_empty_range_returns_no_missing(self, migrated_access_db: str) -> None:
        """Weekend date range has no sessions — nothing to be missing."""
        settings = load_settings()
        with session_scope(settings) as session:
            upsert_market_sessions(session, date(2024, 1, 6), date(2024, 1, 7))
            missing = missing_sessions_for_symbol(
                session, "AAPL", date(2024, 1, 6), date(2024, 1, 7)
            )
        assert missing == []

    def test_symbol_missing_exchange_set_correctly(
        self, migrated_access_db: str
    ) -> None:
        settings = load_settings()

        with session_scope(settings) as session:
            upsert_market_sessions(session, date(2024, 1, 2), date(2024, 1, 2))
            missing = missing_sessions_for_symbol(
                session, "AMD", date(2024, 1, 2), date(2024, 1, 2)
            )

        assert len(missing) == 1
        assert missing[0].exchange == "XNYS"
        assert missing[0].symbol == "AMD"
        assert missing[0].session_date == date(2024, 1, 2)


# ---------------------------------------------------------------------------
# Integration tests: migration assertions for Phase 2 Plan 02 schema
# ---------------------------------------------------------------------------


class TestPhase2Plan02MigrationSchema:
    def test_migration_creates_market_sessions_table(
        self, migrated_access_db: str
    ) -> None:
        from sqlalchemy import inspect

        from trading_platform.db.session import get_engine

        settings = load_settings()
        inspector = inspect(get_engine(settings))

        assert "market_sessions" in inspector.get_table_names()

        cols = {c["name"] for c in inspector.get_columns("market_sessions")}
        assert cols >= {
            "id",
            "exchange",
            "session_date",
            "market_open",
            "market_close",
            "early_close",
            "created_at",
            "updated_at",
        }

        uq = {uc["name"] for uc in inspector.get_unique_constraints("market_sessions")}
        assert "uq_market_sessions_exchange_date" in uq

    def test_migration_adds_enriched_symbol_columns(
        self, migrated_access_db: str
    ) -> None:
        from sqlalchemy import inspect

        from trading_platform.db.session import get_engine

        settings = load_settings()
        inspector = inspect(get_engine(settings))

        cols = {c["name"] for c in inspector.get_columns("symbols")}
        assert cols >= {
            "list_date",
            "currency_name",
            "cik",
            "composite_figi",
            "share_class_figi",
            "metadata_provider",
        }
