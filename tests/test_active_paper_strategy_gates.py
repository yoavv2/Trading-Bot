"""PAPER-01 gates: submit-time (typed 409), admission serialization (SER) and
run-time ownership re-checks before every broker action (D-03).

The ownership fixtures here are DIRECT test-only writes through
``tests/support/paper_ownership.py``; the control that normally changes the
owner is 20.1-12 and is deliberately not used.
"""

from __future__ import annotations

import ast
import threading
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from tests.support.paper_eligibility import allow_paper_execution
from tests.support.paper_ownership import (
    clear_active_paper_strategy,
    seed_registered_strategy,
    set_active_paper_strategy,
)
from tests.support.query_counter import count_queries
from tests.test_backtest_runner import migrated_backtest_db, strategy_config_override  # noqa: F401
from tests.test_job_operations_e2e import _run_worker_once, job_operations_env  # noqa: F401
from tests.test_paper_execution import (
    FakeExecutionService,
    _seed_approved_risk_batch,
    migrated_paper_db,  # noqa: F401
)
from tests.test_paper_session_job_e2e import (
    PAYLOAD,
    RECONCILIATION_PAYLOAD,
    STRATEGY_ID,
    BrokerFakes,
    paper_jobs_env,  # noqa: F401
)
from tests.test_risk_pipeline import (
    _seed_symbol_and_bar,
    migrated_risk_db,  # noqa: F401
)
from tests.test_risk_pipeline import (
    strategy_config_override as risk_strategy_config_override,  # noqa: F401
)

from trading_platform.api.app import create_app
from trading_platform.core.settings import load_settings
from trading_platform.db.models import (
    ActivePaperStrategy,
    ExecutionEvent,
    Job,
    JobFailureReason,
    JobStatus,
    OrderLifecycleState,
    PaperOrder,
    Strategy,
    StrategyRun,
    StrategyRunStatus,
    StrategyRunType,
)
from trading_platform.db.session import get_engine, get_session_factory, session_scope
from trading_platform.jobs.handlers import payload_fields as payload_fields_module
from trading_platform.jobs.handlers.backtest_submission import BacktestSubmissionSpec
from trading_platform.jobs.handlers.paper_session_submission import (
    PaperSessionSubmissionSpec,
    PaperSessionSubmitConflict,
)
from trading_platform.jobs.handlers.reconciliation_submission import (
    ReconciliationSubmissionSpec,
    ReconciliationSubmitConflict,
)
from trading_platform.jobs.handlers.risk_evaluation_submission import RiskEvaluationSubmissionSpec
from trading_platform.jobs.registry import JobSubmissionConflictError, build_default_registry
from trading_platform.orchestration.job_mutations import JobOrchestrationService
from trading_platform.services import operator_controls
from trading_platform.services.active_paper_strategy import (
    BLOCKED_REASON_NOT_ACTIVE_PAPER_STRATEGY,
    ActivePaperStrategyUnavailableError,
    OwnershipBlock,
)
from trading_platform.services.alpaca import BrokerAccountSnapshot
from trading_platform.services.execution import (
    OrderIntent,
    OrderSubmissionResult,
    run_paper_order_submission,
    run_paper_session,
)
from trading_platform.services.operator_controls import OperatorControlService
from trading_platform.services.risk import run_risk_evaluation

__all__ = [
    "job_operations_env",
    "migrated_backtest_db",
    "migrated_paper_db",
    "migrated_risk_db",
    "paper_jobs_env",
    "risk_strategy_config_override",
    "strategy_config_override",
]

OTHER = "donchian_breakout_daily"
SESSION = date(2024, 1, 5)
_ROOT = Path(__file__).resolve().parents[1]

_SPEC_BY_JOB_TYPE = {
    "paper-session": (PaperSessionSubmissionSpec, PaperSessionSubmitConflict, PAYLOAD),
    "reconciliation": (ReconciliationSubmissionSpec, ReconciliationSubmitConflict, RECONCILIATION_PAYLOAD),
}
_STATES = {
    "owner": (STRATEGY_ID, None),
    "non_owner": (OTHER, "strategy_not_active_paper_strategy"),
    "no_owner": (None, "no_active_paper_strategy"),
}


