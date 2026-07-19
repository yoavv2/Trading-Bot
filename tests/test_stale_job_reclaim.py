"""Phase 17 claim-safety and crash-recovery tests (JOB-02, D-01-D-04, D-07)."""

from __future__ import annotations

import os
import sys
import threading
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
from alembic import command
from sqlalchemy import func, select, text

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
from trading_platform.db.session import clear_engine_cache, get_session_factory, session_scope
from trading_platform.jobs.lifecycle import JobTransitionRequest, apply_job_transition
from trading_platform.jobs.queue import (
    claim_next_job,
    find_lost_job_ids,
    reclaim_lost_jobs,
    renew_lease,
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
def migrated_job_queue_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Reuses the migrated-database fixture pattern (no shared conftest.py entry
    exists for `migrated_database`), matching the precedent set by
    tests/test_job_lifecycle.py, tests/test_job_dependencies.py, and
    tests/test_job_cancellation.py.
    """
    database_name = f"job_queue_{uuid.uuid4().hex[:8]}"
    admin_params = _admin_connection_settings()

    try:
        with _connect_admin(admin_params) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f'CREATE DATABASE "{database_name}"')
    except psycopg.Error as exc:  # pragma: no cover - exercised when local Postgres is unavailable
        pytest.fail(
            "PostgreSQL is required for tests/test_stale_job_reclaim.py. "
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
        "job_type": "phase17_queue_probe",
        "payload": {},
        "status": status,
    }
    defaults.update(overrides)
    job = Job(**defaults)
    session.add(job)
    session.flush()
    return job


def _seed_lost_job(session: Any, *, lease_expires_at: datetime, **overrides: Any) -> Job:
    """Seed a Job already RUNNING with an expired lease, directly -- not via a
    CLAIMED transition -- so the only JobEvent a reclaim produces is
    LEASE_EXPIRED itself (keeps audit-count assertions unambiguous).
    """
    now = datetime.now(UTC)
    defaults: dict[str, Any] = {
        "status": JobStatus.RUNNING,
        "started_at": now - timedelta(minutes=10),
        "lease_owner": "worker-dead",
        "lease_expires_at": lease_expires_at,
        "heartbeat_at": now - timedelta(minutes=6),
    }
    defaults.update(overrides)
    return _seed_job(session, **defaults)


# --- Claim-safety tests ------------------------------------------------


def test_claim_returns_oldest_ready_job_first(migrated_job_queue_db: str) -> None:
    settings = load_settings()
    now = datetime.now(UTC)
    with session_scope(settings) as session:
        oldest = _seed_job(session, queued_at=now - timedelta(minutes=10))
        _seed_job(session, queued_at=now - timedelta(minutes=5))
        _seed_job(session, queued_at=now)
        oldest_id = oldest.id

    with session_scope(settings) as session:
        claimed = claim_next_job(session, worker_id="worker-1")

    assert claimed == oldest_id


def test_claim_transitions_job_to_running_with_lease(migrated_job_queue_db: str) -> None:
    settings = load_settings()
    with session_scope(settings) as session:
        job = _seed_job(session)
        job_id = job.id

    before_claim = datetime.now(UTC)
    with session_scope(settings) as session:
        claimed = claim_next_job(session, worker_id="worker-1")
    assert claimed == job_id

    with session_scope(settings) as session:
        job = session.get(Job, job_id)
        assert job is not None
        assert job.status is JobStatus.RUNNING
        assert job.lease_owner == "worker-1"
        assert job.lease_expires_at is not None
        assert job.lease_expires_at > before_claim
        assert job.started_at is not None

        events = (
            session.execute(
                select(JobEvent).where(
                    JobEvent.job_id == job_id,
                    JobEvent.event_type == JobEventType.CLAIMED,
                )
            )
            .scalars()
            .all()
        )
    assert len(events) == 1


def test_concurrent_workers_never_claim_the_same_job(migrated_job_queue_db: str) -> None:
    """The JOB-02 no-duplication proof: two genuinely separate connections,
    one eligible Job, and a non-blocking thread-join assertion rather than a
    wall-clock threshold (a threshold fails open -- if SKIP LOCKED ever
    regresses to a blocking FOR UPDATE, this shape converts that regression
    into a deterministic failure instead of a hang).
    """
    settings = load_settings()
    with session_scope(settings) as session:
        job = _seed_job(session)
        job_id = job.id

    session_factory = get_session_factory(settings)
    session_one = session_factory()
    result: dict[str, uuid.UUID | None] = {}
    errors: list[BaseException] = []

    try:
        claimed_by_one = claim_next_job(session_one, worker_id="worker-1")
        assert claimed_by_one == job_id
        # session_one's transaction is intentionally left open (no commit) so
        # its row lock is still held when the second worker attempts to claim.

        def _claim_in_thread() -> None:
            session_two = session_factory()
            try:
                # Belt-and-braces: even if SKIP LOCKED ever regressed to a
                # blocking FOR UPDATE, this bounds the wait to a hard DB error
                # instead of an indefinite hang.
                session_two.execute(text("SET LOCAL lock_timeout = '5s'"))
                result["claimed"] = claim_next_job(session_two, worker_id="worker-2")
                session_two.rollback()
            except BaseException as exc:  # noqa: BLE001 - captured for the main thread to assert on
                errors.append(exc)
            finally:
                session_two.close()

        thread = threading.Thread(target=_claim_in_thread)
        thread.start()
        thread.join(timeout=10)

        assert not thread.is_alive()
        assert not errors, f"claim thread raised: {errors!r}"
        assert result.get("claimed") is None

        session_one.rollback()
    finally:
        session_one.close()

    # The row was skipped, not lost: once the first transaction ends, the
    # same Job becomes claimable again.
    with session_scope(settings) as session:
        reclaimed = claim_next_job(session, worker_id="worker-3")
    assert reclaimed == job_id


def test_claim_skips_job_with_unsatisfied_dependency(migrated_job_queue_db: str) -> None:
    settings = load_settings()
    with session_scope(settings) as session:
        dependency = _seed_job(session, status=JobStatus.RUNNING, started_at=datetime.now(UTC))
        dependent = _seed_job(session)
        session.add(JobDependency(job_id=dependent.id, depends_on_job_id=dependency.id))
        session.flush()
        dependency_id = dependency.id
        dependent_id = dependent.id

    with session_scope(settings) as session:
        claimed = claim_next_job(session, worker_id="worker-1")
    assert claimed is None

    with session_scope(settings) as session:
        apply_job_transition(
            session,
            job_id=dependency_id,
            request=JobTransitionRequest(event_type=JobEventType.SUCCEEDED),
        )

    with session_scope(settings) as session:
        claimed = claim_next_job(session, worker_id="worker-1")
    assert claimed == dependent_id


def test_claim_skips_job_with_pending_cancellation(migrated_job_queue_db: str) -> None:
    settings = load_settings()
    with session_scope(settings) as session:
        _seed_job(
            session,
            cancellation_requested_at=datetime.now(UTC),
            cancellation_requested_by="operator_1",
        )

    with session_scope(settings) as session:
        claimed = claim_next_job(session, worker_id="worker-1")
    assert claimed is None


def test_renew_lease_extends_expiry_for_owner(migrated_job_queue_db: str) -> None:
    settings = load_settings()
    with session_scope(settings) as session:
        job = _seed_job(session)
        job_id = job.id

    with session_scope(settings) as session:
        claim_next_job(session, worker_id="worker-1")

    with session_scope(settings) as session:
        job = session.get(Job, job_id)
        assert job is not None
        original_expiry = job.lease_expires_at
    assert original_expiry is not None

    renewed = renew_lease(job_id=job_id, worker_id="worker-1", settings=settings)
    assert renewed is True

    with session_scope(settings) as session:
        job = session.get(Job, job_id)
        assert job is not None
        assert job.lease_expires_at is not None
        assert job.lease_expires_at > original_expiry


def test_renew_lease_returns_false_for_non_owner(migrated_job_queue_db: str) -> None:
    settings = load_settings()
    with session_scope(settings) as session:
        job = _seed_job(session)
        job_id = job.id

    with session_scope(settings) as session:
        claim_next_job(session, worker_id="worker-1")

    renewed = renew_lease(job_id=job_id, worker_id="worker-2", settings=settings)
    assert renewed is False

    # Also False once the lease has already been reclaimed by a sweep.
    with session_scope(settings) as session:
        job = session.get(Job, job_id, with_for_update=True)
        assert job is not None
        job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)

    with session_scope(settings) as session:
        reclaim_lost_jobs(session)

    renewed_after_reclaim = renew_lease(job_id=job_id, worker_id="worker-1", settings=settings)
    assert renewed_after_reclaim is False


# --- Crash-recovery tests ------------------------------------------------


def test_find_lost_jobs_detects_only_running_past_lease_expiry(migrated_job_queue_db: str) -> None:
    settings = load_settings()
    now = datetime.now(UTC)
    with session_scope(settings) as session:
        lost = _seed_lost_job(session, lease_expires_at=now - timedelta(minutes=5))
        valid_lease = _seed_lost_job(session, lease_expires_at=now + timedelta(minutes=5))
        queued = _seed_job(session)
        lost_id = lost.id
        valid_lease_id = valid_lease.id
        queued_id = queued.id

    with session_scope(settings) as session:
        found = find_lost_job_ids(session, now=now)

    assert lost_id in found
    assert valid_lease_id not in found
    assert queued_id not in found


def test_reclaim_lost_jobs_marks_rows_failed_with_audit(migrated_job_queue_db: str) -> None:
    settings = load_settings()
    now = datetime.now(UTC)
    with session_scope(settings) as session:
        job = _seed_lost_job(session, lease_expires_at=now - timedelta(minutes=5))
        job_id = job.id

    with session_scope(settings) as session:
        reclaimed = reclaim_lost_jobs(session, now=now)
    assert reclaimed == [job_id]

    with session_scope(settings) as session:
        job = session.get(Job, job_id)
        assert job is not None
        assert job.status is not JobStatus.CANCELLED
        assert job.status is JobStatus.FAILED
        assert job.failure_reason is JobFailureReason.LEASE_EXPIRED
        assert job.lease_owner is None
        assert job.lease_expires_at is None

        events = session.execute(select(JobEvent).where(JobEvent.job_id == job_id)).scalars().all()
    assert len(events) == 1
    assert events[0].event_type == JobEventType.LEASE_EXPIRED


def test_reclaim_records_outcome_uncertain(migrated_job_queue_db: str) -> None:
    settings = load_settings()
    now = datetime.now(UTC)
    with session_scope(settings) as session:
        job = _seed_lost_job(session, lease_expires_at=now - timedelta(minutes=5))
        job_id = job.id

    with session_scope(settings) as session:
        reclaim_lost_jobs(session, now=now)

    with session_scope(settings) as session:
        job = session.get(Job, job_id)
        assert job is not None
        assert job.outcome_uncertain is True


def test_reclaim_lost_jobs_is_idempotent(migrated_job_queue_db: str) -> None:
    settings = load_settings()
    now = datetime.now(UTC)
    with session_scope(settings) as session:
        job = _seed_lost_job(session, lease_expires_at=now - timedelta(minutes=5))
        job_id = job.id

    with session_scope(settings) as session:
        first_pass = reclaim_lost_jobs(session, now=now)
    assert first_pass == [job_id]

    with session_scope(settings) as session:
        second_pass = reclaim_lost_jobs(session, now=now)
    assert second_pass == []

    with session_scope(settings) as session:
        events = session.execute(select(JobEvent).where(JobEvent.job_id == job_id)).scalars().all()
    assert len(events) == 1


def test_reclaim_never_requeues(migrated_job_queue_db: str) -> None:
    settings = load_settings()
    now = datetime.now(UTC)
    with session_scope(settings) as session:
        job = _seed_lost_job(session, lease_expires_at=now - timedelta(minutes=5))
        job_id = job.id
        queued_count_before = session.execute(
            select(func.count()).select_from(Job).where(Job.status == JobStatus.QUEUED)
        ).scalar_one()

    with session_scope(settings) as session:
        reclaim_lost_jobs(session, now=now)

    with session_scope(settings) as session:
        job = session.get(Job, job_id)
        assert job is not None
        assert job.status is not JobStatus.QUEUED

        queued_count_after = session.execute(
            select(func.count()).select_from(Job).where(Job.status == JobStatus.QUEUED)
        ).scalar_one()
    assert queued_count_after == queued_count_before


def test_reclaim_cascades_to_unstarted_dependents(migrated_job_queue_db: str) -> None:
    settings = load_settings()
    now = datetime.now(UTC)
    with session_scope(settings) as session:
        ancestor = _seed_lost_job(session, lease_expires_at=now - timedelta(minutes=5))
        dependent = _seed_job(session)
        session.add(JobDependency(job_id=dependent.id, depends_on_job_id=ancestor.id))
        session.flush()
        ancestor_id = ancestor.id
        dependent_id = dependent.id

    with session_scope(settings) as session:
        reclaim_lost_jobs(session, now=now)

    with session_scope(settings) as session:
        dependent_job = session.get(Job, dependent_id)
        assert dependent_job is not None
        assert dependent_job.status is JobStatus.CANCELLED
        assert dependent_job.root_cause_job_id == ancestor_id
        assert dependent_job.cancellation_cause is JobCancellationCause.DEPENDENCY_FAILED


def test_reclaim_preserves_last_progress(migrated_job_queue_db: str) -> None:
    settings = load_settings()
    now = datetime.now(UTC)
    with session_scope(settings) as session:
        job = _seed_lost_job(
            session, lease_expires_at=now - timedelta(minutes=5), progress_percent=60
        )
        job_id = job.id

    with session_scope(settings) as session:
        reclaim_lost_jobs(session, now=now)

    with session_scope(settings) as session:
        job = session.get(Job, job_id)
        assert job is not None
        assert job.progress_percent == 60
