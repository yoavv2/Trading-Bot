"""Job execution: the runner and the restart-safe worker loop (JOB-02, JOB-03).

This module contains no job-type literals and no domain behavior. Every
Job type it can execute is resolved by a string key (``job_type``) read
from the database and handed to ``JobRegistry.resolve``; the module never
names a concrete job type. That is precisely what makes JOB-03's "adding
a Job type touches zero queue-framework modules" claim true -- this file
is one of the queue-framework modules that must never change to add one.

Two responsibilities live here:

1. ``execute_job`` -- resolves the handler for one claimed Job, runs it
   with no database session open (mirroring the DB-04/DB-05 convention:
   external side effects never run inside an open transaction), and lands
   every possible outcome (success, handler exception, cooperative
   cancellation, unknown job type, lost lease) on the correct terminal
   state.
2. ``run_worker_loop`` -- the restart-safe poll loop: sweeps lost leases
   and cancellation timeouts, claims the next ready Job, executes it, and
   repeats until told to stop. This is the execution half of JOB-02 (a
   Job queued before a worker restart survives and runs after it).
"""

from __future__ import annotations

import signal
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
from trading_platform.jobs.cancellation import (
    JobNotCancellableError,
    acknowledge_cancellation,
    sweep_cancellation_timeouts,
)
from trading_platform.jobs.context import DatabaseJobContext
from trading_platform.jobs.contracts import JobCancelledError
from trading_platform.jobs.dependencies import cascade_dependency_outcome
from trading_platform.jobs.lifecycle import (
    IllegalJobTransition,
    JobTransitionRequest,
    apply_job_transition,
)
from trading_platform.jobs.queue import (
    HEARTBEAT_SECONDS,
    POLL_INTERVAL_SECONDS,
    claim_next_job,
    reclaim_lost_jobs,
    renew_lease,
)
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
            try:
                renewed = renew_lease(job_id=job_id, worker_id=worker_id, settings=settings)
            except Exception:
                # renew_lease opens its own session_scope, so a transient DB
                # error would otherwise raise out of this thread target and
                # silently kill all future renewals (thread exceptions do not
                # propagate to the main thread). Log and retry on the next tick
                # instead: a single transient blip must not abandon a healthy
                # Job. If the error persists, the lease lapses and a sweep
                # reclaims the Job -- now tolerated by the terminal-write guard
                # in execute_job -- rather than crashing.
                logger.exception(
                    "job_runner_heartbeat_error",
                    extra={
                        "context": {
                            "job_id": str(job_id),
                            "worker_id": worker_id,
                        }
                    },
                )
                continue
            if not renewed:
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

    # The terminal writes below assume this Job is still RUNNING and owned by
    # this worker. Under concurrency that can be false: another worker's
    # cancellation-timeout sweep or lease reclaim can terminalize this Job
    # within the HEARTBEAT_SECONDS window before this worker's next
    # renew_lease would set lease_lost. The success path then hits
    # SUCCEEDED-from-terminal (IllegalJobTransition), the cancelled path hits
    # a non-RUNNING acknowledge_cancellation (JobNotCancellableError), and the
    # error path hits FAILED-from-terminal (IllegalJobTransition). In every
    # case the Job is already correctly terminal and this worker no longer
    # owns it, so this is not data loss -- treat it exactly like a lost lease:
    # write nothing and report the current status honestly, rather than
    # letting the exception crash the worker process.
    try:
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
    except (IllegalJobTransition, JobNotCancellableError):
        logger.warning(
            "job_runner_concurrently_terminalized",
            extra={
                "context": {
                    "job_id": str(job_id),
                    "worker_id": worker_id,
                    "job_type": job_type,
                    "outcome_kind": outcome_kind,
                }
            },
        )
        with session_scope(settings) as session:
            job = session.get(Job, job_id)
            return job.status if job is not None else JobStatus.FAILED


