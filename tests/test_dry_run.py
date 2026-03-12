from __future__ import annotations

import os
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alembic import command
from scripts.dry_run import main as dry_run_main
from scripts.migrate import build_alembic_config
from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.db.models import StrategyRun, StrategyRunStatus
from trading_platform.db.session import clear_engine_cache, session_scope


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


def _upgrade_to_head() -> None:
    clear_settings_cache()
    clear_engine_cache()
    command.upgrade(build_alembic_config(), "head")


@pytest.fixture()
def migrated_database(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    database_name = f"phase1_dry_run_{uuid.uuid4().hex[:8]}"
    admin_params = _admin_connection_settings()

    try:
        with _connect_admin(admin_params) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f'CREATE DATABASE "{database_name}"')
    except psycopg.Error as exc:  # pragma: no cover - exercised when local Postgres is unavailable
        pytest.fail(
            "PostgreSQL is required for tests/test_dry_run.py. "
            "Start the local db service first (for example `docker compose up -d db`). "
            f"Connection error: {exc}"
        )

    _set_database_env(monkeypatch, database_name)
    _upgrade_to_head()

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


def test_dry_run_script_persists_strategy_run(
    migrated_database: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = dry_run_main(["--strategy", "trend_following_daily"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "dry_run_started" in captured.out
    assert "dry_run_succeeded" in captured.out

    with session_scope(load_settings()) as session:
        persisted_run = session.execute(select(StrategyRun)).scalar_one()

    assert persisted_run.status == StrategyRunStatus.SUCCEEDED
    assert persisted_run.trigger_source == "dry_run_script"
    assert persisted_run.result_summary["strategy"]["strategy_id"] == "trend_following_daily"
    assert persisted_run.result_summary["details"]["universe_size"] == 10


def test_dry_run_script_fails_cleanly_for_unknown_strategy(
    migrated_database: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = dry_run_main(["--strategy", "unknown_strategy"])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "Unknown strategy 'unknown_strategy'" in captured.err

    with session_scope(load_settings()) as session:
        persisted_runs = session.execute(select(StrategyRun)).scalars().all()

    assert persisted_runs == []
