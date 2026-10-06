"""G-1 on the REAL path: a released pre-send order and a recorded rejection (plan 20.1-34).

Sources: G-1 of ``20.1-VERIFICATION.md``, the user's decisions 1 and 2 of 2026-10-06 and the user's
correction of the same day (a definitive rejection explains the absence at the broker, and never
grants a resend). The earlier regressions (``tests/test_cr01_reuse_e2e.py``) were green only because
they SEEDED a clean standalone reconciliation; these run the REAL services.

* SAF-01 release (Tests A and B): the order is registered (``register()`` raises
  ``submission_attempt_count`` to 1) and the worker dies before T1, so no attempt row exists and
  nothing was POSTed. A REAL standalone reconciliation of either scope must report nothing for the
  order and be clean; then Continue (``run_paper_continuation``) and a new Start through
  ``run_paper_session`` (whose in-session reconciliation is also real) each POST the pinned intent
  exactly once;
* recorded rejection (Tests C and D): the broker answered 4xx, attempt ``(1, rejected)`` is
  committed, the worker died before the rejection was persisted and the Job was reclaimed
  ``outcome_uncertain``; the order is still ``pending_submission`` with count 1. A REAL
  reconciliation is clean for it, the gate and A5 release, and NOTHING may be re-sent: Continue
  skips the rejected intent and a new Start in the same evaluation session is refused (the
  rejection consumed the TL-10 allowance).

Harness rules of ``tests/test_cr01_reuse_e2e.py`` apply: the only injected faults are a one-shot
worker crash and the lease lapse (before T1 for the SAF-01 shapes; after the rejection's T2 and
before its persist for the recorded-rejection shape). Everything after it is a product entrypoint.
Two differences: time is WALL-CLOCK (``WallClockTimeline``: a real reconciliation stamps
``datetime.now(UTC)``, so it can only follow a crash that is not future-dated) and every standalone
reconciliation is the REAL service (``tests/support/real_reconciliation.py``). No reconciliation
row is seeded: not the release, and not the reconciliation inside the new evaluation's basis. The
evaluation's recorded basis is arranged with ``seed_verified_basis(with_reconciliation=False)`` and
a ``base`` that places its sync Job between the last broker effect and the REAL release, so the
release itself is the clean standalone reconciliation of the basis window.

The CR-01 interim runbook prohibition on Continue / Retry is NOT lifted by this module.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from tests.support.basis_fixtures import seed_fresh_broker_snapshot
from tests.support.real_reconciliation import (
    WallClockTimeline,
    reconciliation_completed_at,
    run_real_account_reconciliation,
    run_real_strategy_reconciliation,
    scripted_read_broker,
)
from tests.support.recovery_agreement import assert_recovery_consumers_agree
from tests.test_cr01_reuse_e2e import (  # noqa: F401  (http and _seams are fixtures)
    FAILED,
    PENDING,
    World,
    _a5_passed,
    _agree,
    _crash_job,
    _finish_job,
    _job_row,
    _order,
    _r3_entries,
    _run_of,
    _saf01_shape,
    _seams,
    _start_job,
    _unproven,
    http,
)
from tests.test_paper_execution import migrated_paper_db  # noqa: F401  (database fixture of http)
from tests.test_paper_session_operations import (
    DEFAULT_BATCH,
    SESSION,
    STRATEGY,
    S1Broker,
    _continue_conflict,
    _continue_validate,
    _start,
    _validate,
    attempt_outcomes,
    continue_job,
    count,
    dispositions,
    evaluation,
    intent_rows,
    manifest,
    operation_row,
    run_continue,
    seed_batch,
)
from tests.test_recovery_order_linkage import _accepted_registrations

from trading_platform.core.settings import load_settings
from trading_platform.db.models import (
    AccountReconciliationRun,
    ExecutionOperation,
    ExecutionOperationIntent,
    JobStatus,
    OrderEvent,
    OrderSubmissionAttempt,
    PaperOrder,
    RiskEvent,
    StrategyRun,
    StrategyRunType,
)
from trading_platform.db.models.symbol import Symbol
from trading_platform.db.session import session_scope
from trading_platform.services.execution import submit_orders as submit_orders_module
from trading_platform.services.execution.attempts import load_submission_evidence
from trading_platform.services.execution.intent_identity import (
    decision_fingerprint,
    decision_inputs_digest,
    load_basis_verification_rows,
    portfolio_state_digest,
    risk_config_digest,
    verify_evaluation_basis,
)
from trading_platform.services.execution.operations import end_operation
from trading_platform.services.execution.permission import strategy_working_orders
from trading_platform.services.execution.submit_orders import (
    build_client_order_id,
    run_paper_session,
)
from trading_platform.services.recovery import GateCode

SCOPES = ("account", "strategy")
SAF01_MATRIX = [
    pytest.param(scope, status, id=f"{scope}-{status}")
    for scope in SCOPES
    for status in (PENDING, FAILED)
]
REJECTION_SCOPES = [pytest.param(scope, id=f"{scope}-recorded_rejection") for scope in SCOPES]
REJECTION_START_MATRIX = [
    pytest.param(scope, decision, id=f"{scope}-recorded_rejection-{decision}")
    for scope in SCOPES
    for decision in ("same_fingerprint", "changed_quantity")
]

#: The reply of the broker to the first POST of the recorded-rejection shape (a definitive 4xx).
REJECTION_REPLY = {"code": 40310000, "message": "insufficient buying power"}


# ---------------------------------------------------------------------------
# The recorded-rejection world (imported by 20.1-36)
# ---------------------------------------------------------------------------


@dataclass
class RejectingS1Broker(S1Broker):
    """``S1Broker`` whose first POST (``rejections``) is answered with a definitive 4xx.

    The request is COUNTED (it reached the broker); every later POST is the unchanged
    ``S1Broker.handler`` (nothing is copied from it), so the same broker accepts the next intent.
    """

    rejections: int = 1

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.rejections > 0:
            self.rejections -= 1
            client_order_id = json.loads(request.content)["client_order_id"]
            self.received[client_order_id] = self.received.get(client_order_id, 0) + 1
            return httpx.Response(403, json=REJECTION_REPLY)
        return super().handler(request)


def _recorded_rejection_shape(monkeypatch: pytest.MonkeyPatch, timeline: Any) -> World:
    """Seed a two-intent batch, start the session as Job J1 and let the broker REJECT intent 1.

    The ONLY injected fault is a one-shot exception at the ``reject`` persist of the loop (the
    worker dies after the broker answered 4xx and after the attempt outcome ``rejected`` was
    committed by T2, before the rejection was persisted), followed by the lease lapse that the
    product sweep (``reclaim_lost_jobs``) turns into FAILED / ``outcome_uncertain``. Afterwards
    the order is still ``pending_submission`` with ``submission_attempt_count`` 1.
    """

    risk_run, _events = seed_batch(DEFAULT_BATCH[:2], manifest=manifest())
    j1 = _start_job(risk_run)
    broker = RejectingS1Broker()
    fired = {"count": 0}
    real_persist = submit_orders_module._persist_fenced

    def crashing_persist(ctx: Any, write: Any) -> bool:
        if write.__name__ == "reject" and fired["count"] == 0:
            fired["count"] += 1
            raise RuntimeError(
                "worker crashed after the broker answered, before the rejection was persisted"
            )
        return real_persist(ctx, write)

    monkeypatch.setattr(submit_orders_module, "_persist_fenced", crashing_persist)
    with pytest.raises(RuntimeError, match="before the rejection was persisted"):
        _start(broker.service(), risk_run_id=risk_run, job_id=j1)
    monkeypatch.setattr(submit_orders_module, "_persist_fenced", real_persist)
    assert fired["count"] == 1  # the fault fired exactly once, at the `reject` persist
    crashed_at = timeline.next()
    _crash_job(j1, crashed_at)

    # preconditions, asserted explicitly (never seeded)
    operation = operation_row()
    (intent1, cid1), (_intent2, cid2) = intent_rows()
    assert broker.received == {cid1: 1}  # one POST, answered 4xx
    assert attempt_outcomes(cid1) == [(1, "rejected")]  # T2 committed the rejection
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
        assert order.status.value == PENDING  # the rejection was never persisted
        assert order.submission_attempt_count == 1
        assert order.broker_order_id is None
        bound = session.execute(
            select(ExecutionOperationIntent.paper_order_id).where(
                ExecutionOperationIntent.id == intent1
            )
        ).scalar_one()
        assert bound == order_id
        assert _accepted_registrations(session, order_id) == {("intent_registered", r1)}
    job = _job_row(j1)
    assert job.status is JobStatus.FAILED and job.outcome_uncertain is True
    return World(
        status=PENDING,
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


# ---------------------------------------------------------------------------
# Checkpoint, release and read helpers
# ---------------------------------------------------------------------------


def _checkpoint(
    http: TestClient,
    world: World,
    *,
    linked: list[uuid.UUID],
    flagged: list[uuid.UUID],
    expect: GateCode | None,
    operation_id: uuid.UUID | None = None,
) -> None:
    """The consumer-agreement assertion, the expected gate, no registering Job
    ``execution_path_unproven`` and A5 passing exactly when the gate is open."""

    gate = assert_recovery_consumers_agree(
        http, **_agree(world, linked=linked, flagged=flagged, operation_id=operation_id)
    )
    world.history.append(gate)
    assert gate is expect, (gate, expect, world.history)
    for job_id in flagged:
        assert not _unproven(job_id), f"Job {job_id} reads execution_path_unproven"
    assert _a5_passed() is (expect is None)


def _shared_verdict(order_id: uuid.UUID) -> str:
    """The shared submission verdict of the order (the one the recovery gate reads)."""

    with session_scope(load_settings()) as session:
        order = session.get(PaperOrder, order_id)
        assert order is not None
        return load_submission_evidence(session, [order])[order_id].value


def _findings_of(report: Any) -> list[dict[str, Any]]:
    """The findings of a report as plain dicts (strategy reports carry objects, account reports
    dicts)."""

    return [f if isinstance(f, dict) else f.to_dict() for f in report.findings]


def _release(scope: str, world: World) -> datetime:
    """The REAL standalone reconciliation of ``scope`` against a broker that holds no order (the
    truth: nothing was ever created at the broker for the order). It must be clean and report
    nothing about the released order. Returns its ``completed_at``."""

    broker = scripted_read_broker()
    if scope == "account":
        report, _job_id = run_real_account_reconciliation(broker=broker)
    else:
        report, _job_id = run_real_strategy_reconciliation(
            strategy_id=STRATEGY, as_of_session=SESSION, broker=broker
        )
    findings = _findings_of(report)
    order_id = str(world.order_id)
    naming = [f for f in findings if f.get("paper_order_id") == order_id]
    assert not naming, (
        f"G-1: the REAL {scope} reconciliation reports the released order: "
        f"{[(f['event_type'], f['details'].get('submission_attempt_count')) for f in naming]} "
        f"shared_verdict={_shared_verdict(world.order_id)} "
        f"local_status={[f['details'].get('local_status') for f in naming]}"
    )
    assert report.blocks_execution is False, (scope, findings)
    assert report.finding_count == 0, (scope, findings)
    assert findings == [] and not report.unresolved_reasons, (scope, report.unresolved_reasons)
    completed_at = reconciliation_completed_at(report)
    assert completed_at > world.j1_completed_at
    return completed_at


def _reconciliation_run_counts() -> tuple[int, int]:
    """(strategy reconciliation runs, account reconciliation runs): Continue must add none."""

    with session_scope(load_settings()) as session:
        strategy_runs = session.execute(
            select(func.count())
            .select_from(StrategyRun)
            .where(StrategyRun.run_type == StrategyRunType.RECONCILIATION)
        ).scalar_one()
        account_runs = session.execute(
            select(func.count()).select_from(AccountReconciliationRun)
        ).scalar_one()
    return int(strategy_runs), int(account_runs)


@dataclass(frozen=True)
class _PinnedIntent:
    intent_id: uuid.UUID
    ticker: str
    side: str
    quantity: Decimal
    client_order_id: str
    decision_fingerprint: str


def _pinned_intent(operation_id: uuid.UUID, client_order_id: str) -> _PinnedIntent:
    with session_scope(load_settings()) as session:
        row = session.execute(
            select(
                ExecutionOperationIntent.id,
                Symbol.ticker,
                ExecutionOperationIntent.side,
                ExecutionOperationIntent.quantity,
                ExecutionOperationIntent.client_order_id,
                ExecutionOperationIntent.decision_fingerprint,
            )
            .join(Symbol, Symbol.id == ExecutionOperationIntent.symbol_id)
            .where(
                ExecutionOperationIntent.operation_id == operation_id,
                ExecutionOperationIntent.client_order_id == client_order_id,
            )
        ).one()
    return _PinnedIntent(*row)


def _assert_pinned_identity(broker: S1Broker, operation_id: uuid.UUID, cid: str) -> None:
    """The POST carried the pinned identity: client_order_id, symbol, side and quantity of the
    operation's intent row."""

    intent = _pinned_intent(operation_id, cid)
    sent = broker.orders[cid]
    assert intent.client_order_id == cid == sent["client_order_id"]
    assert (sent["symbol"], sent["side"], Decimal(sent["qty"])) == (
        intent.ticker,
        intent.side,
        intent.quantity,
    )


