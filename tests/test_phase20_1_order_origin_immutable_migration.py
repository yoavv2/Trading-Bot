"""Phase 20.1 migration 0029 tests: order origin immutability and durable evidence protection.

CR-01 (user decision 2026-10-05): ``paper_orders.strategy_run_id`` is the ORIGIN run and cannot
change. V-1 (approved): ``order_events`` is append-only and no evidence table can be truncated,
directly or through ``TRUNCATE ... CASCADE``. V-2 (retention policy change): no DELETE, direct
or by parent cascade, can erase attribution or recovery evidence. W4: the reconciliation evidence
the recovery gate reads is protected too.

Every refused statement is followed by a row-count comparison proving nothing was removed.
"""

from __future__ import annotations

import sys
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alembic import command
from alembic.script import ScriptDirectory
from scripts.migrate import build_alembic_config
from tests.support.migrated_db import migrated_database
from tests.support.operation_fixtures import seed_operation, seed_operation_intent
from tests.support.recovery_fixtures import (
    OWNER,
    at,
    seed_account_run,
    seed_intent,
    seed_job,
    seed_operation_bound_intent,
    seed_paper_run,
    seed_strategy_reconciliation,
    seed_uncertain_session,
)
from tests.support.submission_attempts import seed_paper_order_row

from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.db.models import (
    AccountSnapshot,
    AttemptOutcomeClass,
    ExecutionOperationIntent,
    Job,
    JobStatus,
    OrderEvent,
    OrderLifecycleState,
    OrderSubmissionAttempt,
    OrderTransitionOutcome,
    PaperFill,
    PaperOrder,
    StrategyRun,
    StrategyRunType,
)
from trading_platform.db.models.order_event import OrderTransitionEventType
from trading_platform.db.session import clear_engine_cache, session_scope
from trading_platform.services.execution.transition import (
    OrderTransitionRequest,
    apply_order_transition,
)
from trading_platform.services.recovery import (
    GateCode,
    get_job_recovery,
    strategy_recovery_status,
)

REVISION = "0029_phase20_1_order_origin_immutable"
PREVIOUS_REVISION = "0028_phase20_1_attempt_log_append_only"

ORIGIN_TRIGGER = "trg_paper_orders_origin_run_immutable"
LEDGER_TRIGGER = "trg_order_events_append_only"
DELETE_TABLES = (
    "paper_orders",
    "order_events",
    "strategy_runs",
    "jobs",
    "execution_operations",
    "execution_operation_intents",
    "execution_operation_jobs",
    "paper_fills",
    "account_reconciliation_runs",
)
TRUNCATE_TABLES = (
    "order_submission_attempts",
    "paper_orders",
    "order_events",
    "paper_fills",
    "strategy_runs",
    "jobs",
    "execution_operations",
    "execution_operation_intents",
    "execution_operation_jobs",
    "recovery_records",
    "external_broker_activity",
    "account_reconciliation_runs",
)
TRIGGER_NAMES = (
    [ORIGIN_TRIGGER, LEDGER_TRIGGER]
    + [f"trg_{table}_no_delete" for table in DELETE_TABLES]
    + [f"trg_{table}_no_truncate" for table in TRUNCATE_TABLES]
)
FUNCTION_NAMES = (
    "paper_orders_origin_run_immutable",
    "order_events_append_only",
    "phase20_1_evidence_no_delete",
    "phase20_1_evidence_no_truncate",
)
COUNTED_TABLES = (
    "paper_orders",
    "order_events",
    "order_submission_attempts",
    "strategy_runs",
    "strategies",
    "symbols",
    "jobs",
    "paper_fills",
    "execution_operations",
    "execution_operation_intents",
    "execution_operation_jobs",
    "recovery_records",
    "external_broker_activity",
    "account_reconciliation_runs",
)

PENDING = OrderLifecycleState.PENDING_SUBMISSION


@pytest.fixture()
def migrated_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    with migrated_database(monkeypatch, "phase20_1_origin_immutable") as name:
        yield name


def _fresh_caches() -> None:
    clear_settings_cache()
    clear_engine_cache()


def _head() -> str:
    head = ScriptDirectory.from_config(build_alembic_config()).get_current_head()
    assert head is not None
    return head


