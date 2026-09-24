"""D-04/D-05 tests: closed JobResourceKind and resources[] on Job detail.

resources[] is derived at read time solely from strategy_runs.job_id -- it
must be visible while the Job is RUNNING and survive every terminal Job
state (SUCCEEDED, FAILED, CANCELLED) as long as a run is linked, and it must
never rewrite or infer the linked run's own status (D-13 read side).

Uses its own isolated migrated Postgres database, mirroring the exact
create/upgrade/teardown sequence established by tests/test_job_api.py's
`migrated_job_api_db` (no shared conftest.py fixture exists for this shape).
"""

from __future__ import annotations

import os
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from alembic import command
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.migrate import build_alembic_config  # noqa: E402

from trading_platform.api.app import create_app  # noqa: E402
from trading_platform.core.settings import clear_settings_cache, load_settings  # noqa: E402
from trading_platform.db.models import (  # noqa: E402
    Job,
    JobStatus,
    Strategy,
    StrategyRun,
    StrategyRunStatus,
    StrategyRunType,
)
from trading_platform.db.session import clear_engine_cache, session_scope  # noqa: E402
from trading_platform.services.job_reads import JobReadService, JobResourceKind  # noqa: E402


def _admin_connection_settings() -> dict[str, str]:
    return {
        "host": os.getenv("TRADING_PLATFORM_DATABASE__HOST", "localhost"),
        "port": os.getenv("TRADING_PLATFORM_DATABASE__PORT", "5432"),
        "user": os.getenv("TRADING_PLATFORM_DATABASE__USER", "trading_platform"),
        "password": os.getenv("TRADING_PLATFORM_DATABASE__PASSWORD", "trading_platform"),
        "dbname": os.getenv("TRADING_PLATFORM_ADMIN_DB", "postgres"),
    }


def _connect_admin(params: dict[str, str] | None = None) -> psycopg.Connection:
    params = params or _admin_connection_settings()
    return psycopg.connect(
        host=params["host"],
        port=params["port"],
        user=params["user"],
        password=params["password"],
        dbname=params["dbname"],
        autocommit=True,
    )


def _set_database_env(monkeypatch: pytest.MonkeyPatch, database_name: str) -> None:
    params = _admin_connection_settings()
    monkeypatch.setenv("TRADING_PLATFORM_DATABASE__HOST", params["host"])
    monkeypatch.setenv("TRADING_PLATFORM_DATABASE__PORT", params["port"])
    monkeypatch.setenv("TRADING_PLATFORM_DATABASE__USER", params["user"])
    monkeypatch.setenv("TRADING_PLATFORM_DATABASE__PASSWORD", params["password"])
    monkeypatch.setenv("TRADING_PLATFORM_DATABASE__NAME", database_name)


