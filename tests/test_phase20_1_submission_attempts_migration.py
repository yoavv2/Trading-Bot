"""Phase 20.1 migration 0023 tests: order_submission_attempts (COR-06, D-12).

The closed outcome set, the outcome/completion pairing and the unique attempt
number are database invariants, not application conventions.
"""

from __future__ import annotations

import re
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
from tests.support.submission_attempts import seed_paper_order_row

from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.db.models import AttemptOutcomeClass, OrderSubmissionAttempt
from trading_platform.db.session import clear_engine_cache, get_engine, session_scope

REVISION = "0023_phase20_1_order_submission_attempts"
PREVIOUS_REVISION = "0022_phase20_1_active_paper_strategy"
OUTCOME_CHECK = "ck_order_submission_attempts_outcome_class"
PAIR_CHECK = "ck_order_submission_attempts_outcome_pair"
POSITIVE_CHECK = "ck_order_submission_attempts_attempt_number_positive"
UNIQUE = "uq_order_submission_attempts_paper_order_id_attempt_number"
SIX_VALUES = {
    "pre_connection",
    "deadline_expired",
    "ambiguous",
    "duplicate_reported",
    "rejected",
    "accepted",
}


def _fresh_caches() -> None:
    clear_settings_cache()
    clear_engine_cache()


@pytest.fixture()
def migrated_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    with migrated_database(monkeypatch, "phase20_1_attempts") as name:
        yield name


def _create_order() -> tuple[uuid.UUID, uuid.UUID]:
    """Return (paper_order_id, strategy_run_id) for a minimal PENDING order."""
    with session_scope(load_settings()) as session:
        return seed_paper_order_row(session)


def _insert(sql_values: str, params: dict[str, object]) -> None:
    with session_scope(load_settings()) as session:
        session.execute(
            text(
                "INSERT INTO order_submission_attempts "
                "(id, paper_order_id, attempt_number, started_at, completed_at, outcome_class) "
                f"VALUES (:id, :oid, :n, now(), {sql_values})"
            ),
            {"id": uuid.uuid4(), **params},
        )


def test_chain_is_linear_and_0023_follows_0022(migrated_db: str) -> None:
    script = ScriptDirectory.from_config(build_alembic_config())
    # Head-agnostic (20.1-08): later migrations extend the chain, so assert a single linear
    # head that descends from REVISION rather than pinning REVISION as the head.
    heads = script.get_heads()
    assert len(heads) == 1
    assert REVISION in {r.revision for r in script.walk_revisions(base=PREVIOUS_REVISION, head=heads[0])}
    revision = script.get_revision(REVISION)
    assert revision is not None
    assert revision.down_revision == PREVIOUS_REVISION


def test_schema_columns_and_foreign_keys(migrated_db: str) -> None:
    inspector = inspect(get_engine(load_settings()))
    columns = {c["name"]: c for c in inspector.get_columns("order_submission_attempts")}
    assert set(columns) == {
        "id",
        "paper_order_id",
        "strategy_run_id",
        "attempt_number",
        "started_at",
        "completed_at",
        "outcome_class",
        "http_status",
        "error_type",
        "broker_message",
        "created_at",
        "updated_at",
    }
    assert not columns["paper_order_id"]["nullable"]
    assert columns["strategy_run_id"]["nullable"]
    assert not columns["attempt_number"]["nullable"]
    assert not columns["started_at"]["nullable"]
    assert columns["completed_at"]["nullable"]
    assert columns["outcome_class"]["nullable"]
    fks = {
        fk["constrained_columns"][0]: (fk["referred_table"], fk["options"].get("ondelete"))
        for fk in inspector.get_foreign_keys("order_submission_attempts")
    }
    assert fks == {
        "paper_order_id": ("paper_orders", "CASCADE"),
        "strategy_run_id": ("strategy_runs", "SET NULL"),
    }


def test_out_of_set_outcome_class_is_rejected(migrated_db: str) -> None:
    order_id, _ = _create_order()
    with pytest.raises(IntegrityError, match=OUTCOME_CHECK):
        _insert("now(), 'retried'", {"oid": order_id, "n": 1})


