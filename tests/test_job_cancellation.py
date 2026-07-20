"""Phase 17 Job cancellation behavior tests (JOB-06, D-07-D-10)."""

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
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.migrate import build_alembic_config

from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.db.models import (
    Job,
    JobCancellationCause,
    JobDependency,
    JobEvent,
    JobEventType,
    JobFailureReason,
    JobStatus,
)
from trading_platform.db.session import clear_engine_cache, session_scope
from trading_platform.jobs.cancellation import (
    CANCELLATION_GRACE_SECONDS,
    JobNotCancellableError,
    acknowledge_cancellation,
    find_cancellation_timeout_job_ids,
    request_cancellation,
    sweep_cancellation_timeouts,
)
from trading_platform.jobs.dependencies import find_ready_job_ids
from trading_platform.jobs.lifecycle import JobTransitionRequest, apply_job_transition


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
def migrated_job_cancellation_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Reuses the migrated-database fixture pattern from tests/test_db_migrations.py.

    A pytest fixture cannot be imported across test modules without a shared
    conftest.py entry (none exists for `migrated_database`), so this mirrors
    the exact create/upgrade/teardown sequence, matching the precedent set by
    tests/test_job_lifecycle.py's `migrated_job_lifecycle_db`.
    """
    database_name = f"job_cancellation_{uuid.uuid4().hex[:8]}"
    admin_params = _admin_connection_settings()

    try:
        with _connect_admin(admin_params) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f'CREATE DATABASE "{database_name}"')
    except psycopg.Error as exc:  # pragma: no cover - exercised when local Postgres is unavailable
        pytest.fail(
            "PostgreSQL is required for tests/test_job_cancellation.py. "
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


def _seed_job(session: Any, *, status: JobStatus = JobStatus.QUEUED, **overrides: Any) -> Job:
    defaults: dict[str, Any] = {
        "job_type": "phase17_cancellation_probe",
        "payload": {},
        "status": status,
    }
    defaults.update(overrides)
    job = Job(**defaults)
    session.add(job)
    session.flush()
    return job


def _seed_running_job(session: Any, **overrides: Any) -> Job:
    """Seed a QUEUED Job and drive it to RUNNING via the guarded transition path."""
    job = _seed_job(session, status=JobStatus.QUEUED, **overrides)
    apply_job_transition(
        session, job_id=job.id, request=JobTransitionRequest(event_type=JobEventType.CLAIMED)
    )
    return job


def test_cancel_queued_job_transitions_immediately(migrated_job_cancellation_db: str) -> None:
    settings = load_settings()
    with session_scope(settings) as session:
        job = _seed_job(session)
        job_id = job.id

    result = request_cancellation(job_id=job_id, requested_by="operator_1", settings=settings)

    assert result.mode == "immediate"
    assert result.accepted is True
    assert result.status is JobStatus.CANCELLED

    with session_scope(settings) as session:
        persisted = session.get(Job, job_id)
        assert persisted is not None
        assert persisted.status is JobStatus.CANCELLED
        assert persisted.cancellation_cause is JobCancellationCause.OPERATOR_REQUEST
        assert persisted.cancellation_acknowledged_at is not None
        assert persisted.failure_reason is None


def test_cancelled_queued_job_is_never_claimable(migrated_job_cancellation_db: str) -> None:
    settings = load_settings()
    with session_scope(settings) as session:
        job = _seed_job(session)
        job_id = job.id

    request_cancellation(job_id=job_id, requested_by="operator_1", settings=settings)

    with session_scope(settings) as session:
        ready = find_ready_job_ids(session, limit=10)
    assert job_id not in ready


def test_cancel_running_job_persists_request_without_transitioning(
    migrated_job_cancellation_db: str,
) -> None:
    settings = load_settings()
    with session_scope(settings) as session:
        job = _seed_running_job(session)
        job_id = job.id

    result = request_cancellation(
        job_id=job_id, requested_by="operator_1", reason="stop it", settings=settings
    )

    assert result.mode == "cooperative"
    assert result.accepted is True
    assert result.status is JobStatus.RUNNING

    with session_scope(settings) as session:
        job = session.get(Job, job_id)
        assert job is not None
        assert job.status is JobStatus.RUNNING
        assert job.cancellation_requested_at is not None
        assert job.cancellation_requested_by == "operator_1"
        assert job.cancellation_reason == "stop it"

        events = (
            session.execute(
                select(JobEvent).where(
                    JobEvent.job_id == job_id,
                    JobEvent.event_type == JobEventType.CANCELLATION_REQUESTED,
                )
            )
            .scalars()
            .all()
        )
    assert len(events) == 1
    assert events[0].to_status is None


def test_running_job_becomes_cancelled_only_after_acknowledgement(
    migrated_job_cancellation_db: str,
) -> None:
    settings = load_settings()
    with session_scope(settings) as session:
        job = _seed_running_job(session)
        job_id = job.id

    request_cancellation(
        job_id=job_id, requested_by="operator_1", reason="stop it", settings=settings
    )

    with session_scope(settings) as session:
        result = acknowledge_cancellation(session, job_id=job_id)
    assert result.status is JobStatus.CANCELLED

    with session_scope(settings) as session:
        job = session.get(Job, job_id)
        assert job is not None
        assert job.status is JobStatus.CANCELLED
        assert job.cancellation_acknowledged_at is not None

        cancelled_event = (
            session.execute(
                select(JobEvent).where(
                    JobEvent.job_id == job_id,
                    JobEvent.event_type == JobEventType.CANCELLED,
                )
            )
            .scalars()
            .one()
        )
    assert cancelled_event.requested_by == "operator_1"
    assert cancelled_event.reason == "stop it"
    assert cancelled_event.requested_at is not None
    assert cancelled_event.acknowledged_at is not None


def test_acknowledge_without_pending_request_is_rejected(migrated_job_cancellation_db: str) -> None:
    settings = load_settings()
    with session_scope(settings) as session:
        job = _seed_running_job(session)
        job_id = job.id

    with session_scope(settings) as session:
        with pytest.raises(JobNotCancellableError):
            acknowledge_cancellation(session, job_id=job_id)


@pytest.mark.parametrize(
    "terminal_status", [JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED]
)
def test_cancel_terminal_job_is_rejected(
    migrated_job_cancellation_db: str, terminal_status: JobStatus
) -> None:
    settings = load_settings()
    with session_scope(settings) as session:
        job = _seed_job(session, status=terminal_status)
        job_id = job.id

    with pytest.raises(JobNotCancellableError):
        request_cancellation(job_id=job_id, requested_by="operator_1", settings=settings)


def test_second_cancellation_request_does_not_overwrite_first_requester(
    migrated_job_cancellation_db: str,
) -> None:
    settings = load_settings()
    with session_scope(settings) as session:
        job = _seed_running_job(session)
        job_id = job.id

    first_result = request_cancellation(job_id=job_id, requested_by="operator_1", settings=settings)
    assert first_result.accepted is True

    second_result = request_cancellation(
        job_id=job_id, requested_by="operator_2", settings=settings
    )
    assert second_result.accepted is False

    with session_scope(settings) as session:
        job = session.get(Job, job_id)
        assert job is not None
        assert job.cancellation_requested_by == "operator_1"


def test_grace_period_overrun_fails_with_cancellation_timeout(
    migrated_job_cancellation_db: str,
) -> None:
    settings = load_settings()
    with session_scope(settings) as session:
        job = _seed_running_job(session)
        job_id = job.id
        job.cancellation_requested_at = datetime.now(UTC) - timedelta(
            seconds=CANCELLATION_GRACE_SECONDS + 60
        )
        job.cancellation_requested_by = "operator_1"
        job.cancellation_reason = "stop it"

    with session_scope(settings) as session:
        swept = sweep_cancellation_timeouts(session)
    assert swept == [job_id]

    with session_scope(settings) as session:
        job = session.get(Job, job_id)
        assert job is not None
        assert job.status is not JobStatus.CANCELLED
        assert job.status is JobStatus.FAILED
        assert job.failure_reason is JobFailureReason.CANCELLATION_TIMEOUT
        assert job.outcome_uncertain is True


def test_sweep_ignores_jobs_within_grace_period(migrated_job_cancellation_db: str) -> None:
    settings = load_settings()
    with session_scope(settings) as session:
        job = _seed_running_job(session)
        job_id = job.id
        job.cancellation_requested_at = datetime.now(UTC) - timedelta(seconds=1)
        job.cancellation_requested_by = "operator_1"

    with session_scope(settings) as session:
        swept = sweep_cancellation_timeouts(session)
    assert swept == []

    with session_scope(settings) as session:
        job = session.get(Job, job_id)
        assert job is not None
        assert job.status is JobStatus.RUNNING


def test_sweep_ignores_acknowledged_jobs(migrated_job_cancellation_db: str) -> None:
    settings = load_settings()
    with session_scope(settings) as session:
        job = _seed_running_job(session)
        job_id = job.id
        old = datetime.now(UTC) - timedelta(seconds=CANCELLATION_GRACE_SECONDS + 60)
        job.cancellation_requested_at = old
        job.cancellation_requested_by = "operator_1"
        job.cancellation_acknowledged_at = old + timedelta(seconds=1)

    with session_scope(settings) as session:
        swept = sweep_cancellation_timeouts(session)
    assert swept == []

    with session_scope(settings) as session:
        job = session.get(Job, job_id)
        assert job is not None
        assert job.status is JobStatus.RUNNING


def test_sweep_is_idempotent(migrated_job_cancellation_db: str) -> None:
    settings = load_settings()
    with session_scope(settings) as session:
        job = _seed_running_job(session)
        job_id = job.id
        job.cancellation_requested_at = datetime.now(UTC) - timedelta(
            seconds=CANCELLATION_GRACE_SECONDS + 60
        )
        job.cancellation_requested_by = "operator_1"

    with session_scope(settings) as session:
        first_sweep = sweep_cancellation_timeouts(session)
    assert first_sweep == [job_id]

    with session_scope(settings) as session:
        second_sweep = sweep_cancellation_timeouts(session)
    assert second_sweep == []

    with session_scope(settings) as session:
        timeout_events = (
            session.execute(
                select(JobEvent).where(
                    JobEvent.job_id == job_id,
                    JobEvent.event_type == JobEventType.CANCELLATION_TIMEOUT,
                )
            )
            .scalars()
            .all()
        )
    assert len(timeout_events) == 1


def test_timeout_path_preserves_last_progress(migrated_job_cancellation_db: str) -> None:
    settings = load_settings()
    with session_scope(settings) as session:
        job = _seed_running_job(session)
        job_id = job.id
        job.progress_percent = 60
        job.cancellation_requested_at = datetime.now(UTC) - timedelta(
            seconds=CANCELLATION_GRACE_SECONDS + 60
        )
        job.cancellation_requested_by = "operator_1"

    with session_scope(settings) as session:
        sweep_cancellation_timeouts(session)

    with session_scope(settings) as session:
        job = session.get(Job, job_id)
        assert job is not None
        assert job.progress_percent == 60


def test_find_cancellation_timeout_job_ids_detects_only_pending_past_cutoff(
    migrated_job_cancellation_db: str,
) -> None:
    settings = load_settings()
    with session_scope(settings) as session:
        job = _seed_running_job(session)
        job_id = job.id
        job.cancellation_requested_at = datetime.now(UTC) - timedelta(
            seconds=CANCELLATION_GRACE_SECONDS + 60
        )
        job.cancellation_requested_by = "operator_1"

    with session_scope(settings) as session:
        found = find_cancellation_timeout_job_ids(session)
    assert found == [job_id]


def _seed_dependent(session: Any, *, depends_on: uuid.UUID, **overrides: Any) -> Job:
    """Seed a QUEUED Job with a JobDependency edge onto ``depends_on``."""
    dependent = _seed_job(session, status=JobStatus.QUEUED, **overrides)
    session.add(JobDependency(job_id=dependent.id, depends_on_job_id=depends_on))
    session.flush()
    return dependent


def test_timeout_sweep_cascades_to_unstarted_dependent(
    migrated_job_cancellation_db: str,
) -> None:
    """CR-01 regression (timeout path): a QUEUED dependent of a Job that times
    out its cancellation (RUNNING -> FAILED) must be cascade-CANCELLED rather
    than stranded forever behind the dead dependency (D-04).
    """
    settings = load_settings()
    with session_scope(settings) as session:
        blocker = _seed_running_job(session)
        blocker_id = blocker.id
        blocker.cancellation_requested_at = datetime.now(UTC) - timedelta(
            seconds=CANCELLATION_GRACE_SECONDS + 60
        )
        blocker.cancellation_requested_by = "operator_1"
        dependent = _seed_dependent(session, depends_on=blocker_id)
        dependent_id = dependent.id

    with session_scope(settings) as session:
        swept = sweep_cancellation_timeouts(session)
    assert swept == [blocker_id]

    with session_scope(settings) as session:
        blocker = session.get(Job, blocker_id)
        dependent = session.get(Job, dependent_id)
        assert blocker is not None and blocker.status is JobStatus.FAILED
        assert dependent is not None
        # The dependent is no longer stranded in QUEUED.
        assert dependent.status is JobStatus.CANCELLED
        assert dependent.cancellation_cause is JobCancellationCause.DEPENDENCY_FAILED
        assert dependent.root_cause_job_id == blocker_id

    with session_scope(settings) as session:
        ready = find_ready_job_ids(session, limit=10)
    assert dependent_id not in ready


def test_immediate_cancel_cascades_to_unstarted_dependent(
    migrated_job_cancellation_db: str,
) -> None:
    """CR-01 regression (immediate-cancel path): a QUEUED dependent of a QUEUED
    Job that is cancelled immediately must be cascade-CANCELLED rather than
    stranded behind the cancelled dependency (D-04).
    """
    settings = load_settings()
    with session_scope(settings) as session:
        blocker = _seed_job(session)
        blocker_id = blocker.id
        dependent = _seed_dependent(session, depends_on=blocker_id)
        dependent_id = dependent.id

    result = request_cancellation(job_id=blocker_id, requested_by="operator_1", settings=settings)
    assert result.status is JobStatus.CANCELLED

    with session_scope(settings) as session:
        blocker = session.get(Job, blocker_id)
        dependent = session.get(Job, dependent_id)
        assert blocker is not None and blocker.status is JobStatus.CANCELLED
        assert dependent is not None
        assert dependent.status is JobStatus.CANCELLED
        assert dependent.cancellation_cause is JobCancellationCause.DEPENDENCY_CANCELLED
        assert dependent.root_cause_job_id == blocker_id

    with session_scope(settings) as session:
        ready = find_ready_job_ids(session, limit=10)
    assert dependent_id not in ready
