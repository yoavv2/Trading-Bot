"""CR-01: durable order-to-run linkage (user decision 2026-10-05, plan 20.1-26).

Before this plan ``register()`` re-parented an existing order (``paper_orders.strategy_run_id =
<retrying run>``) on Continue / M15 retry / Start ``retry_existing``. The flagged SAF-01 Job that
first registered the order was then left with a run and ZERO orders, i.e.
``unresolved(execution_path_unproven)``: a blocking state with no product release that
permanently blocked T1, Continue and A5.

The fix keeps the ORIGIN run immutable and reads every later registering run from the accepted
``intent_registered`` / ``retry_requested`` ``order_events`` rows ``register()`` already writes.
What each test pins:

* the origin run stays the order's run; the retrying run is recorded by its own
  ``retry_requested`` row; every attempt row names the run that SENT it;
* legacy run-level reads attribute a Continue / retry order to its origin run (deliberate Phase
  20.1 behaviour; registration-aware run reads are deferred to the Phase 21 read models);
* the permanent CR-01 repro (post-fix write set AND the pre-fix re-parented data shape);
* no new uncertainty (a crashed retrying Job lists the order and is not execution_path_unproven);
* an order attributed to two flagged Jobs is listed once per Job and blocks under both;
* blocking is not loosened (order-less Job, reuse-only Job, rejected event, legacy order);
* one broker lookup per order per sync pass; statement bounds unchanged; no src statement
  reassigns an order's run.
"""

from __future__ import annotations

import ast
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from tests.support.operation_fixtures import seed_operation, seed_operation_job
from tests.support.paper_eligibility import allow_paper_execution
from tests.support.paper_execution_seams import allow_direct_paper_execution
from tests.support.query_counter import count_queries
from tests.support.recovery_agreement import assert_recovery_consumers_agree
from tests.support.recovery_fixtures import (
    OWNER,
    at,
    seed_account_run,
    seed_job,
    seed_operation_bound_intent,
    seed_paper_run,
)
from tests.test_paper_execution import migrated_paper_db  # noqa: F401  (database fixture)
from tests.test_paper_session_operations import (
    DEFAULT_BATCH,
    Gate,
    S1Broker,
    _continue_validate,
    _s1_world,
    _start,
    attempt_outcomes,
    continue_job,
    finish_jobs,
    intent_rows,
    manifest,
    operation_row,
    reclaim_job,
    run_continue,
    seed_batch,
    seed_clean_reconciliation_after_now,
    takeover,
    terminate_lock_holder,
)
from tests.test_recovery_predicate import LookupBroker
from tests.test_recovery_shared_classifier import (  # noqa: F401  (fixtures + helpers reuse)
    _a5_passed,
    _arrange,
    _gate,
    http,
    shared_db,
)

from trading_platform.core.settings import load_settings
from trading_platform.db.models import (
    AccountReconciliationRun,
    AttemptOutcomeClass,
    ExecutionEvent,
    Job,
    JobStatus,
    OrderEvent,
    OrderLifecycleState,
    OrderSubmissionAttempt,
    OrderTransitionOutcome,
    PaperOrder,
    RecoveryRecord,
    StrategyRun,
)
from trading_platform.db.models.order_event import OrderTransitionEventType
from trading_platform.db.session import session_scope
from trading_platform.services.execution.sync_orders import sync_account_state
from trading_platform.services.execution.transition import (
    OrderTransitionRequest,
    apply_order_transition,
)
from trading_platform.services.recovery import (
    AbsenceEvidenceItem,
    GateCode,
    RecoveryClassification,
    UnresolvedReason,
    account_recovery_status,
    strategy_recovery_status,
)


@pytest.fixture(autouse=True)
def _seams(monkeypatch: pytest.MonkeyPatch) -> None:
    """The shared run-time seam (historical session, stubbed window and manifest) the S1 flow
    of tests/test_paper_session_operations.py runs under."""

    allow_paper_execution(monkeypatch)
    allow_direct_paper_execution(monkeypatch)


