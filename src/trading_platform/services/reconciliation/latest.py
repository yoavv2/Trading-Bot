"""The ONE reader of the latest standalone reconciliation (ACCT-01, R-5).

Recovery (20.1-10), handover checks (20.1-12) and analytics all ask the same
question -- "what did the newest standalone broker reconciliation find?" -- so they
share this single read-only function set. A standalone reconciliation is either:

- an owner-less account-level run (``account_reconciliation_runs``), which qualifies
  for a query about ANY strategy; or
- a strategy-scoped reconciliation run created by a ``reconciliation`` Job
  (``StrategyRun.job_id`` joins a Job of that type AND ``trigger_source == 'job'``).

An in-session reconciliation (``trigger_source`` ``<x>_reconciliation``, linked to a
paper-session Job) is NEVER standalone (R-5): it is part of a session, not an
independent broker check.

Everything here is a pure read (no writes, no flushes).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session

from trading_platform.db.models import (
    AccountReconciliationRun,
    ExecutionOperation,
    ExecutionOperationJob,
    ExternalBrokerActivity,
    Job,
    Strategy,
    StrategyRun,
    StrategyRunStatus,
    StrategyRunType,
)
from trading_platform.services.broker_jobs import (
    RECONCILIATION_JOB_TYPE,
    RECORD_EXTERNAL_ACTIVITY_JOB_TYPE,
    STATE_CHANGING_BROKER_JOB_TYPES,
)

ReconciliationScopeFilter = Literal["either", "account", "strategy"]

#: ``StrategyRun.trigger_source`` written by the ``reconciliation`` Job handler.
STANDALONE_TRIGGER_SOURCE = "job"


@dataclass(frozen=True)
class StandaloneReconciliation:
    """The newest qualifying standalone reconciliation, scope-tagged."""

    scope: Literal["account", "strategy"]
    run_id: uuid.UUID
    strategy_id: str | None
    status: str
    completed_at: datetime | None
    blocks_execution: bool
    unresolved_reasons: tuple[str, ...]

    @property
    def is_clean(self) -> bool:
        """Succeeded, not blocking and nothing unresolved: the only state a gate may read as clean."""

        return (
            self.status == "succeeded" and not self.blocks_execution and not self.unresolved_reasons
        )


def latest_account_reconciliation_run(session: Session) -> AccountReconciliationRun | None:
    """Newest account-level run by start time (any status). One statement."""

    return session.execute(
        select(AccountReconciliationRun)
        .order_by(
            AccountReconciliationRun.started_at.desc(), AccountReconciliationRun.created_at.desc()
        )
        .limit(1)
    ).scalar_one_or_none()


def latest_broker_effect_at(
    session: Session, strategy_public_id: str | None = None
) -> datetime | None:
    """Newest moment a broker-touching action last changed local state (A6 boundary).

    The newest of: ``completed_at`` of paper-session / broker-order-sync Jobs, and the
    newest ``external_broker_activity.created_at`` (a recording's effect time).
    ``record-external-activity`` Jobs are EXCLUDED from the Job side on purpose: the
    handler runs its own fresh account reconciliation before the Job completes, so the
    Job's ``completed_at`` always postdates that fresh check and would make it
    non-qualifying. ``None`` when neither exists. Two statements, read-only.

    ``strategy_public_id`` (20.1-10) narrows the Job side to that strategy's Jobs PLUS
    every account-level Job (no ``strategy_id`` in the payload); recordings always count
    (they are account-wide). ``None`` keeps the all-Jobs behaviour.

    SAF-05: a Continue Job carries only ``operation_id``; its strategy is its operation's
    strategy (payload ``strategy_id``, else the payload operation, else the newest
    ``execution_operation_jobs`` link: the same precedence as
    ``broker_jobs.job_strategy_public_id_sql``), so a completed Continue Job of A never
    moves B's effect boundary.
    """

    job_types = STATE_CHANGING_BROKER_JOB_TYPES - {RECORD_EXTERNAL_ACTIVITY_JOB_TYPE}
    job_stmt = select(func.max(Job.completed_at)).where(
        Job.job_type.in_(sorted(job_types)), Job.completed_at.is_not(None)
    )
    if strategy_public_id is not None:
        via_payload_operation = (
            select(Strategy.strategy_id)
            .select_from(ExecutionOperation)
            .join(Strategy, Strategy.id == ExecutionOperation.strategy_id)
            .where(cast(ExecutionOperation.id, String) == Job.payload["operation_id"].as_string())
            .scalar_subquery()
        )
        via_link = (
            select(Strategy.strategy_id)
            .select_from(ExecutionOperationJob)
            .join(ExecutionOperation, ExecutionOperation.id == ExecutionOperationJob.operation_id)
            .join(Strategy, Strategy.id == ExecutionOperation.strategy_id)
            .where(ExecutionOperationJob.job_id == Job.id)
            .order_by(ExecutionOperationJob.created_at.desc())
            .limit(1)
            .scalar_subquery()
        )
        job_strategy = func.coalesce(
            Job.payload["strategy_id"].as_string(), via_payload_operation, via_link
        )
        job_stmt = job_stmt.where(or_(job_strategy == strategy_public_id, job_strategy.is_(None)))
    job_time = session.execute(job_stmt).scalar_one_or_none()
    record_time = session.execute(
        select(func.max(ExternalBrokerActivity.created_at))
    ).scalar_one_or_none()
    candidates = [value for value in (job_time, record_time) if value is not None]
    return max(candidates) if candidates else None


def latest_standalone_reconciliation(
    session: Session,
    strategy_public_id: str | None = None,
    scope: ReconciliationScopeFilter = "either",
    completed_after: datetime | None = None,
    completed_before: datetime | None = None,
) -> StandaloneReconciliation | None:
    """Newest COMPLETED standalone reconciliation across the requested scope(s).

    ``strategy_public_id`` narrows only the strategy half; the account half always
    qualifies. ``completed_after`` keeps runs completed strictly after that instant and
    ``completed_before`` runs completed at or before it (20.1-15 evaluation-basis window).
    At most two statements regardless of history size (one per scope).
    """

    candidates: list[StandaloneReconciliation] = []

    if scope in ("either", "account"):
        account_stmt = select(AccountReconciliationRun).where(
            AccountReconciliationRun.completed_at.is_not(None)
        )
        if completed_after is not None:
            account_stmt = account_stmt.where(
                AccountReconciliationRun.completed_at > completed_after
            )
        if completed_before is not None:
            account_stmt = account_stmt.where(
                AccountReconciliationRun.completed_at <= completed_before
            )
        account_run = session.execute(
            account_stmt.order_by(AccountReconciliationRun.completed_at.desc()).limit(1)
        ).scalar_one_or_none()
        if account_run is not None:
            candidates.append(
                StandaloneReconciliation(
                    scope="account",
                    run_id=account_run.id,
                    strategy_id=None,
                    status=account_run.status,
                    completed_at=account_run.completed_at,
                    blocks_execution=bool(account_run.blocks_execution),
                    unresolved_reasons=tuple(
                        str(r) for r in (account_run.unresolved_reasons or [])
                    ),
                )
            )

    if scope in ("either", "strategy"):
        strategy_stmt = (
            select(StrategyRun, Strategy.strategy_id)
            .join(Job, Job.id == StrategyRun.job_id)
            .join(Strategy, Strategy.id == StrategyRun.strategy_id)
            .where(
                StrategyRun.run_type == StrategyRunType.RECONCILIATION,
                StrategyRun.trigger_source == STANDALONE_TRIGGER_SOURCE,
                Job.job_type == RECONCILIATION_JOB_TYPE,
                StrategyRun.completed_at.is_not(None),
            )
        )
        if strategy_public_id is not None:
            strategy_stmt = strategy_stmt.where(Strategy.strategy_id == strategy_public_id)
        if completed_after is not None:
            strategy_stmt = strategy_stmt.where(StrategyRun.completed_at > completed_after)
        if completed_before is not None:
            strategy_stmt = strategy_stmt.where(StrategyRun.completed_at <= completed_before)
        row = session.execute(
            strategy_stmt.order_by(StrategyRun.completed_at.desc()).limit(1)
        ).one_or_none()
        if row is not None:
            run, public_id = row
            summary = run.result_summary or {}
            candidates.append(
                StandaloneReconciliation(
                    scope="strategy",
                    run_id=run.id,
                    strategy_id=public_id,
                    status=run.status.value,
                    completed_at=run.completed_at,
                    # A strategy run that is not SUCCEEDED, or whose summary does not
                    # positively say "not blocking", is treated as blocking (fail closed).
                    blocks_execution=(
                        run.status is not StrategyRunStatus.SUCCEEDED
                        or summary.get("blocks_execution", True) is not False
                    ),
                    unresolved_reasons=tuple(str(r) for r in summary.get("unresolved_reasons", [])),
                )
            )

    if not candidates:
        return None
    return max(candidates, key=lambda c: c.completed_at or datetime.min.replace(tzinfo=UTC))


__all__ = [
    "STANDALONE_TRIGGER_SOURCE",
    "ReconciliationScopeFilter",
    "StandaloneReconciliation",
    "latest_account_reconciliation_run",
    "latest_broker_effect_at",
    "latest_standalone_reconciliation",
]
