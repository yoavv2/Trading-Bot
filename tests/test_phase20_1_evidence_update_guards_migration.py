"""Phase 20.1 migration 0030 tests: UPDATE guards for recovery and reconciliation evidence.

Review WR-01 / gap G-2 (user decision 3, 2026-10-06, completed by the user's correction of
2026-10-06): an ordinary UPDATE can no longer detach or re-scope recovery attribution, disqualify
or forge a standalone reconciliation through its Job's type, or rewrite completed reconciliation
evidence, and every legitimate writer keeps working.

Every refusal is asserted by SQLSTATE 23000 AND by the 0030 message prefix (0029's delete guard
raises the same SQLSTATE, so the code alone would not prove which guard fired), followed by a
read-back proving the row is unchanged. The legitimate writers run through the real services
(``reconcile_paper_execution``, ``reconcile_account``, ``reclaim_stale_runs``, the Job lifecycle).

Fixtures are direct INSERTs (migration 0029 makes the evidence rows undeletable). The only delete in
this module is a Job delete: it is the foreign key ``ON DELETE SET NULL`` path under test. The pins
are head-AGNOSTIC: Phase 21 chains its own migration on this revision, so nothing compares against
a literal head.
"""

from __future__ import annotations

import json
import sys
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alembic import command
from alembic.script import ScriptDirectory
from scripts.migrate import build_alembic_config
from tests.support.migrated_db import migrated_database
from tests.support.real_reconciliation import (
    run_real_account_reconciliation,
    run_real_strategy_reconciliation,
    scripted_read_broker,
)
from tests.support.recovery_fixtures import (
    OTHER,
    OWNER,
    SESSION_DATE,
    at,
    seed_account_run,
    seed_intent,
    seed_job,
    seed_operation_bound_intent,
    seed_paper_run,
    seed_strategy_reconciliation,
    strategy_row,
)
from tests.test_attribution_reconciliation import FakeBroker

from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.db.models import (
    AccountReconciliationRun,
    AttemptOutcomeClass,
    Job,
    JobEventType,
    JobFailureReason,
    JobStatus,
    OrderEvent,
    OrderLifecycleState,
    OrderSubmissionAttempt,
    OrderTransitionOutcome,
    Strategy,
    StrategyRun,
    StrategyRunStatus,
    StrategyRunType,
)
from trading_platform.db.models.order_event import OrderTransitionEventType
from trading_platform.db.session import clear_engine_cache, session_scope
from trading_platform.jobs.lifecycle import JobTransitionRequest, apply_job_transition
from trading_platform.jobs.queue import renew_lease
from trading_platform.services.reconciliation import (
    latest_standalone_reconciliation,
    reconcile_account,
    reconcile_paper_execution,
)
from trading_platform.services.reconciliation import report as report_module
from trading_platform.services.reconciliation.latest import STANDALONE_TRIGGER_SOURCE
from trading_platform.services.recovery import (
    GateCode,
    UnresolvedReason,
    strategy_recovery_status,
)
from trading_platform.services.stale_runs import reclaim_stale_runs

REVISION = "0030_phase20_1_evidence_update_guards"
PREVIOUS_REVISION = "0029_phase20_1_order_origin_immutable"

LINK_TRIGGER = "trg_strategy_runs_link_immutable"
RECONCILIATION_RUN_TRIGGER = "trg_strategy_runs_reconciliation_complete_once"
ACCOUNT_RUN_TRIGGER = "trg_account_reconciliation_runs_complete_once"
JOB_TYPE_TRIGGER = "trg_jobs_reconciliation_job_type_immutable"
NEW_TRIGGERS = {
    LINK_TRIGGER: "strategy_runs",
    RECONCILIATION_RUN_TRIGGER: "strategy_runs",
    ACCOUNT_RUN_TRIGGER: "account_reconciliation_runs",
    JOB_TYPE_TRIGGER: "jobs",
}
NEW_FUNCTIONS = (
    "phase20_1_strategy_run_link_immutable",
    "phase20_1_reconciliation_run_complete_once",
    "phase20_1_account_reconciliation_run_complete_once",
    "phase20_1_reconciliation_job_type_immutable",
)

