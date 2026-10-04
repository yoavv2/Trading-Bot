"""Context manager that provisions a throwaway migrated PostgreSQL database.

Used by the 20.1-03 account-truth tests; mirrors the per-module ``migrated_*_db``
fixtures without copying them a fourth time.
"""

from __future__ import annotations

import os
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import psycopg
import pytest
from alembic import command

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.migrate import build_alembic_config

from trading_platform.core.settings import clear_settings_cache
from trading_platform.db.session import clear_engine_cache


def _admin_params() -> dict[str, str]:
    return {
        "host": os.getenv("TRADING_PLATFORM_DATABASE__HOST", "localhost"),
        "port": os.getenv("TRADING_PLATFORM_DATABASE__PORT", "5432"),
        "user": os.getenv("TRADING_PLATFORM_DATABASE__USER", "trading_platform"),
        "password": os.getenv("TRADING_PLATFORM_DATABASE__PASSWORD", "trading_platform"),
        "dbname": os.getenv("TRADING_PLATFORM_ADMIN_DB", "postgres"),
    }


def _connect_admin() -> psycopg.Connection:
    return psycopg.connect(autocommit=True, **_admin_params())


@contextmanager
def migrated_database(monkeypatch: pytest.MonkeyPatch, prefix: str) -> Iterator[str]:
    database_name = f"{prefix}_{uuid.uuid4().hex[:8]}"
    params = _admin_params()
    try:
        with _connect_admin() as connection:
            with connection.cursor() as cursor:
                cursor.execute(f'CREATE DATABASE "{database_name}"')
    except psycopg.Error as exc:
        pytest.fail(f"PostgreSQL is required for {prefix} tests. Connection error: {exc}")

    monkeypatch.setenv("TRADING_PLATFORM_DATABASE__HOST", params["host"])
    monkeypatch.setenv("TRADING_PLATFORM_DATABASE__PORT", params["port"])
    monkeypatch.setenv("TRADING_PLATFORM_DATABASE__USER", params["user"])
    monkeypatch.setenv("TRADING_PLATFORM_DATABASE__PASSWORD", params["password"])
    monkeypatch.setenv("TRADING_PLATFORM_DATABASE__NAME", database_name)
    clear_settings_cache()
    clear_engine_cache()
    command.upgrade(build_alembic_config(), "head")
    try:
        yield database_name
    finally:
        clear_settings_cache()
        clear_engine_cache()
        with _connect_admin() as connection:
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