@pytest.fixture(autouse=True)
def _eligible_paper_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    """This module's subject is not eligibility (COR-04): see
    tests/support/paper_eligibility.py. Real eligibility is tested in
    tests/test_paper_session_eligibility.py."""

    allow_paper_execution(monkeypatch)


def _arrange_owner(state: str) -> str | None:
    """Seed TREND and OTHER (enabled) and make the owner match ``state``."""
    settings = load_settings()
    seed_registered_strategy(settings, STRATEGY_ID)
    seed_registered_strategy(settings, OTHER)
    owner, expected_code = _STATES[state]
    set_active_paper_strategy(settings, owner)
    return expected_code


def _job_count(job_type: str | None = None) -> int:
    with session_scope(load_settings()) as session:
        statement = select(func.count()).select_from(Job)
        if job_type is not None:
            statement = statement.where(Job.job_type == job_type)
        return session.scalar(statement) or 0


# --- gate matrix: job type x {owner, non-owner, no owner} x {spec, HTTP} ---------


@pytest.mark.parametrize("state", list(_STATES))
@pytest.mark.parametrize("job_type", list(_SPEC_BY_JOB_TYPE))
def test_gate_matrix_at_spec_level(paper_jobs_env: BrokerFakes, job_type: str, state: str) -> None:  # noqa: F811
    spec_cls, _conflict, payload = _SPEC_BY_JOB_TYPE[job_type]
    expected_code = _arrange_owner(state)
    spec = spec_cls(load_settings())

    if expected_code is None:
        assert spec.validate_payload(payload)["strategy_id"] == STRATEGY_ID
        return
    with pytest.raises(JobSubmissionConflictError) as exc_info:
        spec.validate_payload(payload)
    assert exc_info.value.job_type == job_type
    assert exc_info.value.code == expected_code
    assert dict(exc_info.value.detail) == {"strategy_id": STRATEGY_ID}


@pytest.mark.parametrize("state", list(_STATES))
@pytest.mark.parametrize("job_type", list(_SPEC_BY_JOB_TYPE))
def test_gate_matrix_over_http(paper_jobs_env: BrokerFakes, job_type: str, state: str) -> None:  # noqa: F811
    _spec_cls, _conflict, payload = _SPEC_BY_JOB_TYPE[job_type]
    expected_code = _arrange_owner(state)
    before = _job_count()

    with TestClient(create_app()) as client:
        response = client.post(
            "/api/v1/jobs",
            headers={"Idempotency-Key": f"matrix-{job_type}-{state}"},
            json={"job_type": job_type, "payload": payload},
        )

    if expected_code is None:
        assert response.status_code == 202, response.text
        assert _job_count() == before + 1
        return
    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": expected_code,
        "job_type": job_type,
        "strategy_id": STRATEGY_ID,
    }
    assert _job_count() == before  # a refusal creates nothing


@pytest.mark.parametrize("state", ["non_owner", "no_owner"])
def test_non_gated_job_types_are_accepted_for_a_non_owner(
    paper_jobs_env: BrokerFakes,  # noqa: F811
    state: str,
) -> None:
    _arrange_owner(state)
    submissions = {
        "broker-order-sync": {"strategy_id": STRATEGY_ID, "as_of_session": SESSION.isoformat()},
        "risk-evaluation": {"strategy_id": STRATEGY_ID, "as_of_session": SESSION.isoformat()},
        "backtest": {
            "strategy_id": STRATEGY_ID,
            "from_date": "2024-01-02",
            "to_date": "2024-01-05",
        },
    }
    with TestClient(create_app()) as client:
        for job_type, payload in submissions.items():
            response = client.post(
                "/api/v1/jobs",
                headers={"Idempotency-Key": f"nongated-{job_type}-{state}"},
                json={"job_type": job_type, "payload": payload},
            )
            assert response.status_code == 202, (job_type, response.text)


