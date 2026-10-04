"""Phase 20.1 migration 0027 tests: execution operations (REC-02, D-16..D-21, S-6).

The operation, its pinned intents and its Job links persist with DB-enforced closed states
and reasons and at most one OPEN operation per strategy (partial unique index).
"""

from __future__ import annotations

import itertools
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
from tests.support.operation_fixtures import (
    seed_operation,
    seed_operation_intent,
    seed_operation_job,
    seed_risk_run,
)
from tests.support.recovery_fixtures import OTHER, OWNER

from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.db.models import (
    OPEN_OPERATION_STATES,
    ExecutionOperation,
    ExecutionOperationIntent,
    ExecutionOperationJob,
    OrderSubmissionAttempt,
)
from trading_platform.db.models.execution_operation import (
    PAUSED_REASON_VALUES,
    REEVALUATION_FIXED_REASON_VALUES,
    TERMINATED_REASON_VALUES,
)
from trading_platform.db.session import clear_engine_cache, get_engine, session_scope

REVISION = "0027_phase20_1_execution_operations"
PREVIOUS_REVISION = "0026_phase20_1_recovery_records"
OPEN_STATES = ("running", "paused", "requires_reevaluation")


@pytest.fixture()
def migrated_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    with migrated_database(monkeypatch, "phase20_1_operations") as name:
        yield name


def _reason_for(state: str) -> str | None:
    return {
        "running": None,
        "completed": None,
        "paused": "awaiting_reconciliation",
        "requires_reevaluation": "evaluation_data_changed",
        "terminated": "cancelled_by_operator",
    }[state]


def _insert_operation(strategy_id: str, state: str, reason: str | None, *, session_date=None):
    with session_scope(load_settings()) as session:
        kwargs = {} if session_date is None else {"session_date": session_date}
        seed_operation(session, strategy_id=strategy_id, state=state, reason=reason, **kwargs)


def test_chain_is_linear_and_0027_follows_0026(migrated_db: str) -> None:
    script = ScriptDirectory.from_config(build_alembic_config())
    assert len(script.get_heads()) == 1
    revision = script.get_revision(REVISION)
    assert revision is not None
    assert revision.down_revision == PREVIOUS_REVISION
    assert REVISION in {r.revision for r in script.walk_revisions()}


def test_schema_has_the_three_tables_and_attempt_columns(migrated_db: str) -> None:
    inspector = inspect(get_engine(load_settings()))
    tables = set(inspector.get_table_names())
    assert {"execution_operations", "execution_operation_intents", "execution_operation_jobs"} <= (
        tables
    )
    operation_columns = {c["name"] for c in inspector.get_columns("execution_operations")}
    assert {
        "strategy_id",
        "as_of_session",
        "risk_run_id",
        "state",
        "reason",
        "basis_verification",
        "executor_job_id",
        "execution_epoch",
        "last_guarded_at",
        "ended_by",
        "end_reason",
    } <= operation_columns
    intent_columns = {c["name"] for c in inspector.get_columns("execution_operation_intents")}
    assert {
        "operation_id",
        "sequence",
        "client_order_id",
        "paper_order_id",
        "decision_fingerprint",
        "prior_execution_refs",
        "reference_price",
        "disposition",
    } <= intent_columns
    attempt_columns = {c["name"]: c for c in inspector.get_columns("order_submission_attempts")}
    for name in ("execution_epoch", "executor_job_id", "authorization_deadline"):
        assert attempt_columns[name]["nullable"] is True


def test_closed_state_set_is_enforced_by_the_database(migrated_db: str) -> None:
    with pytest.raises(IntegrityError):
        _insert_operation(OWNER, "flying", None)