def _accepted_registrations(session: Any, order_id: uuid.UUID) -> set[tuple[str, uuid.UUID]]:
    rows = session.execute(
        select(OrderEvent).where(
            OrderEvent.paper_order_id == order_id,
            OrderEvent.outcome == "accepted",
            OrderEvent.event_type.in_(
                [
                    OrderTransitionEventType.INTENT_REGISTERED,
                    OrderTransitionEventType.RETRY_REQUESTED,
                ]
            ),
        )
    ).scalars()
    return {(row.event_type.value, row.strategy_run_id) for row in rows}


def _continued_order_world() -> dict[str, Any]:
    """The S1 v2 flow: worker A's attempt 1 fails pre_connection, recovery establishes not_sent,
    a NEW executor (a Continue Job on its own run) retries the order and sends it once."""

    from tests.test_recovery_predicate import LookupBroker

    from trading_platform.services.execution.sync_orders import sync_account_state

    broker = S1Broker(script=["connect_error", "accept"], before_request=Gate())
    gate = broker.before_request
    assert gate is not None
    worker_a, _run = _s1_world(broker)
    gate.wait_arrived()
    operation = operation_row()
    (_intent1, cid1), _second = intent_rows()
    terminate_lock_holder()
    reclaim_job(operation.executor_job_id)
    acquisition, _job_b = takeover(operation.id)
    assert acquisition.paused
    gate.release.set()
    worker_a.join()
    finish_jobs()
    sync_account_state(settings=load_settings(), broker_client=LookupBroker())
    finish_jobs()
    seed_clean_reconciliation_after_now()
    assert _continue_validate(operation.id)["mode"] == "continue"

    continue_job_id = continue_job(operation.id)
    run_continue(broker.service(), operation_id=operation.id, job_id=continue_job_id)
    assert broker.received == {cid1: 1}  # POST count 1: sent once by the new executor

    with session_scope(load_settings()) as session:
        order = session.execute(
            select(PaperOrder).where(PaperOrder.client_order_id == cid1)
        ).scalar_one()
        continue_run_id = session.execute(
            select(StrategyRun.id).where(StrategyRun.job_id == continue_job_id)
        ).scalar_one()
        return {
            "order_id": order.id,
            "origin_run_id": order.strategy_run_id,
            "continue_run_id": continue_run_id,
            "continue_job_id": continue_job_id,
            "client_order_id": cid1,
        }


def test_continue_keeps_the_origin_run_and_records_the_retrying_run(  # noqa: F811
    migrated_paper_db: str,
) -> None:
    world = _continued_order_world()

    assert world["origin_run_id"] != world["continue_run_id"]
    with session_scope(load_settings()) as session:
        order = session.get(PaperOrder, world["order_id"])
        assert order is not None
        origin = session.get(StrategyRun, order.strategy_run_id)
        assert origin is not None and origin.id != world["continue_run_id"]  # the ORIGIN run
        registrations = _accepted_registrations(session, order.id)
        assert ("intent_registered", order.strategy_run_id) in registrations
        assert ("retry_requested", world["continue_run_id"]) in registrations
        attempts = session.execute(
            select(
                OrderSubmissionAttempt.attempt_number,
                OrderSubmissionAttempt.strategy_run_id,
                OrderSubmissionAttempt.executor_job_id,
            )
            .where(OrderSubmissionAttempt.paper_order_id == order.id)
            .order_by(OrderSubmissionAttempt.attempt_number)
        ).all()
    assert [a[0] for a in attempts] == [1, 2]
    assert attempts[0][1] == world["origin_run_id"]  # attempt 1 sent by worker A's run
    assert attempts[1][1] == world["continue_run_id"]  # attempt 2 sent by the Continue run
    assert attempts[1][2] == world["continue_job_id"]
    assert attempt_outcomes(world["client_order_id"]) == [(1, "pre_connection"), (2, "accepted")]


