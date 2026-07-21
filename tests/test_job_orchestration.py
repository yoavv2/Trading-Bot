"""Real-PostgreSQL invariants for transport-independent Job orchestration."""

from __future__ import annotations

import os
import sys
import threading
import uuid
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any
from unittest import mock

import psycopg
import pytest
from alembic import command
from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.migrate import build_alembic_config

from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.db.models import Job, JobEvent, JobMutation, JobStatus
from trading_platform.db.session import clear_engine_cache, session_scope
from trading_platform.jobs.contracts import JobContext
from trading_platform.jobs.registry import InvalidJobPayloadError, JobRegistry
from trading_platform.orchestration.job_mutations import (
    IdempotencyConflictError,
    InvalidCancellationReasonError,
    InvalidIdempotencyKeyError,
    JobMutationNotFoundError,
    JobOrchestrationService,
    JobTerminalConflictError,
    MissingIdempotencyKeyError,
    UnknownJobTypeForSubmissionError,
)


class _ProbeHandler:
    job_type = "phase18_e2e_probe"

    def run(self, context: JobContext) -> Mapping[str, Any]:
        return {"message": "done"}


class _ProbeSubmissionSpec:
    job_type = _ProbeHandler.job_type

    def validate_payload(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        message = payload.get("message")
        if message not in {"hello", "again"}:
            raise InvalidJobPayloadError(job_type=self.job_type, reason="message is not accepted")
        return {"message": message}


def _registry(*, with_spec: bool = True) -> JobRegistry:
    registry = JobRegistry()
    registry.register(_ProbeHandler(), submission_spec=_ProbeSubmissionSpec() if with_spec else None)
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
    params = params or _admin_connection_settings()
    return psycopg.connect(**params, autocommit=True)


def _set_database_env(monkeypatch: pytest.MonkeyPatch, database_name: str) -> None:
    for key, value in _admin_connection_settings().items():
        if key != "dbname":
            monkeypatch.setenv(f"TRADING_PLATFORM_DATABASE__{key.upper()}", value)
    monkeypatch.setenv("TRADING_PLATFORM_DATABASE__NAME", database_name)


@pytest.fixture()
def migrated_job_orchestration_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    database_name = f"job_orchestration_{uuid.uuid4().hex[:8]}"
    admin_params = _admin_connection_settings()
    try:
        with _connect_admin(admin_params) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f'CREATE DATABASE "{database_name}"')
    except psycopg.Error as exc:  # pragma: no cover
        pytest.fail(f"PostgreSQL is required for job orchestration tests: {exc}")

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


def _service() -> JobOrchestrationService:
    return JobOrchestrationService(load_settings(), _registry())


def _counts() -> tuple[int, int, int]:
    with session_scope(load_settings()) as session:
        return (
            session.scalar(select(func.count()).select_from(Job)) or 0,
            session.scalar(select(func.count()).select_from(JobMutation)) or 0,
            session.scalar(select(func.count()).select_from(JobEvent)) or 0,
        )


@pytest.mark.usefixtures("migrated_job_orchestration_db")
def test_submit_rejects_invalid_payload_before_session_entry_and_writes_nothing() -> None:
    with mock.patch(
        "trading_platform.orchestration.job_mutations.session_scope",
        side_effect=AssertionError("session_scope must not be entered"),
    ):
        with pytest.raises(InvalidJobPayloadError) as exc_info:
            _service().submit(
                job_type=_ProbeHandler.job_type,
                payload={"message": "goodbye"},
                idempotency_key="invalid-payload",
            )
    assert exc_info.value.job_type == _ProbeHandler.job_type
    assert _counts() == (0, 0, 0)


@pytest.mark.usefixtures("migrated_job_orchestration_db")
def test_submit_validation_failures_leave_all_rows_empty() -> None:
    service = _service()
    with pytest.raises(MissingIdempotencyKeyError):
        service.submit(job_type=_ProbeHandler.job_type, payload={"message": "hello"}, idempotency_key=None)
    with pytest.raises(InvalidIdempotencyKeyError):
        service.submit(job_type=_ProbeHandler.job_type, payload={"message": "hello"}, idempotency_key=" ")
    with pytest.raises(InvalidIdempotencyKeyError):
        service.submit(job_type=_ProbeHandler.job_type, payload={"message": "hello"}, idempotency_key="x" * 256)
    with pytest.raises(UnknownJobTypeForSubmissionError):
        service.submit(job_type="missing", payload={"message": "hello"}, idempotency_key="unknown")
    with pytest.raises(UnknownJobTypeForSubmissionError):
        JobOrchestrationService(load_settings(), _registry(with_spec=False)).submit(
            job_type=_ProbeHandler.job_type,
            payload={"message": "hello"},
            idempotency_key="runner-only",
        )
    assert _counts() == (0, 0, 0)


@pytest.mark.usefixtures("migrated_job_orchestration_db")
def test_submit_replays_equivalent_payload_and_conflicts_on_changed_identity() -> None:
    service = _service()
    created = service.submit(
        job_type=_ProbeHandler.job_type,
        payload={"message": "hello"},
        idempotency_key="stable-key",
    )
    replayed = service.submit(
        job_type=_ProbeHandler.job_type,
        payload={"message": "hello"},
        idempotency_key="stable-key",
    )

    assert created.created is True
    assert replayed.replayed is True
    assert replayed.reference.job_id == created.reference.job_id
    assert _counts() == (1, 1, 1)

    with pytest.raises(IdempotencyConflictError) as exc_info:
        service.submit(
            job_type=_ProbeHandler.job_type,
            payload={"message": "again"},
            idempotency_key="stable-key",
        )
    assert exc_info.value.original_job_id == created.reference.job_id
    assert _counts() == (1, 1, 1)


