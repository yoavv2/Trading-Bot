"""Explicit Job dependencies: submission-time validation, readiness gating, and
transitive dependency-outcome cascade cancellation (JOB-05, D-04-D-06).

This is the one Phase 17 mechanism with no existing analog in the codebase --
no DAG or cycle-detection code exists anywhere else. Three responsibilities
live here:

1. ``validate_dependency_set`` / ``submit_job``: because a Job's dependency set
   is immutable after submission (D-06) and validation runs before insertion,
   a cycle is never representable in the database -- this module is the only
   place a cycle could be created and the only place it is prevented. There is
   deliberately no ``add_dependency`` or ``remove_dependency`` function, and no
   other function anywhere may add, remove, or modify a ``JobDependency`` row
   after ``submit_job`` returns.

2. ``find_ready_job_ids`` / ``unsatisfied_dependency_exists``: a Job becomes
   claimable only once every declared dependency has reached SUCCEEDED.
   ``unsatisfied_dependency_exists`` is the single, reusable definition of that
   predicate -- plan 17-07's ``claim_next_job`` reuses it verbatim.

3. ``cascade_dependency_outcome``: a dependent Job must never be left stranded
   in QUEUED behind a dead dependency (D-04). When a Job reaches FAILED or
   CANCELLED, this function is the only mechanism that transitively cancels
   every still-unstarted descendant, recording the full causal chain -- the
   blocking Job, its terminal status, and the root failed-or-cancelled
   ancestor (D-05).
"""

from __future__ import annotations

import uuid
from collections import deque
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import ColumnElement, exists, select
from sqlalchemy.orm import Session, aliased

from trading_platform.core.settings import DatabaseSettings, Settings
from trading_platform.db.models import (
    Job,
    JobCancellationCause,
    JobDependency,
    JobEvent,
    JobEventType,
    JobStatus,
    JobTransitionOutcome,
)
from trading_platform.db.session import session_scope
from trading_platform.jobs.lifecycle import JobTransitionRequest, apply_job_transition


class SelfDependencyError(ValueError):
    """Raised when a Job's dependency set would include itself."""

    def __init__(self, *, job_type: str) -> None:
        self.job_type = job_type
        super().__init__(f"Job of type '{job_type}' cannot depend on itself.")


class DependencyCycleError(ValueError):
    """Raised when a proposed dependency set would close a cycle."""

    def __init__(self, *, cycle: tuple[uuid.UUID, ...]) -> None:
        self.cycle = cycle
        chain = " -> ".join(str(job_id) for job_id in cycle)
        super().__init__(f"Dependency cycle detected: {chain}")


class UnknownDependencyError(LookupError):
    """Raised when a declared dependency does not reference an existing Job."""

    def __init__(self, *, depends_on_job_id: uuid.UUID) -> None:
        self.depends_on_job_id = depends_on_job_id
        super().__init__(f"Unknown dependency Job id '{depends_on_job_id}'.")


def _detect_cycle(
    adjacency: Mapping[uuid.UUID, set[uuid.UUID]],
    *,
    start: uuid.UUID,
) -> tuple[uuid.UUID, ...] | None:
    """Iterative (explicit-stack) three-color DFS cycle detector rooted at ``start``.

    Dependency chains are unbounded and Python's recursion limit is not a
    correctness boundary, so this deliberately does not recurse. White = unvisited,
    grey = on the current DFS path, black = fully explored. Encountering a grey
    node while exploring means the current path closes a cycle back to it.

    Because the existing edge graph is always a DAG (this function is the only
    gate that could ever introduce a cycle, and it always runs before insertion),
    the only cycle that can ever exist runs through ``start`` -- so a single
    DFS rooted there is sufficient; a full graph scan is not required.
    """

    WHITE, GREY, BLACK = 0, 1, 2
    color: dict[uuid.UUID, int] = {start: GREY}
    parent: dict[uuid.UUID, uuid.UUID | None] = {start: None}
    stack: list[tuple[uuid.UUID, list[uuid.UUID]]] = [(start, list(adjacency.get(start, ())))]

    while stack:
        node, remaining_children = stack[-1]
        if remaining_children:
            child = remaining_children.pop()
            child_color = color.get(child, WHITE)
            if child_color == GREY:
                cycle = [child]
                cursor: uuid.UUID | None = node
                while cursor is not None and cursor != child:
                    cycle.append(cursor)
                    cursor = parent[cursor]
                cycle.append(child)
                cycle.reverse()
                return tuple(cycle)
            if child_color == WHITE:
                color[child] = GREY
                parent[child] = node
                stack.append((child, list(adjacency.get(child, ()))))
        else:
            color[node] = BLACK
            stack.pop()

    return None


