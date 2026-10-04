"""Phase 20 migration 0021 tests: domain_conflict enum value, non-unique
strategy_runs.job_id index, market_data_ingestion_runs.job_id FK, and
jobs.retry_of_job_id UNIQUE FK (D-04, D-07, D-16, D-17)."""

from __future__ import annotations

import os
import sys
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import psycopg
import pytest
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import IntegrityError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alembic import command
from alembic.script import ScriptDirectory
from scripts.migrate import build_alembic_config

from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.db.models import (
    Job,
    MarketDataIngestionRun,
    Strategy,
    StrategyRun,
)
from trading_platform.db.session import clear_engine_cache, get_engine, session_scope
from trading_platform.jobs.dependencies import submit_job


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
def migrated_phase20_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    database_name = f"phase20_ops_safety_{uuid.uuid4().hex[:8]}"
    admin_params = _admin_connection_settings()

    try:
        with _connect_admin(admin_params) as connection:
            connection.execute(f'CREATE DATABASE "{database_name}"')
    except psycopg.Error as exc:  # pragma: no cover - exercised when Postgres is unavailable
        pytest.fail(f"PostgreSQL is required for Phase 20 migration tests: {exc}")

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


def _create_job(*, job_type: str = "phase20_probe") -> uuid.UUID:
    with session_scope(load_settings()) as session:
        job = Job(job_type=job_type, payload={})
        session.add(job)
        session.flush()
        return job.id