def _counts() -> dict[str, int]:
    with session_scope(load_settings()) as session:
        return {
            table: int(session.execute(text(f"SELECT count(*) FROM {table}")).scalar_one())
            for table in COUNTED_TABLES
        }


def _scalar(sql: str, **params: object) -> Any:
    with session_scope(load_settings()) as session:
        return session.execute(text(sql), params).scalar_one()


def _refuse(sql: str, **params: object) -> IntegrityError:
    """Run ``sql`` and require the 0029 trigger (SQLSTATE 23000) to refuse it."""

    with pytest.raises(IntegrityError) as info, session_scope(load_settings()) as session:
        session.execute(text(sql), params)
    assert getattr(info.value.orig, "sqlstate", None) == "23000", info.value
    return info.value


def _refuse_and_keep(sql: str, **params: object) -> IntegrityError:
    before = _counts()
    error = _refuse(sql, **params)
    assert _counts() == before
    return error


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


def _event(
    session: Any,
    order_id: uuid.UUID,
    run_id: uuid.UUID,
    event_type: OrderTransitionEventType,
    *,
    outcome: OrderTransitionOutcome = OrderTransitionOutcome.ACCEPTED,
    from_state: OrderLifecycleState = PENDING,
) -> uuid.UUID:
    row = OrderEvent(
        paper_order_id=order_id,
        strategy_run_id=run_id,
        from_state=from_state,
        to_state=PENDING,
        event_type=event_type,
        outcome=outcome,
        event_at=at(0),
        details={},
    )
    session.add(row)
    session.flush()
    return row.id


def _world() -> dict[str, uuid.UUID]:
    """Every evidence kind: a flagged paper-session Job J1 with origin run R1 and an ambiguous
    order O, a retrying Job J2 / run R2 that holds only an accepted retry_requested event and
    the second attempt row of O, an operation with an intent and an operation-Job link, a fill,
    plus a legacy zero-attempt order, an operation-bound order and a terminal filled order."""

    with session_scope(load_settings()) as session:
        job1, run1, order = seed_uncertain_session(session)
        job2 = seed_job(session, uncertain=False, status=JobStatus.CANCELLED, completed_at=at(2))
        run2 = seed_paper_run(session, job2)
        _event(session, order.id, run1.id, OrderTransitionEventType.INTENT_REGISTERED)
        retry_event = _event(session, order.id, run2.id, OrderTransitionEventType.RETRY_REQUESTED)
        session.add(
            OrderSubmissionAttempt(
                paper_order_id=order.id,
                strategy_run_id=run2.id,
                attempt_number=2,
                started_at=at(3),
                completed_at=at(3),
                outcome_class=AttemptOutcomeClass.AMBIGUOUS.value,
                executor_job_id=job2.id,
            )
        )
        fill = PaperFill(
            paper_order_id=order.id,
            symbol_id=order.symbol_id,
            broker_fill_id=f"f-{uuid.uuid4().hex[:8]}",
            broker_order_id=f"b-{uuid.uuid4().hex[:8]}",
            side="buy",
            quantity=Decimal("1"),
            price=Decimal("100"),
            filled_at=at(4),
            broker_payload={},
        )
        session.add(fill)
        legacy = seed_intent(session, run1, status=PENDING, attempts=(), ticker="MSFT")
        filled = seed_intent(
            session,
            run1,
            status=OrderLifecycleState.FILLED,
            attempts=(),
            ticker="TSLA",
            broker_order_id=f"b-{uuid.uuid4().hex[:8]}",
        )
        operation = seed_operation(
            session,
            state="terminated",
            reason="cancelled_by_operator",
            executor_job=job1,
            jobs=[(job1, "start")],
        )
        seeded = seed_operation_intent(session, operation, with_order=True, ticker="NVDA")
        assert seeded.order is not None
        bound = seed_operation_bound_intent(session, run1, attempts=(), ticker="AMD")
        session.flush()
        return {
            "job1": job1.id,
            "job2": job2.id,
            "run1": run1.id,
            "run2": run2.id,
            "order": order.id,
            "retry_event": retry_event,
            "fill": fill.id,
            "legacy_order": legacy.id,
            "filled_order": filled.id,
            "bound_order": bound.id,
            "operation": operation.id,
            "operation_intent": seeded.row.id,
            "operation_order": seeded.order.id,
        }