def _order_event_count(order_id: uuid.UUID) -> int:
    """The order's transition history length: a registration, a retry or an applied rejection
    would each add a row."""

    with session_scope(load_settings()) as session:
        return int(
            session.execute(
                select(func.count())
                .select_from(OrderEvent)
                .where(OrderEvent.paper_order_id == order_id)
            ).scalar_one()
        )


def _attempt_attribution(order_id: uuid.UUID) -> tuple[uuid.UUID | None, uuid.UUID | None]:
    """(executor_job_id, strategy_run_id) of the order's single attempt row."""

    with session_scope(load_settings()) as session:
        attempt = session.execute(
            select(OrderSubmissionAttempt).where(OrderSubmissionAttempt.paper_order_id == order_id)
        ).scalar_one()
        return attempt.executor_job_id, attempt.strategy_run_id


def _latest_effect_at(world: World) -> datetime:
    """The last broker effect so far: J1's reclaim and the newest attempt start (the execution
    watermark of the basis verification)."""

    with session_scope(load_settings()) as session:
        started = session.execute(select(func.max(OrderSubmissionAttempt.started_at))).scalar_one()
    return max(world.j1_completed_at, started) if started is not None else world.j1_completed_at


def _basis_base(after: datetime, released_at: datetime) -> datetime:
    """The ``base`` of ``seed_verified_basis`` that puts its sync Job (``base + 1 min``) strictly
    between the last broker effect and the REAL release, and the evaluation's completion
    (``base + 3 min``) after it: the release is then the clean standalone reconciliation of the
    basis window and no reconciliation row has to be seeded."""

    sync_at = after + (released_at - after) / 2
    base = sync_at - timedelta(minutes=1)
    assert after < sync_at < released_at < base + timedelta(minutes=3)
    return base


