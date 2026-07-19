"""Read-only Job observation service -- the D-15 generic read surface for JOB-07.

This module is the transport-agnostic read layer over the Job framework's
persistence models (``Job``/``JobDependency``/``JobEvent``/``JobLog``). It is
strictly read-only: no method here writes to the database. Operation
submission and the broader orchestration API remain Phase 18 scope.

Boundary constraint (JOB-04): this module lives under
``trading_platform/services/`` and is scanned by
``tests/test_job_import_boundary.py``. It must never import the ``jobs``,
``api``, or ``worker`` top-level packages of this project. Where a query
here overlaps with logic that also exists in the ``jobs`` package (e.g.
the "blocking dependency" predicate defined in ``jobs/dependencies.py``'s
``unsatisfied_dependency_exists``), it is deliberately reimplemented here
as an independent read-only query rather than imported across the
boundary -- do not "deduplicate" this into a boundary violation.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select

from trading_platform.core.settings import Settings, load_settings
from trading_platform.db.models import (
    Job,
    JobDependency,
    JobEvent,
    JobLog,
    JobStatus,
)
from trading_platform.db.session import session_scope

DEFAULT_LIMIT = 20
MAX_LIMIT = 100
DEFAULT_LOG_PAGE_SIZE = 100
MAX_LOG_PAGE_SIZE = 500


@dataclass(frozen=True)
class JobReadFilters:
    status: str | None = None
    job_type: str | None = None
    limit: int = DEFAULT_LIMIT


class JobReadService:
    """Transport-agnostic reads over the Job framework. Every method returns
    plain JSON-serializable dicts; no ORM object crosses this boundary."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings

    @property
    def settings(self) -> Settings:
        return self._settings or load_settings()

    def list_jobs(self, filters: JobReadFilters | None = None) -> list[dict[str, Any]]:
        resolved_filters = filters or JobReadFilters()
        capped_limit = min(resolved_filters.limit, MAX_LIMIT)

        with session_scope(self.settings) as session:
            stmt = select(Job).order_by(Job.queued_at.desc())
            if resolved_filters.status is not None:
                stmt = stmt.where(Job.status == JobStatus(resolved_filters.status))
            if resolved_filters.job_type is not None:
                stmt = stmt.where(Job.job_type == resolved_filters.job_type)
            rows = session.execute(stmt.limit(capped_limit)).scalars().all()
            items = [_serialize_job_summary(job) for job in rows]

        return items

    def get_job_detail(self, job_id: str) -> dict[str, Any]:
        job_uuid = uuid.UUID(job_id)

        with session_scope(self.settings) as session:
            job = session.get(Job, job_uuid)
            if job is None:
                raise LookupError(f"Job '{job_id}' was not found.")

            dependency_rows = session.execute(
                select(JobDependency, Job)
                .join(Job, Job.id == JobDependency.depends_on_job_id)
                .where(JobDependency.job_id == job_uuid)
            ).all()

            dependencies: list[dict[str, Any]] = []
            blocking_dependencies: list[dict[str, Any]] = []
            for _edge, dependency_job in dependency_rows:
                entry = {
                    "id": str(dependency_job.id),
                    "job_type": dependency_job.job_type,
                    "status": dependency_job.status.value,
                }
                dependencies.append(entry)
                # Reimplemented inline rather than importing
                # jobs/dependencies.py's unsatisfied_dependency_exists --
                # this services/ module must not reach into the jobs
                # package at all (JOB-04 import boundary).
                if dependency_job.status != JobStatus.SUCCEEDED:
                    blocking_dependencies.append(entry)

            detail = {
                "id": str(job.id),
                "job_type": job.job_type,
                "status": job.status.value,
                "queued_at": _dt(job.queued_at),
                "started_at": _dt(job.started_at),
                "completed_at": _dt(job.completed_at),
                "failure_reason": _enum_value(job.failure_reason),
                "failure_message": job.failure_message,
                "outcome_uncertain": job.outcome_uncertain,
                "result_summary": job.result_summary,
                "progress": _serialize_progress(job),
                "cancellation_requested_by": job.cancellation_requested_by,
                "cancellation_reason": job.cancellation_reason,
                "cancellation_requested_at": _dt(job.cancellation_requested_at),
                "cancellation_acknowledged_at": _dt(job.cancellation_acknowledged_at),
                "cancellation_cause": _enum_value(job.cancellation_cause),
                "blocking_job_id": _uuid_value(job.blocking_job_id),
                "blocking_job_status": _enum_value(job.blocking_job_status),
                "root_cause_job_id": _uuid_value(job.root_cause_job_id),
                "dependencies": dependencies,
                "blocking_dependencies": blocking_dependencies,
            }

        return detail

    def get_job_progress(self, job_id: str) -> dict[str, Any]:
        job_uuid = uuid.UUID(job_id)

        with session_scope(self.settings) as session:
            row = session.execute(
                select(
                    Job.status,
                    Job.progress_percent,
                    Job.progress_step,
                    Job.progress_current,
                    Job.progress_total,
                    Job.progress_updated_at,
                ).where(Job.id == job_uuid)
            ).one_or_none()
            if row is None:
                raise LookupError(f"Job '{job_id}' was not found.")
            status, percent, step, current, total, updated_at = row

        return {
            "status": status.value,
            "percent": percent,
            "step": step,
            "current": current,
            "total": total,
            "progress_updated_at": _dt(updated_at),
        }

    def list_job_logs(
        self,
        job_id: str,
        *,
        after_sequence: int | None = None,
        limit: int = DEFAULT_LOG_PAGE_SIZE,
    ) -> dict[str, Any]:
        job_uuid = uuid.UUID(job_id)
        capped_limit = min(limit, MAX_LOG_PAGE_SIZE)

        with session_scope(self.settings) as session:
            exists_job = session.execute(
                select(Job.id).where(Job.id == job_uuid)
            ).scalar_one_or_none()
            if exists_job is None:
                raise LookupError(f"Job '{job_id}' was not found.")

            stmt = select(JobLog).where(JobLog.job_id == job_uuid)
            if after_sequence is not None:
                stmt = stmt.where(JobLog.sequence > after_sequence)
            # Order strictly by sequence (D-13) -- never logged_at, which can
            # collide within a single Job. Fetch one row past the page size so
            # `has_more` reflects whether another row actually exists, rather
            # than assuming a full-size page always has more.
            stmt = stmt.order_by(JobLog.sequence.asc()).limit(capped_limit + 1)
            rows = session.execute(stmt).scalars().all()

            has_more = len(rows) > capped_limit
            page_rows = rows[:capped_limit]

            items = [
                {
                    "sequence": row.sequence,
                    "logged_at": _dt(row.logged_at),
                    "level": row.level,
                    "event_code": row.event_code,
                    "message": row.message,
                    "handler_type": row.handler_type,
                    "context": row.context,
                }
                for row in page_rows
            ]

        next_after_sequence = page_rows[-1].sequence if page_rows else after_sequence

        return {
            "job_id": job_id,
            "items": items,
            "count": len(items),
            "next_after_sequence": next_after_sequence,
            "has_more": has_more,
        }

    def list_job_events(
        self, job_id: str, *, limit: int = DEFAULT_LOG_PAGE_SIZE
    ) -> list[dict[str, Any]]:
        job_uuid = uuid.UUID(job_id)
        capped_limit = min(limit, MAX_LOG_PAGE_SIZE)

        with session_scope(self.settings) as session:
            exists_job = session.execute(
                select(Job.id).where(Job.id == job_uuid)
            ).scalar_one_or_none()
            if exists_job is None:
                raise LookupError(f"Job '{job_id}' was not found.")

            rows = (
                session.execute(
                    select(JobEvent)
                    .where(JobEvent.job_id == job_uuid)
                    .order_by(JobEvent.event_at.asc(), JobEvent.id.asc())
                    .limit(capped_limit)
                )
                .scalars()
                .all()
            )

            items = [
                {
                    "id": str(event.id),
                    "from_status": _enum_value(event.from_status),
                    "to_status": _enum_value(event.to_status),
                    "event_type": event.event_type.value,
                    "outcome": event.outcome.value,
                    "event_at": _dt(event.event_at),
                    "requested_by": event.requested_by,
                    "reason": event.reason,
                    "requested_at": _dt(event.requested_at),
                    "acknowledged_at": _dt(event.acknowledged_at),
                    "terminal_cause": event.terminal_cause,
                    "details": event.details,
                }
                for event in rows
            ]

        return items


def _serialize_job_summary(job: Job) -> dict[str, Any]:
    return {
        "id": str(job.id),
        "job_type": job.job_type,
        "status": job.status.value,
        "queued_at": _dt(job.queued_at),
        "started_at": _dt(job.started_at),
        "completed_at": _dt(job.completed_at),
        "failure_reason": _enum_value(job.failure_reason),
        "outcome_uncertain": job.outcome_uncertain,
        "cancellation_requested_at": _dt(job.cancellation_requested_at),
        "progress": _serialize_progress(job),
    }


def _serialize_progress(job: Job) -> dict[str, Any]:
    return {
        "percent": job.progress_percent,
        "step": job.progress_step,
        "current": job.progress_current,
        "total": job.progress_total,
        "progress_updated_at": _dt(job.progress_updated_at),
    }


def _dt(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _enum_value(value: Any) -> str | None:
    return value.value if value is not None else None


def _uuid_value(value: uuid.UUID | None) -> str | None:
    return str(value) if value is not None else None
