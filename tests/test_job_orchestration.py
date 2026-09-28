"""Real-PostgreSQL invariants for transport-independent Job orchestration."""

from __future__ import annotations

import os
import sys
import threading
import uuid
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime, timedelta
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
from trading_platform.db.models import Job, JobDependency, JobEvent, JobMutation, JobStatus
from trading_platform.db.session import clear_engine_cache, session_scope
from trading_platform.jobs.contracts import JobContext
from trading_platform.jobs.queue import claim_next_job
from trading_platform.jobs.registry import (
    InvalidJobPayloadError,
    JobCancellationMode,
    JobRegistry,
)
from trading_platform.orchestration.job_mutations import (
    RETRY_ENDPOINT_ID,
    IdempotencyConflictError,
    InvalidCancellationReasonError,
    InvalidIdempotencyKeyError,
    InvalidRetryPayloadError,
    JobMutationNotFoundError,
    JobNotCancellableRunningError,
    JobNotRetryableError,
    JobOrchestrationService,
    JobTerminalConflictError,
    MissingIdempotencyKeyError,
    RetryAlreadyExistsError,
    RetryBlockedError,
    UnknownJobTypeForSubmissionError,
)


class _ProbeHandler:
    job_type = "phase18_e2e_probe"

    def run(self, context: JobContext) -> Mapping[str, Any]:
        return {"message": "done"}


