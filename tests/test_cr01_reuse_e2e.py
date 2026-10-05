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
  the earlier proven-not-sent order is re-registered (``retry_existing``) and POSTed once.

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
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from tests.support.paper_eligibility import allow_paper_execution
from tests.support.paper_execution_seams import TEST_LEASE_OWNER, allow_direct_paper_execution
from tests.support.recovery_agreement import assert_recovery_consumers_agree
from tests.support.recovery_fixtures import seed_account_run
from tests.test_attribution_reconciliation import _broker_fill, _broker_order
from tests.test_paper_execution import migrated_paper_db  # noqa: F401  (database fixture)
from tests.test_paper_session_operations import (
    DEFAULT_BATCH,
    SESSION,
    STRATEGY,
    Gate,
    S1Broker,
    Worker,
    _continue_validate,
    _start,
    attempt_outcomes,
    continue_job,
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
    StrategyRun,
    StrategyRunType,
)
from trading_platform.db.session import session_scope
from trading_platform.jobs.lifecycle import JobTransitionRequest, apply_job_transition
from trading_platform.jobs.queue import reclaim_lost_jobs
from trading_platform.services.execution import submit_orders as submit_orders_module
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


def _agree(world: World, *, linked: list[uuid.UUID], flagged: list[uuid.UUID]) -> dict[str, Any]:
    """The arguments of the shared consumer-agreement assertion at one checkpoint."""

    return {
        "strategy_id": STRATEGY,
        "linked_job_ids": linked,
        "registering_flagged_job_ids": flagged,
        "operation_id": world.operation_id,
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
    assert world.history[:4] == [
        GateCode.RECONCILIATION_REQUIRED,
        None,
        GateCode.RECONCILIATION_REQUIRED,
        None,
    ]
