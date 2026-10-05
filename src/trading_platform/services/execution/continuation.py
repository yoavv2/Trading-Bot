"""Continue session preconditions (REC-02, D-19), read-only and shared by submit and run time.

One predicate decides both the submit-time refusal of a Continue Job
(``operation_not_paused``, ``working_order_commitments_unaccounted``, ``awaiting_reconciliation``)
and the run-time re-verification inside the advisory lock, so the two cannot drift. Everything
here is read-only: nothing is written, nothing is sent.

``continue_precheck`` evaluates the EFFECTIVE state: an operation whose window elapsed (or whose
evaluation was superseded) is accepted and skips the working-order and reconciliation conditions,
so the run-time ``touch_operation`` can apply the termination (05 E11). A crash-left ``running``
operation (``takeover_pending``) is accepted for run-time takeover; a ``running`` operation with
a live Job is ``operation_not_paused``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.orm import Session

from trading_platform.core import clock
from trading_platform.core.settings import Settings
from trading_platform.db.models import ExecutionOperation, OperationState
from trading_platform.services.execution.operations import PausedReason, effective_state
from trading_platform.services.execution.permission import strategy_working_orders
from trading_platform.services.reconciliation.latest import (
    latest_broker_effect_at,
    latest_standalone_reconciliation,
)
from trading_platform.services.recovery import GateCode, strategy_recovery_status

OPERATION_NOT_PAUSED = "operation_not_paused"
WORKING_ORDER_COMMITMENTS_UNACCOUNTED = "working_order_commitments_unaccounted"
AWAITING_RECONCILIATION = "awaiting_reconciliation"


@dataclass(frozen=True)
class ContinueRefusal:
    """A Continue precondition that does not hold (the code is the typed 409 code)."""

    code: str
    detail: dict[str, str] = field(default_factory=dict)


def awaiting_reconciliation(session: Session, strategy_id: str) -> bool:
    """True when no clean-or-blocking standalone reconciliation completed after the latest
    broker-touching effect (a sync Job, a session run or a recording): the same predicate the
    per-intent permission check uses for ``continuation=True``."""

    effect_at = latest_broker_effect_at(session, strategy_id)
    return latest_standalone_reconciliation(session, strategy_id, completed_after=effect_at) is None


def continue_precheck(
    session: Session,
    operation: ExecutionOperation,
    strategy_id: str,
    *,
    now: datetime | None = None,
    settings: Settings,
    exclude_job_id: uuid.UUID | None = None,
) -> ContinueRefusal | None:
    """The first failing Continue precondition, or ``None`` when Continue may proceed."""

    at = now or clock.now_utc()
    effective = effective_state(
        session, operation, now=at, settings=settings, exclude_job_id=exclude_job_id
    )
    if effective.will_end:
        return None
    if effective.state is not OperationState.PAUSED and not effective.takeover_pending:
        return ContinueRefusal(
            OPERATION_NOT_PAUSED,
            {
                "operation_id": str(operation.id),
                "operation_state": effective.state.value,
                "operation_reason": effective.reason or "",
            },
        )
    working = strategy_working_orders(session, strategy_id)
    if working:
        return ContinueRefusal(
            WORKING_ORDER_COMMITMENTS_UNACCOUNTED,
            {
                "operation_id": str(operation.id),
                "working_orders": ",".join(order.client_order_id for order in working[:20]),
            },
        )
    if awaiting_reconciliation(session, strategy_id):
        return ContinueRefusal(AWAITING_RECONCILIATION, {"operation_id": str(operation.id)})
    return None


def settled_blocker(session: Session, strategy_id: str) -> PausedReason | None:
    """Why a continuation with NOTHING left to send may not complete the operation: a working
    or unsynced order, or no sync plus clean reconciliation after the latest effect. ``None``
    means every submitted order is terminal and synced, so the operation completes."""

    if strategy_recovery_status(session, strategy_id).gate_code is GateCode.OUTCOME_UNRESOLVED:
        return PausedReason.OUTCOME_UNRESOLVED
    if strategy_working_orders(session, strategy_id):
        return PausedReason.WORKING_ORDER_COMMITMENTS_UNACCOUNTED
    effect_at = latest_broker_effect_at(session, strategy_id)
    reconciliation = latest_standalone_reconciliation(
        session, strategy_id, completed_after=effect_at
    )
    if reconciliation is None:
        return PausedReason.AWAITING_RECONCILIATION
    if not reconciliation.is_clean:
        return PausedReason.RECONCILIATION_BLOCKING
    return None


__all__ = [
    "AWAITING_RECONCILIATION",
    "OPERATION_NOT_PAUSED",
    "WORKING_ORDER_COMMITMENTS_UNACCOUNTED",
    "ContinueRefusal",
    "awaiting_reconciliation",
    "continue_precheck",
    "settled_blocker",
]
