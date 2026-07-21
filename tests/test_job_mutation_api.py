"""HTTP contract tests for idempotent Job submission and cancellation."""

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
from trading_platform.db.models import Job, JobEvent, JobMutation, JobStatus  # noqa: E402
from trading_platform.db.session import clear_engine_cache, session_scope  # noqa: E402
from trading_platform.jobs.contracts import JobContext  # noqa: E402
from trading_platform.jobs.registry import (  # noqa: E402
    InvalidJobPayloadError,
    JobRegistry,
)


class _ProbeHandler:
    job_type = "phase18_e2e_probe"

    def run(self, context: JobContext) -> Mapping[str, Any]:
        return {"message": "done"}


class _OtherProbeHandler:
    job_type = "phase18_other_probe"

    def run(self, context: JobContext) -> Mapping[str, Any]:
        return {"message": "done"}


class _ProbeSubmissionSpec:
    def __init__(self, job_type: str) -> None:
        self.job_type = job_type

    def validate_payload(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        if payload != {"message": "hello"}:
            raise InvalidJobPayloadError(job_type=self.job_type, reason="message must be hello")
        return {"message": "hello"}


def _registry() -> JobRegistry:
    registry = JobRegistry()
    registry.register(_ProbeHandler(), submission_spec=_ProbeSubmissionSpec(_ProbeHandler.job_type))
    registry.register(
        _OtherProbeHandler(),
        submission_spec=_ProbeSubmissionSpec(_OtherProbeHandler.job_type),
    )
    return registry


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
def migrated_job_mutation_api_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    database_name = f"job_mutation_api_{uuid.uuid4().hex[:8]}"
    admin_params = _admin_connection_settings()
    try:
        with _connect_admin(admin_params) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f'CREATE DATABASE "{database_name}"')
    except psycopg.Error as exc:  # pragma: no cover
        pytest.fail(f"PostgreSQL is required for job mutation API tests: {exc}")

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


@pytest.fixture()
def client(migrated_job_mutation_api_db: str) -> Iterator[TestClient]:
    clear_settings_cache()
    app = create_app()
    app.state.job_registry = _registry()
    with TestClient(app) as test_client:
        yield test_client


def _counts() -> tuple[int, int, int]:
    with session_scope(load_settings()) as session:
        return (
            session.scalar(select(func.count()).select_from(Job)) or 0,
            session.scalar(select(func.count()).select_from(JobMutation)) or 0,
            session.scalar(select(func.count()).select_from(JobEvent)) or 0,
        )


def _seed_job(*, status: JobStatus) -> Job:
    with session_scope(load_settings()) as session:
        job = Job(job_type=_ProbeHandler.job_type, payload={"message": "hello"}, status=status)
        session.add(job)
        session.flush()
        session.expunge(job)
        return job


def _submit(client: TestClient, *, key: str | None, payload: dict[str, object] | None = None) -> Any:
    headers = {"Idempotency-Key": key} if key is not None else {}
    return client.post(
        "/api/v1/jobs",
        headers=headers,
        json={"job_type": _ProbeHandler.job_type, "payload": payload or {"message": "hello"}},
    )


def _assert_compact_reference(body: dict[str, object], *, job_id: str, status_value: str) -> None:
    assert set(body) == {"job_id", "job_type", "status", "links"}
    assert body["job_id"] == job_id
    assert body["job_type"] == _ProbeHandler.job_type
    assert body["status"] == status_value
    assert body["links"] == {
        "self": f"/api/v1/jobs/{job_id}",
        "progress": f"/api/v1/jobs/{job_id}/progress",
        "logs": f"/api/v1/jobs/{job_id}/logs",
        "events": f"/api/v1/jobs/{job_id}/events",
    }
    assert all(link.startswith("/api/v1/jobs/") for link in body["links"].values())  # type: ignore[union-attr]


def test_submit_accepts_new_job_and_replays_exact_request(client: TestClient) -> None:
    created = _submit(client, key="submit-key")

    assert created.status_code == 202
    created_body = created.json()
    job_id = created_body["job_id"]
    _assert_compact_reference(created_body, job_id=job_id, status_value="queued")
    assert _counts() == (1, 1, 1)

    replayed = _submit(client, key="submit-key")

    assert replayed.status_code == 200
    assert replayed.headers["Idempotency-Replayed"] == "true"
    assert replayed.json() == created_body
    assert _counts() == (1, 1, 1)


def test_submit_conflicts_for_changed_registered_type(client: TestClient) -> None:
    created = _submit(client, key="conflict-key")
    job_id = created.json()["job_id"]
    before = _counts()

    changed_type = client.post(
        "/api/v1/jobs",
        headers={"Idempotency-Key": "conflict-key"},
        json={"job_type": _OtherProbeHandler.job_type, "payload": {"message": "hello"}},
    )

    assert changed_type.status_code == 409
    assert changed_type.json()["detail"] == {
        "code": "idempotency_key_conflict",
        "original_job_id": job_id,
    }
    assert _counts() == before


