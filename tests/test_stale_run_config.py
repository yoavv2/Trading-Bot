"""Phase 8 concurrency guard: stale-run timeout config + STALE enum migration tests."""

from __future__ import annotations

import os
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from alembic import command
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.migrate import build_alembic_config

from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.db.models import StrategyRun, StrategyRunStatus, StrategyRunType
from trading_platform.db.session import clear_engine_cache, session_scope
from trading_platform.services.bootstrap import ensure_strategy_record
from trading_platform.strategies.registry import build_default_registry


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
def migrated_stale_run_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    database_name = f"stale_run_config_{uuid.uuid4().hex[:8]}"
    admin_params = _admin_connection_settings()

    try:
        with _connect_admin(admin_params) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f'CREATE DATABASE "{database_name}"')
    except psycopg.Error as exc:
        pytest.fail(
            "PostgreSQL is required for tests/test_stale_run_config.py. "
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


def test_stale_run_timeout_minutes_defaults_to_30(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_settings_cache()
    settings = load_settings()

    assert settings.execution.safety.stale_run_timeout_minutes == 30


def test_stale_run_timeout_minutes_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "TRADING_PLATFORM_EXECUTION__SAFETY__STALE_RUN_TIMEOUT_MINUTES", "5"
    )
    clear_settings_cache()
    settings = load_settings()

    assert settings.execution.safety.stale_run_timeout_minutes == 5

    clear_settings_cache()


def test_stale_status_round_trips_against_migrated_db(
    migrated_stale_run_db: str,
) -> None:
    settings = load_settings()
    registry = build_default_registry(settings)
    strategy = registry.resolve("trend_following_daily")

    with session_scope(settings) as session:
        strategy_record = ensure_strategy_record(session, strategy.metadata)
        strategy_run = StrategyRun(
            strategy_id=strategy_record.id,
            run_type=StrategyRunType.PAPER_EXECUTION,
            status=StrategyRunStatus.STALE,
            trigger_source="test_suite",
        )
        session.add(strategy_run)
        session.flush()
        run_id = strategy_run.id

    with session_scope(settings) as session:
        persisted = session.execute(
            select(StrategyRun).where(StrategyRun.id == run_id)
        ).scalar_one()

        assert persisted.status == StrategyRunStatus.STALE
