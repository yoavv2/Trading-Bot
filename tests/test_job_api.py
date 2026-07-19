"""Phase 17 JOB-07 API observability tests -- the HTTP surface over JobReadService.

Every JOB-07 read path (lifecycle, terminal outcome, causal chain, progress,
logs, and audit trail) is exercised end-to-end through the HTTP surface,
including during-execution progress, deterministic log ordering, safe
cursor pagination, and the read-only scope fence (D-15).
"""

from __future__ import annotations

import os
import sys
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

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
    JobCancellationCause,
    JobDependency,
    JobEvent,
    JobEventType,
    JobFailureReason,
    JobLog,
    JobStatus,
    JobTransitionOutcome,
)
from trading_platform.db.session import clear_engine_cache, session_scope  # noqa: E402


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
def migrated_job_api_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Isolated migrated Postgres database, mirroring the exact
    create/upgrade/teardown sequence established by
    tests/test_job_context.py's `migrated_job_context_db` (no shared
    conftest.py fixture exists for this shape yet)."""

    database_name = f"job_api_{uuid.uuid4().hex[:8]}"
    admin_params = _admin_connection_settings()

    try:
        with _connect_admin(admin_params) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f'CREATE DATABASE "{database_name}"')
    except psycopg.Error as exc:  # pragma: no cover - exercised when local Postgres is unavailable
        pytest.fail(
            "PostgreSQL is required for tests/test_job_api.py. "
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


def _seed_job(session: Any, *, status: JobStatus = JobStatus.QUEUED, **overrides: Any) -> Job:
    defaults: dict[str, Any] = {
        "job_type": "phase17_api_probe",
        "payload": {},
        "status": status,
    }
    defaults.update(overrides)
    job = Job(**defaults)
    session.add(job)
    session.flush()
    return job


def test_list_jobs_returns_seeded_jobs_newest_first(migrated_job_api_db: str) -> None:
    settings = load_settings()
    base = datetime.now(UTC)

    with session_scope(settings) as session:
        oldest = _seed_job(session, queued_at=base)
        middle = _seed_job(session, queued_at=base + timedelta(seconds=1))
        newest = _seed_job(session, queued_at=base + timedelta(seconds=2))
        oldest_id, middle_id, newest_id = str(oldest.id), str(middle.id), str(newest.id)

    with _build_client() as client:
        response = client.get("/api/v1/jobs")

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"filters", "count", "items"}
    ids = [item["id"] for item in body["items"]]
    assert ids == [newest_id, middle_id, oldest_id]


def test_list_jobs_filters_by_status_and_job_type(migrated_job_api_db: str) -> None:
    settings = load_settings()

    with session_scope(settings) as session:
        target = _seed_job(session, job_type="type_a", status=JobStatus.QUEUED)
        _seed_job(session, job_type="type_b", status=JobStatus.RUNNING)
        _seed_job(session, job_type="type_a", status=JobStatus.SUCCEEDED)
        target_id = str(target.id)

    with _build_client() as client:
        response = client.get("/api/v1/jobs", params={"status": "queued", "job_type": "type_a"})

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["items"][0]["id"] == target_id


def test_list_jobs_rejects_out_of_enum_status(migrated_job_api_db: str) -> None:
    with _build_client() as client:
        response = client.get("/api/v1/jobs", params={"status": "bogus"})

    assert response.status_code == 422


def test_list_jobs_caps_limit(migrated_job_api_db: str) -> None:
    settings = load_settings()
    base = datetime.now(UTC)

    with session_scope(settings) as session:
        for i in range(105):
            _seed_job(session, queued_at=base + timedelta(seconds=i))

    with _build_client() as client:
        oversized = client.get("/api/v1/jobs", params={"limit": 500})
        capped = client.get("/api/v1/jobs", params={"limit": 100})

    assert oversized.status_code == 422
    assert capped.status_code == 200
    assert len(capped.json()["items"]) <= 100


def test_job_detail_returns_full_lifecycle_and_outcome(migrated_job_api_db: str) -> None:
    settings = load_settings()

    with session_scope(settings) as session:
        job = _seed_job(
            session,
            status=JobStatus.FAILED,
            failure_reason=JobFailureReason.WORKER_LOST,
            failure_message="lost during heartbeat",
            outcome_uncertain=True,
            started_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
        )
        job_id = str(job.id)

    with _build_client() as client:
        response = client.get(f"/api/v1/jobs/{job_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["failure_reason"] == "worker_lost"
    assert body["failure_message"] == "lost during heartbeat"
    assert body["outcome_uncertain"] is True


def test_job_detail_exposes_dependency_causal_chain(migrated_job_api_db: str) -> None:
    settings = load_settings()

    with session_scope(settings) as session:
        root_job = _seed_job(session, status=JobStatus.FAILED)
        blocking_job = _seed_job(session, status=JobStatus.CANCELLED)
        cancelled_job = _seed_job(
            session,
            status=JobStatus.CANCELLED,
            cancellation_cause=JobCancellationCause.DEPENDENCY_CANCELLED,
            blocking_job_id=blocking_job.id,
            blocking_job_status=JobStatus.CANCELLED,
            root_cause_job_id=root_job.id,
        )
        cancelled_job_id = str(cancelled_job.id)
        blocking_job_id = str(blocking_job.id)
        root_job_id = str(root_job.id)

    with _build_client() as client:
        response = client.get(f"/api/v1/jobs/{cancelled_job_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["blocking_job_id"] == blocking_job_id
    assert body["blocking_job_status"] == "cancelled"
    assert body["root_cause_job_id"] == root_job_id
    assert body["cancellation_cause"] == "dependency_cancelled"


def test_job_detail_lists_blocking_dependencies_for_queued_job(migrated_job_api_db: str) -> None:
    settings = load_settings()

    with session_scope(settings) as session:
        job = _seed_job(session, status=JobStatus.QUEUED)
        dep_succeeded = _seed_job(session, status=JobStatus.SUCCEEDED)
        dep_running = _seed_job(session, status=JobStatus.RUNNING)
        session.add(JobDependency(job_id=job.id, depends_on_job_id=dep_succeeded.id))
        session.add(JobDependency(job_id=job.id, depends_on_job_id=dep_running.id))
        session.flush()
        job_id = str(job.id)
        dep_running_id = str(dep_running.id)

    with _build_client() as client:
        response = client.get(f"/api/v1/jobs/{job_id}")

    assert response.status_code == 200
    body = response.json()
    assert len(body["dependencies"]) == 2
    assert [d["id"] for d in body["blocking_dependencies"]] == [dep_running_id]


def test_job_detail_exposes_cancellation_audit_record(migrated_job_api_db: str) -> None:
    settings = load_settings()
    requested_at = datetime.now(UTC)
    acknowledged_at = requested_at + timedelta(seconds=5)

    with session_scope(settings) as session:
        job = _seed_job(
            session,
            status=JobStatus.CANCELLED,
            cancellation_requested_by="operator",
            cancellation_reason="manual stop",
            cancellation_requested_at=requested_at,
            cancellation_acknowledged_at=acknowledged_at,
            cancellation_cause=JobCancellationCause.OPERATOR_REQUEST,
        )
        job_id = str(job.id)

    with _build_client() as client:
        response = client.get(f"/api/v1/jobs/{job_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["cancellation_requested_by"] == "operator"
    assert body["cancellation_reason"] == "manual stop"
    assert body["cancellation_requested_at"] is not None
    assert body["cancellation_acknowledged_at"] is not None
    assert body["cancellation_cause"] == "operator_request"


def test_job_detail_unknown_id_returns_404(migrated_job_api_db: str) -> None:
    unknown_id = str(uuid.uuid4())

    with _build_client() as client:
        response = client.get(f"/api/v1/jobs/{unknown_id}")

    assert response.status_code == 404
    body = response.json()
    assert unknown_id in body["detail"]
    assert "detail" in body
    assert response.status_code != 500


def test_job_detail_malformed_id_returns_422(migrated_job_api_db: str) -> None:
    with _build_client() as client:
        response = client.get("/api/v1/jobs/not-a-uuid")

    assert response.status_code == 422


def test_progress_is_readable_while_job_is_running(migrated_job_api_db: str) -> None:
    settings = load_settings()

    with session_scope(settings) as session:
        job = _seed_job(
            session,
            status=JobStatus.RUNNING,
            started_at=datetime.now(UTC),
            progress_percent=40,
            progress_updated_at=datetime.now(UTC),
        )
        job_id = str(job.id)

    with _build_client() as client:
        response = client.get(f"/api/v1/jobs/{job_id}/progress")

    assert response.status_code == 200
    body = response.json()
    assert body["percent"] == 40
    assert body["status"] == "running"


def test_progress_of_failed_job_preserves_last_value(migrated_job_api_db: str) -> None:
    settings = load_settings()

    with session_scope(settings) as session:
        job = _seed_job(
            session,
            status=JobStatus.FAILED,
            failure_reason=JobFailureReason.HANDLER_ERROR,
            progress_percent=60,
            progress_updated_at=datetime.now(UTC),
        )
        job_id = str(job.id)

    with _build_client() as client:
        response = client.get(f"/api/v1/jobs/{job_id}/progress")

    assert response.status_code == 200
    assert response.json()["percent"] == 60


def test_logs_are_returned_in_sequence_order(migrated_job_api_db: str) -> None:
    settings = load_settings()
    frozen_timestamp = datetime.now(UTC)

    with session_scope(settings) as session:
        job = _seed_job(session)
        for sequence, event_code in ((3, "third"), (1, "first"), (2, "second")):
            session.add(
                JobLog(
                    job_id=job.id,
                    sequence=sequence,
                    logged_at=frozen_timestamp,
                    level="info",
                    event_code=event_code,
                    message=event_code,
                    handler_type="phase17_api_probe",
                    context={},
                )
            )
        session.flush()
        job_id = str(job.id)

    with _build_client() as client:
        response = client.get(f"/api/v1/jobs/{job_id}/logs")

    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["sequence"] for item in items] == [1, 2, 3]
    assert [item["event_code"] for item in items] == ["first", "second", "third"]


def test_logs_cursor_pagination_does_not_skip_or_duplicate(migrated_job_api_db: str) -> None:
    settings = load_settings()
    base = datetime.now(UTC)

    with session_scope(settings) as session:
        job = _seed_job(session)
        for sequence in range(1, 6):
            session.add(
                JobLog(
                    job_id=job.id,
                    sequence=sequence,
                    logged_at=base + timedelta(seconds=sequence),
                    level="info",
                    event_code=f"step_{sequence}",
                    message=f"step {sequence}",
                    handler_type="phase17_api_probe",
                    context={},
                )
            )
        session.flush()
        job_id = str(job.id)

    with _build_client() as client:
        page_1 = client.get(f"/api/v1/jobs/{job_id}/logs", params={"limit": 2})
        assert page_1.status_code == 200
        body_1 = page_1.json()
        assert [item["sequence"] for item in body_1["items"]] == [1, 2]
        assert body_1["next_after_sequence"] == 2
        assert body_1["has_more"] is True

        page_2 = client.get(
            f"/api/v1/jobs/{job_id}/logs",
            params={"after_sequence": body_1["next_after_sequence"], "limit": 2},
        )
        assert page_2.status_code == 200
        body_2 = page_2.json()
        assert [item["sequence"] for item in body_2["items"]] == [3, 4]
        assert body_2["next_after_sequence"] == 4
        assert body_2["has_more"] is True

        page_3 = client.get(
            f"/api/v1/jobs/{job_id}/logs",
            params={"after_sequence": body_2["next_after_sequence"], "limit": 2},
        )
        assert page_3.status_code == 200
        body_3 = page_3.json()
        assert [item["sequence"] for item in body_3["items"]] == [5]
        assert body_3["next_after_sequence"] == 5
        assert body_3["has_more"] is False

    concatenated = (
        [item["sequence"] for item in body_1["items"]]
        + [item["sequence"] for item in body_2["items"]]
        + [item["sequence"] for item in body_3["items"]]
    )
    assert concatenated == [1, 2, 3, 4, 5]


def test_logs_reject_oversized_limit(migrated_job_api_db: str) -> None:
    settings = load_settings()

    with session_scope(settings) as session:
        job = _seed_job(session)
        job_id = str(job.id)

    with _build_client() as client:
        response = client.get(f"/api/v1/jobs/{job_id}/logs", params={"limit": 5000})

    assert response.status_code == 422


def test_logs_for_existing_job_with_no_logs_returns_empty_200(migrated_job_api_db: str) -> None:
    settings = load_settings()

    with session_scope(settings) as session:
        job = _seed_job(session)
        job_id = str(job.id)

    with _build_client() as client:
        response = client.get(f"/api/v1/jobs/{job_id}/logs")

    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["count"] == 0


def test_logs_unknown_job_returns_404(migrated_job_api_db: str) -> None:
    unknown_id = str(uuid.uuid4())

    with _build_client() as client:
        response = client.get(f"/api/v1/jobs/{unknown_id}/logs")

    assert response.status_code == 404


def test_events_expose_rejected_and_accepted_transitions(migrated_job_api_db: str) -> None:
    settings = load_settings()
    event_at = datetime.now(UTC)

    with session_scope(settings) as session:
        job = _seed_job(session, status=JobStatus.RUNNING)
        session.add(
            JobEvent(
                job_id=job.id,
                from_status=JobStatus.QUEUED,
                to_status=JobStatus.RUNNING,
                event_type=JobEventType.CLAIMED,
                outcome=JobTransitionOutcome.ACCEPTED,
                event_at=event_at,
            )
        )
        session.add(
            JobEvent(
                job_id=job.id,
                from_status=JobStatus.RUNNING,
                to_status=None,
                event_type=JobEventType.CLAIMED,
                outcome=JobTransitionOutcome.REJECTED,
                event_at=event_at + timedelta(seconds=1),
                details={"attempted_transition": {"from_status": "running"}},
            )
        )
        session.flush()
        job_id = str(job.id)

    with _build_client() as client:
        response = client.get(f"/api/v1/jobs/{job_id}/events")

    assert response.status_code == 200
    outcomes = [item["outcome"] for item in response.json()["items"]]
    assert "accepted" in outcomes
    assert "rejected" in outcomes


def test_jobs_router_exposes_no_mutating_verbs() -> None:
    app = create_app()
    for route in app.routes:
        path = str(getattr(route, "path", ""))
        if path.startswith("/api/v1/jobs"):
            methods = set(getattr(route, "methods", set()))
            assert methods <= {"GET", "HEAD"}, (path, methods)