def test_run_reads_attribute_a_continued_order_to_its_origin_run(  # noqa: F811
    migrated_paper_db: str,
) -> None:
    """Deliberate Phase 20.1 behaviour (W5): the legacy run-level reads join through
    ``PaperOrder.strategy_run_id``, which is now immutable, so a Continue order belongs to its
    ORIGIN run and the Continue run shows 0 orders although it sent the order. Registration-aware
    run reads are deferred to the Phase 21 read models; this test must change with them."""

    from tests.test_paper_session_operations import STRATEGY

    from trading_platform.services.operator_reads import OperatorReadFilters, OperatorReadService

    world = _continued_order_world()
    reads = OperatorReadService(load_settings())

    origin = reads.get_run_detail(str(world["origin_run_id"]))
    continued = reads.get_run_detail(str(world["continue_run_id"]))
    assert origin["artifact_counts"]["paper_orders"] == 1
    assert continued["artifact_counts"]["paper_orders"] == 0
    listed = [
        item
        for item in reads.list_paper_orders(OperatorReadFilters(strategy_id=STRATEGY))
        if item["order_id"] == str(world["order_id"])
    ]
    assert len(listed) == 1
    assert listed[0]["run_id"] == str(world["origin_run_id"])


def test_first_registration_records_its_own_run(migrated_paper_db: str) -> None:  # noqa: F811
    """A create (first registration) still writes the registering run and an accepted
    intent_registered row on it."""

    run, _ = seed_batch(DEFAULT_BATCH[:1], manifest=manifest())
    _start(S1Broker().service(), risk_run_id=run)

    with session_scope(load_settings()) as session:
        order = session.execute(select(PaperOrder)).scalar_one()
        registrations = _accepted_registrations(session, order.id)
        origin = order.strategy_run_id
    assert registrations == {("intent_registered", origin)}


# ===========================================================================
# Recovery predicate: attribution by durable registration history (Task 2)
# ===========================================================================

PENDING = OrderLifecycleState.PENDING_SUBMISSION
FAILED = OrderLifecycleState.SUBMISSION_FAILED
UNKNOWN = OrderLifecycleState.UNKNOWN


@dataclass
class World:
    """The SAF-01 shape: a flagged Job J1 (run R1) whose operation-bound order was never sent,
    and the executor Job of the operation that may later retry it."""

    j1: uuid.UUID
    r1: uuid.UUID
    order_id: uuid.UUID
    executor: uuid.UUID
    operation_id: uuid.UUID
    r2: uuid.UUID | None = None


def _executor_and_operation(session: Any) -> tuple[Any, Any]:
    executor = seed_operation_job(
        session,
        status=JobStatus.RUNNING,
        lease_owner="worker-1",
        lease_expires_at=datetime(2100, 1, 1, tzinfo=UTC),
    )
    operation = seed_operation(
        session,
        state="running",
        reason=None,
        epoch=3,
        executor_job=executor,
        jobs=[(executor, "start")],
    )
    return executor, operation


def _saf01_world(session: Any, status: OrderLifecycleState) -> World:
    flagged = seed_job(session, completed_at=at(0))
    run = seed_paper_run(session, flagged)
    executor, operation = _executor_and_operation(session)
    order = seed_operation_bound_intent(
        session, run, status=status, attempts=(), operation=operation
    )
    return World(flagged.id, run.id, order.id, executor.id, operation.id)


def _registration_event(
    session: Any,
    *,
    order_id: uuid.UUID,
    run_id: uuid.UUID,
    event_type: OrderTransitionEventType,
    outcome: OrderTransitionOutcome = OrderTransitionOutcome.ACCEPTED,
    from_state: OrderLifecycleState = PENDING,
) -> None:
    session.add(
        OrderEvent(
            paper_order_id=order_id,
            strategy_run_id=run_id,
            from_state=from_state,
            to_state=PENDING,
            event_type=event_type,
            outcome=outcome,
            event_at=at(0),
            details={},
        )
    )
    session.flush()


def _reregister(session: Any, world: World) -> uuid.UUID:
    """Exactly the write set ``register()`` performs for ``retry_existing`` after the fix: a run
    for the executor Job and the accepted ``retry_requested`` transition on it; the order's own
    run is NOT touched."""

    executor = session.get(Job, world.executor)
    run = seed_paper_run(session, executor)
    apply_order_transition(
        world.order_id,
        OrderTransitionRequest(
            strategy_run_id=run.id,
            event_type=OrderTransitionEventType.RETRY_REQUESTED,
            details={"trigger_source": "continue"},
        ),
        session=session,
        settings=load_settings(),
    )
    world.r2 = run.id
    return run.id