def run_worker_loop(
    *,
    worker_id: str,
    registry: JobRegistry,
    max_jobs: int | None = None,
    poll_interval_seconds: float = POLL_INTERVAL_SECONDS,
    once: bool = False,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Poll, sweep, claim, and execute Jobs until told to stop.

    Every iteration opens one short transaction that runs
    ``reclaim_lost_jobs`` then ``sweep_cancellation_timeouts`` -- both cheap,
    idempotent, single-query-driven sweeps that are safe on every poll --
    then attempts ``claim_next_job`` in the same transaction. A claimed Job
    is executed via ``execute_job`` outside that transaction.

    Stops after ``max_jobs`` executions when set, after a single pass when
    ``once=True``, or on SIGTERM/SIGINT (the in-flight Job, if any, finishes
    first so no lease is orphaned). Any previously installed SIGTERM/SIGINT
    handlers are restored on exit. When no Job was claimed, sleeps
    ``poll_interval_seconds`` via an interruptible ``threading.Event.wait``
    rather than ``time.sleep``, so a shutdown signal is honored promptly.
    """

    shutdown_event = threading.Event()

    def _handle_shutdown_signal(signum: int, _frame: Any) -> None:
        logger.warning(
            "job_runner_shutdown_signal_received",
            extra={"context": {"signal": signum, "worker_id": worker_id}},
        )
        shutdown_event.set()

    previous_handlers: dict[int, Any] = {}
    for signal_number in (signal.SIGTERM, signal.SIGINT):
        previous_handlers[int(signal_number)] = signal.signal(
            signal_number, _handle_shutdown_signal
        )

    jobs_executed = 0
    succeeded = 0
    failed = 0
    cancelled = 0
    reclaimed_total = 0
    cancellation_timeouts_total = 0
    stopped_reason = "signal"

    try:
        while True:
            if shutdown_event.is_set():
                stopped_reason = "signal"
                break
            if max_jobs is not None and jobs_executed >= max_jobs:
                stopped_reason = "max_jobs"
                break

            with session_scope(settings) as session:
                reclaimed = reclaim_lost_jobs(session)
                timed_out = sweep_cancellation_timeouts(session)
                claimed_job_id = claim_next_job(session, worker_id=worker_id)

            reclaimed_total += len(reclaimed)
            cancellation_timeouts_total += len(timed_out)

            if claimed_job_id is not None:
                # Last-resort net: execute_job already converts the known
                # concurrent-terminalization races (IllegalJobTransition /
                # JobNotCancellableError) into an honest status return, so this
                # broad except only fires on a genuinely unforeseen error. No
                # single Job may crash the poll loop and terminate the worker
                # process -- log it and continue. jobs_executed is still
                # incremented so max_jobs bounds hold and the loop cannot spin
                # forever on one pathological Job.
                try:
                    status = execute_job(
                        job_id=claimed_job_id,
                        worker_id=worker_id,
                        registry=registry,
                        settings=settings,
                    )
                    if status is JobStatus.SUCCEEDED:
                        succeeded += 1
                    elif status is JobStatus.FAILED:
                        failed += 1
                    elif status is JobStatus.CANCELLED:
                        cancelled += 1
                except Exception:
                    logger.exception(
                        "job_runner_execution_crashed",
                        extra={
                            "context": {
                                "job_id": str(claimed_job_id),
                                "worker_id": worker_id,
                            }
                        },
                    )
                jobs_executed += 1

            if once:
                stopped_reason = "once"
                break
            if max_jobs is not None and jobs_executed >= max_jobs:
                stopped_reason = "max_jobs"
                break

            if claimed_job_id is None:
                # Nothing to do this pass -- wait interruptibly rather than
                # busy-polling or blocking a shutdown signal.
                if shutdown_event.wait(poll_interval_seconds):
                    stopped_reason = "signal"
                    break
    finally:
        for restore_signal_number, restore_handler in previous_handlers.items():
            signal.signal(restore_signal_number, restore_handler)

    return {
        "worker_id": worker_id,
        "jobs_executed": jobs_executed,
        "succeeded": succeeded,
        "failed": failed,
        "cancelled": cancelled,
        "reclaimed": reclaimed_total,
        "cancellation_timeouts": cancellation_timeouts_total,
        "stopped_reason": stopped_reason,
    }
