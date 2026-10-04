"""HTTP/production-path tests for the D-15 recovery gates (REC-01, 20.1-10).

The gate lives inside ``PaperSessionSubmissionSpec.validate_payload`` (the pattern 20.1-01
ownership and 20.1-05 eligibility use for DB-reading checks), so ``POST /api/v1/jobs`` and
``POST /api/v1/jobs/{id}/retry`` are both gated by the SAME call: a typed 409
``{code, job_type, strategy_id, required_job_type: 'reconciliation'}`` with zero Job rows and
zero ``job_mutation`` rows. ``broker-order-sync`` is never gated and never resolves anything.
"""

from __future__ import annotations

import ast
import inspect
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from tests.support.paper_eligibility import allow_paper_execution
from tests.support.query_counter import count_queries
from tests.support.recovery_fixtures import (
    at,
    seed_account_run,
    seed_intent,
    seed_job,
    seed_paper_run,
    seed_uncertain_session,
)
from tests.test_job_operations_e2e import (
    _run_worker_once,
    job_operations_env,
    migrated_backtest_db,
    strategy_config_override,
)
from tests.test_paper_execution import FakeBrokerClient
from tests.test_paper_session_job_e2e import (
    PAYLOAD,
    BrokerFakes,
    _empty_account,
    paper_jobs_env,
)

from trading_platform.api.app import create_app
from trading_platform.core.settings import load_settings
from trading_platform.db.models import (
    AttemptOutcomeClass,
    Job,
    JobMutation,
    JobStatus,
    OrderLifecycleState,
)
from trading_platform.db.session import get_engine, session_scope
from trading_platform.jobs.handlers.paper_session_submission import PaperSessionSubmissionSpec
from trading_platform.jobs.registry import (
    RECOVERY_CONFLICT_CODES,
    build_default_registry,
    recovery_gated_for,
)
from trading_platform.services import recovery
from trading_platform.services.execution import sync_orders as sync_orders_module

# Fixtures consumed by pytest name; re-exported so ruff F401 passes.
__all__ = [
    "job_operations_env",
    "migrated_backtest_db",
    "paper_jobs_env",
    "strategy_config_override",
]

STRATEGY_ID = "trend_following_daily"
AMBIGUOUS = AttemptOutcomeClass.AMBIGUOUS


@pytest.fixture(autouse=True)
def _eligible_paper_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    """Eligibility and provenance are not this module's subject (see
    tests/support/paper_eligibility.py); the recovery gate is."""

    allow_paper_execution(monkeypatch)


def _arrange(builder: Callable[[Any], Any]) -> Any:
    with session_scope(load_settings()) as session:
        return builder(session)


def _counts() -> tuple[int, int]:
    with session_scope(load_settings()) as session:
        jobs = session.execute(select(func.count()).select_from(Job)).scalar_one()
        mutations = session.execute(select(func.count()).select_from(JobMutation)).scalar_one()
    return jobs, mutations


def _submit(client: TestClient, key: str, *, job_type: str = "paper-session", payload: Any = None):
    return client.post(
        "/api/v1/jobs",
        headers={"Idempotency-Key": key},
        json={"job_type": job_type, "payload": payload if payload is not None else PAYLOAD},
    )


def _make_unresolved(code: str) -> None:
    """Arrange the strategy state for one recovery gate code."""

    if code == "outcome_unresolved":
        _arrange(lambda s: seed_uncertain_session(s, completed_at=at(0)))
    else:
        # An uncertain broker-order-sync Job (nothing_submitted) awaits the fresh check.
        _arrange(lambda s: seed_job(s, job_type="broker-order-sync", completed_at=at(0)))
        if code == "reconciliation_not_clean":
            _arrange(lambda s: seed_account_run(s, completed_at=at(1), blocks=True))


def _resolve() -> None:
    _arrange(lambda s: seed_account_run(s, completed_at=datetime.now(UTC)))


