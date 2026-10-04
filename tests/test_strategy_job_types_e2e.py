"""Phase 20 (Plan 19) production-path E2E for the three strategy-scoped Job
types -- ``risk-evaluation``, ``reconciliation`` and ``broker-order-sync`` --
plus operator retry (SC4) and the D-19 reconcile-first block.

Every test drives the whole vertical slice through the **production** Job
registry: ``create_app()`` (lifespan builds ``build_default_registry``) ->
``POST /api/v1/jobs`` -> the real ``run-jobs --once`` CLI command ->
the existing domain service -> observable ``GET /api/v1/jobs/{id}``.

PAPER-mode types (``reconciliation``, ``broker-order-sync``) pass the
per-dispatch preflight (D-22) only with Alpaca paper credentials configured.
The environment therefore sets non-empty *fake* credential strings and
replaces the ``AlpacaClient`` symbol the two services construct when no
``broker_client`` is passed (monkeypatch on the service module attribute --
handler code is never touched). No network call is ever made.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from tests.support.paper_ownership import seed_registered_strategy
from tests.support.symbol_metadata import ready_symbol_fields
from tests.test_analytics_service import _seed_paper_operational_state
from tests.test_job_operations_e2e import (
    _counts,
    _run_worker_once,
    job_operations_env,
    migrated_backtest_db,
    strategy_config_override,
)
from tests.test_paper_execution import FakeBrokerClient

from trading_platform.api.app import create_app
from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.db.models import PaperOrder, RiskEvent
from trading_platform.db.models.symbol import Symbol
from trading_platform.db.session import session_scope
from trading_platform.jobs.handlers import reconciliation as reconciliation_handler_module
from trading_platform.jobs.handlers import risk_evaluation as risk_handler_module
from trading_platform.services.alpaca import BrokerAccountSnapshot
from trading_platform.services.execution import sync_orders as sync_orders_module
from trading_platform.services.reconciliation import report as reconciliation_report_module

# Fixtures consumed by pytest name (strategy_jobs_env -> job_operations_env ->
# migrated_backtest_db/strategy_config_override); re-exported so ruff F401 passes.
__all__ = [
    "job_operations_env",
    "migrated_backtest_db",
    "strategy_config_override",
]

STRATEGY_ID = "trend_following_daily"
AS_OF_SESSION = "2024-01-10"
PAYLOAD: dict[str, str] = {"strategy_id": STRATEGY_ID, "as_of_session": AS_OF_SESSION}
STRATEGY_JOB_TYPES = ["risk-evaluation", "reconciliation", "broker-order-sync"]

_FAKE_API_KEY = "fake-key-not-a-secret"  # pragma: allowlist secret
_FAKE_API_SECRET = "fake-secret-not-a-secret"  # pragma: allowlist secret


class _ExplodingSyncBrokerClient(FakeBrokerClient):
    """Fake broker whose first read raises -- simulates a broker failure
    *after* the handler logged ``external_broker_sync_started``."""

    def list_orders(self) -> list[Any]:
        raise RuntimeError("simulated broker outage during sync")


@dataclass
class BrokerScript:
    """Mutable seam the patched ``AlpacaClient`` factory reads on every call."""

    fail: bool = False
    constructed: int = 0
    _account: BrokerAccountSnapshot = field(default_factory=lambda: _empty_account())

    def build(self, _alpaca_settings: Any = None) -> FakeBrokerClient:
        self.constructed += 1
        kwargs: dict[str, Any] = {
            "orders": [],
            "fills": [],
            "positions": [],
            "account": self._account,
        }
        if self.fail:
            return _ExplodingSyncBrokerClient(**kwargs)
        return FakeBrokerClient(**kwargs)


def _empty_account() -> BrokerAccountSnapshot:
    from decimal import Decimal

    return BrokerAccountSnapshot(
        cash=Decimal("100000.000000"),
        buying_power=Decimal("100000.000000"),
        equity=Decimal("100000.000000"),
        long_market_value=Decimal("0"),
        short_market_value=Decimal("0"),
        raw_payload={"equity": "100000.000000"},
    )


@pytest.fixture()
def strategy_jobs_env(
    job_operations_env: None, monkeypatch: pytest.MonkeyPatch
) -> Iterator[BrokerScript]:
    """job_operations_env (mutations enabled, seeded sessions/bars, blank
    broker creds) plus fake Alpaca paper creds and the faked broker seam."""

    monkeypatch.setenv("TRADING_PLATFORM_BROKER__ALPACA__API_KEY", _FAKE_API_KEY)
    monkeypatch.setenv("TRADING_PLATFORM_BROKER__ALPACA__API_SECRET", _FAKE_API_SECRET)
    clear_settings_cache()

    # Explicit arrangement (20.1-01): the strategy under test is enabled AND the
    # active paper strategy, so strategy-scoped reconciliation is admitted (D-03).
    seed_registered_strategy(load_settings(), STRATEGY_ID, enabled=True, owner=True)
    # D-29 (20.1-04): the seeded universe models READY symbols; tests that need a
    # metadata-less symbol clear it explicitly through _set_symbol_metadata.
    _set_symbol_metadata(ready=True)

    script = BrokerScript()
    monkeypatch.setattr(reconciliation_report_module, "AlpacaClient", script.build)
    monkeypatch.setattr(sync_orders_module, "AlpacaClient", script.build)
    try:
        yield script
    finally:
        clear_settings_cache()


def _set_symbol_metadata(*, ready: bool, tickers: tuple[str, ...] = ("AAPL", "MSFT")) -> None:
    fields = (
        ready_symbol_fields()
        if ready
        else {"market": None, "symbol_type": None, "primary_exchange": None, "metadata_provider": None}
    )
    with session_scope(load_settings()) as session:
        for symbol in session.execute(select(Symbol).where(Symbol.ticker.in_(tickers))).scalars():
            for name, value in fields.items():
                setattr(symbol, name, value)


def _submit(client: TestClient, job_type: str, key: str, payload: dict[str, str] | None = None):
    return client.post(
        "/api/v1/jobs",
        headers={"Idempotency-Key": key},
        json={"job_type": job_type, "payload": payload if payload is not None else PAYLOAD},
    )


def _submit_and_run(client: TestClient, job_type: str, key: str) -> dict[str, Any]:
    submitted = _submit(client, job_type, key)
    assert submitted.status_code == 202, submitted.text
    job_id = submitted.json()["job_id"]
    _run_worker_once()
    detail = client.get(f"/api/v1/jobs/{job_id}")
    assert detail.status_code == 200
    return detail.json()


def _assert_resources_match_produced_run_ids(detail: dict[str, Any]) -> None:
    """D-09: result_summary.produced_run_ids is exactly the set of linked
    strategy_run resources on the Job detail."""

    produced = set(detail["result_summary"]["produced_run_ids"])
    linked = {r["id"] for r in detail["resources"]}
    assert produced == linked
    assert all(r["kind"] == "strategy_run" for r in detail["resources"])


def _paper_order_sync_fields() -> list[tuple[uuid.UUID, int, str | None, Any]]:
    with session_scope(load_settings()) as session:
        rows = session.execute(
            select(
                PaperOrder.id,
                PaperOrder.sync_failure_count,
                PaperOrder.last_sync_error,
                PaperOrder.last_sync_failure_at,
            ).order_by(PaperOrder.id)
        ).all()
    return [tuple(row) for row in rows]  # type: ignore[misc]


# --- Task 1: the three Job types end-to-end ---------------------------------


def _risk_event_codes(as_of_session: str) -> dict[str, str]:
    with session_scope(load_settings()) as session:
        rows = session.execute(
            select(Symbol.ticker, RiskEvent.decision_code)
            .join(Symbol, Symbol.id == RiskEvent.symbol_id)
            .where(RiskEvent.session_date == date.fromisoformat(as_of_session))
        ).all()
    return {ticker: code for ticker, code in rows}


def test_risk_evaluation_job_rejects_metadata_less_symbol_as_symbol_not_ready(
    strategy_jobs_env: BrokerScript,
) -> None:
    """D-29: a symbol without required metadata is rejected by the Job-run risk
    evaluation with `symbol_not_ready`; the rest of the evaluation is unaffected."""

    _set_symbol_metadata(ready=False, tickers=("AAPL",))
    payload = {"strategy_id": STRATEGY_ID, "as_of_session": "2024-01-05"}

    with TestClient(create_app()) as client:
        submitted = _submit(client, "risk-evaluation", "e2e-risk-not-ready", payload)
        assert submitted.status_code == 202, submitted.text
        _run_worker_once()
        detail = client.get(f"/api/v1/jobs/{submitted.json()['job_id']}").json()

    assert detail["status"] == "succeeded", detail["failure_message"]
    codes = _risk_event_codes("2024-01-05")
    assert codes["AAPL"] == "symbol_not_ready"
    assert codes["MSFT"] == "non_actionable_signal"


def test_risk_evaluation_job_with_ready_metadata_does_not_reject_as_not_ready(
    strategy_jobs_env: BrokerScript,
) -> None:
    payload = {"strategy_id": STRATEGY_ID, "as_of_session": "2024-01-05"}

    with TestClient(create_app()) as client:
        submitted = _submit(client, "risk-evaluation", "e2e-risk-ready", payload)
        assert submitted.status_code == 202, submitted.text
        _run_worker_once()

    codes = _risk_event_codes("2024-01-05")
    assert codes["AAPL"] != "symbol_not_ready"
    assert "symbol_not_ready" not in codes.values()


def test_risk_evaluation_job_runs_through_production_path(
    strategy_jobs_env: BrokerScript,
) -> None:
    """SC1/OPS-02: production registry, real worker pass, one linked run."""

    with TestClient(create_app()) as client:
        submitted = _submit(client, "risk-evaluation", "e2e-risk-1")
        assert submitted.status_code == 202
        body = submitted.json()
        job_id = body["job_id"]
        assert body["job_type"] == "risk-evaluation"

        _run_worker_once()

        detail = client.get(body["links"]["self"]).json()
        assert detail["status"] == "succeeded"
        assert detail["failure_reason"] is None

        assert len(detail["resources"]) == 1
        run_resource = detail["resources"][0]
        assert run_resource["kind"] == "strategy_run"
        assert run_resource["id"] in detail["result_summary"]["produced_run_ids"]
        _assert_resources_match_produced_run_ids(detail)

        run_detail = client.get(f"/api/v1/runs/{run_resource['id']}").json()["run"]

    assert run_detail["trigger_source"] == "job"
    assert run_detail["job_id"] == job_id
    # Risk evaluation is BACKTEST-mode: it never touched the broker seam.
    assert strategy_jobs_env.constructed == 0


def test_reconciliation_job_runs_report_only(strategy_jobs_env: BrokerScript) -> None:
    """SC1/OPS-04 + D-06: one linked reconciliation run; the per-order
    sync-failure fields are never mutated (corrections are a separate step)."""

    _seed_paper_operational_state()
    before = _paper_order_sync_fields()
    assert before, "seeded paper orders are required for the D-06 comparison"

    with TestClient(create_app()) as client:
        detail = _submit_and_run(client, "reconciliation", "e2e-recon-1")
        assert detail["status"] == "succeeded", detail["failure_message"]
        assert len(detail["resources"]) == 1
        run_resource = detail["resources"][0]
        assert run_resource["kind"] == "strategy_run"
        _assert_resources_match_produced_run_ids(detail)

        run_detail = client.get(f"/api/v1/runs/{run_resource['id']}").json()["run"]

    assert run_detail["run_type"] == "reconciliation"
    assert run_detail["trigger_source"] == "job"
    assert run_detail["job_id"] == detail["id"]
    # The empty fake broker disagrees with the seeded local orders, so the run
    # really found drift -- and still wrote nothing to the orders (D-06).
    assert detail["result_summary"]["finding_count"] > 0
    assert strategy_jobs_env.constructed == 1
    assert _paper_order_sync_fields() == before


def test_broker_order_sync_job_runs_with_no_resources(strategy_jobs_env: BrokerScript) -> None:
    """SC1/OPS-06: no strategy_run is produced (D-08); the sync counters ride
    on result_summary."""

    _seed_paper_operational_state()

    with TestClient(create_app()) as client:
        detail = _submit_and_run(client, "broker-order-sync", "e2e-sync-1")

    assert detail["status"] == "succeeded", detail["failure_message"]
    assert detail["outcome_uncertain"] is False
    assert detail["resources"] == []
    summary = detail["result_summary"]
    assert "orders_synced" in summary
    assert "fills_ingested" in summary
    assert summary["produced_run_ids"] == []
    assert strategy_jobs_env.constructed == 1


def test_cancel_queued_reconciliation_never_executes(
    strategy_jobs_env: BrokerScript, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-02 queued path: a cancel accepted while QUEUED means the service is
    never called and no broker client is ever built."""

    def _must_not_run(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("reconcile_paper_execution must not run for a cancelled QUEUED Job")

    monkeypatch.setattr(reconciliation_handler_module, "reconcile_paper_execution", _must_not_run)

    with TestClient(create_app()) as client:
        submitted = _submit(client, "reconciliation", "e2e-recon-cancel")
        assert submitted.status_code == 202
        links = submitted.json()["links"]

        cancel = client.post(
            links["self"] + "/cancel",
            headers={"Idempotency-Key": "cancel-recon-queued"},
            json={"reason": "operator stop"},
        )
        # The cancel route answers 200 for every accepted (non-replayed or
        # replayed) cancel; only submit/retry answer 202.
        assert cancel.status_code == 200
        assert cancel.json()["status"] == "cancelled"

        _run_worker_once()
        detail = client.get(links["self"]).json()

    assert detail["status"] == "cancelled"
    assert detail["cancellation_cause"] == "operator_request"
    assert detail["resources"] == []
    assert detail["result_summary"] == {}
    assert strategy_jobs_env.constructed == 0


@pytest.mark.parametrize("job_type", STRATEGY_JOB_TYPES)
def test_missing_as_of_session_rejected_for_each_type(
    strategy_jobs_env: BrokerScript, job_type: str
) -> None:
    """D-21: as_of_session is required; a 422 with zero rows written."""

    before = _counts()
    with TestClient(create_app()) as client:
        response = _submit(
            client, job_type, f"e2e-missing-{job_type}", payload={"strategy_id": STRATEGY_ID}
        )
    after = _counts()

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "code": "invalid_job_payload",
        "job_type": job_type,
        "reason": "missing_required_field",
    }
    assert after == before


