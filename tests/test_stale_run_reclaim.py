"""Phase 8 concurrency guard: stale-run detection + reclaim tests (LOCK-04, LOCK-05)."""

from __future__ import annotations

import os
import sys
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import psycopg
import pytest
from alembic import command
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.migrate import build_alembic_config

from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.db.models import (
    ExecutionEvent,
    StrategyRun,
    StrategyRunStatus,
    StrategyRunType,
)
from trading_platform.db.session import clear_engine_cache, session_scope
from trading_platform.services.bootstrap import ensure_strategy_record
from trading_platform.services.stale_runs import find_stale_runs, reclaim_stale_runs
from trading_platform.strategies.registry import build_default_registry

_TIMEOUT_MINUTES = 30
_SESSION_DATE = date(2026, 7, 10)


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
def migrated_stale_reclaim_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    database_name = f"stale_run_reclaim_{uuid.uuid4().hex[:8]}"
    admin_params = _admin_connection_settings()

    try:
        with _connect_admin(admin_params) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f'CREATE DATABASE "{database_name}"')
    except psycopg.Error as exc:
        pytest.fail(
            "PostgreSQL is required for tests/test_stale_run_reclaim.py. "
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


def _seed_run(
    session,
    *,
    strategy_row_id: uuid.UUID,
    status: StrategyRunStatus,
    started_at: datetime,
    session_date: date | None = _SESSION_DATE,
) -> StrategyRun:
    run = StrategyRun(
        strategy_id=strategy_row_id,
        run_type=StrategyRunType.PAPER_EXECUTION,
        status=status,
        trigger_source="test_suite",
        started_at=started_at,
        parameters_snapshot=(
            {"as_of_session": session_date.isoformat()} if session_date is not None else {}
        ),
    )
    session.add(run)
    session.flush()
    return run


def test_find_stale_runs_detects_only_running_past_timeout(
    migrated_stale_reclaim_db: str,
) -> None:
    settings = load_settings()
    registry = build_default_registry(settings)
    strategy = registry.resolve("trend_following_daily")

    now = datetime.now(UTC)

    with session_scope(settings) as session:
        strategy_record = ensure_strategy_record(session, strategy.metadata)
        old_run = _seed_run(
            session,
            strategy_row_id=strategy_record.id,
            status=StrategyRunStatus.RUNNING,
            started_at=now - timedelta(minutes=40),
        )
        fresh_run = _seed_run(
            session,
            strategy_row_id=strategy_record.id,
            status=StrategyRunStatus.RUNNING,
            started_at=now,
        )
        old_succeeded_run = _seed_run(
            session,
            strategy_row_id=strategy_record.id,
            status=StrategyRunStatus.SUCCEEDED,
            started_at=now - timedelta(minutes=40),
        )
        old_run_id = old_run.id
        fresh_run_id = fresh_run.id
        old_succeeded_run_id = old_succeeded_run.id

    with session_scope(settings) as session:
        stale_runs = find_stale_runs(session, timeout_minutes=_TIMEOUT_MINUTES)
        stale_ids = {run.id for run in stale_runs}

    assert old_run_id in stale_ids
    assert fresh_run_id not in stale_ids
    assert old_succeeded_run_id not in stale_ids


def test_reclaim_stale_runs_marks_all_past_threshold_rows_stale_with_audit(
    migrated_stale_reclaim_db: str,
) -> None:
    settings = load_settings()
    registry = build_default_registry(settings)
    strategy = registry.resolve("trend_following_daily")

    now = datetime.now(UTC)
    reclaiming_run_id = uuid.uuid4()

    with session_scope(settings) as session:
        strategy_record = ensure_strategy_record(session, strategy.metadata)
        old_run_one = _seed_run(
            session,
            strategy_row_id=strategy_record.id,
            status=StrategyRunStatus.RUNNING,
            started_at=now - timedelta(minutes=40),
        )
        old_run_two = _seed_run(
            session,
            strategy_row_id=strategy_record.id,
            status=StrategyRunStatus.RUNNING,
            started_at=now - timedelta(minutes=50),
        )
        fresh_run = _seed_run(
            session,
            strategy_row_id=strategy_record.id,
            status=StrategyRunStatus.RUNNING,
            started_at=now,
        )
        old_run_one_id = old_run_one.id
        old_run_two_id = old_run_two.id
        fresh_run_id = fresh_run.id

    with session_scope(settings) as session:
        reclaimed_ids = reclaim_stale_runs(
            session,
            strategy_public_id=strategy.metadata.strategy_id,
            session_date=_SESSION_DATE,
            timeout_minutes=_TIMEOUT_MINUTES,
            reclaiming_run_id=reclaiming_run_id,
        )
        session.commit()

    assert set(reclaimed_ids) == {old_run_one_id, old_run_two_id}

    with session_scope(settings) as session:
        old_one = session.execute(
            select(StrategyRun).where(StrategyRun.id == old_run_one_id)
        ).scalar_one()
        old_two = session.execute(
            select(StrategyRun).where(StrategyRun.id == old_run_two_id)
        ).scalar_one()
        fresh = session.execute(
            select(StrategyRun).where(StrategyRun.id == fresh_run_id)
        ).scalar_one()

        assert old_one.status == StrategyRunStatus.STALE
        assert old_one.completed_at is not None
        assert old_two.status == StrategyRunStatus.STALE
        assert old_two.completed_at is not None
        assert fresh.status == StrategyRunStatus.RUNNING

        events = session.execute(
            select(ExecutionEvent).where(
                ExecutionEvent.strategy_run_id.in_([old_run_one_id, old_run_two_id])
            )
        ).scalars().all()
        assert len(events) == 2
        for event in events:
            assert event.event_type == "paper_run_reclaimed_stale"
            assert event.severity == "warning"
            assert event.blocks_execution is False
            assert event.details["reclaiming_run_id"] == str(reclaiming_run_id)
            assert event.details["session_date"] == _SESSION_DATE.isoformat()
            assert event.details["timeout_minutes"] == _TIMEOUT_MINUTES


def test_reclaim_stale_runs_is_idempotent(migrated_stale_reclaim_db: str) -> None:
    settings = load_settings()
    registry = build_default_registry(settings)
    strategy = registry.resolve("trend_following_daily")

    now = datetime.now(UTC)

    with session_scope(settings) as session:
        strategy_record = ensure_strategy_record(session, strategy.metadata)
        _seed_run(
            session,
            strategy_row_id=strategy_record.id,
            status=StrategyRunStatus.RUNNING,
            started_at=now - timedelta(minutes=40),
        )

    with session_scope(settings) as session:
        first_pass = reclaim_stale_runs(
            session,
            strategy_public_id=strategy.metadata.strategy_id,
            session_date=_SESSION_DATE,
            timeout_minutes=_TIMEOUT_MINUTES,
        )
        session.commit()

    assert len(first_pass) == 1

    with session_scope(settings) as session:
        second_pass = reclaim_stale_runs(
            session,
            strategy_public_id=strategy.metadata.strategy_id,
            session_date=_SESSION_DATE,
            timeout_minutes=_TIMEOUT_MINUTES,
        )
        session.commit()

    assert second_pass == []
