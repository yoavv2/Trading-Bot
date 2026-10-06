"""Phase 20.1 gap E1: the reconciliation Job-type guard holds in every window (plan 20.1-38).

The round-3 safety review (WR-01) and the round-4 re-verification (escalation E-1) reproduced three
single-statement ways around the ``job_type`` guard of migration 0030. Each one hides a newer DIRTY
standalone strategy reconciliation from the recovery gate, so an older CLEAN one releases it:

(a) pre-run window: ``reconcile_paper_execution`` reads the broker BEFORE it inserts its run, so the
    RUNNING ``reconciliation`` Job has no ``strategy_runs`` row yet and the guard saw nothing;
(b) insert race: the run INSERT's foreign key takes FOR KEY SHARE on the Job row, a ``job_type``
    UPDATE takes FOR NO KEY UPDATE (no conflict), and the guard could not see the uncommitted run;
(c) lookup shadowing: the guard looked ``strategy_runs`` up through the CALLER's search path, so a
    session TEMP table (or a caller-owned schema) named ``strategy_runs`` answered instead.

User decision 2026-10-06: E1 is in scope. Each probe is a regression on the PRODUCT path: the real
standalone strategy reconciliation (what the ``reconciliation`` Job handler calls, with
``trigger_source='job'`` and the Job's id) against a scripted broker that shows an unrecognized order,
so the new run is DIRTY. The arrangement first proves that an older clean pair releases the gate
(gate ``None``), the attack runs from a SEPARATE connection inside its window, and each test asserts
the refusal (SQLSTATE 23000 and the 0030 message, never a lock timeout), the Job type unchanged, the
dirty run as the newest standalone strategy reconciliation and the gate ``reconciliation_not_clean``.

The reconciliation-Job cases are refused by a column arm whatever the lookups resolve to, so the
schema qualification and the pinned search path are proven at trigger level on the arms that still
read tables (guard 4's retained EXISTS arm, guard 1's three EXISTS arms, the R and J arms of the 0029
delete guard), under TEMP and caller-schema shadows and changed caller search paths, and against
operators planted in ``public`` (PUBLIC holds CREATE there on PostgreSQL 14). Every trigger-level
attack is rolled back. Legitimate Job lifecycle writes and unrelated Job types stay free; the
function configuration and an exact downgrade are pinned. Throwaway databases only; no network.
"""

from __future__ import annotations

