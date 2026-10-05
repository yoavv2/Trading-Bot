"""Phase 20.1 migration 0028 tests: attempt log append-only / complete-once in the DB (SAF-10).

A completed ``ambiguous`` attempt must be impossible to rewrite to ``pre_connection`` and an
order with attempt evidence must be impossible to delete, by ANY database client. Both
production completion paths stay legal under the trigger.
"""

from __future__ import annotations

import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alembic import command
from alembic.script import ScriptDirectory
from scripts.migrate import build_alembic_config
from tests.support.migrated_db import migrated_database
from tests.support.operation_fixtures import (
    seed_operation,
    seed_operation_intent,
    seed_operation_job,
)
from tests.support.submission_attempts import seed_paper_order_row

from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.db.models import (
    AttemptOutcomeClass,
    JobStatus,
    OrderSubmissionAttempt,
)
from trading_platform.db.session import clear_engine_cache, get_engine, session_scope
from trading_platform.services.execution import operations as ops

TRIGGER = "trg_order_submission_attempts_append_only"
FK = "fk_order_submission_attempts_paper_order_id_paper_orders"
PREVIOUS_REVISION = "0027_phase20_1_execution_operations"


@pytest.fixture()
def migrated_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    with migrated_database(monkeypatch, "phase20_1_attempt_append") as name:
        yield name


def _create_order() -> tuple[uuid.UUID, uuid.UUID]:
    with session_scope(load_settings()) as session:
        return seed_paper_order_row(session)


def _insert_attempt(
    order_id: uuid.UUID,
    number: int = 1,
    *,
    outcome: str | None = None,
    run_id: uuid.UUID | None = None,
) -> uuid.UUID:
    attempt_id = uuid.uuid4()
    with session_scope(load_settings()) as session:
        session.execute(
            text(
                "INSERT INTO order_submission_attempts "
                "(id, paper_order_id, strategy_run_id, attempt_number, started_at, "
                "completed_at, outcome_class) VALUES "
                "(:id, :oid, :rid, :n, now(), "
                + ("NULL, NULL" if outcome is None else "now(), :outcome")
                + ")"
            ),
            {"id": attempt_id, "oid": order_id, "rid": run_id, "n": number, "outcome": outcome},
        )
    return attempt_id


def _attempt_count() -> int:
    with session_scope(load_settings()) as session:
        return int(
            session.execute(text("SELECT count(*) FROM order_submission_attempts")).scalar_one()
        )


def _update(attempt_id: uuid.UUID, assignment: str, **params: object) -> None:
    with session_scope(load_settings()) as session:
        session.execute(
            text(f"UPDATE order_submission_attempts SET {assignment} WHERE id = :id"),
            {"id": attempt_id, **params},
        )


def test_completed_attempt_cannot_be_rewritten(migrated_db: str) -> None:
    order_id, _ = _create_order()
    attempt_id = _insert_attempt(order_id, outcome="ambiguous")
    for assignment in (
        "outcome_class = 'pre_connection'",
        "http_status = 200",
        "error_type = 'x'",
        "broker_message = 'x'",
        "completed_at = now()",
        "attempt_number = 9",
        "started_at = now() - interval '1 day'",
        # Un-completing is also a rewrite.
        "outcome_class = NULL, completed_at = NULL",
    ):
        with pytest.raises(IntegrityError, match="complete-once"):
            _update(attempt_id, assignment)
    with session_scope(load_settings()) as session:
        outcome = session.execute(
            text("SELECT outcome_class FROM order_submission_attempts WHERE id = :id"),
            {"id": attempt_id},
        ).scalar_one()
    assert outcome == "ambiguous"


def test_attempt_rows_cannot_be_deleted(migrated_db: str) -> None:
    order_id, _ = _create_order()
    complete = _insert_attempt(order_id, 1, outcome="accepted")
    incomplete = _insert_attempt(order_id, 2)
    for attempt_id in (complete, incomplete):
        with (
            pytest.raises(IntegrityError, match="append-only"),
            session_scope(load_settings()) as session,
        ):
            session.execute(
                text("DELETE FROM order_submission_attempts WHERE id = :id"), {"id": attempt_id}
            )
    assert _attempt_count() == 2