@pytest.mark.parametrize(
    ("state", "reason"),
    [
        ("running", None),
        ("completed", None),
        *[("paused", r) for r in PAUSED_REASON_VALUES],
        *[("requires_reevaluation", r) for r in REEVALUATION_FIXED_REASON_VALUES],
        ("requires_reevaluation", "risk_limit_failed:insufficient_cash"),
        ("requires_reevaluation", "risk_limit_failed:strategy_allocation_cap"),
        *[("terminated", r) for r in TERMINATED_REASON_VALUES],
    ],
)
def test_every_closed_reason_is_accepted_for_its_state(
    migrated_db: str, state: str, reason: str | None
) -> None:
    _insert_operation(OWNER, state, reason)
    with session_scope(load_settings()) as session:
        stored = session.execute(select(ExecutionOperation)).scalar_one()
        assert (stored.state, stored.reason) == (state, reason)


@pytest.mark.parametrize(
    ("state", "reason"),
    [
        ("running", "awaiting_reconciliation"),
        ("completed", "cancelled_by_operator"),
        ("paused", None),
        ("paused", "made_up_reason"),
        ("paused", "evaluation_data_changed"),
        ("paused", "cancelled_by_operator"),
        ("requires_reevaluation", None),
        ("requires_reevaluation", "awaiting_reconciliation"),
        ("requires_reevaluation", "risk_limit_failed:"),
        ("requires_reevaluation", "risk_limit_failed:Insufficient_Cash"),
        ("requires_reevaluation", "risk_limit_failed:cash; DROP"),
        ("terminated", None),
        ("terminated", "price_unavailable"),
        ("terminated", "made_up_reason"),
    ],
)
def test_out_of_set_reason_for_a_state_is_rejected(
    migrated_db: str, state: str, reason: str | None
) -> None:
    with pytest.raises(IntegrityError):
        _insert_operation(OWNER, state, reason)


def test_reason_sets_match_the_documented_cardinalities() -> None:
    assert len(PAUSED_REASON_VALUES) == 12
    assert set(REEVALUATION_FIXED_REASON_VALUES) == {
        "evaluation_data_changed",
        "strategy_settings_changed",
    }
    assert len(TERMINATED_REASON_VALUES) == 3
    assert [s.value for s in OPEN_OPERATION_STATES] == list(OPEN_STATES)


def _open_index_states() -> set[str]:
    """The state literals of the partial unique index predicate, introspected from pg_indexes.

    PostgreSQL rewrites ``IN (...)`` as ``= ANY (ARRAY[...])``, so the literals are parsed out
    of the stored predicate instead of string-matching it.
    """

    with session_scope(load_settings()) as session:
        row = session.execute(
            text(
                "SELECT indexdef FROM pg_indexes WHERE tablename = 'execution_operations' "
                "AND indexname = 'uq_execution_operations_one_open_per_strategy'"
            )
        ).one()
    definition: str = row.indexdef
    assert "UNIQUE" in definition and "(strategy_id)" in definition
    predicate = definition.split(" WHERE ", 1)[1]
    return set(re.findall(r"'([a-z_]+)'", predicate))


@pytest.mark.parametrize(("first", "second"), list(itertools.product(OPEN_STATES, OPEN_STATES)))
def test_second_open_operation_for_strategy_fails_at_db_level(
    migrated_db: str, first: str, second: str
) -> None:
    from datetime import date

    # The failing constraint is the partial unique index whose predicate names EXACTLY the
    # three open states (introspected from pg_indexes).
    assert _open_index_states() == set(OPEN_STATES)
    _insert_operation(OWNER, first, _reason_for(first))
    with pytest.raises(IntegrityError) as excinfo:
        _insert_operation(OWNER, second, _reason_for(second), session_date=date(2024, 1, 8))
    assert "uq_execution_operations_one_open_per_strategy" in str(excinfo.value)


def test_open_index_predicate_names_exactly_the_three_open_states(migrated_db: str) -> None:
    assert _open_index_states() == set(OPEN_STATES)


def test_terminated_or_completed_never_blocks_a_new_open_operation(migrated_db: str) -> None:
    from datetime import date

    _insert_operation(OWNER, "terminated", "cancelled_by_operator")
    _insert_operation(OWNER, "completed", None, session_date=date(2024, 1, 8))
    _insert_operation(OWNER, "running", None, session_date=date(2024, 1, 9))
    with session_scope(load_settings()) as session:
        assert len(session.execute(select(ExecutionOperation)).scalars().all()) == 3