# ---------------------------------------------------------------------------
# Origin guard and order_events append-only
# ---------------------------------------------------------------------------


def test_order_origin_run_cannot_be_reparented(migrated_db: str) -> None:
    with session_scope(load_settings()) as session:
        order_id, run_id = seed_paper_order_row(session)
        other = StrategyRun(
            strategy_id=session.get(StrategyRun, run_id).strategy_id,  # type: ignore[union-attr]
            run_type=StrategyRunType.PAPER_EXECUTION,
        )
        session.add(other)
        session.flush()
        other_id = other.id
    error = _refuse(
        "UPDATE paper_orders SET strategy_run_id = :r WHERE id = :o", r=other_id, o=order_id
    )
    assert "origin run" in str(error)
    assert _scalar("SELECT strategy_run_id FROM paper_orders WHERE id = :o", o=order_id) == run_id
    # An UPDATE that also changes other columns is refused as a whole.
    _refuse(
        "UPDATE paper_orders SET strategy_run_id = :r, status = 'submitted' WHERE id = :o",
        r=other_id,
        o=order_id,
    )
    assert _scalar("SELECT status::text FROM paper_orders WHERE id = :o", o=order_id) == (
        "pending_submission"
    )


def test_same_value_and_other_column_updates_are_allowed(migrated_db: str) -> None:
    with session_scope(load_settings()) as session:
        order_id, run_id = seed_paper_order_row(session)
    with session_scope(load_settings()) as session:
        session.execute(
            text("UPDATE paper_orders SET strategy_run_id = strategy_run_id WHERE id = :o"),
            {"o": order_id},
        )
    with session_scope(load_settings()) as session:
        order = session.get(PaperOrder, order_id)
        assert order is not None
        order.status = OrderLifecycleState.SUBMITTED
        order.submission_attempt_count = 3
    with session_scope(load_settings()) as session:
        order = session.get(PaperOrder, order_id)
        assert order is not None
        assert order.status is OrderLifecycleState.SUBMITTED
        assert order.submission_attempt_count == 3
        assert order.strategy_run_id == run_id


def test_order_events_rows_cannot_be_updated(migrated_db: str) -> None:
    with session_scope(load_settings()) as session:
        order_id, run_id = seed_paper_order_row(session)
    result = apply_order_transition(
        order_id,
        OrderTransitionRequest(
            strategy_run_id=run_id,
            event_type=OrderTransitionEventType.INTENT_REGISTERED,
            event_at=datetime(2024, 1, 5, 14, 30, tzinfo=UTC),
        ),
        settings=load_settings(),
    )
    assert result.outcome is OrderTransitionOutcome.ACCEPTED  # inserts stay legal
    for assignment in ("details = '{}'::json", "outcome = 'rejected'", "event_at = now()"):
        error = _refuse_and_keep(
            f"UPDATE order_events SET {assignment} WHERE id = :id", id=result.event_id
        )
        assert "append-only" in str(error)
    assert _scalar("SELECT count(*) FROM order_events WHERE id = :id", id=result.event_id) == 1
    apply_order_transition(
        order_id,
        OrderTransitionRequest(
            strategy_run_id=run_id,
            event_type=OrderTransitionEventType.BROKER_ACKNOWLEDGED,
            event_at=datetime(2024, 1, 5, 14, 35, tzinfo=UTC),
        ),
        settings=load_settings(),
    )
    assert _scalar("SELECT count(*) FROM order_events WHERE paper_order_id = :o", o=order_id) == 2


# ---------------------------------------------------------------------------
# DELETE: direct
# ---------------------------------------------------------------------------

