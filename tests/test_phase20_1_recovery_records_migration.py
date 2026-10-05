"""Phase 20.1 migration 0026 tests: recovery_records (REC-01, D-12/D-14, S-5, J-2).

The table stores per-intent classifications, absence-evidence items and operator broker
statements as append-only evidence: closed sets and per-kind shapes enforced by the
database, one broker statement per intent, no foreign key to strategies or runs, and a
trigger that rejects every DELETE and every UPDATE except the foreign keys' null-outs.
"""

from __future__ import annotations

import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alembic import command
from alembic.script import ScriptDirectory
from scripts.migrate import build_alembic_config
from tests.support.migrated_db import migrated_database

from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.db.models import Job, RecoveryRecord
from trading_platform.db.session import clear_engine_cache, get_engine, session_scope

REVISION = "0026_phase20_1_recovery_records"
PREVIOUS_REVISION = "0025_phase20_1_external_broker_activity"
TABLE = "recovery_records"


def _fresh_caches() -> None:
    clear_settings_cache()
    clear_engine_cache()


@pytest.fixture()
def migrated_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    with migrated_database(monkeypatch, "phase20_1_recovery") as name:
        yield name


_COLUMNS = (
    "kind",
    "job_id",
    "paper_order_id",
    "strategy_public_id",
    "classification",
    "broker_state",
    "unresolved_reason",
    "evidence_item",
    "evidence_result",
    "observed_at",
    "statement",
    "reference",
    "reason",
)


