"""The single guarded path through which a Job's status ever changes.

``apply_job_transition`` is the ONLY authorized writer of ``Job.status`` in
this codebase. Every later Phase 17 module (queue/lease claim, dependency
cascade, cancellation, execution) calls this function rather than assigning
``job.status`` directly -- this is the code-level guarantee behind JOB-01's
"no state outside the enum is representable" claim, complementing the
database enum from plan 17-01.

The legal-transition table below is closed: ``SUCCEEDED``, ``FAILED``, and
``CANCELLED`` are absorbing terminal states with zero outgoing edges, so no
Job can ever leave a terminal state once it lands there. Every accepted OR
rejected transition writes exactly one append-only ``JobEvent`` audit row in
the same transaction as the status update.

Callers own the transaction boundary (matching ``reclaim_stale_runs``'s
convention in ``services/stale_runs.py``): this module only flushes the
session it is given, it never commits.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from trading_platform.db.models import (
    Job,
    JobCancellationCause,
    JobEvent,
    JobEventType,
    JobFailureReason,
    JobStatus,
    JobTransitionOutcome,
)

# The complete, closed transition table (JOB-01). Exactly these 8 edges exist
# and no others. SUCCEEDED/FAILED/CANCELLED are absorbing terminal states with
# empty inner dicts -- there is no edge back to QUEUED anywhere in this table,
# so no automatic requeue or retry of a crashed Job is representable (D-02).
#
# CANCELLATION_REQUESTED and DEPENDENCY_RESOLVED are deliberately absent from
# every inner dict: they are recorded as JobEvent rows by plans 17-06 and
# 17-05 respectively WITHOUT changing status, so they must never appear here.
_LEGAL_TRANSITIONS: dict[JobStatus, dict[JobEventType, JobStatus]] = {
    JobStatus.QUEUED: {
        JobEventType.CLAIMED: JobStatus.RUNNING,
        # D-07: cancelling a QUEUED Job is atomic -- the handler is never invoked.
        JobEventType.CANCELLED: JobStatus.CANCELLED,
    },
    JobStatus.RUNNING: {
        JobEventType.SUCCEEDED: JobStatus.SUCCEEDED,
        JobEventType.FAILED: JobStatus.FAILED,
        # D-08: cancelling a RUNNING Job lands here only after handler acknowledgement.
        JobEventType.CANCELLED: JobStatus.CANCELLED,
        # D-01: worker loss / lease expiry are infrastructure failures, never a cancellation.
        JobEventType.WORKER_LOST: JobStatus.FAILED,
        JobEventType.LEASE_EXPIRED: JobStatus.FAILED,
        # D-09: a handler that ignores cancellation past the grace period fails, it is
        # never falsely reported as a successful cancellation.
        JobEventType.CANCELLATION_TIMEOUT: JobStatus.FAILED,
    },
    JobStatus.SUCCEEDED: {},
    JobStatus.FAILED: {},
    JobStatus.CANCELLED: {},
}

# The events that force outcome_uncertain=True regardless of what the caller
# supplied (D-03, D-09) -- the framework enforces this rule itself rather than
# leaving it to caller discipline.
_FORCED_OUTCOME_UNCERTAIN_EVENTS = frozenset(
    {
        JobEventType.WORKER_LOST,
        JobEventType.LEASE_EXPIRED,
        JobEventType.CANCELLATION_TIMEOUT,
    }
)

_TERMINAL_STATUSES = frozenset(status for status, edges in _LEGAL_TRANSITIONS.items() if not edges)


class IllegalJobTransition(RuntimeError):
    """Raised when a Job receives an event that is illegal for its current status."""

    def __init__(
        self,
        *,
        job_id: uuid.UUID,
        from_status: JobStatus,
        event_type: JobEventType,
        details: Mapping[str, Any],
    ) -> None:
        self.job_id = job_id
        self.from_status = from_status
        self.event_type = event_type
        self.details = details
        super().__init__(
            f"Illegal job transition for {job_id}: {from_status.value} -> {event_type.value}"
        )


def resolve_transition_target(
    *,
    from_status: JobStatus,
    event_type: JobEventType,
) -> JobStatus | None:
    """Return the target status for an event, or None when the transition is illegal."""

    return _LEGAL_TRANSITIONS.get(from_status, {}).get(event_type)


@dataclass(frozen=True)
class JobTransitionRequest:
    """The event driving one call to ``apply_job_transition``."""

    event_type: JobEventType
    event_at: datetime | None = None
    failure_reason: JobFailureReason | None = None
    failure_message: str | None = None
    outcome_uncertain: bool | None = None
    cancellation_cause: JobCancellationCause | None = None
    requested_by: str | None = None
    reason: str | None = None
    requested_at: datetime | None = None
    acknowledged_at: datetime | None = None
    result_summary: Mapping[str, Any] | None = None
    blocking_job_id: uuid.UUID | None = None
    blocking_job_status: JobStatus | None = None
    root_cause_job_id: uuid.UUID | None = None
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class JobTransitionResult:
    """The outcome of one accepted call to ``apply_job_transition``."""

    job_id: uuid.UUID
    from_status: JobStatus
    to_status: JobStatus
    event_id: uuid.UUID
    outcome: JobTransitionOutcome


def apply_job_transition(
    session: Session,
    *,
    job_id: uuid.UUID,
    request: JobTransitionRequest,
) -> JobTransitionResult:
    """Apply one guarded status transition to a Job, auditing every outcome.

    The caller owns the transaction boundary (this function flushes, never
    commits). Takes a row lock via ``with_for_update=True`` so two workers
    attempting to transition the same Job concurrently serialize on the row
    rather than racing.
    """

    job = session.get(Job, job_id, with_for_update=True)
    if job is None:
        raise LookupError(f"Job '{job_id}' was not found.")

    from_status = job.status
    event_at = request.event_at or datetime.now(UTC)
    next_status = resolve_transition_target(from_status=from_status, event_type=request.event_type)

    if next_status is None:
        details = dict(request.details)
        details["attempted_transition"] = {
            "job_id": str(job_id),
            "from_status": from_status.value,
            "event_type": request.event_type.value,
        }
        rejected_event = JobEvent(
            job_id=job_id,
            from_status=from_status,
            to_status=None,
            event_type=request.event_type,
            outcome=JobTransitionOutcome.REJECTED,
            event_at=event_at,
            details=details,
        )
        session.add(rejected_event)
        session.flush()
        raise IllegalJobTransition(
            job_id=job_id,
            from_status=from_status,
            event_type=request.event_type,
            details=details,
        )

    # D-01 defensive guard: WORKER_LOST/LEASE_EXPIRED always carry a matching
    # JobFailureReason even if the caller forgot to supply one.
    failure_reason = request.failure_reason
    if failure_reason is None:
        if request.event_type is JobEventType.WORKER_LOST:
            failure_reason = JobFailureReason.WORKER_LOST
        elif request.event_type is JobEventType.LEASE_EXPIRED:
            failure_reason = JobFailureReason.LEASE_EXPIRED

    # D-01 defensive guard: infrastructure failure must never be recorded as a
    # cancellation -- a failure_reason accompanying a CANCELLED target is rejected.
    if next_status is JobStatus.CANCELLED and failure_reason is not None:
        raise ValueError(
            f"Job '{job_id}': a CANCELLED transition must not carry a failure_reason "
            f"({failure_reason.value}); infrastructure failure is never a cancellation."
        )

    job.status = next_status
    if next_status is JobStatus.RUNNING and job.started_at is None:
        job.started_at = event_at
    if next_status in _TERMINAL_STATUSES:
        job.completed_at = event_at

    if failure_reason is not None:
        job.failure_reason = failure_reason
    if request.failure_message is not None:
        job.failure_message = request.failure_message
    if request.cancellation_cause is not None:
        job.cancellation_cause = request.cancellation_cause
    if request.result_summary is not None:
        job.result_summary = dict(request.result_summary)
    if request.blocking_job_id is not None:
        job.blocking_job_id = request.blocking_job_id
    if request.blocking_job_status is not None:
        job.blocking_job_status = request.blocking_job_status
    if request.root_cause_job_id is not None:
        job.root_cause_job_id = request.root_cause_job_id

    if request.event_type in _FORCED_OUTCOME_UNCERTAIN_EVENTS:
        job.outcome_uncertain = True
    elif request.outcome_uncertain is not None:
        job.outcome_uncertain = request.outcome_uncertain

    # D-12: this function NEVER writes progress_percent, progress_step,
    # progress_current, progress_total, or progress_updated_at. FAILED and
    # CANCELLED Jobs preserve their last-reported progress untouched; only
    # the dedicated progress-reporting path (plan 17-07) may write these
    # columns, and only while the Job is still RUNNING.

    terminal_cause: str | None = None
    if failure_reason is not None:
        terminal_cause = failure_reason.value
    elif request.cancellation_cause is not None:
        terminal_cause = request.cancellation_cause.value

    accepted_event = JobEvent(
        job_id=job_id,
        from_status=from_status,
        to_status=next_status,
        event_type=request.event_type,
        outcome=JobTransitionOutcome.ACCEPTED,
        event_at=event_at,
        requested_by=request.requested_by,
        reason=request.reason,
        requested_at=request.requested_at,
        acknowledged_at=request.acknowledged_at,
        terminal_cause=terminal_cause,
        details=dict(request.details),
    )
    session.add(accepted_event)
    session.flush()

    return JobTransitionResult(
        job_id=job_id,
        from_status=from_status,
        to_status=next_status,
        event_id=accepted_event.id,
        outcome=JobTransitionOutcome.ACCEPTED,
    )