def _create_strategy() -> uuid.UUID:
    with session_scope(load_settings()) as session:
        strategy = Strategy(
            strategy_id=f"phase20-probe-{uuid.uuid4().hex[:8]}",
            display_name="Phase 20 Probe Strategy",
            config_reference="phase20_probe.yaml",
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


def test_domain_conflict_enum_value_at_head(migrated_phase20_db: str) -> None:
    settings = load_settings()
    with session_scope(settings) as session:
        enum_values = set(
            session.execute(
                text("SELECT unnest(enum_range(NULL::job_failure_reason))::text")
            ).scalars()
        )
    assert enum_values == {
        "handler_error",
        "worker_lost",
        "lease_expired",
        "cancellation_timeout",
        "config_invalid",
        "domain_conflict",
    }


def test_strategy_runs_job_id_is_indexed_not_unique(migrated_phase20_db: str) -> None:
    settings = load_settings()
    inspector = inspect(get_engine(settings))

    unique_constraints = {
        constraint["name"] for constraint in inspector.get_unique_constraints("strategy_runs")
    }
    assert "uq_strategy_runs_job_id" not in unique_constraints

    indexes = {index["name"]: tuple(index["column_names"]) for index in inspector.get_indexes("strategy_runs")}
    assert indexes["ix_strategy_runs_job_id"] == ("job_id",)


def test_two_strategy_runs_may_share_one_job_id(migrated_phase20_db: str) -> None:
    """D-07/D-08: multiple StrategyRun rows may link to the same Job (e.g. a
    paper-session Job that creates a paper_execution run plus an internal
    reconciliation run)."""

    settings = load_settings()
    strategy_id = _create_strategy()
    job_id = _create_job()

    _create_strategy_run(strategy_id=strategy_id, job_id=job_id)
    _create_strategy_run(strategy_id=strategy_id, job_id=job_id)

    with session_scope(settings) as session:
        count = session.execute(
            select(func.count()).select_from(StrategyRun).where(StrategyRun.job_id == job_id)
        ).scalar_one()
    assert count == 2


def test_market_data_ingestion_runs_job_id_fk_and_index(migrated_phase20_db: str) -> None:
    settings = load_settings()
    inspector = inspect(get_engine(settings))

    columns = {column["name"]: column for column in inspector.get_columns("market_data_ingestion_runs")}
    assert columns["job_id"]["nullable"] is True

    foreign_keys = {fk["name"]: fk for fk in inspector.get_foreign_keys("market_data_ingestion_runs")}
    job_fk = foreign_keys["fk_market_data_ingestion_runs_job_id_jobs"]
    assert job_fk["constrained_columns"] == ["job_id"]
    assert job_fk["referred_table"] == "jobs"
    assert job_fk["referred_columns"] == ["id"]
    assert job_fk["options"]["ondelete"] == "SET NULL"

    indexes = {
        index["name"]: tuple(index["column_names"])
        for index in inspector.get_indexes("market_data_ingestion_runs")
    }
    assert indexes["ix_market_data_ingestion_runs_job_id"] == ("job_id",)

    job_id = _create_job()
    now = datetime.now(UTC)
    with session_scope(settings) as session:
        run = MarketDataIngestionRun(
            provider="polygon",
            from_date=date(2024, 1, 1),
            to_date=date(2024, 1, 2),
            started_at=now,
            job_id=job_id,
        )
        session.add(run)
        session.flush()
        run_id = run.id

    with session_scope(settings) as session:
        session.execute(text("DELETE FROM jobs WHERE id = :job_id"), {"job_id": job_id})

    with session_scope(settings) as session:
        run = session.get(MarketDataIngestionRun, run_id)
        assert run is not None
        assert run.job_id is None


def test_retry_of_job_id_unique_constraint(migrated_phase20_db: str) -> None:
    settings = load_settings()
    parent_job_id = _create_job()
    _create_job(job_type="phase20_retry_a")

    with session_scope(settings) as session:
        retry_a = Job(job_type="phase20_retry_a", payload={}, retry_of_job_id=parent_job_id)
        session.add(retry_a)
        session.flush()

    with pytest.raises(IntegrityError) as exc_info:
        with session_scope(settings) as session:
            retry_b = Job(job_type="phase20_retry_b", payload={}, retry_of_job_id=parent_job_id)
            session.add(retry_b)
            session.flush()

    assert exc_info.value.orig is not None
    assert exc_info.value.orig.diag.constraint_name == "uq_jobs_retry_of_job_id"

    # NULL retry_of_job_id is allowed on many rows.
    with session_scope(settings) as session:
        session.add(Job(job_type="phase20_no_retry_a", payload={}))
        session.add(Job(job_type="phase20_no_retry_b", payload={}))
        session.flush()


def test_submit_job_persists_retry_of_job_id(migrated_phase20_db: str) -> None:
    parent_job_id = _create_job()

    retry_job_id = submit_job(
        job_type="backtest",
        payload={"strategy_id": "trend_following_daily"},
        retry_of_job_id=parent_job_id,
    )
    with session_scope(load_settings()) as session:
        retry_job = session.get(Job, retry_job_id)
        assert retry_job is not None
        assert retry_job.retry_of_job_id == parent_job_id

    fresh_job_id = submit_job(
        job_type="backtest",
        payload={"strategy_id": "trend_following_daily"},
    )
    with session_scope(load_settings()) as session:
        fresh_job = session.get(Job, fresh_job_id)
        assert fresh_job is not None
        assert fresh_job.retry_of_job_id is None


def test_phase20_downgrade_and_reupgrade(migrated_phase20_db: str) -> None:
    config = build_alembic_config()
    command.downgrade(config, "0020_phase19_job_operations")
    clear_settings_cache()
    clear_engine_cache()

    inspector = inspect(get_engine(load_settings()))

    strategy_run_uniques = {
        constraint["name"] for constraint in inspector.get_unique_constraints("strategy_runs")
    }
    assert "uq_strategy_runs_job_id" in strategy_run_uniques
    strategy_run_indexes = {index["name"] for index in inspector.get_indexes("strategy_runs")}
    assert "ix_strategy_runs_job_id" not in strategy_run_indexes

    jobs_columns = {column["name"] for column in inspector.get_columns("jobs")}
    assert "retry_of_job_id" not in jobs_columns

    market_data_columns = {
        column["name"] for column in inspector.get_columns("market_data_ingestion_runs")
    }
    assert "job_id" not in market_data_columns

    command.upgrade(config, "head")
    clear_settings_cache()
    clear_engine_cache()

    inspector = inspect(get_engine(load_settings()))

    strategy_run_uniques = {
        constraint["name"] for constraint in inspector.get_unique_constraints("strategy_runs")
    }
    assert "uq_strategy_runs_job_id" not in strategy_run_uniques
    strategy_run_indexes = {index["name"] for index in inspector.get_indexes("strategy_runs")}
    assert "ix_strategy_runs_job_id" in strategy_run_indexes

    jobs_columns = {column["name"] for column in inspector.get_columns("jobs")}
    assert "retry_of_job_id" in jobs_columns
    jobs_uniques = {constraint["name"] for constraint in inspector.get_unique_constraints("jobs")}
    assert "uq_jobs_retry_of_job_id" in jobs_uniques

    market_data_columns = {
        column["name"] for column in inspector.get_columns("market_data_ingestion_runs")
    }
    assert "job_id" in market_data_columns


def test_phase20_downgrade_refuses_when_a_job_links_multiple_strategy_runs(
    migrated_phase20_db: str,
) -> None:
    """WR-A-04: a paper-session Job may link two strategy_runs (D-07/D-08), which
    the pre-0021 UNIQUE(job_id) constraint cannot hold. The downgrade must
    refuse with a clear error and leave the schema (and rows) untouched."""

    strategy_id = _create_strategy()
    job_id = _create_job()
    _create_strategy_run(strategy_id=strategy_id, job_id=job_id)
    _create_strategy_run(strategy_id=strategy_id, job_id=job_id)
    clear_settings_cache()
    clear_engine_cache()

    with pytest.raises(RuntimeError, match="Cannot downgrade past 0021_phase20_operations_safety"):
        command.downgrade(build_alembic_config(), "0020_phase19_job_operations")

    clear_settings_cache()
    clear_engine_cache()
    settings = load_settings()
    inspector = inspect(get_engine(settings))
    assert "ix_strategy_runs_job_id" in {
        index["name"] for index in inspector.get_indexes("strategy_runs")
    }
    assert "retry_of_job_id" in {column["name"] for column in inspector.get_columns("jobs")}
    with session_scope(settings) as session:
        # Head-agnostic (20.1-01): the whole downgrade runs in one transaction
        # (no transaction_per_migration), so the refusal by 0021 rolls back any
        # later migration's downgrade too and the version stays at the head.
        assert session.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            ScriptDirectory.from_config(build_alembic_config()).get_current_head()
        )
        assert (
            session.execute(
                select(func.count()).select_from(StrategyRun).where(StrategyRun.job_id == job_id)
            ).scalar_one()
            == 2
        )


def test_orm_metadata_matches_phase20_migration() -> None:
    strategy_run_job_id = StrategyRun.__table__.c.job_id
    assert strategy_run_job_id.unique is not True
    assert strategy_run_job_id.index is True

    job_retry_of_job_id = Job.__table__.c.retry_of_job_id
    assert job_retry_of_job_id.unique is True

    market_data_job_id = MarketDataIngestionRun.__table__.c.job_id
    assert market_data_job_id.index is True
