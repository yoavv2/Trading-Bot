"""Phase 17 Job lifecycle transition enforcement tests (JOB-01)."""

from __future__ import annotations

import os
import sys
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
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
    JobEvent,
    JobEventType,
    JobFailureReason,
    JobStatus,
    JobTransitionOutcome,
)
from trading_platform.db.session import clear_engine_cache, session_scope
from trading_platform.jobs.lifecycle import (
    _LEGAL_TRANSITIONS,
    IllegalJobTransition,
    JobTransitionRequest,
    apply_job_transition,
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
def migrated_job_lifecycle_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Reuses the migrated-database fixture pattern from tests/test_db_migrations.py.

    A pytest fixture cannot be imported across test modules without a shared
    conftest.py entry (none exists for `migrated_database`), so this mirrors
    the exact create/upgrade/teardown sequence, matching the precedent set by
    tests/test_stale_run_reclaim.py's `migrated_stale_reclaim_db`.
    """
    database_name = f"job_lifecycle_{uuid.uuid4().hex[:8]}"
    admin_params = _admin_connection_settings()

    try:
        with _connect_admin(admin_params) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f'CREATE DATABASE "{database_name}"')
    except psycopg.Error as exc:  # pragma: no cover - exercised when local Postgres is unavailable
        pytest.fail(
            "PostgreSQL is required for tests/test_job_lifecycle.py. "
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
        "job_type": "phase17_lifecycle_probe",
        "payload": {},
        "status": status,
    }
    defaults.update(overrides)
    job = Job(**defaults)
    session.add(job)
    session.flush()
    return job


def test_queued_to_running_records_started_at_and_accepted_event(
    migrated_job_lifecycle_db: str,
) -> None:
    settings = load_settings()

    with session_scope(settings) as session:
        job = _seed_job(session, status=JobStatus.QUEUED)
        job_id = job.id

    with session_scope(settings) as session:
        result = apply_job_transition(
            session,
            job_id=job_id,
            request=JobTransitionRequest(event_type=JobEventType.CLAIMED),
        )
        session.commit()

    assert result.to_status is JobStatus.RUNNING

    with session_scope(settings) as session:
        job = session.get(Job, job_id)
        assert job is not None
        assert job.status is JobStatus.RUNNING
        assert job.started_at is not None

        events = session.execute(select(JobEvent).where(JobEvent.job_id == job_id)).scalars().all()
        assert len(events) == 1
        assert events[0].outcome is JobTransitionOutcome.ACCEPTED
        assert events[0].from_status is JobStatus.QUEUED
        assert events[0].to_status is JobStatus.RUNNING


def test_queued_to_cancelled_is_legal_and_terminal(migrated_job_lifecycle_db: str) -> None:
    settings = load_settings()

    with session_scope(settings) as session:
        job = _seed_job(session, status=JobStatus.QUEUED)
        job_id = job.id

    with session_scope(settings) as session:
        result = apply_job_transition(
            session,
            job_id=job_id,
            request=JobTransitionRequest(event_type=JobEventType.CANCELLED),
        )
        session.commit()

    assert result.to_status is JobStatus.CANCELLED

    with session_scope(settings) as session:
        job = session.get(Job, job_id)
        assert job is not None
        assert job.status is JobStatus.CANCELLED

    # D-07: CANCELLED is terminal -- any further transition attempt is illegal.
    with session_scope(settings) as session:
        with pytest.raises(IllegalJobTransition):
            apply_job_transition(
                session,
                job_id=job_id,
                request=JobTransitionRequest(event_type=JobEventType.CLAIMED),
            )
        session.commit()


def test_illegal_transition_raises_and_persists_rejected_event(
    migrated_job_lifecycle_db: str,
) -> None:
    settings = load_settings()

    with session_scope(settings) as session:
        job = _seed_job(session, status=JobStatus.QUEUED)
        job_id = job.id

    with session_scope(settings) as session:
        with pytest.raises(IllegalJobTransition):
            apply_job_transition(
                session,
                job_id=job_id,
                request=JobTransitionRequest(event_type=JobEventType.SUCCEEDED),
            )
        session.commit()

    with session_scope(settings) as session:
        job = session.get(Job, job_id)
        assert job is not None
        assert job.status is JobStatus.QUEUED

        events = session.execute(select(JobEvent).where(JobEvent.job_id == job_id)).scalars().all()
        assert len(events) == 1
        rejected_event = events[0]
        assert rejected_event.outcome is JobTransitionOutcome.REJECTED
        assert rejected_event.to_status is None
        assert rejected_event.from_status is JobStatus.QUEUED


def test_worker_lost_lands_on_failed_with_reason_and_outcome_uncertain(
    migrated_job_lifecycle_db: str,
) -> None:
    settings = load_settings()

    with session_scope(settings) as session:
        job = _seed_job(session, status=JobStatus.RUNNING)
        job_id = job.id

    with session_scope(settings) as session:
        apply_job_transition(
            session,
            job_id=job_id,
            request=JobTransitionRequest(event_type=JobEventType.WORKER_LOST),
        )
        session.commit()

    with session_scope(settings) as session:
        job = session.get(Job, job_id)
        assert job is not None
        assert job.status is JobStatus.FAILED
        assert job.failure_reason == JobFailureReason.WORKER_LOST
        assert job.outcome_uncertain is True
        assert job.cancellation_cause is None


def test_lease_expired_lands_on_failed_not_cancelled(migrated_job_lifecycle_db: str) -> None:
    settings = load_settings()

    with session_scope(settings) as session:
        job = _seed_job(session, status=JobStatus.RUNNING)
        job_id = job.id

    with session_scope(settings) as session:
        apply_job_transition(
            session,
            job_id=job_id,
            request=JobTransitionRequest(event_type=JobEventType.LEASE_EXPIRED),
        )
        session.commit()

    with session_scope(settings) as session:
        job = session.get(Job, job_id)
        assert job is not None
        assert job.status is JobStatus.FAILED
        assert job.status is not JobStatus.CANCELLED
        assert job.failure_reason == JobFailureReason.LEASE_EXPIRED
        assert job.outcome_uncertain is True


def test_cancellation_timeout_lands_on_failed_with_outcome_uncertain(
    migrated_job_lifecycle_db: str,
) -> None:
    settings = load_settings()

    with session_scope(settings) as session:
        job = _seed_job(session, status=JobStatus.RUNNING)
        job_id = job.id

    with session_scope(settings) as session:
        apply_job_transition(
            session,
            job_id=job_id,
            request=JobTransitionRequest(
                event_type=JobEventType.CANCELLATION_TIMEOUT,
                failure_reason=JobFailureReason.CANCELLATION_TIMEOUT,
            ),
        )
        session.commit()

    with session_scope(settings) as session:
        job = session.get(Job, job_id)
        assert job is not None
        assert job.status is JobStatus.FAILED
        assert job.failure_reason == JobFailureReason.CANCELLATION_TIMEOUT
        assert job.outcome_uncertain is True


def test_cancelled_transition_with_failure_reason_is_rejected(
    migrated_job_lifecycle_db: str,
) -> None:
    settings = load_settings()

    with session_scope(settings) as session:
        job = _seed_job(session, status=JobStatus.RUNNING)
        job_id = job.id

    with session_scope(settings) as session:
        with pytest.raises(ValueError):
            apply_job_transition(
                session,
                job_id=job_id,
                request=JobTransitionRequest(
                    event_type=JobEventType.CANCELLED,
                    failure_reason=JobFailureReason.HANDLER_ERROR,
                ),
            )
        session.commit()

    with session_scope(settings) as session:
        job = session.get(Job, job_id)
        assert job is not None
        assert job.status is JobStatus.RUNNING


def test_terminal_transition_preserves_last_progress(migrated_job_lifecycle_db: str) -> None:
    settings = load_settings()
    seeded_progress_updated_at = datetime.now(UTC)

    with session_scope(settings) as session:
        job = _seed_job(
            session,
            status=JobStatus.RUNNING,
            progress_percent=42,
            progress_step="halfway",
            progress_updated_at=seeded_progress_updated_at,
        )
        job_id = job.id

    with session_scope(settings) as session:
        seeded_job = session.get(Job, job_id)
        assert seeded_job is not None
        captured_progress_updated_at = seeded_job.progress_updated_at

    with session_scope(settings) as session:
        apply_job_transition(
            session,
            job_id=job_id,
            request=JobTransitionRequest(
                event_type=JobEventType.FAILED,
                failure_reason=JobFailureReason.HANDLER_ERROR,
            ),
        )
        session.commit()

    with session_scope(settings) as session:
        job = session.get(Job, job_id)
        assert job is not None
        assert job.status is JobStatus.FAILED
        assert job.progress_percent == 42
        assert job.progress_step == "halfway"
        assert job.progress_updated_at == captured_progress_updated_at


def test_transition_table_covers_every_status_key() -> None:
    assert set(_LEGAL_TRANSITIONS) == set(JobStatus)
    assert sum(len(v) for v in _LEGAL_TRANSITIONS.values()) == 8