def test_another_strategy_may_hold_its_own_open_operation(migrated_db: str) -> None:
    _insert_operation(OWNER, "running", None)
    _insert_operation(OTHER, "running", None)


def test_second_operation_for_the_same_pinned_risk_run_fails_at_db_level(
    migrated_db: str,
) -> None:
    with session_scope(load_settings()) as session:
        run = seed_risk_run(session)
        seed_operation(session, state="completed", reason=None, risk_run=run)
    with pytest.raises(IntegrityError), session_scope(load_settings()) as session:
        seed_operation(session, state="completed", reason=None, risk_run=run)


def test_intent_client_order_id_is_unique_per_operation_not_globally(migrated_db: str) -> None:
    from datetime import date

    with session_scope(load_settings()) as session:
        first = seed_operation(session, state="completed", reason=None)
        second = seed_operation(
            session, state="completed", reason=None, session_date=date(2024, 1, 8)
        )
        a = seed_operation_intent(session, first, sequence=1)
        # Same client_order_id in ANOTHER operation is legal (identities repeat per session).
        session.add(
            ExecutionOperationIntent(
                operation_id=second.id,
                sequence=1,
                symbol_id=a.row.symbol_id,
                side="buy",
                quantity=a.row.quantity,
                client_order_id=a.row.client_order_id,
                decision_fingerprint="f" * 64,
                prior_execution_refs=[],
                disposition="open",
            )
        )
        session.flush()
        with pytest.raises(IntegrityError), session.begin_nested():
            session.add(
                ExecutionOperationIntent(
                    operation_id=first.id,
                    sequence=2,
                    symbol_id=a.row.symbol_id,
                    side="buy",
                    quantity=a.row.quantity,
                    client_order_id=a.row.client_order_id,
                    decision_fingerprint="f" * 64,
                    prior_execution_refs=[],
                    disposition="open",
                )
            )
            session.flush()


def test_intent_sequence_is_unique_per_operation_and_disposition_is_closed(
    migrated_db: str,
) -> None:
    with session_scope(load_settings()) as session:
        operation = seed_operation(session, state="running", reason=None)
        seed_operation_intent(session, operation, sequence=1)
        with pytest.raises(IntegrityError), session.begin_nested():
            seed_operation_intent(session, operation, sequence=1, ticker="MSFT")
        with pytest.raises(IntegrityError), session.begin_nested():
            seed_operation_intent(
                session, operation, sequence=2, ticker="NVDA", disposition="sent_somehow"
            )


def test_job_link_is_nulled_when_the_job_is_deleted(migrated_db: str) -> None:
    with session_scope(load_settings()) as session:
        job = seed_operation_job(session)
        operation = seed_operation(session, state="completed", reason=None, jobs=[(job, "start")])
        job_id, operation_id = job.id, operation.id
    with session_scope(load_settings()) as session:
        session.execute(text("DELETE FROM jobs WHERE id = :id"), {"id": job_id})
    with session_scope(load_settings()) as session:
        link = session.execute(
            select(ExecutionOperationJob).where(ExecutionOperationJob.operation_id == operation_id)
        ).scalar_one()
        assert link.job_id is None
        assert link.mode == "start"
    with pytest.raises(IntegrityError), session_scope(load_settings()) as session:
        job = seed_operation_job(session)
        session.add(ExecutionOperationJob(operation_id=operation_id, job_id=job.id, mode="resume"))
        session.flush()


def test_paper_order_delete_nulls_the_intent_link(migrated_db: str) -> None:
    with session_scope(load_settings()) as session:
        operation = seed_operation(session, state="running", reason=None)
        seeded = seed_operation_intent(session, operation, with_order=True)
        intent_id = seeded.row.id
        order_id = seeded.order.id  # type: ignore[union-attr]
    with session_scope(load_settings()) as session:
        session.execute(text("DELETE FROM paper_orders WHERE id = :id"), {"id": order_id})
    with session_scope(load_settings()) as session:
        stored = session.get(ExecutionOperationIntent, intent_id)
        assert stored is not None
        assert stored.paper_order_id is None