DIRECT_DELETES = [
    pytest.param("DELETE FROM paper_orders WHERE id = :id", "legacy_order", id="legacy_order"),
    pytest.param("DELETE FROM paper_orders WHERE id = :id", "bound_order", id="bound_order"),
    pytest.param("DELETE FROM paper_orders WHERE id = :id", "filled_order", id="filled_order"),
    pytest.param("DELETE FROM order_events WHERE id = :id", "retry_event", id="order_events"),
    pytest.param("DELETE FROM execution_operations WHERE id = :id", "operation", id="operations"),
    pytest.param(
        "DELETE FROM execution_operation_intents WHERE id = :id",
        "operation_intent",
        id="operation_intents",
    ),
    pytest.param(
        "DELETE FROM execution_operation_jobs WHERE operation_id = :id",
        "operation",
        id="operation_jobs",
    ),
    pytest.param("DELETE FROM paper_fills WHERE id = :id", "fill", id="paper_fills"),
]


@pytest.mark.parametrize(("sql", "key"), DIRECT_DELETES)
def test_evidence_rows_cannot_be_deleted_directly(migrated_db: str, sql: str, key: str) -> None:
    ids = _world()
    _refuse_and_keep(sql, id=ids[key])


# ---------------------------------------------------------------------------
# DELETE: parent cascade
# ---------------------------------------------------------------------------


def test_deleting_a_strategy_cannot_erase_its_runs_and_orders(migrated_db: str) -> None:
    with session_scope(load_settings()) as session:
        order_id, run_id = seed_paper_order_row(session)
        strategy_id = session.get(StrategyRun, run_id).strategy_id  # type: ignore[union-attr]
    _refuse_and_keep("DELETE FROM strategies WHERE id = :id", id=strategy_id)
    assert _scalar("SELECT count(*) FROM paper_orders WHERE id = :o", o=order_id) == 1


def test_deleting_a_symbol_cannot_erase_its_orders(migrated_db: str) -> None:
    with session_scope(load_settings()) as session:
        order_id, _run_id = seed_paper_order_row(session)
        symbol_id = session.get(PaperOrder, order_id).symbol_id  # type: ignore[union-attr]
    before = _counts()
    with pytest.raises(IntegrityError), session_scope(load_settings()) as session:
        session.execute(text("DELETE FROM symbols WHERE id = :id"), {"id": symbol_id})
    assert _counts() == before


def test_deleting_the_origin_run_is_refused(migrated_db: str) -> None:
    ids = _world()
    _refuse_and_keep("DELETE FROM strategy_runs WHERE id = :id", id=ids["run1"])


def test_deleting_a_retrying_run_keeps_its_event_and_attempt_attribution(
    migrated_db: str,
) -> None:
    ids = _world()
    _refuse_and_keep("DELETE FROM strategy_runs WHERE id = :id", id=ids["run2"])
    assert (
        _scalar("SELECT strategy_run_id FROM order_events WHERE id = :id", id=ids["retry_event"])
        == ids["run2"]
    )
    with session_scope(load_settings()) as session:
        row = session.execute(
            text(
                "SELECT strategy_run_id, executor_job_id FROM order_submission_attempts "
                "WHERE paper_order_id = :o AND attempt_number = 2"
            ),
            {"o": ids["order"]},
        ).one()
    assert row.strategy_run_id == ids["run2"]
    assert row.executor_job_id == ids["job2"]


def test_order_less_paper_execution_run_cannot_be_deleted(migrated_db: str) -> None:
    with session_scope(load_settings()) as session:
        job = seed_job(session, completed_at=at(0))
        run = seed_paper_run(session, job)
        run_id = run.id
    _refuse_and_keep("DELETE FROM strategy_runs WHERE id = :id", id=run_id)


@pytest.mark.parametrize(
    ("job_type", "uncertain"),
    [("paper-session", True), ("paper-session", False), ("broker-order-sync", False)],
    ids=["flagged_paper_session", "unflagged_paper_session", "unflagged_broker_order_sync"],
)
def test_protected_job_deletes_are_refused_and_links_stay(
    migrated_db: str, job_type: str, uncertain: bool
) -> None:
    ids = _world()
    with session_scope(load_settings()) as session:
        job = seed_job(
            session,
            job_type=job_type,
            uncertain=uncertain,
            status=JobStatus.FAILED if uncertain else JobStatus.SUCCEEDED,
            completed_at=at(0),
        )
        run = seed_paper_run(session, job)
        job_id, run_id = job.id, run.id
    _refuse_and_keep("DELETE FROM jobs WHERE id = :id", id=job_id)
    assert _scalar("SELECT job_id FROM strategy_runs WHERE id = :id", id=run_id) == job_id
    _refuse_and_keep("DELETE FROM jobs WHERE id = :id", id=ids["job1"])
    assert _scalar("SELECT job_id FROM strategy_runs WHERE id = :id", id=ids["run1"]) == ids["job1"]
    assert (
        _scalar(
            "SELECT job_id FROM execution_operation_jobs WHERE operation_id = :id",
            id=ids["operation"],
        )
        == ids["job1"]
    )


