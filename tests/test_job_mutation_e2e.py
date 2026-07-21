"""End-to-end proof for the generic Phase 18 Job mutation surface."""

from __future__ import annotations

import os
import sys
import uuid
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import psycopg
import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.migrate import build_alembic_config  # noqa: E402

from trading_platform.api.app import create_app  # noqa: E402
from trading_platform.core.settings import clear_settings_cache, load_settings  # noqa: E402
from trading_platform.db.models import Job, JobEvent, JobMutation  # noqa: E402
from trading_platform.db.session import clear_engine_cache, session_scope  # noqa: E402
from trading_platform.jobs.contracts import JobContext  # noqa: E402
from trading_platform.jobs.registry import (  # noqa: E402
    InvalidJobPayloadError,
    JobRegistry,
    build_default_registry,
)
from trading_platform.jobs.runner import run_worker_loop  # noqa: E402


class _Phase18E2EHandler:
    job_type = "phase18_e2e_probe"

    def __init__(self) -> None:
        self.executions = 0

    def run(self, context: JobContext) -> Mapping[str, Any]:
        self.executions += 1
        context.report_progress(percent=50, step="processing", current=1, total=2)
        context.log(
            level="info",
            event_code="phase18_e2e",
            message="Phase 18 E2E probe completed.",
            context={"message": context.payload["message"]},
        )
        return {"message": context.payload["message"], "executions": self.executions}


class _Phase18E2ESubmissionSpec:
    job_type = "phase18_e2e_probe"

    def validate_payload(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        if payload == {"message": "hello"}:
            return {"message": "hello"}
        if payload == {"message": "goodbye"}:
            raise InvalidJobPayloadError(job_type=self.job_type, reason="message must be hello")
        raise InvalidJobPayloadError(
            job_type=self.job_type, reason="payload must be exactly message=hello"
        )


def _admin_connection_settings() -> dict[str, str]:
    return {
        "host": os.getenv("TRADING_PLATFORM_DATABASE__HOST", "localhost"),
        "port": os.getenv("TRADING_PLATFORM_DATABASE__PORT", "5432"),
        "user": os.getenv("TRADING_PLATFORM_DATABASE__USER", "trading_platform"),
        "password": os.getenv("TRADING_PLATFORM_DATABASE__PASSWORD", "trading_platform"),
        "dbname": os.getenv("TRADING_PLATFORM_ADMIN_DB", "postgres"),
    }


def _connect_admin(params: dict[str, str] | None = None) -> psycopg.Connection:
    return psycopg.connect(**(params or _admin_connection_settings()), autocommit=True)


def _set_database_env(monkeypatch: pytest.MonkeyPatch, database_name: str) -> None:
    for key, value in _admin_connection_settings().items():
        if key != "dbname":
            monkeypatch.setenv(f"TRADING_PLATFORM_DATABASE__{key.upper()}", value)
    monkeypatch.setenv("TRADING_PLATFORM_DATABASE__NAME", database_name)


@pytest.fixture()
def migrated_job_mutation_e2e_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    database_name = f"job_mutation_e2e_{uuid.uuid4().hex[:8]}"
    admin_params = _admin_connection_settings()
    try:
        with _connect_admin(admin_params) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f'CREATE DATABASE "{database_name}"')
    except psycopg.Error as exc:  # pragma: no cover
        pytest.fail(f"PostgreSQL is required for Job mutation E2E tests: {exc}")

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
                    WHERE datname = %s AND usename = current_user AND pid <> pg_backend_pid()
                    """,
                    (database_name,),
                )
                cursor.execute(f'DROP DATABASE IF EXISTS "{database_name}"')


def _counts() -> tuple[int, int, int]:
    with session_scope(load_settings()) as session:
        return (
            session.scalar(select(func.count()).select_from(Job)) or 0,
            session.scalar(select(func.count()).select_from(JobMutation)) or 0,
            session.scalar(select(func.count()).select_from(JobEvent)) or 0,
        )


def test_submit_execute_and_observe_with_test_only_handler(
    migrated_job_mutation_e2e_db: str,
) -> None:
    handler = _Phase18E2EHandler()
    registry = JobRegistry()
    registry.register(handler, submission_spec=_Phase18E2ESubmissionSpec())

    assert build_default_registry().list_job_types() == []
    production_operations = {
        "backtest",
        "risk",
        "paper",
        "reconciliation",
        "market-data",
        "broker-order-lifecycle",
    }
    assert production_operations.isdisjoint(build_default_registry().list_job_types())

    app = create_app(job_registry=registry)
    with TestClient(app) as client:
        rejected = client.post(
            "/api/v1/jobs",
            headers={"Idempotency-Key": "phase18-e2e-invalid"},
            json={"job_type": handler.job_type, "payload": {"message": "goodbye"}},
        )
        assert rejected.status_code == 422
        assert rejected.json()["detail"] == {
            "code": "invalid_job_payload",
            "job_type": handler.job_type,
        }
        assert _counts() == (0, 0, 0)

        submitted = client.post(
            "/api/v1/jobs",
            headers={"Idempotency-Key": "phase18-e2e-valid"},
            json={"job_type": handler.job_type, "payload": {"message": "hello"}},
        )
        assert submitted.status_code == 202
        body = submitted.json()
        assert set(body) == {"job_id", "job_type", "status", "links"}
        assert body["job_type"] == handler.job_type
        assert body["status"] == "queued"
        assert set(body["links"]) == {"self", "progress", "logs", "events"}
        assert all(link.startswith("/api/v1/jobs/") for link in body["links"].values())
        assert _counts() == (1, 1, 1)

        with session_scope(load_settings()) as session:
            job = session.get(Job, uuid.UUID(body["job_id"]))
            assert job is not None
            assert job.payload == {"message": "hello"}

        replay = client.post(
            "/api/v1/jobs",
            headers={"Idempotency-Key": "phase18-e2e-valid"},
            json={"job_type": handler.job_type, "payload": {"message": "hello"}},
        )
        assert replay.status_code == 200
        assert replay.headers["Idempotency-Replayed"] == "true"
        assert replay.json() == body
        assert _counts() == (1, 1, 1)
        assert handler.executions == 0

        report = run_worker_loop(
            worker_id="phase18-e2e-worker",
            registry=registry,
            max_jobs=1,
            settings=load_settings(),
        )
        assert report["jobs_executed"] == 1
        assert report["succeeded"] == 1
        assert handler.executions == 1

        detail = client.get(body["links"]["self"])
        progress = client.get(body["links"]["progress"])
        logs = client.get(body["links"]["logs"])
        events = client.get(body["links"]["events"])

    assert detail.status_code == 200
    assert detail.json()["status"] == "succeeded"
    assert detail.json()["result_summary"] == {"message": "hello", "executions": 1}
    assert progress.status_code == 200
    assert progress.json()["percent"] == 100
    assert logs.status_code == 200
    assert any(item["event_code"] == "phase18_e2e" for item in logs.json()["items"])
    assert events.status_code == 200
    assert {item["event_type"] for item in events.json()["items"]}.issuperset(
        {"submitted", "claimed", "succeeded"}
    )