def _flag(session: Any, job_id: uuid.UUID, *, completed_at: datetime) -> None:
    job = session.get(Job, job_id)
    job.status = JobStatus.FAILED
    job.outcome_uncertain = True
    job.completed_at = completed_at
    job.lease_owner = None
    job.lease_expires_at = None
    session.flush()


def _status(strategy_id: str = OWNER) -> Any:
    with session_scope(load_settings()) as session:
        return strategy_recovery_status(session, strategy_id)


def _entries(job_id: uuid.UUID, order_id: uuid.UUID | None = None) -> list[Any]:
    return [
        i
        for i in _status().intents
        if i.job_id == job_id and (order_id is None or i.intent_id == order_id)
    ]


def _unproven(job_id: uuid.UUID) -> bool:
    return any(
        i.intent_id is None and i.unresolved_reason is UnresolvedReason.EXECUTION_PATH_UNPROVEN
        for i in _entries(job_id)
    )


@pytest.mark.parametrize(
    "status", [PENDING, FAILED], ids=["pending_submission", "submission_failed"]
)
@pytest.mark.parametrize("shape", ["reregistered", "reparented_before_fix"])
def test_cr01_saf01_release_survives_reregistration(
    http: TestClient, status: OrderLifecycleState, shape: str
) -> None:
    """CR-01 (permanent repro). SAF-01: an operation-bound unsent order on a flagged Job is
    released by a fresh clean reconciliation. A Continue / retry Job that re-registers the order
    on its own run must not undo that release.

    ``reregistered`` applies the post-fix write set (the order keeps its origin run). The
    ``reparented_before_fix`` shape is the exact data the pre-fix ``register()`` left behind
    (the order on the retrying run, J1's registration only in its intent_registered event),
    seeded by INSERT so it stays valid under the 0029 trigger; it failed at the plan base."""

    if shape == "reregistered":
        world = _arrange(lambda s: _saf01_world(s, status))
        _arrange(lambda s: seed_account_run(s, completed_at=at(30)))
        assert _gate() is None and _a5_passed()  # the SAF-01 release
        _arrange(lambda s: _reregister(s, world))
    else:

        def build(session: Any) -> World:
            flagged = seed_job(session, completed_at=at(0))
            run1 = seed_paper_run(session, flagged)
            executor, operation = _executor_and_operation(session)
            run2 = seed_paper_run(session, executor)
            order = seed_operation_bound_intent(
                session, run2, status=status, attempts=(), operation=operation
            )
            _registration_event(
                session,
                order_id=order.id,
                run_id=run1.id,
                event_type=OrderTransitionEventType.INTENT_REGISTERED,
            )
            _registration_event(
                session,
                order_id=order.id,
                run_id=run2.id,
                event_type=OrderTransitionEventType.RETRY_REQUESTED,
                from_state=status,
            )
            return World(flagged.id, run1.id, order.id, executor.id, operation.id, run2.id)

        world = _arrange(build)
        _arrange(lambda s: seed_account_run(s, completed_at=at(30)))

    assert _gate() is None
    assert _a5_passed()
    assert not _unproven(world.j1)
    gate = assert_recovery_consumers_agree(
        http,
        strategy_id=OWNER,
        linked_job_ids=[world.j1],
        registering_flagged_job_ids=[world.j1],
        operation_id=world.operation_id,
    )
    assert gate is None
    body = http.get(f"/api/v1/jobs/{world.j1}/recovery").json()
    assert [(i["intent_id"], i["classification"], i["blocking"]) for i in body["intents"]] == [
        (str(world.order_id), "not_sent", False)
    ]
    assert body["gate_code"] is None