def test_only_paper_session_and_reconciliation_modules_check_ownership() -> None:
    handlers = _ROOT / "src/trading_platform/jobs/handlers"
    offenders = sorted(
        path.name
        for path in handlers.glob("*.py")
        if "require_active_paper_strategy" in path.read_text() or "ownership_block_for" in path.read_text()
    )
    assert offenders == ["paper_session_submission.py", "payload_fields.py", "reconciliation_submission.py"]


def test_retry_of_a_failed_paper_session_job_after_ownership_moved_returns_the_same_409(
    paper_jobs_env: BrokerFakes,  # noqa: F811
) -> None:
    _arrange_owner("owner")
    with session_scope(load_settings()) as session:
        failed = Job(
            job_type="paper-session",
            payload=dict(PAYLOAD),
            status=JobStatus.FAILED,
            failure_reason=JobFailureReason.HANDLER_ERROR,
            failure_message="arranged failure",
            outcome_uncertain=False,
            started_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
        )
        session.add(failed)
        session.flush()
        failed_id = str(failed.id)

    with TestClient(create_app()) as client:
        for index, (state, code) in enumerate(
            (("non_owner", "strategy_not_active_paper_strategy"), ("no_owner", "no_active_paper_strategy"))
        ):
            _arrange_owner(state)
            response = client.post(
                f"/api/v1/jobs/{failed_id}/retry", headers={"Idempotency-Key": f"retry-{index}"}
            )
            assert response.status_code == 409
            assert response.json()["detail"] == {
                "code": code,
                "job_type": "paper-session",
                "strategy_id": STRATEGY_ID,
            }
            assert client.get(f"/api/v1/jobs/{failed_id}").json()["retried_as_job_id"] is None

        _arrange_owner("owner")
        accepted = client.post(f"/api/v1/jobs/{failed_id}/retry", headers={"Idempotency-Key": "retry-ok"})
        assert accepted.status_code == 202, accepted.text


# --- SER: admission serialized with ownership changes -----------------------------


def _orchestration() -> JobOrchestrationService:
    settings = load_settings()
    return JobOrchestrationService(settings, build_default_registry(settings))


def _run_in_thread(fn) -> tuple[threading.Thread, dict[str, Any]]:
    outcome: dict[str, Any] = {}

    def target() -> None:
        try:
            outcome["result"] = fn()
        except BaseException as exc:  # noqa: BLE001 - surfaced to the test
            outcome["error"] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread, outcome


def _lock_singleton_for_update(holder) -> None:
    holder.execute(select(ActivePaperStrategy.id).where(ActivePaperStrategy.id == 1).with_for_update())


def test_admission_blocks_behind_a_handover_then_rechecks_and_refuses(
    paper_jobs_env: BrokerFakes,  # noqa: F811
) -> None:
    """Order 1 of the race: the handover (FOR UPDATE on the singleton) is in
    flight when a paper-session submit arrives. The submit waits for the lock,
    then its in-transaction ownership re-check sees the new owner and refuses;
    no Job is inserted."""
    settings = load_settings()
    _arrange_owner("owner")
    service = _orchestration()
    holder = get_session_factory(settings)()
    try:
        _lock_singleton_for_update(holder)
        thread, outcome = _run_in_thread(
            lambda: service.submit(job_type="paper-session", payload=dict(PAYLOAD), idempotency_key="race-1")
        )
        thread.join(timeout=1.5)
        assert thread.is_alive(), "admission must wait for the handover's row lock"
        assert _job_count() == 0

        new_owner = holder.execute(select(Strategy.id).where(Strategy.strategy_id == OTHER)).scalar_one()
        holder.execute(update(ActivePaperStrategy).where(ActivePaperStrategy.id == 1).values(strategy_id=new_owner))
        holder.commit()
    finally:
        holder.close()
    thread.join(timeout=15)

    error = outcome.get("error")
    assert isinstance(error, JobSubmissionConflictError), outcome
    assert error.code == "strategy_not_active_paper_strategy"
    assert _job_count() == 0


