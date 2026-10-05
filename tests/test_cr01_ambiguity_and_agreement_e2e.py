"""CR-01 required regressions (c) and (d), user decision 2026-10-05 (plan 20.1-30).

* (c) A GENUINELY ambiguous earlier attempt stays blocked throughout, whatever happens next: before
  and after a fresh clean standalone reconciliation, after Continue / M15 retry / a new start are
  refused, and after End (D-20). Three shapes: a NULL outcome (T1 committed, the worker died before
  the outcome was recorded), a recorded read-timeout ``ambiguous`` attempt, and an ambiguous attempt
  made by the RETRYING Continue Job on an order whose origin is the earlier SAF-01 Job (the
  uncertainty is attributed to BOTH Jobs). Durable history adds attribution; it never releases.
* (d) ONE agreement assertion (``tests/support/recovery_agreement.assert_recovery_consumers_agree``)
  across R3, the strategy gate, the account read, A5 and the operation read model at every
  checkpoint before and after reuse, for every reuse kind, plus the S14 shape and the order-less
  flagged Job control (OD-1 open: asserts CURRENT blocking behaviour, not an approved state).

Harness rules (same as ``test_cr01_reuse_e2e``): the only injected faults are a worker crash (a
held worker whose lock connection is terminated) and the passage of time (a lease lapses);
every Job is flagged ``outcome_uncertain`` by ``reclaim_lost_jobs``; every later step is a product
entrypoint. No test here writes ``paper_orders`` / ``order_events`` / ``order_submission_attempts`` /
``execution_operation*`` rows. The clean standalone reconciliation (M5) is seeded with an EXPLICIT
``completed_at`` (``Timeline``) so that every ordering a gate value depends on is stated and asserted.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
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
    _conflict,
    _continue_conflict,
    _continue_validate,
    _start,
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
    ExecutionOperationIntent,
    Job,
    JobEventType,
    JobStatus,
    OrderLifecycleState,
    OrderSubmissionAttempt,
    PaperOrder,
    StrategyRun,
    StrategyRunType,
)
from trading_platform.db.session import session_scope
from trading_platform.jobs.lifecycle import JobTransitionRequest, apply_job_transition
from trading_platform.jobs.queue import reclaim_lost_jobs
from trading_platform.jobs.registry import build_default_registry
from trading_platform.orchestration.job_mutations import (
    JobOrchestrationService,
    RetryBlockedError,
)
from trading_platform.services.alpaca import AmbiguousOrderSubmissionError
from trading_platform.services.execution import submit_orders as submit_orders_module
from trading_platform.services.execution.operations import end_operation
from trading_platform.services.execution.sync_orders import sync_account_state
from trading_platform.services.paper_account_checks import EvidenceKind, _check_a5
from trading_platform.services.recovery import (
    GateCode,
    UnresolvedReason,
    account_recovery_status,
    strategy_recovery_status,
)

OUTCOME_UNRESOLVED = GateCode.OUTCOME_UNRESOLVED


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
    """Strictly increasing EXPLICIT instants, all later than any real-time write of the scenario."""

    def __init__(self) -> None:
        self._at = datetime.now(UTC) + timedelta(minutes=10)

    def next(self) -> datetime:
        at = self._at
        self._at += timedelta(minutes=10)
        return at


def _start_job(risk_run_id: uuid.UUID) -> uuid.UUID:
    """A RUNNING start Job holding a lease, with the REAL start payload (an M15 retry of it
    re-runs ``validate_payload`` on this payload; an empty payload would be rejected first)."""

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
            update(Job).where(Job.id == job_id).values(lease_expires_at=at - timedelta(minutes=1))
        )
    with session_scope(load_settings()) as session:
        reclaimed = reclaim_lost_jobs(session, now=at)
    assert reclaimed == [job_id]
    job = _job_row(job_id)
    assert job.status is JobStatus.FAILED and job.outcome_uncertain is True
    assert job.completed_at == at


def _finish_job(job_id: uuid.UUID, at: datetime) -> None:
    """A Job finishes normally at an explicit instant, through the Job lifecycle."""

    with session_scope(load_settings()) as session:
        apply_job_transition(
            session,
            job_id=job_id,
            request=JobTransitionRequest(event_type=JobEventType.SUCCEEDED, event_at=at),
        )
    job = _job_row(job_id)
    assert job.status is JobStatus.SUCCEEDED and job.completed_at == at


def _job_row(job_id: uuid.UUID) -> Job:
    with session_scope(load_settings()) as session:
        job = session.get(Job, job_id)
        assert job is not None
        session.expunge(job)
        return job


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


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------


def _a5() -> Any:
    with session_scope(load_settings()) as session:
        return _check_a5(session, now=clock.now_utc())


def _status() -> Any:
    with session_scope(load_settings()) as session:
        return strategy_recovery_status(session, STRATEGY, now=clock.now_utc())


def _gate() -> GateCode | None:
    gate: GateCode | None = _status().gate_code
    return gate


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


def _r3_items(http: TestClient, job_id: uuid.UUID) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = _r3(http, job_id)["intents"]
    return items


def _r3_blocking_ids(http: TestClient, job_id: uuid.UUID) -> set[str]:
    return {
        item["intent_id"]
        for item in _r3_items(http, job_id)
        if item["blocking"] and item["intent_id"] is not None
    }


def _order(order_id: uuid.UUID) -> PaperOrder:
    with session_scope(load_settings()) as session:
        order = session.get(PaperOrder, order_id)
        assert order is not None
        session.expunge(order)
        return order


def _all_attempts() -> list[tuple[str, int, str | None, uuid.UUID | None]]:
    """EVERY attempt row of the database (order, number, outcome, executor Job)."""

    with session_scope(load_settings()) as session:
        rows = session.execute(
            select(
                PaperOrder.client_order_id,
                OrderSubmissionAttempt.attempt_number,
                OrderSubmissionAttempt.outcome_class,
                OrderSubmissionAttempt.executor_job_id,
            )
            .join(PaperOrder, PaperOrder.id == OrderSubmissionAttempt.paper_order_id)
            .order_by(PaperOrder.client_order_id, OrderSubmissionAttempt.attempt_number)
        ).all()
    return [(cid, number, None if out is None else str(out), job) for cid, number, out, job in rows]


def _retry_job_count() -> int:
    with session_scope(load_settings()) as session:
        return int(
            session.execute(
                select(func.count()).select_from(Job).where(Job.retry_of_job_id.is_not(None))
            ).scalar_one()
        )


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
    assert _order(order_id).status == OrderLifecycleState.FILLED
    return broker


# ---------------------------------------------------------------------------
# Shapes
# ---------------------------------------------------------------------------


@dataclass
class World:
    """The SAF-01 shape: a crash before T1 leaves an operation-bound registered order with zero
    attempts; the start Job J1 is reclaimed ``outcome_uncertain``."""

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


def _saf01_shape(monkeypatch: pytest.MonkeyPatch, timeline: Timeline) -> World:
    """Seed a two-intent batch, start the session as Job J1 and crash it before T1 of intent 1: J1's
    worker is held in a wrapper around ``_send_authorized`` (before ``authorize_send``), its lock
    connection is terminated, its lease lapses and the sweep fails it; the worker is then released
    and its T1 refuses (lease lost)."""

    risk_run, _events = seed_batch(DEFAULT_BATCH[:2], manifest=manifest())
    j1 = _start_job(risk_run)
    broker = S1Broker()
    armed = {"on": True}
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

    # precondition, asserted explicitly (never seeded)
    assert broker.received == {}  # no POST has ever left the process
    operation = operation_row()
    (intent1, cid1), (_intent2, cid2) = intent_rows()
    with session_scope(load_settings()) as session:
        r1 = session.execute(
            select(StrategyRun.id).where(
                StrategyRun.job_id == j1, StrategyRun.run_type == StrategyRunType.PAPER_EXECUTION
            )
        ).scalar_one()
        order = session.execute(
            select(PaperOrder).where(PaperOrder.client_order_id == cid1)
        ).scalar_one()
        order_id = order.id
        assert order.strategy_run_id == r1
        assert order.status.value == "pending_submission" and order.broker_order_id is None
        bound = session.execute(
            select(ExecutionOperationIntent.paper_order_id).where(
                ExecutionOperationIntent.id == intent1
            )
        ).scalar_one()
        assert bound == order_id
        assert _accepted_registrations(session, order_id) == {("intent_registered", r1)}
    assert attempt_outcomes(cid1) == []
    return World(
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


def _flagged(linked: list[uuid.UUID]) -> list[uuid.UUID]:
    return [job_id for job_id in linked if _job_row(job_id).outcome_uncertain]


def _agree(linked: list[uuid.UUID], operation_id: uuid.UUID) -> dict[str, Any]:
    """The arguments of the shared consumer-agreement assertion at one checkpoint. A Job is a
    ``registering_flagged`` Job when it is outcome_uncertain (read from the database)."""

    return {
        "strategy_id": STRATEGY,
        "linked_job_ids": linked,
        "registering_flagged_job_ids": _flagged(linked),
        "operation_id": operation_id,
    }


def _explicit(
    http: TestClient,
    *,
    gate: GateCode | None,
    expect: GateCode | None,
    linked: list[uuid.UUID],
    operation_ids: list[uuid.UUID],
    order_id: uuid.UUID,
    history: list[GateCode | None],
) -> None:
    """The checkpoint assertions stated explicitly in the test (not only inside the shared helper):
    (0) the gate is the expected one; (1) strategy gate == the account subject's gate; (2) A5
    passes exactly when the account is resolved; (3) every FLAGGED linked Job lists the order and
    owns no Job-level execution_path_unproven entry (an unflagged reusing Job is not required to
    list it: R3 attributes an unflagged Job's orders by origin, 20.1-17); (4) the blocking intent
    ids of the strategy status == the union over the R3 reads of the linked Jobs == the order ids
    of the operation read models' unresolved_intents (restricted to those operations); (5) no
    duplicated (job_id, intent_id) pair."""

    history.append(gate)
    assert gate is expect, (gate, expect, history)
    now = clock.now_utc()
    with session_scope(load_settings()) as session:
        strategy = strategy_recovery_status(session, STRATEGY, now=now)
        account = account_recovery_status(session, now=now)
        a5 = _check_a5(session, now=now)
    subject = next((s for s in account.subjects if s.strategy_id == STRATEGY), None)
    assert (subject.gate_code if subject is not None else None) == strategy.gate_code  # (1)
    assert a5.passed is account.resolved  # (2)
    assert a5.passed is (expect is None)
    r3 = {job_id: _r3_items(http, job_id) for job_id in linked}
    for job_id in _flagged(linked):  # (3)
        ids = {item["intent_id"] for item in r3[job_id]}
        assert str(order_id) in ids, f"flagged Job {job_id} does not list the order"
        assert not [i for i in r3[job_id] if i["unresolved_reason"] == "execution_path_unproven"]
        assert all(i["intent_id"] is not None for i in r3[job_id])
    blocking = {str(i.intent_id) for i in strategy.intents if i.blocking and i.intent_id}
    union = {
        item["intent_id"]
        for items in r3.values()
        for item in items
        if item["blocking"] and item["intent_id"] is not None
    }
    assert blocking == union, (blocking, union)  # (4a)
    listed: set[str] = set()
    linked_orders: set[str] = set()
    for operation_id in operation_ids:
        response = http.get(f"/api/v1/execution-operations/{operation_id}")
        assert response.status_code == 200, response.text
        body = response.json()
        listed |= {i["paper_order_id"] for i in body["unresolved_intents"] if i["paper_order_id"]}
        linked_orders |= {str(i["paper_order_id"]) for i in body["intents"] if i["paper_order_id"]}
    assert listed == blocking & linked_orders, (listed, blocking, linked_orders)  # (4b)
    assert blocking <= linked_orders, (blocking, linked_orders)
    pairs = [(i.job_id, i.intent_id) for i in strategy.intents if i.intent_id is not None]
    assert len(pairs) == len(set(pairs))  # (5)
    for job_id, items in r3.items():
        ids_of_job = [i["intent_id"] for i in items]
        assert len(ids_of_job) == len(set(ids_of_job)), (job_id, ids_of_job)


# ---------------------------------------------------------------------------
# Task 1: regression (c) - genuine ambiguity stays blocked
# ---------------------------------------------------------------------------


@dataclass
class Ambiguity:
    risk_run: uuid.UUID
    operation_id: uuid.UUID
    order_id: uuid.UUID
    cid1: str
    broker: S1Broker
    timeline: Timeline
    linked: list[uuid.UUID]  # every Job linked to the order, all flagged outcome_uncertain
    release: Callable[[], None]  # releases a held worker (idempotent), a no-op otherwise
    last_effect_at: datetime


def _null_outcome(timeline: Timeline) -> Ambiguity:
    """Worker A commits T1 for intent 1 (attempt ``(1, NULL)``) and is held BEFORE its request
    reaches the broker; its lock connection is terminated, its lease lapses and the sweep fails
    the Job. A stays held during the assertions and is released at teardown with a broker script
    that ends in a read timeout (the attempt completes ``ambiguous``, never ``accepted``)."""

    risk_run, _events = seed_batch(DEFAULT_BATCH[:2], manifest=manifest())
    j1 = _start_job(risk_run)
    broker = S1Broker(script=["read_timeout"], before_request=Gate())
    gate = broker.before_request
    assert gate is not None
    worker = Worker(lambda: _start(broker.service(), risk_run_id=risk_run, job_id=j1)).start()

    def release() -> None:
        gate.release.set()
        worker.thread.join(timeout=60)

    gate.wait_arrived()
    operation = operation_row()
    (_intent1, cid1), _second = intent_rows()
    assert attempt_outcomes(cid1) == [(1, None)] and broker.received == {}
    terminate_lock_holder()
    crashed_at = timeline.next()
    _crash_job(j1, crashed_at)
    with session_scope(load_settings()) as session:
        order_id = session.execute(
            select(PaperOrder.id).where(PaperOrder.client_order_id == cid1)
        ).scalar_one()
    return Ambiguity(
        risk_run=risk_run,
        operation_id=operation.id,
        order_id=order_id,
        cid1=cid1,
        broker=broker,
        timeline=timeline,
        linked=[j1],
        release=release,
        last_effect_at=crashed_at,
    )


def _read_timeout(timeline: Timeline) -> Ambiguity:
    """The start's POST ends in a read timeout: attempt ``(1, ambiguous)``, order UNKNOWN, the
    operation paused ``outcome_unresolved``; the Job is then reclaimed (its lease lapses)."""

    risk_run, _events = seed_batch(DEFAULT_BATCH[:2], manifest=manifest())
    j1 = _start_job(risk_run)
    broker = S1Broker(script=["read_timeout"])
    with pytest.raises(AmbiguousOrderSubmissionError):
        _start(broker.service(), risk_run_id=risk_run, job_id=j1)
    operation = operation_row()
    (_intent1, cid1), _second = intent_rows()
    assert broker.received == {cid1: 1}
    assert attempt_outcomes(cid1) == [(1, "ambiguous")]
    assert (operation.state, operation.reason) == ("paused", "outcome_unresolved")
    crashed_at = timeline.next()
    _crash_job(j1, crashed_at)
    with session_scope(load_settings()) as session:
        order = session.execute(
            select(PaperOrder).where(PaperOrder.client_order_id == cid1)
        ).scalar_one()
        assert order.status == OrderLifecycleState.UNKNOWN
        order_id = order.id
    return Ambiguity(
        risk_run=risk_run,
        operation_id=operation.id,
        order_id=order_id,
        cid1=cid1,
        broker=broker,
        timeline=timeline,
        linked=[j1],
        release=lambda: None,
        last_effect_at=crashed_at,
    )


def _ambiguous_retry_by_continue(
    http: TestClient, monkeypatch: pytest.MonkeyPatch, timeline: Timeline
) -> Ambiguity:
    """SAF-01 shape on J1 (zero attempts, flagged), a clean M5 releases it, then the Continue Job J2
    re-registers the order (``retry_existing``) and its POST ends in a read timeout: O is UNKNOWN
    with attempt ``(1, ambiguous)`` by J2, origin R1. ``run_continue`` raises and leaves J2 RUNNING
    and unflagged, so the worker's death is arranged explicitly: J2's lease lapses (an UPDATE of
    ``jobs.lease_expires_at``, not an evidence table) and the product sweep reclaims it at T."""

    world = _saf01_shape(monkeypatch, timeline)
    broker, op, order_id, cid1, j1 = (
        world.broker,
        world.operation_id,
        world.order_id,
        world.cid1,
        world.j1,
    )
    broker.script = ["read_timeout"]
    recon_a = timeline.next()
    assert recon_a > world.j1_completed_at
    _clean_reconciliation(recon_a)
    assert _gate() is None  # the SAF-01 release holds before the reuse
    assert _continue_validate(op)["mode"] == "continue"
    j2 = continue_job(op)
    with pytest.raises(AmbiguousOrderSubmissionError):
        run_continue(broker.service(), operation_id=op, job_id=j2)
    r2 = _run_of(j2)
    assert broker.received == {cid1: 1}
    assert attempt_outcomes(cid1) == [(1, "ambiguous")]
    with session_scope(load_settings()) as session:
        attempt = session.execute(
            select(OrderSubmissionAttempt).where(OrderSubmissionAttempt.paper_order_id == order_id)
        ).scalar_one()
        assert attempt.executor_job_id == j2 and attempt.strategy_run_id == r2
        assert _accepted_registrations(session, order_id) == {
            ("intent_registered", world.r1),
            ("retry_requested", r2),
        }
    order = _order(order_id)
    assert order.strategy_run_id == world.r1  # the ORIGIN run is kept
    assert order.status == OrderLifecycleState.UNKNOWN
    # J2 is still RUNNING and unflagged: the strategy gate is already outcome_unresolved (O is
    # unestablished and attributed to the flagged J1, whose R3 lists it blocking); R3(J2) is not
    # required to list O (an unflagged Job's R3 attributes by origin)
    job2 = _job_row(j2)
    assert job2.status is JobStatus.RUNNING and job2.outcome_uncertain is False
    assert _gate() is OUTCOME_UNRESOLVED
    assert _r3_blocking_ids(http, j1) == {str(order_id)}
    # arrange the worker's death: the lease lapses before T, the sweep reclaims J2 at T > M5
    crashed_j2 = timeline.next()
    assert crashed_j2 > recon_a
    _crash_job(j2, crashed_j2)
    # O is attributed to BOTH Jobs and blocks: origin R1 (J1), retry_requested + attempt (J2)
    pairs = [(i.job_id, i.intent_id) for i in _status().intents if i.intent_id is not None]
    assert sorted(pairs) == sorted([(j1, order_id), (j2, order_id)])
    for job_id in (j1, j2):
        assert _r3_blocking_ids(http, job_id) == {str(order_id)}
    return Ambiguity(
        risk_run=world.risk_run,
        operation_id=op,
        order_id=order_id,
        cid1=cid1,
        broker=broker,
        timeline=timeline,
        linked=[j1, j2],
        release=lambda: None,
        last_effect_at=crashed_j2,
    )


@pytest.mark.parametrize("shape", ["null_outcome", "read_timeout", "ambiguous_retry_by_continue"])
def test_c_genuine_ambiguity_stays_blocked(
    http: TestClient, monkeypatch: pytest.MonkeyPatch, shape: str
) -> None:
    """(c) A genuinely ambiguous earlier attempt stays ``outcome_unresolved`` through a fresh clean
    standalone reconciliation, a refused Continue, a refused M15 retry of every flagged Job, End and
    a refused new start: zero POST and zero new attempt rows after the ambiguous attempt."""

    timeline = Timeline()
    if shape == "null_outcome":
        amb = _null_outcome(timeline)
    elif shape == "read_timeout":
        amb = _read_timeout(timeline)
    else:
        amb = _ambiguous_retry_by_continue(http, monkeypatch, timeline)
    broker, op, order_id, linked = amb.broker, amb.operation_id, amb.order_id, amb.linked
    p0 = dict(broker.received)  # POSTs snapshot right after the ambiguous attempt
    a0 = _all_attempts()  # attempt rows snapshot right after the ambiguous attempt
    expected_blocking = {str(order_id)}

    def refusals_hold() -> None:
        """Every refusal of the plan, with the exact code, and no side effect."""

        assert _continue_conflict(op).code == "outcome_unresolved"  # M11 submit gate
        for job_id in linked:  # M15: the recovery block, never a retry Job
            with pytest.raises(RetryBlockedError) as blocked:
                _orchestration().retry(job_id=job_id, idempotency_key=f"cr01-c-{uuid.uuid4().hex}")
            assert blocked.value.block.code == "outcome_unresolved"
        assert _retry_job_count() == 0
        assert broker.received == p0 and _all_attempts() == a0

    try:
        # (i) before any reconciliation (null_outcome / read_timeout; the retry shape reconciled
        # once before its reuse, so the gate is checked on the ambiguity itself)
        assert _gate() is OUTCOME_UNRESOLVED
        for job_id in linked:
            assert _r3_blocking_ids(http, job_id) == expected_blocking
        assert_recovery_consumers_agree(http, **_agree(linked, op))
        assert not _a5().passed

        # (ii) a fresh clean standalone reconciliation, completed after every Job
        recon = timeline.next()
        assert recon > amb.last_effect_at
        _clean_reconciliation(recon)
        assert _gate() is OUTCOME_UNRESOLVED
        a5 = _a5()
        assert not a5.passed
        assert any(
            ref.kind is EvidenceKind.RECOVERY_INTENT and ref.id == str(order_id)
            for ref in a5.evidence_refs
        ), a5.evidence_refs
        for job_id in linked:
            assert _r3_blocking_ids(http, job_id) == expected_blocking
        assert_recovery_consumers_agree(http, **_agree(linked, op))

        # (iii) Continue (M11) and M15 retry of every flagged Job are refused
        refusals_hold()

        # (iv) End (M12) never releases the block (D-20)
        with session_scope(load_settings()) as session:
            end_operation(session, op, operator_reason="cr01 c end", actor="pytest")
        assert _gate() is OUTCOME_UNRESOLVED
        for job_id in linked:
            assert _r3_blocking_ids(http, job_id) == expected_blocking
        assert not _a5().passed
        assert_recovery_consumers_agree(http, **_agree(linked, op))
        refusals_hold()  # every refusal still holds after End (the operation is now terminated)

        # (vi) a new start for a fresh evaluation is refused (after End, so no operation_open masks)
        new_run = evaluation(DEFAULT_BATCH[:1], as_of="2024-01-08", base=timeline.next())
        assert new_run != amb.risk_run
        assert _conflict(new_run).code == "outcome_unresolved"
        assert_recovery_consumers_agree(http, **_agree(linked, op))

        # (vii) zero POST and zero new attempt rows after the ambiguous attempt
        assert broker.received == p0 and _all_attempts() == a0
        assert _retry_job_count() == 0
    finally:
        amb.release()

    if shape == "null_outcome":
        # worker A was released at teardown: ITS one recorded request (already authorized by its
        # T1 before the assertions) reaches the broker and completes ``ambiguous``, never accepted
        assert amb.broker.received == {amb.cid1: 1}
        assert attempt_outcomes(amb.cid1) == [(1, "ambiguous")]
        assert _gate() is OUTCOME_UNRESOLVED
        assert _r3_blocking_ids(http, linked[0]) == expected_blocking
    else:
        assert broker.received == p0 and _all_attempts() == a0