def test_crashed_retrying_job_is_attributed_not_execution_path_unproven(http: TestClient) -> None:
    """No new uncertainty: a Continue / retry Job that re-registered an order and crashed before
    T1 (flagged, zero attempts) lists the order (not_sent); it and the origin Job are NOT
    execution_path_unproven. The gate needs the NEWER clean reconciliation, then releases."""

    world = _arrange(lambda s: _saf01_world(s, PENDING))
    _arrange(lambda s: seed_account_run(s, completed_at=at(30)))
    _arrange(lambda s: _reregister(s, world))
    _arrange(lambda s: _flag(s, world.executor, completed_at=at(40)))

    history: list[GateCode | None] = []
    agree: dict[str, Any] = {
        "strategy_id": OWNER,
        "linked_job_ids": [world.j1, world.executor],
        "registering_flagged_job_ids": [world.j1, world.executor],
        "operation_id": world.operation_id,
    }
    history.append(assert_recovery_consumers_agree(http, **agree))
    for job_id in (world.j1, world.executor):
        assert not _unproven(job_id)
        listed = http.get(f"/api/v1/jobs/{job_id}/recovery").json()["intents"]
        assert [(i["intent_id"], i["classification"]) for i in listed] == [
            (str(world.order_id), "not_sent")
        ]
    _arrange(lambda s: seed_account_run(s, completed_at=at(50)))
    history.append(assert_recovery_consumers_agree(http, **agree))

    assert history == [GateCode.RECONCILIATION_REQUIRED, None]
    with session_scope(load_settings()) as session:
        runs = (
            session.execute(
                select(AccountReconciliationRun.completed_at).order_by(
                    AccountReconciliationRun.completed_at
                )
            )
            .scalars()
            .all()
        )
        executor_done = session.get(Job, world.executor).completed_at
    first, second = runs
    assert first < executor_done < second  # the two relations the gate history relies on


def _unestablished_two_job_world(session: Any, *, with_operation: bool) -> World:
    """An UNKNOWN order with an ambiguous attempt: origin on J1's run R1 (flagged), re-registered
    on the run(s) of a second flagged Job E (history rows) which also made the attempt."""

    flagged = seed_job(session, completed_at=at(0))
    run1 = seed_paper_run(session, flagged)
    executor, operation = _executor_and_operation(session)
    order = seed_operation_bound_intent(
        session,
        run1,
        status=UNKNOWN,
        attempts=(),
        operation=operation if with_operation else None,
    )
    # The ambiguous attempt was made by the second Job (attempt rows are complete-once: the
    # executor is set at INSERT).
    session.add(
        OrderSubmissionAttempt(
            paper_order_id=order.id,
            strategy_run_id=run1.id,
            attempt_number=1,
            started_at=at(1),
            completed_at=at(1),
            outcome_class=AttemptOutcomeClass.AMBIGUOUS.value,
            executor_job_id=executor.id,
        )
    )
    session.flush()
    run2 = seed_paper_run(session, executor)
    run3 = seed_paper_run(session, executor)  # a second run of the same Job: still listed once
    for run in (run2, run3):
        _registration_event(
            session,
            order_id=order.id,
            run_id=run.id,
            event_type=OrderTransitionEventType.RETRY_REQUESTED,
            from_state=UNKNOWN,
        )
    _flag(session, executor.id, completed_at=at(10))
    return World(flagged.id, run1.id, order.id, executor.id, operation.id, run2.id)


def test_order_linked_to_two_flagged_jobs_is_listed_once_per_job_and_blocks(
    http: TestClient,
) -> None:
    world = _arrange(lambda s: _unestablished_two_job_world(s, with_operation=True))
    _arrange(lambda s: seed_account_run(s, completed_at=at(30)))

    gate = assert_recovery_consumers_agree(
        http,
        strategy_id=OWNER,
        linked_job_ids=[world.j1, world.executor],
        registering_flagged_job_ids=[world.j1, world.executor],
        operation_id=world.operation_id,
    )

    assert gate is GateCode.OUTCOME_UNRESOLVED  # a genuinely ambiguous order blocks
    entries = [i for i in _status().intents if i.intent_id == world.order_id]
    assert sorted(str(i.job_id) for i in entries) == sorted(
        [str(world.j1), str(world.executor)]
    )  # once per Job, never twice for one Job, never in the unflagged branch (job_id set)
    assert all(i.blocking for i in entries)
    for job_id in (world.j1, world.executor):
        body = http.get(f"/api/v1/jobs/{job_id}/recovery").json()
        assert body["gate_code"] == "outcome_unresolved"
        assert [i["intent_id"] for i in body["intents"]] == [str(world.order_id)]