def test_outcome_uncertain_job_of_any_type_cannot_be_deleted(migrated_db: str) -> None:
    with session_scope(load_settings()) as session:
        job = seed_job(session, job_type="phase-probe", uncertain=True, completed_at=at(0))
        job_id = job.id
    _refuse_and_keep("DELETE FROM jobs WHERE id = :id", id=job_id)


def test_non_evidence_deletes_still_work(migrated_db: str) -> None:
    ids = _world()
    with session_scope(load_settings()) as session:
        strategy_id = session.get(StrategyRun, ids["run1"]).strategy_id  # type: ignore[union-attr]
        backtest = StrategyRun(strategy_id=strategy_id, run_type=StrategyRunType.BACKTEST)
        session.add(backtest)
        probe = seed_job(
            session, job_type="phase-probe", uncertain=False, status=JobStatus.SUCCEEDED
        )
        account_job = Job(
            job_type="reconciliation",
            payload={"scope": "account"},
            status=JobStatus.SUCCEEDED,
            completed_at=at(5),
        )
        session.add(account_job)
        session.flush()
        account_run = seed_account_run(session, completed_at=at(6))
        account_run.job_id = account_job.id
        unreferenced = Job(
            job_type="reconciliation",
            payload={"scope": "account"},
            status=JobStatus.SUCCEEDED,
            completed_at=at(7),
        )
        session.add(unreferenced)
        snapshot = AccountSnapshot(
            snapshot_at=at(8),
            cash=Decimal("1"),
            gross_exposure=Decimal("0"),
            total_equity=Decimal("1"),
            buying_power=Decimal("1"),
        )
        session.add(snapshot)
        session.flush()
        backtest_id, probe_id, account_job_id = backtest.id, probe.id, account_job.id
        unreferenced_id, snapshot_id, account_run_id = unreferenced.id, snapshot.id, account_run.id
    with session_scope(load_settings()) as session:
        for table, row_id in (
            ("strategy_runs", backtest_id),
            ("jobs", probe_id),
            ("jobs", account_job_id),
            ("jobs", unreferenced_id),
            ("account_snapshots", snapshot_id),
        ):
            session.execute(text(f"DELETE FROM {table} WHERE id = :id"), {"id": row_id})
    assert _scalar("SELECT count(*) FROM jobs WHERE id = :id", id=account_job_id) == 0
    assert _scalar("SELECT count(*) FROM strategy_runs WHERE id = :id", id=backtest_id) == 0
    assert _scalar("SELECT count(*) FROM account_snapshots WHERE id = :id", id=snapshot_id) == 0
    # The account-level Job delete only nulled the link; the reconciliation run survives.
    assert (
        _scalar("SELECT job_id FROM account_reconciliation_runs WHERE id = :id", id=account_run_id)
        is None
    )


# ---------------------------------------------------------------------------
# Refused deletes keep the recovery gate (threat T-20.1-27-04)
# ---------------------------------------------------------------------------


def _gate() -> GateCode | None:
    with session_scope(load_settings()) as session:
        return strategy_recovery_status(session, OWNER).gate_code


