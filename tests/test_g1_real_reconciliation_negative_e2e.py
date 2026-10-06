"""G-1 on the REAL path, the NEGATIVE half: what must stay blocked (plan 20.1-36).

Sources: the user's decision 2 of 2026-10-06 and the rule decided the same day (D-G1-A): only a
PROVEN_NOT_SENT or a definitive REJECTED verdict explains why the broker does not report an unmatched
pre-send order; every other shape keeps its finding (UNESTABLISHED, BROKER_EVIDENCE) and every
broker-side finding is untouched. ``tests/test_g1_real_reconciliation_release_e2e.py`` proves the
positive half (a released order is sent once, a rejection is never re-sent); this module proves that
nothing else gets released, through REAL standalone reconciliations and the normal entry points.

* legacy order (no operation intent, zero attempts: pending_submission with count 0 or 1, and
  submission_failed): MISSING_BROKER in BOTH scopes, the gate stays ``outcome_unresolved`` (TL-4) and
  a Start through ``run_paper_session`` is refused before any broker read or POST;
* unfinished submission (the product null-outcome shape: T1 committed, attempt (1, NULL), nothing
  sent): MISSING_BROKER in both scopes, gate ``outcome_unresolved``, Continue and a Start refused;
* ambiguous submission (the product read-timeout shape: attempt (1, ambiguous), order UNKNOWN, one
  POST reached the broker): a reconciliation never resolves it, whatever the broker shows; the POST
  count stays 1;
* unexpected broker activity is never hidden by the G-1 suppression: an unrecognized broker order
  next to a released order, a broker order that carries the released order's client_order_id but
  another quantity or symbol, and a broker order for a RECORDED REJECTION;
* permission is not resolution: another strategy's unexplained order dirties every account
  reconciliation, which keeps A6 (and, for a legacy order, A5) failing, while a reconciliation of
  the OWNER's scope restores only the owner's permission.

Harness rules of the release module apply: time is WALL-CLOCK, every standalone reconciliation is the
real service of ``tests/support/real_reconciliation.py`` and NO reconciliation result is ever written
by a test. The legacy shapes and the other strategy's orders cannot be produced by the current
product (every new order is operation-bound), so those ORDERS are inserted with the existing legacy
helpers; the unfinished and ambiguous shapes come from the product path (a held worker, a read
timeout) and the SAF-01 and recorded-rejection worlds from the builders of 20.1-29 / 20.1-34. Every
fresh evaluation here has no recorded basis (``verified=False``): each refusal below comes from the
recovery gate, which precedes basis verification at both Start entry points, and an evaluation basis
would write a sync Job (a broker effect) and a reconciliation row of its own. Each Start observes
(never replaces) the D-15 session gate. The read side is a scripted broker; the send side is the
product client over ``httpx.MockTransport``; nothing reaches the network.

Continue is exercised through its submit-time gate only: a refused Continue never gets a Job in
production, and running ``run_paper_continuation`` over a world whose attempt is in doubt would take
S1 authority over it and change the very state under test. The one Continue that runs (the last
test) is the permitted one. The CR-01 interim runbook prohibition on Continue / Retry is NOT lifted
by this module.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from tests.support.real_reconciliation import (
    WallClockTimeline,
    run_real_account_reconciliation,
    run_real_strategy_reconciliation,
    scripted_read_broker,
)
from tests.support.recovery_agreement import assert_recovery_consumers_agree
from tests.test_cr01_ambiguity_and_agreement_e2e import _null_outcome, _read_timeout
from tests.test_cr01_reuse_e2e import (  # noqa: F401  (http and _seams are fixtures)
    PENDING,
    _a5_passed,
    _order,
    _seams,
    _start_job,
    http,
)
from tests.test_g1_real_reconciliation_release_e2e import (
    SCOPES,
    _findings_of,
    _GateSpy,
)
from tests.test_paper_execution import (  # noqa: F401  (migrated_paper_db is the database fixture)
    _CountingBrokerClient,
    _seed_approved_risk_batch,
    _seed_existing_paper_order,
    migrated_paper_db,
)
from tests.test_paper_session_operations import (
    DEFAULT_BATCH,
    SESSION,
    STRATEGY,
    S1Broker,
    _conflict,
    _continue_conflict,
    attempt_outcomes,
    count,
    evaluation,
)
from tests.test_reconciliation_shared_evidence import _broker_twin

from trading_platform.core import clock
from trading_platform.core.settings import load_settings
from trading_platform.db.models import (
    ExecutionOperationIntent,
    OrderLifecycleState,
    OrderSubmissionAttempt,
    PaperOrder,
)
from trading_platform.db.session import session_scope
from trading_platform.services.execution.submit_orders import run_paper_session
from trading_platform.services.recovery import GateCode, strategy_recovery_status

#: The legacy shapes (an order with no operation intent and no attempt row): (status, count).
LEGACY_SHAPES = {
    "pending_count0": ("pending_submission", 0),
    "pending_count1": ("pending_submission", 1),
    "failed": ("submission_failed", 1),
}
LEGACY_MATRIX = [
    pytest.param(scope, shape, id=f"{scope}-{shape}") for scope in SCOPES for shape in LEGACY_SHAPES
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reconcile(scope: str, broker: Any) -> tuple[Any, uuid.UUID]:
    """One REAL standalone reconciliation of ``scope`` (the strategy scope is STRATEGY / SESSION).
    Returns the report and the id of the ``reconciliation`` Job that produced it."""

    if scope == "account":
        return run_real_account_reconciliation(broker=broker)
    return run_real_strategy_reconciliation(
        strategy_id=STRATEGY, as_of_session=SESSION, broker=broker
    )


def _findings_for(report: Any, order_id: uuid.UUID) -> list[dict[str, Any]]:
    """The findings of either report shape that name ``order_id`` (as plain dicts)."""

    return [f for f in _findings_of(report) if f.get("paper_order_id") == str(order_id)]


def _finding_types(report: Any, order_id: uuid.UUID) -> list[str]:
    return [f["event_type"] for f in _findings_for(report, order_id)]


def _post_count(broker: S1Broker) -> int:
    """The number of POSTs that reached the (mock) broker."""

    return sum(broker.received.values())


def _gate(strategy_id: str = STRATEGY) -> GateCode | None:
    """The recovery gate of one strategy, read from the database now."""

    with session_scope(load_settings()) as session:
        gate: GateCode | None = strategy_recovery_status(
            session, strategy_id, now=clock.now_utc()
        ).gate_code
    return gate


def _row_counts() -> tuple[int, int]:
    """(paper_orders, order_submission_attempts): a refused Start may add neither."""

    return count(PaperOrder), count(OrderSubmissionAttempt)


def _counting_read_broker() -> _CountingBrokerClient:
    """A read-side broker that records every call: a D-15 refusal makes none."""

    return _CountingBrokerClient(
        orders=[], fills=[], positions=[], account=scripted_read_broker().get_account()
    )


def _start_session(risk_run: uuid.UUID, *, broker: S1Broker, read_broker: Any) -> Any:
    """A Start through the SESSION entry point ``run_paper_session``, linked to its own Job."""

    return run_paper_session(
        STRATEGY,
        as_of_session=SESSION,
        risk_run_id=str(risk_run),
        trigger_source="pytest",
        settings=load_settings(),
        execution_service=broker.service(),
        broker_client=read_broker,
        job_id=_start_job(risk_run),
    )


def _fresh_evaluation_without_basis() -> uuid.UUID:
    """A fresh evaluation of the same session (same symbol and size as the batch of every world
    here), without a recorded basis: see the module docstring."""

    return evaluation(DEFAULT_BATCH[:1], as_of="2024-01-08", verified=False)


# ---------------------------------------------------------------------------
# Test 1: a legacy order keeps its finding and blocks a Start
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("scope", "legacy_shape"), LEGACY_MATRIX)
def test_legacy_order_keeps_its_finding_and_blocks(
    migrated_paper_db: str,
    monkeypatch: pytest.MonkeyPatch,
    scope: str,
    legacy_shape: str,
) -> None:
    status, attempt_count = LEGACY_SHAPES[legacy_shape]
    risk_run, events = _seed_approved_risk_batch()
    _seed_existing_paper_order(
        risk_run_id=risk_run,
        risk_event_id=events["AAPL"],
        symbol="AAPL",
        session_date=SESSION,
        status=status,
        broker_order_id=None,
        broker_status=None,
        submission_attempt_count=attempt_count,
    )
    with session_scope(load_settings()) as session:
        order_id = session.execute(select(PaperOrder.id)).scalar_one()

    # the legacy shape itself: no attempt row, no operation intent, no broker id (TL-4)
    assert (count(OrderSubmissionAttempt), count(ExecutionOperationIntent)) == (0, 0)
    order = _order(order_id)
    assert (order.status.value, order.broker_order_id, order.submission_attempt_count) == (
        status,
        None,
        attempt_count,
    )
    assert _gate() is GateCode.OUTCOME_UNRESOLVED  # unestablished before any reconciliation

    # the REAL reconciliation of either scope reports it, and the finding blocks
    report, _job_id = _reconcile(scope, scripted_read_broker())
    assert _finding_types(report, order_id) == ["MISSING_BROKER"], (scope, legacy_shape)
    (finding,) = _findings_for(report, order_id)
    assert finding["details"]["submission_evidence"] == "unestablished"
    assert finding["blocks_execution"] is True
    assert report.blocks_execution is True
    # a reconciliation never releases it (the verdict, not the report, decides)
    assert _gate() is GateCode.OUTCOME_UNRESOLVED
    assert not _a5_passed()

    # a Start through the session entry point is refused before any broker read or POST
    orders_before, attempts_before = _row_counts()
    broker, read_broker = S1Broker(), _counting_read_broker()
    spy = _GateSpy(monkeypatch)
    started = _start_session(risk_run, broker=broker, read_broker=read_broker)
    assert spy.answers == ["outcome_unresolved"], spy.answers
    assert started.action == "blocked_outcome_unresolved"
    assert read_broker.calls == []  # zero broker reads
    assert broker.received == {} and _post_count(broker) == 0  # zero POST
    assert _row_counts() == (orders_before, attempts_before)  # no new order, no new attempt
    after = _order(order_id)
    assert (after.status.value, after.broker_order_id, after.submission_attempt_count) == (
        status,
        None,
        attempt_count,
    )


# ---------------------------------------------------------------------------
# Test 2: an unfinished submission keeps its finding and blocks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scope", SCOPES)
def test_unfinished_submission_keeps_its_finding_and_blocks(
    http: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    record_property: Any,
    scope: str,
) -> None:
    """The product null-outcome shape: the worker committed T1 (attempt (1, NULL)) and was held
    before its request reached the broker, then its lease lapsed and the Job was reclaimed. The
    held worker is released (``amb.release``) in the ``finally``, so the thread always ends while
    the database still exists."""

    amb = _null_outcome(WallClockTimeline())
    try:
        order_id, op, cid1 = amb.order_id, amb.operation_id, amb.cid1
        assert _order(order_id).status.value == PENDING
        assert attempt_outcomes(cid1) == [(1, None)]
        assert amb.broker.received == {}

        report, _job_id = _reconcile(scope, scripted_read_broker())
        assert _finding_types(report, order_id) == ["MISSING_BROKER"], scope
        (finding,) = _findings_for(report, order_id)
        assert finding["details"]["submission_evidence"] == "unestablished"
        assert finding["blocks_execution"] is True
        assert report.blocks_execution is True

        gate = assert_recovery_consumers_agree(
            http,
            strategy_id=STRATEGY,
            linked_job_ids=amb.linked,
            registering_flagged_job_ids=amb.linked,
            operation_id=op,
        )
        assert gate is GateCode.OUTCOME_UNRESOLVED
        assert not _a5_passed()
        assert _continue_conflict(op).code == "outcome_unresolved"

        # a new Start: refused at submit time and refused by the session entry point
        new_run = _fresh_evaluation_without_basis()
        code = _conflict(new_run).code
        record_property("start_validation_code", code)
        assert code in {"outcome_unresolved", "operation_open"}, code
        start_broker, read_broker = S1Broker(), _counting_read_broker()
        spy = _GateSpy(monkeypatch)
        started = _start_session(new_run, broker=start_broker, read_broker=read_broker)
        record_property("start_session_gate", ",".join(str(a) for a in spy.answers))
        assert spy.answers == ["outcome_unresolved"], spy.answers
        assert started.action == "blocked_outcome_unresolved"
        assert read_broker.calls == [] and start_broker.received == {}
        assert amb.broker.received == {}  # zero POST while the held worker is still held
        assert attempt_outcomes(cid1) == [(1, None)]
    finally:
        amb.release()

    # the held worker's OWN, already authorized request reaches the broker after the release and
    # completes ``ambiguous``; nothing a test did sent it, and the gate stays blocked
    assert amb.broker.received == {amb.cid1: 1}
    assert attempt_outcomes(amb.cid1) == [(1, "ambiguous")]
    assert _gate() is GateCode.OUTCOME_UNRESOLVED


# ---------------------------------------------------------------------------
# Test 3: an ambiguous submission is never resolved by a reconciliation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("visibility", ["broker_shows_order", "broker_shows_nothing"])
def test_ambiguous_submission_never_reconciles_clean(
    http: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    record_property: Any,
    visibility: str,
) -> None:
    """The product read-timeout shape: the POST reached the broker and its answer never arrived
    (attempt (1, ambiguous), order UNKNOWN, one POST). With the order visible at the broker the
    reconciliation reports a STATE_MISMATCH; with nothing visible it reports nothing, exactly as
    before G-1 (UNKNOWN is not an active status). Either way the gate stays ``outcome_unresolved``
    and the POST count stays 1."""

    amb = _read_timeout(WallClockTimeline())
    order_id, op, cid1, broker = amb.order_id, amb.operation_id, amb.cid1, amb.broker
    assert _order(order_id).status is OrderLifecycleState.UNKNOWN
    assert broker.received == {cid1: 1} and _post_count(broker) == 1
    assert attempt_outcomes(cid1) == [(1, "ambiguous")]
    assert _gate() is GateCode.OUTCOME_UNRESOLVED

    twin = _broker_twin(order_id) if visibility == "broker_shows_order" else None
    read_broker = scripted_read_broker(orders=[twin] if twin is not None else [])
    report, _job_id = run_real_account_reconciliation(broker=read_broker)
    findings = _findings_for(report, order_id)
    if twin is not None:
        assert [f["event_type"] for f in findings] == ["STATE_MISMATCH"]
        assert findings[0]["blocks_execution"] is True
        assert findings[0]["details"]["broker_order_id"] == twin.broker_order_id
        assert report.blocks_execution is True
    else:
        assert findings == []  # UNKNOWN is not an active status: nothing is reported, as before
        # observed: the whole report is CLEAN, and a clean report still resolves nothing (below)
        assert report.blocks_execution is False and report.finding_count == 0
    record_property("account_report_blocks", str(report.blocks_execution))

    # whatever the reconciliation found, the verdict decides: it is not resolved
    gate = assert_recovery_consumers_agree(
        http,
        strategy_id=STRATEGY,
        linked_job_ids=amb.linked,
        registering_flagged_job_ids=amb.linked,
        operation_id=op,
    )
    assert gate is GateCode.OUTCOME_UNRESOLVED
    assert not _a5_passed()
    assert _continue_conflict(op).code == "outcome_unresolved"

    new_run = _fresh_evaluation_without_basis()
    code = _conflict(new_run).code
    record_property("start_validation_code", code)
    assert code in {"outcome_unresolved", "operation_open"}, code
    start_broker, start_read_broker = S1Broker(), _counting_read_broker()
    spy = _GateSpy(monkeypatch)
    started = _start_session(new_run, broker=start_broker, read_broker=start_read_broker)
    record_property("start_session_gate", ",".join(str(a) for a in spy.answers))
    assert spy.answers == ["outcome_unresolved"], spy.answers
    assert started.action == "blocked_outcome_unresolved"
    assert start_read_broker.calls == [] and start_broker.received == {}

    # the POST count stays 1, the attempt log is unchanged, the order was never bound
    assert broker.received == {cid1: 1} and _post_count(broker) == 1
    assert attempt_outcomes(cid1) == [(1, "ambiguous")]
    assert _order(order_id).broker_order_id is None
    assert _gate() is GateCode.OUTCOME_UNRESOLVED
