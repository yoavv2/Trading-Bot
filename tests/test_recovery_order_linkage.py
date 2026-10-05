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

import uuid
from typing import Any

import pytest
from sqlalchemy import select
from tests.support.paper_eligibility import allow_paper_execution
from tests.support.paper_execution_seams import allow_direct_paper_execution
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

from trading_platform.core.settings import load_settings
from trading_platform.db.models import (
    OrderEvent,
    OrderSubmissionAttempt,
    PaperOrder,
    StrategyRun,
)
from trading_platform.db.models.order_event import OrderTransitionEventType
from trading_platform.db.session import session_scope


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
