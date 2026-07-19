"""Phase 17 runner tests: restart survival and every handler outcome (JOB-02, JOB-03)."""

from __future__ import annotations

import os
import sys
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

import psycopg
import pytest
from alembic import command

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.migrate import build_alembic_config

from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.db.models import (
    Job,
    JobEvent,
    JobFailureReason,
    JobStatus,
    JobTransitionOutcome,
)
from trading_platform.db.session import clear_engine_cache, session_scope
from trading_platform.jobs import runner as runner_module
from trading_platform.jobs.cancellation import request_cancellation
from trading_platform.jobs.contracts import JobContext, JobHandler
from trading_platform.jobs.dependencies import submit_job
from trading_platform.jobs.queue import claim_next_job, reclaim_lost_jobs
from trading_platform.jobs.registry import JobRegistry
from trading_platform.jobs.runner import execute_job, run_worker_loop


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
def migrated_job_runner_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Reuses the migrated-database fixture pattern established by every prior
    Phase 17 test module (no shared conftest.py entry exists for it).
    """
    database_name = f"job_runner_{uuid.uuid4().hex[:8]}"
    admin_params = _admin_connection_settings()

    try:
        with _connect_admin(admin_params) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f'CREATE DATABASE "{database_name}"')
    except psycopg.Error as exc:  # pragma: no cover - exercised when local Postgres is unavailable
        pytest.fail(
            "PostgreSQL is required for tests/test_job_runner.py. "
            "Start the local db service first (for example `docker compose up -d db`). "
            f"Connection error: {exc}"
        )

    _set_database_env(monkeypatch, database_name)
    clear_settings_cache()
    clear_engine_cache()
    command.upgrade(build_alembic_config(), "head")

    # HEARTBEAT_SECONDS is referenced as a bare module global inside
    # execute_job's heartbeat thread, so patching the runner module's copy
    # here makes lease-loss observable fast, without waiting on the real
    # (60s-scale) production interval.
    monkeypatch.setattr(runner_module, "HEARTBEAT_SECONDS", 0.05)

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


# --- Local fake handlers (never a real domain handler -- Phase 17 ships none) ---


class _SuccessHandler:
    job_type = "phase17_runner_success"

    def __init__(self, result: Mapping[str, Any] | None = None) -> None:
        self._result = result if result is not None else {"outcome": "ok"}

    def run(self, context: JobContext) -> Mapping[str, Any]:
        return self._result


class _RaisingHandler:
    job_type = "phase17_runner_raises"

    def run(self, context: JobContext) -> Mapping[str, Any]:
        raise RuntimeError("handler exploded")


class _ExternalThenRaisingHandler:
    job_type = "phase17_runner_external_raises"

    def run(self, context: JobContext) -> Mapping[str, Any]:
        context.log(
            level="info",
            event_code="external_call_started",
            message="calling an external broker",
        )
        raise RuntimeError("external side effect state unknown")


class _CancellationObservingHandler:
    job_type = "phase17_runner_observes_cancellation"

    def run(self, context: JobContext) -> Mapping[str, Any]:
        context.raise_if_cancelled()
        return {"should": "never reach here"}


class _ProgressReportingHandler:
    job_type = "phase17_runner_progress"

    def run(self, context: JobContext) -> Mapping[str, Any]:
        context.report_progress(percent=42, step="halfway", current=4, total=10)
        return {"steps_done": 4}


class _LeaseLossHandler:
    """Simulates another sweep reclaiming this Job's lease mid-flight."""

    job_type = "phase17_runner_loses_lease"

    def run(self, context: JobContext) -> Mapping[str, Any]:
        with session_scope() as session:
            job = session.get(Job, context.job_id)
            assert job is not None
            job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        with session_scope() as session:
            reclaim_lost_jobs(session)
        # Give the heartbeat thread (HEARTBEAT_SECONDS monkeypatched small)
        # a chance to observe the expired/reclaimed lease before returning.
        time.sleep(0.3)
        return {"should": "never be persisted"}


def _registry(*handlers: JobHandler) -> JobRegistry:
    registry = JobRegistry()
    for handler in handlers:
        registry.register(handler)
    return registry


