"""Restart-safe claim/lease queue for the generic Job framework (JOB-02).

Two independent mechanisms deliver the "a Job is never silently lost or
duplicated" guarantee:

1. **SKIP LOCKED against duplication.** ``claim_next_job`` selects the oldest
   ready Job under ``SELECT ... FOR UPDATE SKIP LOCKED``: a row another
   worker's open transaction already holds is skipped rather than waited on,
   so two workers polling concurrently can never both walk away with the same
   Job.
2. **Lease expiry against loss.** Every claimed Job carries a ``lease_owner``
   and a ``lease_expires_at``. A worker renews its lease on a cadence
   (``renew_lease``); a worker that crashes simply stops renewing, so its
   lease lapses on its own with no separate liveness protocol required.
   ``find_lost_job_ids``/``reclaim_lost_jobs`` detect and reclaim those Jobs
   honestly -- FAILED, never silently requeued.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_platform.core.settings import DatabaseSettings, Settings
from trading_platform.db.models import Job, JobEventType, JobFailureReason, JobStatus
from trading_platform.db.session import session_scope
from trading_platform.jobs.dependencies import (
    cascade_dependency_outcome,
    unsatisfied_dependency_exists,
)
from trading_platform.jobs.lifecycle import JobTransitionRequest, apply_job_transition

# Lease/heartbeat/poll intervals are the agent's discretion per CONTEXT.md.
# HEARTBEAT_SECONDS * 2 < LEASE_SECONDS is a deliberate margin: a worker that
# misses two consecutive heartbeats (e.g. a slow GC pause or a transient DB
# hiccup) still has time to renew before its lease actually expires.
LEASE_SECONDS = 60
HEARTBEAT_SECONDS = 20
POLL_INTERVAL_SECONDS = 2


def claim_next_job(
    session: Session,
    *,
    worker_id: str,
    now: datetime | None = None,
    lease_seconds: int = LEASE_SECONDS,
) -> uuid.UUID | None:
    """Claim the oldest ready Job for ``worker_id``, or return None.

    Eligibility is exactly three predicates on the same locked statement:
    ``status == QUEUED``, ``cancellation_requested_at IS NULL``, and no
    unsatisfied dependency (reusing ``unsatisfied_dependency_exists`` from
    ``jobs/dependencies.py`` verbatim -- readiness has exactly one SQL
    definition in this codebase). Ordered oldest-``queued_at``-first, limited
    to one row, and locked with ``FOR UPDATE SKIP LOCKED``: a row a concurrent
    worker's open transaction already holds is skipped rather than waited on,
    so this worker gets the next eligible Job, or None, instead of blocking or
    racing another claimant.

    The ``cancellation_requested_at IS NULL`` predicate living inside this
    same locked statement is what guarantees D-07: a Job cancelled while
    QUEUED can never be claimed afterward, so its handler is never invoked.

    On a hit, sets ``lease_owner``/``lease_expires_at``/``heartbeat_at`` and
    calls ``apply_job_transition`` with ``CLAIMED``, which lands the Job on
    RUNNING and stamps ``started_at``. Never commits -- the caller owns the
    transaction boundary.
    """

    resolved_now = now or datetime.now(UTC)

    stmt = (
        select(Job)
        .where(
            Job.status == JobStatus.QUEUED,
            Job.cancellation_requested_at.is_(None),
            ~unsatisfied_dependency_exists(Job.id),
        )
        .order_by(Job.queued_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    job = session.execute(stmt).scalars().first()
    if job is None:
        return None

    job.lease_owner = worker_id
    job.lease_expires_at = resolved_now + timedelta(seconds=lease_seconds)
    job.heartbeat_at = resolved_now

    apply_job_transition(
        session,
        job_id=job.id,
        request=JobTransitionRequest(event_type=JobEventType.CLAIMED, event_at=resolved_now),
    )

    return job.id


def renew_lease(
    *,
    job_id: uuid.UUID,
    worker_id: str,
    lease_seconds: int = LEASE_SECONDS,
    settings: Settings | DatabaseSettings | None = None,
) -> bool:
    """Extend a RUNNING Job's lease on behalf of its current owner.

    Returns False (without raising) when the Job does not exist, is no longer
    RUNNING, or ``worker_id`` no longer matches ``lease_owner`` -- the last
    case means a sweep already reclaimed the lease out from under this
    worker. A False return is the signal plan 17-09's runner uses to stop
    working on a Job it no longer owns.
    """

    with session_scope(settings) as session:
        job = session.get(Job, job_id, with_for_update=True)
        if job is None:
            return False
        if job.status is not JobStatus.RUNNING or job.lease_owner != worker_id:
            return False

        now = datetime.now(UTC)
        job.lease_expires_at = now + timedelta(seconds=lease_seconds)
        job.heartbeat_at = now
        return True


def find_lost_job_ids(session: Session, *, now: datetime | None = None) -> list[uuid.UUID]:
    """Return every RUNNING Job whose lease has lapsed, via ONE query.

    Mirrors ``find_stale_runs``'s single-query cutoff-comparison shape. No
    separate liveness protocol is needed: a dead worker process cannot renew
    its lease, so the lease simply expires on its own.
    """

    resolved_now = now or datetime.now(UTC)
    stmt = select(Job.id).where(
        Job.status == JobStatus.RUNNING,
        Job.lease_expires_at.is_not(None),
        Job.lease_expires_at < resolved_now,
    )
    return list(session.execute(stmt).scalars().all())


def reclaim_lost_jobs(
    session: Session,
    *,
    now: datetime | None = None,
    failure_reason: JobFailureReason = JobFailureReason.LEASE_EXPIRED,
) -> list[uuid.UUID]:
    """Reclaim every lost Job honestly: FAILED, uncertain outcome, never requeued.

    For each Job ``find_lost_job_ids`` reports, transitions it via
    ``apply_job_transition`` with ``LEASE_EXPIRED`` (landing on FAILED per
    D-01 -- never CANCELLED, since a crashed worker is an infrastructure
    failure, not an operator cancellation), forcing
    ``outcome_uncertain=True`` because the framework cannot prove whether the
    handler's side effect completed before the worker disappeared (D-03,
    enforced by ``lifecycle.py`` itself, not repeated here). Clears the lease
    fields once the Job has left RUNNING, then cascades to unstarted
    dependents via ``cascade_dependency_outcome`` so they are not stranded
    behind a dead ancestor (D-04).

    This function must never move a Job back to QUEUED: Phase 17 ships no
    automatic requeue or retry. Recovery here is deterministic and visible;
    an explicit retry that links a new attempt to this Job is Phase 19's
    scope (OPS-07).

    Idempotency mirrors ``reclaim_stale_runs`` exactly: once a Job leaves
    RUNNING it no longer matches ``find_lost_job_ids``'s predicate, so a
    second pass over the same Jobs returns an empty list -- there is
    deliberately no separate reclaimed flag. Flushes and never commits; the
    caller owns the transaction boundary.
    """

    resolved_now = now or datetime.now(UTC)
    lost_ids = find_lost_job_ids(session, now=resolved_now)

    reclaimed_ids: list[uuid.UUID] = []
    for job_id in lost_ids:
        job = session.get(Job, job_id, with_for_update=True)
        if job is None:
            continue
        # Re-check under the row lock: a concurrent sweep may already have
        # reclaimed this Job between the unlocked detector query above and
        # this lock acquisition.
        if job.status is not JobStatus.RUNNING or job.lease_expires_at is None:
            continue
        if job.lease_expires_at >= resolved_now:
            continue

        previous_lease_owner = job.lease_owner
        previous_lease_expires_at = job.lease_expires_at

        apply_job_transition(
            session,
            job_id=job_id,
            request=JobTransitionRequest(
                event_type=JobEventType.LEASE_EXPIRED,
                event_at=resolved_now,
                failure_reason=failure_reason,
                failure_message=(
                    f"Lease held by worker '{previous_lease_owner}' expired at "
                    f"{previous_lease_expires_at.isoformat()}; the worker is presumed "
                    "crashed and the Job is reclaimed without being requeued."
                ),
            ),
        )

        job.lease_owner = None
        job.lease_expires_at = None

        cascade_dependency_outcome(session, terminal_job_id=job_id)

        reclaimed_ids.append(job_id)

    session.flush()
    return reclaimed_ids
