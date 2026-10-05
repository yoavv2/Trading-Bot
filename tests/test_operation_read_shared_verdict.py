"""The operation read model, the End result and the retry guard read the shared verdict (20.1-28).

WR-01: ``GET /api/v1/execution-operations/{id}`` ``unresolved_intents`` and the End result list
exactly the intents whose ``classify_submission_evidence`` verdict is UNESTABLISHED, so they never
contradict the strategy gate. ``IntentFact.state`` (loop selection / lifecycle display) is NOT
changed by this.

WR-08 / V-3 (the ``retry_existing`` registration guard) is exercised further down the module.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import date, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from tests.support.calendar_facts import et, seed_calendar
from tests.support.migrated_db import migrated_database
from tests.support.operation_fixtures import seed_operation, seed_operation_intent
from tests.support.recovery_fixtures import (
    OWNER,
    at,
    seed_intent,
    seed_job,
    seed_operation_bound_intent,
    seed_paper_run,
)

from trading_platform.api.app import create_app
from trading_platform.core import clock
from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.db.models import (
    AttemptOutcomeClass,
    ExecutionOperationIntent,
    JobStatus,
    OrderLifecycleState,
    PaperOrder,
)
from trading_platform.db.session import session_scope
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