def test_first_completion_is_allowed_once(migrated_db: str) -> None:
    order_id, _ = _create_order()
    attempt_id = _insert_attempt(order_id)
    _update(
        attempt_id,
        "outcome_class = 'accepted', http_status = 200, completed_at = now(), "
        "updated_at = now()",
    )
    with pytest.raises(IntegrityError, match="complete-once"):
        _update(
            attempt_id,
            "outcome_class = 'rejected', http_status = 422, completed_at = now()",
        )
    with session_scope(load_settings()) as session:
        row = session.get(OrderSubmissionAttempt, attempt_id)
        assert row is not None
        assert row.outcome_class == "accepted" and row.http_status == 200


def test_complete_attempt_late_succeeds_under_the_trigger(migrated_db: str) -> None:
    with session_scope(load_settings()) as session:
        job = seed_operation_job(session, status=JobStatus.RUNNING)
        operation = seed_operation(
            session, state="running", reason=None, epoch=3, executor_job=job
        )
        seeded = seed_operation_intent(session, operation, with_order=True)
        order = seeded.order
        assert order is not None
        attempt = OrderSubmissionAttempt(
            paper_order_id=order.id,
            attempt_number=1,
            execution_epoch=3,
            executor_job_id=job.id,
        )
        session.add(attempt)
        session.flush()
        attempt_id, job_id = attempt.id, job.id
    result = ops.complete_attempt_late(
        attempt_id,
        AttemptOutcomeClass.ACCEPTED,
        executor_job_id=job_id,
        execution_epoch=3,
        http_status=200,
    )
    assert result.stale is False
    with session_scope(load_settings()) as session:
        row = session.get(OrderSubmissionAttempt, attempt_id)
        assert row is not None
        assert row.outcome_class == "accepted" and row.completed_at is not None


def test_incomplete_attempt_identity_columns_are_immutable(migrated_db: str) -> None:
    order_id, _ = _create_order()
    attempt_id = _insert_attempt(order_id)
    for assignment, params in (
        ("started_at = now() - interval '1 day'", {}),
        ("attempt_number = 7", {}),
        ("execution_epoch = 5", {}),
        ("executor_job_id = :value", {"value": uuid.uuid4()}),
        ("authorization_deadline = now()", {}),
        ("paper_order_id = :value", {"value": uuid.uuid4()}),
        # Completing AND changing an identity column in the same statement is refused.
        (
            "outcome_class = 'accepted', completed_at = now(), attempt_number = 7",
            {},
        ),
    ):
        with pytest.raises(IntegrityError):
            _update(attempt_id, assignment, **params)
    with session_scope(load_settings()) as session:
        row = session.get(OrderSubmissionAttempt, attempt_id)
        assert row is not None
        assert row.outcome_class is None and row.attempt_number == 1
        assert row.execution_epoch is None and row.executor_job_id is None


def test_paper_order_with_attempts_cannot_be_deleted(migrated_db: str) -> None:
    with session_scope(load_settings()) as session:
        operation = seed_operation(session, state="running", reason=None)
        seeded = seed_operation_intent(
            session, operation, attempts=(AttemptOutcomeClass.AMBIGUOUS,)
        )
        order_id = seeded.order.id  # type: ignore[union-attr]
        intent_id = seeded.row.id
    with pytest.raises(IntegrityError), session_scope(load_settings()) as session:
        session.execute(text("DELETE FROM paper_orders WHERE id = :id"), {"id": order_id})
    with session_scope(load_settings()) as session:
        attempts = session.execute(
            text(
                "SELECT outcome_class FROM order_submission_attempts WHERE paper_order_id = :id"
            ),
            {"id": order_id},
        ).scalars().all()
        link = session.execute(
            text("SELECT paper_order_id FROM execution_operation_intents WHERE id = :id"),
            {"id": intent_id},
        ).scalar_one()
    assert attempts == ["ambiguous"]
    assert link == order_id


def test_paper_order_without_attempts_cannot_be_deleted(migrated_db: str) -> None:
    # 0029 retention policy (user decision 2026-10-05): evidence rows are not deletable
    with session_scope(load_settings()) as session:
        operation = seed_operation(session, state="running", reason=None)
        seeded = seed_operation_intent(session, operation, with_order=True)
        order_id = seeded.order.id  # type: ignore[union-attr]
        intent_id = seeded.row.id
    with pytest.raises(IntegrityError), session_scope(load_settings()) as session:
        session.execute(text("DELETE FROM paper_orders WHERE id = :id"), {"id": order_id})
    with session_scope(load_settings()) as session:
        assert (
            session.execute(
                text("SELECT count(*) FROM paper_orders WHERE id = :id"), {"id": order_id}
            ).scalar_one()
            == 1
        )
        link = session.execute(
            text("SELECT paper_order_id FROM execution_operation_intents WHERE id = :id"),
            {"id": intent_id},
        ).scalar_one()
    assert link == order_id