class _ProbeSubmissionSpec:
    job_type = _ProbeHandler.job_type
    description = "Probe submission spec for orchestration service invariants."
    cancellation_mode = JobCancellationMode.STEP_BOUNDARY

    def validate_payload(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        message = payload.get("message")
        if message not in {"hello", "again"}:
            raise InvalidJobPayloadError(job_type=self.job_type, reason="message is not accepted")
        return {"message": message}

    def submission_defaults(self) -> None:
        return None


def _registry(*, with_spec: bool = True) -> JobRegistry:
    registry = JobRegistry()
    registry.register(_ProbeHandler(), submission_spec=_ProbeSubmissionSpec() if with_spec else None)
    return registry


class _QueuedOnlyHandler:
    job_type = "phase20_10_queued_only_probe"

    def run(self, context: JobContext) -> Mapping[str, Any]:
        return {"message": "done"}


class _QueuedOnlySpec:
    job_type = _QueuedOnlyHandler.job_type
    description = "Queued-only-cancellable probe submission spec for D-02 invariants."
    cancellation_mode = JobCancellationMode.QUEUED_ONLY

    def validate_payload(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return dict(payload)

    def submission_defaults(self) -> None:
        return None


def _registry_with_queued_only() -> JobRegistry:
    registry = _registry()
    registry.register(_QueuedOnlyHandler(), submission_spec=_QueuedOnlySpec())
    return registry


def _service_with_queued_only() -> JobOrchestrationService:
    return JobOrchestrationService(load_settings(), _registry_with_queued_only())


_PREREQUISITE_JOB_TYPE = "phase20_10_prereq_probe"


class _RetryProbeHandler:
    job_type = "phase20_10_retry_probe"

    def run(self, context: JobContext) -> Mapping[str, Any]:
        return {"message": "done"}


class _RetryProbeSubmissionSpec:
    job_type = _RetryProbeHandler.job_type
    description = "Retry probe submission spec declaring a D-19 retry prerequisite."
    cancellation_mode = JobCancellationMode.STEP_BOUNDARY
    retry_prerequisite_job_type = _PREREQUISITE_JOB_TYPE

    def validate_payload(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return dict(payload)

    def submission_defaults(self) -> None:
        return None


def _registry_with_retry_prerequisite() -> JobRegistry:
    registry = _registry()
    registry.register(_RetryProbeHandler(), submission_spec=_RetryProbeSubmissionSpec())
    return registry


def _service_with_retry_prerequisite() -> JobOrchestrationService:
    return JobOrchestrationService(load_settings(), _registry_with_retry_prerequisite())


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


def _seed_job(
    *,
    status: JobStatus,
    job_type: str = _ProbeHandler.job_type,
    payload: Mapping[str, Any] | None = None,
    completed_at: datetime | None = None,
    outcome_uncertain: bool = False,
) -> uuid.UUID:
    with session_scope(load_settings()) as session:
        job = Job(
            job_type=job_type,
            payload=dict(payload) if payload is not None else {"message": "hello"},
            status=status,
            completed_at=completed_at,
            outcome_uncertain=outcome_uncertain,
        )
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


@pytest.mark.usefixtures("migrated_job_orchestration_db")
def test_cancel_running_queued_only_job_is_rejected_before_begin_nested() -> None:
    job_id = _seed_job(status=JobStatus.RUNNING, job_type=_QueuedOnlyHandler.job_type)
    service = _service_with_queued_only()
    before = _counts()

    with pytest.raises(JobNotCancellableRunningError) as exc_info:
        service.cancel(job_id=job_id, reason=None, idempotency_key="queued-only-running")

    assert exc_info.value.job_id == job_id
    assert _counts() == before
    with session_scope(load_settings()) as session:
        job = session.get(Job, job_id)
        assert job is not None
        assert job.status is JobStatus.RUNNING
        assert job.cancellation_requested_at is None


@pytest.mark.usefixtures("migrated_job_orchestration_db")
def test_cancel_queued_queued_only_job_still_transitions_to_cancelled() -> None:
    job_id = _seed_job(status=JobStatus.QUEUED, job_type=_QueuedOnlyHandler.job_type)
    service = _service_with_queued_only()

    result = service.cancel(job_id=job_id, reason=None, idempotency_key="queued-only-queued")

    assert result.reference.status == "cancelled"
    with session_scope(load_settings()) as session:
        job = session.get(Job, job_id)
        assert job is not None
        assert job.status is JobStatus.CANCELLED


@pytest.mark.usefixtures("migrated_job_orchestration_db")
def test_cancel_running_step_boundary_job_remains_cooperative_alongside_queued_only_type() -> None:
    job_id = _seed_job(status=JobStatus.RUNNING, job_type=_ProbeHandler.job_type)
    service = _service_with_queued_only()

    result = service.cancel(job_id=job_id, reason="stop", idempotency_key="step-boundary-running")

    assert result.reference.status == "running"
    with session_scope(load_settings()) as session:
        job = session.get(Job, job_id)
        assert job is not None
        assert job.cancellation_requested_at is not None


@pytest.mark.usefixtures("migrated_job_orchestration_db")
def test_cancel_running_unregistered_job_type_is_not_treated_as_queued_only() -> None:
    job_id = _seed_job(status=JobStatus.RUNNING, job_type="totally_unregistered_job_type")
    service = _service_with_queued_only()

    result = service.cancel(job_id=job_id, reason=None, idempotency_key="unregistered-running")

    assert result.reference.status == "running"
    with session_scope(load_settings()) as session:
        job = session.get(Job, job_id)
        assert job is not None
        assert job.cancellation_requested_at is not None


@pytest.mark.usefixtures("migrated_job_orchestration_db")
def test_cancel_race_against_claim_resolves_to_exactly_one_outcome() -> None:
    """D-02: a cancel racing a worker claim on the same QUEUED queued-only Job
    resolves, in each of 10 barrier-synchronized iterations, to exactly one of
    {CANCELLED and unclaimed, RUNNING and rejected} -- never RUNNING with
    cancellation_requested_at set.
    """

    service = _service_with_queued_only()

    for iteration in range(10):
        job_id = _seed_job(status=JobStatus.QUEUED, job_type=_QueuedOnlyHandler.job_type)
        barrier = threading.Barrier(2)
        results: dict[str, object] = {}

        def do_claim() -> None:
            barrier.wait(timeout=5)
            with session_scope(load_settings()) as session:
                claimed = claim_next_job(session, worker_id="race-worker")
            results["claimed"] = claimed

        def do_cancel() -> None:
            barrier.wait(timeout=5)
            try:
                service.cancel(
                    job_id=job_id,
                    reason=None,
                    idempotency_key=f"race-key-{iteration}",
                )
                results["cancel_outcome"] = "accepted"
            except JobNotCancellableRunningError:
                results["cancel_outcome"] = "rejected"

        threads = [threading.Thread(target=do_claim), threading.Thread(target=do_cancel)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        assert all(not thread.is_alive() for thread in threads)

        with session_scope(load_settings()) as session:
            job = session.get(Job, job_id)
            assert job is not None
            final_status = job.status
            cancellation_requested_at = job.cancellation_requested_at

        claimed = results.get("claimed")
        cancel_outcome = results.get("cancel_outcome")

        cancelled_and_unclaimed = final_status is JobStatus.CANCELLED and claimed is None
        running_and_rejected = (
            final_status is JobStatus.RUNNING
            and cancel_outcome == "rejected"
            and cancellation_requested_at is None
        )
        assert cancelled_and_unclaimed or running_and_rejected, (
            iteration,
            final_status,
            claimed,
            cancel_outcome,
            cancellation_requested_at,
        )
        assert not (final_status is JobStatus.RUNNING and cancellation_requested_at is not None)


@pytest.mark.usefixtures("migrated_job_orchestration_db")
@pytest.mark.parametrize("original_status", [JobStatus.FAILED, JobStatus.CANCELLED])
def test_retry_failed_and_cancelled_originals_create_linked_queued_job(
    original_status: JobStatus,
) -> None:
    service = _service()
    original_id = _seed_job(status=original_status, payload={"message": "hello"})

    retried = service.retry(job_id=original_id, idempotency_key="retry-key")

    assert retried.created is True
    assert retried.replayed is False
    assert retried.reference.status == "queued"
    new_job_id = uuid.UUID(retried.reference.job_id)

    with session_scope(load_settings()) as session:
        new_job = session.get(Job, new_job_id)
        assert new_job is not None
        assert new_job.job_type == _ProbeHandler.job_type
        assert new_job.payload == {"message": "hello"}
        assert new_job.retry_of_job_id == original_id
        assert new_job.status is JobStatus.QUEUED
        dependency_count = session.scalar(
            select(func.count()).select_from(JobDependency).where(JobDependency.job_id == new_job_id)
        )
        assert dependency_count == 0
        mutation = session.execute(
            select(JobMutation).where(
                JobMutation.endpoint_id == RETRY_ENDPOINT_ID,
                JobMutation.idempotency_key == "retry-key",
            )
        ).scalar_one()
        assert mutation.job_id == new_job_id


@pytest.mark.usefixtures("migrated_job_orchestration_db")
def test_retry_chain_links_to_immediate_parent_not_root() -> None:
    service = _service()
    a_id = _seed_job(status=JobStatus.FAILED, payload={"message": "hello"})

    retried_b = service.retry(job_id=a_id, idempotency_key="retry-a")
    b_id = uuid.UUID(retried_b.reference.job_id)

    with session_scope(load_settings()) as session:
        job_b = session.get(Job, b_id)
        assert job_b is not None
        job_b.status = JobStatus.FAILED
        job_b.completed_at = datetime.now(UTC)

    retried_c = service.retry(job_id=b_id, idempotency_key="retry-b")
    c_id = uuid.UUID(retried_c.reference.job_id)

    with session_scope(load_settings()) as session:
        job_c = session.get(Job, c_id)
        assert job_c is not None
        assert job_c.retry_of_job_id == b_id
        assert job_c.retry_of_job_id != a_id


@pytest.mark.usefixtures("migrated_job_orchestration_db")
def test_retry_replay_same_key_and_conflict_on_different_target() -> None:
    service = _service()
    failed_id = _seed_job(status=JobStatus.FAILED, payload={"message": "hello"})
    other_failed_id = _seed_job(status=JobStatus.FAILED, payload={"message": "hello"})

    created = service.retry(job_id=failed_id, idempotency_key="shared-retry-key")
    replayed = service.retry(job_id=failed_id, idempotency_key="shared-retry-key")
    assert replayed.replayed is True
    assert replayed.reference.job_id == created.reference.job_id

    with pytest.raises(IdempotencyConflictError) as exc_info:
        service.retry(job_id=other_failed_id, idempotency_key="shared-retry-key")
    assert exc_info.value.original_job_id == created.reference.job_id


@pytest.mark.usefixtures("migrated_job_orchestration_db")
@pytest.mark.parametrize("status", [JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.SUCCEEDED])
def test_retry_rejects_non_terminal_original_and_writes_nothing(status: JobStatus) -> None:
    service = _service()
    job_id = _seed_job(status=status, payload={"message": "hello"})
    before = _counts()

    with pytest.raises(JobNotRetryableError) as exc_info:
        service.retry(job_id=job_id, idempotency_key=f"retry-{status.value}")

    assert exc_info.value.status == status.value
    assert _counts() == before


@pytest.mark.usefixtures("migrated_job_orchestration_db")
def test_retry_second_fresh_key_retry_conflicts_with_existing_retry() -> None:
    service = _service()
    failed_id = _seed_job(status=JobStatus.FAILED, payload={"message": "hello"})

    first = service.retry(job_id=failed_id, idempotency_key="first-retry-key")

    with pytest.raises(RetryAlreadyExistsError) as exc_info:
        service.retry(job_id=failed_id, idempotency_key="second-retry-key")

    assert exc_info.value.job_id == failed_id
    assert exc_info.value.existing_retry_job_id == uuid.UUID(first.reference.job_id)


@pytest.mark.usefixtures("migrated_job_orchestration_db")
def test_concurrent_fresh_key_retries_create_exactly_one_retry_job() -> None:
    failed_id = _seed_job(status=JobStatus.FAILED, payload={"message": "hello"})
    barrier = threading.Barrier(2)
    results: list[object] = []

    def do_retry(key: str) -> None:
        barrier.wait(timeout=5)
        try:
            results.append(_service().retry(job_id=failed_id, idempotency_key=key))
        except Exception as exc:  # pragma: no cover - assertion below reports unexpected failures
            results.append(exc)

    threads = [
        threading.Thread(target=do_retry, args=("concurrent-retry-a",)),
        threading.Thread(target=do_retry, args=("concurrent-retry-b",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert all(not thread.is_alive() for thread in threads)
    assert sum(isinstance(result, RetryAlreadyExistsError) for result in results) == 1
    assert sum(not isinstance(result, Exception) for result in results) == 1

    with session_scope(load_settings()) as session:
        retry_count = session.scalar(
            select(func.count()).select_from(Job).where(Job.retry_of_job_id == failed_id)
        )
    assert retry_count == 1


@pytest.mark.usefixtures("migrated_job_orchestration_db")
def test_retry_rejects_invalid_stored_payload_and_writes_nothing() -> None:
    service = _service()
    job_id = _seed_job(status=JobStatus.FAILED, payload={"message": "not-accepted"})
    before = _counts()

    with pytest.raises(InvalidRetryPayloadError) as exc_info:
        service.retry(job_id=job_id, idempotency_key="retry-invalid-payload")

    assert exc_info.value.job_id == job_id
    assert exc_info.value.reason == "message is not accepted"
    assert _counts() == before


@pytest.mark.usefixtures("migrated_job_orchestration_db")
def test_retry_unregistered_job_type_raises_and_writes_nothing() -> None:
    service = _service()
    job_id = _seed_job(status=JobStatus.FAILED, job_type="totally_unregistered_job_type")
    before = _counts()

    with pytest.raises(UnknownJobTypeForSubmissionError) as exc_info:
        service.retry(job_id=job_id, idempotency_key="retry-unregistered")

    assert exc_info.value.job_type == "totally_unregistered_job_type"
    assert _counts() == before


def _d19_time(offset_seconds: int) -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=offset_seconds)


@pytest.mark.usefixtures("migrated_job_orchestration_db")
@pytest.mark.parametrize(
    ("case", "expect_blocked"),
    [
        ("no_prerequisite_job", True),
        ("prerequisite_succeeded_same_strategy_completed_later", False),
        ("prerequisite_succeeded_other_strategy", True),
        ("prerequisite_succeeded_completed_earlier", True),
        ("prerequisite_failed", True),
        ("original_not_uncertain", False),
        ("original_cancelled", False),
        ("spec_without_prerequisite", False),
    ],
)
def test_retry_block_d19_matrix(case: str, expect_blocked: bool) -> None:
    """D-19/D-20: retry_block()/retry() agree on the reconcile-first predicate,
    reading only the jobs table."""

    service = _service_with_retry_prerequisite()
    base_completed_at = _d19_time(1_000)

    if case == "spec_without_prerequisite":
        original_id = _seed_job(
            status=JobStatus.FAILED,
            job_type=_ProbeHandler.job_type,
            payload={"message": "hello", "strategy_id": "strat-a"},
            completed_at=base_completed_at,
            outcome_uncertain=True,
        )
    else:
        original_status = JobStatus.CANCELLED if case == "original_cancelled" else JobStatus.FAILED
        outcome_uncertain = case != "original_not_uncertain"
        original_id = _seed_job(
            status=original_status,
            job_type=_RetryProbeHandler.job_type,
            payload={"message": "hello", "strategy_id": "strat-a"},
            completed_at=base_completed_at,
            outcome_uncertain=outcome_uncertain,
        )

        if case == "prerequisite_succeeded_same_strategy_completed_later":
            _seed_job(
                status=JobStatus.SUCCEEDED,
                job_type=_PREREQUISITE_JOB_TYPE,
                payload={"strategy_id": "strat-a"},
                completed_at=base_completed_at + timedelta(seconds=60),
            )
        elif case == "prerequisite_succeeded_other_strategy":
            _seed_job(
                status=JobStatus.SUCCEEDED,
                job_type=_PREREQUISITE_JOB_TYPE,
                payload={"strategy_id": "strat-b"},
                completed_at=base_completed_at + timedelta(seconds=60),
            )
        elif case == "prerequisite_succeeded_completed_earlier":
            _seed_job(
                status=JobStatus.SUCCEEDED,
                job_type=_PREREQUISITE_JOB_TYPE,
                payload={"strategy_id": "strat-a"},
                completed_at=base_completed_at - timedelta(seconds=60),
            )
        elif case == "prerequisite_failed":
            _seed_job(
                status=JobStatus.FAILED,
                job_type=_PREREQUISITE_JOB_TYPE,
                payload={"strategy_id": "strat-a"},
                completed_at=base_completed_at + timedelta(seconds=60),
                outcome_uncertain=True,
            )
        # "no_prerequisite_job" and "original_not_uncertain"/"original_cancelled"
        # seed no prerequisite Job row.

    before = _counts()
    block = service.retry_block(job_id=original_id)
    # retry_block() is read-only (D-19/D-20): it must never write, whether or
    # not it finds a block.
    assert _counts() == before

    if expect_blocked:
        assert block is not None
        assert block.code == "reconciliation_required"
        assert block.required_job_type == _PREREQUISITE_JOB_TYPE
        assert block.strategy_id == "strat-a"
        with pytest.raises(RetryBlockedError) as exc_info:
            service.retry(job_id=original_id, idempotency_key=f"retry-{case}")
        assert exc_info.value.block == block
        # A blocked retry() rejects before session.begin_nested() -- zero rows.
        assert _counts() == before
    else:
        assert block is None
        result = service.retry(job_id=original_id, idempotency_key=f"retry-{case}")
        assert result.created is True


@pytest.mark.usefixtures("migrated_job_orchestration_db")
def test_retry_block_missing_job_raises() -> None:
    service = _service_with_retry_prerequisite()
    with pytest.raises(JobMutationNotFoundError):
        service.retry_block(job_id=uuid.uuid4())