def test_job_without_any_order_linkage_stays_execution_path_unproven(http: TestClient) -> None:
    """Blocking is not loosened (user decision 4; open decision OD-1): a flagged paper-session
    Job whose runs neither originate, register nor retry any order stays
    unresolved(execution_path_unproven) today; a broker-order-sync Job stays nothing_submitted."""

    def build(session: Any) -> tuple[uuid.UUID, uuid.UUID]:
        flagged = seed_job(session, completed_at=at(0))
        seed_paper_run(session, flagged)
        sync = seed_job(session, job_type="broker-order-sync", completed_at=at(0))
        return flagged.id, sync.id

    j1, sync_job = _arrange(build)
    _arrange(lambda s: seed_account_run(s, completed_at=at(30)))

    assert _unproven(j1)
    assert [i.classification for i in _entries(sync_job)] == [
        RecoveryClassification.NOTHING_SUBMITTED
    ]
    assert _gate() is GateCode.OUTCOME_UNRESOLVED
    assert (
        assert_recovery_consumers_agree(http, strategy_id=OWNER, linked_job_ids=[j1, sync_job])
        is GateCode.OUTCOME_UNRESOLVED
    )


def test_reuse_only_job_stays_execution_path_unproven(http: TestClient) -> None:
    """A flagged Job whose run only REUSED an existing order (reuse_existing writes an
    ExecutionEvent ``paper_order_reused`` and NO order_events row) registered nothing: the
    absence of an order alone is not proof nothing could be sent (OD-1 is open, not approved)."""

    def build(session: Any) -> tuple[uuid.UUID, uuid.UUID]:
        flagged = seed_job(session, completed_at=at(0))
        run1 = seed_paper_run(session, flagged)
        origin_job = seed_job(session, uncertain=False, status=JobStatus.SUCCEEDED)
        run_origin = seed_paper_run(session, origin_job)
        order = seed_operation_bound_intent(session, run_origin, status=PENDING, attempts=())
        session.add(
            ExecutionEvent(
                strategy_run_id=run1.id,
                paper_order_id=order.id,
                event_type="paper_order_reused",
                severity="info",
                blocks_execution=False,
                event_at=at(0),
                message="Reused existing intent; no new submission was attempted.",
                details={},
            )
        )
        session.flush()
        return flagged.id, order.id

    j1, order_id = _arrange(build)
    _arrange(lambda s: seed_account_run(s, completed_at=at(30)))

    assert _unproven(j1)
    assert _entries(j1, order_id) == []
    assert _gate() is GateCode.OUTCOME_UNRESOLVED


def test_rejected_registration_event_does_not_attribute(http: TestClient) -> None:
    """Only ACCEPTED intent_registered / retry_requested rows attribute an order to a run."""

    def build(session: Any) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
        flagged = seed_job(session, completed_at=at(0))
        run1 = seed_paper_run(session, flagged)
        origin_job = seed_job(session, uncertain=False, status=JobStatus.SUCCEEDED)
        run_origin = seed_paper_run(session, origin_job)
        order = seed_operation_bound_intent(session, run_origin, status=PENDING, attempts=())
        _registration_event(
            session,
            order_id=order.id,
            run_id=run1.id,
            event_type=OrderTransitionEventType.RETRY_REQUESTED,
            outcome=OrderTransitionOutcome.REJECTED,
        )
        return flagged.id, run1.id, order.id

    j1, run1, order_id = _arrange(build)
    _arrange(lambda s: seed_account_run(s, completed_at=at(30)))

    assert _unproven(j1) and _entries(j1, order_id) == []
    # control: the same row, accepted, attributes the order to J1 and releases it
    _arrange(
        lambda s: _registration_event(
            s,
            order_id=order_id,
            run_id=run1,
            event_type=OrderTransitionEventType.RETRY_REQUESTED,
        )
    )
    assert not _unproven(j1)
    assert [i.classification for i in _entries(j1, order_id)] == [RecoveryClassification.NOT_SENT]
    assert _gate() is None