def test_generated_resources_match_produced_run_ids(strategy_jobs_env: BrokerScript) -> None:
    """D-09 generalization: for every successful Job of the three types,
    produced_run_ids == the linked strategy_run resource ids (run-producing
    types: exactly one; broker-order-sync: none)."""

    _seed_paper_operational_state()
    expected_resource_counts = {"risk-evaluation": 1, "reconciliation": 1, "broker-order-sync": 0}

    with TestClient(create_app()) as client:
        for job_type in STRATEGY_JOB_TYPES:
            detail = _submit_and_run(client, job_type, f"e2e-resources-{job_type}")
            assert detail["status"] == "succeeded", (job_type, detail["failure_message"])
            assert len(detail["resources"]) == expected_resource_counts[job_type]
            _assert_resources_match_produced_run_ids(detail)


# --- Task 2: operator retry + reconcile-first block --------------------------


def test_retry_failed_risk_evaluation_end_to_end(
    strategy_jobs_env: BrokerScript, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SC4/OPS-07: lineage, idempotent replay, rejection and the retry Job
    running to SUCCEEDED on the next worker pass."""

    real_run_risk_evaluation = risk_handler_module.run_risk_evaluation
    calls: list[int] = []

    def _fail_first_call(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("simulated risk evaluation failure")
        return real_run_risk_evaluation(*args, **kwargs)

    monkeypatch.setattr(risk_handler_module, "run_risk_evaluation", _fail_first_call)

    with TestClient(create_app()) as client:
        original = _submit_and_run(client, "risk-evaluation", "e2e-retry-original")
        original_id = original["id"]
        assert original["status"] == "failed"
        assert original["failure_reason"] == "handler_error"
        assert original["outcome_uncertain"] is False
        assert original["retry_blocked"] is None
        assert original["retried_as_job_id"] is None

        retry = client.post(
            f"/api/v1/jobs/{original_id}/retry", headers={"Idempotency-Key": "retry-key-K"}
        )
        assert retry.status_code == 202, retry.text
        retry_id = retry.json()["job_id"]
        assert retry_id != original_id

        retry_detail = client.get(f"/api/v1/jobs/{retry_id}").json()
        assert retry_detail["job_type"] == original["job_type"]
        assert retry_detail["payload"] == original["payload"]
        assert retry_detail["retry_of_job_id"] == original_id
        assert retry_detail["status"] == "queued"

        original_after = client.get(f"/api/v1/jobs/{original_id}").json()
        assert original_after["retried_as_job_id"] == retry_id

        replay = client.post(
            f"/api/v1/jobs/{original_id}/retry", headers={"Idempotency-Key": "retry-key-K"}
        )
        assert replay.status_code == 200
        assert replay.headers["Idempotency-Replayed"] == "true"
        assert replay.json()["job_id"] == retry_id

        duplicate = client.post(
            f"/api/v1/jobs/{original_id}/retry", headers={"Idempotency-Key": "retry-key-fresh"}
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["detail"] == {
            "code": "retry_exists",
            "existing_retry_job_id": retry_id,
        }

        _run_worker_once()

        succeeded = client.get(f"/api/v1/jobs/{retry_id}").json()
        assert succeeded["status"] == "succeeded", succeeded["failure_message"]
        assert len(succeeded["resources"]) == 1
        _assert_resources_match_produced_run_ids(succeeded)

        not_retryable = client.post(
            f"/api/v1/jobs/{retry_id}/retry", headers={"Idempotency-Key": "retry-succeeded"}
        )

    assert not_retryable.status_code == 409
    assert not_retryable.json()["detail"] == {
        "code": "job_not_retryable",
        "status": "succeeded",
    }
    assert len(calls) == 2


def test_broker_order_sync_uncertain_failure_retry_is_never_gated(
    strategy_jobs_env: BrokerScript,
) -> None:
    """D-15 / 20.1-10 (supersedes Phase 20 D-19 / T-20-19-01): a broker sync that fails after
    its external marker is outcome_uncertain, but broker sync is NEVER gated and never resolves
    anything by itself: ``retry_blocked`` is null and the retry is accepted without reconciling."""

    strategy_jobs_env.fail = True

    with TestClient(create_app()) as client:
        failed = _submit_and_run(client, "broker-order-sync", "e2e-sync-uncertain")
        failed_id = failed["id"]
        assert failed["status"] == "failed"
        assert failed["failure_reason"] == "handler_error"
        assert failed["outcome_uncertain"] is True
        assert failed["retry_blocked"] is None

        # No reconciliation first: the retry is accepted straight away.
        retry = client.post(
            f"/api/v1/jobs/{failed_id}/retry", headers={"Idempotency-Key": "retry-no-gate"}
        )
        assert retry.status_code == 202, retry.text
        retry_detail = client.get(f"/api/v1/jobs/{retry.json()['job_id']}").json()

    assert retry_detail["retry_of_job_id"] == failed_id
    assert retry_detail["job_type"] == "broker-order-sync"
    assert retry_detail["payload"] == failed["payload"]
