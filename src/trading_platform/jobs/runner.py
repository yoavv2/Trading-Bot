"""Job execution: the runner (JOB-02, JOB-03).

This module contains no job-type literals and no domain behavior. Every
Job type it can execute is resolved by a string key (``job_type``) read
from the database and handed to ``JobRegistry.resolve``; the module never
names a concrete job type. That is precisely what makes JOB-03's "adding
a Job type touches zero queue-framework modules" claim true -- this file
is one of the queue-framework modules that must never change to add one.

``execute_job`` resolves the handler for one claimed Job, runs it with no
database session open (mirroring the DB-04/DB-05 convention: external side
effects never run inside an open transaction), and lands every possible
outcome (success, handler exception, cooperative cancellation, unknown job
type, lost lease) on the correct terminal state.
"""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_platform.core.logging import get_logger
from trading_platform.core.settings import Settings
from trading_platform.db.models import Job, JobEventType, JobFailureReason, JobLog, JobStatus
from trading_platform.db.session import session_scope
from trading_platform.jobs import progress as _progress
from trading_platform.jobs.cancellation import acknowledge_cancellation
from trading_platform.jobs.context import DatabaseJobContext
from trading_platform.jobs.contracts import JobCancelledError
from trading_platform.jobs.dependencies import cascade_dependency_outcome
from trading_platform.jobs.lifecycle import JobTransitionRequest, apply_job_transition
from trading_platform.jobs.queue import HEARTBEAT_SECONDS, renew_lease
from trading_platform.jobs.registry import JobRegistry, UnknownJobTypeError

logger = get_logger(__name__)

# Truncation width for a handler-exception failure_message. The full
# traceback is never persisted here -- it is emitted through
# get_logger(...).exception(...), whose JsonLogFormatter sanitizes the
# whole assembled payload (T-17-09-01).
_MAX_FAILURE_MESSAGE_CHARS = 2000


def _job_emitted_external_side_effect_log(session: Session, *, job_id: uuid.UUID) -> bool:
    """D-03: true when the Job emitted at least one ``job_logs`` row whose
    ``event_code`` starts with ``external_`` -- the framework's only signal
    that an external side effect may have occurred before a handler failed,
    so the outcome must be recorded as uncertain rather than definite.
    """

    event_codes = (
        session.execute(select(JobLog.event_code).where(JobLog.job_id == job_id)).scalars().all()
    )
    return any(event_code.startswith("external_") for event_code in event_codes)