def test_status_reads_keep_two_statements_with_history_arm(shared_db: str) -> None:
    world = _arrange(lambda s: _unestablished_two_job_world(s, with_operation=True))
    del world
    with session_scope(load_settings()) as session:
        with count_queries(session) as strategy_counter:
            strategy_recovery_status(session, OWNER)
        with count_queries(session) as account_counter:
            account_recovery_status(session)
    assert strategy_counter.count == 2
    assert account_counter.count == 2


def _lookup_world(shape: str) -> World:
    if shape == "unestablished":
        return _arrange(lambda s: _unestablished_two_job_world(s, with_operation=True))

    def build(session: Any) -> World:
        # UNKNOWN, attempt history proves not sent (pre_connection), operation-bound: the
        # liveness branch returns it to submission_failed. Attributed to J1 (origin) and E.
        flagged = seed_job(session, completed_at=at(0))
        run1 = seed_paper_run(session, flagged)
        executor, operation = _executor_and_operation(session)
        order = seed_operation_bound_intent(
            session,
            run1,
            status=UNKNOWN,
            attempts=(AttemptOutcomeClass.PRE_CONNECTION,),
            operation=operation,
        )
        run2 = seed_paper_run(session, executor)
        _registration_event(
            session,
            order_id=order.id,
            run_id=run2.id,
            event_type=OrderTransitionEventType.RETRY_REQUESTED,
            from_state=UNKNOWN,
        )
        _flag(session, executor.id, completed_at=at(10))
        return World(flagged.id, run1.id, order.id, executor.id, operation.id, run2.id)

    return _arrange(build)


@pytest.mark.parametrize("shape", ["unestablished", "unknown_proven_not_sent"])
def test_sync_assessment_looks_up_an_order_once_across_two_flagged_jobs(
    shared_db: str, shape: str
) -> None:
    """W6: an order attributed to two flagged Jobs is looked up, transitioned and evidenced ONCE
    per sync pass (the evidence is keyed by the order, so both Jobs' R3 see it), with zero POST."""

    world = _lookup_world(shape)
    broker = LookupBroker()

    sync_account_state(settings=load_settings(), broker_client=broker)

    assert broker.post_count == 0
    with session_scope(load_settings()) as session:
        a_records = session.execute(
            select(func.count())
            .select_from(RecoveryRecord)
            .where(
                RecoveryRecord.paper_order_id == world.order_id,
                RecoveryRecord.evidence_item == AbsenceEvidenceItem.A_CLIENT_ORDER_ID_404.value,
            )
        ).scalar_one()
        transitions = session.execute(
            select(func.count())
            .select_from(OrderEvent)
            .where(
                OrderEvent.paper_order_id == world.order_id,
                OrderEvent.event_type == OrderTransitionEventType.SUBMISSION_FAILED,
            )
        ).scalar_one()
        status = session.get(PaperOrder, world.order_id).status
    if shape == "unestablished":
        assert broker.lookup_calls == 1
        assert a_records == 1
        assert transitions == 0
        assert status is UNKNOWN
    else:
        assert broker.lookup_calls == 0  # proven not sent: nothing to look up
        assert transitions == 1  # exactly one liveness transition
        assert status is FAILED


def test_no_src_statement_reassigns_an_order_run() -> None:
    """CR-01 source pin: no statement in src assigns ``<x>.strategy_run_id`` (an order's origin
    run is immutable). The 0029 trigger of plan 20.1-27 is the second line of defence."""

    root = Path(__file__).resolve().parents[1] / "src" / "trading_platform"
    offenders: list[str] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AugAssign | ast.AnnAssign):
                targets = [node.target]
            else:
                continue
            for target in targets:
                for sub in ast.walk(target):
                    if isinstance(sub, ast.Attribute) and sub.attr == "strategy_run_id":
                        offenders.append(f"{path.relative_to(root)}:{node.lineno}")
    assert offenders == [], f"an order's run is reassigned at: {offenders}"
