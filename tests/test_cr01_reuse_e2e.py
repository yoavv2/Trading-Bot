"""CR-01 required end-to-end regressions (a) and (b), user decision 2026-10-05 (plan 20.1-29).

"Drive the real paths, not direct assignments." The earlier SAF-01 test stopped at the send
authorization and never drove the Continue registration, which is why a re-parenting of the order
stayed green. These tests run the finished code (20.1-26 durable linkage, 20.1-27 migration 0029,
20.1-28 shared verdict) through the product entrypoints:

* (a) SAF-01 release -> clean standalone reconciliation -> Continue (``run_paper_continuation``)
  re-registers the earlier zero-attempt order (``retry_existing``), POSTs it once, keeps the order's
  origin run, never makes the original Job ``execution_path_unproven`` and progresses to the next
  intent; parametrized over ``pending_submission`` and ``submission_failed``;
* (b1) an M15 (OPS-07) retry of a FAILED Continue Job that itself registered the order and crashed
  before T1 creates no new uncertainty and re-registers once;
* (b2) an M15 retry of the failed START Job resolves per the 20.1-15 rule and never reaches
  registration;
* (b3) the Start-path reuse decision: after End, a clean M5, a fresh evaluation and a new start,
  the earlier proven-not-sent order is re-registered (``retry_existing``) and POSTed once;
* (b3-session) the same scenario with the new start entering through the SESSION entry point
  ``run_paper_session`` (D-15 session recovery gate, pre-lock recovery and reconciliation), with
  fake execution / broker clients.

What is INJECTED (and nothing else): the simulated worker crash before T1 (a held worker whose lock
connection is terminated, or a one-shot exception before T1) and the passage of time (a lease
expires). Everything after it is a product entrypoint: ``reclaim_lost_jobs`` (the Job becomes
FAILED / outcome_uncertain only that way), ``run_paper_continuation``, ``run_paper_order_submission``
(the start domain call), ``JobOrchestrationService.retry`` (M15), ``claim_next_job`` (the worker's
claim), ``end_operation`` (M12), ``sync_account_state`` (M4) and ``apply_job_transition`` (a Job
finishing). No test writes ``paper_orders``, ``order_events``, ``order_submission_attempts``,
``execution_operations`` or ``execution_operation_intents`` rows (migration 0029 forbids it anyway).
The clean standalone reconciliation (M5) is seeded with an EXPLICIT ``completed_at`` because the
reconciliation itself is not under test and every gate value below depends on its ordering against
Job completion times (``Timeline``); ``finish_jobs()`` and ``_recover_by_client_order_id`` are not
used because they rewrite every Job's status / ``completed_at``.

The CR-01 interim runbook prohibition is NOT lifted by this module.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from tests.support.basis_fixtures import seed_fresh_broker_snapshot
from tests.support.paper_eligibility import allow_paper_execution
from tests.support.paper_execution_seams import TEST_LEASE_OWNER, allow_direct_paper_execution
from tests.support.recovery_agreement import assert_recovery_consumers_agree
from tests.support.recovery_fixtures import seed_account_run
from tests.test_attribution_reconciliation import _broker_fill, _broker_order
from tests.test_paper_execution import (  # noqa: F401  (migrated_paper_db is the database fixture)
    FakeBrokerClient,
    migrated_paper_db,
)
from tests.test_paper_session_operations import (
    DEFAULT_BATCH,
    SESSION,
    STRATEGY,
    Gate,
    S1Broker,
    Worker,
    _continue_validate,
    _start,
    _validate,
    attempt_outcomes,
    continue_job,
    evaluation,
    intent_rows,
    manifest,
    operation_row,
    run_continue,
    seed_batch,
    terminate_lock_holder,
)
from tests.test_recovery_order_linkage import _accepted_registrations
from tests.test_recovery_predicate import LookupBroker

from trading_platform.api.app import create_app
from trading_platform.core import clock
from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.db.models import (
    AccountReconciliationRun,
    ExecutionOperationIntent,
    Job,
    JobEventType,
    JobStatus,
    OrderEvent,
    OrderLifecycleState,
    OrderSubmissionAttempt,
    PaperOrder,
    RiskEvent,
    StrategyRun,
    StrategyRunType,
)
from trading_platform.db.session import session_scope
from trading_platform.jobs.lifecycle import JobTransitionRequest, apply_job_transition
from trading_platform.jobs.queue import claim_next_job, reclaim_lost_jobs
from trading_platform.jobs.registry import JobSubmissionConflictError, build_default_registry
from trading_platform.orchestration.job_mutations import JobOrchestrationService
from trading_platform.services.alpaca import BrokerAccountSnapshot
from trading_platform.services.execution import submit_orders as submit_orders_module
from trading_platform.services.execution.operations import end_operation
from trading_platform.services.execution.submit_orders import (
    build_client_order_id,
    run_paper_session,
)
from trading_platform.services.execution.sync_orders import sync_account_state
from trading_platform.services.paper_account_checks import _check_a5
from trading_platform.services.recovery import (
    GateCode,
    UnresolvedReason,
    strategy_recovery_status,
)

PENDING = "pending_submission"
FAILED = "submission_failed"


@pytest.fixture(autouse=True)
def _seams(monkeypatch: pytest.MonkeyPatch) -> None:
    """The shared run-time seam (historical session, stubbed window / manifest answers)."""

    allow_paper_execution(monkeypatch)
    allow_direct_paper_execution(monkeypatch)


@pytest.fixture()
def http(migrated_paper_db: str, monkeypatch: pytest.MonkeyPatch):  # noqa: F811
    """R3 / operation reads over HTTP against the SAME throwaway database as the product calls."""

    monkeypatch.setenv("TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED", "true")
    clear_settings_cache()
    with TestClient(create_app()) as client:
        yield client


# ---------------------------------------------------------------------------
# Timeline and Job helpers (the only injected faults: a worker crash and the passage of time)
# ---------------------------------------------------------------------------


class Timeline:
    """Strictly increasing EXPLICIT instants, all later than any real-time write of the scenario.

    Every ordering the gate depends on (a Job completing before / after a reconciliation) is
    stated with these instants and asserted at its checkpoint; nothing relies on wall-clock luck.
    """

    def __init__(self) -> None:
        self._at = datetime.now(UTC) + timedelta(minutes=10)

    def next(self) -> datetime:
        at = self._at
        self._at += timedelta(minutes=10)
        return at


def _start_job(risk_run_id: uuid.UUID) -> uuid.UUID:
    """A RUNNING start Job holding a lease, with the REAL start payload (the M15 retry of it
    re-runs ``validate_payload`` on this payload)."""

    with session_scope(load_settings()) as session:
        job = Job(
            job_type="paper-session",
            payload={
                "strategy_id": STRATEGY,
                "as_of_session": SESSION.isoformat(),
                "risk_run_id": str(risk_run_id),
            },
            status=JobStatus.RUNNING,
            lease_owner=TEST_LEASE_OWNER,
            lease_expires_at=datetime.now(UTC) + timedelta(days=1),
        )
        session.add(job)
        session.flush()
        return job.id


def _crash_job(job_id: uuid.UUID, at: datetime) -> None:
    """The lease lapses (passage of time) and the product sweep reclaims the Job: FAILED with
    ``outcome_uncertain`` and ``completed_at == at``. The sweep must reclaim exactly this Job."""

    with session_scope(load_settings()) as session:
        session.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(lease_expires_at=datetime.now(UTC) - timedelta(minutes=1))
        )
    with session_scope(load_settings()) as session:
        reclaimed = reclaim_lost_jobs(session, now=at)
    assert reclaimed == [job_id]
    with session_scope(load_settings()) as session:
        job = session.get(Job, job_id)
        assert job is not None
        assert job.status is JobStatus.FAILED and job.outcome_uncertain is True
        assert job.completed_at == at


def _finish_job(job_id: uuid.UUID, at: datetime) -> None:
    """A Job finishes normally at an explicit instant, through the Job lifecycle (not a direct
    status write and not ``finish_jobs()``)."""

    with session_scope(load_settings()) as session:
        apply_job_transition(
            session,
            job_id=job_id,
            request=JobTransitionRequest(event_type=JobEventType.SUCCEEDED, event_at=at),
        )
    with session_scope(load_settings()) as session:
        job = session.get(Job, job_id)
        assert job is not None
        assert job.status is JobStatus.SUCCEEDED and job.completed_at == at


def _completed_at(job_id: uuid.UUID) -> datetime:
    with session_scope(load_settings()) as session:
        job = session.get(Job, job_id)
        assert job is not None and job.completed_at is not None
        return job.completed_at


def _clean_reconciliation(at: datetime) -> None:
    """M5: a clean succeeded standalone account reconciliation completed at ``at``."""

    with session_scope(load_settings()) as session:
        seed_account_run(session, completed_at=at)


def _run_of(job_id: uuid.UUID) -> uuid.UUID:
    with session_scope(load_settings()) as session:
        return session.execute(
            select(StrategyRun.id).where(
                StrategyRun.job_id == job_id,
                StrategyRun.run_type == StrategyRunType.PAPER_EXECUTION,
            )
        ).scalar_one()


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------


def _a5_passed() -> bool:
    with session_scope(load_settings()) as session:
        return _check_a5(session, now=clock.now_utc()).passed


def _status() -> Any:
    with session_scope(load_settings()) as session:
        return strategy_recovery_status(session, STRATEGY, now=clock.now_utc())


def _unproven(job_id: uuid.UUID) -> bool:
    return any(
        i.job_id == job_id
        and i.intent_id is None
        and i.unresolved_reason is UnresolvedReason.EXECUTION_PATH_UNPROVEN
        for i in _status().intents
    )


def _r3(http: TestClient, job_id: uuid.UUID) -> dict[str, Any]:
    response = http.get(f"/api/v1/jobs/{job_id}/recovery")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _r3_entries(http: TestClient, job_id: uuid.UUID) -> list[tuple[str | None, str, bool]]:
    return [
        (item["intent_id"], item["classification"], item["blocking"])
        for item in _r3(http, job_id)["intents"]
    ]


def _evidence(order_id: uuid.UUID) -> dict[str, Any]:
    """Everything a re-registration could add or erase for one order, plus the POST-relevant
    attempt rows."""

    with session_scope(load_settings()) as session:
        order = session.get(PaperOrder, order_id)
        assert order is not None
        events = session.execute(
            select(func.count())
            .select_from(OrderEvent)
            .where(OrderEvent.paper_order_id == order_id)
        ).scalar_one()
        attempts = session.execute(
            select(OrderSubmissionAttempt.attempt_number, OrderSubmissionAttempt.outcome_class)
            .where(OrderSubmissionAttempt.paper_order_id == order_id)
            .order_by(OrderSubmissionAttempt.attempt_number)
        ).all()
        runs = session.execute(
            select(func.count())
            .select_from(StrategyRun)
            .where(StrategyRun.run_type == StrategyRunType.PAPER_EXECUTION)
        ).scalar_one()
        return {
            "order_events": int(events),
            "attempts": [(number, str(outcome)) for number, outcome in attempts],
            "paper_execution_runs": int(runs),
            "origin_run": order.strategy_run_id,
            "status": order.status,
        }


def _order(order_id: uuid.UUID) -> PaperOrder:
    with session_scope(load_settings()) as session:
        order = session.get(PaperOrder, order_id)
        assert order is not None
        session.expunge(order)
        return order


# ---------------------------------------------------------------------------
# The SAF-01 shape: crash before T1 -> an operation-bound registered order with zero attempts
# ---------------------------------------------------------------------------


@dataclass
class World:
    status: str
    risk_run: uuid.UUID
    operation_id: uuid.UUID
    j1: uuid.UUID
    r1: uuid.UUID
    order_id: uuid.UUID
    cid1: str
    cid2: str
    broker: S1Broker
    timeline: Timeline
    j1_completed_at: datetime
    history: list[GateCode | None] = field(default_factory=list)


def _saf01_shape(status: str, monkeypatch: pytest.MonkeyPatch, timeline: Timeline) -> World:
    """Seed a two-intent batch, start the session as Job J1 and crash it before T1 of intent 1.

    ``pending_submission``: J1's worker is held in a wrapper around ``_send_authorized`` (before it
    calls ``authorize_send``), its lock connection is terminated, its lease lapses and the sweep
    fails it ``outcome_uncertain``; the worker is then released and its T1 refuses (LEASE_LOST).
    ``submission_failed``: a one-shot non-refusal exception before T1 moves the order to
    ``submission_failed``; J1's lease then lapses and the sweep fails it.
    """

    risk_run, _events = seed_batch(DEFAULT_BATCH[:2], manifest=manifest())
    j1 = _start_job(risk_run)
    broker = S1Broker()
    armed = {"on": True}
    if status == PENDING:
        gate = Gate()
        real_send = submit_orders_module._send_authorized

        def held_send(ctx: Any, **kwargs: Any) -> Any:
            if armed["on"]:
                armed["on"] = False
                gate.hold()  # J1's worker is suspended BEFORE its transaction T1
            return real_send(ctx, **kwargs)

        monkeypatch.setattr(submit_orders_module, "_send_authorized", held_send)
        worker = Worker(lambda: _start(broker.service(), risk_run_id=risk_run, job_id=j1)).start()
        gate.wait_arrived()
        terminate_lock_holder()  # the worker "crashes": its session advisory lock is gone
        crashed_at = timeline.next()
        _crash_job(j1, crashed_at)
        gate.release.set()
        worker.join()
        assert worker.error is not None  # T1 refused (lease lost); the executor ended
        monkeypatch.setattr(submit_orders_module, "_send_authorized", real_send)
    else:
        real_authorize = submit_orders_module.authorize_send

        def failing_authorize(*args: Any, **kwargs: Any) -> Any:
            if armed["on"]:
                armed["on"] = False
                raise RuntimeError("worker failed before its send authorization")
            return real_authorize(*args, **kwargs)

        monkeypatch.setattr(submit_orders_module, "authorize_send", failing_authorize)
        with pytest.raises(RuntimeError, match="before its send authorization"):
            _start(broker.service(), risk_run_id=risk_run, job_id=j1)
        monkeypatch.setattr(submit_orders_module, "authorize_send", real_authorize)
        crashed_at = timeline.next()
        _crash_job(j1, crashed_at)

    # precondition, asserted explicitly (never seeded)
    assert broker.received == {}  # no POST has ever left the process
    operation = operation_row()
    (intent1, cid1), (_intent2, cid2) = intent_rows()
    with session_scope(load_settings()) as session:
        runs = (
            session.execute(
                select(StrategyRun.id).where(
                    StrategyRun.job_id == j1,
                    StrategyRun.run_type == StrategyRunType.PAPER_EXECUTION,
                )
            )
            .scalars()
            .all()
        )
        assert len(runs) == 1
        r1 = runs[0]
        order = session.execute(
            select(PaperOrder).where(PaperOrder.client_order_id == cid1)
        ).scalar_one()
        order_id = order.id
        assert order.strategy_run_id == r1
        assert order.status.value == status
        assert order.broker_order_id is None
        bound = session.execute(
            select(ExecutionOperationIntent.paper_order_id).where(
                ExecutionOperationIntent.id == intent1
            )
        ).scalar_one()
        assert bound == order_id
        assert _accepted_registrations(session, order_id) == {("intent_registered", r1)}
    assert attempt_outcomes(cid1) == []
    return World(
        status=status,
        risk_run=risk_run,
        operation_id=operation.id,
        j1=j1,
        r1=r1,
        order_id=order_id,
        cid1=cid1,
        cid2=cid2,
        broker=broker,
        timeline=timeline,
        j1_completed_at=crashed_at,
    )


def _agree(
    world: World,
    *,
    linked: list[uuid.UUID],
    flagged: list[uuid.UUID],
    operation_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """The arguments of the shared consumer-agreement assertion at one checkpoint."""

    return {
        "strategy_id": STRATEGY,
        "linked_job_ids": linked,
        "registering_flagged_job_ids": flagged,
        "operation_id": operation_id or world.operation_id,
    }


def _record(
    http: TestClient,
    world: World,
    gate: GateCode | None,
    *,
    flagged: list[uuid.UUID],
    expect: GateCode | None,
) -> None:
    """After the agreement assertion of a checkpoint: the gate is the expected one (A5 passes
    exactly when it is None), the registering Jobs are never execution_path_unproven and own no
    Job-level entry, and the gate history is extended."""

    world.history.append(gate)
    assert gate is expect, (gate, expect, world.history)
    for job_id in flagged:
        assert not _unproven(job_id), f"Job {job_id} reads execution_path_unproven"
        for item in _r3(http, job_id)["intents"]:
            assert item["intent_id"] is not None, "a Job-level entry for a registering Job"
    assert _a5_passed() is (expect is None)


def _filled_sync(order_id: uuid.UUID) -> LookupBroker:
    """M4: ``sync_account_state`` against a broker that reports the order filled (read-only; the
    broker records zero POST)."""

    order = _order(order_id)
    assert order.broker_order_id is not None
    snapshot = _broker_order(
        broker_order_id=order.broker_order_id,
        client_order_id=order.client_order_id,
        symbol="AAPL",
        quantity=str(int(order.quantity)),
        broker_status="filled",
        created_at=order.created_at + timedelta(minutes=1),
    )
    fill = _broker_fill(
        fill_id=f"fill-{uuid.uuid4().hex[:8]}",
        order_id=order.broker_order_id,
        symbol="AAPL",
        quantity=str(int(order.quantity)),
    )
    broker = LookupBroker(orders=[snapshot], fills=[fill], lookup={order.client_order_id: snapshot})
    sync_account_state(settings=load_settings(), broker_client=broker)
    assert broker.post_count == 0
    synced = _order(order_id)
    assert synced.status == OrderLifecycleState.FILLED and synced.last_synced_at is not None
    return broker


# ---------------------------------------------------------------------------
# Task 1: regression (a)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status", [PENDING, FAILED], ids=["pending_submission", "submission_failed"]
)
def test_a_saf01_release_survives_continue(
    http: TestClient, monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    """SAF-01 release -> clean standalone reconciliation -> Continue -> progression, on the product
    Continue path (``run_paper_continuation``)."""

    timeline = Timeline()
    world = _saf01_shape(status, monkeypatch, timeline)
    j1, order_id, cid1, cid2, op = (
        world.j1,
        world.order_id,
        world.cid1,
        world.cid2,
        world.operation_id,
    )
    broker = world.broker

    # C1: after the crash. J1 is reclaimed; no reconciliation exists yet.
    with session_scope(load_settings()) as session:
        assert (
            session.execute(select(func.count()).select_from(AccountReconciliationRun)).scalar_one()
            == 0
        )
    gate = assert_recovery_consumers_agree(http, **_agree(world, linked=[j1], flagged=[j1]))
    _record(http, world, gate, flagged=[j1], expect=GateCode.RECONCILIATION_REQUIRED)
    assert _r3_entries(http, j1) == [(str(order_id), "not_sent", False)]

    # C2: a clean standalone reconciliation completed AFTER J1 releases it (SAF-01)
    recon_a = timeline.next()
    assert recon_a > world.j1_completed_at
    _clean_reconciliation(recon_a)
    gate = assert_recovery_consumers_agree(http, **_agree(world, linked=[j1], flagged=[j1]))
    _record(http, world, gate, flagged=[j1], expect=None)
    assert _r3(http, j1)["resolved"] is True
    assert _r3_entries(http, j1) == [(str(order_id), "not_sent", False)]

    # C3: Continue through the product entrypoint (a new Job J2 on its own run R2)
    assert _continue_validate(op)["mode"] == "continue"  # the submit-time Continue gate passes
    j2 = continue_job(op)
    report = run_continue(broker.service(), operation_id=op, job_id=j2)
    r2 = _run_of(j2)
    assert r2 != world.r1
    submitted = report.result_summary["submitted_orders"]
    assert [o["client_order_id"] for o in submitted] == [cid1]
    assert submitted[0]["intent_decision"]["action"] == "retry_existing"
    assert broker.received == {cid1: 1}  # one POST, never more
    assert attempt_outcomes(cid1) == [(1, "accepted")]
    with session_scope(load_settings()) as session:
        attempt = session.execute(
            select(OrderSubmissionAttempt).where(OrderSubmissionAttempt.paper_order_id == order_id)
        ).scalar_one()
        assert attempt.executor_job_id == j2
        assert attempt.strategy_run_id == r2
        assert _accepted_registrations(session, order_id) == {
            ("intent_registered", world.r1),
            ("retry_requested", r2),
        }
    assert _order(order_id).strategy_run_id == world.r1  # the ORIGIN run is kept
    assert (report.operation_state, report.operation_reason) == (
        "paused",
        "working_order_commitments_unaccounted",
    )
    # C3 checkpoint, J2 still RUNNING (no completed_at, so not yet a broker effect): the original
    # Job keeps its order and the gate stays released
    assert _job_row(j2).completed_at is None and _job_row(j2).status is JobStatus.RUNNING
    gate = assert_recovery_consumers_agree(http, **_agree(world, linked=[j1, j2], flagged=[j1, j2]))
    _record(http, world, gate, flagged=[j1, j2], expect=None)
    assert _r3_entries(http, j1) == [(str(order_id), "found_verified", False)]

    # C4: right after J2 (finished at an explicit instant later than the C2 reconciliation)
    finished_j2 = timeline.next()
    assert finished_j2 > recon_a
    _finish_job(j2, finished_j2)
    gate = assert_recovery_consumers_agree(http, **_agree(world, linked=[j1, j2], flagged=[j1, j2]))
    _record(http, world, gate, flagged=[j1, j2], expect=GateCode.RECONCILIATION_REQUIRED)
    for job_id in (j1, j2):  # no Job-level execution_path_unproven in any consumer
        assert not _unproven(job_id)
        assert all(i.intent_id is not None for i in _status().intents if i.job_id == job_id)
    # J1 (flagged) still lists the order, now broker-evidenced (accepted) and non-blocking; J2
    # finished normally (not outcome_uncertain), so it owns no recovery entry at all
    assert _r3_entries(http, j1) == [(str(order_id), "found_verified", False)]
    assert _r3_entries(http, j2) == []

    # C5: the order fills at the broker; M4 sync; a fresh clean M5 after J2
    assert _completed_at(j1) == world.j1_completed_at  # J1 keeps its reclaimed timestamp
    _filled_sync(order_id)
    recon_b = timeline.next()
    assert recon_b > _completed_at(j2)
    _clean_reconciliation(recon_b)
    assert _completed_at(j1) == world.j1_completed_at and _completed_at(j2) == finished_j2
    gate = assert_recovery_consumers_agree(http, **_agree(world, linked=[j1, j2], flagged=[j1, j2]))
    _record(http, world, gate, flagged=[j1, j2], expect=None)
    assert _r3(http, j1)["resolved"] is True and _r3(http, j2)["resolved"] is True

    # C6: progression: the next Continue sends intent 2 exactly once under its original identity
    assert _continue_validate(op)["mode"] == "continue"
    j3 = continue_job(op)
    report2 = run_continue(broker.service(), operation_id=op, job_id=j3)
    assert broker.received == {cid1: 1, cid2: 1}
    assert [o["client_order_id"] for o in report2.result_summary["submitted_orders"]] == [cid2]
    assert (
        report2.result_summary["submitted_orders"][0]["intent_decision"]["action"] == "create_new"
    )
    assert (report2.operation_state, report2.operation_reason) == (
        "paused",
        "working_order_commitments_unaccounted",
    )
    world.history.append(
        assert_recovery_consumers_agree(
            http,
            strategy_id=STRATEGY,
            linked_job_ids=[j1, j2, j3],
            registering_flagged_job_ids=[j1, j2],
            operation_id=op,
        )
    )
    assert GateCode.OUTCOME_UNRESOLVED not in world.history, world.history
    assert world.history[:5] == [
        GateCode.RECONCILIATION_REQUIRED,  # C1
        None,  # C2
        None,  # C3 (J2 still running)
        GateCode.RECONCILIATION_REQUIRED,  # C4
        None,  # C5
    ]


# ---------------------------------------------------------------------------
# Task 2: regressions (b1), (b2), (b3)
# ---------------------------------------------------------------------------


def _orchestration() -> JobOrchestrationService:
    settings = load_settings()
    return JobOrchestrationService(settings, build_default_registry(settings))


def _hold_before_t1(monkeypatch: pytest.MonkeyPatch) -> Gate:
    """One-shot: the NEXT executor reaching ``_send_authorized`` is suspended before its T1."""

    gate = Gate()
    armed = {"on": True}
    real_send = submit_orders_module._send_authorized

    def held_send(ctx: Any, **kwargs: Any) -> Any:
        if armed["on"]:
            armed["on"] = False
            gate.hold()
        return real_send(ctx, **kwargs)

    monkeypatch.setattr(submit_orders_module, "_send_authorized", held_send)
    return gate


def _job_row(job_id: uuid.UUID) -> Job:
    with session_scope(load_settings()) as session:
        job = session.get(Job, job_id)
        assert job is not None
        session.expunge(job)
        return job


def test_b1_retry_of_a_failed_continue_job_creates_no_new_uncertainty(
    http: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(b1) The Continue Job J2 re-registers the order and crashes before T1 (reclaimed
    outcome_uncertain). Neither J1 nor J2 becomes execution_path_unproven; the M15 retry J3 of J2
    re-registers once (``retry_existing``) and POSTs exactly once."""

    timeline = Timeline()
    world = _saf01_shape(PENDING, monkeypatch, timeline)
    j1, order_id, cid1, op, broker = (
        world.j1,
        world.order_id,
        world.cid1,
        world.operation_id,
        world.broker,
    )
    recon_a = timeline.next()
    assert recon_a > world.j1_completed_at
    _clean_reconciliation(recon_a)

    # J2: a Continue Job whose registration commits, then crashes before its T1
    assert _continue_validate(op)["mode"] == "continue"
    j2 = continue_job(op)
    gate = _hold_before_t1(monkeypatch)
    worker = Worker(lambda: run_continue(broker.service(), operation_id=op, job_id=j2)).start()
    gate.wait_arrived()
    r2 = _run_of(j2)
    with session_scope(load_settings()) as session:  # the registration committed, nothing sent
        assert _accepted_registrations(session, order_id) == {
            ("intent_registered", world.r1),
            ("retry_requested", r2),
        }
    assert attempt_outcomes(cid1) == [] and broker.received == {}
    terminate_lock_holder()
    crashed_j2 = timeline.next()
    assert crashed_j2 > recon_a
    _crash_job(j2, crashed_j2)
    gate.release.set()
    worker.join()
    assert worker.error is not None  # T1 refused (lease lost)
    assert attempt_outcomes(cid1) == [] and broker.received == {}
    assert _order(order_id).strategy_run_id == world.r1

    # no new uncertainty: both registering Jobs list the order (not_sent) and are never unproven
    gate_after_crash = assert_recovery_consumers_agree(
        http, **_agree(world, linked=[j1, j2], flagged=[j1, j2])
    )
    _record(
        http, world, gate_after_crash, flagged=[j1, j2], expect=GateCode.RECONCILIATION_REQUIRED
    )
    for job_id in (j1, j2):
        assert not _unproven(job_id)
        assert _r3_entries(http, job_id) == [(str(order_id), "not_sent", False)]
    # a clean M5 after J2 releases it again
    recon_b = timeline.next()
    assert recon_b > crashed_j2
    _clean_reconciliation(recon_b)
    gate_released = assert_recovery_consumers_agree(
        http, **_agree(world, linked=[j1, j2], flagged=[j1, j2])
    )
    _record(http, world, gate_released, flagged=[j1, j2], expect=None)

    # M15 (OPS-07): retry the failed Continue Job
    result = _orchestration().retry(job_id=j2, idempotency_key="cr01-b1-retry-j2")
    j3 = uuid.UUID(result.reference.job_id)
    retried = _job_row(j3)
    assert retried.retry_of_job_id == j2 and retried.status is JobStatus.QUEUED
    assert retried.payload == {"mode": "continue", "operation_id": str(op)}
    assert not _unproven(j1) and not _unproven(j2)  # the retry alone adds no uncertainty
    with session_scope(load_settings()) as session:  # the worker's product claim
        claimed = claim_next_job(session, worker_id="retry-worker", lease_seconds=86400)
    assert claimed == j3
    report = run_continue(broker.service(), operation_id=op, job_id=j3)
    r3 = _run_of(j3)
    submitted = report.result_summary["submitted_orders"]
    assert [o["client_order_id"] for o in submitted] == [cid1]
    assert submitted[0]["intent_decision"]["action"] == "retry_existing"
    assert broker.received == {cid1: 1}  # exactly one POST
    assert attempt_outcomes(cid1) == [(1, "accepted")]
    with session_scope(load_settings()) as session:
        attempt = session.execute(
            select(OrderSubmissionAttempt).where(OrderSubmissionAttempt.paper_order_id == order_id)
        ).scalar_one()
        assert attempt.executor_job_id == j3 and attempt.strategy_run_id == r3
        assert _accepted_registrations(session, order_id) == {
            ("intent_registered", world.r1),
            ("retry_requested", r2),
            ("retry_requested", r3),
        }
    assert _order(order_id).strategy_run_id == world.r1
    # checkpoint right after the retried Continue, J3 still RUNNING (not yet an effect)
    assert _job_row(j3).completed_at is None and _job_row(j3).status is JobStatus.RUNNING
    gate_reused = assert_recovery_consumers_agree(
        http, **_agree(world, linked=[j1, j2, j3], flagged=[j1, j2])
    )
    _record(http, world, gate_reused, flagged=[j1, j2], expect=None)

    # settle exactly as C5 of regression (a)
    finished_j3 = timeline.next()
    assert finished_j3 > recon_b
    _finish_job(j3, finished_j3)
    gate_after_retry = assert_recovery_consumers_agree(
        http, **_agree(world, linked=[j1, j2, j3], flagged=[j1, j2])
    )
    _record(
        http, world, gate_after_retry, flagged=[j1, j2], expect=GateCode.RECONCILIATION_REQUIRED
    )
    _filled_sync(order_id)
    recon_c = timeline.next()
    assert recon_c > _completed_at(j3)
    _clean_reconciliation(recon_c)
    assert _completed_at(j1) == world.j1_completed_at and _completed_at(j2) == crashed_j2
    gate_settled = assert_recovery_consumers_agree(
        http, **_agree(world, linked=[j1, j2, j3], flagged=[j1, j2])
    )
    _record(http, world, gate_settled, flagged=[j1, j2], expect=None)
    assert GateCode.OUTCOME_UNRESOLVED not in world.history, world.history