# The 0029 objects (23 triggers, 4 functions) must be untouched by 0030 and by its downgrade.
_DELETE_GUARDED = (
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
_BULK_ERASE_GUARDED = (
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
TRIGGERS_0029 = (
    ["trg_paper_orders_origin_run_immutable", "trg_order_events_append_only"]
    + [f"trg_{table}_no_delete" for table in _DELETE_GUARDED]
    + [f"trg_{table}_no_truncate" for table in _BULK_ERASE_GUARDED]
)
FUNCTIONS_0029 = (
    "paper_orders_origin_run_immutable",
    "order_events_append_only",
    "phase20_1_evidence_no_delete",
    "phase20_1_evidence_no_truncate",
)

# Message prefixes raised by the four guards. The strategy-run guard's prefix is a suffix of the
# account-run guard's message, so compare with startswith, never ``in``.
LINK_REFUSAL = "strategy_runs evidence link is immutable"
RECONCILIATION_RUN_REFUSAL = "reconciliation run is complete-once"
ACCOUNT_RUN_REFUSAL = "account reconciliation run is complete-once"
JOB_TYPE_REFUSAL = "job_type of a Job behind a standalone reconciliation run is immutable"

PENDING = OrderLifecycleState.PENDING_SUBMISSION
LEASE_OWNER = "update-guards-test-worker"


@pytest.fixture()
def migrated_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    with migrated_database(monkeypatch, "phase20_1_update_guards") as name:
        yield name


def _fresh_caches() -> None:
    clear_settings_cache()
    clear_engine_cache()


def _script() -> ScriptDirectory:
    return ScriptDirectory.from_config(build_alembic_config())


def _head() -> str:
    head = _script().get_current_head()
    assert head is not None
    return head


# ---------------------------------------------------------------------------
# SQL helpers
# ---------------------------------------------------------------------------


def _scalar(sql: str, **params: object) -> Any:
    with session_scope(load_settings()) as session:
        return session.execute(text(sql), params).scalar_one()


def _exec(sql: str, **params: object) -> None:
    """Run a statement that must be ACCEPTED."""

    with session_scope(load_settings()) as session:
        session.execute(text(sql), params)


def _refuse(sql: str, *, by: str, **params: object) -> str:
    """Run ``sql`` and require 0030 to refuse it: SQLSTATE 23000 and a message starting ``by``."""

    with pytest.raises(IntegrityError) as info, session_scope(load_settings()) as session:
        session.execute(text(sql), params)
    orig = info.value.orig
    assert getattr(orig, "sqlstate", None) == "23000", info.value
    message = orig.diag.message_primary  # type: ignore[union-attr]
    assert message is not None and message.startswith(by), message
    return message


def _trigger_count(names: Any) -> int:
    return int(
        _scalar(
            "SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal AND tgname = ANY(:names)",
            names=list(names),
        )
    )


def _function_count(names: Any) -> int:
    return int(
        _scalar("SELECT count(*) FROM pg_proc WHERE proname = ANY(:names)", names=list(names))
    )


def _json(value: Any) -> str:
    """A SQL ``json`` literal. ``json.dumps`` always puts a space after a colon, which keeps
    ``text()`` from reading ``":x"`` inside the document as a bind parameter."""

    return f"'{json.dumps(value)}'::json"


def _gate() -> GateCode | None:
    with session_scope(load_settings()) as session:
        return strategy_recovery_status(session, OWNER).gate_code


# ---------------------------------------------------------------------------
# Seeding (INSERTs only)
# ---------------------------------------------------------------------------


def _probe_job(session: Session, *, completed_at: datetime | None = None) -> Job:
    """A terminal, unflagged Job of a type 0029 J does not protect. Deleting it is legal under
    0029, so a Job delete that aborts can only have been aborted by 0030."""

    return seed_job(
        session,
        job_type="phase-probe",
        uncertain=False,
        status=JobStatus.SUCCEEDED,
        completed_at=completed_at if completed_at is not None else at(0),
    )


def _running_job(session: Session, job_type: str = "reconciliation") -> Job:
    """A RUNNING Job holding a lease, as the worker would have claimed it."""

    now = datetime.now(UTC)
    job = Job(
        job_type=job_type,
        payload={"strategy_id": OWNER},
        status=JobStatus.RUNNING,
        started_at=now,
        lease_owner=LEASE_OWNER,
        lease_expires_at=now + timedelta(days=1),
        heartbeat_at=now,
    )
    session.add(job)
    session.flush()
    return job


def _plain_run(
    session: Session,
    strategy: Strategy,
    job: Job | None,
    run_type: StrategyRunType,
    *,
    status: StrategyRunStatus = StrategyRunStatus.SUCCEEDED,
    trigger_source: str = "test_suite",
    completed_at: datetime | None = None,
) -> StrategyRun:
    run = StrategyRun(
        strategy_id=strategy.id,
        job_id=job.id if job is not None else None,
        run_type=run_type,
        status=status,
        trigger_source=trigger_source,
        completed_at=completed_at,
        parameters_snapshot={},
        result_summary={},
    )
    session.add(run)
    session.flush()
    return run


def _event(session: Session, order_id: uuid.UUID, run_id: uuid.UUID) -> None:
    """An accepted ``retry_requested`` registration row recorded by ``run_id``."""

    session.add(
        OrderEvent(
            paper_order_id=order_id,
            strategy_run_id=run_id,
            from_state=PENDING,
            to_state=PENDING,
            event_type=OrderTransitionEventType.RETRY_REQUESTED,
            outcome=OrderTransitionOutcome.ACCEPTED,
            event_at=at(0),
            details={},
        )
    )
    session.flush()


def _saf01_flagged_job() -> None:
    """A flagged Job (effect at minute 0) whose only order is an operation-bound unsent one: the
    shape whose recovery gate depends solely on the reconciliation evidence."""

    with session_scope(load_settings()) as session:
        job = seed_job(session, completed_at=at(0))
        run = seed_paper_run(session, job)
        seed_operation_bound_intent(session, run, status=PENDING, attempts=())


@dataclass(frozen=True)
class Arranged:
    run_id: uuid.UUID
    job_id: uuid.UUID
    other_job_id: uuid.UUID
    strategy_pk: uuid.UUID
    other_strategy_pk: uuid.UUID
    run_type: str


def _arrange(shape: str) -> Arranged:
    """One evidence-bearing run with a NON-NULL job_id (a NULL job_id would make ``job_id -> NULL``
    a no-op that the guard rightly ignores).

    ``order_event_only`` / ``attempt_only``: the target is a dry_bootstrap run, evidence ONLY through
    the row that references it; the order itself belongs to a different (origin) run.
    """

    with session_scope(load_settings()) as session:
        strategy = strategy_row(session, OWNER)
        other = strategy_row(session, OTHER)
        other_job = _probe_job(session)
        if shape == "paper_execution":
            job: Job | None = _probe_job(session)
            run = seed_paper_run(session, job)
        elif shape == "reconciliation":
            run = seed_strategy_reconciliation(session, completed_at=at(5))
            job = session.get(Job, run.job_id)
        else:
            origin = seed_paper_run(session, _probe_job(session))
            order = seed_intent(session, origin, status=PENDING, attempts=())
            job = _probe_job(session)
            run = _plain_run(session, strategy, job, StrategyRunType.DRY_BOOTSTRAP)
            if shape == "order_event_only":
                _event(session, order.id, run.id)
            else:
                assert shape == "attempt_only"
                session.add(
                    OrderSubmissionAttempt(
                        paper_order_id=order.id,
                        strategy_run_id=run.id,
                        attempt_number=1,
                        started_at=at(1),
                        completed_at=at(1),
                        outcome_class=AttemptOutcomeClass.AMBIGUOUS.value,
                    )
                )
                session.flush()
        assert job is not None
        return Arranged(
            run_id=run.id,
            job_id=job.id,
            other_job_id=other_job.id,
            strategy_pk=strategy.id,
            other_strategy_pk=other.id,
            run_type=run.run_type.value,
        )


def _link_row(run_id: uuid.UUID) -> tuple[Any, ...]:
    with session_scope(load_settings()) as session:
        row = session.execute(
            text(
                "SELECT job_id, run_type::text AS run_type, strategy_id "
                "FROM strategy_runs WHERE id = :r"
            ),
            {"r": run_id},
        ).one()
    return (row.job_id, row.run_type, row.strategy_id)


# ---------------------------------------------------------------------------
# Chain, inventory and literal pins
# ---------------------------------------------------------------------------


def test_chain_is_linear_and_head_agnostic() -> None:
    script = _script()
    heads = script.get_heads()
    assert len(heads) == 1, heads
    revision = script.get_revision(REVISION)
    assert revision is not None
    assert revision.down_revision == PREVIOUS_REVISION
    # 0030 sits on the single line from 0029 to whatever the head is (a later phase may chain on it).
    walked = [rev.revision for rev in script.walk_revisions(base=PREVIOUS_REVISION, head=heads[0])]
    assert REVISION in walked
    assert walked[-1] == PREVIOUS_REVISION
    assert len(walked) == len(set(walked))


def test_four_triggers_and_functions_exist(migrated_db: str) -> None:
    for name, table in NEW_TRIGGERS.items():
        row = _scalar(
            "SELECT c.relname || ':' || t.tgenabled FROM pg_trigger t "
            "JOIN pg_class c ON c.oid = t.tgrelid WHERE t.tgname = :n AND NOT t.tgisinternal",
            n=name,
        )
        assert row == f"{table}:O", name  # on the right table and enabled
    assert _trigger_count(NEW_TRIGGERS) == 4
    assert _function_count(NEW_FUNCTIONS) == 4
    # 0029 is untouched: 23 triggers and 4 functions.
    assert len(TRIGGERS_0029) == 23 and len(set(TRIGGERS_0029)) == 23
    assert _trigger_count(TRIGGERS_0029) == 23
    assert _function_count(FUNCTIONS_0029) == 4
    assert _scalar("SELECT version_num FROM alembic_version") == _head()


def test_trigger_definitions_pin_the_scope_of_each_guard(migrated_db: str) -> None:
    def definition(name: str) -> str:
        return str(_scalar("SELECT pg_get_triggerdef(oid) FROM pg_trigger WHERE tgname = :n", n=name))

    link = definition(LINK_TRIGGER)
    assert "BEFORE UPDATE OF job_id, run_type, strategy_id ON" in link and "FOR EACH ROW" in link
    strategy_complete = definition(RECONCILIATION_RUN_TRIGGER)
    assert "BEFORE UPDATE ON" in strategy_complete and "WHEN" in strategy_complete
    assert "reconciliation" in strategy_complete
    account_complete = definition(ACCOUNT_RUN_TRIGGER)
    assert "BEFORE UPDATE ON" in account_complete and "FOR EACH ROW" in account_complete
    # The Job guard watches job_type ONLY: column-scoped and conditioned on an actual change.
    job_type = definition(JOB_TYPE_TRIGGER)
    assert "BEFORE UPDATE OF job_type ON" in job_type
    assert "WHEN" in job_type and "IS DISTINCT FROM" in job_type


def test_standalone_trigger_literal_matches_the_application_constant() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0030_phase20_1_evidence_update_guards.py"
    ).read_text()
    assert STANDALONE_TRIGGER_SOURCE == "job"
    assert f"sr.trigger_source = '{STANDALONE_TRIGGER_SOURCE}'" in source


# ---------------------------------------------------------------------------
# Guard 1: strategy_runs link immutability (job_id, run_type, strategy_id)
# ---------------------------------------------------------------------------

LINK_SHAPES = ("paper_execution", "reconciliation", "order_event_only", "attempt_only")
LINK_UPDATES = {
    "job_to_null": "UPDATE strategy_runs SET job_id = NULL WHERE id = :run",
    "job_to_other": "UPDATE strategy_runs SET job_id = :other_job WHERE id = :run",
    "type_to_backtest": "UPDATE strategy_runs SET run_type = 'backtest' WHERE id = :run",
    "strategy_to_other": "UPDATE strategy_runs SET strategy_id = :other_strategy WHERE id = :run",
}


@pytest.mark.parametrize("update", list(LINK_UPDATES))
@pytest.mark.parametrize("shape", LINK_SHAPES)
def test_evidence_run_link_update_is_refused(migrated_db: str, shape: str, update: str) -> None:
    arranged = _arrange(shape)
    before = _link_row(arranged.run_id)
    assert before == (arranged.job_id, arranged.run_type, arranged.strategy_pk)
    params: dict[str, object] = {"run": arranged.run_id}
    if update == "job_to_other":
        params["other_job"] = arranged.other_job_id
    if update == "strategy_to_other":
        params["other_strategy"] = arranged.other_strategy_pk
    _refuse(LINK_UPDATES[update], by=LINK_REFUSAL, **params)
    assert _link_row(arranged.run_id) == before


@pytest.mark.parametrize(
    ("start", "target"),
    [("backtest", "paper_execution"), ("risk_evaluation", "reconciliation")],
    ids=["backtest_to_paper_execution", "risk_evaluation_to_reconciliation"],
)
def test_conversion_into_an_evidence_type_is_refused(
    migrated_db: str, start: str, target: str
) -> None:
    with session_scope(load_settings()) as session:
        run = _plain_run(
            session, strategy_row(session, OWNER), _probe_job(session), StrategyRunType(start)
        )
        run_id = run.id
    _refuse(
        f"UPDATE strategy_runs SET run_type = '{target}' WHERE id = :run",
        by=LINK_REFUSAL,
        run=run_id,
    )
    assert _scalar("SELECT run_type::text FROM strategy_runs WHERE id = :run", run=run_id) == start


def test_non_evidence_run_link_changes_still_work(migrated_db: str) -> None:
    with session_scope(load_settings()) as session:
        strategy = strategy_row(session, OWNER)
        other = strategy_row(session, OTHER)
        job_a = _probe_job(session)
        job_b = _probe_job(session)
        risk = _plain_run(session, strategy, job_a, StrategyRunType.RISK_EVALUATION)
        backtest = _plain_run(session, strategy, job_a, StrategyRunType.BACKTEST)
        job_b_id, other_pk, risk_id, backtest_id = job_b.id, other.id, risk.id, backtest.id
    _exec("UPDATE strategy_runs SET job_id = :j WHERE id = :r", j=job_b_id, r=risk_id)
    assert _scalar("SELECT job_id FROM strategy_runs WHERE id = :r", r=risk_id) == job_b_id
    _exec("UPDATE strategy_runs SET job_id = NULL WHERE id = :r", r=risk_id)
    assert _scalar("SELECT job_id FROM strategy_runs WHERE id = :r", r=risk_id) is None
    _exec("UPDATE strategy_runs SET strategy_id = :s WHERE id = :r", s=other_pk, r=risk_id)
    assert _scalar("SELECT strategy_id FROM strategy_runs WHERE id = :r", r=risk_id) == other_pk
    _exec("UPDATE strategy_runs SET run_type = 'dry_bootstrap' WHERE id = :r", r=backtest_id)
    assert (
        _scalar("SELECT run_type::text FROM strategy_runs WHERE id = :r", r=backtest_id)
        == "dry_bootstrap"
    )
    # A same-value write on an evidence run is not a change and stays legal.
    arranged = _arrange("paper_execution")
    _exec(
        "UPDATE strategy_runs SET job_id = job_id, run_type = run_type, strategy_id = strategy_id "
        "WHERE id = :r",
        r=arranged.run_id,
    )
    assert _link_row(arranged.run_id)[0] == arranged.job_id


def test_order_origin_run_link_update_is_refused(migrated_db: str) -> None:
    """A run that originates a paper order is evidence whatever its type (0029 predicate R)."""

    with session_scope(load_settings()) as session:
        strategy = strategy_row(session, OWNER)
        other_pk = strategy_row(session, OTHER).id
        job = _probe_job(session)
        origin = _plain_run(session, strategy, job, StrategyRunType.DRY_BOOTSTRAP)
        seed_intent(session, origin, status=PENDING, attempts=())
        run_id, job_id, owner_pk = origin.id, job.id, strategy.id
    before = (job_id, "dry_bootstrap", owner_pk)
    assert _link_row(run_id) == before
    _refuse("UPDATE strategy_runs SET job_id = NULL WHERE id = :r", by=LINK_REFUSAL, r=run_id)
    _refuse(
        "UPDATE strategy_runs SET run_type = 'backtest' WHERE id = :r", by=LINK_REFUSAL, r=run_id
    )
    _refuse(
        "UPDATE strategy_runs SET strategy_id = :s WHERE id = :r",
        by=LINK_REFUSAL,
        s=other_pk,
        r=run_id,
    )
    assert _link_row(run_id) == before


def test_fk_set_null_fires_the_guard(migrated_db: str) -> None:
    """A Job delete SET NULLs ``strategy_runs.job_id``. That is an UPDATE: legal for a non-evidence
    run, refused (and the whole delete aborted) for an evidence run, whatever the Job type."""

    with session_scope(load_settings()) as session:
        strategy = strategy_row(session, OWNER)
        loose_job = _probe_job(session)
        loose_run = _plain_run(session, strategy, loose_job, StrategyRunType.BACKTEST)
        evidence_job = _probe_job(session)
        evidence_run = seed_paper_run(session, evidence_job)
        loose_job_id, loose_run_id = loose_job.id, loose_run.id
        evidence_job_id, evidence_run_id = evidence_job.id, evidence_run.id
    _exec("DELETE FROM jobs WHERE id = :j", j=loose_job_id)
    assert _scalar("SELECT count(*) FROM jobs WHERE id = :j", j=loose_job_id) == 0
    assert _scalar("SELECT job_id FROM strategy_runs WHERE id = :r", r=loose_run_id) is None
    _refuse("DELETE FROM jobs WHERE id = :j", by=LINK_REFUSAL, j=evidence_job_id)
    assert _scalar("SELECT count(*) FROM jobs WHERE id = :j", j=evidence_job_id) == 1
    assert (
        _scalar("SELECT job_id FROM strategy_runs WHERE id = :r", r=evidence_run_id)
        == evidence_job_id
    )


def test_wr01_order_less_flagged_job_cannot_be_detached(migrated_db: str) -> None:
    """The review's exact WR-01 scenario: the only paper_execution run of an order-less flagged Job
    is what keeps the Job ``execution_path_unproven``. Detaching it (job_id), retyping it, or moving
    it to another strategy was one ordinary UPDATE; it is now refused. OD-1 is NOT implemented: a
    newer clean reconciliation releases nothing."""

    with session_scope(load_settings()) as session:
        job = seed_job(session, completed_at=at(0))  # flagged paper-session Job
        run = seed_paper_run(session, job)  # ... with one run and NO order
        other = strategy_row(session, OTHER)
        job_id, run_id, other_pk, owner_pk = job.id, run.id, other.id, run.strategy_id

    def state() -> tuple[GateCode | None, bool]:
        with session_scope(load_settings()) as session:
            status = strategy_recovery_status(session, OWNER, now=at(60))
        unproven = any(
            item.job_id == job_id
            and item.intent_id is None
            and item.unresolved_reason is UnresolvedReason.EXECUTION_PATH_UNPROVEN
            for item in status.intents
        )
        return status.gate_code, unproven

    expected = (GateCode.OUTCOME_UNRESOLVED, True)
    assert state() == expected

    def attack() -> None:
        _refuse("UPDATE strategy_runs SET job_id = NULL WHERE id = :r", by=LINK_REFUSAL, r=run_id)
        _refuse(
            "UPDATE strategy_runs SET run_type = 'backtest' WHERE id = :r",
            by=LINK_REFUSAL,
            r=run_id,
        )
        _refuse(
            "UPDATE strategy_runs SET strategy_id = :s WHERE id = :r",
            by=LINK_REFUSAL,
            s=other_pk,
            r=run_id,
        )
        assert _link_row(run_id) == (job_id, "paper_execution", owner_pk)

    attack()
    assert state() == expected
    with session_scope(load_settings()) as session:
        seed_account_run(session, completed_at=at(10))  # newer and clean
    assert state() == expected
    attack()
    assert state() == expected


# ---------------------------------------------------------------------------
# Guard 2: reconciliation strategy runs are complete-once
# ---------------------------------------------------------------------------

STRATEGY_RUN_REWRITES = {
    "status": "status = 'failed'",
    "completed_at_to_null": "completed_at = NULL",
    "started_at": "started_at = started_at + interval '1 minute'",
    "error_message": "error_message = 'tampered'",
    "result_summary": "result_summary = "
    + _json({"blocks_execution": False, "unresolved_reasons": [], "tampered": True}),
    "parameters_snapshot": "parameters_snapshot = " + _json({"tampered": True}),
}


def _completed_strategy_run_world() -> tuple[uuid.UUID, uuid.UUID]:
    with session_scope(load_settings()) as session:
        run = seed_strategy_reconciliation(session, completed_at=at(5))
        return run.id, run.job_id  # type: ignore[return-value]


def test_reconciliation_run_completes_once_through_the_real_service(migrated_db: str) -> None:
    with session_scope(load_settings()) as session:
        strategy_row(session, OWNER)
    report, job_id = run_real_strategy_reconciliation(
        strategy_id=OWNER, as_of_session=SESSION_DATE, broker=scripted_read_broker()
    )
    run_id = uuid.UUID(report.run_id)
    with session_scope(load_settings()) as session:
        run = session.get(StrategyRun, run_id)
        job = session.get(Job, job_id)
        assert run is not None and job is not None
        assert run.status is StrategyRunStatus.SUCCEEDED
        assert run.completed_at is not None
        assert run.trigger_source == "job" and run.job_id == job_id
        assert run.result_summary["stage"] == "completed"
        # The Job's own SUCCEEDED transition (run by the helper) passed with its run in place, so
        # the Job type guard was armed and did not fire.
        assert job.status is JobStatus.SUCCEEDED and job.job_type == "reconciliation"
    # Complete-once: the real result can no longer be rewritten, and the Job type is frozen.
    _refuse(
        f"UPDATE strategy_runs SET result_summary = {_json({'stage': 'tampered'})} WHERE id = :r",
        by=RECONCILIATION_RUN_REFUSAL,
        r=run_id,
    )
    _refuse(
        "UPDATE jobs SET job_type = 'phase-probe' WHERE id = :j", by=JOB_TYPE_REFUSAL, j=job_id
    )


def test_failed_reconciliation_run_completes_once_as_failed(
    migrated_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    with session_scope(load_settings()) as session:
        strategy_row(session, OWNER)
        job_id = _running_job(session).id

    def explode(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("scripted reconciliation failure")

    monkeypatch.setattr(report_module, "_reconcile_against_broker_state", explode)
    with pytest.raises(RuntimeError, match="scripted reconciliation failure"):
        reconcile_paper_execution(
            OWNER,
            as_of_session=SESSION_DATE,
            trigger_source="job",
            job_id=job_id,
            broker_client=scripted_read_broker(),
            settings=load_settings(),
        )
    with session_scope(load_settings()) as session:
        run_id = session.execute(
            text("SELECT id FROM strategy_runs WHERE job_id = :j"), {"j": job_id}
        ).scalar_one()
        stored = session.get(StrategyRun, run_id)
        assert stored is not None
        assert stored.status is StrategyRunStatus.FAILED
        assert stored.completed_at is not None
        assert stored.error_message == "scripted reconciliation failure"
        assert stored.result_summary["stage"] == "failed"
    # The Job fails through the lifecycle exactly as the runner does; its run exists, guard 4 is armed.
    with session_scope(load_settings()) as session:
        apply_job_transition(
            session,
            job_id=job_id,
            request=JobTransitionRequest(
                event_type=JobEventType.FAILED,
                failure_reason=JobFailureReason.HANDLER_ERROR,
                failure_message="scripted reconciliation failure",
                outcome_uncertain=False,
            ),
        )
    assert _scalar("SELECT status::text FROM jobs WHERE id = :j", j=job_id) == "failed"
    _refuse(
        "UPDATE strategy_runs SET status = 'succeeded' WHERE id = :r",
        by=RECONCILIATION_RUN_REFUSAL,
        r=run_id,
    )


@pytest.mark.parametrize("rewrite", list(STRATEGY_RUN_REWRITES))
def test_completed_reconciliation_run_rewrite_is_refused(migrated_db: str, rewrite: str) -> None:
    run_id, _job_id = _completed_strategy_run_world()
    with session_scope(load_settings()) as session:
        before = session.execute(
            text(
                "SELECT status::text, completed_at, started_at, error_message, "
                "result_summary::text, parameters_snapshot::text, trigger_source "
                "FROM strategy_runs WHERE id = :r"
            ),
            {"r": run_id},
        ).one()
    _refuse(
        f"UPDATE strategy_runs SET {STRATEGY_RUN_REWRITES[rewrite]} WHERE id = :r",
        by=RECONCILIATION_RUN_REFUSAL,
        r=run_id,
    )
    with session_scope(load_settings()) as session:
        after = session.execute(
            text(
                "SELECT status::text, completed_at, started_at, error_message, "
                "result_summary::text, parameters_snapshot::text, trigger_source "
                "FROM strategy_runs WHERE id = :r"
            ),
            {"r": run_id},
        ).one()
    assert tuple(after) == tuple(before)


@pytest.mark.parametrize("completed", [False, True], ids=["pending", "completed"])
def test_reconciliation_trigger_source_is_immutable_even_while_pending(
    migrated_db: str, completed: bool
) -> None:
    with session_scope(load_settings()) as session:
        if completed:
            run_id = seed_strategy_reconciliation(session, completed_at=at(5)).id
        else:
            run_id = _plain_run(
                session,
                strategy_row(session, OWNER),
                _probe_job(session),
                StrategyRunType.RECONCILIATION,
                status=StrategyRunStatus.PENDING,
                trigger_source="job",
            ).id
    _refuse(
        "UPDATE strategy_runs SET trigger_source = 'paper_reconciliation' WHERE id = :r",
        by=RECONCILIATION_RUN_REFUSAL,
        r=run_id,
    )
    assert _scalar("SELECT trigger_source FROM strategy_runs WHERE id = :r", r=run_id) == "job"


def test_pending_reconciliation_run_accepts_its_single_completion(migrated_db: str) -> None:
    with session_scope(load_settings()) as session:
        run_id = _plain_run(
            session,
            strategy_row(session, OWNER),
            _probe_job(session),
            StrategyRunType.RECONCILIATION,
            status=StrategyRunStatus.PENDING,
            trigger_source="job",
        ).id
    _exec(
        "UPDATE strategy_runs SET status = 'succeeded', completed_at = now(), "
        f"result_summary = {_json({'stage': 'completed'})}, updated_at = now() WHERE id = :r",
        r=run_id,
    )
    assert _scalar("SELECT status::text FROM strategy_runs WHERE id = :r", r=run_id) == "succeeded"
    _refuse(
        "UPDATE strategy_runs SET status = 'failed' WHERE id = :r",
        by=RECONCILIATION_RUN_REFUSAL,
        r=run_id,
    )
    _refuse(
        f"UPDATE strategy_runs SET result_summary = {_json({'stage': 'again'})} WHERE id = :r",
        by=RECONCILIATION_RUN_REFUSAL,
        r=run_id,
    )
    assert _scalar("SELECT status::text FROM strategy_runs WHERE id = :r", r=run_id) == "succeeded"


def test_same_value_writes_on_completed_runs_pass(migrated_db: str) -> None:
    """Complete-once compares VALUES (json through jsonb), not statements: a write that changes
    nothing, or re-spells the same document, is not a rewrite."""

    with session_scope(load_settings()) as session:
        strategy_run_id = seed_strategy_reconciliation(session, completed_at=at(5)).id
        account_run_id = seed_account_run(session, completed_at=at(6)).id
    _exec(
        "UPDATE strategy_runs SET status = status, completed_at = completed_at, "
        "result_summary = result_summary, updated_at = now() WHERE id = :r",
        r=strategy_run_id,
    )
    _exec(
        # Same document, different spelling (key order, whitespace): equal as jsonb.
        "UPDATE strategy_runs SET result_summary = "
        "'{\"unresolved_reasons\":   [],   \"blocks_execution\": false}'::json WHERE id = :r",
        r=strategy_run_id,
    )
    _exec(
        "UPDATE account_reconciliation_runs SET findings = '[ ]'::json, "
        "unresolved_reasons = '[ ]'::json, updated_at = now() WHERE id = :a",
        a=account_run_id,
    )


# ---------------------------------------------------------------------------
# Guard 3: account_reconciliation_runs are complete-once
# ---------------------------------------------------------------------------

ACCOUNT_RUN_REWRITES = {
    "status": "status = 'failed'",
    "completed_at": "completed_at = NULL",
    "started_at": "started_at = started_at + interval '1 minute'",
    "as_of_session": "as_of_session = DATE '2024-01-05'",
    "blocks_execution": "blocks_execution = true",
    "finding_count": "finding_count = 3",
    "blocking_count": "blocking_count = 2",
    "error_message": "error_message = 'tampered'",
    "findings": "findings = " + _json([{"tampered": True}]),
    "account_divergence": "account_divergence = " + _json({"tampered": True}),
    "unexplained_exposure": "unexplained_exposure = " + _json({"AAPL": "1"}),
    "classification_summary": "classification_summary = "
    + _json({"orders": {"unrecognized": 1}}),
    "unresolved_reasons": "unresolved_reasons = " + _json(["tampered"]),
    "result_summary": "result_summary = " + _json({"tampered": True}),
}

_ACCOUNT_ROW_SQL = (
    "SELECT status, completed_at, started_at, as_of_session, blocks_execution, finding_count, "
    "blocking_count, error_message, findings::text, account_divergence::text, "
    "unexplained_exposure::text, classification_summary::text, unresolved_reasons::text, "
    "result_summary::text, trigger_source, scope FROM account_reconciliation_runs WHERE id = :a"
)


def test_account_run_completes_once_through_the_real_service(
    migrated_db: str,
) -> None:
    report, job_id = run_real_account_reconciliation(broker=scripted_read_broker())
    run_id = uuid.UUID(report.run_id)
    with session_scope(load_settings()) as session:
        row = session.execute(
            text(
                "SELECT status, completed_at, trigger_source, job_id "
                "FROM account_reconciliation_runs WHERE id = :a"
            ),
            {"a": run_id},
        ).one()
    assert row.status == "succeeded" and row.completed_at is not None
    assert row.trigger_source == "job" and row.job_id == job_id
    _refuse(
        "UPDATE account_reconciliation_runs SET blocks_execution = true WHERE id = :a",
        by=ACCOUNT_RUN_REFUSAL,
        a=run_id,
    )


def test_failed_account_run_completes_once_as_failed_and_reraises(migrated_db: str) -> None:
    with pytest.raises(RuntimeError, match="broker down"):
        reconcile_account(
            settings=load_settings(),
            broker_client=FakeBroker(orders_error=RuntimeError("broker down")),
        )
    with session_scope(load_settings()) as session:
        row = session.execute(
            text(
                "SELECT id, status, blocks_execution, error_message, completed_at "
                "FROM account_reconciliation_runs"
            )
        ).one()
    assert row.status == "failed" and row.blocks_execution is True
    assert row.error_message == "broker down" and row.completed_at is not None
    _refuse(
        "UPDATE account_reconciliation_runs SET status = 'succeeded' WHERE id = :a",
        by=ACCOUNT_RUN_REFUSAL,
        a=row.id,
    )


@pytest.mark.parametrize("rewrite", list(ACCOUNT_RUN_REWRITES))
def test_completed_account_run_rewrite_is_refused(migrated_db: str, rewrite: str) -> None:
    assert len(ACCOUNT_RUN_REWRITES) == 14  # every frozen result column
    with session_scope(load_settings()) as session:
        run_id = seed_account_run(session, completed_at=at(5)).id
    with session_scope(load_settings()) as session:
        before = tuple(session.execute(text(_ACCOUNT_ROW_SQL), {"a": run_id}).one())
    _refuse(
        f"UPDATE account_reconciliation_runs SET {ACCOUNT_RUN_REWRITES[rewrite]} WHERE id = :a",
        by=ACCOUNT_RUN_REFUSAL,
        a=run_id,
    )
    with session_scope(load_settings()) as session:
        assert tuple(session.execute(text(_ACCOUNT_ROW_SQL), {"a": run_id}).one()) == before


@pytest.mark.parametrize("completed", [False, True], ids=["pending", "completed"])
def test_account_run_trigger_source_and_scope_are_immutable(
    migrated_db: str, completed: bool
) -> None:
    with session_scope(load_settings()) as session:
        run_id = seed_account_run(session, completed_at=at(5) if completed else None).id
    _refuse(
        "UPDATE account_reconciliation_runs SET trigger_source = 'tampered' WHERE id = :a",
        by=ACCOUNT_RUN_REFUSAL,
        a=run_id,
    )
    # The scope CHECK would refuse another value anyway (SQLSTATE 23514, after BEFORE triggers);
    # the trigger comes first, which is what the message prefix proves.
    _refuse(
        "UPDATE account_reconciliation_runs SET scope = 'strategy' WHERE id = :a",
        by=ACCOUNT_RUN_REFUSAL,
        a=run_id,
    )
    _exec("UPDATE account_reconciliation_runs SET scope = 'account' WHERE id = :a", a=run_id)
    assert (
        _scalar("SELECT trigger_source FROM account_reconciliation_runs WHERE id = :a", a=run_id)
        == "job"
    )


@pytest.mark.parametrize(
    ("table", "status", "completed", "rewrite", "by"),
    [
        ("strategy", "failed", False, "error_message = 'tampered'", RECONCILIATION_RUN_REFUSAL),
        ("strategy", "stale", False, "error_message = 'tampered'", RECONCILIATION_RUN_REFUSAL),
        ("strategy", "pending", True, "error_message = 'tampered'", RECONCILIATION_RUN_REFUSAL),
        ("account", "failed", False, "blocks_execution = true", ACCOUNT_RUN_REFUSAL),
        ("account", "pending", True, "blocks_execution = true", ACCOUNT_RUN_REFUSAL),
    ],
    ids=[
        "strategy_failed_without_completed_at",
        "strategy_stale_without_completed_at",
        "strategy_pending_with_completed_at",
        "account_failed_without_completed_at",
        "account_pending_with_completed_at",
    ],
)
def test_completion_is_detected_by_status_or_by_completed_at(
    migrated_db: str, table: str, status: str, completed: bool, rewrite: str, by: str
) -> None:
    """A run is complete when completed_at is set OR its status is terminal: either arm alone
    freezes it, so clearing one marker cannot reopen the row."""

    stamp = at(5) if completed else None
    with session_scope(load_settings()) as session:
        if table == "strategy":
            row_id = _plain_run(
                session,
                strategy_row(session, OWNER),
                _probe_job(session),
                StrategyRunType.RECONCILIATION,
                status=StrategyRunStatus(status),
                trigger_source="job",
                completed_at=stamp,
            ).id
        else:
            account = AccountReconciliationRun(
                trigger_source="job", status=status, completed_at=stamp
            )
            session.add(account)
            session.flush()
            row_id = account.id
    relation = "strategy_runs" if table == "strategy" else "account_reconciliation_runs"
    _refuse(f"UPDATE {relation} SET {rewrite} WHERE id = :i", by=by, i=row_id)


def test_pending_account_run_accepts_its_single_completion(migrated_db: str) -> None:
    with session_scope(load_settings()) as session:
        run_id = seed_account_run(session, completed_at=None).id
    _exec(
        "UPDATE account_reconciliation_runs SET status = 'succeeded', completed_at = now(), "
        "blocks_execution = true, finding_count = 1, blocking_count = 1, "
        f"findings = {_json([{'x': 1}])}, classification_summary = {_json({'x': 1})}, "
        f"unresolved_reasons = {_json(['x'])}, result_summary = {_json({'stage': 'completed'})}, "
        "updated_at = now() WHERE id = :a",
        a=run_id,
    )
    _refuse(
        "UPDATE account_reconciliation_runs SET blocks_execution = false WHERE id = :a",
        by=ACCOUNT_RUN_REFUSAL,
        a=run_id,
    )


def test_account_run_job_id_stays_writable(migrated_db: str) -> None:
    """``job_id`` is deliberately NOT frozen: no gate reads it, and its foreign key SET NULL must
    keep working after completion."""

    with session_scope(load_settings()) as session:
        run_id = seed_account_run(session, completed_at=at(6)).id
        account_job = Job(
            job_type="reconciliation",
            payload={"scope": "account"},
            status=JobStatus.SUCCEEDED,
            completed_at=at(5),
        )
        session.add(account_job)
        session.flush()
        job_id = account_job.id
    _exec("UPDATE account_reconciliation_runs SET job_id = :j WHERE id = :a", j=job_id, a=run_id)
    assert (
        _scalar("SELECT job_id FROM account_reconciliation_runs WHERE id = :a", a=run_id) == job_id
    )
    _exec("DELETE FROM jobs WHERE id = :j", j=job_id)
    assert _scalar("SELECT count(*) FROM jobs WHERE id = :j", j=job_id) == 0
    assert _scalar("SELECT job_id FROM account_reconciliation_runs WHERE id = :a", a=run_id) is None
    # Nothing else moved while the link was written and nulled.
    assert _scalar("SELECT status FROM account_reconciliation_runs WHERE id = :a", a=run_id) == (
        "succeeded"
    )


# ---------------------------------------------------------------------------
# Gate pins (W4 class, UPDATE half)
# ---------------------------------------------------------------------------

DIRTY_ACCOUNT_REWRITES = {
    "blocks_and_reasons": "blocks_execution = false, unresolved_reasons = " + _json([]),
    "blocks_execution": "blocks_execution = false",
    "unresolved_reasons": "unresolved_reasons = " + _json([]),
    "result_summary": "result_summary = " + _json({"blocks_execution": False}),
    "status": "status = 'failed'",
    "completed_at_backdated": "completed_at = completed_at - interval '1 day'",
    "trigger_source": "trigger_source = 'tampered'",
}
DIRTY_STRATEGY_REWRITES = {
    "result_summary_clean": "result_summary = "
    + _json({"blocks_execution": False, "unresolved_reasons": []}),
    "status": "status = 'failed'",
    "completed_at_backdated": "completed_at = completed_at - interval '1 day'",
    "trigger_source": "trigger_source = 'paper_reconciliation'",
}


def test_newer_dirty_account_run_cannot_be_rewritten_clean(migrated_db: str) -> None:
    _saf01_flagged_job()
    with session_scope(load_settings()) as session:
        seed_account_run(session, completed_at=at(10))
        dirty = seed_account_run(
            session,
            completed_at=at(20),
            blocks=True,
            unresolved_reasons=["unexplained_position"],
        )
        dirty_id = dirty.id
    assert _gate() is GateCode.RECONCILIATION_NOT_CLEAN
    for rewrite in DIRTY_ACCOUNT_REWRITES.values():
        _refuse(
            f"UPDATE account_reconciliation_runs SET {rewrite} WHERE id = :a",
            by=ACCOUNT_RUN_REFUSAL,
            a=dirty_id,
        )
        assert _gate() is GateCode.RECONCILIATION_NOT_CLEAN
    assert _scalar(
        "SELECT blocks_execution FROM account_reconciliation_runs WHERE id = :a", a=dirty_id
    )


def test_newer_dirty_strategy_run_cannot_be_rewritten_clean(migrated_db: str) -> None:
    _saf01_flagged_job()
    with session_scope(load_settings()) as session:
        seed_account_run(session, completed_at=at(5))
        seed_strategy_reconciliation(session, completed_at=at(10))
        dirty = seed_strategy_reconciliation(session, completed_at=at(20), blocks=True)
        dirty_id = dirty.id
    assert _gate() is GateCode.RECONCILIATION_NOT_CLEAN
    for rewrite in DIRTY_STRATEGY_REWRITES.values():
        _refuse(
            f"UPDATE strategy_runs SET {rewrite} WHERE id = :r",
            by=RECONCILIATION_RUN_REFUSAL,
            r=dirty_id,
        )
        assert _gate() is GateCode.RECONCILIATION_NOT_CLEAN
    assert (
        _scalar(
            "SELECT result_summary::jsonb ->> 'blocks_execution' FROM strategy_runs WHERE id = :r",
            r=dirty_id,
        )
        == "true"
    )


def test_newer_dirty_strategy_run_cannot_be_moved_to_another_strategy(migrated_db: str) -> None:
    """The gate selects the strategy through ``strategy_runs.strategy_id``: moving a newer dirty run
    to another strategy drops it from this strategy's query (user correction, 2026-10-06)."""

    _saf01_flagged_job()
    with session_scope(load_settings()) as session:
        seed_account_run(session, completed_at=at(5))
        seed_strategy_reconciliation(session, completed_at=at(10))
        dirty = seed_strategy_reconciliation(session, completed_at=at(20), blocks=True)
        other_pk = strategy_row(session, OTHER).id
        dirty_id, owner_pk = dirty.id, dirty.strategy_id
    assert _gate() is GateCode.RECONCILIATION_NOT_CLEAN
    _refuse(
        "UPDATE strategy_runs SET strategy_id = :s WHERE id = :r",
        by=LINK_REFUSAL,
        s=other_pk,
        r=dirty_id,
    )
    assert _scalar("SELECT strategy_id FROM strategy_runs WHERE id = :r", r=dirty_id) == owner_pk
    with session_scope(load_settings()) as session:
        latest = latest_standalone_reconciliation(session, OWNER, scope="strategy")
    assert latest is not None and latest.run_id == dirty_id
    assert _gate() is GateCode.RECONCILIATION_NOT_CLEAN


@pytest.mark.parametrize("direction", ["away_from_reconciliation", "into_reconciliation"])
def test_reconciliation_job_type_is_immutable_behind_a_standalone_run(
    migrated_db: str, direction: str
) -> None:
    """The gate needs ``job_type = 'reconciliation'`` through ``strategy_runs.job_id``: one UPDATE of
    the Job's type would drop a newer dirty run from the query, or make a run qualify."""

    _saf01_flagged_job()
    if direction == "away_from_reconciliation":
        with session_scope(load_settings()) as session:
            seed_account_run(session, completed_at=at(5))
            seed_strategy_reconciliation(session, completed_at=at(10))
            dirty = seed_strategy_reconciliation(session, completed_at=at(20), blocks=True)
            run_id, job_id = dirty.id, dirty.job_id
        assert _gate() is GateCode.RECONCILIATION_NOT_CLEAN
        _refuse(
            "UPDATE jobs SET job_type = 'backtest' WHERE id = :j", by=JOB_TYPE_REFUSAL, j=job_id
        )
        assert _scalar("SELECT job_type FROM jobs WHERE id = :j", j=job_id) == "reconciliation"
        with session_scope(load_settings()) as session:
            latest = latest_standalone_reconciliation(session, OWNER)
        assert latest is not None and latest.run_id == run_id
    else:
        # A newer CLEAN run whose Job is not a reconciliation Job does not qualify; the older dirty
        # qualifying run is the one the gate reads.
        with session_scope(load_settings()) as session:
            seed_account_run(session, completed_at=at(5))
            seed_strategy_reconciliation(session, completed_at=at(10), blocks=True)
            clean = seed_strategy_reconciliation(
                session, completed_at=at(20), job_type="phase-probe", trigger_source="job"
            )
            job_id = clean.job_id
        assert _gate() is GateCode.RECONCILIATION_NOT_CLEAN  # arrangement proof
        _refuse(
            "UPDATE jobs SET job_type = 'reconciliation' WHERE id = :j",
            by=JOB_TYPE_REFUSAL,
            j=job_id,
        )
        assert _scalar("SELECT job_type FROM jobs WHERE id = :j", j=job_id) == "phase-probe"
    assert _gate() is GateCode.RECONCILIATION_NOT_CLEAN


# ---------------------------------------------------------------------------
# Legitimate writers
# ---------------------------------------------------------------------------


def test_legitimate_writers_still_work(migrated_db: str) -> None:
    with session_scope(load_settings()) as session:
        strategy = strategy_row(session, OWNER)
        stale = StrategyRun(
            strategy_id=strategy.id,
            job_id=_probe_job(session).id,
            run_type=StrategyRunType.PAPER_EXECUTION,
            status=StrategyRunStatus.RUNNING,
            trigger_source="job",
            started_at=datetime.now(UTC) - timedelta(hours=3),
            parameters_snapshot={"as_of_session": SESSION_DATE.isoformat()},
            result_summary={},
        )
        session.add(stale)
        risk = _plain_run(
            session,
            strategy,
            _probe_job(session),
            StrategyRunType.RISK_EVALUATION,
            status=StrategyRunStatus.RUNNING,
        )
        paper = _plain_run(
            session,
            strategy,
            _probe_job(session),
            StrategyRunType.PAPER_EXECUTION,
            status=StrategyRunStatus.RUNNING,
        )
        session.flush()
        stale_id, risk_id, paper_id = stale.id, risk.id, paper.id

    # stale reclaim: a past-threshold running paper_execution run flips to stale.
    with session_scope(load_settings()) as session:
        reclaimed = reclaim_stale_runs(
            session, strategy_public_id=OWNER, session_date=SESSION_DATE, timeout_minutes=30
        )
    assert reclaimed == [stale_id]
    with session_scope(load_settings()) as session:
        row = session.execute(
            text("SELECT status::text AS status, completed_at FROM strategy_runs WHERE id = :r"),
            {"r": stale_id},
        ).one()
    assert row.status == "stale" and row.completed_at is not None

    # other run types complete freely, and a paper_execution run is not complete-once.
    completed = datetime.now(UTC)
    for run_id in (risk_id, paper_id):
        with session_scope(load_settings()) as session:
            run = session.get(StrategyRun, run_id)
            assert run is not None
            run.status = StrategyRunStatus.SUCCEEDED
            run.result_summary = {"stage": "completed"}
            run.completed_at = completed
        with session_scope(load_settings()) as session:
            run = session.get(StrategyRun, run_id)
            assert run is not None
            run.status = StrategyRunStatus.FAILED
            run.result_summary = {"stage": "rewritten"}
        assert (
            _scalar("SELECT status::text FROM strategy_runs WHERE id = :r", r=run_id) == "failed"
        )


def test_job_lifecycle_writes_behind_a_standalone_run_still_work(migrated_db: str) -> None:
    """Guard 4 fires only on a job_type change: claim, lease, progress, completion, outcome and
    cancellation writes of a Job behind a standalone reconciliation run are untouched."""

    with session_scope(load_settings()) as session:
        strategy = strategy_row(session, OWNER)
        succeeding = _running_job(session)
        failing = _running_job(session)
        for job in (succeeding, failing):
            _plain_run(
                session,
                strategy,
                job,
                StrategyRunType.RECONCILIATION,
                status=StrategyRunStatus.PENDING,
                trigger_source="job",
            )
        succeeding_id, failing_id = succeeding.id, failing.id

    assert renew_lease(job_id=succeeding_id, worker_id=LEASE_OWNER, settings=load_settings())
    with session_scope(load_settings()) as session:
        job = session.get(Job, succeeding_id)
        assert job is not None
        job.progress_percent = 50  # progress write (a plain column UPDATE)
        job.progress_step = "reconciling"
    with session_scope(load_settings()) as session:
        apply_job_transition(
            session,
            job_id=succeeding_id,
            request=JobTransitionRequest(event_type=JobEventType.SUCCEEDED, result_summary={}),
        )
    with session_scope(load_settings()) as session:
        apply_job_transition(
            session,
            job_id=failing_id,
            request=JobTransitionRequest(
                event_type=JobEventType.FAILED,
                failure_reason=JobFailureReason.HANDLER_ERROR,
                failure_message="handler failed",
                outcome_uncertain=False,
            ),
        )
    assert _scalar("SELECT status::text FROM jobs WHERE id = :j", j=succeeding_id) == "succeeded"
    assert _scalar("SELECT status::text FROM jobs WHERE id = :j", j=failing_id) == "failed"
    # A same-value job_type write is not a change.
    _exec("UPDATE jobs SET job_type = job_type WHERE id = :j", j=succeeding_id)
    # ... and the guard is armed on exactly those Jobs (the writes above did not pass vacuously).
    for job_id in (succeeding_id, failing_id):
        _refuse(
            "UPDATE jobs SET job_type = 'phase-probe' WHERE id = :j", by=JOB_TYPE_REFUSAL, j=job_id
        )

    # The guard is scoped: a Job behind only a backtest run, and a Job behind an IN-SESSION
    # reconciliation run (trigger '<x>_reconciliation', never standalone), keep a free job_type.
    with session_scope(load_settings()) as session:
        backtest_job = Job(
            job_type="backtest", payload={}, status=JobStatus.SUCCEEDED, completed_at=at(1)
        )
        session.add(backtest_job)
        session.flush()
        _plain_run(session, strategy_row(session, OWNER), backtest_job, StrategyRunType.BACKTEST)
        in_session = seed_strategy_reconciliation(
            session,
            completed_at=at(2),
            trigger_source="paper_reconciliation",
            job_type="paper-session",
        )
        backtest_job_id, in_session_job_id = backtest_job.id, in_session.job_id
    _exec("UPDATE jobs SET job_type = 'phase-probe' WHERE id = :j", j=backtest_job_id)
    _exec("UPDATE jobs SET job_type = 'phase-probe' WHERE id = :j", j=in_session_job_id)
    assert _scalar("SELECT job_type FROM jobs WHERE id = :j", j=backtest_job_id) == "phase-probe"
    assert _scalar("SELECT job_type FROM jobs WHERE id = :j", j=in_session_job_id) == "phase-probe"


# ---------------------------------------------------------------------------
# Downgrade drops exactly 0030, re-upgrade restores it
# ---------------------------------------------------------------------------


def test_downgrade_drops_exactly_0030_and_reupgrade_restores(migrated_db: str) -> None:
    _saf01_flagged_job()
    with session_scope(load_settings()) as session:
        seed_account_run(session, completed_at=at(5))
        seed_strategy_reconciliation(session, completed_at=at(10))
        dirty = seed_strategy_reconciliation(session, completed_at=at(20), blocks=True)
        evidence = seed_paper_run(session, _probe_job(session))
        completed_account = seed_account_run(session, completed_at=at(6), blocks=True)
        other_pk = strategy_row(session, OTHER).id
        dirty_job_id, evidence_id = dirty.job_id, evidence.id
        completed_account_id = completed_account.id
    assert _gate() is GateCode.RECONCILIATION_NOT_CLEAN
    assert _trigger_count(NEW_TRIGGERS) == 4 and _function_count(NEW_FUNCTIONS) == 4
    assert _trigger_count(TRIGGERS_0029) == 23 and _function_count(FUNCTIONS_0029) == 4
    # Refused while 0030 is in place.
    _refuse(
        "UPDATE strategy_runs SET strategy_id = :s WHERE id = :r",
        by=LINK_REFUSAL,
        s=other_pk,
        r=evidence_id,
    )
    _refuse(
        "UPDATE jobs SET job_type = 'backtest' WHERE id = :j", by=JOB_TYPE_REFUSAL, j=dirty_job_id
    )
    _refuse(
        "UPDATE account_reconciliation_runs SET blocks_execution = false WHERE id = :a",
        by=ACCOUNT_RUN_REFUSAL,
        a=completed_account_id,
    )

    _fresh_caches()
    command.downgrade(build_alembic_config(), PREVIOUS_REVISION)
    _fresh_caches()
    assert _trigger_count(NEW_TRIGGERS) == 0
    assert _function_count(NEW_FUNCTIONS) == 0
    assert _trigger_count(TRIGGERS_0029) == 23 and _function_count(FUNCTIONS_0029) == 4
    assert _scalar("SELECT version_num FROM alembic_version") == PREVIOUS_REVISION

    # Without 0030 the same statements succeed on these throwaway rows: the refusals above came from
    # 0030 and from nothing else, and one Job-type UPDATE really does release the gate.
    _exec("UPDATE strategy_runs SET strategy_id = :s WHERE id = :r", s=other_pk, r=evidence_id)
    _exec(
        "UPDATE account_reconciliation_runs SET blocks_execution = false WHERE id = :a",
        a=completed_account_id,
    )
    _exec("UPDATE jobs SET job_type = 'backtest' WHERE id = :j", j=dirty_job_id)
    assert _gate() is None  # the newer dirty run dropped out of the query: the older clean one rules

    _fresh_caches()
    command.upgrade(build_alembic_config(), "head")
    _fresh_caches()
    assert _trigger_count(NEW_TRIGGERS) == 4 and _function_count(NEW_FUNCTIONS) == 4
    assert _trigger_count(TRIGGERS_0029) == 23 and _function_count(FUNCTIONS_0029) == 4
    assert _scalar("SELECT version_num FROM alembic_version") == _head()
    # Re-armed.
    _refuse(
        "UPDATE jobs SET job_type = 'phase-probe' WHERE id = :j", by=JOB_TYPE_REFUSAL, j=dirty_job_id
    )
