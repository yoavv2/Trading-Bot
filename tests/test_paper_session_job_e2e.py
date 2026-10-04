"""Phase 20 (Plan 20) production-path E2E for the ``paper-session`` Job type --
the only broker-submission path after Phase 20 (D-28).

Every test drives the whole vertical slice through the **production** Job
registry: ``create_app()`` (lifespan builds ``build_default_registry``) ->
``POST /api/v1/jobs`` -> the real ``run-jobs --once`` CLI command -> the
real ``PaperSessionJobHandler`` -> the real ``run_paper_session`` service ->
observable ``GET /api/v1/jobs/{id}``.

The paper-session type is PAPER-mode, so the per-dispatch preflight (D-22)
passes only with Alpaca paper credentials configured. The environment sets
non-empty *fake* credential strings and replaces the two broker symbols the
service constructs when no client is injected -- ``AlpacaClient`` in
``services.reconciliation.report`` (broker state reads) and
``AlpacaExecutionService`` in ``services.execution.submit_orders`` (order
submission) -- with in-memory fakes. Handler code is never touched and no
network call is ever made (T-20-20-03).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from tests.support.paper_eligibility import allow_paper_execution
from tests.support.paper_ownership import seed_strategy, set_active_paper_strategy
from tests.test_job_operations_e2e import (
    _run_worker_once,
    job_operations_env,
    migrated_backtest_db,
    strategy_config_override,
)
from tests.test_paper_execution import FakeBrokerClient, FakeExecutionService

from trading_platform.api.app import create_app
from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.db.models import (
    Job,
    JobStatus,
    RiskEvent,
    StrategyRun,
    StrategyRunStatus,
    StrategyRunType,
)
from trading_platform.db.models.symbol import Symbol
from trading_platform.db.session import session_scope
from trading_platform.jobs.handlers import paper_session as paper_session_handler_module
from trading_platform.services.alpaca import BrokerAccountSnapshot
from trading_platform.services.concurrency_guard import session_run_lock
from trading_platform.services.execution import submit_orders as submit_orders_module
from trading_platform.services.reconciliation import report as reconciliation_report_module
from trading_platform.strategies.registry import build_default_registry

# Fixtures consumed by pytest name (paper_jobs_env -> job_operations_env ->
# migrated_backtest_db/strategy_config_override); re-exported so ruff F401 passes.
__all__ = [
    "job_operations_env",
    "migrated_backtest_db",
    "strategy_config_override",
]

STRATEGY_ID = "trend_following_daily"
SESSION_DATE = date(2024, 1, 5)
AS_OF_SESSION = SESSION_DATE.isoformat()
PAYLOAD: dict[str, Any] = {
    "strategy_id": STRATEGY_ID,
    "as_of_session": AS_OF_SESSION,
    "risk_run_id": None,
}
RECONCILIATION_PAYLOAD: dict[str, str] = {
    "strategy_id": STRATEGY_ID,
    "as_of_session": AS_OF_SESSION,
}

_FAKE_API_KEY = "fake-key-not-a-secret"  # pragma: allowlist secret
_FAKE_API_SECRET = "fake-secret-not-a-secret"  # pragma: allowlist secret


@pytest.fixture(autouse=True)
def _eligible_paper_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    """This module's subject is not eligibility (COR-04): see
    tests/support/paper_eligibility.py. Real eligibility is tested in
    tests/test_paper_session_eligibility.py."""

    allow_paper_execution(monkeypatch)


def _empty_account() -> BrokerAccountSnapshot:
    return BrokerAccountSnapshot(
        cash=Decimal("100000.000000"),
        buying_power=Decimal("100000.000000"),
        equity=Decimal("100000.000000"),
        long_market_value=Decimal("0"),
        short_market_value=Decimal("0"),
        raw_payload={"equity": "100000.000000"},
    )


@dataclass
class BrokerFakes:
    """Mutable seam the patched broker symbols read on every construction.

    ``execution`` is the single shared fake order-submission service, so a
    test can count every order the production path sent to the "broker".
    """

    execution: FakeExecutionService = field(default_factory=FakeExecutionService)
    state_clients_built: int = 0

    def build_state_client(self, _alpaca_settings: Any = None) -> FakeBrokerClient:
        self.state_clients_built += 1
        return FakeBrokerClient(orders=[], fills=[], positions=[], account=_empty_account())

    def build_execution_service(self, _alpaca_settings: Any = None) -> FakeExecutionService:
        return self.execution


def _seed_approved_risk_run(session_date: date = SESSION_DATE) -> None:
    """One SUCCEEDED risk evaluation with two approved events (AAPL, MSFT)
    for ``session_date``. Reuses the AAPL/MSFT symbols already seeded by the
    shared market-data fixture."""

    settings = load_settings()
    strategy = build_default_registry(settings).resolve(STRATEGY_ID)

    with session_scope(settings) as session:
        strategy_record = seed_strategy(session, strategy.metadata, enabled=True)
        set_active_paper_strategy(session, strategy.metadata.strategy_id)  # explicit owner (20.1-01)
        aapl = session.execute(select(Symbol).where(Symbol.ticker == "AAPL")).scalar_one()
        msft = session.execute(select(Symbol).where(Symbol.ticker == "MSFT")).scalar_one()

        risk_run = StrategyRun(
            strategy_id=strategy_record.id,
            run_type=StrategyRunType.RISK_EVALUATION,
            status=StrategyRunStatus.SUCCEEDED,
            trigger_source="test_suite",
            started_at=datetime(2024, 1, 5, 14, 30, tzinfo=UTC),
            parameters_snapshot={"as_of_session": session_date.isoformat()},
            result_summary={"stage": "completed", "as_of_session": session_date.isoformat()},
            completed_at=datetime(2024, 1, 5, 14, 32, tzinfo=UTC),
        )
        session.add(risk_run)
        session.flush()
        for symbol, price, quantity in (
            (aapl, "120.000000", "10.000000"),
            (msft, "300.000000", "5.000000"),
        ):
            session.add(
                RiskEvent(
                    strategy_run_id=risk_run.id,
                    symbol_id=symbol.id,
                    session_date=session_date,
                    signal_direction="long",
                    signal_reason="trend_entry",
                    outcome="approved",
                    decision_code="approved",
                    decision_reason="Approved for paper execution.",
                    reference_price=Decimal(price),
                    proposed_quantity=Decimal(quantity),
                    proposed_notional=Decimal(price) * Decimal(quantity),
                    risk_metadata={"remaining_cash": 90000.0},
                )
            )


@pytest.fixture()
def paper_jobs_env(
    job_operations_env: None, monkeypatch: pytest.MonkeyPatch
) -> Iterator[BrokerFakes]:
    """job_operations_env (mutations enabled, seeded sessions/bars) plus fake
    Alpaca paper creds, the faked broker seam, and an approved risk batch for
    session 2024-01-05."""

    monkeypatch.setenv("TRADING_PLATFORM_BROKER__ALPACA__API_KEY", _FAKE_API_KEY)
    monkeypatch.setenv("TRADING_PLATFORM_BROKER__ALPACA__API_SECRET", _FAKE_API_SECRET)
    clear_settings_cache()

    fakes = BrokerFakes()
    monkeypatch.setattr(reconciliation_report_module, "AlpacaClient", fakes.build_state_client)
    monkeypatch.setattr(
        submit_orders_module, "AlpacaExecutionService", fakes.build_execution_service
    )
    _seed_approved_risk_run()
    try:
        yield fakes
    finally:
        clear_settings_cache()


def _submit(
    client: TestClient,
    key: str,
    *,
    job_type: str = "paper-session",
    payload: dict[str, Any] | None = None,
):
    return client.post(
        "/api/v1/jobs",
        headers={"Idempotency-Key": key},
        json={"job_type": job_type, "payload": payload if payload is not None else PAYLOAD},
    )


def _submit_and_run(
    client: TestClient,
    key: str,
    *,
    job_type: str = "paper-session",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    submitted = _submit(client, key, job_type=job_type, payload=payload)
    assert submitted.status_code == 202, submitted.text
    job_id = submitted.json()["job_id"]
    _run_worker_once()
    detail = client.get(f"/api/v1/jobs/{job_id}")
    assert detail.status_code == 200
    return detail.json()


def _event_types(client: TestClient, job_id: str) -> set[str]:
    events = client.get(f"/api/v1/jobs/{job_id}/events")
    assert events.status_code == 200
    return {item["event_type"] for item in events.json()["items"]}


def _log_codes(client: TestClient, job_id: str) -> set[str]:
    logs = client.get(f"/api/v1/jobs/{job_id}/logs")
    assert logs.status_code == 200
    return {item["event_code"] for item in logs.json()["items"]}


# --- Task 1: linkage, blocked outcome, queued and running cancellation -------


def test_paper_session_job_links_both_runs(paper_jobs_env: BrokerFakes) -> None:
    """SC1/OPS-03 + D-08/D-09: risk_run_id null resolves the latest approved
    batch; the Job links exactly the internal reconciliation run and the
    paper_execution run, and both carry the Job id."""

    with TestClient(create_app()) as client:
        detail = _submit_and_run(client, "e2e-paper-happy")
        assert detail["status"] == "succeeded", detail["failure_message"]
        assert detail["failure_reason"] is None
        assert detail["outcome_uncertain"] is False
        assert detail["result_summary"]["action"] == "submitted_missing_orders"

        resources = detail["resources"]
        assert len(resources) == 2
        assert all(r["kind"] == "strategy_run" for r in resources)
        linked_ids = {r["id"] for r in resources}
        assert linked_ids == set(detail["result_summary"]["produced_run_ids"])
        assert linked_ids == {
            detail["result_summary"]["reconciliation_run_id"],
            detail["result_summary"]["execution_run_id"],
        }

        run_details = {
            run_id: client.get(f"/api/v1/runs/{run_id}").json()["run"] for run_id in linked_ids
        }
        assert "external_broker_session_started" in _log_codes(client, detail["id"])

    assert {run["run_type"] for run in run_details.values()} == {
        "reconciliation",
        "paper_execution",
    }
    assert all(run["job_id"] == detail["id"] for run in run_details.values())
    assert all(run["trigger_source"] != "operator" for run in run_details.values())
    # Both approved candidates went to the (fake) broker exactly once.
    assert sorted(i.symbol for i in paper_jobs_env.execution.submitted_intents) == [
        "AAPL",
        "MSFT",
    ]
    assert paper_jobs_env.state_clients_built == 1


def test_cancel_while_running_is_rejected_and_submission_completes(
    paper_jobs_env: BrokerFakes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SC2/D-03/T-20-20-01: a cancel arriving while the Job is RUNNING is
    rejected 409 job_not_cancellable_running; the Job is never mislabelled
    cancelled after submission began -- it ends SUCCEEDED with no
    cancellation state recorded anywhere."""

    real_run_paper_session = paper_session_handler_module.run_paper_session
    cancel_responses: list[Any] = []
    job_id_holder: dict[str, str] = {}

    with TestClient(create_app()) as client:

        def _cancel_then_run(*args: Any, **kwargs: Any) -> Any:
            assert str(kwargs["job_id"]) == job_id_holder["job_id"]
            cancel_responses.append(
                client.post(
                    f"/api/v1/jobs/{job_id_holder['job_id']}/cancel",
                    headers={"Idempotency-Key": "cancel-paper-running"},
                    json={"reason": "too late"},
                )
            )
            return real_run_paper_session(*args, **kwargs)

        monkeypatch.setattr(paper_session_handler_module, "run_paper_session", _cancel_then_run)

        submitted = _submit(client, "e2e-paper-cancel-running")
        assert submitted.status_code == 202
        job_id_holder["job_id"] = submitted.json()["job_id"]

        _run_worker_once()

        detail = client.get(f"/api/v1/jobs/{job_id_holder['job_id']}").json()
        event_types = _event_types(client, job_id_holder["job_id"])

    assert len(cancel_responses) == 1
    assert cancel_responses[0].status_code == 409
    assert cancel_responses[0].json()["detail"]["code"] == "job_not_cancellable_running"

    assert detail["status"] == "succeeded", detail["failure_message"]
    assert detail["cancellation_requested_at"] is None
    assert detail["cancellation_acknowledged_at"] is None
    assert detail["cancellation_cause"] is None
    assert "cancellation_requested" not in event_types
    assert len(paper_jobs_env.execution.submitted_intents) == 2