def test_operation_with_intents_cannot_be_deleted(migrated_db: str) -> None:
    with session_scope(load_settings()) as session:
        operation = seed_operation(session, state="running", reason=None)
        seed_operation_intent(session, operation)
        operation_id = operation.id
    with pytest.raises(IntegrityError), session_scope(load_settings()) as session:
        session.execute(
            text("DELETE FROM execution_operations WHERE id = :id"), {"id": operation_id}
        )


def test_epoch_defaults_to_zero_and_cannot_be_negative(migrated_db: str) -> None:
    with session_scope(load_settings()) as session:
        operation = seed_operation(session, state="running", reason=None)
        assert operation.execution_epoch == 0
        assert operation.executor_job_id is None
        assert operation.last_guarded_at is None
    with pytest.raises(IntegrityError), session_scope(load_settings()) as session:
        seed_operation(session, state="completed", reason=None, epoch=-1)


def test_attempt_rows_carry_the_fencing_columns(migrated_db: str) -> None:
    from tests.support.submission_attempts import seed_paper_order_row

    with session_scope(load_settings()) as session:
        order_id, _run_id = seed_paper_order_row(session)
        session.add(
            OrderSubmissionAttempt(
                paper_order_id=order_id,
                attempt_number=1,
                execution_epoch=3,
                executor_job_id=uuid.uuid4(),
            )
        )
        session.flush()
        stored = session.execute(select(OrderSubmissionAttempt)).scalar_one()
        assert stored.execution_epoch == 3
        assert stored.authorization_deadline is None


def test_downgrade_drops_tables_and_columns_and_reupgrade_restores_them(
    migrated_db: str,
) -> None:
    config = build_alembic_config()
    command.downgrade(config, PREVIOUS_REVISION)
    clear_settings_cache()
    clear_engine_cache()
    inspector = inspect(get_engine(load_settings()))
    assert "execution_operations" not in inspector.get_table_names()
    assert "execution_operation_intents" not in inspector.get_table_names()
    assert "execution_operation_jobs" not in inspector.get_table_names()
    attempt_columns = {c["name"] for c in inspector.get_columns("order_submission_attempts")}
    assert "execution_epoch" not in attempt_columns
    command.upgrade(config, "head")
    clear_settings_cache()
    clear_engine_cache()
    inspector = inspect(get_engine(load_settings()))
    assert "execution_operations" in inspector.get_table_names()
    attempt_columns = {c["name"] for c in inspector.get_columns("order_submission_attempts")}
    assert {"execution_epoch", "executor_job_id", "authorization_deadline"} <= attempt_columns
    indexes = {i["name"] for i in inspector.get_indexes("execution_operations")}
    assert "uq_execution_operations_one_open_per_strategy" in indexes


def test_orm_metadata_matches_migration(migrated_db: str) -> None:
    inspector = inspect(get_engine(load_settings()))
    for model in (ExecutionOperation, ExecutionOperationIntent, ExecutionOperationJob):
        table = model.__table__
        db_columns = {c["name"]: c for c in inspector.get_columns(table.name)}
        assert set(db_columns) == {c.name for c in table.columns}, table.name
        for column in table.columns:
            assert db_columns[column.name]["nullable"] == column.nullable, (
                table.name,
                column.name,
            )
        db_checks = {c["name"] for c in inspector.get_check_constraints(table.name)}
        orm_checks = {
            str(c.name) for c in table.constraints if c.__class__.__name__ == "CheckConstraint"
        }
        assert db_checks == orm_checks, table.name
        db_indexes = {i["name"] for i in inspector.get_indexes(table.name)}
        assert {str(i.name) for i in table.indexes} <= db_indexes, table.name
        db_uniques = {u["name"] for u in inspector.get_unique_constraints(table.name)}
        orm_uniques = {
            str(c.name) for c in table.constraints if c.__class__.__name__ == "UniqueConstraint"
        }
        assert db_uniques == orm_uniques, table.name
    attempt_columns = {c["name"] for c in inspector.get_columns("order_submission_attempts")}
    assert attempt_columns == {c.name for c in OrderSubmissionAttempt.__table__.columns}