def validate_dependency_set(
    session: Session,
    *,
    new_job_id: uuid.UUID | None,
    depends_on: Sequence[uuid.UUID],
    job_type: str | None = None,
) -> None:
    """Reject a self-dependency, an unknown dependency, or a cycle before any row is inserted.

    Per D-06, a Job's dependency set is immutable after submission and this
    validation always runs before ``submit_job`` inserts anything -- this is
    the only place a cyclic graph could ever be created and the only place it
    is prevented, so cyclic graphs are never representable in the database.

    ``job_type`` is accepted only to make ``SelfDependencyError`` messages more
    informative when called from ``submit_job``; it does not affect validation.
    """

    unique_depends_on = list(dict.fromkeys(depends_on))

    if new_job_id is not None and new_job_id in unique_depends_on:
        raise SelfDependencyError(job_type=job_type or str(new_job_id))

    if unique_depends_on:
        existing_ids = set(
            session.execute(select(Job.id).where(Job.id.in_(unique_depends_on))).scalars().all()
        )
        for dependency_id in unique_depends_on:
            if dependency_id not in existing_ids:
                raise UnknownDependencyError(depends_on_job_id=dependency_id)

    if new_job_id is None or not unique_depends_on:
        return

    adjacency: dict[uuid.UUID, set[uuid.UUID]] = {}
    for job_id, depends_on_job_id in session.execute(
        select(JobDependency.job_id, JobDependency.depends_on_job_id)
    ).all():
        adjacency.setdefault(job_id, set()).add(depends_on_job_id)

    adjacency.setdefault(new_job_id, set()).update(unique_depends_on)

    cycle = _detect_cycle(adjacency, start=new_job_id)
    if cycle is not None:
        raise DependencyCycleError(cycle=cycle)


def _submit_job_in_session(
    session: Session,
    *,
    job_type: str,
    payload: Mapping[str, Any],
    depends_on: Sequence[uuid.UUID],
) -> uuid.UUID:
    """Insert one Job and its immutable submission records into ``session``."""

    unique_depends_on = list(dict.fromkeys(depends_on))
    new_job_id = uuid.uuid4()

    validate_dependency_set(
        session,
        new_job_id=new_job_id,
        depends_on=unique_depends_on,
        job_type=job_type,
    )

    event_at = datetime.now(UTC)
    session.add(
        Job(
            id=new_job_id,
            job_type=job_type,
            payload=dict(payload),
            status=JobStatus.QUEUED,
        )
    )

    for dependency_id in unique_depends_on:
        session.add(JobDependency(job_id=new_job_id, depends_on_job_id=dependency_id))

    session.add(
        JobEvent(
            job_id=new_job_id,
            from_status=None,
            to_status=JobStatus.QUEUED,
            event_type=JobEventType.SUBMITTED,
            outcome=JobTransitionOutcome.ACCEPTED,
            event_at=event_at,
        )
    )
    session.flush()
    return new_job_id


def submit_job(
    *,
    job_type: str,
    payload: Mapping[str, Any],
    depends_on: Sequence[uuid.UUID] = (),
    settings: Settings | DatabaseSettings | None = None,
    session: Session | None = None,
) -> uuid.UUID:
    """Submit a new Job with an immutable, validated dependency set.

    Generates the Job UUID, validates the proposed dependency set against that
    ID (rejecting self-dependencies, unknown dependencies, and cycles before
    any row is written), inserts the Job row (status QUEUED), inserts one
    deduplicated ``JobDependency`` row per dependency, and writes a
    ``SUBMITTED`` ``JobEvent`` directly rather than through
    ``apply_job_transition`` -- submission creates the row rather than
    transitioning it, and the closed transition table in ``jobs/lifecycle.py``
    has no inbound edge to QUEUED by design.

    When ``session`` is supplied, this function flushes its inserts into that
    caller-owned transaction without committing or closing it. Otherwise it
    preserves the standalone ``session_scope(settings)`` transaction behavior.

    Validation failure raises before any insert, so the whole transaction
    rolls back with nothing written.

    Per D-06: no function in this module or anywhere else may add, remove, or
    modify a ``JobDependency`` row after this function returns -- there is
    deliberately no ``add_dependency`` or ``remove_dependency`` function.
    """

    if session is not None:
        return _submit_job_in_session(
            session,
            job_type=job_type,
            payload=payload,
            depends_on=depends_on,
        )

    with session_scope(settings) as standalone_session:
        return _submit_job_in_session(
            standalone_session,
            job_type=job_type,
            payload=payload,
            depends_on=depends_on,
        )


def unsatisfied_dependency_exists(job_id_column: Any) -> ColumnElement[bool]:
    """Reusable correlated-EXISTS expression: true when ``job_id_column`` has at
    least one dependency whose target Job has not reached SUCCEEDED.

    Exposed as a standalone expression builder (not a query-executing function)
    so plan 17-07's ``claim_next_job`` reuses this exact SQL rather than
    duplicating it -- readiness must have exactly one definition.
    """

    dependency_target = aliased(Job)
    return exists(
        select(JobDependency.id)
        .join(dependency_target, dependency_target.id == JobDependency.depends_on_job_id)
        .where(
            JobDependency.job_id == job_id_column,
            dependency_target.status != JobStatus.SUCCEEDED,
        )
    )