def test_refused_legacy_order_delete_keeps_the_gate_and_the_intent(migrated_db: str) -> None:
    with session_scope(load_settings()) as session:
        job = seed_job(session, completed_at=at(0))
        run = seed_paper_run(session, job)
        order = seed_intent(session, run, status=PENDING, attempts=())
        operation = seed_operation(session, state="terminated", reason="cancelled_by_operator")
        intent = ExecutionOperationIntent(
            operation_id=operation.id,
            sequence=1,
            symbol_id=order.symbol_id,
            side=order.side,
            quantity=order.quantity,
            reference_price=Decimal("100"),
            client_order_id=order.client_order_id,
            paper_order_id=order.id,
            decision_fingerprint=uuid.uuid4().hex + uuid.uuid4().hex,
            prior_execution_refs=[],
            disposition="open",
            created_at=order.created_at + timedelta(seconds=1),  # LEGACY: intent after order
        )
        session.add(intent)
        session.flush()
        order_id, intent_id = order.id, intent.id
    assert _gate() is GateCode.OUTCOME_UNRESOLVED
    _refuse_and_keep("DELETE FROM paper_orders WHERE id = :id", id=order_id)
    assert _gate() is GateCode.OUTCOME_UNRESOLVED
    with session_scope(load_settings()) as session:
        row = session.execute(
            text(
                "SELECT paper_order_id, disposition FROM execution_operation_intents WHERE id = :id"
            ),
            {"id": intent_id},
        ).one()
    assert (row.paper_order_id, row.disposition) == (order_id, "open")


def test_refused_order_less_run_delete_keeps_the_gate_and_r3(migrated_db: str) -> None:
    with session_scope(load_settings()) as session:
        job = seed_job(session, completed_at=at(0))
        run = seed_paper_run(session, job)
        job_id, run_id = job.id, run.id

    def read() -> tuple[GateCode | None, dict[str, Any]]:
        with session_scope(load_settings()) as session:
            view = get_job_recovery(session, job_id)
            # The evidence package carries evaluation-time stamps; the verdict fields are stable.
            return (
                strategy_recovery_status(session, OWNER).gate_code,
                {k: view[k] for k in ("resolved", "gate_code", "intents")},
            )

    before = read()
    assert before[0] is GateCode.OUTCOME_UNRESOLVED
    assert "execution_path_unproven" in repr(before[1])
    _refuse_and_keep("DELETE FROM strategy_runs WHERE id = :id", id=run_id)
    assert read() == before


# ---------------------------------------------------------------------------
# W4: reconciliation evidence
# ---------------------------------------------------------------------------


def _saf01_flagged_job() -> None:
    """A flagged Job (effect at minute 0) whose only order is an operation-bound unsent one: the
    SAF-01 shape whose gate depends solely on the reconciliation evidence."""

    with session_scope(load_settings()) as session:
        job = seed_job(session, completed_at=at(0))
        run = seed_paper_run(session, job)
        seed_operation_bound_intent(session, run, status=PENDING, attempts=())


def test_newer_dirty_account_reconciliation_cannot_be_deleted_to_release_the_gate(
    migrated_db: str,
) -> None:
    _saf01_flagged_job()
    with session_scope(load_settings()) as session:
        seed_account_run(session, completed_at=at(10))
        dirty = seed_account_run(
            session,
            completed_at=at(20),
            blocks=True,
            status="succeeded",
            unresolved_reasons=["unexplained_position"],
        )
        dirty_id = dirty.id
    assert _gate() is GateCode.RECONCILIATION_NOT_CLEAN
    _refuse_and_keep("DELETE FROM account_reconciliation_runs WHERE id = :id", id=dirty_id)
    assert _gate() is GateCode.RECONCILIATION_NOT_CLEAN


def test_newer_dirty_strategy_reconciliation_cannot_be_deleted_to_release_the_gate(
    migrated_db: str,
) -> None:
    _saf01_flagged_job()
    with session_scope(load_settings()) as session:
        seed_account_run(session, completed_at=at(5))
        seed_strategy_reconciliation(session, completed_at=at(10))
        dirty = seed_strategy_reconciliation(session, completed_at=at(20), blocks=True)
        dirty_id, dirty_job_id = dirty.id, dirty.job_id
    assert _gate() is GateCode.RECONCILIATION_NOT_CLEAN
    # The run itself ...
    _refuse_and_keep("DELETE FROM strategy_runs WHERE id = :id", id=dirty_id)
    assert _gate() is GateCode.RECONCILIATION_NOT_CLEAN
    # ... and (W-N2) the Job the strategy_latest join goes through.
    _refuse_and_keep("DELETE FROM jobs WHERE id = :id", id=dirty_job_id)
    assert _scalar("SELECT job_id FROM strategy_runs WHERE id = :id", id=dirty_id) == dirty_job_id
    assert _gate() is GateCode.RECONCILIATION_NOT_CLEAN


