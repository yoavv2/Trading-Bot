"""R3 recovery read and the REC-01 broker-statement control (05 R3 / M14, 20.1-10).

``GET /api/v1/jobs/{id}/recovery`` is read-only and bounded; ``POST
/api/v1/recovery/intents/{id}/broker-statement`` is the only new mutating route and records
audited EVIDENCE ONLY (round 5, 2026-10-04): it never resolves an intent and never authorizes
a resend.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from tests.support.paper_eligibility import allow_paper_execution
from tests.support.query_counter import count_queries
from tests.support.recovery_fixtures import (
    OTHER,
    OWNER,
    at,
    seed_account_run,
    seed_intent,
    seed_job,
    seed_paper_run,
    seed_uncertain_session,
)
from tests.test_job_operations_e2e import (
    job_operations_env,
    migrated_backtest_db,
    strategy_config_override,
)
from tests.test_paper_session_job_e2e import PAYLOAD, BrokerFakes, paper_jobs_env

from trading_platform.api.app import create_app
from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.db.models import (
    AttemptOutcomeClass,
    ExecutionEvent,
    Job,
    JobMutation,
    OrderLifecycleState,
    OrderSubmissionAttempt,
    PaperOrder,
    RecoveryRecord,
    StrategyRun,
    StrategyRunType,
)
from trading_platform.db.session import get_engine, session_scope
from trading_platform.services import recovery

# Fixtures consumed by pytest name; re-exported so ruff F401 passes.
__all__ = [
    "job_operations_env",
    "migrated_backtest_db",
    "paper_jobs_env",
    "strategy_config_override",
]

AMBIGUOUS = AttemptOutcomeClass.AMBIGUOUS
STATEMENT_PATH = "/api/v1/recovery/intents/{intent_id}/broker-statement"


@pytest.fixture(autouse=True)
def _eligible_paper_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    allow_paper_execution(monkeypatch)


def _arrange(builder: Callable[[Any], Any]) -> Any:
    with session_scope(load_settings()) as session:
        return builder(session)


def _statement_url(intent_id: Any) -> str:
    return STATEMENT_PATH.format(intent_id=intent_id)


def _body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "statement": "not_received",
        "reference": "broker-case-4711",
        "reason": "Broker support confirmed no such order.",
    }
    body.update(overrides)
    return body


def _write_counts() -> tuple[int, ...]:
    with session_scope(load_settings()) as session:
        return tuple(
            session.execute(select(func.count()).select_from(model)).scalar_one()
            for model in (
                RecoveryRecord,
                StrategyRun,
                ExecutionEvent,
                Job,
                JobMutation,
                PaperOrder,
                OrderSubmissionAttempt,
            )
        )


def _ambiguous_intent(strategy_id: str = OWNER) -> tuple[uuid.UUID, uuid.UUID]:
    def build(session: Any) -> tuple[uuid.UUID, uuid.UUID]:
        job, _run, order = seed_uncertain_session(session, strategy_id=strategy_id)
        return job.id, order.id

    return _arrange(build)


# ---------------------------------------------------------------------------
# R3: GET /api/v1/jobs/{id}/recovery
# ---------------------------------------------------------------------------


def test_r3_shape_over_the_29_sep_fixture(paper_jobs_env: BrokerFakes) -> None:
    def build(session: Any) -> list[uuid.UUID]:
        first = seed_job(session, completed_at=at(0))
        second = seed_job(session, completed_at=at(1))
        sync = seed_job(session, job_type="broker-order-sync", completed_at=at(2))
        return [first.id, second.id, sync.id]

    job_ids = _arrange(build)
    with TestClient(create_app()) as client:
        for job_id in job_ids:
            response = client.get(f"/api/v1/jobs/{job_id}/recovery")
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["job_id"] == str(job_id)
            assert body["strategy_id"] == OWNER
            assert body["resolved"] is False
            assert body["gate_code"] == "reconciliation_required"
            assert len(body["intents"]) == 1
            row = body["intents"][0]
            assert row["classification"] == "nothing_submitted"
            assert row["intent_id"] is None
            assert row["resubmission_permitted"] is False
            assert row["statement"] is None
            datetime.fromisoformat(body["as_of"])


def test_r3_shape_for_an_ambiguous_intent_carries_the_evidence_package(
    paper_jobs_env: BrokerFakes,
) -> None:
    job_id, order_id = _ambiguous_intent()
    with TestClient(create_app()) as client:
        body = client.get(f"/api/v1/jobs/{job_id}/recovery").json()
    assert body["resolved"] is False
    assert body["gate_code"] == "outcome_unresolved"
    (row,) = body["intents"]
    assert row["intent_id"] == str(order_id)
    assert row["classification"] == "not_found"
    assert row["blocking"] is True
    assert row["resubmission_permitted"] is False
    assert row["resubmission_reason"] == "resubmission_unavailable"
    (package,) = body["evidence_package"]
    assert package["intent_id"] == str(order_id)
    assert package["client_order_id"] == row["client_order_id"]
    assert package["complete"] is False
    assert package["grace_period_seconds"] == 300
    (attempt,) = package["attempts"]
    assert attempt["outcome_class"] == "ambiguous"
    datetime.fromisoformat(attempt["started_at"])
    assert {"lookup_results", "scan_results", "fill_reference_results", "exposure_results"} <= set(
        package
    )


def test_r3_unknown_job_is_404(paper_jobs_env: BrokerFakes) -> None:
    with TestClient(create_app()) as client:
        assert client.get(f"/api/v1/jobs/{uuid.uuid4()}/recovery").status_code == 404


class _TerminatedView:
    def state_for_intent(self, session: Any, paper_order_id: uuid.UUID) -> str:
        return "terminated"


def test_r3_lists_the_same_intent_before_and_after_the_operation_ends(
    paper_jobs_env: BrokerFakes,
) -> None:
    """J-2: ending the operation changes nothing about the intent or the gate."""

    job_id, order_id = _ambiguous_intent()
    with session_scope(load_settings()) as session:
        ended = recovery.get_job_recovery(session, job_id, operation_view=_TerminatedView())
        never = recovery.get_job_recovery(session, job_id)
    assert [i["intent_id"] for i in ended["intents"]] == [str(order_id)]
    assert ended["resolved"] is False and never["resolved"] is False
    assert ended["gate_code"] == never["gate_code"] == "outcome_unresolved"
    assert ended["intents"][0]["blocking"] is True
    assert ended["intents"][0]["resubmission_permitted"] is False


def test_r3_is_read_only_and_bounded(paper_jobs_env: BrokerFakes) -> None:
    job_id, _order = _ambiguous_intent()

    def read_statements() -> list[str]:
        with TestClient(create_app()) as client:
            with count_queries(get_engine(load_settings())) as counter:
                assert client.get(f"/api/v1/jobs/{job_id}/recovery").status_code == 200
        return list(counter.statements)

    small = read_statements()
    assert not [s for s in small if s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]

    def history(session: Any) -> None:
        for index in range(10):
            job = seed_job(session, completed_at=at(index + 10))
            run = seed_paper_run(session, job)
            seed_intent(session, run, status=OrderLifecycleState.UNKNOWN, attempts=(AMBIGUOUS,))

    before = _write_counts()
    _arrange(history)
    large = read_statements()
    assert len(large) == len(small)
    assert _write_counts()[0] == before[0]


# ---------------------------------------------------------------------------
# M14: POST /api/v1/recovery/intents/{id}/broker-statement
# ---------------------------------------------------------------------------


def test_statement_records_audited_evidence_and_replays_idempotently(
    paper_jobs_env: BrokerFakes,
) -> None:
    _job_id, order_id = _ambiguous_intent()
    with TestClient(create_app()) as client:
        first = client.post(_statement_url(order_id), json=_body())
        assert first.status_code == 200, first.text
        payload = first.json()
        assert payload["intent_id"] == str(order_id)
        assert payload["statement"] == "not_received"
        assert payload["changed"] is True
        assert payload["classification"] == "not_found"
        assert payload["run_id"]
        after_first = _write_counts()

        replay = client.post(_statement_url(order_id), json=_body())
        assert replay.status_code == 200, replay.text
        assert replay.json()["changed"] is False
    after_replay = _write_counts()
    # One recovery record in total; the replay is audited (a second run) but adds no record.
    assert after_first[0] == after_replay[0] == 1
    assert after_replay[1] == after_first[1] + 1

    with session_scope(load_settings()) as session:
        run = session.execute(
            select(StrategyRun).where(StrategyRun.run_type == StrategyRunType.OPERATOR_CONTROL)
        ).scalars().first()
        assert run is not None
        events = session.execute(
            select(ExecutionEvent).where(
                ExecutionEvent.event_type == "recovery_broker_statement_recorded"
            )
        ).scalars().all()
        assert len(events) == 2


def test_statement_audit_run_is_attached_to_the_intents_own_strategy(
    paper_jobs_env: BrokerFakes,
) -> None:
    _job_id, order_id = _ambiguous_intent(strategy_id=OTHER)
    with TestClient(create_app()) as client:
        response = client.post(_statement_url(order_id), json=_body())
    assert response.status_code == 200, response.text
    with session_scope(load_settings()) as session:
        run = session.get(StrategyRun, uuid.UUID(response.json()["run_id"]))
        assert run is not None
        assert run.run_type is StrategyRunType.OPERATOR_CONTROL
        assert run.result_summary["strategy_id"] == OTHER
        assert OWNER not in str(run.result_summary["strategy_id"])


def test_statement_conflict_when_a_different_statement_is_recorded(
    paper_jobs_env: BrokerFakes,
) -> None:
    _job_id, order_id = _ambiguous_intent()
    with TestClient(create_app()) as client:
        assert client.post(_statement_url(order_id), json=_body()).status_code == 200
        before = _write_counts()
        conflict = client.post(
            _statement_url(order_id), json=_body(statement="order_record", reference="rec-1")
        )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "statement_conflict"
    assert _write_counts() == before


def test_statement_unknown_intent_is_404_with_zero_writes(paper_jobs_env: BrokerFakes) -> None:
    before = _write_counts()
    with TestClient(create_app()) as client:
        response = client.post(_statement_url(uuid.uuid4()), json=_body())
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "intent_not_found"
    assert _write_counts() == before


def test_statement_for_an_intent_not_on_the_missing_order_path_is_409(
    paper_jobs_env: BrokerFakes,
) -> None:
    def build(session: Any) -> uuid.UUID:
        job = seed_job(session, completed_at=at(0))
        run = seed_paper_run(session, job)
        order = seed_intent(
            session,
            run,
            status=OrderLifecycleState.FILLED,
            attempts=(AttemptOutcomeClass.ACCEPTED,),
            broker_order_id="brk-1",
            broker_status="filled",
        )
        return order.id

    order_id = _arrange(build)
    before = _write_counts()
    with TestClient(create_app()) as client:
        response = client.post(_statement_url(order_id), json=_body())
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "intent_not_on_missing_order_path"
    assert _write_counts() == before


@pytest.mark.parametrize(
    ("body", "code"),
    [
        (None, "invalid_control_request"),
        ([], "invalid_control_request"),
        ({**_body(), "previous_executor_terminated": True}, "invalid_control_request"),
        (_body(statement="withdrawn"), "invalid_control_target"),
        (_body(statement=None), "invalid_control_target"),
        (_body(reference=""), "invalid_broker_reference"),
        (_body(reference="x" * 501), "invalid_broker_reference"),
        (_body(reference="a\x00b"), "invalid_broker_reference"),
        (_body(reference=7), "invalid_broker_reference"),
        (_body(reason="   "), "invalid_control_reason"),
        (_body(reason="r" * 501), "invalid_control_reason"),
        (_body(reason="a\x00b"), "invalid_control_reason"),
    ],
)
def test_statement_typed_422_with_zero_writes(
    paper_jobs_env: BrokerFakes, body: Any, code: str
) -> None:
    _job_id, order_id = _ambiguous_intent()
    before = _write_counts()
    with TestClient(create_app()) as client:
        if body is None:
            response = client.post(
                _statement_url(order_id),
                content=b"not json",
                headers={"Content-Type": "application/json"},
            )
        else:
            response = client.post(_statement_url(order_id), json=body)
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == code
    assert _write_counts() == before


def test_statement_malformed_intent_id_is_a_typed_422(paper_jobs_env: BrokerFakes) -> None:
    with TestClient(create_app()) as client:
        response = client.post(_statement_url("not-a-uuid"), json=_body())
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_control_target"


def test_deeply_nested_body_is_a_typed_422(paper_jobs_env: BrokerFakes) -> None:
    _job_id, order_id = _ambiguous_intent()
    with TestClient(create_app()) as client:
        response = client.post(
            _statement_url(order_id),
            content=b"[" * 100000,
            headers={"Content-Type": "application/json"},
        )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_control_request"


def test_order_record_statement_does_not_resolve(paper_jobs_env: BrokerFakes) -> None:
    job_id, order_id = _ambiguous_intent()
    with TestClient(create_app()) as client:
        response = client.post(
            _statement_url(order_id), json=_body(statement="order_record", reference="rec-9")
        )
        assert response.status_code == 200, response.text
        body = client.get(f"/api/v1/jobs/{job_id}/recovery").json()
    assert body["resolved"] is False
    assert body["intents"][0]["statement"]["statement"] == "order_record"
    assert body["intents"][0]["blocking"] is True


def test_not_received_statement_route_is_evidence_only(paper_jobs_env: BrokerFakes) -> None:
    """Round 5 (2026-10-04): a non-receipt statement is audited evidence; it resolves nothing,
    permits no resend, and never contributes to resolution, even after a clean reconciliation."""

    job_id, order_id = _ambiguous_intent()
    with TestClient(create_app()) as client:
        before_classification = client.get(f"/api/v1/jobs/{job_id}/recovery").json()["intents"][0][
            "classification"
        ]
        counts_before = _write_counts()
        response = client.post(_statement_url(order_id), json=_body())
        assert response.status_code == 200, response.text
        assert response.json()["changed"] is True
        assert response.json()["classification"] == before_classification
        counts_after = _write_counts()
        # Only evidence and audit rows: no Job, PaperOrder or attempt row, no broker POST.
        assert counts_after[3:] == counts_before[3:]
        assert paper_jobs_env.execution.submitted_intents == []

        def assert_still_unresolved() -> None:
            body = client.get(f"/api/v1/jobs/{job_id}/recovery").json()
            assert body["resolved"] is False
            assert body["gate_code"] == "outcome_unresolved"
            (row,) = body["intents"]
            assert row["classification"] == before_classification
            assert row["statement"]["statement"] == "not_received"
            assert row["resubmission_permitted"] is False
            assert row["resubmission_reason"] == "resubmission_unavailable"
            fresh = client.post(
                "/api/v1/jobs",
                headers={"Idempotency-Key": f"fresh-{uuid.uuid4()}"},
                json={"job_type": "paper-session", "payload": PAYLOAD},
            )
            assert fresh.status_code == 409
            assert fresh.json()["detail"]["code"] == "outcome_unresolved"

        assert_still_unresolved()
        _arrange(lambda s: seed_account_run(s, completed_at=datetime.now(UTC)))
        assert_still_unresolved()
        assert paper_jobs_env.execution.submitted_intents == []
        assert _write_counts()[3:] == counts_before[3:]


def test_mutations_disabled_rejects_the_statement_with_zero_writes(
    paper_jobs_env: BrokerFakes, monkeypatch: pytest.MonkeyPatch
) -> None:
    _job_id, order_id = _ambiguous_intent()
    before = _write_counts()
    monkeypatch.setenv("TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED", "false")
    clear_settings_cache()
    with TestClient(create_app()) as client:
        response = client.post(_statement_url(order_id), json=_body())
    assert response.status_code == 403
    assert response.json()["detail"] == {"code": "mutations_disabled"}
    assert _write_counts() == before


def test_no_withdrawal_route_exists() -> None:
    """W-1 declined: nothing under /api/v1/recovery withdraws a statement, and the broker
    statement is the only mutating recovery route."""

    paths: set[tuple[str, str]] = set()
    for route in create_app().routes:
        candidates = (
            route.effective_candidates() if hasattr(route, "effective_candidates") else [route]
        )
        for candidate in candidates:
            path = str(getattr(candidate, "path", ""))
            assert "withdraw" not in path.lower()
            if path.startswith("/api/v1/recovery"):
                for method in set(getattr(candidate, "methods", set()) or set()):
                    paths.add((method, path))
    assert paths == {("POST", STATEMENT_PATH.replace("{intent_id}", "{intent_id}"))}
