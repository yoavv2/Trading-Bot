"""Operator cancellation: atomic for QUEUED Jobs, cooperative for RUNNING ones (JOB-06).

There are exactly three cancellation outcomes in this module:

1. **QUEUED -> CANCELLED, immediately.** ``request_cancellation`` row-locks the
   Job and applies the transition inside the same transaction that a
   concurrent worker would use to claim it -- so a cancelled QUEUED Job can
   never subsequently be claimed and its handler is never invoked (D-07).
2. **RUNNING -> CANCELLED, only after handler acknowledgement.** A cancellation
   request against a RUNNING Job persists the request facts and appends a
   ``CANCELLATION_REQUESTED`` audit event WITHOUT changing status (D-08). The
   Job reaches CANCELLED only when ``acknowledge_cancellation`` is called --
   normally by plan 17-07's runner after the handler observes the request via
   ``jobs/context.py``'s ``is_cancellation_requested``/``raise_if_cancelled``
   checkpoint and stops.
3. **RUNNING with no acknowledgement past the grace period -> FAILED.**
   ``sweep_cancellation_timeouts`` reports this honestly as a failure with
   reason ``cancellation_timeout`` and ``outcome_uncertain=True`` -- never as
   a successful CANCELLED, since the framework cannot prove the handler
   actually stopped (D-09).

Every cancellation records requester identity, optional reason,
``requested_at``, ``acknowledged_at``, and terminal cause (D-10), both on the
``Job`` row and on an append-only ``JobEvent``.

Phase 17 never automatically requeues or retries a Job (D-02): there is
deliberately no requeue or retry function in this module or anywhere else in
the ``jobs`` package. All status changes route through
``trading_platform.jobs.lifecycle.apply_job_transition``, the sole authorized
writer of ``Job.status`` -- the one exception is the RUNNING request path
above, which intentionally does not change status at all.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_platform.core.settings import DatabaseSettings, Settings
from trading_platform.db.models import (
    Job,
    JobCancellationCause,
    JobEvent,
    JobEventType,
    JobFailureReason,
    JobStatus,
    JobTransitionOutcome,
)
from trading_platform.db.session import session_scope
from trading_platform.jobs.lifecycle import JobTransitionRequest, apply_job_transition

# Default cooperative cancellation grace period, in seconds. This is the
# agent's discretion per CONTEXT.md; plan 17-07's worker loop is the only
# caller expected to ever override it (e.g. for a faster test cadence).
CANCELLATION_GRACE_SECONDS = 300


@dataclass(frozen=True)
class CancellationResult:
    """The outcome of one cancellation-related call in this module."""

    job_id: uuid.UUID
    status: JobStatus
    accepted: bool
    already_terminal: bool
    mode: str  # "immediate" or "cooperative"


@dataclass
class JobNotCancellableError(RuntimeError):
    """Raised when a cancellation-related call targets a Job that cannot accept it.

    Covers both an already-terminal Job (SUCCEEDED/FAILED/CANCELLED are
    absorbing per ``jobs/lifecycle.py``'s closed transition table -- D-07/D-08
    only cover QUEUED and RUNNING) and a RUNNING Job with no pending
    cancellation request being acknowledged. Field assignment here is
    dataclass-generated rather than written as literal source, and unlike
    ``strategies/registry.py``'s ``UnknownStrategyError`` this is
    deliberately NOT frozen: a frozen dataclass exception raised through a
    nested ``@contextmanager``-based ``session_scope`` fails when contextlib
    tries to attach a traceback to it (``FrozenInstanceError``).
    """

    job_id: uuid.UUID
    status: JobStatus

    def __str__(self) -> str:
        return (
            f"Job '{self.job_id}' cannot be cancelled or acknowledged: "
            f"current status is '{self.status.value}'."
        )


def _request_cancellation_in_session(
    session: Session,
    *,
    job_id: uuid.UUID,
    requested_by: str,
    reason: str | None,
) -> CancellationResult:
    """Apply one cancellation request using a caller-owned transaction."""

    # Imported here to avoid a module-level coupling from cancellation ->
    # dependencies; dependencies never imports cancellation.
    from trading_platform.jobs.dependencies import cascade_dependency_outcome

    # The row lock is what makes the QUEUED path atomic against a worker
    # concurrently claiming the same Job (D-07): the cancel transaction
    # and the claim transaction serialize on this row, and once this
    # transaction commits CANCELLED, a claim attempt finds an absorbing
    # terminal status and can never proceed.
    job = session.get(Job, job_id, with_for_update=True)
    if job is None:
        raise LookupError(f"Job '{job_id}' was not found.")

    if job.status is JobStatus.QUEUED:
        now = datetime.now(UTC)
        job.cancellation_requested_at = now
        job.cancellation_requested_by = requested_by
        job.cancellation_reason = reason
        job.cancellation_acknowledged_at = now

        apply_job_transition(
            session,
            job_id=job_id,
            request=JobTransitionRequest(
                event_type=JobEventType.CANCELLED,
                cancellation_cause=JobCancellationCause.OPERATOR_REQUEST,
                failure_reason=None,
                requested_by=requested_by,
                reason=reason,
                requested_at=now,
                acknowledged_at=now,
            ),
        )
        # D-04: the Job just reached CANCELLED. Cascade to unstarted QUEUED
        # dependents so they are not stranded behind it, in the same
        # transaction (the session is already open and row-locked).
        cascade_dependency_outcome(session, terminal_job_id=job_id)
        return CancellationResult(
            job_id=job_id,
            status=JobStatus.CANCELLED,
            accepted=True,
            already_terminal=False,
            mode="immediate",
        )

    if job.status is JobStatus.RUNNING:
        if job.cancellation_requested_at is not None:
            # A request is already pending -- do not overwrite the first
            # requester's identity, reason, or timestamp.
            return CancellationResult(
                job_id=job_id,
                status=job.status,
                accepted=False,
                already_terminal=False,
                mode="cooperative",
            )

        now = datetime.now(UTC)
        job.cancellation_requested_at = now
        job.cancellation_requested_by = requested_by
        job.cancellation_reason = reason

        # No apply_job_transition call here: the Job stays RUNNING until
        # the handler acknowledges (D-08). CANCELLATION_REQUESTED is
        # deliberately absent from lifecycle.py's transition table, so
        # this JobEvent is appended directly with to_status=None.
        session.add(
            JobEvent(
                job_id=job_id,
                from_status=JobStatus.RUNNING,
                to_status=None,
                event_type=JobEventType.CANCELLATION_REQUESTED,
                outcome=JobTransitionOutcome.ACCEPTED,
                event_at=now,
                requested_by=requested_by,
                reason=reason,
                requested_at=now,
            )
        )
        session.flush()
        return CancellationResult(
            job_id=job_id,
            status=JobStatus.RUNNING,
            accepted=True,
            already_terminal=False,
            mode="cooperative",
        )

    # SUCCEEDED, FAILED, CANCELLED are absorbing terminal states (D-07/D-08
    # only cover QUEUED and RUNNING).
    raise JobNotCancellableError(job_id=job_id, status=job.status)


def request_cancellation(
    *,
    job_id: uuid.UUID,
    requested_by: str,
    reason: str | None = None,
    settings: Settings | DatabaseSettings | None = None,
    session: Session | None = None,
) -> CancellationResult:
    """Request cancellation of a Job, atomic for QUEUED and cooperative for RUNNING.

    Raises ``LookupError`` if the Job does not exist, and
    ``JobNotCancellableError`` if it is already terminal.

    A second call while a RUNNING cancellation request is already pending is
    a no-op that returns ``accepted=False`` without overwriting the original
    requester, reason, or ``requested_at`` -- the first requester owns the
    audit record.

    When ``session`` is supplied, this function flushes its mutations without
    committing or closing the caller-owned transaction. Otherwise it preserves
    standalone ``session_scope(settings)`` transaction behavior.
    """

    if session is not None:
        return _request_cancellation_in_session(
            session,
            job_id=job_id,
            requested_by=requested_by,
            reason=reason,
        )

    with session_scope(settings) as standalone_session:
        return _request_cancellation_in_session(
            standalone_session,
            job_id=job_id,
            requested_by=requested_by,
            reason=reason,
        )


def acknowledge_cancellation(session: Session, *, job_id: uuid.UUID) -> CancellationResult:
    """Acknowledge that a RUNNING handler observed and honored a cancellation request.

    Called by plan 17-07's runner when a handler raises ``JobCancelledError``
    or otherwise returns after observing the request via
    ``jobs/context.py``'s cooperative-cancellation checkpoint. Takes an open
    session -- the caller owns the transaction boundary.

    Raises ``JobNotCancellableError`` when the Job is not RUNNING or has no
    pending cancellation request, so a handler can never fabricate a
    cancellation nobody asked for.
    """

    job = session.get(Job, job_id, with_for_update=True)
    if job is None:
        raise LookupError(f"Job '{job_id}' was not found.")

    if job.status is not JobStatus.RUNNING or job.cancellation_requested_at is None:
        raise JobNotCancellableError(job_id=job_id, status=job.status)

    now = datetime.now(UTC)
    job.cancellation_acknowledged_at = now

    apply_job_transition(
        session,
        job_id=job_id,
        request=JobTransitionRequest(
            event_type=JobEventType.CANCELLED,
            cancellation_cause=JobCancellationCause.OPERATOR_REQUEST,
            failure_reason=None,
            requested_by=job.cancellation_requested_by,
            reason=job.cancellation_reason,
            requested_at=job.cancellation_requested_at,
            acknowledged_at=now,
        ),
    )
    return CancellationResult(
        job_id=job_id,
        status=JobStatus.CANCELLED,
        accepted=True,
        already_terminal=False,
        mode="cooperative",
    )


def find_cancellation_timeout_job_ids(
    session: Session,
    *,
    grace_seconds: int = CANCELLATION_GRACE_SECONDS,
    now: datetime | None = None,
) -> list[uuid.UUID]:
    """Return RUNNING Job IDs whose cancellation request has outlived the grace period.

    A single query mirroring ``find_stale_runs``'s cutoff-comparison shape:
    RUNNING status, a pending (non-null, non-acknowledged) cancellation
    request, and ``cancellation_requested_at`` older than
    ``now - grace_seconds``.
    """

    resolved_now = now or datetime.now(UTC)
    cutoff = resolved_now - timedelta(seconds=grace_seconds)
    stmt = select(Job.id).where(
        Job.status == JobStatus.RUNNING,
        Job.cancellation_requested_at.is_not(None),
        Job.cancellation_acknowledged_at.is_(None),
        Job.cancellation_requested_at < cutoff,
    )
    return list(session.execute(stmt).scalars().all())


def sweep_cancellation_timeouts(
    session: Session,
    *,
    grace_seconds: int = CANCELLATION_GRACE_SECONDS,
    now: datetime | None = None,
) -> list[uuid.UUID]:
    """Fail every RUNNING Job whose cancellation request outlived the grace period.

    Per D-09, the resulting status is FAILED with reason
    ``cancellation_timeout`` and ``outcome_uncertain=True`` -- never CANCELLED,
    since the framework cannot prove the handler actually stopped.

    Idempotency mirrors ``reclaim_stale_runs``/``cascade_dependency_outcome``:
    once a Job leaves RUNNING it no longer matches
    ``find_cancellation_timeout_job_ids``'s predicate, so a second sweep over
    the same Jobs is a safe no-op returning an empty list. There is
    deliberately no separate "swept" flag.

    Flushes and never commits; the caller (plan 17-07's worker loop) owns the
    transaction boundary.
    """

    # Imported inside the function to avoid a new module-level coupling from
    # cancellation -> dependencies; dependencies never imports cancellation,
    # so this is only a cascade dispatch, mirroring reclaim_lost_jobs.
    from trading_platform.jobs.dependencies import cascade_dependency_outcome

    resolved_now = now or datetime.now(UTC)
    cutoff = resolved_now - timedelta(seconds=grace_seconds)
    job_ids = find_cancellation_timeout_job_ids(
        session, grace_seconds=grace_seconds, now=resolved_now
    )

    swept: list[uuid.UUID] = []
    for job_id in job_ids:
        # Candidate selection above was an unlocked read: between it and the
        # transition below a concurrent worker can acknowledge cancellation or
        # land the Job terminal. Lock the row and re-check every eligibility
        # condition against the same cutoff before transitioning, so a losing
        # sweep skips the candidate instead of hitting an absorbing terminal
        # from_status and raising IllegalJobTransition (which, since the whole
        # poll iteration is one transaction, would roll back reclaim + sweep +
        # claim together). A skipped candidate writes nothing, so the original
        # cancellation-request facts and event are left untouched. This is not
        # broad suppression: recovery is by locked revalidation of persisted
        # state, and a genuinely illegal transition still raises.
        job = session.get(Job, job_id, with_for_update=True)
        if job is None:
            continue
        if job.status is not JobStatus.RUNNING:
            continue
        if job.cancellation_requested_at is None:
            continue
        if job.cancellation_acknowledged_at is not None:
            continue
        if job.cancellation_requested_at >= cutoff:
            continue

        apply_job_transition(
            session,
            job_id=job_id,
            request=JobTransitionRequest(
                event_type=JobEventType.CANCELLATION_TIMEOUT,
                failure_reason=JobFailureReason.CANCELLATION_TIMEOUT,
                failure_message=(
                    f"Cancellation grace period of {grace_seconds} seconds elapsed without "
                    "the handler acknowledging the cancellation request."
                ),
                outcome_uncertain=True,
                cancellation_cause=None,
                requested_by=job.cancellation_requested_by,
                reason=job.cancellation_reason,
                requested_at=job.cancellation_requested_at,
                acknowledged_at=None,
            ),
        )
        # D-04: the Job just reached FAILED (CANCELLATION_TIMEOUT). Cascade to
        # unstarted QUEUED dependents so they are not stranded behind it, in
        # the same transaction as the terminal write -- mirrors reclaim_lost_jobs.
        cascade_dependency_outcome(session, terminal_job_id=job_id)
        swept.append(job_id)

    return swept