def execute_job(
    *,
    job_id: uuid.UUID,
    worker_id: str,
    registry: JobRegistry,
    settings: Settings | None = None,
) -> JobStatus:
    """Execute one claimed Job and land it on the correct terminal state.

    Assumes the caller (normally ``run_worker_loop``) has already claimed
    the Job via ``claim_next_job``, so it is RUNNING with a lease owned by
    ``worker_id``. Resolves the handler by ``job_type`` alone -- the only
    handler-selection call in this module is ``registry.resolve(job_type)``,
    where ``job_type`` came from the database row.

    Outcomes:
      - Unregistered ``job_type``: FAILED / ``HANDLER_ERROR``,
        ``outcome_uncertain=False`` -- nothing ran, so nothing is uncertain.
      - Normal handler return: SUCCEEDED at 100% progress with the handler's
        returned mapping persisted as ``result_summary``.
      - ``JobCancelledError``: CANCELLED via ``acknowledge_cancellation`` --
        acknowledgement, not a direct transition, owns this path (D-08).
      - Any other exception: FAILED / ``HANDLER_ERROR``, with
        ``outcome_uncertain`` forced True when the Job logged an
        ``external_*`` event before failing (D-03).
      - Lost lease (a sweep reclaimed this Job while the handler was still
        running): no terminal transition is written here at all -- the
        sweep already wrote one, and this worker no longer owns the Job.

    On any FAILED or CANCELLED outcome, cascades to unstarted dependents
    (D-04) in the same transaction as the terminal write.
    """

    with session_scope(settings) as session:
        job = session.get(Job, job_id)
        if job is None:
            raise LookupError(f"Job '{job_id}' was not found.")
        job_type = job.job_type
        payload = dict(job.payload)

    try:
        handler = registry.resolve(job_type)
    except UnknownJobTypeError:
        with session_scope(settings) as session:
            apply_job_transition(
                session,
                job_id=job_id,
                request=JobTransitionRequest(
                    event_type=JobEventType.FAILED,
                    failure_reason=JobFailureReason.HANDLER_ERROR,
                    failure_message=f"No handler registered for job type '{job_type}'.",
                    outcome_uncertain=False,
                ),
            )
            cascade_dependency_outcome(session, terminal_job_id=job_id)
        return JobStatus.FAILED

    context = DatabaseJobContext(
        job_id=job_id, job_type=job_type, payload=payload, settings=settings
    )

    # Heartbeat: a daemon thread renews the lease on HEARTBEAT_SECONDS
    # cadence. `HEARTBEAT_SECONDS` is referenced as a bare module global
    # (not captured into a local) so a test can `monkeypatch.setattr` this
    # module's `HEARTBEAT_SECONDS` to a small value and observe a fast
    # lease-loss signal without waiting on the real interval.
    lease_lost = threading.Event()
    stop_heartbeat = threading.Event()

    def _heartbeat_loop() -> None:
        while not stop_heartbeat.wait(HEARTBEAT_SECONDS):
            if not renew_lease(job_id=job_id, worker_id=worker_id, settings=settings):
                lease_lost.set()
                return

    heartbeat_thread = threading.Thread(
        target=_heartbeat_loop, name=f"job-heartbeat-{job_id}", daemon=True
    )
    heartbeat_thread.start()

    outcome_kind: str
    failure_message: str | None = None
    result: Mapping[str, Any] | None = None

    try:
        try:
            # No database session is open across this call -- external
            # side effects (broker calls, file I/O, ...) must never run
            # inside an open transaction, mirroring the DB-04/DB-05
            # convention already enforced in services/execution.
            result = handler.run(context)
            outcome_kind = "success"
        except JobCancelledError:
            outcome_kind = "cancelled"
        except Exception as exc:
            outcome_kind = "error"
            failure_message = f"{type(exc).__name__}: {exc}"[:_MAX_FAILURE_MESSAGE_CHARS]
            logger.exception(
                "job_runner_handler_error",
                extra={"context": {"job_id": str(job_id), "job_type": job_type}},
            )
    finally:
        # Stop and join the heartbeat thread on every exit path so no
        # thread outlives this Job's execution.
        stop_heartbeat.set()
        heartbeat_thread.join()

    if lease_lost.is_set():
        logger.warning(
            "job_runner_lease_lost",
            extra={
                "context": {
                    "job_id": str(job_id),
                    "worker_id": worker_id,
                    "job_type": job_type,
                }
            },
        )
        # A sweep already reclaimed this Job and wrote its terminal state.
        # This worker no longer owns it -- write nothing. Read (never
        # write) the current status purely to report an honest return value.
        with session_scope(settings) as session:
            job = session.get(Job, job_id)
            return job.status if job is not None else JobStatus.FAILED

    if outcome_kind == "success":
        with session_scope(settings) as session:
            job = session.get(Job, job_id)
            if job is None:
                raise LookupError(f"Job '{job_id}' was not found.")
            _progress.mark_completed(job, now=datetime.now(UTC))
            apply_job_transition(
                session,
                job_id=job_id,
                request=JobTransitionRequest(
                    event_type=JobEventType.SUCCEEDED,
                    result_summary=result if result is not None else {},
                ),
            )
        return JobStatus.SUCCEEDED

    if outcome_kind == "cancelled":
        with session_scope(settings) as session:
            acknowledge_cancellation(session, job_id=job_id)
            cascade_dependency_outcome(session, terminal_job_id=job_id)
        return JobStatus.CANCELLED

    # outcome_kind == "error"
    with session_scope(settings) as session:
        outcome_uncertain = _job_emitted_external_side_effect_log(session, job_id=job_id)
        apply_job_transition(
            session,
            job_id=job_id,
            request=JobTransitionRequest(
                event_type=JobEventType.FAILED,
                failure_reason=JobFailureReason.HANDLER_ERROR,
                failure_message=failure_message,
                outcome_uncertain=outcome_uncertain,
            ),
        )
        cascade_dependency_outcome(session, terminal_job_id=job_id)
    return JobStatus.FAILED
