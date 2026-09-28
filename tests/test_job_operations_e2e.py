"""OPS-01 production-path E2E: HTTP submit -> JobOrchestrationService ->
registered ``backtest`` handler -> ``run-jobs`` worker command -> the existing
``services.backtesting`` service.

Unlike ``tests/test_job_mutation_e2e.py`` (Phase 18, test-only handler) and
``tests/test_backtest_job_type.py`` (unit-level handler/spec tests), this
module proves the whole vertical slice with the **production** Job registry
(``create_app()`` with no registry override -- the lifespan builds
``build_default_registry(settings)``) and the real ``run-jobs`` CLI command.

Reuses ``tests.test_backtest_runner``'s Postgres fixtures (``migrated_backtest_db``,
``strategy_config_override``, ``_seed_market_data``).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from tests.test_backtest_runner import (
    _seed_market_data,
    migrated_backtest_db,
    strategy_config_override,
)

from trading_platform.api.app import create_app
from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.db.models import Job, JobEvent, JobMutation, StrategyRun, StrategyRunType
from trading_platform.db.session import session_scope
from trading_platform.jobs.handlers import backtest as backtest_handler_module
from trading_platform.services.market_data_access import latest_completed_session
from trading_platform.worker.commands.run_jobs import run_jobs_command
from trading_platform.worker.parser import build_parser

# migrated_backtest_db/strategy_config_override are used as pytest fixture
# names (via job_operations_env's parameter list below), not by expression --
# re-export them explicitly so ruff's unused-import check (F401) passes,
# mirroring tests/test_backtest_job_type.py's precedent.
__all__ = ["migrated_backtest_db", "strategy_config_override"]

BACKTEST_PAYLOAD: dict[str, str] = {
    "strategy_id": "trend_following_daily",
    "from_date": "2024-01-02",
    "to_date": "2024-01-10",
}


@pytest.fixture()
def job_operations_env(
    migrated_backtest_db: str,
    strategy_config_override: None,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """Production-path environment: mutations enabled (D-19 default is
    disabled), no broker credentials configured (D-22 proof -- the worker
    boots and runs a backtest without them), seeded market data."""

    monkeypatch.setenv("TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED", "true")
    monkeypatch.setenv("TRADING_PLATFORM_BROKER__ALPACA__API_KEY", "")
    monkeypatch.setenv("TRADING_PLATFORM_BROKER__ALPACA__API_SECRET", "")
    clear_settings_cache()
    _seed_market_data()
    yield


def _run_worker_once() -> None:
    """Run one real `run-jobs --once` poll pass through the CLI entrypoint."""

    args = build_parser().parse_args(["run-jobs", "--once", "--compact"])
    try:
        run_jobs_command(args)
    except SystemExit as exc:  # pragma: no cover - failure path
        pytest.fail(f"run_jobs_command exited unexpectedly: {exc!r}")


def _counts() -> tuple[int, int, int, int]:
    with session_scope(load_settings()) as session:
        return (
            session.scalar(select(func.count()).select_from(Job)) or 0,
            session.scalar(select(func.count()).select_from(JobMutation)) or 0,
            session.scalar(select(func.count()).select_from(JobEvent)) or 0,
            session.scalar(select(func.count()).select_from(StrategyRun)) or 0,
        )


# --- Task 1: happy path, idempotent replay, catalog, HTTP payload rejection --


def test_backtest_job_runs_through_production_path(job_operations_env: None) -> None:
    """SC1/OPS-01: production registry, full submit -> worker -> observe path."""

    with TestClient(create_app()) as client:
        submitted = client.post(
            "/api/v1/jobs",
            headers={"Idempotency-Key": "e2e-backtest-1"},
            json={"job_type": "backtest", "payload": BACKTEST_PAYLOAD},
        )
        assert submitted.status_code == 202
        body = submitted.json()
        assert body["job_type"] == "backtest"
        assert body["status"] == "queued"
        assert set(body["links"]) == {"self", "progress", "logs", "events"}
        job_id = body["job_id"]

        _run_worker_once()

        detail_resp = client.get(body["links"]["self"])
        progress_resp = client.get(body["links"]["progress"])
        logs_resp = client.get(body["links"]["logs"])
        events_resp = client.get(body["links"]["events"])

        assert detail_resp.status_code == 200
        detail = detail_resp.json()
        assert detail["status"] == "succeeded"
        assert detail["failure_reason"] is None

        assert progress_resp.status_code == 200
        assert progress_resp.json()["percent"] == 100

        # D-04/D-06: resources[] is the authoritative link to the run.
        resources = detail["resources"]
        assert len(resources) == 1
        run_resource = resources[0]
        assert run_resource["kind"] == "strategy_run"
        assert run_resource["status"] == "succeeded"
        assert run_resource["links"]["self"] == f"/api/v1/runs/{run_resource['id']}"
        assert detail["result_summary"]["run_id"] == run_resource["id"]

        assert logs_resp.status_code == 200
        event_codes = {item["event_code"] for item in logs_resp.json()["items"]}
        assert "backtest_run_started" in event_codes
        assert "backtest_run_completed" in event_codes

        assert events_resp.status_code == 200
        event_types = {item["event_type"] for item in events_resp.json()["items"]}
        assert "submitted" in event_types
        assert "succeeded" in event_types

        # D-07/D-11: run-detail back-link to the originating Job.
        run_detail_resp = client.get(f"/api/v1/runs/{run_resource['id']}")

    assert run_detail_resp.status_code == 200
    run_detail = run_detail_resp.json()["run"]
    assert run_detail["job_id"] == job_id
    assert run_detail["trigger_source"] == "job"


def test_idempotent_resubmission_creates_one_run(job_operations_env: None) -> None:
    """SC2: replaying the same Idempotency-Key never produces a second run."""

    with TestClient(create_app()) as client:
        first = client.post(
            "/api/v1/jobs",
            headers={"Idempotency-Key": "e2e-backtest-idempotent"},
            json={"job_type": "backtest", "payload": BACKTEST_PAYLOAD},
        )
        assert first.status_code == 202
        job_id = first.json()["job_id"]

        _run_worker_once()

        replay = client.post(
            "/api/v1/jobs",
            headers={"Idempotency-Key": "e2e-backtest-idempotent"},
            json={"job_type": "backtest", "payload": BACKTEST_PAYLOAD},
        )
        assert replay.status_code == 200
        assert replay.headers["Idempotency-Replayed"] == "true"
        assert replay.json()["job_id"] == job_id

        _run_worker_once()

    with session_scope(load_settings()) as session:
        runs_for_job = session.scalar(
            select(func.count())
            .select_from(StrategyRun)
            .where(StrategyRun.job_id == uuid.UUID(job_id))
        )
        total_backtest_runs = session.scalar(
            select(func.count())
            .select_from(StrategyRun)
            .where(StrategyRun.run_type == StrategyRunType.BACKTEST)
        )

    assert runs_for_job == 1
    assert total_backtest_runs == 1


def test_job_types_catalog_lists_backtest_with_defaults(job_operations_env: None) -> None:
    settings = load_settings()
    with session_scope(settings) as session:
        expected_to_date = latest_completed_session(
            session, exchange=settings.market_data.calendar.exchange
        )
    assert expected_to_date is not None

    with TestClient(create_app()) as client:
        response = client.get("/api/v1/job-types")

    assert response.status_code == 200
    body = response.json()
    assert body["mutations_enabled"] is True
    # Phase 20 (Plan 16) registers seven more job types in the production
    # registry; the 8-item catalog count is pinned by test_job_catalog.py
    # instead of here.
    entry = next(item for item in body["items"] if item["job_type"] == "backtest")
    assert entry["cancellation_mode"] == "step_boundary"
    assert entry["description"]
    assert set(entry["submission_defaults"]) == {"from_date", "to_date"}
    assert entry["submission_defaults"]["to_date"] == expected_to_date.isoformat()


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        (
            {"strategy_id": "nope", "from_date": "2024-01-02", "to_date": "2024-01-10"},
            "unknown_strategy_id",
        ),
        (
            {
                "strategy_id": "trend_following_daily",
                "from_date": "2024-01-10",
                "to_date": "2024-01-02",
            },
            "from_date_after_to_date",
        ),
        (
            {
                "strategy_id": "trend_following_daily",
                "from_date": "2024-01-02",
                "to_date": "2999-01-01",
            },
            "to_date_in_future",
        ),
        (
            {
                "strategy_id": "trend_following_daily",
                "from_date": "2024-01-02",
                "to_date": "2024-01-10",
                "extra_field": "nope",
            },
            "unknown_payload_keys",
        ),
    ],
    ids=[
        "unknown_strategy_id",
        "from_date_after_to_date",
        "to_date_in_future",
        "unknown_payload_keys",
    ],
)
def test_backtest_payload_rejections_write_nothing(
    job_operations_env: None, payload: dict[str, Any], reason: str
) -> None:
    """D-09: each rejection is a typed 422 over HTTP with zero rows written."""

    before = _counts()
    with TestClient(create_app()) as client:
        response = client.post(
            "/api/v1/jobs",
            headers={"Idempotency-Key": f"e2e-reject-{reason}"},
            json={"job_type": "backtest", "payload": payload},
        )
    after = _counts()

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "code": "invalid_job_payload",
        "job_type": "backtest",
        "reason": reason,
    }
    assert after == before


# --- Task 2: queued and running cancellation outcomes (SC7) -----------------


def test_cancel_queued_backtest_never_executes(job_operations_env: None) -> None:
    with TestClient(create_app()) as client:
        submitted = client.post(
            "/api/v1/jobs",
            headers={"Idempotency-Key": "e2e-cancel-queued"},
            json={"job_type": "backtest", "payload": BACKTEST_PAYLOAD},
        )
        assert submitted.status_code == 202
        body = submitted.json()
        job_id = body["job_id"]
        links = body["links"]

        cancel_resp = client.post(
            links["self"] + "/cancel",
            headers={"Idempotency-Key": "cancel-queued"},
            json={"reason": "  operator stop  "},
        )
        assert cancel_resp.status_code == 200
        assert cancel_resp.json()["status"] == "cancelled"

        _run_worker_once()

        detail = client.get(links["self"]).json()
        logs = client.get(links["logs"]).json()

    assert detail["status"] == "cancelled"
    assert detail["cancellation_cause"] == "operator_request"
    assert detail["cancellation_reason"] == "operator stop"
    assert detail["resources"] == []
    assert detail["result_summary"] == {}
    assert logs["items"] == []

    with session_scope(load_settings()) as session:
        linked_runs = session.scalar(
            select(func.count())
            .select_from(StrategyRun)
            .where(StrategyRun.job_id == uuid.UUID(job_id))
        )
    assert linked_runs == 0


def test_cancel_running_backtest_acknowledged_after_service(
    job_operations_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SC7 running / D-12 / D-13: a cancel issued mid-``run_backtest`` lands the
    Job CANCELLED at the post-call boundary while the linked run keeps its
    real (SUCCEEDED) status."""

    real_run_backtest = backtest_handler_module.run_backtest
    job_id_holder: dict[str, str] = {}

    with TestClient(create_app()) as client:

        def _cancel_then_run(
            strategy_id: str,
            *,
            from_date: Any,
            to_date: Any,
            trigger_source: str,
            job_id: Any,
            settings: Any = None,
        ) -> Any:
            assert str(job_id) == job_id_holder["job_id"]
            cancel_resp = client.post(
                f"/api/v1/jobs/{job_id_holder['job_id']}/cancel",
                headers={"Idempotency-Key": "cancel-running"},
                json={"reason": None},
            )
            assert cancel_resp.status_code == 200
            assert cancel_resp.json()["status"] == "running"
            return real_run_backtest(
                strategy_id,
                from_date=from_date,
                to_date=to_date,
                trigger_source=trigger_source,
                job_id=job_id,
                settings=settings,
            )

        monkeypatch.setattr(backtest_handler_module, "run_backtest", _cancel_then_run)

        submitted = client.post(
            "/api/v1/jobs",
            headers={"Idempotency-Key": "e2e-cancel-running"},
            json={"job_type": "backtest", "payload": BACKTEST_PAYLOAD},
        )
        assert submitted.status_code == 202
        job_id_holder["job_id"] = submitted.json()["job_id"]

        _run_worker_once()

        detail = client.get(f"/api/v1/jobs/{job_id_holder['job_id']}").json()

        assert detail["status"] == "cancelled"
        assert detail["cancellation_cause"] == "operator_request"
        assert detail["cancellation_requested_at"] is not None
        assert detail["cancellation_acknowledged_at"] is not None
        assert detail["result_summary"] == {}
        assert len(detail["resources"]) == 1
        run_resource = detail["resources"][0]
        assert run_resource["status"] == "succeeded"

        run_detail = client.get(f"/api/v1/runs/{run_resource['id']}").json()["run"]

    assert run_detail["status"] == "succeeded"
    assert run_detail["job_id"] == job_id_holder["job_id"]