def test_b2_retry_of_the_failed_start_job_never_reaches_registration(
    http: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(b2) M15 retry of the failed START Job J1 resolves per the 20.1-15 rule: ``operation_open``
    while the operation is open, ``risk_run_already_operated`` once it was ended. It never reaches
    registration: no order_events row, no attempt row, no run, no POST is added."""

    timeline = Timeline()
    world = _saf01_shape(PENDING, monkeypatch, timeline)
    j1, order_id, op, broker = world.j1, world.order_id, world.operation_id, world.broker
    _clean_reconciliation(timeline.next())
    before = _evidence(order_id)
    assert before["attempts"] == [] and broker.received == {}

    with pytest.raises(JobSubmissionConflictError) as open_error:
        _orchestration().retry(job_id=j1, idempotency_key="cr01-b2-open")
    assert open_error.value.code == "operation_open"
    assert open_error.value.detail["operation_id"] == str(op)
    assert _evidence(order_id) == before and broker.received == {}

    with session_scope(load_settings()) as session:  # End through the product service (M12)
        end_operation(session, op, operator_reason="cr01 b2 end", actor="pytest")
    with pytest.raises(JobSubmissionConflictError) as ended_error:
        _orchestration().retry(job_id=j1, idempotency_key="cr01-b2-ended")
    assert ended_error.value.code == "risk_run_already_operated"
    assert _evidence(order_id) == before and broker.received == {}
    with session_scope(load_settings()) as session:  # no retry Job was ever created
        retries = session.execute(
            select(func.count()).select_from(Job).where(Job.retry_of_job_id == j1)
        ).scalar_one()
    assert retries == 0
    assert _order(order_id).status.value == PENDING


@pytest.mark.parametrize(
    "status", [PENDING, FAILED], ids=["pending_submission", "submission_failed"]
)
def test_b3_new_start_after_end_reuses_the_proven_not_sent_order_once(
    http: TestClient, monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    """(b3) After End, a clean M5, a fresh evaluation of the SAME session with the SAME quantity
    and a new start, the new operation re-registers the earlier proven-not-sent order
    (``retry_existing``, same client_order_id) and POSTs it once. This is the evidence that a new
    session is not a CR-01 path once the fix lands; the runbook prohibition is NOT lifted here."""

    timeline = Timeline()
    world = _saf01_shape(status, monkeypatch, timeline)
    j1, order_id, cid1, op, broker = (
        world.j1,
        world.order_id,
        world.cid1,
        world.operation_id,
        world.broker,
    )
    with session_scope(load_settings()) as session:  # End (M12): the unsent intents are cancelled
        end_operation(session, op, operator_reason="cr01 b3 end", actor="pytest")
        dispositions = (
            session.execute(
                select(ExecutionOperationIntent.disposition).order_by(
                    ExecutionOperationIntent.sequence
                )
            )
            .scalars()
            .all()
        )
    assert dispositions == ["cancelled_unsent", "cancelled_unsent"]
    assert _order(order_id).status.value == status and attempt_outcomes(cid1) == []

    recon_a = timeline.next()
    assert recon_a > world.j1_completed_at
    _clean_reconciliation(recon_a)
    gate = assert_recovery_consumers_agree(http, **_agree(world, linked=[j1], flagged=[j1]))
    _record(http, world, gate, flagged=[j1], expect=None)

    # M8: a fresh evaluation of the same session whose AAPL candidate has the same quantity
    new_run = evaluation(DEFAULT_BATCH[:1], as_of="2024-01-08", base=timeline.next())
    assert new_run != world.risk_run
    with session_scope(load_settings()) as session:
        quantity = session.execute(
            select(RiskEvent.proposed_quantity).where(RiskEvent.strategy_run_id == new_run)
        ).scalar_one()
    assert quantity == _order(order_id).quantity
    derived = build_client_order_id(
        prefix=load_settings().execution.client_order_id_prefix,
        strategy_id=STRATEGY,
        session_date=SESSION,
        symbol="AAPL",
        side="buy",
        quantity=Decimal(quantity),
    )
    assert derived == cid1  # otherwise this would silently exercise create_new_version
    assert _validate(new_run)["risk_run_id"] == str(new_run)  # the submit-time start gate passes
    gate = assert_recovery_consumers_agree(http, **_agree(world, linked=[j1], flagged=[j1]))
    _record(http, world, gate, flagged=[j1], expect=None)

    # M10: the new start, Job J4, through the start domain call
    j4 = _start_job(new_run)
    report = _start(broker.service(), risk_run_id=new_run, job_id=j4)
    r4 = _run_of(j4)
    new_operation = uuid.UUID(report.result_summary["operation"]["id"])
    assert new_operation != op
    submitted = report.result_summary["submitted_orders"]
    assert [o["client_order_id"] for o in submitted] == [cid1]
    assert submitted[0]["intent_decision"]["action"] == "retry_existing"
    assert broker.received == {cid1: 1}  # POST once
    assert attempt_outcomes(cid1) == [(1, "accepted")]
    with session_scope(load_settings()) as session:
        attempt = session.execute(
            select(OrderSubmissionAttempt).where(OrderSubmissionAttempt.paper_order_id == order_id)
        ).scalar_one()
        assert attempt.executor_job_id == j4 and attempt.strategy_run_id == r4
        assert _accepted_registrations(session, order_id) == {
            ("intent_registered", world.r1),
            ("retry_requested", r4),
        }
    assert _order(order_id).strategy_run_id == world.r1  # the origin run is kept
    assert not _unproven(j1)
    # checkpoint right after the reuse, J4 still RUNNING (not yet an effect)
    assert _job_row(j4).completed_at is None and _job_row(j4).status is JobStatus.RUNNING
    gate = assert_recovery_consumers_agree(
        http, **_agree(world, linked=[j1, j4], flagged=[j1, j4], operation_id=new_operation)
    )
    _record(http, world, gate, flagged=[j1, j4], expect=None)

    finished_j4 = timeline.next()
    _finish_job(j4, finished_j4)
    gate = assert_recovery_consumers_agree(
        http, **_agree(world, linked=[j1, j4], flagged=[j1, j4], operation_id=new_operation)
    )
    _record(http, world, gate, flagged=[j1, j4], expect=GateCode.RECONCILIATION_REQUIRED)

    # settle exactly as C5 of regression (a)
    _filled_sync(order_id)
    recon_b = timeline.next()
    assert recon_b > finished_j4
    _clean_reconciliation(recon_b)
    gate = assert_recovery_consumers_agree(
        http, **_agree(world, linked=[j1, j4], flagged=[j1, j4], operation_id=new_operation)
    )
    _record(http, world, gate, flagged=[j1, j4], expect=None)
    assert GateCode.OUTCOME_UNRESOLVED not in world.history, world.history


def _clean_fake_broker() -> FakeBrokerClient:
    """The read-side broker client of ``run_paper_session`` (recovery / reconciliation reads only;
    nothing is ever POSTed through it)."""

    return FakeBrokerClient(
        orders=[],
        fills=[],
        positions=[],
        account=BrokerAccountSnapshot(
            cash=Decimal("100000.000000"),
            buying_power=Decimal("100000.000000"),
            equity=Decimal("100000.000000"),
            long_market_value=Decimal("0"),
            short_market_value=Decimal("0"),
            raw_payload={"equity": "100000.000000"},
        ),
    )


_SESSION_PENDING_FINDING = (
    "PRODUCT FINDING (20.1-29 follow-up): the session entry point run_paper_session blocks the "
    "Start of a SAF-01-released proven-not-sent pending_submission order. Its pre-lock "
    "reconciliation (reconcile_paper_execution -> matcher._is_local_order_active) treats a "
    "pending_submission order whose submission_attempt_count != 0 as active, and registration "
    "(submit_orders, before T1) already incremented that counter to 1 although no "
    "order_submission_attempts row exists and nothing was ever POSTed; the broker therefore (truly) "
    "does not report the order and the finding MISSING_BROKER (blocks_execution) yields action "
    "blocked_reconciliation: no re-POST, no execution run. The D-15 session gate itself is "
    "evaluated and returns None. PaperSessionJobHandler calls run_paper_session, so a real worker "
    "Start is blocked (fail-closed) while the direct start path (b3) reuses the order."
)


@pytest.mark.parametrize(
    "status",
    [
        pytest.param(
            PENDING,
            id="pending_submission",
            marks=pytest.mark.xfail(
                strict=True, raises=AssertionError, reason=_SESSION_PENDING_FINDING
            ),
        ),
        pytest.param(FAILED, id="submission_failed"),
    ],
)
def test_b3_new_start_after_end_through_run_paper_session(
    http: TestClient, monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    """(b3, session entry point) The scenario of (b3), but the new start goes through
    ``run_paper_session``: its D-15 session-level recovery gate and its pre-lock recovery /
    reconciliation (fake execution service and fake read-side broker client) must neither block a
    SAF-01-released proven-not-sent order nor cause a second POST."""

    timeline = Timeline()
    world = _saf01_shape(status, monkeypatch, timeline)
    j1, order_id, cid1, op, broker = (
        world.j1,
        world.order_id,
        world.cid1,
        world.operation_id,
        world.broker,
    )
    with session_scope(load_settings()) as session:  # End (M12)
        end_operation(session, op, operator_reason="cr01 b3 session end", actor="pytest")
    assert _order(order_id).status.value == status and attempt_outcomes(cid1) == []

    recon_a = timeline.next()
    assert recon_a > world.j1_completed_at
    _clean_reconciliation(recon_a)
    gate = assert_recovery_consumers_agree(http, **_agree(world, linked=[j1], flagged=[j1]))
    _record(http, world, gate, flagged=[j1], expect=None)

    # execution sizes only on a fresh broker-observed snapshot (no configured-cash fallback)
    with session_scope(load_settings()) as session:
        seed_fresh_broker_snapshot(session)
    new_run = evaluation(DEFAULT_BATCH[:1], as_of="2024-01-08", base=timeline.next())
    assert new_run != world.risk_run
    with session_scope(load_settings()) as session:
        quantity = session.execute(
            select(RiskEvent.proposed_quantity).where(RiskEvent.strategy_run_id == new_run)
        ).scalar_one()
    assert quantity == _order(order_id).quantity
    derived = build_client_order_id(
        prefix=load_settings().execution.client_order_id_prefix,
        strategy_id=STRATEGY,
        session_date=SESSION,
        symbol="AAPL",
        side="buy",
        quantity=Decimal(quantity),
    )
    assert derived == cid1
    assert _validate(new_run)["risk_run_id"] == str(new_run)
    gate = assert_recovery_consumers_agree(http, **_agree(world, linked=[j1], flagged=[j1]))
    _record(http, world, gate, flagged=[j1], expect=None)

    # M10 through the SESSION entry point. The D-15 gate is observed (not replaced): the spy
    # records the real answer and passes it through.
    gate_answers: list[str | None] = []
    real_gate = submit_orders_module._recovery_gate_code

    def spying_gate(settings: Any, strategy_id: str) -> str | None:
        answer = real_gate(settings, strategy_id)
        gate_answers.append(answer)
        return answer

    monkeypatch.setattr(submit_orders_module, "_recovery_gate_code", spying_gate)
    j4 = _start_job(new_run)
    report = run_paper_session(
        STRATEGY,
        as_of_session=SESSION,
        risk_run_id=str(new_run),
        trigger_source="pytest",
        settings=load_settings(),
        execution_service=broker.service(),
        broker_client=_clean_fake_broker(),
        job_id=j4,
    )
    # the D-15 session gate was evaluated exactly once, answered None, and did not block
    assert gate_answers == [None], gate_answers
    assert report.action != "blocked_outcome_unresolved", report.action
    # the run went through the session path (pre-lock recovery + reconciliation ran for this Job)
    # and reached submission: the session-level reconciliation must not block the released order
    summary = report.result_summary
    reconciliation = (
        summary.get("reconciliation") or summary.get("session_preflight", {}).get("reconciliation")
    ) or {}
    assert report.action == "submitted_missing_orders", (
        report.action,
        broker.received,
        [(f["event_type"], f["message"]) for f in reconciliation.get("findings", [])],
    )
    r4 = _run_of(j4)
    assert report.reconciliation_run_id is not None
    assert report.execution_run_id == str(r4)
    with session_scope(load_settings()) as session:
        reconciliation_run = session.get(StrategyRun, uuid.UUID(str(report.reconciliation_run_id)))
        assert reconciliation_run is not None and reconciliation_run.job_id == j4

    new_operation = uuid.UUID(report.result_summary["operation"]["id"])
    assert new_operation != op
    submitted = report.result_summary["submitted_orders"]
    assert [o["client_order_id"] for o in submitted] == [cid1]
    assert submitted[0]["intent_decision"]["action"] == "retry_existing"
    assert broker.received == {cid1: 1}  # POST once
    assert attempt_outcomes(cid1) == [(1, "accepted")]
    with session_scope(load_settings()) as session:
        attempt = session.execute(
            select(OrderSubmissionAttempt).where(OrderSubmissionAttempt.paper_order_id == order_id)
        ).scalar_one()
        assert attempt.executor_job_id == j4 and attempt.strategy_run_id == r4
        assert _accepted_registrations(session, order_id) == {
            ("intent_registered", world.r1),
            ("retry_requested", r4),
        }
    assert _order(order_id).strategy_run_id == world.r1  # the origin run is kept
    assert not _unproven(j1)
    assert _job_row(j4).completed_at is None and _job_row(j4).status is JobStatus.RUNNING
    gate = assert_recovery_consumers_agree(
        http, **_agree(world, linked=[j1, j4], flagged=[j1, j4], operation_id=new_operation)
    )
    _record(http, world, gate, flagged=[j1, j4], expect=None)

    finished_j4 = timeline.next()
    _finish_job(j4, finished_j4)
    gate = assert_recovery_consumers_agree(
        http, **_agree(world, linked=[j1, j4], flagged=[j1, j4], operation_id=new_operation)
    )
    _record(http, world, gate, flagged=[j1, j4], expect=GateCode.RECONCILIATION_REQUIRED)

    # settle exactly as C5 of regression (a): A5 passes after settling
    _filled_sync(order_id)
    recon_b = timeline.next()
    assert recon_b > finished_j4
    _clean_reconciliation(recon_b)
    gate = assert_recovery_consumers_agree(
        http, **_agree(world, linked=[j1, j4], flagged=[j1, j4], operation_id=new_operation)
    )
    _record(http, world, gate, flagged=[j1, j4], expect=None)
    assert _a5_passed()
    assert GateCode.OUTCOME_UNRESOLVED not in world.history, world.history