# ---------------------------------------------------------------------------
# Fresh submission
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code", ["outcome_unresolved", "reconciliation_required", "reconciliation_not_clean"]
)
def test_fresh_paper_session_rejected_while_unresolved(
    paper_jobs_env: BrokerFakes, code: str
) -> None:
    _make_unresolved(code)
    before = _counts()
    with TestClient(create_app()) as client:
        response = _submit(client, f"fresh-{code}")
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == code
    assert detail["job_type"] == "paper-session"
    assert detail["strategy_id"] == STRATEGY_ID
    assert detail["required_job_type"] == "reconciliation"
    # Zero Job rows and zero job_mutation rows were written.
    assert _counts() == before
    assert paper_jobs_env.execution.submitted_intents == []


def test_fresh_paper_session_is_accepted_when_nothing_is_uncertain(
    paper_jobs_env: BrokerFakes,
) -> None:
    with TestClient(create_app()) as client:
        assert _submit(client, "fresh-clean").status_code == 202


# ---------------------------------------------------------------------------
# Retry
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code", ["outcome_unresolved", "reconciliation_required", "reconciliation_not_clean"]
)
def test_retry_of_failed_paper_session_rejected_while_unresolved(
    paper_jobs_env: BrokerFakes, code: str
) -> None:
    failed = _arrange(
        lambda s: seed_job(
            s,
            job_type="paper-session",
            payload=dict(PAYLOAD),
            completed_at=at(0),
            uncertain=False,
        )
    )
    _make_unresolved(code)
    before = _counts()
    expected_block = {
        "code": code,
        "required_job_type": "reconciliation",
        "strategy_id": STRATEGY_ID,
    }
    with TestClient(create_app()) as client:
        # The Job detail reports the very same block.
        assert client.get(f"/api/v1/jobs/{failed.id}").json()["retry_blocked"] == expected_block
        response = client.post(
            f"/api/v1/jobs/{failed.id}/retry", headers={"Idempotency-Key": f"retry-{code}"}
        )
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == {**expected_block, "job_type": "paper-session"}
    assert _counts() == before


def test_retry_is_accepted_once_the_gate_resolves(paper_jobs_env: BrokerFakes) -> None:
    failed = _arrange(
        lambda s: seed_job(
            s, job_type="paper-session", payload=dict(PAYLOAD), completed_at=at(0), uncertain=False
        )
    )
    _make_unresolved("reconciliation_required")
    with TestClient(create_app()) as client:
        assert client.get(f"/api/v1/jobs/{failed.id}").json()["retry_blocked"] is not None
        _resolve()
        assert client.get(f"/api/v1/jobs/{failed.id}").json()["retry_blocked"] is None
        retry = client.post(
            f"/api/v1/jobs/{failed.id}/retry", headers={"Idempotency-Key": "retry-resolved"}
        )
    assert retry.status_code == 202, retry.text


def test_retry_block_is_none_for_non_gated_types_and_for_succeeded_jobs(
    paper_jobs_env: BrokerFakes,
) -> None:
    _make_unresolved("outcome_unresolved")

    def build(session: Any) -> tuple[Job, Job, Job]:
        succeeded = seed_job(
            session,
            job_type="paper-session",
            payload=dict(PAYLOAD),
            completed_at=at(2),
            uncertain=False,
            status=JobStatus.SUCCEEDED,
        )
        sync = seed_job(
            session,
            job_type="broker-order-sync",
            payload={"strategy_id": STRATEGY_ID, "as_of_session": "2024-01-05"},
            completed_at=at(3),
            uncertain=True,
        )
        queued = seed_job(
            session,
            job_type="paper-session",
            payload=dict(PAYLOAD),
            completed_at=None,
            uncertain=False,
            status=JobStatus.QUEUED,
        )
        queued.completed_at = None
        return succeeded, sync, queued

    succeeded, sync, queued = _arrange(build)
    with TestClient(create_app()) as client:
        for job in (succeeded, sync, queued):
            assert client.get(f"/api/v1/jobs/{job.id}").json()["retry_blocked"] is None, job.id


