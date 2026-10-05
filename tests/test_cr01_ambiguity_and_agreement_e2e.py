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
from tests.support.recovery_fixtures import seed_account_run, seed_job, seed_paper_run
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
    _validate,
    attempt_outcomes,
    continue_job,
    evaluation,
    intent_rows,
    manifest,
    operation_row,
    run_continue,
    seed_batch,
    takeover,
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
from trading_platform.jobs.queue import claim_next_job, reclaim_lost_jobs
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


# ---------------------------------------------------------------------------
# Task 2: regression (d) - one consumer-agreement matrix before and after reuse
# ---------------------------------------------------------------------------

REUSE_KINDS = ["continue", "retry_of_continue", "start_after_end", "ambiguous_retry_by_continue"]


def _attempt_of(order_id: uuid.UUID) -> OrderSubmissionAttempt:
    with session_scope(load_settings()) as session:
        attempt = session.execute(
            select(OrderSubmissionAttempt).where(OrderSubmissionAttempt.paper_order_id == order_id)
        ).scalar_one()
        session.expunge(attempt)
        return attempt


@pytest.mark.parametrize("kind", REUSE_KINDS)
def test_d_consumers_agree_before_and_after_reuse(
    http: TestClient, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    """(d) At every checkpoint (K1 after the crash, K2 after the clean M5, K3 after the reuse
    registration committed and BEFORE its T1, K3b right after the reuse POST while the reusing Job
    is still RUNNING, K4 after the reusing Job finished or was reclaimed, K5 after settling) the
    shared agreement assertion holds and the explicit assertions of ``_explicit`` hold, for every
    reuse kind. The genuinely ambiguous kind ends blocked at every later checkpoint."""

    timeline = Timeline()
    world = _saf01_shape(monkeypatch, timeline)
    j1, order_id, cid1, op, broker = (
        world.j1,
        world.order_id,
        world.cid1,
        world.operation_id,
        world.broker,
    )
    history = world.history
    ambiguous = kind == "ambiguous_retry_by_continue"
    if ambiguous:
        broker.script = ["read_timeout"]
    operation_ids = [op]
    resolved = GateCode.RECONCILIATION_REQUIRED

    # K1: right after the crash (J1 reclaimed, no reconciliation yet)
    gate = assert_recovery_consumers_agree(http, **_agree([j1], op))
    _explicit(
        http,
        gate=gate,
        expect=resolved,
        linked=[j1],
        operation_ids=operation_ids,
        order_id=order_id,
        history=history,
    )
    assert [i.classification.value for i in _status().intents] == ["not_sent"]

    if kind == "start_after_end":  # End (M12): the unsent intents are cancelled, O keeps its status
        with session_scope(load_settings()) as session:
            end_operation(session, op, operator_reason="cr01 d end", actor="pytest")

    # K2: a clean standalone reconciliation after J1 (before any reuse)
    recon_a = timeline.next()
    assert recon_a > world.j1_completed_at
    _clean_reconciliation(recon_a)
    gate = assert_recovery_consumers_agree(http, **_agree([j1], op))
    _explicit(
        http,
        gate=gate,
        expect=None,
        linked=[j1],
        operation_ids=operation_ids,
        order_id=order_id,
        history=history,
    )

    # the reusing Job
    prior = [j1]
    new_run: uuid.UUID | None = None
    if kind == "retry_of_continue":
        # J2 registers (retry_requested) and crashes before T1, then M15 retries it as J3
        j2 = continue_job(op)
        hold = _hold_before_t1(monkeypatch)
        worker = Worker(lambda: run_continue(broker.service(), operation_id=op, job_id=j2)).start()
        hold.wait_arrived()
        terminate_lock_holder()
        crashed_j2 = timeline.next()
        assert crashed_j2 > recon_a
        _crash_job(j2, crashed_j2)
        hold.release.set()
        worker.join()
        assert worker.error is not None  # T1 refused (lease lost)
        assert attempt_outcomes(cid1) == [] and broker.received == {}
        recon_b = timeline.next()
        assert recon_b > crashed_j2
        _clean_reconciliation(recon_b)
        gate = assert_recovery_consumers_agree(http, **_agree([j1, j2], op))
        _explicit(
            http,
            gate=gate,
            expect=None,
            linked=[j1, j2],
            operation_ids=operation_ids,
            order_id=order_id,
            history=history,
        )
        result = _orchestration().retry(job_id=j2, idempotency_key="cr01-d-retry-j2")
        reuse = uuid.UUID(result.reference.job_id)
        assert _job_row(reuse).retry_of_job_id == j2
        with session_scope(load_settings()) as session:  # the worker's product claim
            assert claim_next_job(session, worker_id="retry-worker", lease_seconds=86400) == reuse
        prior = [j1, j2]
    elif kind == "start_after_end":
        new_run = evaluation(DEFAULT_BATCH[:1], as_of="2024-01-08", base=timeline.next())
        assert _validate(new_run)["risk_run_id"] == str(new_run)  # the start gate passes
        reuse = _start_job(new_run)
    else:
        assert _continue_validate(op)["mode"] == "continue"  # the Continue gate passes
        reuse = continue_job(op)

    # K3: the reuse registration committed, the reusing executor held BEFORE its T1
    if new_run is not None:
        start_run = new_run
        fn: Callable[[], Any] = lambda: _start(  # noqa: E731
            broker.service(), risk_run_id=start_run, job_id=reuse
        )
    else:
        fn = lambda: run_continue(broker.service(), operation_id=op, job_id=reuse)  # noqa: E731
    hold = _hold_before_t1(monkeypatch)
    worker = Worker(fn).start()
    hold.wait_arrived()
    reuse_run = _run_of(reuse)
    current_op = op
    if new_run is not None:
        current_op = operation_row().id
        assert current_op != op
        operation_ids = [op, current_op]
    with session_scope(load_settings()) as session:
        assert _accepted_registrations(session, order_id) >= {
            ("intent_registered", world.r1),
            ("retry_requested", reuse_run),
        }
    assert attempt_outcomes(cid1) == [] and broker.received == {}
    linked = [*prior, reuse]
    gate = assert_recovery_consumers_agree(http, **_agree(linked, current_op))
    _explicit(
        http,
        gate=gate,
        expect=None,
        linked=linked,
        operation_ids=operation_ids,
        order_id=order_id,
        history=history,
    )

    # K3b: the reusing executor POSTs once; its Job is still RUNNING (not yet a broker effect)
    hold.release.set()
    worker.join()
    outcome = "ambiguous" if ambiguous else "accepted"
    assert broker.received == {cid1: 1}  # exactly one POST
    assert attempt_outcomes(cid1) == [(1, outcome)]
    attempt = _attempt_of(order_id)
    assert attempt.executor_job_id == reuse and attempt.strategy_run_id == reuse_run
    assert _order(order_id).strategy_run_id == world.r1  # the origin run is kept
    running = _job_row(reuse)
    assert running.status is JobStatus.RUNNING and running.completed_at is None
    if ambiguous:
        assert isinstance(worker.error, AmbiguousOrderSubmissionError)
    else:
        assert worker.error is None
        decision = worker.result.result_summary["submitted_orders"][0]["intent_decision"]
        assert decision["action"] == "retry_existing"
    after_post = OUTCOME_UNRESOLVED if ambiguous else None
    gate = assert_recovery_consumers_agree(http, **_agree(linked, current_op))
    _explicit(
        http,
        gate=gate,
        expect=after_post,
        linked=linked,
        operation_ids=operation_ids,
        order_id=order_id,
        history=history,
    )

    if ambiguous:
        # K4: J2 is reclaimed (flagged); O is attributed to BOTH Jobs and blocks
        crashed = timeline.next()
        _crash_job(reuse, crashed)
        gate = assert_recovery_consumers_agree(http, **_agree(linked, current_op))
        _explicit(
            http,
            gate=gate,
            expect=OUTCOME_UNRESOLVED,
            linked=linked,
            operation_ids=operation_ids,
            order_id=order_id,
            history=history,
        )
        for job_id in linked:
            assert _r3_blocking_ids(http, job_id) == {str(order_id)}
        # K5: a fresh clean M5 after every Job does NOT settle a genuinely ambiguous order
        recon_c = timeline.next()
        assert recon_c > crashed
        _clean_reconciliation(recon_c)
        gate = assert_recovery_consumers_agree(http, **_agree(linked, current_op))
        _explicit(
            http,
            gate=gate,
            expect=OUTCOME_UNRESOLVED,
            linked=linked,
            operation_ids=operation_ids,
            order_id=order_id,
            history=history,
        )
        assert history == [
            resolved,
            None,
            None,
            OUTCOME_UNRESOLVED,
            OUTCOME_UNRESOLVED,
            OUTCOME_UNRESOLVED,
        ]
        return

    # K4: the reusing Job finished at an explicit instant later than the K2 reconciliation
    finished = timeline.next()
    assert finished > recon_a
    _finish_job(reuse, finished)
    gate = assert_recovery_consumers_agree(http, **_agree(linked, current_op))
    _explicit(
        http,
        gate=gate,
        expect=resolved,
        linked=linked,
        operation_ids=operation_ids,
        order_id=order_id,
        history=history,
    )
    # K5: the order fills, M4 sync, a fresh clean M5 after the reusing Job
    _filled_sync(order_id)
    recon_c = timeline.next()
    assert recon_c > finished
    _clean_reconciliation(recon_c)
    gate = assert_recovery_consumers_agree(http, **_agree(linked, current_op))
    _explicit(
        http,
        gate=gate,
        expect=None,
        linked=linked,
        operation_ids=operation_ids,
        order_id=order_id,
        history=history,
    )
    assert OUTCOME_UNRESOLVED not in history, history
    expected_history = [resolved, None, None, None, resolved, None]
    if kind == "retry_of_continue":  # one extra checkpoint: after J2's crash and the second M5
        expected_history.insert(2, None)
    assert history == expected_history, history
    assert _job_row(j1).completed_at == world.j1_completed_at  # J1 keeps its reclaimed timestamp


# ---------------------------------------------------------------------------
# S14: an UNKNOWN order with a complete pre_connection history
# ---------------------------------------------------------------------------


def _op_read(http: TestClient, operation_id: uuid.UUID) -> dict[str, Any]:
    response = http.get(f"/api/v1/execution-operations/{operation_id}")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


@pytest.mark.parametrize("path", ["continue", "end"])
def test_d_s14_unknown_order_with_a_complete_pre_connection_history(
    http: TestClient, path: str
) -> None:
    """S14 on a product path (the S1 takeover of ``test_s1_v2``): worker A is held before its
    request leaves the process, A's Job is reclaimed, the takeover executor B parks the intent
    UNKNOWN (in doubt) and A's request then fails ``pre_connection`` (never sent, attempt log
    complete). The shared verdict is PROVEN_NOT_SENT: every recovery consumer agrees (gate
    ``reconciliation_required``, A5 fails, R3 lists it NON-blocking) while the operation read
    model's ``unresolved_intents`` does NOT list it, the intent still DISPLAYS ``ambiguous`` and the
    operation still reads ``paused / outcome_unresolved``. This test pins CURRENT behaviour; the
    display / gate mismatch it exposes is an OPEN DECISION (20.1-28 deviation 4, reported in the
    20.1-30 SUMMARY), not something this test approves. The one safety property pinned
    unconditionally: the UNKNOWN order is never POSTed."""

    timeline = Timeline()
    risk_run, _events = seed_batch(DEFAULT_BATCH[:2], manifest=manifest())
    j1 = _start_job(risk_run)
    broker = S1Broker(script=["connect_error", "accept"], before_request=Gate())
    gate = broker.before_request
    assert gate is not None
    worker = Worker(lambda: _start(broker.service(), risk_run_id=risk_run, job_id=j1)).start()
    gate.wait_arrived()
    op = operation_row().id
    (_intent1, cid1), (_intent2, cid2) = intent_rows()
    terminate_lock_holder()
    crashed_a = timeline.next()
    _crash_job(j1, crashed_a)
    acquisition, job_b = takeover(op)
    assert acquisition.paused is True  # the takeover parked the in-doubt intent
    gate.release.set()
    worker.join()
    assert worker.error is not None  # A lost authority
    assert attempt_outcomes(cid1) == [(1, "pre_connection")]  # no attempt #2
    assert broker.received == {}  # the request never left the process
    with session_scope(load_settings()) as session:
        order_id = session.execute(
            select(PaperOrder.id).where(PaperOrder.client_order_id == cid1)
        ).scalar_one()
    assert _order(order_id).status == OrderLifecycleState.UNKNOWN
    finished_b = timeline.next()
    assert finished_b > crashed_a
    _finish_job(job_b, finished_b)  # the takeover executor is a normal Job that ended
    linked = [j1, job_b]
    attempts_before = _all_attempts()

    def display() -> tuple[list[str], list[Any]]:
        body = _op_read(http, op)
        return [i["state"] for i in body["intents"]], body["unresolved_intents"]

    # before the clean M5: all consumers agree on reconciliation_required ...
    gate_code = assert_recovery_consumers_agree(http, **_agree(linked, op))
    assert gate_code is GateCode.RECONCILIATION_REQUIRED
    assert [(i.job_id, i.classification.value, i.blocking) for i in _status().intents] == [
        (j1, "not_sent", False)
    ]
    assert not _a5().passed
    (item,) = _r3_items(http, j1)
    assert (item["intent_id"], item["classification"], item["blocking"]) == (
        str(order_id),
        "not_sent",
        False,
    )
    assert _r3_items(http, job_b) == []
    assert _continue_conflict(op).code == "reconciliation_required"
    # ... while the operation read model does not list the order and still shows it as ambiguous
    states, unresolved = display()
    assert states == ["ambiguous", "planned"] and unresolved == []
    operation = _op_read(http, op)
    assert (operation["state"], operation["reason"]) == ("paused", "outcome_unresolved")

    # a clean standalone reconciliation after every Job resolves the gate (the order is proven
    # not sent) although the order is still UNKNOWN and still displayed as ambiguous
    recon = timeline.next()
    assert recon > finished_b
    _clean_reconciliation(recon)
    gate_code = assert_recovery_consumers_agree(http, **_agree(linked, op))
    assert gate_code is None and _a5().passed
    assert _order(order_id).status == OrderLifecycleState.UNKNOWN
    states, unresolved = display()
    assert states == ["ambiguous", "planned"] and unresolved == []

    if path == "continue":
        assert _continue_validate(op)["mode"] == "continue"  # the Continue gate admits it
        run_continue(broker.service(), operation_id=op, job_id=continue_job(op))
    else:
        with session_scope(load_settings()) as session:
            result = end_operation(session, op, operator_reason="cr01 s14 end", actor="pytest")
        assert result.unresolved_intents == ()  # End lists nothing for the UNKNOWN order
        assert len(result.unsent_cancelled) == 1  # only the planned intent 2 is cancelled
        states, unresolved = display()
        assert states == ["ambiguous", "cancelled_unsent"] and unresolved == []
    # the safety property: the UNKNOWN order is never POSTed and gets no new attempt row
    assert cid1 not in broker.received
    assert [row for row in _all_attempts() if row[0] == cid1] == [
        row for row in attempts_before if row[0] == cid1
    ]
    assert _order(order_id).status == OrderLifecycleState.UNKNOWN


# ---------------------------------------------------------------------------
# The order-less flagged Job control (OD-1 open)
# ---------------------------------------------------------------------------


def test_d_order_less_flagged_job_stays_blocked_while_od1_is_open(
    http: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # OD-1 open decision (user, 2026-10-05): this asserts CURRENT blocking behaviour; it is not an approved terminal state. If OD-1 is approved, this control changes per 20.1-OD-1-DRAFT.md.
    """A flagged paper-session Job whose run registered no order stays ``execution_path_unproven``
    in every consumer: the absence of an order alone is not proof that nothing could be sent. ORDER
    MATTERS (a blocking control seeded first would refuse every Continue / T1 / start): a full
    Continue-reuse scenario is settled FIRST (gate None, A5 passes), THEN the control Job is added.
    The control is fixture-seeded: it asserts current blocking behaviour, it is not a reuse path."""

    timeline = Timeline()
    world = _saf01_shape(monkeypatch, timeline)
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
    j2 = continue_job(op)
    report = run_continue(broker.service(), operation_id=op, job_id=j2)
    assert report.result_summary["submitted_orders"][0]["intent_decision"]["action"] == (
        "retry_existing"
    )
    assert broker.received == {cid1: 1}
    finished_j2 = timeline.next()
    _finish_job(j2, finished_j2)
    _filled_sync(order_id)
    settled = timeline.next()
    assert settled > finished_j2
    _clean_reconciliation(settled)
    linked = [j1, j2]
    gate = assert_recovery_consumers_agree(http, **_agree(linked, op))
    assert gate is None and _a5().passed  # the reuse scenario is settled
    reuse_reads = {job_id: _r3_items(http, job_id) for job_id in linked}

    # the control: a flagged paper-session Job with a linked paper_execution run and NO order,
    # completed explicitly later than the settled reconciliation
    control_at = timeline.next()
    assert control_at > settled
    with session_scope(load_settings()) as session:
        control_job = seed_job(session, strategy_id=STRATEGY, completed_at=control_at)
        seed_paper_run(session, control_job)
        control = control_job.id
    assert _job_row(control).outcome_uncertain is True

    def assert_blocked() -> None:
        assert _gate() is OUTCOME_UNRESOLVED
        assert _unproven(control)
        status = _status()
        (entry,) = [i for i in status.intents if i.job_id == control]
        assert entry.intent_id is None and not entry.established and entry.blocking
        with session_scope(load_settings()) as session:  # the account read shows the same entry
            account = account_recovery_status(session, now=clock.now_utc())
        subject = next(s for s in account.subjects if s.strategy_id == STRATEGY)
        assert any(
            i.job_id == control
            and i.intent_id is None
            and i.unresolved_reason is UnresolvedReason.EXECUTION_PATH_UNPROVEN
            for i in subject.intents
        )
        (item,) = _r3_items(http, control)  # R3 of the control Job
        assert item["intent_id"] is None and item["blocking"] is True
        assert item["unresolved_reason"] == "execution_path_unproven"
        assert _r3(http, control)["gate_code"] == "outcome_unresolved"
        a5 = _a5()  # A5 fails with the control Job among its refs
        assert not a5.passed
        assert any(
            ref.kind is EvidenceKind.JOB and ref.id == str(control) for ref in a5.evidence_refs
        ), a5.evidence_refs
        # the reused order stays attributed to its own Jobs: their R3 reads are unchanged
        assert {job_id: _r3_items(http, job_id) for job_id in linked} == reuse_reads
        assert _continue_conflict(op).code == "outcome_unresolved"  # and it blocks everything

    # the control Job registered no order, so it is not passed as a registering Job
    with_control = {**_agree(linked, op), "linked_job_ids": [*linked, control]}
    assert_blocked()
    gate = assert_recovery_consumers_agree(http, **with_control)
    assert gate is OUTCOME_UNRESOLVED

    # a further fresh clean reconciliation (completed after the control Job) does NOT release it
    fresh = timeline.next()
    assert fresh > control_at
    _clean_reconciliation(fresh)
    assert_blocked()
    gate = assert_recovery_consumers_agree(http, **with_control)
    assert gate is OUTCOME_UNRESOLVED