# ---------------------------------------------------------------------------
# TRUNCATE
# ---------------------------------------------------------------------------

# Tables no foreign key references: a plain TRUNCATE reaches the trigger (a referenced table is
# refused by PostgreSQL itself with SQLSTATE 0A000 unless CASCADE is given).
UNREFERENCED = (
    "order_submission_attempts",
    "order_events",
    "paper_fills",
    "execution_operation_jobs",
    "recovery_records",
    "external_broker_activity",
    "account_reconciliation_runs",
    "execution_operation_intents",
)


@pytest.mark.parametrize("table", TRUNCATE_TABLES)
def test_evidence_tables_cannot_be_truncated(migrated_db: str, table: str) -> None:
    _world()
    before = _counts()
    for statement in (
        [f"TRUNCATE {table}", f"TRUNCATE {table} CASCADE"]
        if table in UNREFERENCED
        else [f"TRUNCATE {table} CASCADE"]
    ):
        error = _refuse(statement)
        assert f"{table} is evidence" in str(error)
    assert _counts() == before


@pytest.mark.parametrize(
    "parent", ["paper_orders", "strategy_runs", "strategies", "symbols", "jobs", "risk_events"]
)
def test_truncate_cascade_from_any_parent_is_refused(migrated_db: str, parent: str) -> None:
    _world()
    before = _counts()
    _refuse(f"TRUNCATE {parent} CASCADE")
    assert _counts() == before
    assert _scalar("SELECT count(*) FROM paper_orders") == before["paper_orders"]
    assert _scalar("SELECT count(*) FROM order_events") == before["order_events"]
    assert _scalar("SELECT count(*) FROM strategy_runs") == before["strategy_runs"]


# ---------------------------------------------------------------------------
# 0028 unchanged
# ---------------------------------------------------------------------------


def test_0028_protections_unchanged_under_0029(migrated_db: str) -> None:
    with session_scope(load_settings()) as session:
        order_id, run_id = seed_paper_order_row(session)
    attempt_id = uuid.uuid4()
    with session_scope(load_settings()) as session:
        session.execute(
            text(
                "INSERT INTO order_submission_attempts "
                "(id, paper_order_id, strategy_run_id, attempt_number, started_at) "
                "VALUES (:id, :o, :r, 1, now())"
            ),
            {"id": attempt_id, "o": order_id, "r": run_id},
        )
    with session_scope(load_settings()) as session:  # shape A: the single completion
        session.execute(
            text(
                "UPDATE order_submission_attempts SET outcome_class = 'accepted', "
                "completed_at = now(), updated_at = now() WHERE id = :id"
            ),
            {"id": attempt_id},
        )
    with (
        pytest.raises(IntegrityError, match="complete-once"),
        session_scope(load_settings()) as session,
    ):
        session.execute(
            text("UPDATE order_submission_attempts SET outcome_class = 'rejected' WHERE id = :id"),
            {"id": attempt_id},
        )
    with (
        pytest.raises(IntegrityError, match="append-only"),
        session_scope(load_settings()) as session,
    ):
        session.execute(
            text("DELETE FROM order_submission_attempts WHERE id = :id"), {"id": attempt_id}
        )
    assert _scalar("SELECT count(*) FROM order_submission_attempts") == 1
    assert _scalar("SELECT count(*) FROM paper_orders WHERE id = :o", o=order_id) == 1
    with pytest.raises(IntegrityError), session_scope(load_settings()) as session:
        session.execute(text("DELETE FROM paper_orders WHERE id = :o"), {"o": order_id})
    assert _scalar("SELECT count(*) FROM paper_orders WHERE id = :o", o=order_id) == 1


# ---------------------------------------------------------------------------
# Historical CR-01 data shape
# ---------------------------------------------------------------------------