# ---------------------------------------------------------------------------
# broker-order-sync and the other types are never gated
# ---------------------------------------------------------------------------


def test_broker_order_sync_never_gated_and_never_resolves(
    paper_jobs_env: BrokerFakes, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_unresolved("outcome_unresolved")
    failed_sync = _arrange(
        lambda s: seed_job(
            s,
            job_type="broker-order-sync",
            payload={"strategy_id": STRATEGY_ID, "as_of_session": "2024-01-05"},
            completed_at=at(5),
            uncertain=True,
        )
    )
    monkeypatch.setattr(
        sync_orders_module,
        "AlpacaClient",
        lambda *_a, **_k: FakeBrokerClient(
            orders=[], fills=[], positions=[], account=_empty_account()
        ),
    )

    def status() -> tuple[bool, str | None]:
        with session_scope(load_settings()) as session:
            result = recovery.strategy_recovery_status(session, STRATEGY_ID)
        return result.resolved, result.gate_code.value if result.gate_code else None

    before = status()
    assert before == (False, "outcome_unresolved")
    with TestClient(create_app()) as client:
        account = _submit(
            client, "sync-account", job_type="broker-order-sync", payload={"scope": "account"}
        )
        strategy = _submit(
            client,
            "sync-strategy",
            job_type="broker-order-sync",
            payload={"strategy_id": STRATEGY_ID, "as_of_session": "2024-01-05"},
        )
        assert account.status_code == 202, account.text
        assert strategy.status_code == 202, strategy.text
        _run_worker_once()
        _run_worker_once()
        for response in (account, strategy):
            job = client.get(f"/api/v1/jobs/{response.json()['job_id']}").json()
            assert job["status"] == "succeeded", job["failure_message"]
        # A retry of a FAILED uncertain sync is accepted too (no reconcile-first).
        assert client.get(f"/api/v1/jobs/{failed_sync.id}").json()["retry_blocked"] is None
        retry = client.post(
            f"/api/v1/jobs/{failed_sync.id}/retry", headers={"Idempotency-Key": "retry-sync"}
        )
        assert retry.status_code == 202, retry.text
    # The sync resolved nothing: same gate, and the paper-session gate still refuses.
    assert status() == before
    with TestClient(create_app()) as client:
        assert _submit(client, "still-blocked").status_code == 409


def test_reconciliation_and_other_types_unaffected(paper_jobs_env: BrokerFakes) -> None:
    _make_unresolved("outcome_unresolved")
    with TestClient(create_app()) as client:
        strategy_scope = _submit(
            client,
            "recon-strategy",
            job_type="reconciliation",
            payload={"strategy_id": STRATEGY_ID, "as_of_session": "2024-01-05"},
        )
        account_scope = _submit(
            client, "recon-account", job_type="reconciliation", payload={"scope": "account"}
        )
    assert strategy_scope.status_code == 202, strategy_scope.text
    assert account_scope.status_code == 202, account_scope.text


# ---------------------------------------------------------------------------
# Run-time defence
# ---------------------------------------------------------------------------


def test_run_time_gate_blocks_queued_job_without_broker_calls(
    paper_jobs_env: BrokerFakes,
) -> None:
    """A Job queued before the uncertainty appeared must not reach the broker."""

    with TestClient(create_app()) as client:
        queued = _submit(client, "queued-before-uncertainty")
        assert queued.status_code == 202, queued.text
        _make_unresolved("outcome_unresolved")
        _run_worker_once()
        detail = client.get(f"/api/v1/jobs/{queued.json()['job_id']}").json()

    assert detail["status"] == "succeeded", detail["failure_message"]
    assert detail["result_summary"]["action"] == "blocked_outcome_unresolved"
    execution_run = detail["result_summary"]["execution_run_id"]
    assert execution_run is not None
    # Zero broker POSTs and zero broker reads.
    assert paper_jobs_env.execution.submitted_intents == []
    assert paper_jobs_env.state_clients_built == 0
    with TestClient(create_app()) as client:
        run = client.get(f"/api/v1/runs/{execution_run}").json()["run"]
    assert run["result_summary"]["blocked_reason"] == "outcome_unresolved"
    assert run["result_summary"]["action"] == "blocked_outcome_unresolved"
    assert run["result_summary"]["required_job_type"] == "reconciliation"


@pytest.mark.parametrize("code", ["reconciliation_required", "reconciliation_not_clean"])
def test_run_time_gate_carries_the_reconciliation_gate_codes(
    paper_jobs_env: BrokerFakes, code: str
) -> None:
    with TestClient(create_app()) as client:
        queued = _submit(client, f"queued-{code}")
        assert queued.status_code == 202
        _make_unresolved(code)
        _run_worker_once()
        detail = client.get(f"/api/v1/jobs/{queued.json()['job_id']}").json()
    assert detail["result_summary"]["action"] == "blocked_outcome_unresolved"
    assert paper_jobs_env.execution.submitted_intents == []
    assert paper_jobs_env.state_clients_built == 0
    with TestClient(create_app()) as client:
        run = client.get(f"/api/v1/runs/{detail['result_summary']['execution_run_id']}").json()
    assert run["run"]["result_summary"]["blocked_reason"] == code


# ---------------------------------------------------------------------------
# Mechanism pins
# ---------------------------------------------------------------------------


def test_gate_is_read_only_and_bounded(paper_jobs_env: BrokerFakes) -> None:
    spec = PaperSessionSubmissionSpec(load_settings())

    def conflict_statements() -> int:
        with count_queries(get_engine(load_settings())) as counter:
            with pytest.raises(Exception) as raised:
                spec.validate_payload(PAYLOAD)
        assert getattr(raised.value, "code", None) in RECOVERY_CONFLICT_CODES
        assert not [
            s
            for s in counter.statements
            if s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
        ]
        return counter.count

    _arrange(lambda s: seed_uncertain_session(s, completed_at=at(0)))
    small = conflict_statements()

    def history(session: Any) -> None:
        for index in range(10):
            job = seed_job(session, completed_at=at(index + 1))
            run = seed_paper_run(session, job)
            seed_intent(session, run, status=OrderLifecycleState.UNKNOWN, attempts=(AMBIGUOUS,))

    _arrange(history)
    assert conflict_statements() == small


def test_orchestration_layer_still_imports_no_services() -> None:
    from trading_platform.orchestration import job_mutations

    tree = ast.parse(Path(inspect.getsourcefile(job_mutations) or "").read_text())
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
        elif isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
    assert not [m for m in imported if m.startswith("trading_platform.services")]
    assert "recovery_gated_for" in Path(inspect.getsourcefile(job_mutations) or "").read_text()


def test_recovery_conflict_codes_equal_the_domain_gate_codes() -> None:
    assert RECOVERY_CONFLICT_CODES == recovery.RECOVERY_GATE_CODES


def test_recovery_gated_marker_is_exposed_through_the_registry() -> None:
    registry = build_default_registry(load_settings())
    assert recovery_gated_for(registry.resolve_submission_spec("paper-session")) is True
    for job_type in registry.list_job_types():
        if job_type != "paper-session":
            assert recovery_gated_for(registry.resolve_submission_spec(job_type)) is False


def test_replayed_key_reevaluates_the_gate(paper_jobs_env: BrokerFakes) -> None:
    """Consequence recorded in the SUMMARY: validation precedes the idempotent-replay lookup, so
    replaying a key while the strategy has become unresolved is refused (409), and replays
    normally (200 + Idempotency-Replayed) once the gate has resolved again."""

    with TestClient(create_app()) as client:
        first = _submit(client, "replay-key")
        assert first.status_code == 202
        _make_unresolved("reconciliation_required")
        refused = _submit(client, "replay-key")
        assert refused.status_code == 409
        assert refused.json()["detail"]["code"] == "reconciliation_required"
        _resolve()
        replay = _submit(client, "replay-key")
    assert replay.status_code == 200
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert replay.json()["job_id"] == first.json()["job_id"]