def _fresh_evaluation(
    world: World,
    released_at: datetime,
    batch: list[tuple[str, str, str]],
) -> uuid.UUID:
    """A fresh evaluation of the same session after the REAL release, with a verified basis whose
    reconciliation is the release (``with_reconciliation=False``)."""

    new_run = evaluation(
        batch,
        as_of="2024-01-08",
        base=_basis_base(_latest_effect_at(world), released_at),
        with_reconciliation=False,
    )
    assert new_run != world.risk_run
    return new_run


class _GateSpy:
    """Observe (not replace) the D-15 session gate of ``run_paper_session``."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.answers: list[str | None] = []
        real_gate = submit_orders_module._recovery_gate_code

        def spying_gate(settings: Any, strategy_id: str) -> str | None:
            answer = real_gate(settings, strategy_id)
            self.answers.append(answer)
            return answer

        monkeypatch.setattr(submit_orders_module, "_recovery_gate_code", spying_gate)


def _in_session_reconciliation(report: Any, job_id: uuid.UUID, order_id: uuid.UUID) -> None:
    """The reconciliation inside ``run_paper_session`` was real, belonged to the Job, did not block
    and named no finding for the order."""

    assert report.reconciliation_run_id is not None
    with session_scope(load_settings()) as session:
        run = session.get(StrategyRun, uuid.UUID(str(report.reconciliation_run_id)))
        assert run is not None
        assert run.job_id == job_id
        assert run.trigger_source == "pytest_reconciliation"
        summary = dict(run.result_summary)
    assert summary["blocks_execution"] is False, summary
    assert summary["finding_count"] == 0, summary
    assert all(f.get("paper_order_id") != str(order_id) for f in summary["findings"]), summary


def _new_start_quantity_matches(order_id: uuid.UUID, new_run: uuid.UUID) -> str:
    """The new evaluation sizes the SAME quantity; returns the client_order_id it derives."""

    with session_scope(load_settings()) as session:
        quantity = session.execute(
            select(RiskEvent.proposed_quantity).where(RiskEvent.strategy_run_id == new_run)
        ).scalar_one()
    assert quantity == _order(order_id).quantity
    return build_client_order_id(
        prefix=load_settings().execution.client_order_id_prefix,
        strategy_id=STRATEGY,
        session_date=SESSION,
        symbol="AAPL",
        side="buy",
        quantity=Decimal(quantity),
    )


# ---------------------------------------------------------------------------
# Test A: SAF-01 release by a REAL reconciliation -> Continue sends the pinned intent once
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("scope", "status"), SAF01_MATRIX)
def test_g1_continue_after_real_reconciliation(
    http: TestClient, monkeypatch: pytest.MonkeyPatch, scope: str, status: str
) -> None:
    world = _saf01_shape(status, monkeypatch, WallClockTimeline())
    j1, order_id, cid1, op, broker = (
        world.j1,
        world.order_id,
        world.cid1,
        world.operation_id,
        world.broker,
    )
    # the exact G-1 input: the counter is raised, no attempt row exists, nothing was POSTed
    assert _order(order_id).submission_attempt_count >= 1
    assert attempt_outcomes(cid1) == [] and broker.received == {}

    # Continue REQUIRES the standalone reconciliation: before the real M5 it is refused
    _checkpoint(http, world, linked=[j1], flagged=[j1], expect=GateCode.RECONCILIATION_REQUIRED)
    assert _continue_conflict(op).code == "reconciliation_required"

    # the REAL reconciliation releases the order
    _release(scope, world)
    _checkpoint(http, world, linked=[j1], flagged=[j1], expect=None)
    assert _r3_entries(http, j1) == [(str(order_id), "not_sent", False)]

    # Continue through the product entrypoint; it runs no reconciliation of its own
    assert _continue_validate(op)["mode"] == "continue"
    runs_before = _reconciliation_run_counts()
    j2 = continue_job(op)
    report = run_continue(broker.service(), operation_id=op, job_id=j2)
    assert _reconciliation_run_counts() == runs_before
    r2 = _run_of(j2)
    assert r2 != world.r1
    submitted = report.result_summary["submitted_orders"]
    assert [o["client_order_id"] for o in submitted] == [cid1]
    assert submitted[0]["intent_decision"]["action"] == "retry_existing"
    assert broker.received == {cid1: 1}  # one POST, never more
    _assert_pinned_identity(broker, op, cid1)
    assert attempt_outcomes(cid1) == [(1, "accepted")]
    assert _attempt_attribution(order_id) == (j2, r2)
    assert _order(order_id).strategy_run_id == world.r1  # the ORIGIN run is kept
    with session_scope(load_settings()) as session:
        assert _accepted_registrations(session, order_id) == {
            ("intent_registered", world.r1),
            ("retry_requested", r2),
        }
    assert not _unproven(j1)
    assert _job_row(j2).status is JobStatus.RUNNING
    _checkpoint(http, world, linked=[j1, j2], flagged=[j1, j2], expect=None)
    assert _r3_entries(http, j1) == [(str(order_id), "found_verified", False)]


# ---------------------------------------------------------------------------
# Test B: SAF-01 release by a REAL reconciliation -> a new Start through run_paper_session
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("scope", "status"), SAF01_MATRIX)
def test_g1_start_via_run_paper_session_after_real_reconciliation(
    http: TestClient, monkeypatch: pytest.MonkeyPatch, scope: str, status: str
) -> None:
    timeline = WallClockTimeline()
    world = _saf01_shape(status, monkeypatch, timeline)
    j1, order_id, cid1, op, broker = (
        world.j1,
        world.order_id,
        world.cid1,
        world.operation_id,
        world.broker,
    )
    with session_scope(load_settings()) as session:  # End (M12)
        end_operation(session, op, operator_reason="g1 start", actor="pytest")
    assert _order(order_id).status.value == status and attempt_outcomes(cid1) == []

    # sizing basis (SAF-09) BEFORE the release, so that the scripted read broker mirrors it
    with session_scope(load_settings()) as session:
        seed_fresh_broker_snapshot(session)
    released_at = _release(scope, world)
    _checkpoint(http, world, linked=[j1], flagged=[j1], expect=None)

    # a fresh evaluation of the same session and quantity; the derived identity is cid1
    new_run = _fresh_evaluation(world, released_at, list(DEFAULT_BATCH[:1]))
    assert _new_start_quantity_matches(order_id, new_run) == cid1
    assert _validate(new_run)["risk_run_id"] == str(new_run)
    _checkpoint(http, world, linked=[j1], flagged=[j1], expect=None)

    # the new Start through the SESSION entry point (the D-15 gate is observed, not replaced)
    spy = _GateSpy(monkeypatch)
    j4 = _start_job(new_run)
    report = run_paper_session(
        STRATEGY,
        as_of_session=SESSION,
        risk_run_id=str(new_run),
        trigger_source="pytest",
        settings=load_settings(),
        execution_service=broker.service(),
        broker_client=scripted_read_broker(),
        job_id=j4,
    )
    assert spy.answers == [None], spy.answers
    assert report.action == "submitted_missing_orders", (report.action, broker.received)
    r4 = _run_of(j4)
    assert report.execution_run_id == str(r4)
    _in_session_reconciliation(report, j4, order_id)

    new_operation = uuid.UUID(report.result_summary["operation"]["id"])
    assert new_operation != op
    submitted = report.result_summary["submitted_orders"]
    assert [o["client_order_id"] for o in submitted] == [cid1]
    assert submitted[0]["intent_decision"]["action"] == "retry_existing"
    assert broker.received == {cid1: 1}  # one POST, never more
    _assert_pinned_identity(broker, new_operation, cid1)
    assert attempt_outcomes(cid1) == [(1, "accepted")]
    assert _attempt_attribution(order_id) == (j4, r4)
    assert _order(order_id).strategy_run_id == world.r1  # the ORIGIN run is kept
    with session_scope(load_settings()) as session:
        assert _accepted_registrations(session, order_id) == {
            ("intent_registered", world.r1),
            ("retry_requested", r4),
        }
    assert not _unproven(j1)
    assert _job_row(j4).status is JobStatus.RUNNING
    _checkpoint(
        http,
        world,
        linked=[j1, j4],
        flagged=[j1, j4],
        expect=None,
        operation_id=new_operation,
    )

    # J4 finishes: its effect is newer than the REAL release, a fresh reconciliation is required
    _finish_job(j4, timeline.next())
    _checkpoint(
        http,
        world,
        linked=[j1, j4],
        flagged=[j1, j4],
        expect=GateCode.RECONCILIATION_REQUIRED,
        operation_id=new_operation,
    )


# ---------------------------------------------------------------------------
# Test C: a recorded rejection -> a clean REAL reconciliation, and Continue never re-sends
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scope", REJECTION_SCOPES)
def test_g1_recorded_rejection_m5_is_clean_and_continue_never_resends(
    http: TestClient, monkeypatch: pytest.MonkeyPatch, scope: str
) -> None:
    world = _recorded_rejection_shape(monkeypatch, WallClockTimeline())
    j1, order_id, cid1, cid2, op, broker = (
        world.j1,
        world.order_id,
        world.cid1,
        world.cid2,
        world.operation_id,
        world.broker,
    )

    # before any M5 the flagged J1 still needs a fresh clean reconciliation (the REJECTED verdict
    # itself is established), and Continue is refused
    _checkpoint(http, world, linked=[j1], flagged=[j1], expect=GateCode.RECONCILIATION_REQUIRED)
    assert _continue_conflict(op).code == "reconciliation_required"

    # the REAL reconciliation: the broker holds no order (it created none)
    _release(scope, world)
    _checkpoint(http, world, linked=[j1], flagged=[j1], expect=None)
    assert _r3_entries(http, j1) == [(str(order_id), "rejected_at_submission", False)]

    # Continue: the rejected intent is never loaded, the planned intent behind it is sent once
    assert _continue_validate(op)["mode"] == "continue"
    state_before = (operation_row(op).state, operation_row(op).reason)
    runs_before = _reconciliation_run_counts()
    events_before = _order_event_count(order_id)
    j2 = continue_job(op)
    report = run_continue(broker.service(), operation_id=op, job_id=j2)
    assert _reconciliation_run_counts() == runs_before
    state_after = (report.operation_state, report.operation_reason)
    assert state_after == (operation_row(op).state, operation_row(op).reason)
    submitted_ids = [o["client_order_id"] for o in report.result_summary["submitted_orders"]]
    assert cid1 not in submitted_ids
    assert submitted_ids == [cid2]
    assert broker.received.get(cid1) == 1  # the rejected intent was never re-sent
    assert broker.received.get(cid2) == 1  # Continue progressed to intent 2 and sent it once
    assert attempt_outcomes(cid1) == [(1, "rejected")]  # no new attempt row, no T1 for it
    with session_scope(load_settings()) as session:
        assert _accepted_registrations(session, order_id) == {("intent_registered", world.r1)}
    # Continue never loaded the rejected intent: its order was not touched in any way
    assert _order_event_count(order_id) == events_before
    assert _order(order_id).status.value == PENDING
    assert not _unproven(j1)
    assert _job_row(j2).status is JobStatus.RUNNING
    _checkpoint(http, world, linked=[j1, j2], flagged=[j1, j2], expect=None)
    # the recorded states (for the SUMMARY)
    assert state_before == ("paused", "awaiting_reconciliation"), state_before
    assert state_after == ("paused", "working_order_commitments_unaccounted"), state_after


# ---------------------------------------------------------------------------
# Test D: a recorded rejection consumes the TL-10 session allowance of a new Start
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("scope", "decision"), REJECTION_START_MATRIX)
def test_g1_recorded_rejection_new_start_consumes_the_session_allowance(
    http: TestClient, monkeypatch: pytest.MonkeyPatch, scope: str, decision: str
) -> None:
    timeline = WallClockTimeline()
    world = _recorded_rejection_shape(monkeypatch, timeline)
    j1, order_id, cid1, op, broker = (
        world.j1,
        world.order_id,
        world.cid1,
        world.operation_id,
        world.broker,
    )
    with session_scope(load_settings()) as session:  # End (M12)
        end_operation(session, op, operator_reason="g1 rejected start", actor="pytest")
    assert _order(order_id).status.value == PENDING
    assert attempt_outcomes(cid1) == [(1, "rejected")]

    with session_scope(load_settings()) as session:  # sizing basis (SAF-09) BEFORE the release
        seed_fresh_broker_snapshot(session)
    released_at = _release(scope, world)
    _checkpoint(http, world, linked=[j1], flagged=[j1], expect=None)

    # a fresh evaluation of the same session for cid1's symbol and side only
    batch = list(DEFAULT_BATCH[:1]) if decision == "same_fingerprint" else [("AAPL", "7", "120")]
    new_run = _fresh_evaluation(world, released_at, batch)
    assert _validate(new_run)["risk_run_id"] == str(new_run)
    earlier = _pinned_intent(op, cid1)
    with session_scope(load_settings()) as session:
        settings = load_settings()
        risk_run = session.get(StrategyRun, new_run)
        assert risk_run is not None
        rows = load_basis_verification_rows(session, strategy_public_id=STRATEGY, risk_run=risk_run)
        inputs = decision_inputs_digest(
            (risk_run.result_summary or {}).get("evaluation_manifest"),
            risk_config=risk_config_digest(settings, STRATEGY),
        )
        working = strategy_working_orders(session, STRATEGY)
        portfolio = portfolio_state_digest(
            rows.basis_positions, [(o.symbol, o.side, o.quantity) for o in working]
        )
        new_quantity = Decimal(batch[0][1])
        candidate_fingerprint = decision_fingerprint(
            strategy_id=STRATEGY,
            session_date=SESSION,
            symbol="AAPL",
            side="buy",
            quantity=new_quantity,
            inputs_digest=inputs,
            portfolio_digest=portfolio,
        )
        # check 5 runs before TL-10: a verified basis that holds no position in the symbol
        assert verify_evaluation_basis(rows).verified
        assert rows.basis_positions.get("AAPL", Decimal("0")) == 0
    if decision == "same_fingerprint":
        assert candidate_fingerprint == earlier.decision_fingerprint
    else:
        assert candidate_fingerprint != earlier.decision_fingerprint
        assert new_quantity != earlier.quantity

    orders_before, attempts_before = count(PaperOrder), count(OrderSubmissionAttempt)
    events_before = _order_event_count(order_id)
    operations_before, intents_before = count(ExecutionOperation), count(ExecutionOperationIntent)
    spy = _GateSpy(monkeypatch)
    j4 = _start_job(new_run)
    report = run_paper_session(
        STRATEGY,
        as_of_session=SESSION,
        risk_run_id=str(new_run),
        trigger_source="pytest",
        settings=load_settings(),
        execution_service=broker.service(),
        broker_client=scripted_read_broker(),
        job_id=j4,
    )
    assert spy.answers == [None], spy.answers
    _in_session_reconciliation(report, j4, order_id)
    expected = (
        "replay_of_earlier_decision"
        if decision == "same_fingerprint"
        else "action_already_submitted"
    )
    # the refused candidate is listed in the persisted dispositions of the run; a refusal creates
    # no operation, no intent and no order
    assert dispositions(report) == [("AAPL", "buy", expected)], report.result_summary.get(
        "candidate_dispositions"
    )
    if decision == "same_fingerprint":
        refused = report.result_summary["candidate_dispositions"][0]
        assert refused["earlier_intent_id"] == str(earlier.intent_id)
    assert broker.received == {cid1: 1}  # zero additional POST
    assert (count(PaperOrder), count(OrderSubmissionAttempt)) == (orders_before, attempts_before)
    assert (count(ExecutionOperation), count(ExecutionOperationIntent)) == (
        operations_before,
        intents_before,
    )
    assert report.operation_id is None
    assert _order_event_count(order_id) == events_before  # nothing was registered or applied
    assert attempt_outcomes(cid1) == [(1, "rejected")]
    assert _order(order_id).status.value == PENDING
    assert report.action == "noop_existing_orders", report.action  # recorded in the SUMMARY