def test_historical_cr01_shape_upgrades_unchanged_and_is_attributed_to_the_original_job(
    migrated_db: str,
) -> None:
    """A database that already holds the pre-fix shape (the order on the retrying run, J1's
    registration only in its intent_registered event) upgrades unchanged; the 20.1-26 predicate
    attributes the order to the ORIGINAL Job from the recorded history."""

    _fresh_caches()
    command.downgrade(build_alembic_config(), PREVIOUS_REVISION)
    _fresh_caches()
    assert _scalar("SELECT count(*) FROM pg_trigger WHERE tgname = :n", n=ORIGIN_TRIGGER) == 0
    with session_scope(load_settings()) as session:
        flagged = seed_job(session, completed_at=at(0))
        run1 = seed_paper_run(session, flagged)
        executor = seed_job(
            session, uncertain=False, status=JobStatus.SUCCEEDED, completed_at=at(1)
        )
        run2 = seed_paper_run(session, executor)
        order = seed_operation_bound_intent(session, run2, status=PENDING, attempts=())
        _event(session, order.id, run1.id, OrderTransitionEventType.INTENT_REGISTERED)
        _event(session, order.id, run2.id, OrderTransitionEventType.RETRY_REQUESTED)
        order_id, run2_id, flagged_id = order.id, run2.id, flagged.id
    _fresh_caches()
    command.upgrade(build_alembic_config(), "head")
    _fresh_caches()
    assert _scalar("SELECT version_num FROM alembic_version") == _head()
    assert _scalar("SELECT strategy_run_id FROM paper_orders WHERE id = :o", o=order_id) == run2_id
    with session_scope(load_settings()) as session:
        seed_account_run(session, completed_at=at(30))
    assert _gate() is None
    with session_scope(load_settings()) as session:
        view = get_job_recovery(session, flagged_id)
    assert "execution_path_unproven" not in repr(view)
    assert str(order_id) in repr(view)


# ---------------------------------------------------------------------------
# Triggers, downgrade, literals
# ---------------------------------------------------------------------------


def _trigger_count() -> int:
    return int(
        _scalar("SELECT count(*) FROM pg_trigger WHERE tgname = ANY(:names)", names=TRIGGER_NAMES)
    )


def _function_count() -> int:
    return int(
        _scalar(
            "SELECT count(*) FROM pg_proc WHERE proname = ANY(:names)", names=list(FUNCTION_NAMES)
        )
    )


def _0028_trigger_exists() -> bool:
    return bool(
        _scalar(
            "SELECT count(*) FROM pg_trigger WHERE tgname = :n",
            n="trg_order_submission_attempts_append_only",
        )
    )


def test_downgrade_drops_and_reupgrade_restores_every_0029_guard(migrated_db: str) -> None:
    assert len(TRIGGER_NAMES) == 23 and len(set(TRIGGER_NAMES)) == 23
    assert _trigger_count() == 23
    assert _function_count() == len(FUNCTION_NAMES)
    assert _0028_trigger_exists()
    _fresh_caches()
    command.downgrade(build_alembic_config(), PREVIOUS_REVISION)
    _fresh_caches()
    assert _trigger_count() == 0
    assert _function_count() == 0
    assert _0028_trigger_exists()
    assert _scalar("SELECT version_num FROM alembic_version") == PREVIOUS_REVISION
    _fresh_caches()
    command.upgrade(build_alembic_config(), "head")
    _fresh_caches()
    assert _trigger_count() == 23
    assert _function_count() == len(FUNCTION_NAMES)
    assert _scalar("SELECT version_num FROM alembic_version") == _head()


def test_job_type_literals_match_broker_jobs() -> None:
    from trading_platform.services.broker_jobs import (
        BROKER_ORDER_SYNC_JOB_TYPE,
        PAPER_SESSION_JOB_TYPE,
    )

    source = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0029_phase20_1_order_origin_immutable.py"
    ).read_text()
    assert PAPER_SESSION_JOB_TYPE == "paper-session"
    assert BROKER_ORDER_SYNC_JOB_TYPE == "broker-order-sync"
    assert f"'{PAPER_SESSION_JOB_TYPE}', '{BROKER_ORDER_SYNC_JOB_TYPE}'" in source