def test_outcome_class_check_accepts_exactly_six_values(migrated_db: str) -> None:
    assert {member.value for member in AttemptOutcomeClass} == SIX_VALUES
    order_id, _ = _create_order()
    for number, value in enumerate(sorted(SIX_VALUES), start=1):
        _insert("now(), :value", {"oid": order_id, "n": number, "value": value})
    with session_scope(load_settings()) as session:
        stored = set(
            session.execute(select(OrderSubmissionAttempt.outcome_class)).scalars().all()
        )
        assert stored == SIX_VALUES
        definition = session.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = :name"
            ),
            {"name": OUTCOME_CHECK},
        ).scalar_one()
    assert set(re.findall(r"'([a-z_]+)'", definition)) == SIX_VALUES


def test_incomplete_row_with_null_outcome_is_allowed(migrated_db: str) -> None:
    order_id, _ = _create_order()
    _insert("NULL, NULL", {"oid": order_id, "n": 1})
    with session_scope(load_settings()) as session:
        row = session.execute(select(OrderSubmissionAttempt)).scalar_one()
        assert row.completed_at is None and row.outcome_class is None


def test_half_completed_rows_are_rejected(migrated_db: str) -> None:
    order_id, _ = _create_order()
    with pytest.raises(IntegrityError, match=PAIR_CHECK):
        _insert("now(), NULL", {"oid": order_id, "n": 1})
    with pytest.raises(IntegrityError, match=PAIR_CHECK):
        _insert("NULL, 'accepted'", {"oid": order_id, "n": 2})


def test_duplicate_attempt_number_is_rejected(migrated_db: str) -> None:
    order_id, _ = _create_order()
    _insert("NULL, NULL", {"oid": order_id, "n": 1})
    with pytest.raises(IntegrityError, match=UNIQUE):
        _insert("NULL, NULL", {"oid": order_id, "n": 1})


def test_attempt_number_must_be_positive(migrated_db: str) -> None:
    order_id, _ = _create_order()
    with pytest.raises(IntegrityError, match=POSITIVE_CHECK):
        _insert("NULL, NULL", {"oid": order_id, "n": 0})


def test_paper_order_delete_cascades_and_run_delete_nulls_the_run(migrated_db: str) -> None:
    order_id, run_id = _create_order()
    _insert("NULL, NULL", {"oid": order_id, "n": 1})
    with session_scope(load_settings()) as session:
        session.execute(
            text("UPDATE order_submission_attempts SET strategy_run_id = :rid"), {"rid": run_id}
        )
    with session_scope(load_settings()) as session:
        session.execute(text("DELETE FROM paper_orders WHERE id = :oid"), {"oid": order_id})
        assert session.execute(text("SELECT count(*) FROM order_submission_attempts")).scalar_one() == 0


def test_downgrade_drops_table_and_reupgrade_restores_it(migrated_db: str) -> None:
    _fresh_caches()
    command.downgrade(build_alembic_config(), PREVIOUS_REVISION)
    _fresh_caches()
    assert "order_submission_attempts" not in inspect(get_engine(load_settings())).get_table_names()
    with session_scope(load_settings()) as session:
        version = session.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert version == PREVIOUS_REVISION

    _fresh_caches()
    command.upgrade(build_alembic_config(), "head")
    _fresh_caches()
    assert "order_submission_attempts" in inspect(get_engine(load_settings())).get_table_names()


def test_orm_metadata_matches_migration(migrated_db: str) -> None:
    table = OrderSubmissionAttempt.__table__
    inspector = inspect(get_engine(load_settings()))
    db_columns = {c["name"]: c for c in inspector.get_columns("order_submission_attempts")}
    assert set(db_columns) == {c.name for c in table.columns}
    for column in table.columns:
        assert db_columns[column.name]["nullable"] == column.nullable, column.name
    db_checks = {c["name"] for c in inspector.get_check_constraints("order_submission_attempts")}
    orm_checks = {
        str(c.name) for c in table.constraints if c.__class__.__name__ == "CheckConstraint"
    }
    assert db_checks == {OUTCOME_CHECK, PAIR_CHECK, POSITIVE_CHECK}
    assert orm_checks == db_checks
    db_uniques = {u["name"] for u in inspector.get_unique_constraints("order_submission_attempts")}
    assert db_uniques == {UNIQUE}
    db_indexes = {i["name"] for i in inspector.get_indexes("order_submission_attempts")}
    assert "ix_order_submission_attempts_strategy_run_id" in db_indexes
