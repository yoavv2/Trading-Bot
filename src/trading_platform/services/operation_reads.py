"""Read-only execution operation observation (R2, 05-INTERIM-API-OPERATIONS; REC-02).

Plain dicts, no ORM object crosses this boundary. Every read is write-free: a window that
already elapsed is reported through the PURE effective state (``will_end: true``) and is
never persisted here (D-21: only ``operations.touch_operation`` persists expiry, from
Continue submit/run, End, a new session request and run-time operation creation).

Statement counts are hard bounded and independent of history size: the list read issues one
statement for the operations (with strategies), two for intents/orders/attempts, one for the
Job links, two for live Jobs and one for the calendar window; the detail read adds none.
``operation_for_jobs`` (job id -> {id, state, reason}) is ONE statement; 20.1-14 uses it.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_platform.core import clock
from trading_platform.core.settings import Settings, load_settings
from trading_platform.db.models import (
    ExecutionOperation,
    ExecutionOperationJob,
    Job,
    OperationState,
    Strategy,
)
from trading_platform.db.session import session_scope
from trading_platform.services.calendar_facts import load_calendar_window
from trading_platform.services.execution import operations as op_domain
from trading_platform.services.execution.operations import (
    IntentFact,
    OperationNotFoundError,
)

DEFAULT_LIMIT = 20
MAX_LIMIT = 100


class StrategyNotFoundError(LookupError):
    """The ``strategy_id`` filter names no strategy."""

    def __init__(self, strategy_id: str) -> None:
        super().__init__(f"Strategy '{strategy_id}' was not found.")
        self.strategy_id = strategy_id


class InvalidStateFilterError(ValueError):
    """The ``state`` filter is not one of the five closed operation states."""

    def __init__(self, state: str) -> None:
        super().__init__(f"'{state}' is not an execution operation state.")
        self.state = state


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _intent_dict(fact: IntentFact) -> dict[str, Any]:
    row = fact.row
    return {
        "intent_id": str(row.id),
        "sequence": row.sequence,
        "symbol": fact.ticker,
        "side": row.side,
        "quantity": str(row.quantity),
        "reference_price": str(row.reference_price) if row.reference_price is not None else None,
        "client_order_id": row.client_order_id,
        "paper_order_id": str(row.paper_order_id) if row.paper_order_id is not None else None,
        "state": fact.state.value,
        "disposition": row.disposition,
        "attempts": len(fact.attempts),
    }


def _operation_dict(
    operation: ExecutionOperation,
    strategy_public_id: str,
    facts: Sequence[IntentFact],
    jobs: Sequence[dict[str, Any]],
    effective: op_domain.EffectiveState,
) -> dict[str, Any]:
    return {
        "operation_id": str(operation.id),
        "strategy_id": strategy_public_id,
        "as_of_session": operation.as_of_session.isoformat(),
        "risk_run_id": str(operation.risk_run_id),
        # The EFFECTIVE state at ``as_of`` (what a touch would persist), then the stored one.
        "state": effective.state.value,
        "reason": effective.reason,
        "reason_detail": operation.reason_detail,
        "persisted_state": effective.persisted_state.value,
        "persisted_reason": effective.persisted_reason,
        "next_action": effective.next_action.value,
        "will_end": effective.will_end,
        "takeover_pending": effective.takeover_pending,
        "window": effective.window.value,
        "intents": [_intent_dict(fact) for fact in facts],
        "working_orders": op_domain.operation_working_orders(facts),
        "unresolved_intents": op_domain.operation_unresolved_intents(facts),
        "jobs": list(jobs),
        "execution_epoch": operation.execution_epoch,
        "executor_job_id": (
            str(operation.executor_job_id) if operation.executor_job_id is not None else None
        ),
        "last_guarded_at": _iso(operation.last_guarded_at),
        "basis_verification": operation.basis_verification,
        "ended_by": operation.ended_by,
        "end_reason": operation.end_reason,
        "state_changed_at": _iso(operation.state_changed_at),
        "created_at": _iso(operation.created_at),
        "as_of": effective.as_of.isoformat(),
    }


class OperationReadService:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings

    @property
    def settings(self) -> Settings:
        return self._settings or load_settings()

    def list_operations(
        self,
        *,
        strategy_id: str | None = None,
        state: str | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> list[dict[str, Any]]:
        """Newest first. ``StrategyNotFoundError`` / ``InvalidStateFilterError`` before any read
        of operations."""

        capped = max(1, min(int(limit), MAX_LIMIT))
        state_filter: str | None = None
        if state is not None:
            try:
                state_filter = OperationState(state).value
            except ValueError as exc:
                raise InvalidStateFilterError(state) from exc
        with session_scope(self.settings) as session:
            statement = (
                select(ExecutionOperation, Strategy.strategy_id)
                .join(Strategy, Strategy.id == ExecutionOperation.strategy_id)
                .order_by(ExecutionOperation.created_at.desc(), ExecutionOperation.id.desc())
                .limit(capped)
            )
            if strategy_id is not None:
                known = session.execute(
                    select(Strategy.id).where(Strategy.strategy_id == strategy_id)
                ).scalar_one_or_none()
                if known is None:
                    raise StrategyNotFoundError(strategy_id)
                statement = statement.where(Strategy.strategy_id == strategy_id)
            if state_filter is not None:
                statement = statement.where(ExecutionOperation.state == state_filter)
            rows = session.execute(statement).all()
            return self._build(session, [(op, public_id) for op, public_id in rows])

    def get_operation(self, operation_id: uuid.UUID) -> dict[str, Any]:
        """``OperationNotFoundError`` for an unknown id."""

        with session_scope(self.settings) as session:
            row = session.execute(
                select(ExecutionOperation, Strategy.strategy_id)
                .join(Strategy, Strategy.id == ExecutionOperation.strategy_id)
                .where(ExecutionOperation.id == operation_id)
            ).one_or_none()
            if row is None:
                raise OperationNotFoundError(operation_id)
            return self._build(session, [(row[0], row[1])])[0]

    def operation_for_jobs(self, job_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, dict[str, Any]]:
        """Job id -> ``{id, state, reason}`` of the operation each Job is linked to, in ONE
        bounded statement (Jobs without an operation are absent)."""

        wanted = list(job_ids)
        if not wanted:
            return {}
        with session_scope(self.settings) as session:
            rows = session.execute(
                select(
                    ExecutionOperationJob.job_id,
                    ExecutionOperation.id,
                    ExecutionOperation.state,
                    ExecutionOperation.reason,
                )
                .join(
                    ExecutionOperation,
                    ExecutionOperation.id == ExecutionOperationJob.operation_id,
                )
                .where(ExecutionOperationJob.job_id.in_(wanted))
                .order_by(ExecutionOperationJob.created_at)
            ).all()
        result: dict[uuid.UUID, dict[str, Any]] = {}
        for job_id, operation_id, state, reason in rows:
            if job_id is None:
                continue
            result[job_id] = {"id": str(operation_id), "state": state, "reason": reason}
        return result

    def _build(
        self, session: Session, operations: Sequence[tuple[ExecutionOperation, str]]
    ) -> list[dict[str, Any]]:
        if not operations:
            return []
        settings = self.settings
        as_of = clock.now_utc()
        ids = [operation.id for operation, _public in operations]
        facts = op_domain.load_intent_facts(session, ids)
        live = op_domain.live_job_ids(session, ids)
        jobs_by_operation: dict[uuid.UUID, list[dict[str, Any]]] = {op_id: [] for op_id in ids}
        link_rows = session.execute(
            select(
                ExecutionOperationJob.operation_id,
                ExecutionOperationJob.job_id,
                ExecutionOperationJob.mode,
                Job.status,
            )
            .outerjoin(Job, Job.id == ExecutionOperationJob.job_id)
            .where(ExecutionOperationJob.operation_id.in_(ids))
            .order_by(ExecutionOperationJob.created_at)
        ).all()
        for operation_id, job_id, mode, status in link_rows:
            jobs_by_operation[operation_id].append(
                {
                    "job_id": str(job_id) if job_id is not None else None,
                    "mode": mode,
                    "status": status.value if status is not None else None,
                }
            )
        needs_window = any(
            OperationState(operation.state)
            not in (OperationState.TERMINATED, OperationState.COMPLETED)
            for operation, _public in operations
        )
        window = (
            load_calendar_window(session, now=as_of, settings=settings) if needs_window else None
        )
        items: list[dict[str, Any]] = []
        for operation, public_id in operations:
            if window is None or OperationState(operation.state) in (
                OperationState.TERMINATED,
                OperationState.COMPLETED,
            ):
                verdict = op_domain.WindowVerdict.UNKNOWN
            else:
                verdict = op_domain.window_verdict(window, settings, operation.as_of_session)
            effective = op_domain.compute_effective_state(
                state=operation.state,
                reason=operation.reason,
                has_live_job=bool(live[operation.id]),
                verdict=verdict,
                as_of=as_of,
            )
            items.append(
                _operation_dict(
                    operation,
                    public_id,
                    facts[operation.id],
                    jobs_by_operation[operation.id],
                    effective,
                )
            )
        return items


__all__ = [
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "InvalidStateFilterError",
    "OperationNotFoundError",
    "OperationReadService",
    "StrategyNotFoundError",
]
