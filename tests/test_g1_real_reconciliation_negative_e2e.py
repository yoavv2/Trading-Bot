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

import dataclasses
import uuid
from collections.abc import Sequence
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from tests.support.real_reconciliation import (
    WallClockTimeline,
    reconciliation_completed_at,
    run_real_account_reconciliation,
    run_real_strategy_reconciliation,
    scripted_read_broker,
)
from tests.support.recovery_agreement import assert_recovery_consumers_agree
from tests.support.recovery_fixtures import OTHER, seed_intent, seed_paper_run, strategy_row
from tests.test_cr01_ambiguity_and_agreement_e2e import _null_outcome, _read_timeout
from tests.test_cr01_reuse_e2e import (  # noqa: F401  (http and _seams are fixtures)
    PENDING,
    _a5_passed,
    _agree,
    _order,
    _saf01_shape,
    _seams,
    _start_job,
    http,
)
from tests.test_g1_real_reconciliation_release_e2e import (
    REJECTION_SCOPES,
    SAF01_MATRIX,
    SCOPES,
    _findings_of,
    _GateSpy,
    _order_event_count,
    _recorded_rejection_shape,
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
    _continue_validate,
    attempt_outcomes,
    continue_job,
    count,
    evaluation,
    run_continue,
)
from tests.test_reconciliation_shared_evidence import (
    _attribution,
    _broker_twin,
    _unrecognized_broker_order,
)

from trading_platform.core import clock
from trading_platform.core.settings import load_settings
from trading_platform.db.models import (
    ExecutionOperationIntent,
    OrderLifecycleState,
    OrderSubmissionAttempt,
    PaperOrder,
)
from trading_platform.db.session import session_scope
from trading_platform.services.alpaca import BrokerOrderSnapshot
from trading_platform.services.execution.operations import end_operation
from trading_platform.services.execution.submit_orders import run_paper_session
from trading_platform.services.paper_account_checks import (
    AccountChecks,
    CheckId,
    CheckReason,
    CheckResult,
    EvidenceKind,
    _check_a5,
    _check_a6,
    _latest_completed_account_run,
    evaluate_account_checks,
)
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


