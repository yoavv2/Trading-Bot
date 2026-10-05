"""The operation read model, the End result and the retry guard read the shared verdict (20.1-28).

WR-01: ``GET /api/v1/execution-operations/{id}`` ``unresolved_intents`` and the End result list
exactly the intents whose ``classify_submission_evidence`` verdict is UNESTABLISHED, so they never
contradict the strategy gate. ``IntentFact.state`` (loop selection / lifecycle display) is NOT
changed by this.

WR-08 / V-3 (the ``retry_existing`` registration guard): a rejection counts only when the COMPLETE
attempt history is conclusively rejected under the shared classifier; any ambiguous or unfinished
attempt dominates a later rejection and keeps the intent blocked; a broker-reaching rejection
consumes the TL-10 key (never resent: ``replay_of_earlier_decision`` / ``action_already_submitted``).
These tests drive the product loop (``tests/test_paper_session_operations.py`` harness).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import date, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from tests.support.calendar_facts import et, seed_calendar
from tests.support.migrated_db import migrated_database
from tests.support.operation_fixtures import seed_operation, seed_operation_intent
from tests.support.paper_eligibility import allow_paper_execution
from tests.support.paper_execution_seams import allow_direct_paper_execution
from tests.support.recovery_fixtures import (
    OWNER,
    at,
    seed_intent,
    seed_job,
    seed_operation_bound_intent,
    seed_paper_run,
)
from tests.support.submission_attempts import seed_attempt_row
from tests.test_paper_execution import migrated_paper_db  # noqa: F401  (database fixture)
from tests.test_paper_session_operations import (
    ScriptedExecutionService,
    _start,
    attempt_outcomes,
    count,
    dispositions,
    end_all_open_operations,
    evaluation,
    finish_jobs,
    intent_rows,
    operation_row,
    run_continue,
    seed_clean_reconciliation_after_now,
)

from trading_platform.api.app import create_app
from trading_platform.core import clock
from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.db.models import (
    AttemptOutcomeClass,
    ExecutionEvent,
    ExecutionOperationIntent,
    JobStatus,
    OrderEvent,
    OrderLifecycleState,
    OrderTransitionEventType,
    PaperOrder,
)
from trading_platform.db.session import session_scope
from trading_platform.services.alpaca import AmbiguousOrderSubmissionError
from trading_platform.services.execution import submit_orders as submit_orders_module
from trading_platform.services.execution.attempts import SubmissionIntentState
from trading_platform.services.execution.operations import load_intent_facts
from trading_platform.services.recovery import GateCode, strategy_recovery_status

S = date(2025, 12, 2)
IN_WINDOW = et(2025, 12, 3, 10, 0)
BASE = "/api/v1/execution-operations"
PENDING = OrderLifecycleState.PENDING_SUBMISSION
FAILED = OrderLifecycleState.SUBMISSION_FAILED


@pytest.fixture()
def api_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    monkeypatch.setenv("TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED", "true")
    with migrated_database(monkeypatch, "operation_read_shared_verdict") as name:
        clear_settings_cache()
        seed_calendar(date(2025, 11, 24), date(2026, 1, 9))
        monkeypatch.setattr(clock, "now_utc", lambda: IN_WINDOW)
        yield name
    clear_settings_cache()


@pytest.fixture()
def client(api_db: str) -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        yield test_client


def _gate() -> GateCode | None:
    with session_scope(load_settings()) as session:
        return strategy_recovery_status(session, OWNER).gate_code


def _ids(items: list[dict[str, Any]]) -> set[str]:
    return {item["intent_id"] for item in items}


# ---------------------------------------------------------------------------
# WR-01
# ---------------------------------------------------------------------------


def test_operation_read_lists_the_db06_accepted_without_broker_id_shape(
    client: TestClient,
) -> None:
    """DB-06: the attempt is ``accepted`` but the ``accepted()`` persist rolled back, so the order
    is still ``submission_failed`` with no broker id. The lifecycle display says ``submitted``;
    the shared verdict (and the gate) say the outcome is not established."""

    with session_scope(load_settings()) as session:
        job = seed_job(session, uncertain=False, status=JobStatus.CANCELLED, completed_at=at(0))
        run = seed_paper_run(session, job)
        operation = seed_operation(
            session, state="paused", reason="awaiting_reconciliation", session_date=S
        )
        order = seed_operation_bound_intent(
            session,
            run,
            status=FAILED,
            attempts=(AttemptOutcomeClass.ACCEPTED,),
            operation=operation,
        )
        operation_id, order_id = operation.id, order.id
        intent_id = session.execute(
            select(ExecutionOperationIntent.id).where(
                ExecutionOperationIntent.paper_order_id == order_id
            )
        ).scalar_one()

    detail = client.get(f"{BASE}/{operation_id}")
    assert detail.status_code == 200, detail.text
    item = detail.json()
    assert [u["intent_id"] for u in item["unresolved_intents"]] == [str(intent_id)]
    entry = item["unresolved_intents"][0]
    assert entry["blocking_effect"] == "outcome_unresolved"
    assert entry["state"] == "ambiguous"
    assert entry["paper_order_id"] == str(order_id)
    # The lifecycle display is unchanged: the same intent still reads `submitted` in `intents`.
    assert [i["state"] for i in item["intents"]] == ["submitted"]
    assert item["working_orders"] == []  # no broker id: not a working order
    assert _gate() is GateCode.OUTCOME_UNRESOLVED


def test_end_result_lists_the_legacy_reused_zero_attempt_order_and_does_not_cancel_it(
    client: TestClient,
) -> None:
    """A legacy order (no attempt rows, created BEFORE every intent row that references it) is
    UNESTABLISHED: A5 and the gate block on it, End lists it and never cancels it."""

    with session_scope(load_settings()) as session:
        job = seed_job(session, uncertain=False, status=JobStatus.CANCELLED, completed_at=at(0))
        run = seed_paper_run(session, job)
        order = seed_intent(session, run, status=PENDING, attempts=())
        operation = seed_operation(
            session, state="paused", reason="awaiting_reconciliation", session_date=S
        )
        row = ExecutionOperationIntent(
            operation_id=operation.id,
            sequence=1,
            symbol_id=order.symbol_id,
            side=order.side,
            quantity=order.quantity,
            reference_price=order.quantity,
            client_order_id=order.client_order_id,
            paper_order_id=order.id,
            decision_fingerprint=uuid.uuid4().hex + uuid.uuid4().hex,
            prior_execution_refs=[],
            disposition="open",
            created_at=order.created_at + timedelta(seconds=5),
        )
        session.add(row)
        session.flush()
        operation_id, intent_id = operation.id, row.id

    detail = client.get(f"{BASE}/{operation_id}").json()
    assert _ids(detail["unresolved_intents"]) == {str(intent_id)}
    assert _gate() is GateCode.OUTCOME_UNRESOLVED

    ended = client.post(f"{BASE}/{operation_id}/end", json={"reason": "operator decision"})
    assert ended.status_code == 200, ended.text
    body = ended.json()
    assert body["unsent_cancelled"] == []
    assert _ids(body["unresolved_intents"]) == {str(intent_id)}
    assert [u["blocking_effect"] for u in body["unresolved_intents"]] == ["outcome_unresolved"]
    with session_scope(load_settings()) as session:
        persisted = session.get(ExecutionOperationIntent, intent_id)
        assert persisted is not None and persisted.disposition == "open"
        reread = session.get(PaperOrder, persisted.paper_order_id)
        assert reread is not None and reread.status is PENDING


def test_planned_and_proven_not_sent_intents_are_never_listed(client: TestClient) -> None:
    with session_scope(load_settings()) as session:
        job = seed_job(session, uncertain=False, status=JobStatus.CANCELLED, completed_at=at(0))
        run = seed_paper_run(session, job)
        operation = seed_operation(
            session, state="paused", reason="awaiting_reconciliation", session_date=S
        )
        seed_operation_bound_intent(session, run, status=PENDING, operation=operation)
        seed_operation_bound_intent(
            session,
            run,
            status=FAILED,
            attempts=(AttemptOutcomeClass.PRE_CONNECTION,),
            operation=operation,
            ticker="MSFT",
        )
        seed_operation_intent(session, operation, sequence=9, ticker="NVDA")  # planned
        operation_id = operation.id
        facts = load_intent_facts(session, [operation_id])[operation_id]
        assert [f.state for f in facts] == [
            SubmissionIntentState.REGISTERED_UNSENT,
            SubmissionIntentState.NOT_SENT,
            SubmissionIntentState.PLANNED,
        ]
        assert facts[2].evidence is None

    item = client.get(f"{BASE}/{operation_id}").json()
    assert item["unresolved_intents"] == []
    assert _gate() is None


# ---------------------------------------------------------------------------
# WR-08 / V-3: the retry_existing guard on the shared verdict
# ---------------------------------------------------------------------------

R = AttemptOutcomeClass.REJECTED
PRE = AttemptOutcomeClass.PRE_CONNECTION
DEADLINE = AttemptOutcomeClass.DEADLINE_EXPIRED
AMBIG = AttemptOutcomeClass.AMBIGUOUS
ONE = [("NVDA", "2", "500")]
TWO = [("AAPL", "10", "120"), ("MSFT", "5", "300")]
SECOND_FIRST = [("AAPL", "10", "120"), ("NVDA", "2", "500")]


@pytest.fixture()
def paper_seams(monkeypatch: pytest.MonkeyPatch) -> None:
    allow_paper_execution(monkeypatch)
    allow_direct_paper_execution(monkeypatch)


# How the guard is reached. An intent already bound to its order whose history is REJECTED,
# ambiguous or unfinished is never loaded as sendable (``derive_intent_state``), so the loop never
# registers it again. The guard runs for a PLANNED intent whose identity resolves to an OLDER
# retryable order (same strategy / session / symbol / side / quantity) that is proven not sent
# when the operation is started and gains its history afterwards. These tests drive exactly that
# shape through the product loop: operation 1 registers NVDA (not sent) and is ended; operation 2
# is [AAPL, NVDA]; while AAPL's POST is in flight the arranged attempt rows land on the older NVDA
# order, AAPL is rejected (the loop continues) and NVDA is registered through the retry guard.


def _older_retryable_order(status: OrderLifecycleState) -> tuple[uuid.UUID, str]:
    run = evaluation(ONE, verified=False)
    report = _start(ScriptedExecutionService(["not_sent"]), risk_run_id=run)
    assert report.result_summary["operation"]["reason"] == "broker_unavailable"
    end_all_open_operations()  # the unsent intent is cancelled_unsent; the order stays retryable
    with session_scope(load_settings()) as session:
        order = session.execute(select(PaperOrder)).scalar_one()
        order_id, client_order_id = order.id, order.client_order_id
        session.execute(update(PaperOrder).where(PaperOrder.id == order_id).values(status=status))
    return order_id, client_order_id


def _second_operation(
    older: uuid.UUID,
    history: list[tuple[AttemptOutcomeClass | None, int | None]],
    *,
    when: str = "during_post",
    monkeypatch: pytest.MonkeyPatch | None = None,
) -> tuple[ScriptedExecutionService, Any]:
    """Operation 2 over [AAPL, NVDA]. ``when`` places the arranged attempt rows on the older NVDA
    order: ``during_post`` (while AAPL's POST is in flight: the per-intent permission check of
    NVDA, which reads the strategy gate, runs AFTER them) or ``after_permission`` (committed by
    another connection between NVDA's permission check and its registration: the window only the
    registration guard covers)."""

    run = evaluation(SECOND_FIRST, digest="bars-corrected")

    def history_lands() -> None:
        for number, (outcome, status) in enumerate(history, start=2):
            seed_attempt_row(
                load_settings(), older, number=number, outcome=outcome, http_status=status
            )

    if when == "during_post":
        service = ScriptedExecutionService([(history_lands, "reject"), "accept"])
    else:
        assert monkeypatch is not None
        service = ScriptedExecutionService(["reject", "accept"])
        real = submit_orders_module._candidate_for_view
        landed: list[bool] = []

        def racing(session: Any, view: Any, risk_run_id: uuid.UUID) -> Any:
            if view.symbol == "NVDA" and not landed:
                landed.append(True)
                history_lands()
            return real(session, view, risk_run_id)

        monkeypatch.setattr(submit_orders_module, "_candidate_for_view", racing)
    try:
        return service, _start(service, risk_run_id=run)
    except AmbiguousOrderSubmissionError as parked:  # the guard parks UNKNOWN and re-raises
        return service, parked


def _order(order_id: uuid.UUID) -> PaperOrder:
    with session_scope(load_settings()) as session:
        order = session.get(PaperOrder, order_id)
        assert order is not None
        session.expunge(order)
        return order


def _events(order_id: uuid.UUID, event_type: str) -> int:
    with session_scope(load_settings()) as session:
        return int(
            session.execute(
                select(func.count())
                .select_from(ExecutionEvent)
                .where(
                    ExecutionEvent.paper_order_id == order_id,
                    ExecutionEvent.event_type == event_type,
                )
            ).scalar_one()
        )


def _order_event_types(order_id: uuid.UUID) -> list[str]:
    with session_scope(load_settings()) as session:
        return [
            f"{row.event_type.value}:{row.outcome.value}"
            for row in session.execute(
                select(OrderEvent)
                .where(OrderEvent.paper_order_id == order_id)
                .order_by(OrderEvent.created_at, OrderEvent.id)
            ).scalars()
        ]


def _gate_for_strategy() -> GateCode | None:
    with session_scope(load_settings()) as session:
        return strategy_recovery_status(session, "trend_following_daily").gate_code


def _max_intent_version() -> int:
    with session_scope(load_settings()) as session:
        return int(session.execute(select(func.max(PaperOrder.intent_version))).scalar_one())


WHEN = pytest.mark.parametrize("when", ["during_post", "after_permission"])


@WHEN
@pytest.mark.parametrize(
    "status", [PENDING, FAILED], ids=["pending_submission", "submission_failed"]
)
def test_rejected_history_is_applied_not_parked_unknown(
    migrated_paper_db: str,
    paper_seams: None,
    monkeypatch: pytest.MonkeyPatch,
    status: OrderLifecycleState,
    when: str,
) -> None:  # noqa: F811
    older, cid = _older_retryable_order(status)

    service, report = _second_operation(older, [(R, 403)], when=when, monkeypatch=monkeypatch)

    assert [i.symbol for i in service.submitted_intents] == ["AAPL"]  # NVDA: zero POST
    assert service.post_attempts == 1
    assert attempt_outcomes(cid) == [(1, "pre_connection"), (2, "rejected")]  # no new attempt
    order = _order(older)
    assert order.status is OrderLifecycleState.REJECTED
    assert order.broker_order_id is None
    assert _events(older, "submission_rejection_recorded") == 1
    assert _events(older, "submission_outcome_uncertain") == 0  # no uncertainty manufactured
    types = _order_event_types(older)
    assert "broker_rejected:accepted" in types
    # the rejection is recorded on the REGISTERING run (operation 2's run), not the origin run
    with session_scope(load_settings()) as session:
        rejected_row = session.execute(
            select(OrderEvent).where(
                OrderEvent.paper_order_id == older,
                OrderEvent.event_type == OrderTransitionEventType.BROKER_REJECTED,
            )
        ).scalar_one()
        registering_run = session.execute(
            select(PaperOrder.strategy_run_id).where(PaperOrder.client_order_id != cid)
        ).scalar_one()
    assert rejected_row.strategy_run_id == registering_run
    assert rejected_row.strategy_run_id != order.strategy_run_id  # origin run unchanged
    assert not any(t.startswith("broker_status_unknown") for t in types)
    summary = report.result_summary
    assert summary["operation"]["state"] == "completed"  # the loop continued; every intent rejected
    assert {o["client_order_id"] for o in summary["rejected_orders"]} >= {cid}
    assert len(summary["rejected_orders"]) == 2
    with session_scope(load_settings()) as session:
        job_free = session.execute(
            select(func.count())
            .select_from(ExecutionEvent)
            .where(ExecutionEvent.event_type == "submission_outcome_uncertain")
        ).scalar_one()
    assert job_free == 0
    assert _gate_for_strategy() is None


@WHEN
@pytest.mark.parametrize(
    "history",
    [
        [(AMBIG, None)],
        [(None, None)],
        [(AMBIG, None), (R, 403)],
        [(None, None), (R, 403)],
    ],
    ids=["ambiguous", "unfinished", "ambiguous-then-rejected", "unfinished-then-rejected"],
)
def test_ambiguous_or_unfinished_history_dominates_a_rejection_and_is_never_posted(
    migrated_paper_db: str,
    paper_seams: None,
    monkeypatch: pytest.MonkeyPatch,
    history: list[tuple[AttemptOutcomeClass | None, int | None]],
    when: str,
) -> None:  # noqa: F811
    older, cid = _older_retryable_order(FAILED)

    service, outcome = _second_operation(older, history, when=when, monkeypatch=monkeypatch)

    assert [i.symbol for i in service.submitted_intents] == ["AAPL"]  # NVDA: zero POST
    assert service.post_attempts == 1
    if when == "after_permission":
        # the registration guard parks the order UNKNOWN (unchanged) and raises
        assert isinstance(outcome, AmbiguousOrderSubmissionError)
        assert _order(older).status is OrderLifecycleState.UNKNOWN
        assert _events(older, "submission_outcome_uncertain") == 1
        assert operation_row().reason == "outcome_unresolved"
    else:  # the per-intent permission check (strategy gate) stops it first
        operation = outcome.result_summary["operation"]
        assert (operation["state"], operation["reason"]) == ("paused", "outcome_unresolved")
    order = _order(older)
    assert order.status is not OrderLifecycleState.REJECTED  # never applied as a rejection
    assert _events(older, "submission_rejection_recorded") == 0
    assert "broker_rejected:accepted" not in _order_event_types(older)
    assert len(attempt_outcomes(cid)) == 1 + len(history)  # no new attempt row
    assert _gate_for_strategy() is GateCode.OUTCOME_UNRESOLVED


@WHEN
def test_pre_connection_history_is_still_sent_once(
    migrated_paper_db: str, paper_seams: None, monkeypatch: pytest.MonkeyPatch, when: str
) -> None:  # noqa: F811
    older, cid = _older_retryable_order(FAILED)

    service, _report = _second_operation(
        older, [(DEADLINE, None)], when=when, monkeypatch=monkeypatch
    )

    assert [i.symbol for i in service.submitted_intents] == ["AAPL", "NVDA"]
    assert [i.client_order_id for i in service.submitted_intents][1] == cid  # NVDA sent once
    assert attempt_outcomes(cid)[:2] == [(1, "pre_connection"), (2, "deadline_expired")]
    assert _order(older).broker_order_id is not None
    assert _events(older, "submission_rejection_recorded") == 0


def test_recorded_rejection_is_never_resent_by_continue(
    migrated_paper_db: str, paper_seams: None
) -> None:  # noqa: F811
    """An intent already bound to its order with a recorded rejection is not loaded as sendable:
    Continue never POSTs it (and the planned intent behind it is still sent normally)."""

    run = evaluation(TWO, verified=False)
    _start(ScriptedExecutionService(["not_sent"]), risk_run_id=run)  # AAPL registered, unsent
    (_intent, cid), _second = intent_rows()
    with session_scope(load_settings()) as session:
        order_id = session.execute(
            select(PaperOrder.id).where(PaperOrder.client_order_id == cid)
        ).scalar_one()
    seed_attempt_row(load_settings(), order_id, number=2, outcome=R, http_status=403)
    finish_jobs()
    seed_clean_reconciliation_after_now()
    service = ScriptedExecutionService(["accept"])

    run_continue(service)

    assert cid not in [i.client_order_id for i in service.submitted_intents]
    assert [i.symbol for i in service.submitted_intents] == ["MSFT"]
    assert attempt_outcomes(cid) == [(1, "pre_connection"), (2, "rejected")]
    assert _gate_for_strategy() is None  # the rejected history is an established outcome


def _a_recorded_rejection_applied() -> tuple[uuid.UUID, int]:
    older, _cid = _older_retryable_order(FAILED)
    _service, report = _second_operation(older, [(R, 403)])
    assert report.result_summary["operation"]["state"] == "completed"
    assert _order(older).status is OrderLifecycleState.REJECTED
    finish_jobs()
    return older, count(PaperOrder)


def test_recorded_rejection_same_fingerprint_new_start_is_replay_of_earlier_decision(
    migrated_paper_db: str, paper_seams: None
) -> None:  # noqa: F811
    _older, orders = _a_recorded_rejection_applied()
    again = evaluation(
        ONE, digest="bars-corrected", as_of="2024-01-09"
    )  # same decision fingerprint
    service = ScriptedExecutionService(["accept"])

    report = _start(service, risk_run_id=again)

    assert dispositions(report) == [("NVDA", "buy", "replay_of_earlier_decision")]
    assert service.post_attempts == 0
    assert count(PaperOrder) == orders
    assert _max_intent_version() == 1


def test_recorded_rejection_different_quantity_new_start_is_action_already_submitted(
    migrated_paper_db: str, paper_seams: None
) -> None:  # noqa: F811
    _older, orders = _a_recorded_rejection_applied()
    changed = evaluation([("NVDA", "1", "500")], digest="bars-corrected-2")  # a BUY, flat portfolio
    service = ScriptedExecutionService(["accept"])

    report = _start(service, risk_run_id=changed)

    assert dispositions(report) == [("NVDA", "buy", "action_already_submitted")]
    assert service.post_attempts == 0
    assert count(PaperOrder) == orders
    assert _max_intent_version() == 1
