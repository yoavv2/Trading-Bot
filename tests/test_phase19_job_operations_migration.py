"""Phase 19 migration 0020 tests: strategy_runs.job_id link + config_invalid enum value."""

from __future__ import annotations

import os
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import IntegrityError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alembic import command
from scripts.migrate import build_alembic_config

from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.db.models import Job, Strategy, StrategyRun
from trading_platform.db.session import clear_engine_cache, get_engine, session_scope


def _admin_connection_settings() -> dict[str, str]:
    return {
        "host": os.getenv("TRADING_PLATFORM_DATABASE__HOST", "localhost"),
        "port": os.getenv("TRADING_PLATFORM_DATABASE__PORT", "5432"),
        "user": os.getenv("TRADING_PLATFORM_DATABASE__USER", "trading_platform"),
        "password": os.getenv("TRADING_PLATFORM_DATABASE__PASSWORD", "trading_platform"),
        "dbname": os.getenv("TRADING_PLATFORM_ADMIN_DB", "postgres"),
    }


def _connect_admin(params: dict[str, str]) -> psycopg.Connection:
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


def _upgrade_to_revision(revision: str) -> None:
    clear_settings_cache()
    clear_engine_cache()
    command.upgrade(build_alembic_config(), revision)


@pytest.fixture()
def migrated_phase19_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    database_name = f"phase19_job_ops_{uuid.uuid4().hex[:8]}"
    admin_params = _admin_connection_settings()

    try:
        with _connect_admin(admin_params) as connection:
            connection.execute(f'CREATE DATABASE "{database_name}"')
    except psycopg.Error as exc:  # pragma: no cover - exercised when Postgres is unavailable
        pytest.fail(f"PostgreSQL is required for Phase 19 migration tests: {exc}")

    _set_database_env(monkeypatch, database_name)
    _upgrade_to_revision("head")

    try:
        yield database_name
    finally:
        clear_settings_cache()
        clear_engine_cache()
        with _connect_admin(admin_params) as connection:
            connection.execute(
                """
                SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE datname = %s
                  AND usename = current_user
                  AND pid <> pg_backend_pid()
                """,
                (database_name,),
            )
            connection.execute(f'DROP DATABASE IF EXISTS "{database_name}"')


def _create_job() -> uuid.UUID:
    with session_scope(load_settings()) as session:
        job = Job(job_type="phase19_probe", payload={})
        session.add(job)
        session.flush()
        return job.id


def _create_strategy() -> uuid.UUID:
    with session_scope(load_settings()) as session:
        strategy = Strategy(
            strategy_id=f"phase19-probe-{uuid.uuid4().hex[:8]}",
            display_name="Phase 19 Probe Strategy",
            config_reference="phase19_probe.yaml",
        )
        session.add(strategy)
        session.flush()
        return strategy.id


def _create_strategy_run(*, strategy_id: uuid.UUID, job_id: uuid.UUID | None) -> uuid.UUID:
    with session_scope(load_settings()) as session:
        run = StrategyRun(strategy_id=strategy_id, job_id=job_id)
        session.add(run)
        session.flush()
        return run.id


def test_phase19_migration_adds_job_link_and_config_invalid(migrated_phase19_db: str) -> None:
    settings = load_settings()
    inspector = inspect(get_engine(settings))

    columns = {column["name"]: column for column in inspector.get_columns("strategy_runs")}
    assert columns["job_id"]["nullable"] is True

    foreign_keys = {fk["name"]: fk for fk in inspector.get_foreign_keys("strategy_runs")}
    job_fk = foreign_keys["fk_strategy_runs_job_id_jobs"]
    assert job_fk["constrained_columns"] == ["job_id"]
    assert job_fk["referred_table"] == "jobs"
    assert job_fk["referred_columns"] == ["id"]
    assert job_fk["options"]["ondelete"] == "SET NULL"

    unique_constraints = {
        constraint["name"]: tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("strategy_runs")
    }
    assert unique_constraints["uq_strategy_runs_job_id"] == ("job_id",)

    with session_scope(settings) as session:
        enum_values = set(
            session.execute(
                text("SELECT unnest(enum_range(NULL::job_failure_reason))::text")
            ).scalars()
        )
    assert "config_invalid" in enum_values


def test_strategy_runs_job_id_unique_rejects_second_run_for_same_job(
    migrated_phase19_db: str,
) -> None:
    settings = load_settings()
    strategy_id = _create_strategy()
    job_id = _create_job()

    _create_strategy_run(strategy_id=strategy_id, job_id=job_id)

    with pytest.raises(IntegrityError):
        _create_strategy_run(strategy_id=strategy_id, job_id=job_id)

    _create_strategy_run(strategy_id=strategy_id, job_id=None)
    _create_strategy_run(strategy_id=strategy_id, job_id=None)

    with session_scope(settings) as session:
        null_job_id_count = session.execute(
            select(func.count())
            .select_from(StrategyRun)
            .where(StrategyRun.job_id.is_(None))
        ).scalar_one()
    assert null_job_id_count == 2


def test_deleting_job_sets_strategy_run_job_id_null(migrated_phase19_db: str) -> None:
    settings = load_settings()
    strategy_id = _create_strategy()
    job_id = _create_job()
    run_id = _create_strategy_run(strategy_id=strategy_id, job_id=job_id)

    with session_scope(settings) as session:
        session.execute(text("DELETE FROM jobs WHERE id = :job_id"), {"job_id": job_id})

    with session_scope(settings) as session:
        run = session.get(StrategyRun, run_id)
        assert run is not None
        assert run.job_id is None


def test_phase19_migration_downgrade_and_reupgrade(migrated_phase19_db: str) -> None:
    config = build_alembic_config()
    command.downgrade(config, "0019_phase18_job_idempotency")
    clear_settings_cache()
    clear_engine_cache()

    inspector = inspect(get_engine(load_settings()))
    columns = {column["name"] for column in inspector.get_columns("strategy_runs")}
    assert "job_id" not in columns
    unique_constraints = {
        constraint["name"] for constraint in inspector.get_unique_constraints("strategy_runs")
    }
    assert "uq_strategy_runs_job_id" not in unique_constraints

    with session_scope(load_settings()) as session:
        enum_values = set(
            session.execute(
                text("SELECT unnest(enum_range(NULL::job_failure_reason))::text")
            ).scalars()
        )
    assert "config_invalid" in enum_values

    command.upgrade(config, "head")
    clear_settings_cache()
    clear_engine_cache()

    inspector = inspect(get_engine(load_settings()))
    columns = {column["name"] for column in inspector.get_columns("strategy_runs")}
    assert "job_id" in columns
    unique_constraints = {
        constraint["name"] for constraint in inspector.get_unique_constraints("strategy_runs")
    }
    assert "uq_strategy_runs_job_id" in unique_constraints
    foreign_keys = {fk["name"] for fk in inspector.get_foreign_keys("strategy_runs")}
    assert "fk_strategy_runs_job_id_jobs" in foreign_keys


def test_orm_metadata_matches_migration() -> None:
    column = StrategyRun.__table__.c.job_id
    assert column.nullable is True
    assert column.unique is True

    foreign_keys = list(column.foreign_keys)
    assert len(foreign_keys) == 1
    foreign_key = foreign_keys[0]
    assert foreign_key.column.table.name == "jobs"
    assert foreign_key.column.name == "id"
    assert foreign_key.ondelete == "SET NULL"
