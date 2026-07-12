from __future__ import annotations

import sys
import uuid
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import psycopg
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.db.session import clear_engine_cache
from trading_platform.services.concurrency_guard import (
    CONCURRENT_RUN_LOCK_EXIT_CODE,
    ConcurrentRunLockedError,
    advisory_lock_key,
    session_run_lock,
)


def _admin_connection_settings() -> dict[str, str]:
    return {
        "host": "localhost",
        "port": "5432",
        "user": "trading_platform",
        "password": "trading_platform",
        "dbname": "postgres",
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


def _connect_raw(database_name: str) -> psycopg.Connection:
    params = _admin_connection_settings()
    return psycopg.connect(
        host=params["host"],
        port=params["port"],
        user=params["user"],
        password=params["password"],
        dbname=database_name,
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
def advisory_lock_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """A dedicated, unmigrated Postgres database for advisory-lock tests.

    No schema migration is required: ``session_run_lock`` exercises only
    ``pg_try_advisory_lock``/``pg_advisory_unlock``, which are database-wide
    functions independent of any table.
    """
    database_name = f"concurrency_guard_{uuid.uuid4().hex[:8]}"
    admin_params = _admin_connection_settings()

    try:
        with _connect_admin(admin_params) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f'CREATE DATABASE "{database_name}"')
    except psycopg.Error as exc:
        pytest.fail(
            "PostgreSQL is required for tests/test_concurrency_guard.py. "
            f"Connection error: {exc}"
        )

    _set_database_env(monkeypatch, database_name)
    clear_settings_cache()
    clear_engine_cache()

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


# ---------------------------------------------------------------------------
# Pure unit tests: key derivation + typed error (no DB)
# ---------------------------------------------------------------------------


class TestAdvisoryLockKey:
    def test_deterministic_for_same_inputs(self) -> None:
        first = advisory_lock_key("trend_following_daily", date(2024, 1, 5))
        second = advisory_lock_key("trend_following_daily", date(2024, 1, 5))

        assert first == second

    def test_varies_by_session_date(self) -> None:
        key_a = advisory_lock_key("trend_following_daily", date(2024, 1, 5))
        key_b = advisory_lock_key("trend_following_daily", date(2024, 1, 6))

        assert key_a != key_b

    def test_fits_signed_bigint_range(self) -> None:
        key = advisory_lock_key("trend_following_daily", date(2024, 1, 5))

        assert -(2**63) <= key <= 2**63 - 1


class TestConcurrentRunLockedError:
    def test_str_names_both_fields(self) -> None:
        err = ConcurrentRunLockedError("trend_following_daily", date(2024, 1, 5))

        message = str(err)

        assert "trend_following_daily" in message
        assert "2024-01-05" in message

    def test_is_exception_subclass_assertable_by_class(self) -> None:
        assert issubclass(ConcurrentRunLockedError, RuntimeError)


def test_concurrent_run_lock_exit_code_is_distinct_nonzero_constant() -> None:
    assert CONCURRENT_RUN_LOCK_EXIT_CODE == 3
    assert CONCURRENT_RUN_LOCK_EXIT_CODE != 0
    assert CONCURRENT_RUN_LOCK_EXIT_CODE != 2  # argparse's usage exit code


# ---------------------------------------------------------------------------
# Integration tests: real Postgres contention + release + crash-release
# ---------------------------------------------------------------------------


def test_session_run_lock_denies_concurrent_acquisition_for_same_tuple(
    advisory_lock_db: str,
) -> None:
    settings = load_settings()
    strategy_id = "trend_following_daily"
    session_date = date(2024, 1, 5)

    with session_run_lock(strategy_id=strategy_id, session_date=session_date, settings=settings):
        with pytest.raises(ConcurrentRunLockedError) as exc_info:
            with session_run_lock(
                strategy_id=strategy_id, session_date=session_date, settings=settings
            ):
                pytest.fail("second acquisition must raise before yielding")

    assert exc_info.value.strategy_id == strategy_id
    assert exc_info.value.session_date == session_date


def test_session_run_lock_allows_concurrent_acquisition_for_different_session_date(
    advisory_lock_db: str,
) -> None:
    settings = load_settings()
    strategy_id = "trend_following_daily"

    with session_run_lock(
        strategy_id=strategy_id, session_date=date(2024, 1, 5), settings=settings
    ):
        with session_run_lock(
            strategy_id=strategy_id, session_date=date(2024, 1, 6), settings=settings
        ):
            pass  # second, disjoint tuple must acquire without contention


def test_session_run_lock_releases_on_normal_exit(advisory_lock_db: str) -> None:
    settings = load_settings()
    strategy_id = "trend_following_daily"
    session_date = date(2024, 1, 5)

    with session_run_lock(strategy_id=strategy_id, session_date=session_date, settings=settings):
        pass

    with session_run_lock(strategy_id=strategy_id, session_date=session_date, settings=settings):
        pass  # a fresh acquisition after normal exit must succeed


def test_session_run_lock_acquires_cleanly_after_holder_connection_drops(
    advisory_lock_db: str,
) -> None:
    settings = load_settings()
    strategy_id = "trend_following_daily"
    session_date = date(2024, 1, 5)
    lock_key = advisory_lock_key(strategy_id, session_date)

    crashed_connection = _connect_raw(advisory_lock_db)
    with crashed_connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", (lock_key,))
        (acquired,) = cursor.fetchone()
    assert acquired is True

    # Simulate a crash: drop the connection WITHOUT calling pg_advisory_unlock.
    crashed_connection.close()

    with session_run_lock(strategy_id=strategy_id, session_date=session_date, settings=settings):
        pass  # PostgreSQL must have auto-released the lock on connection drop