def test_submit_rejections_are_typed_and_write_nothing(client: TestClient) -> None:
    for key, expected_code in (
        (None, "missing_idempotency_key"),
        (" ", "invalid_idempotency_key"),
        ("x" * 256, "invalid_idempotency_key"),
    ):
        response = _submit(client, key=key)
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == expected_code
        assert _counts() == (0, 0, 0)

    unknown = client.post(
        "/api/v1/jobs",
        headers={"Idempotency-Key": "unknown-key"},
        json={"job_type": "not_registered", "payload": {"message": "hello"}},
    )
    assert unknown.status_code == 422
    assert unknown.json()["detail"] == {"code": "unknown_job_type", "job_type": "not_registered"}
    assert _counts() == (0, 0, 0)

    invalid_payload = _submit(client, key="invalid-payload", payload={"message": "goodbye"})
    assert invalid_payload.status_code == 422
    assert invalid_payload.json()["detail"] == {
        "code": "invalid_job_payload",
        "job_type": _ProbeHandler.job_type,
    }
    assert _counts() == (0, 0, 0)


def test_submit_persists_the_spec_normalized_payload(client: TestClient) -> None:
    response = _submit(client, key="normalized-payload")

    assert response.status_code == 202
    with session_scope(load_settings()) as session:
        job = session.get(Job, uuid.UUID(response.json()["job_id"]))
        assert job is not None
        assert job.payload == {"message": "hello"}


def test_cancel_handles_queued_running_and_cancelled_repeats(client: TestClient) -> None:
    queued = _seed_job(status=JobStatus.QUEUED)
    queued_response = client.post(
        f"/api/v1/jobs/{queued.id}/cancel",
        headers={"Idempotency-Key": "queued-cancel"},
        json={"reason": "  maintenance  "},
    )
    assert queued_response.status_code == 200
    _assert_compact_reference(queued_response.json(), job_id=str(queued.id), status_value="cancelled")

    running = _seed_job(status=JobStatus.RUNNING)
    running_response = client.post(
        f"/api/v1/jobs/{running.id}/cancel",
        headers={"Idempotency-Key": "running-cancel"},
        json={"reason": "stop now"},
    )
    assert running_response.status_code == 200
    _assert_compact_reference(running_response.json(), job_id=str(running.id), status_value="running")

    repeated = client.post(
        f"/api/v1/jobs/{queued.id}/cancel",
        headers={"Idempotency-Key": "fresh-repeat"},
        json={"reason": "replacement"},
    )
    assert repeated.status_code == 200
    _assert_compact_reference(repeated.json(), job_id=str(queued.id), status_value="cancelled")
    with session_scope(load_settings()) as session:
        persisted = session.get(Job, queued.id)
        assert persisted is not None
        assert persisted.cancellation_reason == "maintenance"
        assert session.scalar(select(func.count()).select_from(JobEvent)) == 2


def test_cancel_normalizes_or_rejects_reasons_without_unexpected_writes(client: TestClient) -> None:
    blank_reason = _seed_job(status=JobStatus.QUEUED)
    blank_response = client.post(
        f"/api/v1/jobs/{blank_reason.id}/cancel",
        headers={"Idempotency-Key": "blank-reason"},
        json={"reason": "   "},
    )
    assert blank_response.status_code == 200
    with session_scope(load_settings()) as session:
        persisted = session.get(Job, blank_reason.id)
        assert persisted is not None
        assert persisted.cancellation_reason is None

    too_long = _seed_job(status=JobStatus.QUEUED)
    before = _counts()
    too_long_response = client.post(
        f"/api/v1/jobs/{too_long.id}/cancel",
        headers={"Idempotency-Key": "too-long"},
        json={"reason": "x" * 501},
    )
    assert too_long_response.status_code == 422
    assert too_long_response.json()["detail"] == {"code": "invalid_cancellation_reason"}
    assert _counts() == before


def test_cancel_rejections_and_endpoint_scoped_keys_preserve_row_counts(client: TestClient) -> None:
    missing = uuid.uuid4()
    missing_response = client.post(
        f"/api/v1/jobs/{missing}/cancel",
        headers={"Idempotency-Key": "missing-job"},
        json={},
    )
    assert missing_response.status_code == 404
    assert missing_response.json()["detail"] == {"code": "job_not_found", "job_id": str(missing)}
    assert _counts() == (0, 0, 0)

    for terminal_status in (JobStatus.SUCCEEDED, JobStatus.FAILED):
        terminal = _seed_job(status=terminal_status)
        before = _counts()
        response = client.post(
            f"/api/v1/jobs/{terminal.id}/cancel",
            headers={"Idempotency-Key": f"terminal-{terminal_status.value}"},
            json={},
        )
        assert response.status_code == 409
        assert response.json()["detail"] == {
            "code": "job_not_cancellable",
            "job_id": str(terminal.id),
            "status": terminal_status.value,
        }
        assert _counts() == before

    submitted = _submit(client, key="shared-key")
    cancel = client.post(
        f"/api/v1/jobs/{submitted.json()['job_id']}/cancel",
        headers={"Idempotency-Key": "shared-key"},
        json={},
    )
    assert cancel.status_code == 200
    assert _counts()[1] == 2