@pytest.mark.usefixtures("migrated_job_orchestration_db")
def test_concurrent_same_key_submission_has_one_persisted_mutation() -> None:
    barrier = threading.Barrier(2)
    results: list[object] = []

    def submit() -> None:
        barrier.wait(timeout=5)
        try:
            results.append(
                _service().submit(
                    job_type=_ProbeHandler.job_type,
                    payload={"message": "hello"},
                    idempotency_key="concurrent-key",
                )
            )
        except Exception as exc:  # pragma: no cover - assertion below reports unexpected failures
            results.append(exc)

    threads = [threading.Thread(target=submit), threading.Thread(target=submit)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert all(not thread.is_alive() for thread in threads)
    assert all(not isinstance(result, Exception) for result in results), results
    assert len({result.reference.job_id for result in results}) == 1  # type: ignore[union-attr]
    assert _counts() == (1, 1, 1)


def _seed_job(*, status: JobStatus) -> uuid.UUID:
    with session_scope(load_settings()) as session:
        job = Job(job_type=_ProbeHandler.job_type, payload={"message": "hello"}, status=status)
        session.add(job)
        session.flush()
        return job.id


@pytest.mark.usefixtures("migrated_job_orchestration_db")
def test_cancel_emits_compact_reference_and_preserves_first_request_facts() -> None:
    job_id = _seed_job(status=JobStatus.QUEUED)
    service = _service()

    cancelled = service.cancel(job_id=job_id, reason="  maintenance  ", idempotency_key="cancel-key")
    assert cancelled.reference.to_dict() == {
        "job_id": str(job_id),
        "job_type": _ProbeHandler.job_type,
        "status": "cancelled",
        "links": {
            "self": f"/api/v1/jobs/{job_id}",
            "progress": f"/api/v1/jobs/{job_id}/progress",
            "logs": f"/api/v1/jobs/{job_id}/logs",
            "events": f"/api/v1/jobs/{job_id}/events",
        },
    }

    replayed = service.cancel(job_id=job_id, reason="maintenance", idempotency_key="cancel-key")
    fresh_repeat = service.cancel(job_id=job_id, reason="replacement", idempotency_key="fresh-cancel-key")
    assert replayed.replayed is True
    assert fresh_repeat.replayed is False
    with session_scope(load_settings()) as session:
        job = session.get(Job, job_id)
        assert job is not None
        assert job.cancellation_reason == "maintenance"
        assert session.scalar(select(func.count()).select_from(JobMutation)) == 2
        assert session.scalar(select(func.count()).select_from(JobEvent)) == 1


@pytest.mark.usefixtures("migrated_job_orchestration_db")
def test_cancel_running_delegates_once_and_rejects_invalid_or_terminal_mutations() -> None:
    running_job_id = _seed_job(status=JobStatus.RUNNING)
    service = _service()
    running = service.cancel(job_id=running_job_id, reason="  stop now  ", idempotency_key="running-key")
    assert running.reference.status == "running"
    service.cancel(job_id=running_job_id, reason="ignored", idempotency_key="fresh-running-key")
    with session_scope(load_settings()) as session:
        job = session.get(Job, running_job_id)
        assert job is not None
        assert job.cancellation_reason == "stop now"
        assert session.scalar(select(func.count()).select_from(JobEvent)) == 1

    terminal_job_id = _seed_job(status=JobStatus.SUCCEEDED)
    before_mutations = _counts()[1]
    with pytest.raises(JobTerminalConflictError) as terminal:
        service.cancel(job_id=terminal_job_id, reason=None, idempotency_key="terminal-key")
    assert terminal.value.status == "succeeded"
    with pytest.raises(JobMutationNotFoundError):
        service.cancel(job_id=uuid.uuid4(), reason=None, idempotency_key="missing-key")
    with pytest.raises(InvalidCancellationReasonError):
        service.cancel(job_id=terminal_job_id, reason="x" * 501, idempotency_key="too-long")
    assert _counts()[1] == before_mutations


@pytest.mark.usefixtures("migrated_job_orchestration_db")
def test_cancellation_replay_is_endpoint_scoped_and_reads_current_status() -> None:
    job_id = _seed_job(status=JobStatus.QUEUED)
    service = _service()
    service.cancel(job_id=job_id, reason=None, idempotency_key="shared-key")
    with session_scope(load_settings()) as session:
        job = session.get(Job, job_id)
        assert job is not None
        job.status = JobStatus.SUCCEEDED

    replayed = service.cancel(job_id=job_id, reason=None, idempotency_key="shared-key")
    assert replayed.replayed is True
    assert replayed.reference.status == "succeeded"
    with pytest.raises(IdempotencyConflictError) as conflict:
        service.cancel(job_id=uuid.uuid4(), reason=None, idempotency_key="shared-key")
    assert conflict.value.original_job_id == str(job_id)


@pytest.mark.usefixtures("migrated_job_orchestration_db")
def test_concurrent_changed_submission_rolls_back_the_losing_candidate() -> None:
    barrier = threading.Barrier(2)
    results: list[object] = []

    def submit(message: str) -> None:
        barrier.wait(timeout=5)
        try:
            results.append(
                _service().submit(
                    job_type=_ProbeHandler.job_type,
                    payload={"message": message},
                    idempotency_key="conflicting-concurrent-key",
                )
            )
        except Exception as exc:  # pragma: no cover - assertion below reports unexpected failures
            results.append(exc)

    threads = [
        threading.Thread(target=submit, args=("hello",)),
        threading.Thread(target=submit, args=("again",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert all(not thread.is_alive() for thread in threads)
    assert sum(isinstance(result, IdempotencyConflictError) for result in results) == 1
    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert _counts() == (1, 1, 1)