def _counting_read_broker(orders: Sequence[BrokerOrderSnapshot] = ()) -> _CountingBrokerClient:
    """A read-side broker that records every call: a D-15 refusal makes none."""

    return _CountingBrokerClient(
        orders=list(orders), fills=[], positions=[], account=scripted_read_broker().get_account()
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
    """A legacy order (no operation intent, no attempt row, no broker id) is reported by the REAL
    reconciliation of either scope, keeps the gate at ``outcome_unresolved`` (TL-4: no reconciliation
    releases it) and a Start through ``run_paper_session`` is refused before any broker read."""

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


# ---------------------------------------------------------------------------
# Tests 4 to 6: unexpected broker activity is never hidden by the G-1 suppression
# ---------------------------------------------------------------------------

MISMATCH_MATRIX = [
    pytest.param(scope, mismatch, id=f"{scope}-{mismatch}")
    for scope in SCOPES
    for mismatch in ("quantity", "symbol")
]


def _end_operation(operation_id: uuid.UUID, reason: str) -> None:
    """End (M12) the operation: the unsent intents are cancelled, nothing is released (D-20)."""

    with session_scope(load_settings()) as session:
        end_operation(session, operation_id, operator_reason=reason, actor="pytest")


def _assert_start_refused_by_the_gate(
    record_property: Any,
    monkeypatch: pytest.MonkeyPatch,
    *,
    broker: S1Broker,
    broker_orders: Sequence[BrokerOrderSnapshot],
    gate: GateCode,
) -> None:
    """After End, a fresh evaluation of the same session is refused at BOTH Start entry points by
    the recovery gate (``gate``), with zero broker reads, zero POST and no new order or attempt row.
    The send side is ``broker`` itself, so any POST would show in its ``received``."""

    assert _gate() is gate  # End never releases (D-20)
    new_run = _fresh_evaluation_without_basis()
    code = _conflict(new_run).code
    record_property("start_validation_code", code)
    assert code == gate.value, code
    posts_before, rows_before = dict(broker.received), _row_counts()
    read_broker = _counting_read_broker(broker_orders)
    spy = _GateSpy(monkeypatch)
    started = _start_session(new_run, broker=broker, read_broker=read_broker)
    record_property("start_session_action", started.action)
    assert spy.answers == [gate.value], spy.answers
    assert started.action in {"blocked_outcome_unresolved", "blocked_reconciliation"}, (
        started.action
    )
    assert read_broker.calls == []
    assert broker.received == posts_before and _row_counts() == rows_before


@pytest.mark.parametrize(("scope", "status"), SAF01_MATRIX)
def test_unrecognized_broker_order_blocks_the_release(
    http: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    record_property: Any,
    scope: str,
    status: str,
) -> None:
    """A REAL reconciliation that would release the SAF-01 order is not clean when the broker also
    holds an order nobody registered (non-platform client id, another symbol): the released order
    is not reported, the unexpected activity IS, the report blocks and the gate reads
    ``reconciliation_not_clean``; Continue and a Start stay refused with zero POST."""

    world = _saf01_shape(status, monkeypatch, WallClockTimeline())
    j1, order_id, cid1, op, broker = (
        world.j1,
        world.order_id,
        world.cid1,
        world.operation_id,
        world.broker,
    )
    assert _order(order_id).status.value == status
    assert attempt_outcomes(cid1) == [] and broker.received == {}
    assert _gate() is GateCode.RECONCILIATION_REQUIRED  # it only awaits a clean reconciliation

    unknown = _unrecognized_broker_order()
    report, _job_id = _reconcile(scope, scripted_read_broker(orders=[unknown]))

    # G-1 still explains the released order ...
    assert _finding_types(report, order_id) == [], (scope, status)
    # ... but the unexpected broker activity is reported, blocks, and is listed as unrecognized
    missing_local = [
        f
        for f in _findings_of(report)
        if f["event_type"] == "MISSING_LOCAL"
        and f["details"].get("broker_order_id") == unknown.broker_order_id
    ]
    unrecognized = [item["broker_order_id"] for item in _attribution(report)["unrecognized_orders"]]
    record_property("missing_local_findings", str(len(missing_local)))
    record_property("unrecognized_orders", str(len(unrecognized)))
    assert (missing_local and missing_local[0]["blocks_execution"] is True) or (
        unknown.broker_order_id in unrecognized
    )
    assert report.blocks_execution is True

    # the gate: the newest qualifying run after J1's effect is this dirty one
    gate = assert_recovery_consumers_agree(http, **_agree(world, linked=[j1], flagged=[j1]))
    assert gate is GateCode.RECONCILIATION_NOT_CLEAN
    assert not _a5_passed()
    assert _continue_conflict(op).code == "reconciliation_not_clean"

    # after End a new Start is refused as well; nothing was ever POSTed
    _end_operation(op, "g1 unrecognized broker order")
    _assert_start_refused_by_the_gate(
        record_property,
        monkeypatch,
        broker=broker,
        broker_orders=[unknown],
        gate=GateCode.RECONCILIATION_NOT_CLEAN,
    )
    assert broker.received == {} and attempt_outcomes(cid1) == []
    assert _order(order_id).broker_order_id is None


@pytest.mark.parametrize(("scope", "mismatch"), MISMATCH_MATRIX)
def test_identity_mismatch_on_the_released_client_order_id_is_never_hidden(
    http: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    record_property: Any,
    scope: str,
    mismatch: str,
) -> None:
    """The broker holds an order with the released order's client_order_id but another quantity or
    symbol. The order is MATCHED by its client_order_id (never MISSING_BROKER), the report blocks,
    the gate reads ``reconciliation_not_clean`` and nothing is bound or POSTed.

    What this proves, and what it does not: a standalone reconciliation never binds, and the Start
    entry point is refused by the D-15 gate BEFORE its in-session recovery pass, so no binder runs
    on this path and ``broker_order_id`` stays None because of that ordering. The D-07 identity
    check of the binders is pinned at the service level (``tests/test_reconciliation_shared_evidence``,
    20.1-35); it is not what is exercised here."""

    world = _saf01_shape(PENDING, monkeypatch, WallClockTimeline())
    j1, order_id, cid1, op, broker = (
        world.j1,
        world.order_id,
        world.cid1,
        world.operation_id,
        world.broker,
    )
    overrides = {"quantity": "11"} if mismatch == "quantity" else {"symbol": "MSFT"}
    twin = _broker_twin(order_id, **overrides)
    assert twin.client_order_id == cid1
    assert (twin.quantity, twin.symbol) != (_order(order_id).quantity, "AAPL")

    report, _job_id = _reconcile(scope, scripted_read_broker(orders=[twin]))

    # matched, not missing; the report blocks; record which mechanism blocks (either is enough)
    types = _finding_types(report, order_id)
    assert "MISSING_BROKER" not in types, (scope, mismatch, types)
    state_mismatch = [
        f for f in _findings_for(report, order_id) if f["event_type"] == "STATE_MISMATCH"
    ]
    anomalies = [
        a["anomaly"]
        for a in _attribution(report)["anomalies"]
        if a["broker_order_id"] == twin.broker_order_id
    ]
    record_property("finding_types", ",".join(types))
    record_property("attribution_anomalies", ",".join(anomalies))
    assert (state_mismatch and state_mismatch[0]["blocks_execution"] is True) or anomalies
    assert report.blocks_execution is True
    assert _order(order_id).broker_order_id is None  # a reconciliation never binds

    gate = assert_recovery_consumers_agree(http, **_agree(world, linked=[j1], flagged=[j1]))
    assert gate is GateCode.RECONCILIATION_NOT_CLEAN
    assert not _a5_passed()
    assert _continue_conflict(op).code == "reconciliation_not_clean"

    _end_operation(op, "g1 identity mismatch")
    _assert_start_refused_by_the_gate(
        record_property,
        monkeypatch,
        broker=broker,
        broker_orders=[twin],
        gate=GateCode.RECONCILIATION_NOT_CLEAN,
    )
    assert broker.received == {} and attempt_outcomes(cid1) == []  # zero POST, no new attempt
    unbound = _order(order_id)
    assert unbound.broker_order_id is None and unbound.broker_status is None
    assert unbound.status.value == PENDING


@pytest.mark.parametrize("scope", REJECTION_SCOPES)
def test_broker_order_for_a_recorded_rejection_is_never_hidden(
    http: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    record_property: Any,
    scope: str,
) -> None:
    """The broker answered 4xx and the rejection persist was lost (the recorded-rejection world of
    20.1-34). A REAL reconciliation whose read broker nevertheless lists an order with that
    client_order_id (the broker shows an order it had answered 4xx) MATCHES it: a blocking
    STATE_MISMATCH, never MISSING_BROKER; the gate reads ``reconciliation_not_clean``, Continue is
    refused, nothing is POSTed again and the local order is never bound."""

    world = _recorded_rejection_shape(monkeypatch, WallClockTimeline())
    j1, order_id, cid1, op, broker = (
        world.j1,
        world.order_id,
        world.cid1,
        world.operation_id,
        world.broker,
    )
    assert broker.received == {cid1: 1}  # the one POST, answered 4xx
    assert attempt_outcomes(cid1) == [(1, "rejected")]
    assert _gate() is GateCode.RECONCILIATION_REQUIRED  # the REJECTED verdict is established

    twin = dataclasses.replace(_broker_twin(order_id), broker_status="new")
    assert twin.client_order_id == cid1
    report, _job_id = _reconcile(scope, scripted_read_broker(orders=[twin]))

    # matched as a STATE_MISMATCH, never explained away as a missing order
    assert _finding_types(report, order_id) == ["STATE_MISMATCH"], scope
    (finding,) = _findings_for(report, order_id)
    assert finding["blocks_execution"] is True
    assert finding["details"]["broker_order_id"] == twin.broker_order_id
    assert report.blocks_execution is True

    gate = assert_recovery_consumers_agree(http, **_agree(world, linked=[j1], flagged=[j1]))
    assert gate is GateCode.RECONCILIATION_NOT_CLEAN
    assert not _a5_passed()
    assert _continue_conflict(op).code == "reconciliation_not_clean"

    # zero additional POST, the attempt log is untouched, the order is never bound
    assert broker.received == {cid1: 1}
    assert attempt_outcomes(cid1) == [(1, "rejected")]
    unbound = _order(order_id)
    assert unbound.broker_order_id is None and unbound.broker_status is None
    assert unbound.status.value == PENDING

    _end_operation(op, "g1 recorded rejection broker order")
    _assert_start_refused_by_the_gate(
        record_property,
        monkeypatch,
        broker=broker,
        broker_orders=[twin],
        gate=GateCode.RECONCILIATION_NOT_CLEAN,
    )
    assert broker.received == {cid1: 1}
    assert _order(order_id).broker_order_id is None


# ---------------------------------------------------------------------------
# Test 7: another strategy's unexplained order: permission is not resolution
# ---------------------------------------------------------------------------

OTHER_ORDERS = ("legacy_pending_no_broker_id", "failed_with_broker_id_absent")


def _seed_other_order(kind: str) -> uuid.UUID:
    """ONE order of the OTHER registered strategy, inserted with the legacy helpers (the current
    product cannot produce it: every new order is operation-bound). ``legacy_pending_no_broker_id``
    is UNESTABLISHED (TL-4): it blocks OTHER's own gate and check A5. ``failed_with_broker_id_absent``
    carries a broker id the broker will not return (BROKER_EVIDENCE): a reconciliation reports it,
    but it is no recovery candidate, so OTHER's own gate is open."""

    with session_scope(load_settings()) as session:
        strategy_row(session, OTHER)
        run = seed_paper_run(session, None, OTHER)  # a paper run of OTHER, linked to no Job
        if kind == "legacy_pending_no_broker_id":
            order = seed_intent(
                session,
                run,
                status=OrderLifecycleState.PENDING_SUBMISSION,
                attempts=(),
                broker_order_id=None,
            )
        else:
            order = seed_intent(
                session,
                run,
                status=OrderLifecycleState.SUBMISSION_FAILED,
                attempts=(),
                broker_order_id="b-other-1",
            )
        return order.id


def _order_facts(order_id: uuid.UUID) -> tuple[Any, ...]:
    """Everything a reconciliation, a gate read or a Continue could change on one order."""

    order = _order(order_id)
    with session_scope(load_settings()) as session:
        attempts = session.execute(
            select(func.count())
            .select_from(OrderSubmissionAttempt)
            .where(OrderSubmissionAttempt.paper_order_id == order_id)
        ).scalar_one()
    return (
        order.status.value,
        order.broker_order_id,
        order.broker_status,
        order.submission_attempt_count,
        order.sync_failure_count,
        order.last_sync_error,
        int(attempts),
        _order_event_count(order_id),
    )


def _a6() -> CheckResult:
    """A6 as the seeding / handover / release controls read it."""

    with session_scope(load_settings()) as session:
        return _check_a6(session, _latest_completed_account_run(session))


def _a5() -> CheckResult:
    with session_scope(load_settings()) as session:
        return _check_a5(session, now=clock.now_utc())


def _account_checks() -> AccountChecks:
    with session_scope(load_settings()) as session:
        return evaluate_account_checks(session, include_handover=False)


def _refs(check: CheckResult) -> set[tuple[EvidenceKind, str]]:
    return {(ref.kind, ref.id) for ref in check.evidence_refs}


@pytest.mark.parametrize("other_order", OTHER_ORDERS)
def test_another_strategys_unexplained_order_keeps_account_checks_failing_while_owner_scope_restores_permission(
    http: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    record_property: Any,
    other_order: str,
) -> None:
    """Permission is not resolution (D-G1-A consequences). The owner holds a released SAF-01 order;
    ANOTHER strategy holds one unexplained pre-send order. A REAL account reconciliation reports
    only the other strategy's order, so it is not clean: the owner's gate reads
    ``reconciliation_not_clean`` and its Continue is refused, and A6 fails. A REAL reconciliation
    of the OWNER's scope (which never loads the other strategy's order) is clean and restores the
    owner's permission, but it cannot satisfy A6 (only an account run does), cannot resolve the
    other strategy's order, and leaves A5 failing for a legacy order (TL-4). The Continue that
    follows sends the owner's pinned intent once and nothing for the other strategy."""

    legacy = other_order == "legacy_pending_no_broker_id"
    other_gate = GateCode.OUTCOME_UNRESOLVED if legacy else None

    # 1. the owner holds the SAF-01 order; 2. the other strategy holds ONE unexplained order
    world = _saf01_shape(PENDING, monkeypatch, WallClockTimeline())
    j1, order_id, cid1, op, broker = (
        world.j1,
        world.order_id,
        world.cid1,
        world.operation_id,
        world.broker,
    )
    other_id = _seed_other_order(other_order)
    other_cid = _order(other_id).client_order_id
    other_before = _order_facts(other_id)
    assert other_before[:4] == (
        ("pending_submission", None, None, 0)
        if legacy
        else ("submission_failed", "b-other-1", None, 0)
    )
    assert other_before[6] == 0  # no attempt row: a legacy order
    assert assert_recovery_consumers_agree(http, strategy_id=OTHER, linked_job_ids=[]) is other_gate
    assert _gate() is GateCode.RECONCILIATION_REQUIRED  # the owner only awaits a reconciliation

    # 3. a REAL ACCOUNT reconciliation against a broker that shows nothing
    account_report, _job_id = run_real_account_reconciliation(broker=scripted_read_broker())
    assert [(f["event_type"], f["paper_order_id"]) for f in _findings_of(account_report)] == [
        ("MISSING_BROKER", str(other_id))
    ]  # the other strategy's order ONLY; nothing names the owner's released order
    assert _finding_types(account_report, order_id) == []
    (finding,) = _findings_for(account_report, other_id)
    assert finding["details"]["submission_evidence"] == (
        "unestablished" if legacy else "broker_evidence"
    )
    assert finding["blocks_execution"] is True
    assert account_report.blocks_execution is True

    # 4. the owner: the newest qualifying run after J1's effect is that dirty account run
    assert (
        assert_recovery_consumers_agree(http, **_agree(world, linked=[j1], flagged=[j1]))
        is GateCode.RECONCILIATION_NOT_CLEAN
    )
    assert _continue_conflict(op).code == "reconciliation_not_clean"
    a6 = _a6()
    assert not a6.passed and a6.reason_code is CheckReason.RECONCILIATION_NOT_CLEAN
    dirty_run = (EvidenceKind.ACCOUNT_RECONCILIATION_RUN, account_report.run_id)
    assert _refs(a6) == {dirty_run}
    assert assert_recovery_consumers_agree(http, strategy_id=OTHER, linked_job_ids=[]) is other_gate
    a5 = _a5()
    assert not a5.passed and (EvidenceKind.STRATEGY, STRATEGY) in _refs(a5)  # the owner, unresolved
    assert ((EvidenceKind.STRATEGY, OTHER) in _refs(a5)) is legacy
    assert _order_facts(other_id) == other_before  # an account reconciliation changes no order

    # 5. a REAL reconciliation of the OWNER's scope: clean (it does not load the other's order)
    owner_report, _owner_job_id = run_real_strategy_reconciliation(
        strategy_id=STRATEGY, as_of_session=SESSION, broker=scripted_read_broker()
    )
    assert owner_report.blocks_execution is False and owner_report.finding_count == 0
    assert _finding_types(owner_report, other_id) == []
    assert reconciliation_completed_at(owner_report) > reconciliation_completed_at(account_report)
    # the owner's permission is restored
    assert assert_recovery_consumers_agree(http, **_agree(world, linked=[j1], flagged=[j1])) is None
    assert _continue_validate(op)["mode"] == "continue"

    # 6. permission is not resolution, asserted right after step 5
    a6 = _a6()
    assert not a6.passed and a6.reason_code is CheckReason.RECONCILIATION_NOT_CLEAN
    # an owner-scope run never satisfies A6: the (dirty) account run still decides
    assert _refs(a6) == {dirty_run}
    a5 = _a5()
    assert (EvidenceKind.STRATEGY, STRATEGY) not in _refs(a5)  # the owner is resolved now
    if legacy:
        assert not a5.passed and a5.reason_code is CheckReason.UNRESOLVED_OUTCOME
        assert (EvidenceKind.STRATEGY, OTHER) in _refs(a5)
        assert (EvidenceKind.RECOVERY_INTENT, str(other_id)) in _refs(a5)
    else:
        assert a5.passed  # A6 is then the one account check that reads the reconciliation and fails
    # the whole account check set at this moment. A1 fails only because the OWNER's own operation is
    # still open (step 7 needs it open) and reads no reconciliation evidence; A2 to A4 pass; of the
    # checks that read reconciliation / recovery evidence A6 always fails and A5 only for a legacy
    # order. For ``failed_with_broker_id_absent`` A6 is therefore the genuinely new refusal.
    checks = _account_checks()
    failed = set(checks.failed_ids())
    record_property("failed_checks_after_owner_scope", ",".join(sorted(c.value for c in failed)))
    record_property("a5_after_owner_scope", f"passed={a5.passed}")
    record_property("a6_after_owner_scope", f"reason={a6.reason_code}")
    a1 = checks.get(CheckId.A1)
    assert a1 is not None and a1.reason_code is CheckReason.OPEN_OPERATION
    assert (EvidenceKind.OPERATION, str(op)) in _refs(a1)
    assert failed == ({CheckId.A1, CheckId.A5, CheckId.A6} if legacy else {CheckId.A1, CheckId.A6})
    assert assert_recovery_consumers_agree(http, strategy_id=OTHER, linked_job_ids=[]) is other_gate
    assert _order_facts(other_id) == other_before  # the other strategy's order is untouched

    # 7. Continue sends the owner's pinned intent once and nothing for the other strategy
    j2 = continue_job(op)
    report = run_continue(broker.service(), operation_id=op, job_id=j2)
    assert [o["client_order_id"] for o in report.result_summary["submitted_orders"]] == [cid1]
    assert broker.received == {cid1: 1} and other_cid not in broker.received
    assert attempt_outcomes(cid1) == [(1, "accepted")]
    assert _order_facts(other_id) == other_before
    assert assert_recovery_consumers_agree(http, strategy_id=OTHER, linked_job_ids=[]) is other_gate
    # the owner stays released while Continue runs, and A6 still fails: only an ACCOUNT run satisfies it
    assert (
        assert_recovery_consumers_agree(http, **_agree(world, linked=[j1, j2], flagged=[j1, j2]))
        is None
    )
    assert not _a6().passed
