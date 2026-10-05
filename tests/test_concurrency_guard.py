from __future__ import annotations

import logging
import sys
import uuid
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import psycopg
import pytest
from sqlalchemy.exc import OperationalError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.db.session import clear_engine_cache, get_engine
from trading_platform.services import concurrency_guard
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


# ---------------------------------------------------------------------------
# SAF-12: a failing unlock never leaks the lock and never masks the body's error
# ---------------------------------------------------------------------------


class _UnlockFailingConnection:
    """Delegates everything to the real connection but fails ``pg_advisory_unlock``."""

    def __init__(self, real: Any, calls: list[str]) -> None:
        self._real = real
        self._calls = calls

    def execution_options(self, *args: Any, **kwargs: Any) -> _UnlockFailingConnection:
        self._real = self._real.execution_options(*args, **kwargs)
        return self

    def execute(self, statement: Any, *args: Any, **kwargs: Any) -> Any:
        if "pg_advisory_unlock" in str(statement):
            self._calls.append("unlock_failed")
            raise OperationalError("SELECT pg_advisory_unlock", {}, Exception("driver down"))
        return self._real.execute(statement, *args, **kwargs)

    def invalidate(self, *args: Any, **kwargs: Any) -> Any:
        self._calls.append("invalidate")
        return self._real.invalidate(*args, **kwargs)

    def close(self) -> Any:
        self._calls.append("close")
        return self._real.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


class _FailingUnlockEngine:
    def __init__(self, real: Any, calls: list[str]) -> None:
        self._real = real
        self._calls = calls

    def connect(self) -> _UnlockFailingConnection:
        return _UnlockFailingConnection(self._real.connect(), self._calls)


def _install_failing_unlock(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    real_engine = get_engine(load_settings())
    monkeypatch.setattr(
        concurrency_guard, "get_engine", lambda _settings: _FailingUnlockEngine(real_engine, calls)
    )
    return calls


def _lock_is_free_for_other_sessions(database_name: str, key: int) -> bool:
    """Probe from a separate raw session: only a released lock can be taken."""

    probe = _connect_raw(database_name)
    try:
        with probe.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", (key,))
            (free,) = cursor.fetchone()
            if free:
                cursor.execute("SELECT pg_advisory_unlock(%s)", (key,))
        return bool(free)
    finally:
        probe.close()


def test_unlock_failure_invalidates_and_preserves_the_body_exception(
    advisory_lock_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = load_settings()
    strategy_id = "trend_following_daily"
    session_date = date(2024, 1, 5)
    key = advisory_lock_key(strategy_id, session_date)
    calls = _install_failing_unlock(monkeypatch)

    with pytest.raises(ValueError, match="body"):
        with session_run_lock(
            strategy_id=strategy_id, session_date=session_date, settings=settings
        ):
            raise ValueError("body")

    # The unlock error did not replace the body's error; the connection was invalidated
    # and still closed (in that order).
    assert calls == ["unlock_failed", "invalidate", "close"]
    monkeypatch.setattr(concurrency_guard, "get_engine", get_engine)
    assert _lock_is_free_for_other_sessions(advisory_lock_db, key)
    with session_run_lock(strategy_id=strategy_id, session_date=session_date, settings=settings):
        pass  # a fresh acquisition succeeds immediately


def test_unlock_failure_after_normal_exit_releases_the_lock(
    advisory_lock_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = load_settings()
    strategy_id = "trend_following_daily"
    session_date = date(2024, 1, 5)
    key = advisory_lock_key(strategy_id, session_date)
    calls = _install_failing_unlock(monkeypatch)

    # A handler on the guard's own logger: other tests call configure_logging(), which clears
    # the root handlers caplog relies on, so caplog is order-dependent in the full suite.
    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    capture = _Capture(level=logging.ERROR)
    guard_logger = concurrency_guard.logger
    previous_level = guard_logger.level
    guard_logger.addHandler(capture)
    guard_logger.setLevel(logging.ERROR)
    try:
        with session_run_lock(
            strategy_id=strategy_id, session_date=session_date, settings=settings
        ):
            pass  # no exception escapes the failed unlock
    finally:
        guard_logger.removeHandler(capture)
        guard_logger.setLevel(previous_level)

    assert calls == ["unlock_failed", "invalidate", "close"]
    assert any("concurrent_run_lock_unlock_failed" in r.getMessage() for r in records)
    monkeypatch.setattr(concurrency_guard, "get_engine", get_engine)
    assert _lock_is_free_for_other_sessions(advisory_lock_db, key)
    with session_run_lock(strategy_id=strategy_id, session_date=session_date, settings=settings):
        pass


def test_lock_denied_closes_without_unlock(
    advisory_lock_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = load_settings()
    strategy_id = "trend_following_daily"
    session_date = date(2024, 1, 5)
    with session_run_lock(strategy_id=strategy_id, session_date=session_date, settings=settings):
        calls = _install_failing_unlock(monkeypatch)
        with pytest.raises(ConcurrentRunLockedError):
            with session_run_lock(
                strategy_id=strategy_id, session_date=session_date, settings=settings
            ):
                pytest.fail("denied acquisition must not yield")
        # No unlock was attempted (so no failure/invalidate), the connection was closed.
        assert calls == ["close"]
        monkeypatch.setattr(concurrency_guard, "get_engine", get_engine)
