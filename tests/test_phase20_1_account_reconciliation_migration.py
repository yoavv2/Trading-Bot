"""Phase 20.1 migration 0024 tests: account_reconciliation_runs (ACCT-01, R-31, J-3).

The table is the owner-less home of account-level reconciliation results: no
owner column, no foreign key to anything but the originating Job, and closed
scope/status sets enforced by the database.
"""

from __future__ import annotations

import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alembic import command
from alembic.script import ScriptDirectory
from scripts.migrate import build_alembic_config
from tests.support.migrated_db import migrated_database

from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.db.models import AccountReconciliationRun, Job
from trading_platform.db.session import clear_engine_cache, get_engine, session_scope

REVISION = "0024_phase20_1_account_reconciliation_runs"
PREVIOUS_REVISION = "0023_phase20_1_order_submission_attempts"
TABLE = "account_reconciliation_runs"
SCOPE_CHECK = "ck_account_reconciliation_runs_scope"
STATUS_CHECK = "ck_account_reconciliation_runs_status"


def _fresh_caches() -> None:
    clear_settings_cache()
    clear_engine_cache()


@pytest.fixture()
def migrated_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    with migrated_database(monkeypatch, "phase20_1_acct_recon") as name:
        yield name


def _insert(*, scope: str = "account", status: str = "pending", job_id: uuid.UUID | None = None):
    run_id = uuid.uuid4()
    with session_scope(load_settings()) as session:
        session.execute(
            text(
                f"INSERT INTO {TABLE} (id, job_id, scope, status, trigger_source, findings, "
                "account_divergence, unexplained_exposure, classification_summary, "
                "unresolved_reasons, result_summary) "
                "VALUES (:id, :job_id, :scope, :status, 'job', '[]', '{}', '{}', '{}', '[]', '{}')"
            ),
            {"id": run_id, "job_id": job_id, "scope": scope, "status": status},
        )
    return run_id


def _create_job() -> uuid.UUID:
    with session_scope(load_settings()) as session:
        job = Job(job_type="reconciliation", payload={"scope": "account"})
        session.add(job)
        session.flush()
        return job.id


def test_chain_is_linear_and_0024_follows_0023(migrated_db: str) -> None:
    script = ScriptDirectory.from_config(build_alembic_config())
    assert len(script.get_heads()) == 1
    revision = script.get_revision(REVISION)
    assert revision is not None
    assert revision.down_revision == PREVIOUS_REVISION
    # Linear: 0024 is reachable from the single head.
    assert REVISION in {r.revision for r in script.walk_revisions()}


def test_schema_has_no_owner_column_and_only_a_job_foreign_key(migrated_db: str) -> None:
    inspector = inspect(get_engine(load_settings()))
    columns = {c["name"]: c for c in inspector.get_columns(TABLE)}
    assert "strategy_id" not in columns
    assert not [name for name in columns if "strategy" in name]
    assert set(columns) == {
        "id",
        "job_id",
        "scope",
        "status",
        "trigger_source",
        "as_of_session",
        "started_at",
        "completed_at",
        "blocks_execution",
        "finding_count",
        "blocking_count",
        "findings",
        "account_divergence",
        "unexplained_exposure",
        "classification_summary",
        "unresolved_reasons",
        "result_summary",
        "error_message",
        "created_at",
        "updated_at",
    }
    assert not columns["blocks_execution"]["nullable"]
    assert columns["job_id"]["nullable"]
    assert columns["completed_at"]["nullable"]
    foreign_keys = inspector.get_foreign_keys(TABLE)
    assert {fk["referred_table"] for fk in foreign_keys} == {"jobs"}
    assert all(fk["options"].get("ondelete") == "SET NULL" for fk in foreign_keys)


def test_out_of_set_scope_is_rejected_by_the_database(migrated_db: str) -> None:
    _insert(scope="account")
    for bad in ("strategy", "", "ACCOUNT"):
        with pytest.raises(IntegrityError, match=SCOPE_CHECK):
            _insert(scope=bad)


def test_out_of_set_status_is_rejected_by_the_database(migrated_db: str) -> None:
    for good in ("pending", "succeeded", "failed"):
        _insert(status=good)
    for bad in ("running", "stale", "done"):
        with pytest.raises(IntegrityError, match=STATUS_CHECK):
            _insert(status=bad)


def test_job_delete_sets_job_id_to_null(migrated_db: str) -> None:
    job_id = _create_job()
    run_id = _insert(job_id=job_id)
    with session_scope(load_settings()) as session:
        session.execute(text("DELETE FROM jobs WHERE id = :id"), {"id": job_id})
    with session_scope(load_settings()) as session:
        row = session.get(AccountReconciliationRun, run_id)
        assert row is not None
        assert row.job_id is None


def test_latest_run_and_job_indexes_exist(migrated_db: str) -> None:
    inspector = inspect(get_engine(load_settings()))
    indexes = {i["name"]: i["column_names"] for i in inspector.get_indexes(TABLE)}
    assert indexes["ix_account_reconciliation_runs_completed_at"] == ["completed_at"]
    assert indexes["ix_account_reconciliation_runs_job_id"] == ["job_id"]


def test_downgrade_drops_table_and_reupgrade_restores_it(migrated_db: str) -> None:
    _fresh_caches()
    command.downgrade(build_alembic_config(), PREVIOUS_REVISION)
    _fresh_caches()
    assert TABLE not in inspect(get_engine(load_settings())).get_table_names()
    with session_scope(load_settings()) as session:
        version = session.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert version == PREVIOUS_REVISION

    _fresh_caches()
    command.upgrade(build_alembic_config(), "head")
    _fresh_caches()
    assert TABLE in inspect(get_engine(load_settings())).get_table_names()


def test_orm_metadata_matches_migration(migrated_db: str) -> None:
    table = AccountReconciliationRun.__table__
    inspector = inspect(get_engine(load_settings()))
    db_columns = {c["name"]: c for c in inspector.get_columns(TABLE)}
    assert set(db_columns) == {c.name for c in table.columns}
    for column in table.columns:
        assert db_columns[column.name]["nullable"] == column.nullable, column.name
    db_checks = {c["name"] for c in inspector.get_check_constraints(TABLE)}
    orm_checks = {str(c.name) for c in table.constraints if c.__class__.__name__ == "CheckConstraint"}
    assert db_checks == {SCOPE_CHECK, STATUS_CHECK}
    assert orm_checks == db_checks
    db_indexes = {i["name"] for i in inspector.get_indexes(TABLE)}
    orm_indexes = {str(i.name) for i in table.indexes}
    assert orm_indexes <= db_indexes

    # The ORM round-trips a full row.
    with session_scope(load_settings()) as session:
        run = AccountReconciliationRun(trigger_source="job", findings=[], blocks_execution=True)
        session.add(run)
        session.flush()
        stored = session.execute(select(AccountReconciliationRun)).scalar_one()
        assert stored.scope == "account"
        assert stored.status == "pending"
        assert stored.blocks_execution is True
