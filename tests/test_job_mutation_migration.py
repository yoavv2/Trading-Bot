from __future__ import annotations

import os
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from sqlalchemy import inspect

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alembic import command
from scripts.migrate import build_alembic_config

from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.db.models import JobMutation
from trading_platform.db.session import clear_engine_cache, get_engine


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
def migrated_job_mutation_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    database_name = f"job_mutation_{uuid.uuid4().hex[:8]}"
    admin_params = _admin_connection_settings()

    try:
        with _connect_admin(admin_params) as connection:
            connection.execute(f'CREATE DATABASE "{database_name}"')
    except psycopg.Error as exc:  # pragma: no cover - exercised when Postgres is unavailable
        pytest.fail(f"PostgreSQL is required for job mutation migration tests: {exc}")

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


def test_job_mutation_orm_schema_matches_persistence_contract() -> None:
    assert JobMutation.__tablename__ == "job_mutations"
    assert set(JobMutation.__table__.columns.keys()) == {
        "id",
        "endpoint_id",
        "idempotency_key",
        "request_fingerprint",
        "job_id",
        "created_at",
        "updated_at",
    }

    unique_constraints = {
        constraint.name: tuple(constraint.columns.keys())
        for constraint in JobMutation.__table__.constraints
    }
    assert unique_constraints["uq_job_mutations_endpoint_key"] == ("endpoint_id", "idempotency_key")
    indexes = {index.name: tuple(index.columns.keys()) for index in JobMutation.__table__.indexes}
    assert indexes["ix_job_mutations_job_id"] == ("job_id",)


def test_phase18_migration_creates_job_mutation_schema(migrated_job_mutation_db: str) -> None:
    inspector = inspect(get_engine(load_settings()))

    assert "job_mutations" in inspector.get_table_names()
    assert {column["name"] for column in inspector.get_columns("job_mutations")} == {
        "id",
        "endpoint_id",
        "idempotency_key",
        "request_fingerprint",
        "job_id",
        "created_at",
        "updated_at",
    }
    unique_constraints = {
        constraint["name"]: tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("job_mutations")
    }
    assert unique_constraints["uq_job_mutations_endpoint_key"] == ("endpoint_id", "idempotency_key")
    indexes = {
        index["name"]: tuple(index["column_names"])
        for index in inspector.get_indexes("job_mutations")
    }
    assert indexes["ix_job_mutations_job_id"] == ("job_id",)
    foreign_keys = inspector.get_foreign_keys("job_mutations")
    assert foreign_keys == [
        {
            "name": "fk_job_mutations_job_id_jobs",
            "constrained_columns": ["job_id"],
            "referred_schema": None,
            "referred_table": "jobs",
            "referred_columns": ["id"],
            "options": {"ondelete": "RESTRICT"},
            "comment": None,
        }
    ]