def test_cancel_queued_paper_session_never_executes(
    paper_jobs_env: BrokerFakes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-02: a cancel accepted while QUEUED means the service is never
    invoked, no broker client is built and nothing is submitted."""

    def _must_not_run(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("run_paper_session must not run for a cancelled QUEUED Job")

    monkeypatch.setattr(paper_session_handler_module, "run_paper_session", _must_not_run)

    with TestClient(create_app()) as client:
        submitted = _submit(client, "e2e-paper-cancel-queued")
        assert submitted.status_code == 202
        links = submitted.json()["links"]

        cancel = client.post(
            links["self"] + "/cancel",
            headers={"Idempotency-Key": "cancel-paper-queued"},
            json={"reason": "operator stop"},
        )
        # The shipped cancel route answers 200 for every accepted cancel
        # (only submit/retry answer 202).
        assert cancel.status_code == 200
        assert cancel.json()["status"] == "cancelled"

        _run_worker_once()
        detail = client.get(links["self"]).json()

    assert detail["status"] == "cancelled"
    assert detail["cancellation_cause"] == "operator_request"
    assert detail["resources"] == []
    assert detail["result_summary"] == {}
    assert paper_jobs_env.state_clients_built == 0
    assert paper_jobs_env.execution.submitted_intents == []


def test_blocked_session_is_succeeded_with_action(paper_jobs_env: BrokerFakes) -> None:
    """D-05: a domain block is an ordinary SUCCEEDED outcome carrying the
    domain action -- the handler never reinterprets it as a failure."""

    with TestClient(create_app()) as client:
        disabled = client.put(
            f"/api/v1/controls/strategies/{STRATEGY_ID}",
            json={"status": "disabled", "reason": "e2e"},
        )
        assert disabled.status_code == 200, disabled.text

        detail = _submit_and_run(client, "e2e-paper-blocked")

    assert detail["status"] == "succeeded", detail["failure_message"]
    assert detail["failure_reason"] is None
    assert detail["outcome_uncertain"] is False
    assert detail["result_summary"]["action"] == "blocked_strategy_disabled"
    # A blocked session never reaches the broker.
    assert paper_jobs_env.execution.submitted_intents == []
    assert paper_jobs_env.state_clients_built == 0
    # The blocked execution run is still linked (D-08); no reconciliation ran.
    assert len(detail["resources"]) == 1
    assert detail["resources"][0]["id"] == detail["result_summary"]["execution_run_id"]
    assert detail["result_summary"]["reconciliation_run_id"] is None


# --- Task 2: domain conflict (SC3) and the D-19 reconcile-first cycle --------


def test_lock_conflict_lands_as_domain_conflict(paper_jobs_env: BrokerFakes) -> None:
    """SC3/OPS-08/D-04: while another holder owns the (strategy, session)
    submission lock the Job fails with the dedicated domain_conflict reason
    -- not handler_error -- with no broker submission, and the failure is
    not outcome-uncertain, so retry is not reconcile-gated."""

    settings = load_settings()

    with TestClient(create_app()) as client:
        submitted = _submit(client, "e2e-paper-lock-conflict")
        assert submitted.status_code == 202
        job_id = submitted.json()["job_id"]

        with session_run_lock(
            strategy_id=STRATEGY_ID, session_date=SESSION_DATE, settings=settings
        ):
            _run_worker_once()
            detail = client.get(f"/api/v1/jobs/{job_id}").json()

        assert detail["status"] == "failed"
        assert detail["failure_reason"] == "domain_conflict"
        assert detail["outcome_uncertain"] is False
        assert STRATEGY_ID in detail["failure_message"]
        assert AS_OF_SESSION in detail["failure_message"]
        assert detail["retry_blocked"] is None

        # The internal reconciliation run was created before the submission
        # lock was denied; the paper_execution run never was.
        assert len(detail["resources"]) == 1
        resource = detail["resources"][0]
        assert resource["kind"] == "strategy_run"
        run = client.get(f"/api/v1/runs/{resource['id']}").json()["run"]
        assert run["run_type"] == "reconciliation"
        assert run["job_id"] == job_id

        # No reconcile-first block: the lock is released, retry is accepted.
        retry = client.post(
            f"/api/v1/jobs/{job_id}/retry", headers={"Idempotency-Key": "retry-after-lock"}
        )
        assert retry.status_code == 202, retry.text

    assert paper_jobs_env.execution.submitted_intents == []


def _job_count() -> int:
    with session_scope(load_settings()) as session:
        return len(session.execute(select(Job.id)).scalars().all())


def _completed_at(job_id: str) -> datetime:
    with session_scope(load_settings()) as session:
        job = session.execute(select(Job).where(Job.id == job_id)).scalar_one()
        assert job.completed_at is not None
        return job.completed_at


def _insert_succeeded_reconciliation_row(*, strategy_id: str, completed_at: datetime) -> None:
    """Direct Job row: the API cannot produce a reconciliation Job for a
    strategy the registry does not know, so the different-strategy decoy is
    inserted straight into ``jobs`` (the D-19 predicate reads only that table)."""

    with session_scope(load_settings()) as session:
        session.add(
            Job(
                job_type="reconciliation",
                payload={"strategy_id": strategy_id, "as_of_session": AS_OF_SESSION},
                status=JobStatus.SUCCEEDED,
                started_at=completed_at - timedelta(seconds=1),
                completed_at=completed_at,
            )
        )


def test_uncertain_failure_requires_later_reconciliation_before_retry(
    paper_jobs_env: BrokerFakes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-15 / 20.1-10 (supersedes the Phase 20 D-19 reconcile-first rule): a paper-session that
    fails after its external marker is outcome-uncertain. EVERY paper-session submission for the
    strategy, fresh or retry, is refused (typed 409 with ``required_job_type: reconciliation``)
    until the predicate resolves: the Job has no linked paper_execution run (``nothing_submitted``)
    so a fresh clean STANDALONE reconciliation completed AFTER the failure resolves it. An earlier
    reconciliation, a reconciliation that never ran, or one for another strategy does not."""

    expected_block = {
        "code": "reconciliation_required",
        "required_job_type": "reconciliation",
        "strategy_id": STRATEGY_ID,
    }
    expected_conflict = {**expected_block, "job_type": "paper-session"}

    def _submission_explodes(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("simulated failure after the broker session started")

    monkeypatch.setattr(submit_orders_module, "run_paper_order_submission", _submission_explodes)

    with TestClient(create_app()) as client:
        # A reconciliation that succeeded BEFORE the failure must not count.
        early = _submit_and_run(
            client, "e2e-recon-early", job_type="reconciliation", payload=RECONCILIATION_PAYLOAD
        )
        assert early["status"] == "succeeded", early["failure_message"]

        failed = _submit_and_run(client, "e2e-paper-uncertain")
        failed_id = failed["id"]
        assert failed["status"] == "failed"
        assert failed["failure_reason"] == "handler_error"
        assert failed["outcome_uncertain"] is True
        assert "external_broker_session_started" in _log_codes(client, failed_id)
        assert failed["retry_blocked"] == expected_block
        assert paper_jobs_env.execution.submitted_intents == []

        def _assert_still_blocked(key: str) -> None:
            assert client.get(f"/api/v1/jobs/{failed_id}").json()["retry_blocked"] == expected_block
            blocked = client.post(
                f"/api/v1/jobs/{failed_id}/retry", headers={"Idempotency-Key": key}
            )
            assert blocked.status_code == 409
            assert blocked.json()["detail"] == expected_conflict
            # D-15: a FRESH submission is refused the same way, writing nothing.
            fresh = _submit(client, f"{key}-fresh")
            assert fresh.status_code == 409
            assert fresh.json()["detail"]["code"] == "reconciliation_required"
            assert fresh.json()["detail"]["required_job_type"] == "reconciliation"
            # The rejected retry created nothing.
            assert client.get(f"/api/v1/jobs/{failed_id}").json()["retried_as_job_id"] is None

        _assert_still_blocked("retry-blocked-early-recon")

        # A SUCCEEDED reconciliation Job row for a DIFFERENT strategy, completed
        # after the failure, does not lift the block.
        _insert_succeeded_reconciliation_row(
            strategy_id="some_other_strategy",
            completed_at=_completed_at(failed_id) + timedelta(minutes=1),
        )
        _assert_still_blocked("retry-blocked-other-strategy")

        # A reconciliation for the same strategy that never ran (cancelled
        # while queued) does not lift the block either.
        queued = _submit(
            client, "e2e-recon-cancelled", job_type="reconciliation", payload=RECONCILIATION_PAYLOAD
        )
        assert queued.status_code == 202
        cancelled = client.post(
            queued.json()["links"]["self"] + "/cancel",
            headers={"Idempotency-Key": "cancel-recon-lift-attempt"},
            json={"reason": "never ran"},
        )
        assert cancelled.status_code == 200
        _run_worker_once()
        _assert_still_blocked("retry-blocked-cancelled-recon")

        # Reconcile first: a later clean STANDALONE reconciliation of the same
        # strategy (completed after the failure) lifts the block.
        later = _submit_and_run(
            client, "e2e-recon-lift", job_type="reconciliation", payload=RECONCILIATION_PAYLOAD
        )
        assert later["status"] == "succeeded", later["failure_message"]

        lifted = client.get(f"/api/v1/jobs/{failed_id}").json()
        assert lifted["retry_blocked"] is None

        retry = client.post(
            f"/api/v1/jobs/{failed_id}/retry", headers={"Idempotency-Key": "retry-after-recon"}
        )
        assert retry.status_code == 202, retry.text
        retry_detail = client.get(f"/api/v1/jobs/{retry.json()['job_id']}").json()

    assert retry_detail["retry_of_job_id"] == failed_id
    assert retry_detail["job_type"] == "paper-session"
    assert retry_detail["payload"] == failed["payload"]
    assert retry_detail["status"] == "queued"


def test_ambiguous_submission_lands_job_failed_outcome_uncertain_and_never_resends(
    paper_jobs_env: BrokerFakes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """COR-06/D-12 end to end: a read timeout after the POST was sent goes
    through the REAL AlpacaExecutionService (httpx.MockTransport). Exactly one
    POST, the intent is parked UNKNOWN, one attempt row is `ambiguous`, the Job
    lands failed with outcome_uncertain=true, and a second paper-session submission
    for the same strategy is refused (D-15 `outcome_unresolved`, superseding the
    earlier "second run sends zero POSTs" shape) so zero further POSTs happen."""

    from trading_platform.db.models import OrderLifecycleState, OrderSubmissionAttempt, PaperOrder
    from trading_platform.services.alpaca import AlpacaClient, AlpacaExecutionService

    posts: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            posts.append(request)
            raise httpx.ReadTimeout("timed out after send", request=request)
        return httpx.Response(404, json={"message": "not found"})

    def _build_real_service(alpaca_settings: Any = None) -> AlpacaExecutionService:
        settings = load_settings().broker.alpaca
        http_client = httpx.Client(
            transport=httpx.MockTransport(handler), base_url="https://paper-api.alpaca.markets"
        )
        return AlpacaExecutionService(settings, client=AlpacaClient(settings, http_client=http_client))

    monkeypatch.setattr(submit_orders_module, "AlpacaExecutionService", _build_real_service)

    # One approved candidate only: this test pins the COR-06 guarantee that the ambiguous
    # intent itself is never re-sent. A SECOND approved candidate is a different, new intent;
    # the D-15 gate (20.1-10) refuses any further submission while this one is unresolved.
    with session_scope(load_settings()) as session:
        msft_event = session.execute(
            select(RiskEvent).join(Symbol, RiskEvent.symbol_id == Symbol.id).where(
                Symbol.ticker == "MSFT"
            )
        ).scalar_one()
        session.delete(msft_event)

    with TestClient(create_app()) as client:
        failed = _submit_and_run(client, "e2e-paper-ambiguous")
        assert failed["status"] == "failed"
        assert failed["failure_reason"] == "handler_error"
        assert failed["outcome_uncertain"] is True
        assert "external_broker_session_started" in _log_codes(client, failed["id"])
        assert len(posts) == 1

        with session_scope(load_settings()) as session:
            orders = session.execute(select(PaperOrder)).scalars().all()
            attempts = session.execute(select(OrderSubmissionAttempt)).scalars().all()
        assert len(orders) == 1
        assert orders[0].status == OrderLifecycleState.UNKNOWN
        assert orders[0].broker_order_id is None
        assert [(a.attempt_number, a.outcome_class) for a in attempts] == [(1, "ambiguous")]

        # D-15 / 20.1-10: while the ambiguous intent is unresolved, a second submission for
        # the strategy is refused (typed 409, zero Jobs written) and nothing is ever re-sent.
        jobs_before = _job_count()
        second = _submit(client, "e2e-paper-ambiguous-second")
        assert second.status_code == 409
        assert second.json()["detail"]["code"] == "outcome_unresolved"
        assert second.json()["detail"]["required_job_type"] == "reconciliation"
        assert _job_count() == jobs_before
        assert len(posts) == 1
        with session_scope(load_settings()) as session:
            assert len(session.execute(select(OrderSubmissionAttempt)).scalars().all()) == 1
            ambiguous_order = session.get(PaperOrder, orders[0].id)
            assert ambiguous_order.status == OrderLifecycleState.UNKNOWN