def test_handover_sees_an_admitted_job_when_the_submit_commits_first(
    paper_jobs_env: BrokerFakes,  # noqa: F811
) -> None:
    """Order 2: the submit commits first. A handover that then takes the row
    FOR UPDATE and evaluates A1 sees the queued Job and must refuse."""
    settings = load_settings()
    _arrange_owner("owner")
    result = _orchestration().submit(
        job_type="paper-session", payload=dict(PAYLOAD), idempotency_key="race-2"
    )
    assert result.created is True

    holder = get_session_factory(settings)()
    try:
        _lock_singleton_for_update(holder)
        open_broker_jobs = holder.execute(
            select(func.count())
            .select_from(Job)
            .where(Job.job_type == "paper-session", Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]))
        ).scalar_one()
        assert open_broker_jobs == 1  # A1 would fail: check_failed:A1
        holder.rollback()
    finally:
        holder.close()
    with session_scope(settings) as session:
        owner_row = session.execute(select(ActivePaperStrategy.strategy_id)).scalar_one()
    assert owner_row is not None  # ownership unchanged


def test_broker_touching_non_gated_job_admission_also_serializes_on_the_singleton(
    paper_jobs_env: BrokerFakes,  # noqa: F811
) -> None:
    settings = load_settings()
    _arrange_owner("non_owner")  # broker-order-sync is not gated by ownership
    service = _orchestration()
    payload = {"strategy_id": STRATEGY_ID, "as_of_session": SESSION.isoformat()}
    holder = get_session_factory(settings)()
    try:
        _lock_singleton_for_update(holder)
        thread, outcome = _run_in_thread(
            lambda: service.submit(job_type="broker-order-sync", payload=payload, idempotency_key="race-3")
        )
        thread.join(timeout=1.5)
        assert thread.is_alive()
        holder.commit()
    finally:
        holder.close()
    thread.join(timeout=15)

    assert "error" not in outcome, outcome.get("error")
    assert outcome["result"].created is True
    assert _job_count("broker-order-sync") == 1


def test_retry_takes_the_singleton_before_locking_the_original_job_row(
    paper_jobs_env: BrokerFakes,  # noqa: F811
) -> None:
    """SER lock order is singleton first, then other rows. A retry that is
    waiting on the singleton (held FOR UPDATE by a handover) must NOT already
    hold the original Job's row lock."""
    from sqlalchemy import text

    settings = load_settings()
    _arrange_owner("owner")
    with session_scope(settings) as session:
        failed = Job(
            job_type="paper-session",
            payload=dict(PAYLOAD),
            status=JobStatus.FAILED,
            failure_reason=JobFailureReason.HANDLER_ERROR,
            failure_message="arranged failure",
            outcome_uncertain=False,
            started_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
        )
        session.add(failed)
        session.flush()
        failed_id = failed.id

    service = _orchestration()
    holder = get_session_factory(settings)()
    probe = get_session_factory(settings)()
    try:
        _lock_singleton_for_update(holder)
        thread, outcome = _run_in_thread(
            lambda: service.retry(job_id=failed_id, idempotency_key="retry-lock-order")
        )
        thread.join(timeout=1.5)
        assert thread.is_alive(), "retry must wait on the singleton"
        # The waiting retry holds no lock on the original Job row.
        probe.execute(text("SELECT id FROM jobs WHERE id = :id FOR UPDATE NOWAIT"), {"id": failed_id})
        probe.rollback()
        holder.commit()
    finally:
        probe.close()
        holder.close()
    thread.join(timeout=15)

    assert "error" not in outcome, outcome.get("error")
    assert outcome["result"].created is True


def test_admission_fails_closed_when_the_singleton_row_is_missing(
    paper_jobs_env: BrokerFakes,  # noqa: F811
) -> None:
    from sqlalchemy import text

    _arrange_owner("owner")
    with session_scope(load_settings()) as session:
        session.execute(text("DELETE FROM active_paper_strategy"))

    with pytest.raises(ActivePaperStrategyUnavailableError):
        _orchestration().submit(
            job_type="paper-session", payload=dict(PAYLOAD), idempotency_key="race-missing"
        )
    assert _job_count() == 0


