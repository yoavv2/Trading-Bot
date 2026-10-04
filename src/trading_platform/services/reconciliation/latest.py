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

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trading_platform.db.models import (
    AccountReconciliationRun,
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


def latest_broker_effect_at(session: Session) -> datetime | None:
    """Newest moment a broker-touching action last changed local state (A6 boundary).

    The newest of: ``completed_at`` of paper-session / broker-order-sync Jobs, and the
    newest ``external_broker_activity.created_at`` (a recording's effect time).
    ``record-external-activity`` Jobs are EXCLUDED from the Job side on purpose: the
    handler runs its own fresh account reconciliation before the Job completes, so the
    Job's ``completed_at`` always postdates that fresh check and would make it
    non-qualifying. ``None`` when neither exists. Two statements, read-only.
    """

    job_types = STATE_CHANGING_BROKER_JOB_TYPES - {RECORD_EXTERNAL_ACTIVITY_JOB_TYPE}
    job_time = session.execute(
        select(func.max(Job.completed_at)).where(
            Job.job_type.in_(sorted(job_types)), Job.completed_at.is_not(None)
        )
    ).scalar_one_or_none()
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
) -> StandaloneReconciliation | None:
    """Newest COMPLETED standalone reconciliation across the requested scope(s).

    ``strategy_public_id`` narrows only the strategy half; the account half always
    qualifies. ``completed_after`` keeps runs completed strictly after that instant.
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
