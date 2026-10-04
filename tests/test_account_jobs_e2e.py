"""Production-path E2E for the owner-less account scope of the ``reconciliation`` and
``broker-order-sync`` Job types (ACCT-01, D-06, D-09).

Drives ``POST /api/v1/jobs`` (the existing ORCH-07 mutation guard; this plan adds no
new mutating route) -> the real ``run-jobs --once`` command -> the real handlers and
services, with the broker faked at the same seam as the paper-session E2E. Both
types must work with NO owner and while trading is blocked, submit nothing and leave
ownership untouched.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from tests.support.paper_ownership import clear_active_paper_strategy
from tests.test_paper_session_job_e2e import (
    AS_OF_SESSION,
    BrokerFakes,
    _submit_and_run,
    job_operations_env,  # noqa: F401
    migrated_backtest_db,  # noqa: F401
    paper_jobs_env,  # noqa: F401
    strategy_config_override,  # noqa: F401
)

from trading_platform.api.app import create_app
from trading_platform.core.settings import load_settings
from trading_platform.db.models import (
    AccountReconciliationRun,
    ActivePaperStrategy,
    PaperOrder,
    Position,
    StrategyRun,
)
from trading_platform.db.session import session_scope
from trading_platform.services.execution import sync_orders as sync_orders_module
from trading_platform.services.operator_controls import OperatorControlService


@pytest.fixture()
def account_env(
    paper_jobs_env: BrokerFakes,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[BrokerFakes]:
    monkeypatch.setattr(sync_orders_module, "AlpacaClient", paper_jobs_env.build_state_client)
    clear_active_paper_strategy(load_settings())
    yield paper_jobs_env


def _count(model: type) -> int:
    with session_scope(load_settings()) as session:
        return session.scalar(select(func.count()).select_from(model)) or 0


def _owner_row() -> tuple[object, ...]:
    with session_scope(load_settings()) as session:
        row = session.execute(select(ActivePaperStrategy)).scalar_one()
        return tuple(getattr(row, c.name) for c in ActivePaperStrategy.__table__.columns)


def _run_both_account_jobs(client: TestClient, key_prefix: str) -> tuple[dict, dict]:
    reconciliation = _submit_and_run(
        client, f"{key_prefix}-recon", job_type="reconciliation", payload={"scope": "account"}
    )
    sync = _submit_and_run(
        client,
        f"{key_prefix}-sync",
        job_type="broker-order-sync",
        payload={"scope": "account", "as_of_session": AS_OF_SESSION},
    )
    return reconciliation, sync


def _assert_account_results(
    reconciliation: dict, sync: dict, fakes: BrokerFakes, *, runs_before: int, owner_before: tuple
) -> None:
    assert reconciliation["status"] == "succeeded", reconciliation["failure_message"]
    assert sync["status"] == "succeeded", sync["failure_message"]

    # reconciliation: an owner-less account run, linked as a resource of the Job
    summary = reconciliation["result_summary"]
    assert summary["scope"] == "account"
    assert summary["strategy_id"] is None
    assert summary["blocks_execution"] is False  # the empty 29 Sep-shaped account is clean
    assert [r["kind"] for r in reconciliation["resources"]] == ["account_reconciliation_run"]
    assert set(summary["produced_run_ids"]) == {r["id"] for r in reconciliation["resources"]}
    with session_scope(load_settings()) as session:
        run = session.execute(select(AccountReconciliationRun)).scalar_one()
        assert run.job_id is not None
        assert str(run.job_id) == reconciliation["id"]
        assert run.trigger_source == "job"
        assert run.as_of_session is None  # as_of_session was omitted and is recorded null

    # sync: basis traceability keys, no run produced, no position written
    sync_summary = sync["result_summary"]
    assert sync_summary["scope"] == "account"
    assert sync_summary["produced_run_ids"] == []
    assert sync["resources"] == []
    assert sync_summary["snapshot_id"] == sync_summary["account_snapshot_id"]
    assert sync_summary["applied_orders"] == []
    assert sync_summary["positions_opened"] == sync_summary["positions_closed"] == 0

    # D-06 / D-09: nothing submitted, ownership untouched, no position or strategy run created
    assert fakes.execution.submitted_intents == []
    assert _owner_row() == owner_before
    assert _count(StrategyRun) == runs_before
    assert _count(Position) == 0
    assert _count(PaperOrder) == 0


def test_account_jobs_run_with_no_owner(account_env: BrokerFakes) -> None:
    assert _owner_row()[1] is None  # (id, strategy_id, ...): no owner
    owner_before = _owner_row()
    runs_before = _count(StrategyRun)

    with TestClient(create_app()) as client:
        reconciliation, sync = _run_both_account_jobs(client, "acct-noowner")

    _assert_account_results(
        reconciliation, sync, account_env, runs_before=runs_before, owner_before=owner_before
    )


def test_account_jobs_run_while_trading_is_blocked(account_env: BrokerFakes) -> None:
    OperatorControlService(load_settings()).trip_kill_switch(reason="acct-01 blocked test")
    owner_before = _owner_row()
    runs_before = _count(StrategyRun)

    with TestClient(create_app()) as client:
        reconciliation, sync = _run_both_account_jobs(client, "acct-blocked")

    _assert_account_results(
        reconciliation, sync, account_env, runs_before=runs_before, owner_before=owner_before
    )


def test_account_scope_with_a_strategy_id_is_a_typed_422_and_creates_nothing(
    account_env: BrokerFakes,
) -> None:
    with TestClient(create_app()) as client:
        for job_type in ("reconciliation", "broker-order-sync"):
            response = client.post(
                "/api/v1/jobs",
                headers={"Idempotency-Key": f"acct-spoof-{job_type}"},
                json={
                    "job_type": job_type,
                    "payload": {"scope": "account", "strategy_id": "trend_following_daily"},
                },
            )
            assert response.status_code == 422, response.text
            assert "account_scope_forbids_strategy_id" in response.text
    assert _count(AccountReconciliationRun) == 0