def test_attempt_run_reference_survives_because_the_run_is_undeletable(
    migrated_db: str,
) -> None:
    # 0029 retention policy (user decision 2026-10-05): evidence rows are not deletable
    order_id, _order_run = _create_order()
    _other_order_id, separate_run = _create_order()
    attempt_id = _insert_attempt(order_id, outcome="rejected", run_id=separate_run)
    with pytest.raises(IntegrityError), session_scope(load_settings()) as session:
        session.execute(text("DELETE FROM strategy_runs WHERE id = :id"), {"id": separate_run})
    with session_scope(load_settings()) as session:
        row = session.execute(
            text(
                "SELECT strategy_run_id, outcome_class FROM order_submission_attempts "
                "WHERE id = :id"
            ),
            {"id": attempt_id},
        ).one()
    assert row.strategy_run_id == separate_run
    assert row.outcome_class == "rejected"


def test_order_run_delete_is_rejected_when_orders_have_attempts(migrated_db: str) -> None:
    order_id, order_run = _create_order()
    _insert_attempt(order_id, outcome="ambiguous", run_id=order_run)
    # paper_orders.strategy_run_id cascades from the run into the RESTRICT attempt FK.
    with pytest.raises(IntegrityError), session_scope(load_settings()) as session:
        session.execute(text("DELETE FROM strategy_runs WHERE id = :id"), {"id": order_run})
    assert _attempt_count() == 1
    with session_scope(load_settings()) as session:
        assert (
            session.execute(
                text("SELECT count(*) FROM paper_orders WHERE id = :id"), {"id": order_id}
            ).scalar_one()
            == 1
        )


def _fresh_caches() -> None:
    clear_settings_cache()
    clear_engine_cache()


def _trigger_exists() -> bool:
    with session_scope(load_settings()) as session:
        return bool(
            session.execute(
                text("SELECT count(*) FROM pg_trigger WHERE tgname = :name"), {"name": TRIGGER}
            ).scalar_one()
        )


def _paper_order_fk_ondelete() -> str | None:
    inspector = inspect(get_engine(load_settings()))
    for fk in inspector.get_foreign_keys("order_submission_attempts"):
        if fk["constrained_columns"] == ["paper_order_id"]:
            return fk["options"].get("ondelete")
    return None


def test_downgrade_restores_cascade_and_drops_trigger(migrated_db: str) -> None:
    assert _trigger_exists()
    assert _paper_order_fk_ondelete() == "RESTRICT"
    _fresh_caches()
    command.downgrade(build_alembic_config(), PREVIOUS_REVISION)
    _fresh_caches()
    assert not _trigger_exists()
    assert _paper_order_fk_ondelete() == "CASCADE"
    with session_scope(load_settings()) as session:
        function_count = session.execute(
            text("SELECT count(*) FROM pg_proc WHERE proname = 'order_submission_attempts_append_only'")
        ).scalar_one()
        version = session.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert function_count == 0
    assert version == PREVIOUS_REVISION
    _fresh_caches()
    command.upgrade(build_alembic_config(), "head")
    _fresh_caches()
    assert _trigger_exists()
    assert _paper_order_fk_ondelete() == "RESTRICT"
    with session_scope(load_settings()) as session:
        version = session.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    # 0029 head: head-agnostic (a later phase chains on 0029)
    assert version == ScriptDirectory.from_config(build_alembic_config()).get_current_head()


def test_orm_fk_matches_migration(migrated_db: str) -> None:
    orm_fk = next(
        fk
        for fk in OrderSubmissionAttempt.__table__.foreign_keys
        if fk.parent.name == "paper_order_id"
    )
    assert orm_fk.ondelete == "RESTRICT"
    assert _paper_order_fk_ondelete() == "RESTRICT"
    inspector = inspect(get_engine(load_settings()))
    names = {fk["name"] for fk in inspector.get_foreign_keys("order_submission_attempts")}
    assert FK in names
