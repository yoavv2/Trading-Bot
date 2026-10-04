"""R2 reads and the End operation control over HTTP (REC-02, 20.1-11, 05 R2 / M12).

The two GET routes are write-free and bounded; ``POST /api/v1/execution-operations/{id}/end`` is
the only new mutating route. It terminates the operation and its UNSENT intents only: no broker
order is cancelled, uncertain outcomes stay blocking, recovery evidence and attempt logs are
untouched, trading permission is unchanged.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from tests.support.calendar_facts import et, seed_calendar
from tests.support.migrated_db import migrated_database
from tests.support.operation_fixtures import (
    seed_operation,
    seed_operation_intent,
    seed_operation_job,
)
from tests.support.query_counter import count_queries
from tests.support.recovery_fixtures import OTHER, OWNER

from trading_platform.api.app import create_app
from trading_platform.core import clock
from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.db.models import (
    AttemptOutcomeClass,
    ExecutionEvent,
    ExecutionOperation,
    ExecutionOperationIntent,
    Job,
    JobStatus,
    KillSwitchState,
    OrderLifecycleState,
    OrderSubmissionAttempt,
    PaperOrder,
    RecoveryRecord,
    Strategy,
    StrategyRun,
    StrategyRunType,
    SystemControl,
)
from trading_platform.db.session import get_engine, session_scope
from trading_platform.services.execution.operations import NextAction
from trading_platform.services.operation_reads import OperationReadService

S = date(2025, 12, 2)
IN_WINDOW = et(2025, 12, 3, 10, 0)
PAST_CUTOFF = et(2025, 12, 3, 15, 50)
BASE = "/api/v1/execution-operations"
PRE_CONNECTION = AttemptOutcomeClass.PRE_CONNECTION
AMBIGUOUS = AttemptOutcomeClass.AMBIGUOUS
ACCEPTED = AttemptOutcomeClass.ACCEPTED
INTENT_STATES = {
    "planned",
    "registered_unsent",
    "not_sent",
    "submitted",
    "ambiguous",
    "rejected",
    "expired_unsent",
    "cancelled_unsent",
}


@pytest.fixture()
def api_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    monkeypatch.setenv("TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED", "true")
    with migrated_database(monkeypatch, "execution_operation_routes") as name:
        clear_settings_cache()
        seed_calendar(date(2025, 11, 24), date(2026, 1, 9))
        monkeypatch.setattr(clock, "now_utc", lambda: IN_WINDOW)
        yield name
    clear_settings_cache()


@pytest.fixture()
def client(api_db: str) -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        yield test_client


def _counts() -> tuple[int, ...]:
    models = (
        StrategyRun,
        ExecutionEvent,
        ExecutionOperation,
        ExecutionOperationIntent,
        RecoveryRecord,
        OrderSubmissionAttempt,
        PaperOrder,
        Job,
    )
    with session_scope(load_settings()) as session:
        return tuple(
            session.execute(select(func.count()).select_from(model)).scalar_one()
            for model in models
        )


def _row_snapshot() -> list[tuple[Any, ...]]:
    with session_scope(load_settings()) as session:
        operations = session.execute(
            select(
                ExecutionOperation.id,
                ExecutionOperation.state,
                ExecutionOperation.reason,
                ExecutionOperation.execution_epoch,
                ExecutionOperation.updated_at,
            ).order_by(ExecutionOperation.id)
        ).all()
        intents = session.execute(
            select(
                ExecutionOperationIntent.id,
                ExecutionOperationIntent.disposition,
            ).order_by(ExecutionOperationIntent.id)
        ).all()
    return [tuple(r) for r in operations] + [tuple(r) for r in intents]


def _seed_mixed(*, strategy: str = OWNER, state: str = "paused") -> uuid.UUID:
    """One operation whose intents reach every closed intent state except rejected ones the
    router can show (rejected is seeded too)."""

    with session_scope(load_settings()) as session:
        reason = {"paused": "awaiting_reconciliation", "running": None}[state]
        operation = seed_operation(
            session, strategy_id=strategy, state=state, reason=reason, session_date=S
        )
        seed_operation_intent(session, operation, sequence=1, ticker="AAA")  # planned
        seed_operation_intent(
            session,
            operation,
            sequence=2,
            ticker="BBB",
            order_status=OrderLifecycleState.PENDING_SUBMISSION,
        )  # registered_unsent
        seed_operation_intent(
            session,
            operation,
            sequence=3,
            ticker="CCC",
            attempts=[PRE_CONNECTION],
            order_status=OrderLifecycleState.SUBMISSION_FAILED,
        )  # not_sent
        seed_operation_intent(
            session,
            operation,
            sequence=4,
            ticker="DDD",
            attempts=[ACCEPTED],
            order_status=OrderLifecycleState.SUBMITTED,
            broker_order_id="broker-working",
        )  # submitted (a working order)
        seed_operation_intent(
            session,
            operation,
            sequence=5,
            ticker="EEE",
            attempts=[AMBIGUOUS],
            order_status=OrderLifecycleState.UNKNOWN,
        )  # ambiguous
        seed_operation_intent(
            session,
            operation,
            sequence=6,
            ticker="FFF",
            attempts=[AttemptOutcomeClass.REJECTED],
            order_status=OrderLifecycleState.REJECTED,
        )  # rejected
        seed_operation_intent(
            session, operation, sequence=7, ticker="GGG", disposition="expired_unsent"
        )
        seed_operation_intent(
            session, operation, sequence=8, ticker="HHH", disposition="cancelled_unsent"
        )
        return operation.id


# ---------------------------------------------------------------------------
# R2 reads
# ---------------------------------------------------------------------------


def test_list_and_detail_shape_with_every_intent_state(client: TestClient) -> None:
    operation_id = _seed_mixed()
    listed = client.get(BASE)
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["count"] == 1 and body["filters"] == {
        "strategy_id": None,
        "state": None,
        "limit": 20,
    }
    detail = client.get(f"{BASE}/{operation_id}")
    assert detail.status_code == 200
    item = detail.json()
    assert item == body["items"][0]
    assert {i["state"] for i in item["intents"]} == INTENT_STATES
    assert [i["sequence"] for i in item["intents"]] == list(range(1, 9))
    assert item["state"] == "paused" and item["reason"] == "awaiting_reconciliation"
    assert item["next_action"] == NextAction.SYNC_RECONCILE_THEN_CONTINUE.value
    assert item["next_action"] in {a.value for a in NextAction}
    assert item["will_end"] is False and item["as_of"]
    assert item["strategy_id"] == OWNER and item["as_of_session"] == "2025-12-02"
    assert [w["blocking_effect"] for w in item["working_orders"]] == [
        "working_order_commitments_unaccounted"
    ]
    assert [u["blocking_effect"] for u in item["unresolved_intents"]] == ["outcome_unresolved"]
    assert {"execution_epoch", "executor_job_id", "last_guarded_at", "jobs"} <= set(item)


def test_list_filters_ordering_and_typed_errors(client: TestClient) -> None:
    with session_scope(load_settings()) as session:
        first = seed_operation(session, state="completed", reason=None, session_date=S)
        second = seed_operation(
            session,
            state="terminated",
            reason="cancelled_by_operator",
            session_date=date(2025, 12, 1),
        )
        other = seed_operation(
            session, strategy_id=OTHER, state="running", reason=None, session_date=S
        )
        # Rows seeded in one transaction share created_at; order them explicitly.
        first.created_at = IN_WINDOW.replace(hour=11)
        second.created_at = IN_WINDOW.replace(hour=12)
        other.created_at = IN_WINDOW.replace(hour=13)
        first_id, second_id, other_id = first.id, second.id, other.id
    everything = client.get(BASE).json()["items"]
    assert {i["operation_id"] for i in everything} == {str(first_id), str(second_id), str(other_id)}
    assert [i["operation_id"] for i in everything] == [
        str(other_id),
        str(second_id),
        str(first_id),
    ]  # newest first
    mine = client.get(BASE, params={"strategy_id": OWNER}).json()["items"]
    assert {i["operation_id"] for i in mine} == {str(first_id), str(second_id)}
    only = client.get(BASE, params={"state": "terminated"}).json()["items"]
    assert [i["operation_id"] for i in only] == [str(second_id)]
    assert client.get(BASE, params={"limit": 1}).json()["count"] == 1
    unknown_strategy = client.get(BASE, params={"strategy_id": "nope"})
    assert unknown_strategy.status_code == 404
    assert unknown_strategy.json()["detail"]["code"] == "strategy_not_found"
    bad_state = client.get(BASE, params={"state": "flying"})
    assert bad_state.status_code == 422
    assert bad_state.json()["detail"]["code"] == "invalid_state_filter"
    assert client.get(BASE, params={"limit": 101}).status_code == 422
    assert client.get(BASE, params={"limit": 0}).status_code == 422
    missing = client.get(f"{BASE}/{uuid.uuid4()}")
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "operation_not_found"
    assert client.get(f"{BASE}/not-a-uuid").status_code == 404


def test_read_routes_write_nothing_and_report_will_end(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    operation_id = _seed_mixed()
    monkeypatch.setattr(clock, "now_utc", lambda: PAST_CUTOFF)
    before_rows, before_counts = _row_snapshot(), _counts()
    engine = get_engine(load_settings())
    with count_queries(engine) as counter:
        listed = client.get(BASE)
        detail = client.get(f"{BASE}/{operation_id}")
    for response in (listed, detail):
        assert response.status_code == 200
    item = detail.json()
    assert item["will_end"] is True
    assert item["state"] == "terminated" and item["reason"] == "execution_window_elapsed"
    assert (
        item["persisted_state"] == "paused"
        and item["persisted_reason"] == "awaiting_reconciliation"
    )
    assert item["next_action"] == "new_evaluation_required"
    assert listed.json()["items"][0]["will_end"] is True
    writes = [
        s
        for s in counter.statements
        if s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
    ]
    assert writes == []
    assert _row_snapshot() == before_rows and _counts() == before_counts


def test_reads_report_supersession_and_a_pending_takeover(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    with session_scope(load_settings()) as session:
        job = seed_operation_job(session, status=JobStatus.FAILED)
        operation = seed_operation(
            session, state="running", reason=None, session_date=S, jobs=[(job, "start")]
        )
        operation_id = operation.id
    item = client.get(f"{BASE}/{operation_id}").json()
    assert item["takeover_pending"] is True and item["state"] == "running"
    assert item["next_action"] == "sync_reconcile_then_continue"
    assert item["jobs"] == [{"job_id": str(job.id), "mode": "start", "status": "failed"}]
    monkeypatch.setattr(clock, "now_utc", lambda: et(2025, 12, 4, 10, 0))
    superseded = client.get(f"{BASE}/{operation_id}").json()
    assert superseded["reason"] == "evaluation_superseded" and superseded["will_end"] is True


def test_read_statement_counts_are_bounded_independent_of_history(client: TestClient) -> None:
    operation_id = _seed_mixed()
    engine = get_engine(load_settings())

    def measure() -> tuple[int, int]:
        with count_queries(engine) as listing:
            assert client.get(BASE).status_code == 200
        with count_queries(engine) as detail:
            assert client.get(f"{BASE}/{operation_id}").status_code == 200
        return listing.count, detail.count

    small = measure()
    with session_scope(load_settings()) as session:
        for index in range(10):
            operation = (
                seed_operation(
                    session,
                    strategy_id=OTHER,
                    state="terminated",
                    reason="cancelled_by_operator",
                    session_date=date(2025, 11, 25 + index % 3),
                )
                if index == 0
                else seed_operation(
                    session,
                    strategy_id=OTHER,
                    state="completed",
                    reason=None,
                    session_date=date(2025, 11, 24),
                    risk_run=None,
                )
            )
            seed_operation_intent(
                session,
                operation,
                sequence=1,
                ticker=f"X{index}",
                attempts=[ACCEPTED],
                order_status=OrderLifecycleState.FILLED,
                broker_order_id=f"b{index}",
            )
    large = measure()
    assert small == large
    assert small[0] <= 12 and small[1] <= 12


def test_operation_for_jobs_is_one_statement(api_db: str) -> None:
    with session_scope(load_settings()) as session:
        start = seed_operation_job(session, status=JobStatus.SUCCEEDED)
        cont = seed_operation_job(session, status=JobStatus.RUNNING)
        unlinked = seed_operation_job(session, status=JobStatus.SUCCEEDED)
        operation = seed_operation(
            session,
            state="paused",
            reason="kill_switch_tripped",
            session_date=S,
            jobs=[(start, "start"), (cont, "continue")],
        )
        operation_id = operation.id
        ids = [start.id, cont.id, unlinked.id]
    service = OperationReadService()
    with count_queries(get_engine(load_settings())) as counter:
        result = service.operation_for_jobs(ids)
    assert counter.count == 1
    expected = {"id": str(operation_id), "state": "paused", "reason": "kill_switch_tripped"}
    assert result == {ids[0]: expected, ids[1]: expected}
    assert service.operation_for_jobs([]) == {}


# ---------------------------------------------------------------------------
# End (M12)
# ---------------------------------------------------------------------------


def _end(client: TestClient, operation_id: Any, **body: Any) -> Any:
    return client.post(f"{BASE}/{operation_id}/end", json=body or {"reason": "operator decision"})


def _control_state() -> tuple[str, list[tuple[str, str]]]:
    with session_scope(load_settings()) as session:
        kill = session.execute(select(SystemControl.state)).scalars().first()
        strategies = session.execute(
            select(Strategy.strategy_id, Strategy.status).order_by(Strategy.strategy_id)
        ).all()
    return (
        kill.value if isinstance(kill, KillSwitchState) else str(kill),
        [(a, b.value) for a, b in strategies],
    )


@pytest.mark.parametrize("job_status", [JobStatus.QUEUED, JobStatus.RUNNING])
def test_end_refused_while_continuation_job_running(
    client: TestClient, job_status: JobStatus
) -> None:
    # The continuation Job is seeded as a Job row directly (payload {mode, operation_id}):
    # the paper-session continue payload mode itself is added by 20.1-16.
    with session_scope(load_settings()) as session:
        operation = seed_operation(
            session, state="paused", reason="awaiting_reconciliation", session_date=S
        )
        job = seed_operation_job(
            session,
            status=job_status,
            payload={"mode": "continue", "operation_id": str(operation.id)},
        )
        operation_id, job_id = operation.id, job.id
    before = _counts()
    response = _end(client, operation_id)
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "operation_running" and detail["running_job_ids"] == [str(job_id)]
    assert _counts() == before  # zero writes, audit run included


def test_end_terminates_unsent_only_without_cancelling_broker_orders(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trading_platform.services import alpaca

    def _no_client(*_a: object, **_k: object) -> None:
        raise AssertionError("End must not construct a broker client (no cancel path exists)")

    monkeypatch.setattr(alpaca.AlpacaClient, "__init__", _no_client)
    operation_id = _seed_mixed()
    before = _counts()
    control_before = _control_state()
    response = _end(client, operation_id, reason="  ending it  ")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "terminated" and body["reason"] == "cancelled_by_operator"
    assert body["changed"] is True and body["trading_permission_changed"] is False
    assert len(body["unsent_cancelled"]) == 3  # planned, registered_unsent, not_sent
    assert [w["blocking_effect"] for w in body["working_orders"]] == [
        "working_order_commitments_unaccounted"
    ]
    assert [u["blocking_effect"] for u in body["unresolved_intents"]] == ["outcome_unresolved"]
    after = _counts()
    # Only the audit run + its event are new; operations, intents, orders, attempts,
    # recovery records and Jobs are unchanged in number.
    assert after[0] == before[0] + 1 and after[1] == before[1] + 1
    assert after[2:] == before[2:]
    assert _control_state() == control_before
    detail = client.get(f"{BASE}/{operation_id}").json()
    states = {i["sequence"]: i["state"] for i in detail["intents"]}
    assert states[1] == states[2] == states[3] == "cancelled_unsent"
    assert states[4] == "submitted" and states[5] == "ambiguous"
    assert detail["working_orders"] and detail["unresolved_intents"]
    assert detail["next_action"] == "new_evaluation_required"
    with session_scope(load_settings()) as session:
        run = session.get(StrategyRun, uuid.UUID(body["run_id"]))
        assert run is not None and run.run_type == StrategyRunType.OPERATOR_CONTROL
        strategy = session.get(Strategy, run.strategy_id)
        assert strategy is not None and strategy.strategy_id == OWNER
        event = session.execute(
            select(ExecutionEvent).where(ExecutionEvent.event_type == "execution_operation_ended")
        ).scalar_one()
        assert event.strategy_run_id == run.id
        assert event.details["operator_reason"] == "ending it"
        assert sorted(event.details["cancelled_intent_ids"]) == sorted(body["unsent_cancelled"])


def test_end_is_attached_to_the_operations_own_strategy(client: TestClient) -> None:
    with session_scope(load_settings()) as session:
        operation = seed_operation(
            session,
            strategy_id=OTHER,
            state="paused",
            reason="kill_switch_tripped",
            session_date=S,
        )
        operation_id = operation.id
    run_id = _end(client, operation_id).json()["run_id"]
    with session_scope(load_settings()) as session:
        run = session.get(StrategyRun, uuid.UUID(run_id))
        assert run is not None
        strategy = session.get(Strategy, run.strategy_id)
        assert strategy is not None and strategy.strategy_id == OTHER


def test_second_end_is_idempotent_and_completed_is_not_open(client: TestClient) -> None:
    with session_scope(load_settings()) as session:
        paused = seed_operation(
            session, state="paused", reason="kill_switch_tripped", session_date=S
        )
        done = seed_operation(
            session, strategy_id=OTHER, state="completed", reason=None, session_date=S
        )
        paused_id, done_id = paused.id, done.id
    first = _end(client, paused_id)
    assert first.status_code == 200 and first.json()["changed"] is True
    second = _end(client, paused_id)
    assert second.status_code == 200
    assert second.json()["changed"] is False and second.json()["state"] == "terminated"
    assert second.json()["reason"] == "cancelled_by_operator"
    completed = _end(client, done_id)
    assert completed.status_code == 409
    assert completed.json()["detail"]["code"] == "operation_not_open"
    assert completed.json()["detail"]["state"] == "completed"


def test_end_after_the_window_elapsed_reports_the_expiry(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    operation_id = _seed_mixed()
    monkeypatch.setattr(clock, "now_utc", lambda: PAST_CUTOFF)
    body = _end(client, operation_id).json()
    assert body["state"] == "terminated" and body["reason"] == "execution_window_elapsed"
    assert body["ended_by_expiry"] is True and body["unsent_cancelled"] == []


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({"reason": ""}, "invalid_control_reason"),
        ({"reason": "   "}, "invalid_control_reason"),
        ({"reason": 5}, "invalid_control_reason"),
        ({"reason": "x" * 501}, "invalid_control_reason"),
        ({"reason": "a\x00b"}, "invalid_control_reason"),
        ({}, "invalid_control_reason"),
        ({"reason": "ok", "extra": 1}, "invalid_control_request"),
        ({"state": "terminated", "reason": "ok"}, "invalid_control_request"),
    ],
)
def test_end_typed_422_with_zero_writes(
    client: TestClient, payload: dict[str, Any], code: str
) -> None:
    operation_id = _seed_mixed()
    before, rows = _counts(), _row_snapshot()
    response = client.post(f"{BASE}/{operation_id}/end", json=payload)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == code
    assert _counts() == before and _row_snapshot() == rows


def test_end_rejects_non_json_and_non_object_bodies(client: TestClient) -> None:
    operation_id = _seed_mixed()
    before = _counts()
    for content in (b"not json", b"[1, 2]", b'"reason"'):
        response = client.post(
            f"{BASE}/{operation_id}/end",
            content=content,
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "invalid_control_request"
    assert _counts() == before


def test_end_unknown_operation_is_404_with_zero_writes(client: TestClient) -> None:
    before = _counts()
    missing = _end(client, uuid.uuid4())
    assert missing.status_code == 404 and missing.json()["detail"]["code"] == "operation_not_found"
    assert _end(client, "not-a-uuid").status_code == 404
    assert _counts() == before


def test_end_is_forbidden_when_mutations_are_disabled(
    api_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    operation_id = _seed_mixed()
    monkeypatch.setenv("TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED", "false")
    clear_settings_cache()
    before, rows = _counts(), _row_snapshot()
    with TestClient(create_app()) as disabled:
        response = disabled.post(f"{BASE}/{operation_id}/end", json={"reason": "x"})
        malformed = disabled.post(f"{BASE}/not-a-real-operation/end", json={"bad": True})
    for item in (response, malformed):
        assert item.status_code == 403
        assert item.json()["detail"] == {"code": "mutations_disabled"}
    assert _counts() == before and _row_snapshot() == rows


def test_no_cancel_route_and_no_withdraw_route_exist() -> None:
    app = create_app()
    paths: set[str] = set()
    for route in app.routes:
        candidates = (
            route.effective_candidates() if hasattr(route, "effective_candidates") else [route]
        )
        for candidate in candidates:
            paths.add(str(getattr(candidate, "path", "")))
    operation_paths = {p for p in paths if p.startswith(BASE)}
    assert operation_paths == {BASE, f"{BASE}/{{operation_id}}", f"{BASE}/{{operation_id}}/end"}
    lowered = " ".join(sorted(paths)).lower()
    assert "withdraw" not in lowered
    assert not any("cancel" in p.lower() for p in operation_paths)