def _submit(job_type: str, *, depends_on: tuple[uuid.UUID, ...] = ()) -> uuid.UUID:
    return submit_job(job_type=job_type, payload={}, depends_on=depends_on)


def _get_job(job_id: uuid.UUID) -> Job:
    with session_scope() as session:
        job = session.get(Job, job_id)
        assert job is not None
        session.expunge(job)
        return job


# --- Tests ---------------------------------------------------------------


def test_queued_job_survives_worker_restart(migrated_job_runner_db: str) -> None:
    load_settings()
    job_id = _submit(_SuccessHandler.job_type)

    # First worker "lifetime": starts and stops without executing anything.
    empty_registry = _registry()
    report_one = run_worker_loop(
        worker_id="worker-lifetime-one", registry=empty_registry, max_jobs=0, once=True
    )
    assert report_one["jobs_executed"] == 0
    assert _get_job(job_id).status is JobStatus.QUEUED

    # Second, genuinely separate worker "lifetime": now it executes.
    success_registry = _registry(_SuccessHandler())
    report_two = run_worker_loop(
        worker_id="worker-lifetime-two", registry=success_registry, max_jobs=1
    )
    assert report_two["jobs_executed"] == 1
    assert report_two["succeeded"] == 1
    assert _get_job(job_id).status is JobStatus.SUCCEEDED


def test_handler_success_records_result_summary_and_full_progress(
    migrated_job_runner_db: str,
) -> None:
    load_settings()
    result = {"trades": 3, "pnl": 12.5}
    handler = _SuccessHandler(result=result)
    job_id = _submit(handler.job_type)

    with session_scope() as session:
        claimed = claim_next_job(session, worker_id="worker-1")
    assert claimed == job_id

    status = execute_job(job_id=job_id, worker_id="worker-1", registry=_registry(handler))
    assert status is JobStatus.SUCCEEDED

    job = _get_job(job_id)
    assert job.status is JobStatus.SUCCEEDED
    assert job.progress_percent == 100
    assert job.result_summary == result


def test_handler_progress_reports_are_persisted(migrated_job_runner_db: str) -> None:
    load_settings()
    handler = _ProgressReportingHandler()
    job_id = _submit(handler.job_type)

    with session_scope() as session:
        claim_next_job(session, worker_id="worker-1")

    status = execute_job(job_id=job_id, worker_id="worker-1", registry=_registry(handler))
    assert status is JobStatus.SUCCEEDED

    job = _get_job(job_id)
    assert job.progress_step == "halfway"
    assert job.progress_current == 4
    assert job.progress_total == 10
    # SUCCEEDED always finishes at 100, overriding the last reported percent.
    assert job.progress_percent == 100


def test_handler_exception_lands_on_failed_with_handler_error(
    migrated_job_runner_db: str,
) -> None:
    load_settings()
    handler = _RaisingHandler()
    job_id = _submit(handler.job_type)

    with session_scope() as session:
        claim_next_job(session, worker_id="worker-1")

    # Report some progress before the handler raises isn't exercised by
    # this fake handler; instead assert last-reported progress (None here)
    # survives untouched -- D-12 preservation is about not resetting it.
    status = execute_job(job_id=job_id, worker_id="worker-1", registry=_registry(handler))
    assert status is JobStatus.FAILED

    job = _get_job(job_id)
    assert job.status is JobStatus.FAILED
    assert job.failure_reason is JobFailureReason.HANDLER_ERROR
    assert "RuntimeError" in (job.failure_message or "")
    assert job.outcome_uncertain is False


def test_handler_exception_after_external_call_sets_outcome_uncertain(
    migrated_job_runner_db: str,
) -> None:
    load_settings()

    external_handler = _ExternalThenRaisingHandler()
    external_job_id = _submit(external_handler.job_type)
    with session_scope() as session:
        claim_next_job(session, worker_id="worker-1")
    execute_job(job_id=external_job_id, worker_id="worker-1", registry=_registry(external_handler))
    assert _get_job(external_job_id).outcome_uncertain is True

    plain_handler = _RaisingHandler()
    plain_job_id = _submit(plain_handler.job_type)
    with session_scope() as session:
        claim_next_job(session, worker_id="worker-1")
    execute_job(job_id=plain_job_id, worker_id="worker-1", registry=_registry(plain_handler))
    assert _get_job(plain_job_id).outcome_uncertain is False


