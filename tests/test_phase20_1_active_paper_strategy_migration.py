"""Phase 20.1 migration 0022 tests: the active_paper_strategy singleton (PAPER-01).

Two owners must be unrepresentable at the database level (D-01) and the
seeded state must be "no owner" (D-02).
"""

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
from alembic.script import ScriptDirectory
from scripts.migrate import build_alembic_config

from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.db.models import ActivePaperStrategy, Strategy, StrategyRun
from trading_platform.db.session import clear_engine_cache, get_engine, session_scope

REVISION = "0022_phase20_1_active_paper_strategy"
PREVIOUS_REVISION = "0021_phase20_operations_safety"
SINGLETON_CONSTRAINT = "ck_active_paper_strategy_singleton"


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


def _fresh_caches() -> None:
    clear_settings_cache()
    clear_engine_cache()


@pytest.fixture()
def migrated_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    database_name = f"phase20_1_owner_{uuid.uuid4().hex[:8]}"
    params = _admin_connection_settings()
    try:
        with _connect_admin(params) as connection:
            connection.execute(f'CREATE DATABASE "{database_name}"')
    except psycopg.Error as exc:  # pragma: no cover - exercised when Postgres is unavailable
        pytest.fail(f"PostgreSQL is required for Phase 20.1 migration tests: {exc}")

    monkeypatch.setenv("TRADING_PLATFORM_DATABASE__HOST", params["host"])
    monkeypatch.setenv("TRADING_PLATFORM_DATABASE__PORT", params["port"])
    monkeypatch.setenv("TRADING_PLATFORM_DATABASE__USER", params["user"])
    monkeypatch.setenv("TRADING_PLATFORM_DATABASE__PASSWORD", params["password"])
    monkeypatch.setenv("TRADING_PLATFORM_DATABASE__NAME", database_name)
    _fresh_caches()
    command.upgrade(build_alembic_config(), "head")

    try:
        yield database_name
    finally:
        _fresh_caches()
        with _connect_admin(params) as connection:
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


def _create_strategy() -> uuid.UUID:
    with session_scope(load_settings()) as session:
        strategy = Strategy(
            strategy_id=f"owner-probe-{uuid.uuid4().hex[:8]}",
            display_name="Owner Probe",
            config_reference="owner_probe.yaml",
        )
        session.add(strategy)
        session.flush()
        return strategy.id


def _create_run(strategy_id: uuid.UUID) -> uuid.UUID:
    with session_scope(load_settings()) as session:
        run = StrategyRun(strategy_id=strategy_id)
        session.add(run)
        session.flush()
        return run.id


def _row_count() -> int:
    with session_scope(load_settings()) as session:
        return session.execute(select(func.count()).select_from(ActivePaperStrategy)).scalar_one()


def test_chain_is_linear_and_0022_follows_0021(migrated_db: str) -> None:
    script = ScriptDirectory.from_config(build_alembic_config())
    assert len(script.get_heads()) == 1
    revision = script.get_revision(REVISION)
    assert revision is not None
    assert revision.down_revision == PREVIOUS_REVISION


def test_seed_row_is_single_and_has_no_owner(migrated_db: str) -> None:
    with session_scope(load_settings()) as session:
        rows = session.execute(
            text("SELECT id, strategy_id, set_by_run_id, reason, since FROM active_paper_strategy")
        ).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.id == 1
    assert row.strategy_id is None
    assert row.set_by_run_id is None
    assert row.reason == "initial state: no active paper strategy"
    assert row.since is not None


def test_second_row_is_rejected_by_the_database(migrated_db: str) -> None:
    with pytest.raises(IntegrityError, match=SINGLETON_CONSTRAINT):
        with session_scope(load_settings()) as session:
            session.execute(text("INSERT INTO active_paper_strategy (id) VALUES (2)"))
    assert _row_count() == 1


def test_duplicate_id_is_rejected_by_the_database(migrated_db: str) -> None:
    with pytest.raises(IntegrityError, match="pk_active_paper_strategy"):
        with session_scope(load_settings()) as session:
            session.execute(text("INSERT INTO active_paper_strategy (id) VALUES (1)"))
    assert _row_count() == 1


