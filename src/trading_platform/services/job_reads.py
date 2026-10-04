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
from enum import StrEnum
from typing import Any

from sqlalchemy import select

from trading_platform.core.settings import Settings, load_settings
from trading_platform.db.models import (
    AccountReconciliationRun,
    Job,
    JobDependency,
    JobEvent,
    JobLog,
    JobStatus,
    MarketDataIngestionRun,
    StrategyRun,
)
from trading_platform.db.session import session_scope
from trading_platform.services import recovery as recovery_service
from trading_platform.services.batch_outcomes import derive_job_outcome

DEFAULT_LIMIT = 20
MAX_LIMIT = 100
DEFAULT_LOG_PAGE_SIZE = 100
MAX_LOG_PAGE_SIZE = 500


class JobResourceKind(StrEnum):
    """Closed vocabulary of resources a Job may link to (D-04).

    Phase 19 defined exactly one member. Phase 20 adds
    market_data_ingestion_run; 20.1-08 adds account_reconciliation_run; nothing outside this module may add a member
    without a resources[] builder here.
    """

    STRATEGY_RUN = "strategy_run"
    MARKET_DATA_INGESTION_RUN = "market_data_ingestion_run"
    # 20.1-08 (ACCT-01, D-09 generalization): the owner-less account-level result.
    ACCOUNT_RECONCILIATION_RUN = "account_reconciliation_run"


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

    def get_job_recovery(self, job_id: str) -> dict[str, Any]:
        """R3 (REC-01): the uncertain-outcome recovery view of one Job.

        Delegates to ``services.recovery.get_job_recovery`` (plain dicts, read-only,
        bounded statements independent of history size). ``LookupError`` for an unknown
        Job. Identical before and after any execution operation ends.
        """

        job_uuid = uuid.UUID(job_id)
        with session_scope(self.settings) as session:
            return recovery_service.get_job_recovery(session, job_uuid)

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

            # D-04/D-05/D-07/D-08: resources[] is derived at read time from
            # the strategy_runs.job_id / market_data_ingestion_runs.job_id
            # FKs only -- never from logs, timestamps, or trigger_source --
            # and is not filtered by Job status, so a stuck, timed-out, or
            # cancelled Job never hides its run(s) (D-13). A Job may now link
            # more than one StrategyRun (e.g. a paper session's internal
            # reconciliation + execution runs), so this is a loop over
            # .scalars().all() rather than a single-row lookup (Pitfall 1).
            linked_runs = (
                session.execute(
                    select(StrategyRun)
                    .where(StrategyRun.job_id == job_uuid)
                    .order_by(StrategyRun.started_at.asc(), StrategyRun.id.asc())
                )
                .scalars()
                .all()
            )
            linked_ingestion_runs = (
                session.execute(
                    select(MarketDataIngestionRun)
                    .where(MarketDataIngestionRun.job_id == job_uuid)
                    .order_by(
                        MarketDataIngestionRun.started_at.asc(),
                        MarketDataIngestionRun.id.asc(),
                    )
                )
                .scalars()
                .all()
            )
            linked_account_runs = (
                session.execute(
                    select(AccountReconciliationRun)
                    .where(AccountReconciliationRun.job_id == job_uuid)
                    .order_by(
                        AccountReconciliationRun.started_at.asc(), AccountReconciliationRun.id.asc()
                    )
                )
                .scalars()
                .all()
            )
            resources: list[dict[str, Any]] = [
                {
                    "kind": JobResourceKind.STRATEGY_RUN.value,
                    "id": str(run.id),
                    "status": run.status.value,
                    "links": {"self": f"/api/v1/runs/{run.id}"},
                }
                for run in linked_runs
            ]
            resources.extend(
                {
                    "kind": JobResourceKind.ACCOUNT_RECONCILIATION_RUN.value,
                    "id": str(run.id),
                    "status": run.status,
                    "links": {"self": f"/api/v1/runs/{run.id}"},
                }
                for run in linked_account_runs
            )
            resources.extend(
                {
                    "kind": JobResourceKind.MARKET_DATA_INGESTION_RUN.value,
                    "id": str(run.id),
                    "status": run.status,
                    "links": {},
                }
                for run in linked_ingestion_runs
            )

            # D-20: payload plus retry lineage, derived at read time.
            # retried_as_job_id is the reverse of retry_of_job_id -- the
            # UNIQUE constraint on retry_of_job_id (0021) guarantees at most
            # one match.
            retried_as_job_id = session.execute(
                select(Job.id).where(Job.retry_of_job_id == job_uuid)
            ).scalar()

            detail = {
                "id": str(job.id),
                "job_type": job.job_type,
                "status": job.status.value,
                "outcome": _job_outcome(job),
                "queued_at": _dt(job.queued_at),
                "started_at": _dt(job.started_at),
                "completed_at": _dt(job.completed_at),
                "failure_reason": _enum_value(job.failure_reason),
                "failure_message": job.failure_message,
                "outcome_uncertain": job.outcome_uncertain,
                "result_summary": job.result_summary,
                "payload": job.payload,
                "retry_of_job_id": _uuid_value(job.retry_of_job_id),
                "retried_as_job_id": _uuid_value(retried_as_job_id),
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
                "resources": resources,
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
        "outcome": _job_outcome(job),
        "queued_at": _dt(job.queued_at),
        "started_at": _dt(job.started_at),
        "completed_at": _dt(job.completed_at),
        "failure_reason": _enum_value(job.failure_reason),
        "outcome_uncertain": job.outcome_uncertain,
        "cancellation_requested_at": _dt(job.cancellation_requested_at),
        "progress": _serialize_progress(job),
    }


def _job_outcome(job: Job) -> str | None:
    """Additive COR-03 batch outcome (derived, never a Job column)."""

    outcome = derive_job_outcome(job.job_type, job.status, job.result_summary)
    return outcome.value if outcome is not None else None


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