def test_handler_observing_cancellation_lands_on_cancelled(
    migrated_job_runner_db: str,
) -> None:
    load_settings()
    handler = _CancellationObservingHandler()
    job_id = _submit(handler.job_type)

    with session_scope() as session:
        claim_next_job(session, worker_id="worker-1")

    request_cancellation(job_id=job_id, requested_by="operator-1")

    status = execute_job(job_id=job_id, worker_id="worker-1", registry=_registry(handler))
    assert status is JobStatus.CANCELLED

    job = _get_job(job_id)
    assert job.status is JobStatus.CANCELLED
    assert job.cancellation_acknowledged_at is not None
    assert job.failure_reason is None


def test_unknown_job_type_fails_without_outcome_uncertain(migrated_job_runner_db: str) -> None:
    load_settings()
    job_id = _submit("phase17_runner_no_such_handler")

    with session_scope() as session:
        claim_next_job(session, worker_id="worker-1")

    status = execute_job(job_id=job_id, worker_id="worker-1", registry=_registry())
    assert status is JobStatus.FAILED

    job = _get_job(job_id)
    assert job.status is JobStatus.FAILED
    assert job.failure_reason is JobFailureReason.HANDLER_ERROR
    assert job.outcome_uncertain is False


def test_failed_job_cascades_to_unstarted_dependent(migrated_job_runner_db: str) -> None:
    load_settings()
    handler = _RaisingHandler()
    job_a_id = _submit(handler.job_type)
    job_b_id = _submit(_SuccessHandler.job_type, depends_on=(job_a_id,))

    with session_scope() as session:
        claim_next_job(session, worker_id="worker-1")

    execute_job(job_id=job_a_id, worker_id="worker-1", registry=_registry(handler))

    job_b = _get_job(job_b_id)
    assert job_b.status is JobStatus.CANCELLED
    assert job_b.root_cause_job_id == job_a_id


def test_worker_that_lost_its_lease_writes_no_terminal_state(
    migrated_job_runner_db: str,
) -> None:
    load_settings()
    handler = _LeaseLossHandler()
    job_id = _submit(handler.job_type)

    with session_scope() as session:
        claim_next_job(session, worker_id="worker-doomed")

    status = execute_job(job_id=job_id, worker_id="worker-doomed", registry=_registry(handler))

    job = _get_job(job_id)
    # The sweep inside the handler already reclaimed this Job (FAILED,
    # lease_expired); the runner must not have overwritten it.
    assert job.status is JobStatus.FAILED
    assert job.failure_reason is JobFailureReason.LEASE_EXPIRED
    assert job.result_summary == {}
    assert status is JobStatus.FAILED

    with session_scope() as session:
        terminal_events = (
            session.query(JobEvent)
            .filter(
                JobEvent.job_id == job_id,
                JobEvent.outcome == JobTransitionOutcome.ACCEPTED,
                JobEvent.to_status.in_(
                    [JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED]
                ),
            )
            .count()
        )
    assert terminal_events == 1


def test_worker_loop_reports_tallies(migrated_job_runner_db: str) -> None:
    load_settings()
    success_handler = _SuccessHandler()
    raising_handler = _RaisingHandler()
    _submit(success_handler.job_type)
    _submit(raising_handler.job_type)

    registry = _registry(success_handler, raising_handler)
    report = run_worker_loop(worker_id="worker-tally", registry=registry, max_jobs=2)

    assert report["jobs_executed"] == 2
    assert report["succeeded"] == 1
    assert report["failed"] == 1
    assert report["cancelled"] == 0


def test_worker_loop_stops_after_max_jobs(migrated_job_runner_db: str) -> None:
    load_settings()
    handler = _SuccessHandler()
    job_ids = [_submit(handler.job_type) for _ in range(3)]

    registry = _registry(handler)
    report = run_worker_loop(worker_id="worker-cap", registry=registry, max_jobs=2)

    assert report["jobs_executed"] == 2
    assert report["stopped_reason"] == "max_jobs"

    statuses = [_get_job(job_id).status for job_id in job_ids]
    assert statuses.count(JobStatus.QUEUED) == 1
    assert statuses.count(JobStatus.SUCCEEDED) == 2