def test_updating_id_to_another_value_is_rejected_by_the_database(migrated_db: str) -> None:
    with pytest.raises(IntegrityError, match=SINGLETON_CONSTRAINT):
        with session_scope(load_settings()) as session:
            session.execute(text("UPDATE active_paper_strategy SET id = 2"))
    with session_scope(load_settings()) as session:
        assert session.execute(select(ActivePaperStrategy.id)).scalar_one() == 1


def test_strategy_id_is_the_only_owner_slot(migrated_db: str) -> None:
    columns = {column["name"] for column in inspect(get_engine(load_settings())).get_columns("active_paper_strategy")}
    owner_like = {name for name in columns if "strategy" in name}
    assert owner_like == {"strategy_id"}


def test_strategy_fk_is_restrict_so_ownership_is_never_silently_dropped(migrated_db: str) -> None:
    strategy_pk = _create_strategy()
    with session_scope(load_settings()) as session:
        session.execute(
            text("UPDATE active_paper_strategy SET strategy_id = :sid"), {"sid": strategy_pk}
        )

    with pytest.raises(IntegrityError, match="fk_active_paper_strategy_strategy_id_strategies"):
        with session_scope(load_settings()) as session:
            session.execute(text("DELETE FROM strategies WHERE id = :sid"), {"sid": strategy_pk})

    with session_scope(load_settings()) as session:
        assert (
            session.execute(select(ActivePaperStrategy.strategy_id)).scalar_one() == strategy_pk
        )


def test_set_by_run_fk_is_set_null_on_run_delete(migrated_db: str) -> None:
    strategy_pk = _create_strategy()
    run_pk = _create_run(strategy_pk)
    with session_scope(load_settings()) as session:
        session.execute(
            text("UPDATE active_paper_strategy SET set_by_run_id = :rid"), {"rid": run_pk}
        )

    with session_scope(load_settings()) as session:
        session.execute(text("DELETE FROM strategy_runs WHERE id = :rid"), {"rid": run_pk})

    with session_scope(load_settings()) as session:
        assert session.execute(select(ActivePaperStrategy.set_by_run_id)).scalar_one() is None
        assert _row_count_in(session) == 1


def _row_count_in(session) -> int:
    return session.execute(select(func.count()).select_from(ActivePaperStrategy)).scalar_one()


def test_downgrade_drops_table_and_reupgrade_restores_the_no_owner_row(migrated_db: str) -> None:
    _fresh_caches()
    command.downgrade(build_alembic_config(), PREVIOUS_REVISION)
    _fresh_caches()
    assert "active_paper_strategy" not in inspect(get_engine(load_settings())).get_table_names()
    with session_scope(load_settings()) as session:
        assert (
            session.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == PREVIOUS_REVISION
        )

    _fresh_caches()
    command.upgrade(build_alembic_config(), REVISION)
    _fresh_caches()
    with session_scope(load_settings()) as session:
        row = session.execute(select(ActivePaperStrategy)).scalar_one()
        assert row.id == 1
        assert row.strategy_id is None


def test_orm_metadata_matches_migration(migrated_db: str) -> None:
    table = ActivePaperStrategy.__table__
    inspector = inspect(get_engine(load_settings()))

    db_columns = {column["name"]: column for column in inspector.get_columns("active_paper_strategy")}
    assert set(db_columns) == {c.name for c in table.columns}
    for column in table.columns:
        assert db_columns[column.name]["nullable"] == column.nullable, column.name

    db_fks = {
        fk["constrained_columns"][0]: (fk["referred_table"], fk["options"].get("ondelete"), fk["name"])
        for fk in inspector.get_foreign_keys("active_paper_strategy")
    }
    orm_fks = {
        column.name: (fk.column.table.name, fk.ondelete)
        for column in table.columns
        for fk in column.foreign_keys
    }
    assert {name: value[:2] for name, value in db_fks.items()} == orm_fks
    assert db_fks["strategy_id"] == (
        "strategies",
        "RESTRICT",
        "fk_active_paper_strategy_strategy_id_strategies",
    )
    assert db_fks["set_by_run_id"] == (
        "strategy_runs",
        "SET NULL",
        "fk_active_paper_strategy_set_by_run_id_strategy_runs",
    )

    db_checks = {check["name"] for check in inspector.get_check_constraints("active_paper_strategy")}
    orm_checks = {
        str(constraint.name)
        for constraint in table.constraints
        if constraint.__class__.__name__ == "CheckConstraint"
    }
    assert db_checks == {SINGLETON_CONSTRAINT}
    assert orm_checks == db_checks