import re
import sys
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg
import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alembic import command
from scripts.migrate import build_alembic_config
from tests.support.migrated_db import migrated_database
from tests.support.real_reconciliation import (
    _finish_job,
    _start_reconciliation_job,
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
from tests.test_paper_execution import FakeBrokerClient
from tests.test_reconciliation_shared_evidence import _unrecognized_broker_order

from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.db.models import (
    AttemptOutcomeClass,
    Job,
    JobEventType,
    JobFailureReason,
    JobStatus,
    OrderEvent,
    OrderLifecycleState,
    OrderSubmissionAttempt,
    OrderTransitionOutcome,
    StrategyRun,
    StrategyRunStatus,
    StrategyRunType,
)
from trading_platform.db.models.order_event import OrderTransitionEventType
from trading_platform.db.session import clear_engine_cache, session_scope
from trading_platform.jobs.cancellation import acknowledge_cancellation, request_cancellation
from trading_platform.jobs.lifecycle import JobTransitionRequest, apply_job_transition
from trading_platform.jobs.queue import claim_next_job, renew_lease
from trading_platform.services.broker_jobs import RECONCILIATION_JOB_TYPE
from trading_platform.services.reconciliation import (
    latest_standalone_reconciliation,
    reconcile_paper_execution,
)
from trading_platform.services.reconciliation.latest import STANDALONE_TRIGGER_SOURCE
from trading_platform.services.recovery import GateCode, strategy_recovery_status

REVISION = "0030_phase20_1_evidence_update_guards"
PREVIOUS_REVISION = "0029_phase20_1_order_origin_immutable"
MIGRATION_0030 = Path(__file__).resolve().parents[1] / "alembic" / "versions" / f"{REVISION}.py"

#: ``pg_proc.proconfig`` of every guard function that reads a table: the application schema, with
#: ``pg_temp`` explicitly LAST (an omitted ``pg_temp`` is searched FIRST for relation names).
TRUSTED_SEARCH_PATH = "search_path=pg_catalog, public, pg_temp"
FUNCTIONS_0030 = (
    "phase20_1_strategy_run_link_immutable",
    "phase20_1_reconciliation_run_complete_once",
    "phase20_1_account_reconciliation_run_complete_once",
    "phase20_1_reconciliation_job_type_immutable",
)
#: The only 0028 / 0029 function with relation lookups: 0030 pins its search path by ALTER FUNCTION.
DELETE_GUARD_0029 = "phase20_1_evidence_no_delete"
FUNCTIONS_0029 = (
    "paper_orders_origin_run_immutable",
    "order_events_append_only",
    DELETE_GUARD_0029,
    "phase20_1_evidence_no_truncate",
)
#: Outside this correction (no relation lookup, or not a 0029 / 0030 function): untouched.
UNTOUCHED_FUNCTIONS = (
    "paper_orders_origin_run_immutable",
    "order_events_append_only",
    "phase20_1_evidence_no_truncate",
    "order_submission_attempts_append_only",
    "external_broker_activity_immutable",
    "recovery_records_immutable",
)

JOB_TYPE_REFUSAL = (
    "job_type of a reconciliation Job or of a Job behind a standalone reconciliation run is "
    "immutable"
)
LINK_REFUSAL = "strategy_runs evidence link is immutable"
RECONCILIATION_RUN_REFUSAL = "reconciliation run is complete-once"
ACCOUNT_RUN_REFUSAL = "account reconciliation run is complete-once"
RUN_DELETE_REFUSAL = "strategy_runs rows are evidence and cannot be deleted"
JOB_DELETE_REFUSAL = "jobs rows are evidence and cannot be deleted"

#: The type an attacker gives the Job: one the gate never reads and that is not broker-touching, so
#: an ACCEPTED retype leaves the older clean pair qualifying and releases the gate (the E1 effect).
DISGUISE = "backtest"
RETYPE = "UPDATE public.jobs SET job_type = %s WHERE id = %s"
WORKER = "e1-guard-windows-worker"
PENDING = OrderLifecycleState.PENDING_SUBMISSION
SHADOW_SCHEMA = "e1_shadow"
SHADOW_COLUMNS = "(id uuid, job_id uuid, strategy_run_id uuid, run_type text, trigger_source text)"


@pytest.fixture()
def migrated_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    with migrated_database(monkeypatch, "phase20_1_e1_windows") as name:
        yield name


def _fresh_caches() -> None:
    clear_settings_cache()
    clear_engine_cache()


# ---------------------------------------------------------------------------
# Reads through the application session
# ---------------------------------------------------------------------------


def _scalar(sql: str, **params: object) -> Any:
    with session_scope(load_settings()) as session:
        return session.execute(text(sql), params).scalar_one()


def _exec(sql: str, **params: object) -> None:
    """Run a statement that must be ACCEPTED."""

    with session_scope(load_settings()) as session:
        session.execute(text(sql), params)


def _gate() -> GateCode | None:
    with session_scope(load_settings()) as session:
        return strategy_recovery_status(session, OWNER).gate_code


def _job_type(job_id: uuid.UUID) -> str:
    return str(_scalar("SELECT job_type FROM jobs WHERE id = :j", j=job_id))


def _job_status(job_id: uuid.UUID) -> str:
    return str(_scalar("SELECT status::text FROM jobs WHERE id = :j", j=job_id))


def _latest_strategy_run() -> uuid.UUID | None:
    with session_scope(load_settings()) as session:
        latest = latest_standalone_reconciliation(session, OWNER, scope="strategy")
    return latest.run_id if latest is not None else None


# ---------------------------------------------------------------------------
# The attacker: an independent connection with transactions of its own
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Attempt:
    """What one statement sent from the attacker's connection did."""

    accepted: bool
    sqlstate: str | None
    message: str | None


@contextmanager
def _other_connection() -> Iterator[psycopg.Connection]:
    """A second connection to the throwaway database. A lock wait fails after 2 s with SQLSTATE
    55P03 instead of hanging the test (and is then NOT a guard refusal)."""

    database = load_settings().database
    connection = psycopg.connect(
        host=database.host,
        port=database.port,
        dbname=database.name,
        user=database.user,
        password=database.password,
    )
    try:
        connection.execute("SET lock_timeout = '2s'")
        connection.execute("SET statement_timeout = '20s'")
        connection.commit()
        yield connection
    finally:
        connection.rollback()
        connection.close()


def _attempt(
    connection: psycopg.Connection, sql: str, params: Sequence[Any] = (), *, keep: bool
) -> Attempt:
    """Send ``sql`` in the connection's CURRENT transaction (after any arrangement already sent in
    it). A refusal rolls back. An accepted statement is committed when ``keep`` (so a gate test
    observes what an open window does) and rolled back otherwise."""

    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
    except psycopg.Error as exc:
        connection.rollback()
        return Attempt(False, exc.sqlstate, exc.diag.message_primary)
    if keep:
        connection.commit()
    else:
        connection.rollback()
    return Attempt(True, None, None)


def _refused(attempt: Attempt, by: str) -> None:
    assert not attempt.accepted, "ACCEPTED: the statement went through (the window is open)"
    assert attempt.sqlstate == "23000", (
        f"refused with SQLSTATE {attempt.sqlstate} ({attempt.message}), not by a guard (23000); "
        "55P03 would be a lock wait"
    )
    assert attempt.message is not None and attempt.message.startswith(by), attempt.message


def _retype(job_id: uuid.UUID, to: str) -> Attempt:
    """A plain retype from another connection (rolled back if it were accepted)."""

    with _other_connection() as connection:
        return _attempt(connection, RETYPE, (to, job_id), keep=False)


def _visible_runs(connection: psycopg.Connection, job_id: uuid.UUID) -> int:
    """How many ``strategy_runs`` rows of ``job_id`` this connection can see (own transaction)."""

    count = connection.execute(
        "SELECT count(*) FROM public.strategy_runs WHERE job_id = %s", (job_id,)
    ).fetchone()
    connection.rollback()
    assert count is not None
    return int(count[0])


def _shadow(
    connection: psycopg.Connection, *, kind: str, caller_path: str, tables: Sequence[str]
) -> dict[str, str]:
    """In the attacker's open transaction: set the CALLER's search path and create tables named like
    the guarded ones (``kind`` ``temp``: session TEMP tables; ``schema``: a caller-owned schema
    listed first). Returns, per name, the schema the caller's own unqualified lookup resolves to,
    which proves the shadow is active for anything that looks the name up through that path."""

    with connection.cursor() as cursor:
        if kind == "schema":
            cursor.execute(f"CREATE SCHEMA {SHADOW_SCHEMA}")
        cursor.execute(f"SET LOCAL search_path TO {caller_path}")
        for name in tables:
            owner = SHADOW_SCHEMA if kind == "schema" else "pg_temp"
            cursor.execute(f"CREATE TABLE {owner}.{name} {SHADOW_COLUMNS}")
        resolved: dict[str, str] = {}
        for name in tables:
            cursor.execute(
                "SELECT n.nspname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE c.oid = %s::regclass",
                (name,),
            )
            row = cursor.fetchone()
            assert row is not None
            resolved[name] = str(row[0])
    return resolved


def _shadow_active(resolved: dict[str, str], kind: str) -> bool:
    if kind == "schema":
        return all(schema == SHADOW_SCHEMA for schema in resolved.values())
    return all(schema.startswith("pg_temp") for schema in resolved.values())


# ---------------------------------------------------------------------------
# The gate world and the real reconciliation
# ---------------------------------------------------------------------------


def _released_world() -> None:
    """A flagged SAF-01 Job (effect at T0) and an older CLEAN standalone pair (account run at +5
    minutes, strategy reconciliation at +10): the gate is released. A newer DIRTY strategy
    reconciliation blocks it again, unless that run drops out of the gate's query (the E1 effect)."""

    with session_scope(load_settings()) as session:
        job = seed_job(session, completed_at=at(0))
        run = seed_paper_run(session, job)
        seed_operation_bound_intent(session, run, status=PENDING, attempts=())
        seed_account_run(session, completed_at=at(5))
        seed_strategy_reconciliation(session, completed_at=at(10))
    assert _gate() is None  # arrangement: losing the newer dirty run WOULD release the gate


def _dirty_broker() -> FakeBrokerClient:
    """A read-side broker that shows an order the platform never registered (blocks)."""

    return scripted_read_broker(orders=[_unrecognized_broker_order()])


def _start_reconciliation() -> uuid.UUID:
    """A RUNNING ``reconciliation`` Job with a lease, as the worker claims it (no run yet)."""

    return _start_reconciliation_job(
        {"strategy_id": OWNER, "as_of_session": SESSION_DATE.isoformat()}
    )


def _reconcile(job_id: uuid.UUID, broker: Any) -> uuid.UUID:
    """The handler's call for a strategy-scope reconciliation Job, then the Job succeeds."""

    report = reconcile_paper_execution(
        OWNER,
        as_of_session=SESSION_DATE,
        trigger_source=STANDALONE_TRIGGER_SOURCE,
        job_id=job_id,
        broker_client=broker,
        settings=load_settings(),
    )
    assert report.blocks_execution  # the new run is DIRTY
    _finish_job(job_id)
    return uuid.UUID(report.run_id)


def _assert_dirty_run_still_blocks(job_id: uuid.UUID, run_id: uuid.UUID, attempt: Attempt) -> None:
    picture = {
        "job_type": _job_type(job_id),
        "newest_strategy_reconciliation_is_the_dirty_run": _latest_strategy_run() == run_id,
        "gate": _gate(),
    }
    assert not attempt.accepted, f"E1 window OPEN: the retype was accepted; afterwards {picture}"
    _refused(attempt, JOB_TYPE_REFUSAL)
    assert picture == {
        "job_type": RECONCILIATION_JOB_TYPE,
        "newest_strategy_reconciliation_is_the_dirty_run": True,
        "gate": GateCode.RECONCILIATION_NOT_CLEAN,
    }


class _FirstReadHook:
    """The read side of a real reconciliation that runs ``attack`` once, on the FIRST broker read.
    ``reconcile_paper_execution`` reads the broker BEFORE it inserts its run: the pre-run window."""

    def __init__(self, inner: FakeBrokerClient, attack: Callable[[], Any]) -> None:
        self._inner = inner
        self._attack = attack
        self.results: list[Any] = []

    def close(self) -> None:
        self._inner.close()

    def list_orders(self) -> Any:
        if not self.results:
            self.results.append(self._attack())
        return self._inner.list_orders()

    def list_fills(self) -> Any:
        return self._inner.list_fills()

    def list_positions(self) -> Any:
        return self._inner.list_positions()

    def get_account(self) -> Any:
        return self._inner.get_account()


@contextmanager
def _on_run_insert(job_id: uuid.UUID, attack: Callable[[], Any]) -> Iterator[list[Any]]:
    """Run ``attack`` once INSIDE the product's transaction: right after the reconciliation run of
    ``job_id`` was flushed (its INSERT sent, so the foreign key holds FOR KEY SHARE on the Job row)
    and BEFORE that transaction commits. The listener is always removed."""

    results: list[Any] = []

    def after_flush(session: Session, flush_context: Any) -> None:
        if results:
            return
        for instance in session.new:
            if (
                isinstance(instance, StrategyRun)
                and instance.job_id == job_id
                and instance.run_type is StrategyRunType.RECONCILIATION
            ):
                results.append(attack())
                return

    event.listen(Session, "after_flush", after_flush)
    try:
        yield results
    finally:
        event.remove(Session, "after_flush", after_flush)


# ---------------------------------------------------------------------------
# E1 (a), (b), (c) on the product path, through the recovery gate
# ---------------------------------------------------------------------------


def test_e1a_retype_before_the_run_exists_is_refused_and_the_dirty_run_keeps_blocking(
    migrated_db: str,
) -> None:
    _released_world()
    job_id = _start_reconciliation()

    def attack() -> tuple[int, Attempt]:
        with _other_connection() as connection:
            runs = _visible_runs(connection, job_id)
            return runs, _attempt(connection, RETYPE, (DISGUISE, job_id), keep=True)

    hook = _FirstReadHook(_dirty_broker(), attack)
    run_id = _reconcile(job_id, hook)

    assert len(hook.results) == 1, "the pre-run hook never fired"
    runs_at_attack, attempt = hook.results[0]
    assert runs_at_attack == 0  # the attack landed BEFORE the run row existed (window a)
    _assert_dirty_run_still_blocks(job_id, run_id, attempt)


def test_e1b_retype_racing_the_uncommitted_run_insert_is_refused(migrated_db: str) -> None:
    _released_world()
    job_id = _start_reconciliation()

    def attack() -> tuple[int, Attempt]:
        with _other_connection() as connection:
            visible = _visible_runs(connection, job_id)
            return visible, _attempt(connection, RETYPE, (DISGUISE, job_id), keep=True)

    with _on_run_insert(job_id, attack) as fired:
        run_id = _reconcile(job_id, _dirty_broker())

    assert len(fired) == 1, "the run-INSERT hook never fired"
    visible_at_attack, attempt = fired[0]
    # Two real transactions: the run INSERT was sent but not committed, so the attacker's
    # transaction could not see it (window b) ...
    assert visible_at_attack == 0
    # ... and once the product committed, the run exists and hangs off the Job.
    assert _scalar("SELECT job_id FROM strategy_runs WHERE id = :r", r=run_id) == job_id
    _assert_dirty_run_still_blocks(job_id, run_id, attempt)


CALLER_PATHS = {
    "default_path": '"$user", public',
    "temp_first": "pg_temp, public",
    "temp_only": "pg_temp",
}


@pytest.mark.parametrize("caller_path", list(CALLER_PATHS.values()), ids=list(CALLER_PATHS))
def test_e1c_temp_shadow_cannot_retype_the_job_behind_a_committed_dirty_run(
    migrated_db: str, caller_path: str
) -> None:
    _released_world()
    report, job_id = run_real_strategy_reconciliation(
        strategy_id=OWNER, as_of_session=SESSION_DATE, broker=_dirty_broker()
    )
    run_id = uuid.UUID(report.run_id)
    assert report.blocks_execution
    assert _gate() is GateCode.RECONCILIATION_NOT_CLEAN  # the committed dirty run rules

    with _other_connection() as connection:
        resolved = _shadow(
            connection, kind="temp", caller_path=caller_path, tables=("strategy_runs",)
        )
        assert _shadow_active(resolved, "temp"), resolved
        attempt = _attempt(connection, RETYPE, (DISGUISE, job_id), keep=True)
    _assert_dirty_run_still_blocks(job_id, run_id, attempt)


# ---------------------------------------------------------------------------
# Trigger level: every table-reading arm ignores shadows and caller search paths
# ---------------------------------------------------------------------------

SHADOWS = {
    "temp_default_path": ("temp", '"$user", public'),
    "temp_first": ("temp", "pg_temp, public"),
    "temp_only": ("temp", "pg_temp"),
    "caller_schema_first": ("schema", f"{SHADOW_SCHEMA}, public"),
}
EVIDENCE_TABLES = ("paper_orders", "order_events", "order_submission_attempts")


@dataclass(frozen=True)
class Case:
    sql: str
    params: tuple[Any, ...]
    tables: tuple[str, ...]
    refusal: str
    unchanged: Callable[[], bool]


def _probe_job(session: Session) -> Job:
    """A terminal, unflagged Job of a type 0029 J does not protect."""

    return seed_job(session, job_type="phase-probe", uncertain=False, status=JobStatus.SUCCEEDED)


def _dry_bootstrap_run(session: Session) -> StrategyRun:
    run = StrategyRun(
        strategy_id=strategy_row(session, OWNER).id,
        job_id=_probe_job(session).id,
        run_type=StrategyRunType.DRY_BOOTSTRAP,
        status=StrategyRunStatus.SUCCEEDED,
        trigger_source="test_suite",
        parameters_snapshot={},
        result_summary={},
    )
    session.add(run)
    session.flush()
    return run


def _evidence_only_run(reference: str) -> uuid.UUID:
    """A ``dry_bootstrap`` run that is evidence ONLY through one referencing row (0029 R / guard 1
    EXISTS arm): it originated an order, or an order of another run has an event or an attempt
    recorded by it."""

    with session_scope(load_settings()) as session:
        run = _dry_bootstrap_run(session)
        if reference == "order_origin":
            seed_intent(session, run, status=PENDING, attempts=())
            return run.id
        origin = seed_paper_run(session, _probe_job(session))
        order = seed_intent(session, origin, status=PENDING, attempts=())
        if reference == "order_event":
            session.add(
                OrderEvent(
                    paper_order_id=order.id,
                    strategy_run_id=run.id,
                    from_state=PENDING,
                    to_state=PENDING,
                    event_type=OrderTransitionEventType.RETRY_REQUESTED,
                    outcome=OrderTransitionOutcome.ACCEPTED,
                    event_at=at(0),
                    details={},
                )
            )
        else:
            assert reference == "attempt"
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
        return run.id


def _run_strategy_pk(run_id: uuid.UUID) -> uuid.UUID:
    return uuid.UUID(str(_scalar("SELECT strategy_id FROM strategy_runs WHERE id = :r", r=run_id)))


def _row_exists(table: str, row_id: uuid.UUID) -> bool:
    return bool(_scalar(f"SELECT count(*) FROM {table} WHERE id = :i", i=row_id))


def _case(name: str) -> Case:
    if name == "job_type_exists_arm":
        # A Job of ANOTHER type behind a standalone reconciliation run: only the EXISTS arm protects.
        with session_scope(load_settings()) as session:
            job_id = seed_strategy_reconciliation(
                session, completed_at=at(20), job_type="phase-probe"
            ).job_id
        assert job_id is not None
        return Case(
            RETYPE,
            (DISGUISE, job_id),
            ("strategy_runs",),
            JOB_TYPE_REFUSAL,
            lambda: _job_type(job_id) == "phase-probe",
        )
    if name.startswith("link_"):
        run_id = _evidence_only_run(name.removeprefix("link_"))
        with session_scope(load_settings()) as session:
            other_pk = strategy_row(session, OTHER).id
        owner_pk = _run_strategy_pk(run_id)
        return Case(
            "UPDATE public.strategy_runs SET strategy_id = %s WHERE id = %s",
            (other_pk, run_id),
            EVIDENCE_TABLES,
            LINK_REFUSAL,
            lambda: _run_strategy_pk(run_id) == owner_pk,
        )
    if name.startswith("run_delete_"):
        run_id = _evidence_only_run(name.removeprefix("run_delete_"))
        return Case(
            "DELETE FROM public.strategy_runs WHERE id = %s",
            (run_id,),
            EVIDENCE_TABLES,
            RUN_DELETE_REFUSAL,
            lambda: _row_exists("strategy_runs", run_id),
        )
    assert name == "job_delete_reconciliation_evidence"
    with session_scope(load_settings()) as session:
        job_id = seed_strategy_reconciliation(
            session, completed_at=at(20), job_type="phase-probe"
        ).job_id
    assert job_id is not None
    return Case(
        "DELETE FROM public.jobs WHERE id = %s",
        (job_id,),
        ("strategy_runs",),
        JOB_DELETE_REFUSAL,
        lambda: _row_exists("jobs", job_id),
    )


#: Guard 4's retained arm, guard 1's three EXISTS arms, and the 0029 delete guard's R arm (an
#: attempt is the ONLY protection of such a run: the attempt's SET NULL is legal under 0028) and
#: J arm (asserted by the 0029 message: without it the 0030 SET NULL check would refuse instead).
TABLE_READING_ARMS = (
    "job_type_exists_arm",
    "link_order_origin",
    "link_order_event",
    "link_attempt",
    "run_delete_attempt",
    "run_delete_order_event",
    "job_delete_reconciliation_evidence",
)


@pytest.mark.parametrize("shadow", list(SHADOWS))
@pytest.mark.parametrize("arm", TABLE_READING_ARMS)
def test_table_reading_guard_arms_ignore_shadows_and_the_caller_search_path(
    migrated_db: str, arm: str, shadow: str
) -> None:
    case = _case(arm)
    kind, caller_path = SHADOWS[shadow]
    with _other_connection() as connection:
        resolved = _shadow(connection, kind=kind, caller_path=caller_path, tables=case.tables)
        assert _shadow_active(resolved, kind), resolved
        attempt = _attempt(connection, case.sql, case.params, keep=False)
    _refused(attempt, case.refusal)
    assert case.unchanged()


# ---------------------------------------------------------------------------
# Trigger level: an operator planted in public cannot change a verdict
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Plant:
    """``=`` for ``type`` planted in ``public`` returning ``result``, and a probe whose value shows
    the plant decides that comparison for an ordinary session."""

    type: str
    result: bool
    probe: str
    probe_value: bool


VARCHAR_NEVER_EQUAL = Plant(
    "varchar", False, "SELECT 'reconciliation'::varchar = 'reconciliation'::varchar", False
)
VARCHAR_ALWAYS_EQUAL = Plant("varchar", True, "SELECT 'job'::varchar = 'x'::varchar", True)
RUN_TYPE_ALWAYS_EQUAL = Plant(
    "strategy_run_type",
    True,
    "SELECT 'paper_execution'::strategy_run_type = 'backtest'::strategy_run_type",
    True,
)
RUN_STATUS_ALWAYS_EQUAL = Plant(
    "strategy_run_status",
    True,
    "SELECT 'succeeded'::strategy_run_status = 'failed'::strategy_run_status",
    True,
)


def _plant(connection: psycopg.Connection, plant: Plant) -> None:
    """In the attacker's open transaction (rolled back with the attempt)."""

    with connection.cursor() as cursor:
        cursor.execute(
            f"CREATE FUNCTION public.e1_planted_eq({plant.type}, {plant.type}) RETURNS boolean "
            f"LANGUAGE sql IMMUTABLE AS 'SELECT {str(plant.result).lower()}'"
        )
        cursor.execute(
            f"CREATE OPERATOR public.= (LEFTARG = {plant.type}, RIGHTARG = {plant.type}, "
            "FUNCTION = public.e1_planted_eq)"
        )
        cursor.execute(plant.probe)
        row = cursor.fetchone()
        assert row is not None and row[0] is plant.probe_value, (plant, row)


def _operator_case(name: str) -> tuple[Plant, Case]:
    if name == "job_type_column_arm":
        job_id = _start_reconciliation()  # a reconciliation Job no run references
        return VARCHAR_NEVER_EQUAL, Case(
            RETYPE,
            (DISGUISE, job_id),
            (),
            JOB_TYPE_REFUSAL,
            lambda: _job_type(job_id) == RECONCILIATION_JOB_TYPE,
        )
    if name == "job_type_exists_arm":
        return VARCHAR_NEVER_EQUAL, _case("job_type_exists_arm")
    if name == "link_run_type":
        with session_scope(load_settings()) as session:
            run_id = seed_paper_run(session, _probe_job(session)).id
        return RUN_TYPE_ALWAYS_EQUAL, Case(
            "UPDATE public.strategy_runs SET run_type = 'backtest' WHERE id = %s",
            (run_id,),
            (),
            LINK_REFUSAL,
            lambda: (
                _scalar("SELECT run_type::text FROM strategy_runs WHERE id = :r", r=run_id)
                == "paper_execution"
            ),
        )
    if name in ("reconciliation_run_status", "reconciliation_run_trigger_source"):
        with session_scope(load_settings()) as session:
            run_id = seed_strategy_reconciliation(session, completed_at=at(20), blocks=True).id
        if name == "reconciliation_run_status":
            plant, sql = RUN_STATUS_ALWAYS_EQUAL, "status = 'failed'"
        else:
            plant, sql = VARCHAR_ALWAYS_EQUAL, "trigger_source = 'tampered'"
        return plant, Case(
            f"UPDATE public.strategy_runs SET {sql} WHERE id = %s",
            (run_id,),
            (),
            RECONCILIATION_RUN_REFUSAL,
            lambda: (
                _scalar(
                    "SELECT status::text || '/' || trigger_source FROM strategy_runs WHERE id = :r",
                    r=run_id,
                )
                == "succeeded/job"
            ),
        )
    assert name in ("account_run_status", "account_run_trigger_source")
    with session_scope(load_settings()) as session:
        account_id = seed_account_run(session, completed_at=at(20), blocks=True).id
    sql = "status = 'failed'" if name == "account_run_status" else "trigger_source = 'tampered'"
    return VARCHAR_ALWAYS_EQUAL, Case(
        f"UPDATE public.account_reconciliation_runs SET {sql} WHERE id = %s",
        (account_id,),
        (),
        ACCOUNT_RUN_REFUSAL,
        lambda: (
            _scalar(
                "SELECT status || '/' || trigger_source FROM account_reconciliation_runs "
                "WHERE id = :a",
                a=account_id,
            )
            == "succeeded/job"
        ),
    )


OPERATOR_CASES = (
    "job_type_column_arm",
    "job_type_exists_arm",
    "link_run_type",
    "reconciliation_run_status",
    "reconciliation_run_trigger_source",
    "account_run_status",
    "account_run_trigger_source",
)


@pytest.mark.parametrize("name", OPERATOR_CASES)
def test_an_operator_planted_in_public_cannot_change_a_0030_verdict(
    migrated_db: str, name: str
) -> None:
    plant, case = _operator_case(name)
    with _other_connection() as connection:
        _plant(connection, plant)
        attempt = _attempt(connection, case.sql, case.params, keep=False)
    _refused(attempt, case.refusal)
    assert case.unchanged()
    assert not _scalar("SELECT count(*) FROM pg_proc WHERE proname = 'e1_planted_eq'")


# ---------------------------------------------------------------------------
# Legitimate writes stay legal
# ---------------------------------------------------------------------------


def _queued_reconciliation(payload: dict[str, Any]) -> uuid.UUID:
    with session_scope(load_settings()) as session:
        job = Job(job_type=RECONCILIATION_JOB_TYPE, payload=payload, status=JobStatus.QUEUED)
        session.add(job)
        session.flush()
        return job.id


STRATEGY_PAYLOAD = {"strategy_id": OWNER, "as_of_session": SESSION_DATE.isoformat()}
ACCOUNT_PAYLOAD = {"scope": "account"}


def test_a_queued_reconciliation_job_is_typed_for_good_and_still_cancellable(
    migrated_db: str,
) -> None:
    """Approved widening: a QUEUED reconciliation Job (no run exists yet) keeps its type."""

    job_id = _queued_reconciliation(STRATEGY_PAYLOAD)
    _refused(_retype(job_id, DISGUISE), JOB_TYPE_REFUSAL)
    _refused(_retype(job_id, "paper-session"), JOB_TYPE_REFUSAL)
    request_cancellation(
        job_id=job_id, requested_by="e1-test", reason="lifecycle pin", settings=load_settings()
    )
    assert _job_status(job_id) == "cancelled"
    assert _job_type(job_id) == RECONCILIATION_JOB_TYPE
    _refused(_retype(job_id, DISGUISE), JOB_TYPE_REFUSAL)


@pytest.mark.parametrize(
    ("payload", "ending"),
    [
        (STRATEGY_PAYLOAD, "succeeded"),
        (ACCOUNT_PAYLOAD, "failed"),
        (STRATEGY_PAYLOAD, "cancelled"),
    ],
    ids=["strategy_scope_succeeds", "account_scope_fails", "running_cancelled"],
)
def test_every_lifecycle_write_of_a_reconciliation_job_stays_legal(
    migrated_db: str, payload: dict[str, Any], ending: str
) -> None:
    """Claim, lease renewal, progress, success / failure / cooperative cancellation: every write
    lands, none changes the type, and the guard is armed in every state (so the writes did not pass
    vacuously). A same-value job_type write is not a change."""

    settings = load_settings()
    job_id = _queued_reconciliation(payload)
    with session_scope(settings) as session:
        assert claim_next_job(session, worker_id=WORKER) == job_id
    assert _job_status(job_id) == "running"
    assert renew_lease(job_id=job_id, worker_id=WORKER, settings=settings)
    with session_scope(settings) as session:
        job = session.get(Job, job_id)
        assert job is not None
        job.progress_percent = 40
        job.progress_step = "reconciling"
    _refused(_retype(job_id, DISGUISE), JOB_TYPE_REFUSAL)  # RUNNING, no run yet

    if ending == "succeeded":
        with session_scope(settings) as session:
            apply_job_transition(
                session,
                job_id=job_id,
                request=JobTransitionRequest(event_type=JobEventType.SUCCEEDED, result_summary={}),
            )
    elif ending == "failed":
        with session_scope(settings) as session:
            apply_job_transition(
                session,
                job_id=job_id,
                request=JobTransitionRequest(
                    event_type=JobEventType.FAILED,
                    failure_reason=JobFailureReason.HANDLER_ERROR,
                    failure_message="handler failed",
                    outcome_uncertain=False,
                ),
            )
    else:
        request_cancellation(job_id=job_id, requested_by="e1-test", settings=settings)
        with session_scope(settings) as session:
            acknowledge_cancellation(session, job_id=job_id)

    assert _job_status(job_id) == ending
    assert _job_type(job_id) == RECONCILIATION_JOB_TYPE
    _exec("UPDATE jobs SET job_type = job_type WHERE id = :j", j=job_id)
    _refused(_retype(job_id, DISGUISE), JOB_TYPE_REFUSAL)
    _refused(_retype(job_id, "paper-session"), JOB_TYPE_REFUSAL)


def test_a_real_account_reconciliation_job_completes_and_keeps_its_type(migrated_db: str) -> None:
    report, job_id = run_real_account_reconciliation(broker=scripted_read_broker())
    assert report.run_id
    assert _job_status(job_id) == "succeeded"
    _refused(_retype(job_id, DISGUISE), JOB_TYPE_REFUSAL)


def test_unrelated_job_types_stay_free_and_no_job_becomes_a_reconciliation_job(
    migrated_db: str,
) -> None:
    with session_scope(load_settings()) as session:
        free = _probe_job(session)
        backtest = seed_job(
            session, job_type="backtest", uncertain=False, status=JobStatus.SUCCEEDED
        )
        in_session = seed_strategy_reconciliation(
            session,
            completed_at=at(2),
            trigger_source="paper_reconciliation",
            job_type="paper-session",
        )
        free_id, backtest_id, in_session_job = free.id, backtest.id, in_session.job_id
    assert in_session_job is not None
    # Neither side is 'reconciliation' and no standalone run references the Job: legal.
    _exec("UPDATE jobs SET job_type = 'risk-evaluation' WHERE id = :j", j=free_id)
    _exec("UPDATE jobs SET job_type = 'phase-probe' WHERE id = :j", j=backtest_id)
    _exec("UPDATE jobs SET job_type = 'phase-probe' WHERE id = :j", j=in_session_job)
    assert _job_type(free_id) == "risk-evaluation"
    assert _job_type(backtest_id) == "phase-probe"
    assert _job_type(in_session_job) == "phase-probe"
    # Approved widening: no Job is turned INTO a reconciliation Job, with or without a run.
    for job_id in (free_id, backtest_id, in_session_job):
        _refused(_retype(job_id, RECONCILIATION_JOB_TYPE), JOB_TYPE_REFUSAL)
        assert _job_type(job_id) != RECONCILIATION_JOB_TYPE


# ---------------------------------------------------------------------------
# Configuration, literals and an exact downgrade
# ---------------------------------------------------------------------------


def _proc_rows(names: Sequence[str]) -> dict[str, dict[str, Any]]:
    with session_scope(load_settings()) as session:
        rows = session.execute(
            text(
                "SELECT p.proname, p.prosrc, p.proconfig, p.prosecdef, p.provolatile, "
                "p.proisstrict, p.proleakproof, p.prokind, p.proparallel, p.pronargs, "
                "p.prorettype::regtype::text AS rettype, l.lanname, "
                "p.pronamespace::regnamespace::text AS schema, "
                "pg_get_userbyid(p.proowner) AS owner, p.proacl::text AS acl "
                "FROM pg_proc p JOIN pg_language l ON l.oid = p.prolang "
                "WHERE p.proname = ANY(:names)"
            ),
            {"names": list(names)},
        ).mappings()
        found = {str(row["proname"]): dict(row) for row in rows}
    assert sorted(found) == sorted(names), found.keys()
    return found


def test_guard_functions_run_with_the_trusted_search_path(migrated_db: str) -> None:
    pinned = _proc_rows((*FUNCTIONS_0030, DELETE_GUARD_0029))
    for name, row in pinned.items():
        assert row["proconfig"] == [TRUSTED_SEARCH_PATH], name
        assert row["prosecdef"] is False, name  # SECURITY INVOKER, never DEFINER
    for name, row in _proc_rows(UNTOUCHED_FUNCTIONS).items():
        assert row["proconfig"] is None, name
        assert row["prosecdef"] is False, name


_TABLE_REFERENCE = re.compile(r"\b(?:FROM|JOIN)\s+([A-Za-z_][\w.]*)")
_DISTINCT_FROM = re.compile(r"\bDISTINCT\s+FROM\b")


def _table_references(body: str) -> list[str]:
    """Relation names after FROM / JOIN (``IS DISTINCT FROM`` is an operator, not a relation)."""

    return _TABLE_REFERENCE.findall(_DISTINCT_FROM.sub("DISTINCT", body))


def test_0030_function_bodies_reference_only_schema_qualified_tables(migrated_db: str) -> None:
    bodies = {name: row["prosrc"] for name, row in _proc_rows(FUNCTIONS_0030).items()}
    references = {name: _table_references(body) for name, body in bodies.items()}
    for name, tables in references.items():
        assert all(table.startswith("public.") for table in tables), (name, tables)
    assert references["phase20_1_reconciliation_job_type_immutable"] == ["public.strategy_runs"]
    assert sorted(references["phase20_1_strategy_run_link_immutable"]) == [
        "public.order_events",
        "public.order_submission_attempts",
        "public.paper_orders",
    ]


def test_job_type_guard_literals_match_the_application_constants() -> None:
    source = MIGRATION_0030.read_text()
    assert RECONCILIATION_JOB_TYPE == "reconciliation"
    assert STANDALONE_TRIGGER_SOURCE == "job"
    assert f"OLD.job_type::text = '{RECONCILIATION_JOB_TYPE}'" in source
    assert f"NEW.job_type::text = '{RECONCILIATION_JOB_TYPE}'" in source
    assert f"sr.trigger_source::text = '{STANDALONE_TRIGGER_SOURCE}'" in source


def test_downgrade_restores_every_0029_function_exactly_and_reupgrade_rearms(
    migrated_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """At head the ONLY difference to a database that never ran 0030 is the delete guard's search
    path; a downgrade to 0029 restores every 0029 (and 0028) function row exactly; re-upgrading
    restores the head state and re-arms the guard."""

    compared = (*FUNCTIONS_0029, "order_submission_attempts_append_only")
    head = _proc_rows(compared)
    head_0030 = _proc_rows(FUNCTIONS_0030)

    with monkeypatch.context() as patch:
        with migrated_database(patch, "phase20_1_e1_at0029", revision=PREVIOUS_REVISION):
            assert _scalar("SELECT version_num FROM alembic_version") == PREVIOUS_REVISION
            never_ran_0030 = _proc_rows(compared)
            assert not _scalar(
                "SELECT count(*) FROM pg_proc WHERE proname = ANY(:n)", n=list(FUNCTIONS_0030)
            )
    _fresh_caches()
    assert _scalar("SELECT current_database()") == migrated_db

    for name, row in never_ran_0030.items():
        expected = dict(row)
        if name == DELETE_GUARD_0029:
            expected["proconfig"] = [TRUSTED_SEARCH_PATH]
        assert head[name] == expected, name

    command.downgrade(build_alembic_config(), PREVIOUS_REVISION)
    _fresh_caches()
    assert _scalar("SELECT version_num FROM alembic_version") == PREVIOUS_REVISION
    assert _proc_rows(compared) == never_ran_0030
    assert not _scalar(
        "SELECT count(*) FROM pg_proc WHERE proname = ANY(:n)", n=list(FUNCTIONS_0030)
    )

    command.upgrade(build_alembic_config(), "head")
    _fresh_caches()
    assert _proc_rows(compared) == head
    assert _proc_rows(FUNCTIONS_0030) == head_0030
    job_id = _start_reconciliation()
    _refused(_retype(job_id, DISGUISE), JOB_TYPE_REFUSAL)