def _classification(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {"kind": "classification", "classification": "not_found"}
    row.update(overrides)
    return row


def _evidence(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "kind": "absence_evidence",
        "evidence_item": "a_client_order_id_404",
        "evidence_result": "confirmed",
        "observed_at": "2026-10-04T10:00:00+00:00",
    }
    row.update(overrides)
    return row


def _statement(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "kind": "broker_statement",
        "statement": "not_received",
        "reference": "ticket-1",
        "reason": "broker support said so",
    }
    row.update(overrides)
    return row


def _insert(values: dict[str, Any]) -> uuid.UUID:
    row_id = uuid.uuid4()
    params = {column: values.get(column) for column in _COLUMNS}
    params["id"] = row_id
    params["recorded_by"] = values.get("recorded_by", "tests")
    with session_scope(load_settings()) as session:
        session.execute(
            text(
                f"INSERT INTO {TABLE} (id, recorded_by, "
                + ", ".join(_COLUMNS)
                + ") VALUES (:id, :recorded_by, "
                + ", ".join(f":{c}" for c in _COLUMNS)
                + ")"
            ),
            params,
        )
    return row_id


def _create_job() -> uuid.UUID:
    with session_scope(load_settings()) as session:
        job = Job(job_type="paper-session", payload={})
        session.add(job)
        session.flush()
        return job.id


def _create_order() -> uuid.UUID:
    """A paper_orders row via the shared test support (full FK chain)."""

    from tests.support.submission_attempts import seed_paper_order_row

    with session_scope(load_settings()) as session:
        order_id, _run_id = seed_paper_order_row(session)
    return order_id


def test_chain_is_linear_and_0026_follows_0025(migrated_db: str) -> None:
    script = ScriptDirectory.from_config(build_alembic_config())
    assert len(script.get_heads()) == 1
    revision = script.get_revision(REVISION)
    assert revision is not None
    assert revision.down_revision == PREVIOUS_REVISION
    assert REVISION in {r.revision for r in script.walk_revisions()}


def test_schema_has_only_job_and_order_foreign_keys(migrated_db: str) -> None:
    inspector = inspect(get_engine(load_settings()))
    columns = {c["name"]: c for c in inspector.get_columns(TABLE)}
    assert set(columns) == {
        "id",
        "kind",
        "job_id",
        "paper_order_id",
        "strategy_public_id",
        "classification",
        "broker_state",
        "unresolved_reason",
        "evidence_item",
        "evidence_result",
        "observed_at",
        "statement",
        "reference",
        "reason",
        "recorded_by",
        "created_at",
    }
    assert "strategy_id" not in columns
    assert columns["strategy_public_id"]["nullable"]
    for required in ("kind", "recorded_by", "created_at"):
        assert not columns[required]["nullable"], required
    foreign_keys = inspector.get_foreign_keys(TABLE)
    assert {fk["referred_table"] for fk in foreign_keys} == {"jobs", "paper_orders"}
    assert all(fk["options"].get("ondelete") == "SET NULL" for fk in foreign_keys)


@pytest.mark.parametrize(
    ("values", "constraint"),
    [
        ({"kind": "other"}, "ck_recovery_records_kind"),
        (
            _classification(classification="statement_resolved"),
            "ck_recovery_records_classification",
        ),
        (
            _classification(classification="found_verified", broker_state="done"),
            "ck_recovery_records_broker_state",
        ),
        (
            _classification(classification="unresolved", unresolved_reason="boom"),
            "ck_recovery_records_unresolved_reason",
        ),
        (_evidence(evidence_item="e_other"), "ck_recovery_records_evidence_item"),
        (_evidence(evidence_result="maybe"), "ck_recovery_records_evidence_result"),
        (_statement(statement="resend"), "ck_recovery_records_statement"),
    ],
)
def test_closed_sets_are_enforced_by_the_database(
    migrated_db: str, values: dict[str, Any], constraint: str
) -> None:
    with pytest.raises(IntegrityError, match=constraint):
        _insert(values)


@pytest.mark.parametrize(
    ("values", "constraint"),
    [
        # classification row without classification
        ({"kind": "classification"}, "ck_recovery_records_classification_shape"),
        # classification value on another kind
        (_evidence(classification="not_found"), "ck_recovery_records_classification_shape"),
        # found_verified without broker_state / broker_state without found_verified
        (
            _classification(classification="found_verified"),
            "ck_recovery_records_broker_state_shape",
        ),
        (_classification(broker_state="working"), "ck_recovery_records_broker_state_shape"),
        # unresolved without reason / reason without unresolved
        (
            _classification(classification="unresolved"),
            "ck_recovery_records_unresolved_reason_shape",
        ),
        (
            _classification(unresolved_reason="lookup_error"),
            "ck_recovery_records_unresolved_reason_shape",
        ),
        # absence evidence missing a required column
        (_evidence(evidence_item=None), "ck_recovery_records_absence_evidence_shape"),
        (_evidence(evidence_result=None), "ck_recovery_records_absence_evidence_shape"),
        (_evidence(observed_at=None), "ck_recovery_records_absence_evidence_shape"),
        # evidence columns on a non-evidence kind
        (
            _statement(evidence_item="a_client_order_id_404"),
            "ck_recovery_records_absence_evidence_shape",
        ),
        # broker statement missing / blank columns
        (_statement(statement=None), "ck_recovery_records_broker_statement_shape"),
        (_statement(reference=None), "ck_recovery_records_broker_statement_shape"),
        (_statement(reason=None), "ck_recovery_records_broker_statement_shape"),
        (_statement(reference="   "), "ck_recovery_records_broker_statement_shape"),
        (_statement(reason=""), "ck_recovery_records_broker_statement_shape"),
        # statement columns on a non-statement kind
        (_classification(reference="x"), "ck_recovery_records_broker_statement_shape"),
    ],
)
def test_per_kind_shape_is_enforced_by_the_database(
    migrated_db: str, values: dict[str, Any], constraint: str
) -> None:
    with pytest.raises(IntegrityError, match=constraint):
        _insert(values)


def test_every_closed_member_is_accepted(migrated_db: str) -> None:
    for classification in ("nothing_submitted", "not_sent", "rejected_at_submission", "not_found"):
        _insert(_classification(classification=classification))
    for state in (
        "working",
        "partially_filled",
        "filled",
        "canceled_expired",
        "rejected",
        "replaced",
    ):
        _insert(_classification(classification="found_verified", broker_state=state))
    for reason in (
        "lookup_error",
        "undocumented_not_found_response",
        "page_cap_reached",
        "unmapped_status",
        "id_mismatch",
        "execution_path_unproven",
    ):
        _insert(_classification(classification="unresolved", unresolved_reason=reason))
    for item in (
        "a_client_order_id_404",
        "b_list_scan_no_match",
        "c_no_fill_reference",
        "d_no_exposure_change",
    ):
        for result in ("confirmed", "not_confirmed", "error"):
            _insert(_evidence(evidence_item=item, evidence_result=result))
    _insert(_statement())
    _insert(_statement(statement="order_record"))


def test_one_broker_statement_per_intent(migrated_db: str) -> None:
    order_id = _create_order()
    _insert(_statement(paper_order_id=order_id))
    with pytest.raises(IntegrityError, match="uq_recovery_records_one_statement_per_intent"):
        _insert(_statement(paper_order_id=order_id, statement="order_record", reference="r2"))
    # Other kinds on the same intent, and statements on other intents, are unaffected.
    _insert(_evidence(paper_order_id=order_id))
    _insert(_classification(paper_order_id=order_id))
    _insert(_statement(paper_order_id=_create_order()))
    # NULL paper_order_id (e.g. after a null-out) never collides.
    _insert(_statement())
    _insert(_statement())


@pytest.mark.parametrize(
    "assignment",
    [
        "reason = 'edited'",
        "reference = 'edited'",
        "statement = 'order_record'",
        "strategy_public_id = 'other'",
        "recorded_by = 'someone'",
        "created_at = now() + interval '1 day'",
        "kind = 'absence_evidence'",
    ],
)
def test_update_of_any_column_is_rejected(migrated_db: str, assignment: str) -> None:
    job_id = _create_job()
    row_id = _insert(_statement(job_id=job_id, paper_order_id=_create_order()))
    with pytest.raises(DBAPIError, match="append-only"):
        with session_scope(load_settings()) as session:
            session.execute(text(f"UPDATE {TABLE} SET {assignment} WHERE id = :id"), {"id": row_id})


def test_update_of_fks_to_non_null_values_is_rejected(migrated_db: str) -> None:
    first = _create_job()
    second = _create_job()
    row_id = _insert(_statement(job_id=first))
    with pytest.raises(DBAPIError, match="append-only"):
        with session_scope(load_settings()) as session:
            session.execute(
                text(f"UPDATE {TABLE} SET job_id = :job WHERE id = :id"),
                {"job": second, "id": row_id},
            )
    unlinked = _insert(_statement())
    with pytest.raises(DBAPIError, match="append-only"):
        with session_scope(load_settings()) as session:
            session.execute(
                text(f"UPDATE {TABLE} SET job_id = :job WHERE id = :id"),
                {"job": first, "id": unlinked},
            )
    with pytest.raises(DBAPIError, match="append-only"):
        with session_scope(load_settings()) as session:
            session.execute(
                text(f"UPDATE {TABLE} SET paper_order_id = :o WHERE id = :id"),
                {"o": _create_order(), "id": unlinked},
            )


def test_delete_is_rejected(migrated_db: str) -> None:
    row_id = _insert(_statement())
    with pytest.raises(DBAPIError, match="append-only"):
        with session_scope(load_settings()) as session:
            session.execute(text(f"DELETE FROM {TABLE} WHERE id = :id"), {"id": row_id})
    with pytest.raises(DBAPIError, match="append-only"):
        with session_scope(load_settings()) as session:
            session.execute(text(f"DELETE FROM {TABLE}"))
    with session_scope(load_settings()) as session:
        assert session.get(RecoveryRecord, row_id) is not None


def test_parent_job_and_order_cannot_be_deleted_so_the_record_keeps_its_links(
    migrated_db: str,
) -> None:
    # 0029 retention policy (user decision 2026-10-05): evidence rows are not deletable
    job_id = _create_job()
    order_id = _create_order()
    row_id = _insert(_statement(job_id=job_id, paper_order_id=order_id))
    with session_scope(load_settings()) as session:
        before = session.get(RecoveryRecord, row_id)
        assert before is not None
        snapshot = (before.reason, before.reference, before.statement, before.created_at)
    # The 'paper-session' Job and the order are evidence: both deletes are refused.
    with pytest.raises(IntegrityError), session_scope(load_settings()) as session:
        session.execute(text("DELETE FROM jobs WHERE id = :id"), {"id": job_id})
    with pytest.raises(IntegrityError), session_scope(load_settings()) as session:
        session.execute(text("DELETE FROM paper_orders WHERE id = :id"), {"id": order_id})
    with session_scope(load_settings()) as session:
        row = session.get(RecoveryRecord, row_id)
        assert row is not None
        assert row.job_id == job_id
        assert row.paper_order_id == order_id
        assert (row.reason, row.reference, row.statement, row.created_at) == snapshot


def test_indexes_exist(migrated_db: str) -> None:
    inspector = inspect(get_engine(load_settings()))
    indexes = {i["name"]: i["column_names"] for i in inspector.get_indexes(TABLE)}
    assert indexes["ix_recovery_records_job_id"] == ["job_id"]
    assert indexes["ix_recovery_records_paper_order_id_kind_created_at"] == [
        "paper_order_id",
        "kind",
        "created_at",
    ]
    assert indexes["ix_recovery_records_strategy_public_id_created_at"] == [
        "strategy_public_id",
        "created_at",
    ]
    assert indexes["uq_recovery_records_one_statement_per_intent"] == ["paper_order_id"]


def test_downgrade_drops_table_function_and_reupgrade_restores_them(migrated_db: str) -> None:
    _fresh_caches()
    command.downgrade(build_alembic_config(), PREVIOUS_REVISION)
    _fresh_caches()
    assert TABLE not in inspect(get_engine(load_settings())).get_table_names()
    with session_scope(load_settings()) as session:
        version = session.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        functions = session.execute(
            text("SELECT proname FROM pg_proc WHERE proname = 'recovery_records_immutable'")
        ).all()
    assert version == PREVIOUS_REVISION
    assert functions == []

    _fresh_caches()
    command.upgrade(build_alembic_config(), "head")
    _fresh_caches()
    assert TABLE in inspect(get_engine(load_settings())).get_table_names()
    with pytest.raises(DBAPIError, match="append-only"):
        row_id = _insert(_statement())
        with session_scope(load_settings()) as session:
            session.execute(text(f"DELETE FROM {TABLE} WHERE id = :id"), {"id": row_id})


def test_orm_metadata_matches_migration(migrated_db: str) -> None:
    table = RecoveryRecord.__table__
    inspector = inspect(get_engine(load_settings()))
    db_columns = {c["name"]: c for c in inspector.get_columns(TABLE)}
    assert set(db_columns) == {c.name for c in table.columns}
    for column in table.columns:
        assert db_columns[column.name]["nullable"] == column.nullable, column.name
    db_checks = {c["name"] for c in inspector.get_check_constraints(TABLE)}
    orm_checks = {
        str(c.name) for c in table.constraints if c.__class__.__name__ == "CheckConstraint"
    }
    assert db_checks == orm_checks
    assert len(db_checks) == 12
    db_indexes = {i["name"] for i in inspector.get_indexes(TABLE)}
    assert {str(i.name) for i in table.indexes} <= db_indexes

    with session_scope(load_settings()) as session:
        session.add(
            RecoveryRecord(
                kind="broker_statement",
                statement="not_received",
                reference="orm",
                reason="orm round trip",
                recorded_by="tests",
            )
        )
        session.flush()
        stored = session.execute(select(RecoveryRecord)).scalar_one()
        assert stored.job_id is None
        assert stored.paper_order_id is None
        assert stored.created_at is not None