@pytest.fixture()
def migrated_job_resources_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    database_name = f"job_resources_{uuid.uuid4().hex[:8]}"
    admin_params = _admin_connection_settings()

    try:
        with _connect_admin(admin_params) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f'CREATE DATABASE "{database_name}"')
    except psycopg.Error as exc:  # pragma: no cover - exercised when local Postgres is unavailable
        pytest.fail(
            "PostgreSQL is required for tests/test_job_resources_read.py. "
            "Start the local db service first (for example `docker compose up -d db`). "
            f"Connection error: {exc}"
        )

    _set_database_env(monkeypatch, database_name)
    clear_settings_cache()
    clear_engine_cache()
    command.upgrade(build_alembic_config(), "head")

    try:
        yield database_name
    finally:
        clear_settings_cache()
        clear_engine_cache()
        with _connect_admin(admin_params) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT pg_terminate_backend(pid)
                    FROM pg_stat_activity
                    WHERE datname = %s
                      AND usename = current_user
                      AND pid <> pg_backend_pid()
                    """,
                    (database_name,),
                )
                cursor.execute(f'DROP DATABASE IF EXISTS "{database_name}"')


def _build_client() -> TestClient:
    clear_settings_cache()
    return TestClient(create_app())


def _seed_job(session, *, status: JobStatus = JobStatus.QUEUED, **overrides) -> Job:
    defaults: dict[str, object] = {
        "job_type": "backtest",
        "payload": {},
        "status": status,
    }
    defaults.update(overrides)
    job = Job(**defaults)
    session.add(job)
    session.flush()
    return job


def _seed_strategy(session) -> Strategy:
    strategy = Strategy(
        strategy_id=f"strat_{uuid.uuid4().hex[:8]}",
        display_name="Job Resources Test Strategy",
        config_reference="job_resources_test.yaml",
    )
    session.add(strategy)
    session.flush()
    return strategy


def _seed_linked_run(session, *, job: Job, status: StrategyRunStatus) -> StrategyRun:
    strategy = _seed_strategy(session)
    strategy_run = StrategyRun(
        strategy_id=strategy.id,
        job_id=job.id,
        run_type=StrategyRunType.BACKTEST,
        status=status,
        trigger_source="job",
    )
    session.add(strategy_run)
    session.flush()
    return strategy_run


def test_resource_kind_enum_is_closed() -> None:
    assert {member.value for member in JobResourceKind} == {"strategy_run"}


def test_detail_resources_empty_without_linked_run(migrated_job_resources_db: str) -> None:
    settings = load_settings()

    with session_scope(settings) as session:
        job = _seed_job(session, status=JobStatus.QUEUED)
        job_id = str(job.id)

    detail = JobReadService(settings).get_job_detail(job_id)

    assert detail["resources"] == []


def test_detail_resources_visible_while_running(migrated_job_resources_db: str) -> None:
    settings = load_settings()

    with session_scope(settings) as session:
        job = _seed_job(session, status=JobStatus.RUNNING)
        run = _seed_linked_run(session, job=job, status=StrategyRunStatus.RUNNING)
        job_id = str(job.id)
        run_id = str(run.id)

    detail = JobReadService(settings).get_job_detail(job_id)

    assert detail["resources"] == [
        {
            "kind": "strategy_run",
            "id": run_id,
            "status": "running",
            "links": {"self": f"/api/v1/runs/{run_id}"},
        }
    ]


@pytest.mark.parametrize(
    ("job_status", "run_status"),
    [
        (JobStatus.SUCCEEDED, StrategyRunStatus.SUCCEEDED),
        (JobStatus.FAILED, StrategyRunStatus.FAILED),
        (JobStatus.CANCELLED, StrategyRunStatus.SUCCEEDED),
    ],
    ids=["job-succeeded", "job-failed", "job-cancelled-run-succeeded"],
)
def test_detail_resources_survive_terminal_states(
    migrated_job_resources_db: str,
    job_status: JobStatus,
    run_status: StrategyRunStatus,
) -> None:
    settings = load_settings()

    with session_scope(settings) as session:
        job = _seed_job(session, status=job_status)
        run = _seed_linked_run(session, job=job, status=run_status)
        job_id = str(job.id)
        run_id = str(run.id)

    detail = JobReadService(settings).get_job_detail(job_id)

    # D-13 (read side): no code path rewrites the run's own status, even
    # when the Job itself landed CANCELLED after the run had already
    # completed.
    assert detail["resources"] == [
        {
            "kind": "strategy_run",
            "id": run_id,
            "status": run_status.value,
            "links": {"self": f"/api/v1/runs/{run_id}"},
        }
    ]


def test_job_detail_http_includes_resources(migrated_job_resources_db: str) -> None:
    settings = load_settings()

    with session_scope(settings) as session:
        job = _seed_job(session, status=JobStatus.SUCCEEDED)
        run = _seed_linked_run(session, job=job, status=StrategyRunStatus.SUCCEEDED)
        job_id = str(job.id)
        run_id = str(run.id)

    with _build_client() as client:
        response = client.get(f"/api/v1/jobs/{job_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["resources"] == [
        {
            "kind": "strategy_run",
            "id": run_id,
            "status": "succeeded",
            "links": {"self": f"/api/v1/runs/{run_id}"},
        }
    ]
