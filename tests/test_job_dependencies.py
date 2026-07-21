"""Phase 17 Job dependency gating and cascade tests (JOB-05, D-04-D-06)."""

from __future__ import annotations

import os
import sys
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest import mock

import psycopg
import pytest
from alembic import command
from sqlalchemy import func, select
from sqlalchemy.orm import aliased

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
from trading_platform.jobs.dependencies import (
    DependencyCycleError,
    SelfDependencyError,
    UnknownDependencyError,
    cascade_dependency_outcome,
    find_ready_job_ids,
    submit_job,
)
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
def migrated_job_dependencies_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Reuses the migrated-database fixture pattern from tests/test_db_migrations.py.

    A pytest fixture cannot be imported across test modules without a shared
    conftest.py entry (none exists for `migrated_database`), so this mirrors
    the exact create/upgrade/teardown sequence, matching the precedent set by
    tests/test_job_lifecycle.py's `migrated_job_lifecycle_db`.
    """
    database_name = f"job_dependencies_{uuid.uuid4().hex[:8]}"
    admin_params = _admin_connection_settings()

    try:
        with _connect_admin(admin_params) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f'CREATE DATABASE "{database_name}"')
    except psycopg.Error as exc:  # pragma: no cover - exercised when local Postgres is unavailable
        pytest.fail(
            "PostgreSQL is required for tests/test_job_dependencies.py. "
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
        "job_type": "phase17_dependency_probe",
        "payload": {},
        "status": status,
    }
    defaults.update(overrides)
    job = Job(**defaults)
    session.add(job)
    session.flush()
    return job


def _fail_job(settings: Any, job_id: uuid.UUID) -> None:
    """Drive a QUEUED Job through CLAIMED -> FAILED via the guarded transition path."""
    with session_scope(settings) as session:
        apply_job_transition(
            session, job_id=job_id, request=JobTransitionRequest(event_type=JobEventType.CLAIMED)
        )
        apply_job_transition(
            session,
            job_id=job_id,
            request=JobTransitionRequest(
                event_type=JobEventType.FAILED,
                failure_reason=JobFailureReason.HANDLER_ERROR,
            ),
        )


def _cancel_queued_job(settings: Any, job_id: uuid.UUID) -> None:
    with session_scope(settings) as session:
        apply_job_transition(
            session, job_id=job_id, request=JobTransitionRequest(event_type=JobEventType.CANCELLED)
        )


def test_submit_rejects_self_dependency(migrated_job_dependencies_db: str) -> None:
    settings = load_settings()
    fixed_id = uuid.uuid4()

    with mock.patch("trading_platform.jobs.dependencies.uuid.uuid4", return_value=fixed_id):
        with pytest.raises(SelfDependencyError):
            submit_job(job_type="probe", payload={}, depends_on=[fixed_id], settings=settings)

    with session_scope(settings) as session:
        job_count = session.execute(select(func.count()).select_from(Job)).scalar_one()
    assert job_count == 0


def test_submit_rejects_two_node_cycle(migrated_job_dependencies_db: str) -> None:
    settings = load_settings()
    job_a_id = submit_job(job_type="a", payload={}, settings=settings)
    job_b_id = submit_job(job_type="b", payload={}, depends_on=[job_a_id], settings=settings)

    # A -> B, closing A -> B -> A: simulate the new submission taking job A's own
    # id (self/cycle rejection is unrepresentable through a caller-chosen id
    # since submit_job always generates a fresh uuid -- see plan advisor note).
    with mock.patch("trading_platform.jobs.dependencies.uuid.uuid4", return_value=job_a_id):
        with pytest.raises(DependencyCycleError):
            submit_job(job_type="c", payload={}, depends_on=[job_b_id], settings=settings)

    with session_scope(settings) as session:
        job_count = session.execute(select(func.count()).select_from(Job)).scalar_one()
        dependency_count = session.execute(
            select(func.count()).select_from(JobDependency)
        ).scalar_one()
    assert job_count == 2
    assert dependency_count == 1


def test_submit_rejects_three_node_cycle(migrated_job_dependencies_db: str) -> None:
    settings = load_settings()
    job_a_id = submit_job(job_type="a", payload={}, settings=settings)
    job_b_id = submit_job(job_type="b", payload={}, depends_on=[job_a_id], settings=settings)
    job_c_id = submit_job(job_type="c", payload={}, depends_on=[job_b_id], settings=settings)

    # Existing: B -> A, C -> B. Proposed: A -> C, closing A -> C -> B -> A, a
    # genuine three-hop cycle (not a one-hop comparison).
    with mock.patch("trading_platform.jobs.dependencies.uuid.uuid4", return_value=job_a_id):
        with pytest.raises(DependencyCycleError):
            submit_job(job_type="d", payload={}, depends_on=[job_c_id], settings=settings)

    with session_scope(settings) as session:
        job_count = session.execute(select(func.count()).select_from(Job)).scalar_one()
        dependency_count = session.execute(
            select(func.count()).select_from(JobDependency)
        ).scalar_one()
    assert job_count == 3
    assert dependency_count == 2


def test_submit_rejects_unknown_dependency(migrated_job_dependencies_db: str) -> None:
    settings = load_settings()
    unknown_id = uuid.uuid4()

    with pytest.raises(UnknownDependencyError):
        submit_job(job_type="probe", payload={}, depends_on=[unknown_id], settings=settings)

    with session_scope(settings) as session:
        job_count = session.execute(select(func.count()).select_from(Job)).scalar_one()
    assert job_count == 0


def test_submit_deduplicates_repeated_dependency_ids(migrated_job_dependencies_db: str) -> None:
    settings = load_settings()
    job_a_id = submit_job(job_type="a", payload={}, settings=settings)
    job_b_id = submit_job(
        job_type="b", payload={}, depends_on=[job_a_id, job_a_id], settings=settings
    )

    with session_scope(settings) as session:
        rows = (
            session.execute(select(JobDependency).where(JobDependency.job_id == job_b_id))
            .scalars()
            .all()
        )
    assert len(rows) == 1


def test_submit_writes_submitted_job_event(migrated_job_dependencies_db: str) -> None:
    settings = load_settings()
    job_id = submit_job(job_type="probe", payload={}, settings=settings)

    with session_scope(settings) as session:
        events = session.execute(select(JobEvent).where(JobEvent.job_id == job_id)).scalars().all()
    assert len(events) == 1
    assert events[0].event_type is JobEventType.SUBMITTED
    assert events[0].to_status is JobStatus.QUEUED


def test_job_with_no_dependencies_is_immediately_ready(migrated_job_dependencies_db: str) -> None:
    settings = load_settings()
    job_id = submit_job(job_type="probe", payload={}, settings=settings)

    with session_scope(settings) as session:
        ready = find_ready_job_ids(session, limit=10)
    assert job_id in ready


def test_job_is_not_ready_until_every_dependency_succeeds(
    migrated_job_dependencies_db: str,
) -> None:
    settings = load_settings()

    with session_scope(settings) as session:
        dep_one = _seed_job(session, job_type="dep_one", status=JobStatus.SUCCEEDED)
        dep_two = _seed_job(session, job_type="dep_two", status=JobStatus.RUNNING)
        dep_one_id, dep_two_id = dep_one.id, dep_two.id

    dependent_id = submit_job(
        job_type="dependent", payload={}, depends_on=[dep_one_id, dep_two_id], settings=settings
    )

    with session_scope(settings) as session:
        ready = find_ready_job_ids(session, limit=10)
    assert dependent_id not in ready

    with session_scope(settings) as session:
        dep_two_row = session.get(Job, dep_two_id)
        assert dep_two_row is not None
        dep_two_row.status = JobStatus.SUCCEEDED

    with session_scope(settings) as session:
        ready = find_ready_job_ids(session, limit=10)
    assert dependent_id in ready


def test_ready_jobs_are_returned_oldest_first_and_respect_limit(
    migrated_job_dependencies_db: str,
) -> None:
    settings = load_settings()
    base = datetime.now(UTC) - timedelta(hours=1)

    with session_scope(settings) as session:
        job_one = _seed_job(session, job_type="one", queued_at=base)
        job_two = _seed_job(session, job_type="two", queued_at=base + timedelta(minutes=5))
        job_three = _seed_job(session, job_type="three", queued_at=base + timedelta(minutes=10))
        ordered_ids = [job_one.id, job_two.id, job_three.id]

    with session_scope(settings) as session:
        ready = find_ready_job_ids(session, limit=2)

    assert ready == ordered_ids[:2]


def test_failed_dependency_cancels_unstarted_descendant_with_causal_chain(
    migrated_job_dependencies_db: str,
) -> None:
    settings = load_settings()
    job_a_id = submit_job(job_type="a", payload={}, settings=settings)
    job_b_id = submit_job(job_type="b", payload={}, depends_on=[job_a_id], settings=settings)

    _fail_job(settings, job_a_id)

    with session_scope(settings) as session:
        cancelled = cascade_dependency_outcome(session, terminal_job_id=job_a_id)
    assert cancelled == [job_b_id]

    with session_scope(settings) as session:
        job_b = session.get(Job, job_b_id)
        assert job_b is not None
        assert job_b.status is JobStatus.CANCELLED
        assert job_b.cancellation_cause is JobCancellationCause.DEPENDENCY_FAILED
        assert job_b.blocking_job_id == job_a_id
        assert job_b.blocking_job_status is JobStatus.FAILED
        assert job_b.root_cause_job_id == job_a_id
        assert job_b.failure_reason is None


def test_cascade_is_transitive_across_three_levels(migrated_job_dependencies_db: str) -> None:
    settings = load_settings()
    job_a_id = submit_job(job_type="a", payload={}, settings=settings)
    job_b_id = submit_job(job_type="b", payload={}, depends_on=[job_a_id], settings=settings)
    job_c_id = submit_job(job_type="c", payload={}, depends_on=[job_b_id], settings=settings)

    _fail_job(settings, job_a_id)

    with session_scope(settings) as session:
        cancelled = cascade_dependency_outcome(session, terminal_job_id=job_a_id)
    assert set(cancelled) == {job_b_id, job_c_id}

    with session_scope(settings) as session:
        job_b = session.get(Job, job_b_id)
        job_c = session.get(Job, job_c_id)
        assert job_b is not None and job_c is not None
        assert job_b.status is JobStatus.CANCELLED
        assert job_c.status is JobStatus.CANCELLED
        assert job_b.root_cause_job_id == job_a_id
        assert job_c.root_cause_job_id == job_a_id
        assert job_b.blocking_job_id == job_a_id
        assert job_c.blocking_job_id == job_b_id


def test_cascade_from_cancelled_dependency_uses_dependency_cancelled_cause(
    migrated_job_dependencies_db: str,
) -> None:
    settings = load_settings()
    job_a_id = submit_job(job_type="a", payload={}, settings=settings)
    job_b_id = submit_job(job_type="b", payload={}, depends_on=[job_a_id], settings=settings)

    _cancel_queued_job(settings, job_a_id)

    with session_scope(settings) as session:
        cascade_dependency_outcome(session, terminal_job_id=job_a_id)

    with session_scope(settings) as session:
        job_b = session.get(Job, job_b_id)
        assert job_b is not None
        assert job_b.cancellation_cause is JobCancellationCause.DEPENDENCY_CANCELLED
        assert job_b.blocking_job_status is JobStatus.CANCELLED


def test_cascade_leaves_running_descendant_untouched(migrated_job_dependencies_db: str) -> None:
    settings = load_settings()
    job_a_id = submit_job(job_type="a", payload={}, settings=settings)
    job_b_id = submit_job(job_type="b", payload={}, depends_on=[job_a_id], settings=settings)

    with session_scope(settings) as session:
        apply_job_transition(
            session, job_id=job_b_id, request=JobTransitionRequest(event_type=JobEventType.CLAIMED)
        )

    _fail_job(settings, job_a_id)

    with session_scope(settings) as session:
        cancelled = cascade_dependency_outcome(session, terminal_job_id=job_a_id)
    assert cancelled == []

    with session_scope(settings) as session:
        job_b = session.get(Job, job_b_id)
        assert job_b is not None
        assert job_b.status is JobStatus.RUNNING


def test_cascade_is_idempotent(migrated_job_dependencies_db: str) -> None:
    settings = load_settings()
    job_a_id = submit_job(job_type="a", payload={}, settings=settings)
    job_b_id = submit_job(job_type="b", payload={}, depends_on=[job_a_id], settings=settings)

    _fail_job(settings, job_a_id)

    with session_scope(settings) as session:
        first_call = cascade_dependency_outcome(session, terminal_job_id=job_a_id)
    assert first_call == [job_b_id]

    with session_scope(settings) as session:
        second_call = cascade_dependency_outcome(session, terminal_job_id=job_a_id)
    assert second_call == []

    with session_scope(settings) as session:
        cancelled_events = (
            session.execute(
                select(JobEvent).where(
                    JobEvent.job_id == job_b_id,
                    JobEvent.event_type == JobEventType.CANCELLED,
                )
            )
            .scalars()
            .all()
        )
    assert len(cancelled_events) == 1


def test_no_dependent_is_left_queued_behind_a_dead_dependency(
    migrated_job_dependencies_db: str,
) -> None:
    settings = load_settings()
    job_a_id = submit_job(job_type="a", payload={}, settings=settings)
    job_b_id = submit_job(job_type="b", payload={}, depends_on=[job_a_id], settings=settings)
    job_c_id = submit_job(job_type="c", payload={}, depends_on=[job_b_id], settings=settings)

    _fail_job(settings, job_a_id)

    with session_scope(settings) as session:
        cascade_dependency_outcome(session, terminal_job_id=job_a_id)

    with session_scope(settings) as session:
        dependency_target = aliased(Job)
        stranded = (
            session.execute(
                select(Job.id)
                .join(JobDependency, JobDependency.job_id == Job.id)
                .join(dependency_target, dependency_target.id == JobDependency.depends_on_job_id)
                .where(
                    Job.status == JobStatus.QUEUED,
                    dependency_target.status.in_([JobStatus.FAILED, JobStatus.CANCELLED]),
                )
            )
            .scalars()
            .all()
        )

    assert stranded == []

    # Sanity: C would have been exactly this stranding scenario without the
    # cascade (it depends on B, which depends on the now-FAILED A) -- prove the
    # cascade actually reached it, not just that the anti-stranding query ran.
    with session_scope(settings) as session:
        job_c = session.get(Job, job_c_id)
        assert job_c is not None
        assert job_c.status is JobStatus.CANCELLED


def test_submit_with_caller_session_matches_standalone_submission(
    migrated_job_dependencies_db: str,
) -> None:
    settings = load_settings()
    dependency_id = submit_job(job_type="dependency", payload={}, settings=settings)
    standalone_job_id = submit_job(
        job_type="probe",
        payload={"source": "standalone"},
        depends_on=[dependency_id, dependency_id],
        settings=settings,
    )

    with session_scope(settings) as session:
        caller_session_job_id = submit_job(
            job_type="probe",
            payload={"source": "standalone"},
            depends_on=[dependency_id, dependency_id],
            session=session,
        )

    with session_scope(settings) as session:
        standalone_job = session.get(Job, standalone_job_id)
        caller_session_job = session.get(Job, caller_session_job_id)
        assert standalone_job is not None
        assert caller_session_job is not None
        assert (caller_session_job.job_type, caller_session_job.payload, caller_session_job.status) == (
            standalone_job.job_type,
            standalone_job.payload,
            standalone_job.status,
        )

        for job_id in (standalone_job_id, caller_session_job_id):
            dependency_rows = session.execute(
                select(JobDependency.depends_on_job_id).where(JobDependency.job_id == job_id)
            ).scalars().all()
            events = session.execute(
                select(JobEvent).where(JobEvent.job_id == job_id)
            ).scalars().all()
            assert dependency_rows == [dependency_id]
            assert len(events) == 1
            assert events[0].event_type is JobEventType.SUBMITTED
            assert events[0].to_status is JobStatus.QUEUED


def test_submit_with_caller_session_rolls_back_all_submission_rows(
    migrated_job_dependencies_db: str,
) -> None:
    settings = load_settings()
    dependency_id = submit_job(job_type="dependency", payload={}, settings=settings)
    submitted_job_id: uuid.UUID

    with pytest.raises(RuntimeError, match="force rollback"):
        with session_scope(settings) as session:
            submitted_job_id = submit_job(
                job_type="probe",
                payload={"source": "caller-session"},
                depends_on=[dependency_id],
                session=session,
            )
            assert session.get(Job, submitted_job_id) is not None
            raise RuntimeError("force rollback")

    with session_scope(settings) as session:
        assert session.get(Job, submitted_job_id) is None
        dependency_count = session.execute(
            select(func.count()).select_from(JobDependency).where(JobDependency.job_id == submitted_job_id)
        ).scalar_one()
        event_count = session.execute(
            select(func.count()).select_from(JobEvent).where(JobEvent.job_id == submitted_job_id)
        ).scalar_one()

    assert dependency_count == 0
    assert event_count == 0