def test_admission_recheck_uses_the_same_ownership_callable(
    paper_jobs_env: BrokerFakes,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Identity: validate_payload and the in-transaction admission re-check
    both call services.active_paper_strategy.ownership_block_for."""
    from trading_platform.services import active_paper_strategy as domain

    assert payload_fields_module.ownership_block_for is domain.ownership_block_for
    _arrange_owner("owner")
    calls: list[bool] = []
    real = domain.ownership_block_for

    def spy(strategy_id, *, settings=None, session=None):
        calls.append(session is not None)
        return real(strategy_id, settings=settings, session=session)

    monkeypatch.setattr(payload_fields_module, "ownership_block_for", spy)
    settings = load_settings()
    spec = PaperSessionSubmissionSpec(settings)

    spec.validate_payload(dict(PAYLOAD))
    with session_scope(settings) as session:
        spec.check_admission(dict(PAYLOAD), session=session)

    assert calls == [False, True]  # submit-time check, then the in-transaction re-check


# --- run time: handover between submit and claim -----------------------------------


class RecordingBrokerClient:
    """Records EVERY call; the zero-call assertions fail on any broker read."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def close(self) -> None:
        self.calls.append("close")

    def list_orders(self) -> list[Any]:
        self.calls.append("list_orders")
        return []

    def list_fills(self) -> list[Any]:
        self.calls.append("list_fills")
        return []

    def list_positions(self) -> list[Any]:
        self.calls.append("list_positions")
        return []

    def get_account(self) -> BrokerAccountSnapshot:
        self.calls.append("get_account")
        raise AssertionError("broker account must not be read when ownership is lost")


class RecordingExecutionService(FakeExecutionService):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    def submit_order(self, intent: OrderIntent) -> OrderSubmissionResult:
        self.calls.append("submit_order")
        return super().submit_order(intent)


def _blocked_execution_runs(settings) -> list[StrategyRun]:
    with session_scope(settings) as session:
        runs = (
            session.execute(
                select(StrategyRun).where(StrategyRun.run_type == StrategyRunType.PAPER_EXECUTION)
            )
            .scalars()
            .all()
        )
        session.expunge_all()
    return list(runs)


@pytest.mark.parametrize(
    ("handover", "expected_block"),
    [
        pytest.param(OTHER, OwnershipBlock.STRATEGY_NOT_ACTIVE_PAPER_STRATEGY, id="to_another_strategy"),
        pytest.param(None, OwnershipBlock.NO_ACTIVE_PAPER_STRATEGY, id="to_no_owner"),
    ],
)
def test_handover_between_submit_and_claim_blocks_with_zero_broker_calls(
    migrated_paper_db: str,  # noqa: F811
    handover: str | None,
    expected_block: OwnershipBlock,
) -> None:
    _seed_approved_risk_batch()  # enabled owner = trend_following_daily
    settings = load_settings()
    seed_registered_strategy(settings, OTHER)
    spec = PaperSessionSubmissionSpec(settings)
    spec.validate_payload(
        {"strategy_id": STRATEGY_ID, "as_of_session": SESSION.isoformat(), "risk_run_id": None}
    )  # submit-time: accepted while the strategy is the owner

    set_active_paper_strategy(settings, handover)  # the handover fixture, a direct write

    broker = RecordingBrokerClient()
    execution = RecordingExecutionService()
    report = run_paper_session(
        STRATEGY_ID,
        as_of_session=SESSION,
        settings=settings,
        execution_service=execution,
        broker_client=broker,
        trigger_source="pytest",
    )

    assert report.action == "blocked_not_active_paper_strategy"
    assert report.result_summary["blocked_reason"] == BLOCKED_REASON_NOT_ACTIVE_PAPER_STRATEGY
    assert report.result_summary["ownership_block"] == expected_block.value
    assert report.reconciliation_run_id is None
    assert broker.calls == []
    assert execution.calls == [] and execution.submitted_intents == []

    runs = _blocked_execution_runs(settings)
    assert len(runs) == 1  # the blocked paper_execution run exists
    assert runs[0].status == StrategyRunStatus.FAILED
    assert runs[0].result_summary["action"] == "blocked_not_active_paper_strategy"
    with session_scope(settings) as session:
        # Blocked before intent registration: nothing in flight or ambiguous.
        assert session.scalar(select(func.count()).select_from(PaperOrder)) == 0


def test_handover_between_job_submit_and_worker_claim_blocks_the_job_run(
    paper_jobs_env: BrokerFakes,  # noqa: F811
) -> None:
    settings = load_settings()
    seed_registered_strategy(settings, OTHER)
    with TestClient(create_app()) as client:
        submitted = client.post(
            "/api/v1/jobs",
            headers={"Idempotency-Key": "handover-job"},
            json={"job_type": "paper-session", "payload": dict(PAYLOAD)},
        )
        assert submitted.status_code == 202, submitted.text
        job_id = submitted.json()["job_id"]

        set_active_paper_strategy(settings, OTHER)  # handover fixture between submit and claim
        _run_worker_once()
        detail = client.get(f"/api/v1/jobs/{job_id}").json()

    assert detail["status"] == "succeeded", detail["failure_message"]
    assert detail["result_summary"]["action"] == "blocked_not_active_paper_strategy"
    assert paper_jobs_env.execution.submitted_intents == []
    assert paper_jobs_env.state_clients_built == 0  # no broker state client was even built


# --- run time: ownership lost mid-run ------------------------------------------------


class OwnershipLosingExecutionService(FakeExecutionService):
    """Submits the first candidate, then hands the account to another strategy."""

    def __init__(self, *, settings) -> None:
        super().__init__()
        self._settings = settings

    def submit_order(self, intent: OrderIntent) -> OrderSubmissionResult:
        if self.submitted_intents:
            raise AssertionError("no further order may be submitted after ownership is lost")
        result = super().submit_order(intent)
        set_active_paper_strategy(self._settings, OTHER, reason="mid-run handover fixture")
        return result


def test_ownership_lost_mid_run_halts_remaining_candidates(migrated_paper_db: str) -> None:  # noqa: F811
    _seed_approved_risk_batch()
    settings = load_settings()
    seed_registered_strategy(settings, OTHER)
    execution = OwnershipLosingExecutionService(settings=settings)

    report = run_paper_order_submission(
        STRATEGY_ID,
        as_of_session=SESSION,
        settings=settings,
        execution_service=execution,
        trigger_source="pytest",
    )

    summary = report.result_summary
    assert report.status == StrategyRunStatus.FAILED.value
    assert summary["stage"] == "blocked_mid_run"
    assert summary["action"] == "blocked_mid_run_not_active_paper_strategy"
    assert summary["blocked_reason"] == BLOCKED_REASON_NOT_ACTIVE_PAPER_STRATEGY
    assert summary["ownership_block"] == OwnershipBlock.STRATEGY_NOT_ACTIVE_PAPER_STRATEGY.value
    assert summary["submitted_count"] == 1
    assert summary["skipped_by_ownership_count"] == 1
    assert summary["skipped_by_kill_switch_count"] == 0
    assert len(execution.submitted_intents) == 1

    submitted_symbol = execution.submitted_intents[0].symbol
    assert {entry["symbol"] for entry in summary["skipped_by_ownership"]} == (
        {"AAPL", "MSFT"} - {submitted_symbol}
    )
    with session_scope(settings) as session:
        orders = session.execute(select(PaperOrder)).scalars().all()
        # The already-submitted order is untouched and nothing further was registered.
        assert [order.symbol_ref.ticker for order in orders] == [submitted_symbol]
        assert orders[0].status == OrderLifecycleState.SUBMITTED
        blocked_events = (
            session.execute(
                select(ExecutionEvent).where(
                    ExecutionEvent.strategy_run_id == uuid.UUID(report.run_id),
                    ExecutionEvent.event_type == "paper_execution_blocked",
                )
            )
            .scalars()
            .all()
        )
        assert len(blocked_events) == 1
        assert blocked_events[0].details["blocked_reason"] == BLOCKED_REASON_NOT_ACTIVE_PAPER_STRATEGY


def test_kill_switch_takes_precedence_when_both_trip_mid_run(migrated_paper_db: str) -> None:  # noqa: F811
    _seed_approved_risk_batch()
    settings = load_settings()
    seed_registered_strategy(settings, OTHER)

    class _BothTrip(OwnershipLosingExecutionService):
        def submit_order(self, intent: OrderIntent) -> OrderSubmissionResult:
            result = super().submit_order(intent)
            OperatorControlService(settings=self._settings).trip_kill_switch(
                reason="both trip", actor="pytest", trigger_source="pytest"
            )
            return result

    execution = _BothTrip(settings=settings)
    report = run_paper_order_submission(
        STRATEGY_ID,
        as_of_session=SESSION,
        settings=settings,
        execution_service=execution,
        trigger_source="pytest",
    )

    assert report.result_summary["action"] == "blocked_mid_run_global_kill_switch"
    assert report.result_summary["skipped_by_kill_switch_count"] == 1
    assert report.result_summary["skipped_by_ownership_count"] == 0
    assert len(execution.submitted_intents) == 1


def test_non_owner_is_blocked_before_the_disabled_check_and_keeps_disabled_meaning_for_the_owner(
    migrated_paper_db: str,  # noqa: F811
) -> None:
    _seed_approved_risk_batch()
    settings = load_settings()
    control = OperatorControlService(settings=settings)
    control.disable_strategy(STRATEGY_ID, reason="maintenance", actor="pytest", trigger_source="pytest")

    # owner + disabled -> the disabled block keeps its meaning
    owner_report = run_paper_order_submission(
        STRATEGY_ID, as_of_session=SESSION, settings=settings, trigger_source="pytest"
    )
    assert owner_report.result_summary["action"] == "blocked_strategy_disabled"

    # non-owner + disabled -> ownership is reported first
    clear_active_paper_strategy(settings)
    non_owner_report = run_paper_order_submission(
        STRATEGY_ID, as_of_session=SESSION, settings=settings, trigger_source="pytest"
    )
    assert non_owner_report.result_summary["action"] == "blocked_not_active_paper_strategy"
    assert non_owner_report.result_summary["ownership_block"] == "no_active_paper_strategy"


# --- research is unaffected ---------------------------------------------------------


def test_research_job_types_unaffected_without_owner(
    migrated_risk_db: str,  # noqa: F811
    risk_strategy_config_override: None,  # noqa: F811
) -> None:
    settings = load_settings()
    # No singleton owner at all (seeded state): research specs accept a non-owner.
    backtest = BacktestSubmissionSpec(settings).validate_payload(
        {"strategy_id": STRATEGY_ID, "from_date": "2024-01-02", "to_date": "2024-01-05"}
    )
    risk = RiskEvaluationSubmissionSpec(settings).validate_payload(
        {"strategy_id": STRATEGY_ID, "as_of_session": SESSION.isoformat()}
    )
    assert backtest["strategy_id"] == risk["strategy_id"] == STRATEGY_ID

    from trading_platform.services.calendar import upsert_market_sessions

    with session_scope(settings) as session:
        upsert_market_sessions(session, date(2024, 1, 3), date(2024, 1, 5))
        for ticker, closes in (("AAPL", ("100", "110", "120")), ("MSFT", ("100", "100", "100"))):
            for day, close in zip((3, 4, 5), closes, strict=True):
                _seed_symbol_and_bar(session, ticker=ticker, session_date=date(2024, 1, day), close=close)

    for owner in (None, OTHER):
        if owner is not None:
            seed_registered_strategy(settings, OTHER)
        set_active_paper_strategy(settings, owner)
        report = run_risk_evaluation(
            STRATEGY_ID, as_of_session=SESSION, trigger_source="test_suite", settings=settings
        )
        assert report.status == StrategyRunStatus.SUCCEEDED.value


# --- query budgets and structure ------------------------------------------------------


def _ownership_statements(counter) -> list[str]:
    return [s for s in counter.statements if "active_paper_strategy" in s]


def test_each_ownership_gate_issues_exactly_one_fresh_statement(
    migrated_paper_db: str,  # noqa: F811
) -> None:
    _seed_approved_risk_batch()
    settings = load_settings()
    engine = get_engine(settings)

    # Happy path with two candidates: run_paper_session pre-check (1) + guarded
    # body (1) + one fresh re-read per candidate (2).
    with count_queries(engine) as counter:
        report = run_paper_session(
            STRATEGY_ID,
            as_of_session=SESSION,
            settings=settings,
            execution_service=FakeExecutionService(),
            trigger_source="pytest",
        )
    assert report.action != "blocked_not_active_paper_strategy"
    assert len(_ownership_statements(counter)) == 1 + 1 + 2

    # Blocked path: pre-check (1) + the guarded body's own re-read (1).
    clear_active_paper_strategy(settings)
    with count_queries(engine) as counter:
        blocked = run_paper_session(
            STRATEGY_ID,
            as_of_session=SESSION,
            settings=settings,
            execution_service=FakeExecutionService(),
            trigger_source="pytest",
        )
    assert blocked.action == "blocked_not_active_paper_strategy"
    assert len(_ownership_statements(counter)) == 2


def test_engine_reads_go_through_the_one_gate_loader(
    migrated_paper_db: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Identity (R-Q1): run_paper_session / the guarded body / the candidate loop
    use operator_controls.load_trading_gate_state, the same function the reads use."""
    _seed_approved_risk_batch()
    settings = load_settings()
    calls: list[int] = []
    real = operator_controls.load_trading_gate_state

    def spy(session, **kwargs):
        calls.append(1)
        return real(session, **kwargs)

    monkeypatch.setattr(operator_controls, "load_trading_gate_state", spy)

    run_paper_session(
        STRATEGY_ID,
        as_of_session=SESSION,
        settings=settings,
        execution_service=FakeExecutionService(),
        trigger_source="pytest",
    )
    assert len(calls) == 1 + 1 + 2

    source = (_ROOT / "src/trading_platform/services/execution/submit_orders.py").read_text()
    assert "load_kill_switch_state" not in source
    assert source.count("read_trading_gate_state(") >= 3
    assert source.count("ownership_block_for(") >= 3


_PAPER_PATH_MODULES = (
    "src/trading_platform/services/execution/submit_orders.py",
    "src/trading_platform/jobs/handlers/paper_session_submission.py",
    "src/trading_platform/jobs/handlers/paper_session.py",
    "src/trading_platform/jobs/handlers/reconciliation_submission.py",
    "src/trading_platform/jobs/handlers/reconciliation.py",
    "src/trading_platform/jobs/handlers/broker_order_sync_submission.py",
    "src/trading_platform/jobs/handlers/broker_order_sync.py",
)


@pytest.mark.parametrize("relative", _PAPER_PATH_MODULES)
def test_no_paper_path_module_reads_default_strategy_id(relative: str) -> None:
    assert "default_strategy_id" not in (_ROOT / relative).read_text()


def test_run_paper_session_requires_an_explicit_strategy_id() -> None:
    with pytest.raises(TypeError):
        run_paper_session(as_of_session=SESSION)  # type: ignore[call-arg]


def test_run_paper_session_signature_has_no_default_strategy() -> None:
    import inspect

    parameter = inspect.signature(run_paper_session).parameters["strategy_id"]
    assert parameter.default is inspect.Parameter.empty


def test_no_other_module_calls_ownership_decision_inline() -> None:
    """The submit-time predicate has one definition (services/active_paper_strategy.py)."""
    definitions = []
    for path in (_ROOT / "src").rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "ownership_block_for":
                definitions.append(path.relative_to(_ROOT).as_posix())
    assert sorted(definitions) == [
        "src/trading_platform/services/active_paper_strategy.py",
        "src/trading_platform/services/operator_controls.py",  # the pure TradingGateState method
    ]


def test_closed_conflict_enums_equal_the_ownership_block_members() -> None:
    expected = {member.value for member in OwnershipBlock}
    # paper-session adds the four execution-eligibility refusals (COR-04, D-23)
    assert {m.value for m in PaperSessionSubmitConflict} == expected | {
        "historical_execution_rejected",
        "outside_execution_window",
        "evaluation_data_not_ready",
        "calendar_data_unavailable",
    }
    assert {m.value for m in ReconciliationSubmitConflict} == expected
