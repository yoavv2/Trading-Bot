"""Phase 17 Job execution context behavior tests (JOB-07, D-08, D-11, D-13)."""

from __future__ import annotations

import inspect
import json
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
from sqlalchemy import select, update

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.migrate import build_alembic_config

from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.db.models import Job, JobLog, JobStatus
from trading_platform.db.session import clear_engine_cache, session_scope
from trading_platform.jobs.context import DatabaseJobContext
from trading_platform.jobs.contracts import JobCancelledError, JobContext


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
def migrated_job_context_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Reuses the migrated-database fixture pattern from tests/test_db_migrations.py.

    A pytest fixture cannot be imported across test modules without a shared
    conftest.py entry (none exists for `migrated_database`), so this mirrors
    the exact create/upgrade/teardown sequence set by
    tests/test_job_lifecycle.py's `migrated_job_lifecycle_db`.
    """
    database_name = f"job_context_{uuid.uuid4().hex[:8]}"
    admin_params = _admin_connection_settings()

    try:
        with _connect_admin(admin_params) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f'CREATE DATABASE "{database_name}"')
    except psycopg.Error as exc:  # pragma: no cover - exercised when local Postgres is unavailable
        pytest.fail(
            "PostgreSQL is required for tests/test_job_context.py. "
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


def _seed_job(session: Any, *, status: JobStatus = JobStatus.RUNNING, **overrides: Any) -> Job:
    defaults: dict[str, Any] = {
        "job_type": "phase17_context_probe",
        "payload": {"symbol": "AAPL"},
        "status": status,
    }
    defaults.update(overrides)
    job = Job(**defaults)
    session.add(job)
    session.flush()
    return job


def _make_context(
    job_id: uuid.UUID, *, job_type: str = "phase17_context_probe"
) -> DatabaseJobContext:
    return DatabaseJobContext(job_id, job_type, {"symbol": "AAPL"})


def test_report_progress_persists_partial_snapshot(migrated_job_context_db: str) -> None:
    settings = load_settings()

    with session_scope(settings) as session:
        job = _seed_job(session)
        job_id = job.id

    context = _make_context(job_id)
    context.report_progress(percent=25, step="loading")
    context.report_progress(step="writing")

    with session_scope(settings) as session:
        job = session.get(Job, job_id)
        assert job is not None
        assert job.progress_percent == 25
        assert job.progress_step == "writing"


@pytest.mark.parametrize(
    "terminal_status", [JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED]
)
def test_report_progress_is_noop_on_terminal_job(
    migrated_job_context_db: str, terminal_status: JobStatus
) -> None:
    """WR-01 regression: a progress write onto an already-terminal Job (the
    CR-02 race, where a handler is still running after a concurrent sweep
    terminalized the Job) must be a cooperative no-op preserving the last
    snapshot (D-12), not overwrite progress on a FAILED/CANCELLED Job.
    """
    settings = load_settings()

    with session_scope(settings) as session:
        job = _seed_job(session, status=terminal_status, progress_percent=60)
        job_id = job.id

    context = _make_context(job_id)
    context.report_progress(percent=90, step="should-not-apply")

    with session_scope(settings) as session:
        job = session.get(Job, job_id)
        assert job is not None
        assert job.status is terminal_status
        assert job.progress_percent == 60
        assert job.progress_step is None


def test_report_progress_rejects_out_of_range_percent(migrated_job_context_db: str) -> None:
    settings = load_settings()

    with session_scope(settings) as session:
        job = _seed_job(session)
        job_id = job.id

    context = _make_context(job_id)

    with pytest.raises(ValueError):
        context.report_progress(percent=101)
    with pytest.raises(ValueError):
        context.report_progress(percent=-1)

    with session_scope(settings) as session:
        job = session.get(Job, job_id)
        assert job is not None
        assert job.progress_percent is None


def test_progress_is_visible_before_job_completes(migrated_job_context_db: str) -> None:
    settings = load_settings()

    with session_scope(settings) as session:
        job = _seed_job(session, status=JobStatus.RUNNING)
        job_id = job.id

    context = _make_context(job_id)
    context.report_progress(percent=50, current=5, total=10)

    with session_scope(settings) as session:
        job = session.get(Job, job_id)
        assert job is not None
        assert job.status is JobStatus.RUNNING
        assert job.progress_percent == 50
        assert job.progress_current == 5
        assert job.progress_total == 10


def test_log_appends_records_with_monotonic_sequence(migrated_job_context_db: str) -> None:
    settings = load_settings()

    with session_scope(settings) as session:
        job = _seed_job(session)
        job_id = job.id

    context = _make_context(job_id)
    context.log(level="info", event_code="step_one", message="first")
    context.log(level="info", event_code="step_two", message="second")
    context.log(level="info", event_code="step_three", message="third")

    with session_scope(settings) as session:
        logs = (
            session.execute(select(JobLog).where(JobLog.job_id == job_id).order_by(JobLog.sequence))
            .scalars()
            .all()
        )
        assert [log.sequence for log in logs] == [1, 2, 3]
        assert [log.message for log in logs] == ["first", "second", "third"]


def test_log_ordering_is_deterministic_under_identical_timestamps(
    migrated_job_context_db: str,
) -> None:
    settings = load_settings()

    with session_scope(settings) as session:
        job = _seed_job(session)
        job_id = job.id

    context = _make_context(job_id)
    context.log(level="info", event_code="alpha", message="alpha")
    context.log(level="info", event_code="beta", message="beta")

    frozen_timestamp = datetime.now(UTC)
    with session_scope(settings) as session:
        session.execute(
            update(JobLog).where(JobLog.job_id == job_id).values(logged_at=frozen_timestamp)
        )

    with session_scope(settings) as session:
        logs = (
            session.execute(
                select(JobLog)
                .where(JobLog.job_id == job_id)
                .order_by(JobLog.job_id, JobLog.sequence)
            )
            .scalars()
            .all()
        )
        assert [log.event_code for log in logs] == ["alpha", "beta"]
        assert logs[0].logged_at == logs[1].logged_at


def test_log_context_is_sanitized(migrated_job_context_db: str) -> None:
    settings = load_settings()
    raw_password = "hunter2-super-secret"

    with session_scope(settings) as session:
        job = _seed_job(session)
        job_id = job.id

    context = _make_context(job_id)
    context.log(
        level="info",
        event_code="broker_auth",
        message="authenticating",
        context={"password": raw_password, "note": "ok"},
    )

    with session_scope(settings) as session:
        log = session.execute(select(JobLog).where(JobLog.job_id == job_id)).scalar_one()
        serialized = json.dumps(log.context)
        assert raw_password not in serialized
        assert "[REDACTED]" in serialized


def test_log_rejects_unknown_level(migrated_job_context_db: str) -> None:
    settings = load_settings()

    with session_scope(settings) as session:
        job = _seed_job(session)
        job_id = job.id

    context = _make_context(job_id)
    with pytest.raises(ValueError):
        context.log(level="verbose", event_code="whatever", message="nope")


def test_log_truncates_oversized_message_and_context(migrated_job_context_db: str) -> None:
    settings = load_settings()

    with session_scope(settings) as session:
        job = _seed_job(session)
        job_id = job.id

    context = _make_context(job_id)
    long_message = "x" * 10000
    large_context = {"blob": "y" * 20000}

    context.log(level="info", event_code="oversized", message=long_message, context=large_context)

    with session_scope(settings) as session:
        log = session.execute(select(JobLog).where(JobLog.job_id == job_id)).scalar_one()
        assert len(log.message) == 4000
        assert log.context.get("_truncated") is True
        assert isinstance(log.context.get("_original_bytes"), int)


def test_is_cancellation_requested_reflects_persisted_request(migrated_job_context_db: str) -> None:
    settings = load_settings()

    with session_scope(settings) as session:
        job = _seed_job(session)
        job_id = job.id

    context = _make_context(job_id)
    assert context.is_cancellation_requested() is False

    with session_scope(settings) as session:
        job = session.get(Job, job_id)
        assert job is not None
        job.cancellation_requested_at = datetime.now(UTC)

    assert context.is_cancellation_requested() is True


def test_raise_if_cancelled_raises_job_cancelled_error(migrated_job_context_db: str) -> None:
    settings = load_settings()

    with session_scope(settings) as session:
        job = _seed_job(session)
        job_id = job.id
        job.cancellation_requested_at = datetime.now(UTC)

    context = _make_context(job_id)
    with pytest.raises(JobCancelledError) as exc_info:
        context.raise_if_cancelled()
    assert exc_info.value.job_id == job_id


def test_context_exposes_no_session_or_status_setter() -> None:
    pub = {n for n, _ in inspect.getmembers(DatabaseJobContext) if not n.startswith("_")}
    required = {
        "job_id",
        "job_type",
        "payload",
        "report_progress",
        "log",
        "is_cancellation_requested",
        "raise_if_cancelled",
    }
    assert pub == required
    assert isinstance(DatabaseJobContext, type)
    # Runtime-checkable Protocol conformance, restated as a test.
    probe = DatabaseJobContext(uuid.uuid4(), "probe", {})
    assert isinstance(probe, JobContext)