def find_ready_job_ids(session: Session, *, limit: int) -> list[uuid.UUID]:
    """Return QUEUED Job IDs, oldest ``queued_at`` first, ready to claim.

    A Job is ready once every declared dependency has reached SUCCEEDED; a Job
    with zero dependencies is always ready (the underlying NOT-EXISTS predicate
    is vacuously true for it). Issues exactly one SQL statement -- this is
    called on every poll of plan 17-07's claim loop, so it must not be a Python
    loop over candidates.
    """

    stmt = (
        select(Job.id)
        .where(Job.status == JobStatus.QUEUED)
        .where(~unsatisfied_dependency_exists(Job.id))
        .order_by(Job.queued_at.asc())
        .limit(limit)
    )
    return list(session.execute(stmt).scalars().all())


def cascade_dependency_outcome(
    session: Session,
    *,
    terminal_job_id: uuid.UUID,
) -> list[uuid.UUID]:
    """Transitively cancel every unstarted descendant of a Job that just reached
    FAILED or CANCELLED, recording the full causal chain (D-04, D-05).

    A dependent Job must never be left stranded in QUEUED behind a dead
    dependency -- this function is the only mechanism preventing that.

    Precondition: ``terminal_job_id`` names a Job that has just reached FAILED
    or CANCELLED. If it is SUCCEEDED, RUNNING, QUEUED, or does not exist, this
    is a no-op that returns an empty list without writing anything, so an
    accidental call is harmless.

    Traverses the reverse dependency edges breadth-first. For each descendant
    still in QUEUED -- and only QUEUED, since D-04 says "still-unstarted"; a
    descendant already RUNNING is left alone because a running Job is
    cancelled cooperatively through plan 17-06's path, not silently
    terminated here -- transitions it to CANCELLED via ``apply_job_transition``
    with ``cancellation_cause`` set to ``DEPENDENCY_FAILED`` or
    ``DEPENDENCY_CANCELLED`` depending on its immediate blocking parent's
    terminal status, ``blocking_job_id``/``blocking_job_status`` naming that
    parent, and ``root_cause_job_id`` always naming ``terminal_job_id`` --
    the root failed-or-cancelled ancestor of the whole cascade.

    Idempotency mirrors ``reclaim_stale_runs``: once a descendant leaves
    QUEUED it no longer matches the traversal's status filter, so a second
    call over the same subgraph transitions nothing and returns an empty
    list. There is deliberately no separate "already cascaded" flag -- the
    status filter is the mechanism.

    Cycle safety: even though cycles are unrepresentable (D-06), the
    traversal carries a ``visited`` set and skips already-visited Job IDs, so
    a corrupted graph produces a bounded traversal rather than an infinite
    loop.

    The caller owns the transaction (plan 17-07's runner calls this on every
    terminal FAILED/CANCELLED transition); this function flushes via
    ``apply_job_transition`` and never commits.
    """

    terminal_job = session.get(Job, terminal_job_id)
    if terminal_job is None or terminal_job.status not in (JobStatus.FAILED, JobStatus.CANCELLED):
        return []

    cancelled_ids: list[uuid.UUID] = []
    visited: set[uuid.UUID] = {terminal_job_id}
    # queue entries: (parent_job_id, blocking_job_id, blocking_job_status, depth)
    queue: deque[tuple[uuid.UUID, uuid.UUID, JobStatus, int]] = deque(
        [(terminal_job_id, terminal_job_id, terminal_job.status, 0)]
    )

    while queue:
        parent_id, blocking_job_id, blocking_job_status, depth = queue.popleft()

        dependents_stmt = (
            select(Job.id, Job.status)
            .join(JobDependency, JobDependency.job_id == Job.id)
            .where(JobDependency.depends_on_job_id == parent_id)
        )
        for dependent_id, dependent_status in session.execute(dependents_stmt).all():
            if dependent_id in visited:
                continue
            visited.add(dependent_id)

            if dependent_status is not JobStatus.QUEUED:
                continue

            cause = (
                JobCancellationCause.DEPENDENCY_FAILED
                if blocking_job_status is JobStatus.FAILED
                else JobCancellationCause.DEPENDENCY_CANCELLED
            )
            reason = (
                f"Cancelled because blocking Job '{blocking_job_id}' reached "
                f"terminal status '{blocking_job_status.value}'."
            )

            apply_job_transition(
                session,
                job_id=dependent_id,
                request=JobTransitionRequest(
                    event_type=JobEventType.CANCELLED,
                    cancellation_cause=cause,
                    blocking_job_id=blocking_job_id,
                    blocking_job_status=blocking_job_status,
                    root_cause_job_id=terminal_job_id,
                    failure_reason=None,
                    requested_by=None,
                    reason=reason,
                    details={
                        "blocking_job_id": str(blocking_job_id),
                        "root_cause_job_id": str(terminal_job_id),
                        "traversal_depth": depth + 1,
                    },
                ),
            )
            cancelled_ids.append(dependent_id)
            queue.append((dependent_id, dependent_id, JobStatus.CANCELLED, depth + 1))

    return cancelled_ids
