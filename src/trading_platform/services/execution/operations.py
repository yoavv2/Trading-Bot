"""Execution operation: persisted state machine, lazy expiry, End, S1 fencing (REC-02, D-16..D-21).

An execution operation is ONE execution of ONE evaluation (strategy, evaluation session,
pinned risk run) that may span several Jobs. This module owns:

* the closed vocabularies (states, paused / re-evaluation / terminated reasons, next actions)
  whose database twins live in ``db/models/execution_operation.py`` (a test pins both);
* ``create_operation`` / ``transition`` / ``begin_continuation`` (compare-and-set moves);
* ``effective_state`` (pure, read-only: reports ``will_end`` and ``takeover_pending``) and
  ``touch_operation`` (the ONLY place expiry is persisted, D-21: lazy, never by a read);
* ``end_operation`` (D-20): terminates the operation and its UNSENT intents only. It never
  touches a broker order, never resolves an uncertain outcome, never erases a recovery
  record or an attempt log and never changes trading permission. This module imports no
  broker client and has no path that ends a broker order (a source test pins it);
* the S1-R3 submission authority and fencing primitives: ``acquire_execution`` (takeover),
  ``authorize_send`` (transaction T1: the durable attempt row BEFORE any POST),
  ``complete_attempt_late`` and the epoch-fenced compare-and-set helpers.

S1-R3 guarantee, restated (see 20.1-11-PLAN S1-R3): (G1) every attempt is durably recorded
before its request can leave the process; (G2) while any submission of the strategy may
still produce an execution no other request is authorized; (G3) every POST is preceded by a
durable attempt row. Limitations: the platform cannot stop one already-recorded request
from reaching the broker late (L1, L5) and has no way to end a broker order.

Reads (``effective_state`` with a loaded window, the fact loaders) perform no writes.
Run-time functions take an optional ``now`` that defaults to ``core.clock.now_utc()``.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import and_, exists, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from trading_platform.core import clock
from trading_platform.core.settings import Settings, load_settings
from trading_platform.db.models import (
    OPEN_OPERATION_STATES,
    AttemptOutcomeClass,
    ExecutionEvent,
    ExecutionOperation,
    ExecutionOperationIntent,
    ExecutionOperationJob,
    IntentDisposition,
    Job,
    JobStatus,
    OperationJobMode,
    OperationState,
    OrderLifecycleState,
    OrderSubmissionAttempt,
    OrderTransitionEventType,
    PaperOrder,
    Strategy,
    Symbol,
)
from trading_platform.db.session import session_scope
from trading_platform.services.calendar import CalendarOutOfBoundsError, previous_session_date
from trading_platform.services.calendar_facts import (
    CalendarWindow,
    FactStatus,
    WindowClosedReason,
    WindowStatus,
    execution_window_from_window,
    load_calendar_window,
    trading_day_from_window,
)
from trading_platform.services.concurrency_guard import RunLock
from trading_platform.services.execution.attempts import (
    AttemptAlreadyCompletedError,
    AttemptRecord,
    SubmissionClass,
    SubmissionIntentState,
    classify_submission,
    derive_intent_state,
    proven_not_sent,
)
from trading_platform.services.execution.transition import (
    IllegalOrderTransition,
    OrderTransitionRequest,
    apply_order_transition,
)
from trading_platform.services.recovery import GateCode, strategy_recovery_status

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Closed vocabularies
# ---------------------------------------------------------------------------

# The five operation states (``OperationState``) are defined once, next to their CHECK, in
# ``db/models/execution_operation.py`` and re-exported here.


class PausedReason(StrEnum):
    """Closed paused reasons (D-18, 03 sec.3.7 A2 plus the review amendments)."""

    WORKING_ORDER_COMMITMENTS_UNACCOUNTED = "working_order_commitments_unaccounted"
    AWAITING_RECONCILIATION = "awaiting_reconciliation"
    KILL_SWITCH_TRIPPED = "kill_switch_tripped"
    STRATEGY_DISABLED = "strategy_disabled"
    RECONCILIATION_BLOCKING = "reconciliation_blocking"
    UNRECOGNIZED_BROKER_ACTIVITY = "unrecognized_broker_activity"
    OUTCOME_UNRESOLVED = "outcome_unresolved"
    BROKER_UNAVAILABLE = "broker_unavailable"
    EXECUTION_WINDOW_NOT_OPEN = "execution_window_not_open"
    # Added by review round 2 (2026-10-03), amendments of D-18 / 03 sec.3.7 A2:
    PRICE_UNAVAILABLE = "price_unavailable"
    NOT_ACTIVE_PAPER_STRATEGY = "not_active_paper_strategy"
    # Moved from the re-evaluation reasons by round 5 (PD-1 approved 2026-10-04).
    PRICE_MOVED_BEYOND_TOLERANCE = "price_moved_beyond_tolerance"


class ReevaluationReason(StrEnum):
    """Fixed re-evaluation reasons; ``risk_limit_failed:<RiskDecisionCode>`` is built separately."""

    EVALUATION_DATA_CHANGED = "evaluation_data_changed"
    STRATEGY_SETTINGS_CHANGED = "strategy_settings_changed"


class TerminatedReason(StrEnum):
    EXECUTION_WINDOW_ELAPSED = "execution_window_elapsed"
    CANCELLED_BY_OPERATOR = "cancelled_by_operator"
    EVALUATION_SUPERSEDED = "evaluation_superseded"


RISK_LIMIT_FAILED_PREFIX = "risk_limit_failed:"

#: The portfolio-dependent subset of ``services.risk.RiskDecisionCode`` (values; a test pins
#: that every entry is a real code). Signal- and data-level codes never pause an operation.
RISK_LIMIT_PORTFOLIO_CODES: frozenset[str] = frozenset(
    {
        "duplicate_open_position",
        "no_open_position",
        "max_positions",
        "strategy_allocation_cap",
        "total_allocation_cap",
        "insufficient_cash",
        "order_rounds_to_zero",
    }
)


def risk_limit_failed(code: StrEnum | str) -> str:
    """The re-evaluation reason ``risk_limit_failed:<code>`` for a portfolio-dependent code."""

    value = code.value if isinstance(code, StrEnum) else str(code)
    if value not in RISK_LIMIT_PORTFOLIO_CODES:
        raise ValueError(f"'{value}' is not a portfolio-dependent risk decision code.")
    return f"{RISK_LIMIT_FAILED_PREFIX}{value}"


def is_reevaluation_reason(reason: str) -> bool:
    if reason in {member.value for member in ReevaluationReason}:
        return True
    return (
        reason.startswith(RISK_LIMIT_FAILED_PREFIX)
        and reason[len(RISK_LIMIT_FAILED_PREFIX) :] in RISK_LIMIT_PORTFOLIO_CODES
    )


class NextAction(StrEnum):
    """Closed next-action vocabulary (R2): exactly one value per state / reason."""

    WAIT_FOR_ORDER_THEN_SYNC_AND_CONTINUE = "wait_for_order_then_sync_and_continue"
    SYNC_RECONCILE_THEN_CONTINUE = "sync_reconcile_then_continue"
    RESET_KILL_SWITCH_THEN_CONTINUE = "reset_kill_switch_then_continue"
    ENABLE_STRATEGY_THEN_CONTINUE = "enable_strategy_then_continue"
    RESOLVE_RECONCILIATION_THEN_CONTINUE = "resolve_reconciliation_then_continue"
    RECORD_EXTERNAL_ACTIVITY_THEN_CONTINUE = "record_external_activity_then_continue"
    RECOVER_OUTCOME_THEN_CONTINUE = "recover_outcome_then_continue"
    RETRY_WHEN_BROKER_AVAILABLE = "retry_when_broker_available"
    WAIT_FOR_PRICE_THEN_CONTINUE = "wait_for_price_then_continue"
    WAIT_FOR_WINDOW_OPEN_THEN_CONTINUE = "wait_for_window_open_then_continue"
    END_OPERATION_THEN_REEVALUATE = "end_operation_then_reevaluate"
    NEW_EVALUATION_REQUIRED = "new_evaluation_required"
    NONE = "none"


_PAUSED_NEXT_ACTION: dict[PausedReason, NextAction] = {
    PausedReason.WORKING_ORDER_COMMITMENTS_UNACCOUNTED: (
        NextAction.WAIT_FOR_ORDER_THEN_SYNC_AND_CONTINUE
    ),
    PausedReason.AWAITING_RECONCILIATION: NextAction.SYNC_RECONCILE_THEN_CONTINUE,
    PausedReason.KILL_SWITCH_TRIPPED: NextAction.RESET_KILL_SWITCH_THEN_CONTINUE,
    PausedReason.STRATEGY_DISABLED: NextAction.ENABLE_STRATEGY_THEN_CONTINUE,
    PausedReason.RECONCILIATION_BLOCKING: NextAction.RESOLVE_RECONCILIATION_THEN_CONTINUE,
    PausedReason.UNRECOGNIZED_BROKER_ACTIVITY: NextAction.RECORD_EXTERNAL_ACTIVITY_THEN_CONTINUE,
    PausedReason.OUTCOME_UNRESOLVED: NextAction.RECOVER_OUTCOME_THEN_CONTINUE,
    PausedReason.BROKER_UNAVAILABLE: NextAction.RETRY_WHEN_BROKER_AVAILABLE,
    PausedReason.EXECUTION_WINDOW_NOT_OPEN: NextAction.WAIT_FOR_WINDOW_OPEN_THEN_CONTINUE,
    PausedReason.PRICE_UNAVAILABLE: NextAction.WAIT_FOR_PRICE_THEN_CONTINUE,
    PausedReason.PRICE_MOVED_BEYOND_TOLERANCE: NextAction.WAIT_FOR_PRICE_THEN_CONTINUE,
    # Ownership cannot be restored while the operation is open (re-seeding needs A1 'no open
    # operation'), so 'restore ownership then continue' would be a dead end.
    PausedReason.NOT_ACTIVE_PAPER_STRATEGY: NextAction.END_OPERATION_THEN_REEVALUATE,
}


def next_action(state: OperationState | str, reason: str | None = None) -> NextAction:
    """The closed next action of a (state, reason) pair; total over every legal pair.

    Raises ``ValueError`` for a pair the database CHECK would reject.
    """

    state = OperationState(state)
    if state in (OperationState.RUNNING, OperationState.COMPLETED):
        if reason is not None:
            raise ValueError(f"State '{state.value}' carries no reason (got '{reason}').")
        return NextAction.NONE
    if reason is None:
        raise ValueError(f"State '{state.value}' requires a reason.")
    if state is OperationState.PAUSED:
        return _PAUSED_NEXT_ACTION[PausedReason(reason)]
    if state is OperationState.REQUIRES_REEVALUATION:
        if not is_reevaluation_reason(reason):
            raise ValueError(f"'{reason}' is not a re-evaluation reason.")
        return NextAction.END_OPERATION_THEN_REEVALUATE
    TerminatedReason(reason)
    return NextAction.NEW_EVALUATION_REQUIRED


def validate_state_reason(state: OperationState | str, reason: str | None) -> None:
    """Python twin of ``ck_execution_operations_reason_by_state`` (raises ``ValueError``)."""

    next_action(state, reason)


#: Legal moves of ``transition``; paused -> running exists ONLY through ``begin_continuation``.
_LEGAL_TRANSITIONS: dict[OperationState, frozenset[OperationState]] = {
    OperationState.RUNNING: frozenset(
        {
            OperationState.PAUSED,
            OperationState.REQUIRES_REEVALUATION,
            OperationState.TERMINATED,
            OperationState.COMPLETED,
        }
    ),
    OperationState.PAUSED: frozenset(
        {
            OperationState.PAUSED,
            OperationState.REQUIRES_REEVALUATION,
            OperationState.TERMINATED,
        }
    ),
    OperationState.REQUIRES_REEVALUATION: frozenset({OperationState.TERMINATED}),
    OperationState.TERMINATED: frozenset(),
    OperationState.COMPLETED: frozenset(),
}

_FINAL_STATES = frozenset({OperationState.TERMINATED, OperationState.COMPLETED})
_OPEN_STATE_VALUES = tuple(state.value for state in OPEN_OPERATION_STATES)

#: Unsent intent states a termination may move to a terminal unsent disposition.
_UNSENT_STATES = frozenset(
    {
        SubmissionIntentState.PLANNED,
        SubmissionIntentState.REGISTERED_UNSENT,
        SubmissionIntentState.NOT_SENT,
    }
)

BLOCKING_WORKING_ORDER = "working_order_commitments_unaccounted"
BLOCKING_OUTCOME_UNRESOLVED = "outcome_unresolved"

DEFAULT_SEND_AUTHORIZATION_TTL_SECONDS = 5

# ---------------------------------------------------------------------------
# Typed errors
# ---------------------------------------------------------------------------


class OperationError(RuntimeError):
    """Base class of every typed operation error."""


class OperationNotFoundError(LookupError):
    def __init__(self, operation_id: uuid.UUID) -> None:
        super().__init__(f"Execution operation '{operation_id}' was not found.")
        self.operation_id = operation_id


class OperationOpenError(OperationError):
    """The strategy already has an OPEN operation (unique index twin: ``operation_open``)."""

    def __init__(self, strategy_id: uuid.UUID, operation_id: uuid.UUID | None = None) -> None:
        super().__init__(f"Strategy '{strategy_id}' already has an open execution operation.")
        self.strategy_id = strategy_id
        self.operation_id = operation_id


class RiskRunAlreadyOperatedError(OperationError):
    """The pinned risk run already has an operation (``risk_run_already_operated``)."""

    def __init__(self, strategy_id: uuid.UUID, risk_run_id: uuid.UUID) -> None:
        super().__init__(f"Risk run '{risk_run_id}' already has an execution operation.")
        self.strategy_id = strategy_id
        self.risk_run_id = risk_run_id


class OperationConflictError(OperationError):
    """A compare-and-set lost (state moved, or the fencing token is stale)."""

    def __init__(self, operation_id: uuid.UUID, detail: str) -> None:
        super().__init__(f"Execution operation '{operation_id}' conflict: {detail}.")
        self.operation_id = operation_id
        self.detail = detail


class IllegalOperationTransition(OperationError):
    def __init__(
        self, operation_id: uuid.UUID, from_state: OperationState, to_state: OperationState
    ) -> None:
        super().__init__(
            f"Illegal operation transition for '{operation_id}': "
            f"{from_state.value} -> {to_state.value}."
        )
        self.operation_id = operation_id
        self.from_state = from_state
        self.to_state = to_state


class OperationRunningError(OperationError):
    """End refused: a queued or running Job of the operation exists (``operation_running``)."""

    def __init__(self, operation_id: uuid.UUID, running_job_ids: Sequence[uuid.UUID]) -> None:
        super().__init__(f"Execution operation '{operation_id}' has a queued or running Job.")
        self.operation_id = operation_id
        self.running_job_ids = tuple(running_job_ids)


class OperationNotOpenError(OperationError):
    """End refused: the operation is completed (``operation_not_open``)."""

    def __init__(self, operation_id: uuid.UUID, state: str) -> None:
        super().__init__(f"Execution operation '{operation_id}' is {state}, not open.")
        self.operation_id = operation_id
        self.state = state


class RunLockNotHeldError(OperationError):
    """``acquire_execution`` requires the session advisory lock to be held by the caller."""


class SendRefusal(StrEnum):
    """Closed reasons transaction T1 (``authorize_send``) refuses; nothing is sent."""

    OPERATION_NOT_FOUND = "operation_not_found"
    OPERATION_NOT_RUNNING = "operation_not_running"
    STALE_EPOCH = "stale_epoch"
    WRONG_EXECUTOR = "wrong_executor"
    LEASE_LOST = "lease_lost"
    INTENT_NOT_FOUND = "intent_not_found"
    INTENT_NOT_OPEN = "intent_not_open"
    INTENT_NOT_REGISTERED = "intent_not_registered"
    INTENT_NOT_SENDABLE = "intent_not_sendable"
    OUTCOME_UNRESOLVED = "outcome_unresolved"


class SendRefusedError(OperationError):
    def __init__(self, refusal: SendRefusal, detail: str | None = None) -> None:
        super().__init__(
            f"Send not authorized: {refusal.value}" + (f" ({detail})" if detail else "")
        )
        self.refusal = refusal
        self.detail = detail


class AttemptOwnershipError(OperationError):
    """A late completion came from a caller that did not create the attempt row."""


# ---------------------------------------------------------------------------
# Pure helpers: windows, effective state
# ---------------------------------------------------------------------------


class WindowVerdict(StrEnum):
    OPEN = "open"
    NOT_YET_OPEN = "not_yet_open"
    ELAPSED = "elapsed"
    SUPERSEDED = "superseded"
    UNKNOWN = "unknown"


def window_verdict(
    window: CalendarWindow, settings: Settings, evaluation_session: date
) -> WindowVerdict:
    """Where the operation's evaluation session S stands against the clock (pure).

    ``elapsed``: S is still ``previous_session(trading day)`` and now is past the cutoff.
    ``superseded``: the trading day moved on, so S is no longer the evaluation session.
    Any needed persisted calendar row missing is ``unknown`` (never an expiry).
    """

    day = trading_day_from_window(window)
    if day.status is FactStatus.UNKNOWN or day.date is None:
        return WindowVerdict.UNKNOWN
    try:
        previous = previous_session_date(day.date, window.exchange)
    except CalendarOutOfBoundsError:
        return WindowVerdict.UNKNOWN
    if previous > evaluation_session:
        return WindowVerdict.SUPERSEDED
    if previous < evaluation_session:
        return WindowVerdict.NOT_YET_OPEN
    execution = execution_window_from_window(window, settings, evaluation_session)
    if execution.status is WindowStatus.OPEN:
        return WindowVerdict.OPEN
    if execution.status is WindowStatus.UNKNOWN:
        return WindowVerdict.UNKNOWN
    if execution.closed_reason is WindowClosedReason.ELAPSED:
        return WindowVerdict.ELAPSED
    return WindowVerdict.NOT_YET_OPEN


_VERDICT_TERMINATED_REASON = {
    WindowVerdict.ELAPSED: TerminatedReason.EXECUTION_WINDOW_ELAPSED,
    WindowVerdict.SUPERSEDED: TerminatedReason.EVALUATION_SUPERSEDED,
}


@dataclass(frozen=True)
class EffectiveState:
    """The pure effective state of an operation at ``as_of`` (reads never persist it)."""

    persisted_state: OperationState
    persisted_reason: str | None
    state: OperationState
    reason: str | None
    will_end: bool
    takeover_pending: bool
    window: WindowVerdict
    next_action: NextAction
    as_of: datetime


def compute_effective_state(
    *,
    state: OperationState | str,
    reason: str | None,
    has_live_job: bool,
    verdict: WindowVerdict,
    as_of: datetime,
) -> EffectiveState:
    """Pure effective state.

    * A final state never changes.
    * An OPEN operation whose window elapsed (or whose evaluation was superseded) reports
      ``terminated`` with ``will_end`` True, except a ``running`` operation with a live Job
      (its executor decides at its next permission check; a Job is never pulled from under).
    * A ``running`` operation whose linked Jobs are all terminal reports ``takeover_pending``:
      the state stays ``running`` (persisted normalization happens ONLY in
      ``acquire_execution``) and the next action is ``sync_reconcile_then_continue``.
    """

    persisted = OperationState(state)
    if persisted in _FINAL_STATES:
        return EffectiveState(
            persisted_state=persisted,
            persisted_reason=reason,
            state=persisted,
            reason=reason,
            will_end=False,
            takeover_pending=False,
            window=verdict,
            next_action=next_action(persisted, reason),
            as_of=as_of,
        )
    expiring = verdict in _VERDICT_TERMINATED_REASON and not (
        persisted is OperationState.RUNNING and has_live_job
    )
    if expiring:
        ended = _VERDICT_TERMINATED_REASON[verdict]
        return EffectiveState(
            persisted_state=persisted,
            persisted_reason=reason,
            state=OperationState.TERMINATED,
            reason=ended.value,
            will_end=True,
            takeover_pending=False,
            window=verdict,
            next_action=next_action(OperationState.TERMINATED, ended.value),
            as_of=as_of,
        )
    takeover_pending = persisted is OperationState.RUNNING and not has_live_job
    action = (
        NextAction.SYNC_RECONCILE_THEN_CONTINUE
        if takeover_pending
        else next_action(persisted, reason)
    )
    return EffectiveState(
        persisted_state=persisted,
        persisted_reason=reason,
        state=persisted,
        reason=reason,
        will_end=False,
        takeover_pending=takeover_pending,
        window=verdict,
        next_action=action,
        as_of=as_of,
    )


def effective_state(
    session: Session,
    operation: ExecutionOperation,
    *,
    now: datetime | None = None,
    settings: Settings | None = None,
    window: CalendarWindow | None = None,
    has_live_job: bool | None = None,
) -> EffectiveState:
    """Effective state of one operation. Read-only: zero writes."""

    resolved = settings or load_settings()
    as_of = now or clock.now_utc()
    state = OperationState(operation.state)
    if state in _FINAL_STATES:
        verdict = WindowVerdict.UNKNOWN
    else:
        calendar = window or load_calendar_window(session, now=as_of, settings=resolved)
        verdict = window_verdict(calendar, resolved, operation.as_of_session)
    if has_live_job is None:
        has_live_job = bool(live_job_ids(session, [operation.id]).get(operation.id))
    return compute_effective_state(
        state=state,
        reason=operation.reason,
        has_live_job=has_live_job,
        verdict=verdict,
        as_of=as_of,
    )


# ---------------------------------------------------------------------------
# Fact loaders (read-only, bounded statement counts)
# ---------------------------------------------------------------------------


def live_job_ids(
    session: Session, operation_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[uuid.UUID]]:
    """Queued or running Jobs of each operation: linked Jobs PLUS paper-session Jobs whose
    payload ``operation_id`` matches (a Continue Job is linked only at ``begin_continuation``).
    Two statements whatever the number of operations."""

    live: dict[uuid.UUID, list[uuid.UUID]] = {op_id: [] for op_id in operation_ids}
    if not operation_ids:
        return live
    live_statuses = (JobStatus.QUEUED, JobStatus.RUNNING)
    linked = session.execute(
        select(ExecutionOperationJob.operation_id, Job.id)
        .join(Job, Job.id == ExecutionOperationJob.job_id)
        .where(ExecutionOperationJob.operation_id.in_(operation_ids), Job.status.in_(live_statuses))
    ).all()
    for operation_id, job_id in linked:
        live[operation_id].append(job_id)
    payload_operation = Job.payload["operation_id"].as_string()
    pending = session.execute(
        select(payload_operation, Job.id).where(
            Job.job_type == "paper-session",
            Job.status.in_(live_statuses),
            payload_operation.in_([str(op_id) for op_id in operation_ids]),
        )
    ).all()
    for raw_operation_id, pending_job_id in pending:
        pending_operation_id = uuid.UUID(str(raw_operation_id))
        job_uuid = uuid.UUID(str(pending_job_id))
        if job_uuid not in live[pending_operation_id]:
            live[pending_operation_id].append(job_uuid)
    return live


@dataclass(frozen=True)
class IntentFact:
    """One pinned intent with its order, attempts and derived closed state."""

    row: ExecutionOperationIntent
    ticker: str
    order: PaperOrder | None
    attempts: tuple[AttemptRecord, ...]
    attempt_log_registered: bool
    state: SubmissionIntentState
    proven_not_sent: bool


def intent_state(
    row: ExecutionOperationIntent,
    order: PaperOrder | None,
    attempts: Sequence[AttemptRecord],
) -> SubmissionIntentState:
    """The persisted unsent disposition when set, else the state derived from order + attempts.

    A broker statement never changes an intent state (round 5).
    """

    if row.disposition == IntentDisposition.EXPIRED_UNSENT.value:
        return SubmissionIntentState.EXPIRED_UNSENT
    if row.disposition == IntentDisposition.CANCELLED_UNSENT.value:
        return SubmissionIntentState.CANCELLED_UNSENT
    return derive_intent_state(order, attempts)


def _attempt_record(row: OrderSubmissionAttempt) -> AttemptRecord:
    return AttemptRecord(
        attempt_number=row.attempt_number,
        started_at=row.started_at,
        completed_at=row.completed_at,
        outcome_class=(
            AttemptOutcomeClass(row.outcome_class) if row.outcome_class is not None else None
        ),
        http_status=row.http_status,
        error_type=row.error_type,
        broker_message=row.broker_message,
    )


def load_intent_facts(
    session: Session, operation_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[IntentFact]]:
    """Intents of the given operations with orders and attempts: two statements, bounded by
    the number of operations asked for, independent of any history outside them."""

    facts: dict[uuid.UUID, list[IntentFact]] = {op_id: [] for op_id in operation_ids}
    if not operation_ids:
        return facts
    rows = session.execute(
        select(ExecutionOperationIntent, PaperOrder, Symbol.ticker)
        .join(Symbol, Symbol.id == ExecutionOperationIntent.symbol_id)
        .outerjoin(PaperOrder, PaperOrder.id == ExecutionOperationIntent.paper_order_id)
        .where(ExecutionOperationIntent.operation_id.in_(operation_ids))
        .order_by(ExecutionOperationIntent.operation_id, ExecutionOperationIntent.sequence)
    ).all()
    order_ids = [order.id for _row, order, _ticker in rows if order is not None]
    attempts_by_order: dict[uuid.UUID, list[AttemptRecord]] = {}
    if order_ids:
        for attempt in session.execute(
            select(OrderSubmissionAttempt)
            .where(OrderSubmissionAttempt.paper_order_id.in_(order_ids))
            .order_by(OrderSubmissionAttempt.paper_order_id, OrderSubmissionAttempt.attempt_number)
        ).scalars():
            attempts_by_order.setdefault(attempt.paper_order_id, []).append(
                _attempt_record(attempt)
            )
    for row, order, ticker in rows:
        attempts = tuple(attempts_by_order.get(order.id, ())) if order is not None else ()
        registered = _attempt_log_registered(row, order, attempts)
        facts[row.operation_id].append(
            IntentFact(
                row=row,
                ticker=ticker,
                order=order,
                attempts=attempts,
                attempt_log_registered=registered,
                state=intent_state(row, order, attempts),
                proven_not_sent=proven_not_sent(order, attempts, attempt_log_registered=registered),
            )
        )
    return facts


def _attempt_log_registered(
    row: ExecutionOperationIntent, order: PaperOrder | None, attempts: Sequence[AttemptRecord]
) -> bool:
    """Registered under the attempt-log invariant (S1-R3).

    True when the order has at least one attempt row, or when it was registered as the
    realisation of this pinned intent (created at or after the intent row). A legacy order
    reused later (created BEFORE the intent row, no attempts) is never proven not sent.
    """

    if attempts:
        return True
    if order is None:
        return True
    return order.created_at >= row.created_at


_LOCALLY_TERMINAL_STATUSES = frozenset(
    {
        OrderLifecycleState.FILLED,
        OrderLifecycleState.CANCELED,
        OrderLifecycleState.REJECTED,
        OrderLifecycleState.EXPIRED,
    }
)


def is_working_order(order: PaperOrder | None) -> bool:
    """TL-2 predicate: an order the broker knows that is not locally terminal, or that reached
    a terminal state without ever being synced (its fills may not be ingested yet).

    An UNKNOWN or PENDING order with no broker id is an unresolved INTENT, not a working order.
    """

    if order is None:
        return False
    at_broker = order.broker_order_id is not None or order.status in (
        OrderLifecycleState.SUBMITTED,
        OrderLifecycleState.PARTIALLY_FILLED,
    )
    if not at_broker:
        return False
    if order.status not in _LOCALLY_TERMINAL_STATUSES:
        return True
    return order.last_synced_at is None


def operation_working_orders(facts: Iterable[IntentFact]) -> list[dict[str, Any]]:
    """The operation's own remaining working orders with their blocking effect."""

    return [
        {
            "intent_id": str(fact.row.id),
            "paper_order_id": str(fact.order.id),
            "client_order_id": fact.row.client_order_id,
            "symbol": fact.ticker,
            "status": fact.order.status.value,
            "blocking_effect": BLOCKING_WORKING_ORDER,
        }
        for fact in facts
        if fact.order is not None and is_working_order(fact.order)
    ]


def operation_unresolved_intents(facts: Iterable[IntentFact]) -> list[dict[str, Any]]:
    """Intents whose outcome is not established (ambiguous), with their blocking effect."""

    return [
        {
            "intent_id": str(fact.row.id),
            "paper_order_id": str(fact.order.id) if fact.order is not None else None,
            "client_order_id": fact.row.client_order_id,
            "symbol": fact.ticker,
            "state": fact.state.value,
            "blocking_effect": BLOCKING_OUTCOME_UNRESOLVED,
        }
        for fact in facts
        if fact.state is SubmissionIntentState.AMBIGUOUS
    ]


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------


def _rowcount(result: Any) -> int:
    """Affected-row count of a DML result (``CursorResult.rowcount``)."""

    return int(result.rowcount)


def _db_now(session: Session) -> datetime:
    """The database wall clock (read at the moment of the call, not the transaction start)."""

    value: datetime = session.execute(select(func.clock_timestamp())).scalar_one()
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _load_locked(session: Session, operation_id: uuid.UUID) -> ExecutionOperation:
    operation = session.execute(
        select(ExecutionOperation).where(ExecutionOperation.id == operation_id).with_for_update()
    ).scalar_one_or_none()
    if operation is None:
        raise OperationNotFoundError(operation_id)
    # A locking read must observe the committed row, not a stale identity-map copy.
    session.refresh(operation)
    return operation


@dataclass(frozen=True)
class PlannedIntent:
    """One approved candidate pinned at operation creation, in plan order."""

    symbol_id: uuid.UUID
    side: str
    quantity: Decimal
    client_order_id: str
    decision_fingerprint: str
    risk_event_id: uuid.UUID | None = None
    reference_price: Decimal | None = None
    prior_execution_refs: tuple[str, ...] = ()


def create_operation(
    session: Session,
    *,
    strategy_pk: uuid.UUID,
    as_of_session: date,
    risk_run_id: uuid.UUID,
    intents: Sequence[PlannedIntent],
    job_id: uuid.UUID | None,
    basis_verification: Mapping[str, Any] | None = None,
) -> ExecutionOperation:
    """Pin the risk run and persist the planned intents in plan order; link the Job as ``start``.

    The new operation is ``running`` and its start Job holds execution authority at epoch 0.
    Raises ``OperationOpenError`` / ``RiskRunAlreadyOperatedError`` from the database
    constraints (race-safe: the unique indexes decide, not a prior read). The caller owns the
    transaction and the session-level locks (20.1-15 creates it in the paper_execution run's
    transaction, after the run-time gates).
    """

    if not intents:
        raise ValueError("An execution operation needs at least one planned intent.")
    operation = ExecutionOperation(
        strategy_id=strategy_pk,
        as_of_session=as_of_session,
        risk_run_id=risk_run_id,
        state=OperationState.RUNNING.value,
        reason=None,
        execution_epoch=0,
        executor_job_id=job_id,
        basis_verification=dict(basis_verification) if basis_verification is not None else None,
    )
    try:
        with session.begin_nested():
            session.add(operation)
            session.flush()
    except IntegrityError as exc:
        message = str(exc.orig)
        if "uq_execution_operations_one_open_per_strategy" in message:
            existing = session.execute(
                select(ExecutionOperation.id).where(
                    ExecutionOperation.strategy_id == strategy_pk,
                    ExecutionOperation.state.in_(_OPEN_STATE_VALUES),
                )
            ).scalar_one_or_none()
            raise OperationOpenError(strategy_pk, existing) from exc
        if "uq_execution_operations_risk_run" in message:
            raise RiskRunAlreadyOperatedError(strategy_pk, risk_run_id) from exc
        raise
    for sequence, planned in enumerate(intents, start=1):
        session.add(
            ExecutionOperationIntent(
                operation_id=operation.id,
                sequence=sequence,
                risk_event_id=planned.risk_event_id,
                symbol_id=planned.symbol_id,
                side=planned.side,
                quantity=planned.quantity,
                reference_price=planned.reference_price,
                client_order_id=planned.client_order_id,
                decision_fingerprint=planned.decision_fingerprint,
                prior_execution_refs=list(planned.prior_execution_refs),
                disposition=IntentDisposition.OPEN.value,
            )
        )
    if job_id is not None:
        session.add(
            ExecutionOperationJob(
                operation_id=operation.id, job_id=job_id, mode=OperationJobMode.START.value
            )
        )
    session.flush()
    return operation


@dataclass(frozen=True)
class Fence:
    """The authority an executor holds: the operation, its epoch and its Job (S1-R3)."""

    operation_id: uuid.UUID
    epoch: int
    job_id: uuid.UUID


def _fence_conditions(fence: Fence) -> list[Any]:
    return [
        ExecutionOperation.id == fence.operation_id,
        ExecutionOperation.execution_epoch == fence.epoch,
        ExecutionOperation.executor_job_id == fence.job_id,
    ]


def transition(
    session: Session,
    operation_id: uuid.UUID,
    to_state: OperationState,
    reason: str | None = None,
    *,
    reason_detail: str | None = None,
    fence: Fence | None = None,
    ended_by: str = "executor",
    now: datetime | None = None,
) -> ExecutionOperation:
    """Compare-and-set move to ``to_state`` (legal moves only; final states are final).

    ``paused -> running`` exists only through ``begin_continuation``. With ``fence`` the move
    applies only while the executor's epoch and Job still hold authority. Moving to a final
    state also increments the epoch and clears the executor, so no stale fenced write can
    touch a final operation. A move to ``terminated`` is delegated to ``terminate_operation``
    (row lock, fence check and the unsent-intent dispositions in ONE step: unsent intents
    become ``cancelled_unsent`` for ``cancelled_by_operator``, else ``expired_unsent``), so a
    terminated operation never keeps ``planned`` intents open. Raises
    ``IllegalOperationTransition`` / ``OperationConflictError`` (the CAS loser).
    """

    validate_state_reason(to_state, reason)
    at = now or clock.now_utc()
    to_state = OperationState(to_state)
    if to_state is OperationState.TERMINATED:
        assert reason is not None
        locked = _load_locked(session, operation_id)
        current_state = OperationState(locked.state)
        if to_state not in _LEGAL_TRANSITIONS[current_state]:
            raise IllegalOperationTransition(operation_id, current_state, to_state)
        terminate_operation(
            session,
            locked,
            TerminatedReason(reason),
            ended_by=ended_by,
            fence=fence,
            now=at,
        )
        return locked
    legal_from = [s for s, targets in _LEGAL_TRANSITIONS.items() if to_state in targets]
    values: dict[str, Any] = {
        "state": to_state.value,
        "reason": reason,
        "reason_detail": reason_detail,
        "state_changed_at": at,
    }
    if to_state in _FINAL_STATES:
        values["execution_epoch"] = ExecutionOperation.execution_epoch + 1
        values["executor_job_id"] = None
    conditions: list[Any] = [
        ExecutionOperation.id == operation_id,
        ExecutionOperation.state.in_([s.value for s in legal_from]),
    ]
    if fence is not None:
        conditions.extend(_fence_conditions(fence))
    result = session.execute(
        update(ExecutionOperation)
        .where(and_(*conditions))
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if _rowcount(result) != 1:
        current = session.execute(
            select(ExecutionOperation.state).where(ExecutionOperation.id == operation_id)
        ).scalar_one_or_none()
        if current is None:
            raise OperationNotFoundError(operation_id)
        if OperationState(current) not in legal_from:
            raise IllegalOperationTransition(operation_id, OperationState(current), to_state)
        raise OperationConflictError(operation_id, "stale executor fence")
    operation = session.get(ExecutionOperation, operation_id)
    assert operation is not None
    session.refresh(operation)
    return operation


def begin_continuation(
    session: Session,
    operation_id: uuid.UUID,
    job_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> ExecutionOperation:
    """CAS ``paused -> running`` and link the Job as ``continue``; the loser raises
    ``OperationConflictError``. Execution authority is taken separately by
    ``acquire_execution`` (under the advisory lock)."""

    at = now or clock.now_utc()
    result = session.execute(
        update(ExecutionOperation)
        .where(
            ExecutionOperation.id == operation_id,
            ExecutionOperation.state == OperationState.PAUSED.value,
        )
        .values(
            state=OperationState.RUNNING.value,
            reason=None,
            reason_detail=None,
            state_changed_at=at,
        )
        .execution_options(synchronize_session=False)
    )
    if _rowcount(result) != 1:
        exists_row = session.execute(
            select(ExecutionOperation.id).where(ExecutionOperation.id == operation_id)
        ).scalar_one_or_none()
        if exists_row is None:
            raise OperationNotFoundError(operation_id)
        raise OperationConflictError(operation_id, "operation is not paused")
    already = session.execute(
        select(ExecutionOperationJob.id).where(
            ExecutionOperationJob.operation_id == operation_id,
            ExecutionOperationJob.job_id == job_id,
        )
    ).scalar_one_or_none()
    if already is None:
        session.add(
            ExecutionOperationJob(
                operation_id=operation_id, job_id=job_id, mode=OperationJobMode.CONTINUE.value
            )
        )
    session.flush()
    operation = session.get(ExecutionOperation, operation_id)
    assert operation is not None
    session.refresh(operation)
    return operation


# ---------------------------------------------------------------------------
# Termination: lazy expiry (D-21) and End (D-20)
# ---------------------------------------------------------------------------


def _unsent_facts(facts: Iterable[IntentFact]) -> list[IntentFact]:
    """Intents that are open AND proven not sent (never an intent that may have been sent)."""

    return [
        fact
        for fact in facts
        if fact.row.disposition == IntentDisposition.OPEN.value
        and fact.state in _UNSENT_STATES
        and fact.proven_not_sent
    ]


def terminate_operation(
    session: Session,
    operation: ExecutionOperation,
    reason: TerminatedReason,
    *,
    unsent_disposition: IntentDisposition | None = None,
    facts: Sequence[IntentFact] | None = None,
    ended_by: str,
    end_reason: str | None = None,
    fence: Fence | None = None,
    now: datetime | None = None,
) -> list[uuid.UUID]:
    """Terminate an OPEN operation (row lock held by the caller) and return the intent ids
    moved to ``unsent_disposition`` (default: ``cancelled_unsent`` for ``cancelled_by_operator``,
    ``expired_unsent`` for window expiry and supersession).

    With ``fence`` (an executor terminating itself, e.g. the per-intent window check of the
    send loop) the whole step applies only while the executor's epoch and Job still hold
    authority and the operation is ``running``; a stale executor raises
    ``OperationConflictError`` and NOTHING changes (state, epoch and intent dispositions are
    written in the same locked step).

    Only intents proven not sent change (planned, registered_unsent, not_sent; frozen intents
    of a ``requires_reevaluation`` operation included); submitted and ambiguous intents,
    PaperOrders, recovery records and attempt logs are never touched, no broker is contacted
    and trading permission is unchanged. The epoch is incremented and the executor cleared so
    no stale fenced write can touch the final operation.
    """

    at = now or clock.now_utc()
    if OperationState(operation.state) in _FINAL_STATES:
        raise IllegalOperationTransition(
            operation.id, OperationState(operation.state), OperationState.TERMINATED
        )
    if fence is not None and (
        operation.id != fence.operation_id
        or operation.execution_epoch != fence.epoch
        or operation.executor_job_id != fence.job_id
        or OperationState(operation.state) is not OperationState.RUNNING
    ):
        raise OperationConflictError(operation.id, "stale executor fence")
    if unsent_disposition is None:
        unsent_disposition = (
            IntentDisposition.CANCELLED_UNSENT
            if reason is TerminatedReason.CANCELLED_BY_OPERATOR
            else IntentDisposition.EXPIRED_UNSENT
        )
    if facts is None:
        facts = load_intent_facts(session, [operation.id])[operation.id]
    moved: list[uuid.UUID] = []
    for fact in _unsent_facts(facts):
        fact.row.disposition = unsent_disposition.value
        fact.row.disposition_at = at
        moved.append(fact.row.id)
    operation.state = OperationState.TERMINATED.value
    operation.reason = reason.value
    operation.reason_detail = None
    operation.state_changed_at = at
    operation.execution_epoch = operation.execution_epoch + 1
    operation.executor_job_id = None
    operation.ended_by = ended_by
    operation.end_reason = end_reason
    session.flush()
    return moved


@dataclass(frozen=True)
class TouchResult:
    changed: bool
    state: OperationState
    reason: str | None
    expired_intent_ids: tuple[uuid.UUID, ...] = ()


def touch_operation(
    session: Session,
    operation_id: uuid.UUID,
    *,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> TouchResult:
    """Persist lazy window expiry (D-21): the ONLY place expiry is written.

    Called by Continue submit/run, End, a new session request and run-time operation
    creation, never by a read route. An OPEN operation whose window elapsed terminates with
    ``execution_window_elapsed`` (``evaluation_superseded`` when the trading day moved on)
    and its unsent intents become ``expired_unsent``; submitted and ambiguous intents are
    untouched. A ``running`` operation with a live Job is left to its executor, and touching
    never normalizes a crash-left ``running`` operation (that happens only inside
    ``acquire_execution`` under the advisory lock).
    """

    resolved = settings or load_settings()
    at = now or clock.now_utc()
    operation = _load_locked(session, operation_id)
    state = OperationState(operation.state)
    if state in _FINAL_STATES:
        return TouchResult(False, state, operation.reason)
    window = load_calendar_window(session, now=at, settings=resolved)
    verdict = window_verdict(window, resolved, operation.as_of_session)
    reason = _VERDICT_TERMINATED_REASON.get(verdict)
    if reason is None:
        return TouchResult(False, state, operation.reason)
    if state is OperationState.RUNNING and live_job_ids(session, [operation_id])[operation_id]:
        return TouchResult(False, state, operation.reason)
    moved = terminate_operation(
        session,
        operation,
        reason,
        unsent_disposition=IntentDisposition.EXPIRED_UNSENT,
        ended_by="system_expiry",
        now=at,
    )
    return TouchResult(True, OperationState.TERMINATED, reason.value, tuple(moved))


@dataclass(frozen=True)
class EndResult:
    operation_id: uuid.UUID
    state: OperationState
    reason: str | None
    changed: bool
    ended_by_expiry: bool
    unsent_cancelled: tuple[uuid.UUID, ...]
    working_orders: tuple[dict[str, Any], ...]
    unresolved_intents: tuple[dict[str, Any], ...]


def end_operation(
    session: Session,
    operation_id: uuid.UUID,
    *,
    operator_reason: str,
    actor: str,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> EndResult:
    """End an operation (D-20, J-2): terminate it and its UNSENT intents only.

    Holds the operation row lock. Refused with ``OperationRunningError`` while a queued or
    running Job of the operation exists (linked Jobs plus paper-session Jobs whose payload
    ``operation_id`` matches) and with ``OperationNotOpenError`` for a completed operation. A
    second End of a terminated operation returns ``changed=False``. Lazy expiry runs first: a
    window that already elapsed terminates the operation with ``execution_window_elapsed`` /
    ``evaluation_superseded`` (reported with ``ended_by_expiry=True``; its unsent intents are
    ``expired_unsent``) instead of ``cancelled_by_operator``. Submitted orders are never
    touched at the broker, ambiguous intents stay ambiguous and blocking, recovery
    records and attempt logs are untouched, trading permission is unchanged. The result lists
    the remaining working orders and unresolved intents with their blocking effect (J-2).
    """

    at = now or clock.now_utc()
    operation = _load_locked(session, operation_id)
    state = OperationState(operation.state)
    if state is OperationState.COMPLETED:
        raise OperationNotOpenError(operation_id, state.value)
    changed = False
    ended_by_expiry = False
    cancelled: list[uuid.UUID] = []
    if state is not OperationState.TERMINATED:
        running = live_job_ids(session, [operation_id])[operation_id]
        if running:
            raise OperationRunningError(operation_id, running)
        touched = touch_operation(session, operation_id, now=at, settings=settings)
        if touched.changed:
            changed = True
            ended_by_expiry = True
        else:
            facts_before = load_intent_facts(session, [operation_id])[operation_id]
            operation = _load_locked(session, operation_id)
            cancelled = terminate_operation(
                session,
                operation,
                TerminatedReason.CANCELLED_BY_OPERATOR,
                unsent_disposition=IntentDisposition.CANCELLED_UNSENT,
                facts=facts_before,
                ended_by=actor,
                end_reason=operator_reason,
                now=at,
            )
            changed = True
    operation = _load_locked(session, operation_id)
    facts = load_intent_facts(session, [operation_id])[operation_id]
    return EndResult(
        operation_id=operation_id,
        state=OperationState(operation.state),
        reason=operation.reason,
        changed=changed,
        ended_by_expiry=ended_by_expiry,
        unsent_cancelled=tuple(cancelled),
        working_orders=tuple(operation_working_orders(facts)),
        unresolved_intents=tuple(operation_unresolved_intents(facts)),
    )


# ---------------------------------------------------------------------------
# S1-R3: fenced compare-and-set helpers (every executor write; 0 rows when stale)
# ---------------------------------------------------------------------------


def cas_update_operation(
    session: Session,
    fence: Fence,
    values: Mapping[str, Any],
    *,
    from_states: Sequence[OperationState] = (OperationState.RUNNING,),
) -> int:
    """Fenced UPDATE of the operation row; returns the affected row count (0 when stale)."""

    result = session.execute(
        update(ExecutionOperation)
        .where(
            *_fence_conditions(fence),
            ExecutionOperation.state.in_([s.value for s in from_states]),
        )
        .values(**dict(values))
        .execution_options(synchronize_session=False)
    )
    return _rowcount(result)


def cas_set_intent_disposition(
    session: Session,
    fence: Fence,
    intent_id: uuid.UUID,
    disposition: IntentDisposition,
    *,
    now: datetime | None = None,
) -> int:
    """Fenced disposition write of one OPEN intent (0 rows when stale or already final)."""

    result = session.execute(
        update(ExecutionOperationIntent)
        .where(
            ExecutionOperationIntent.id == intent_id,
            ExecutionOperationIntent.operation_id == fence.operation_id,
            ExecutionOperationIntent.disposition == IntentDisposition.OPEN.value,
            exists().where(
                *_fence_conditions(fence),
                ExecutionOperation.state == OperationState.RUNNING.value,
            ),
        )
        .values(disposition=disposition.value, disposition_at=now or clock.now_utc())
        .execution_options(synchronize_session=False)
    )
    return _rowcount(result)


def cas_update_order(
    session: Session, fence: Fence, paper_order_id: uuid.UUID, values: Mapping[str, Any]
) -> int:
    """Fenced UPDATE of an operation intent's PaperOrder (0 rows once the executor is stale)."""

    authority = (
        select(ExecutionOperationIntent.id)
        .join(ExecutionOperation, ExecutionOperation.id == ExecutionOperationIntent.operation_id)
        .where(
            ExecutionOperationIntent.paper_order_id == paper_order_id,
            *_fence_conditions(fence),
            ExecutionOperation.state == OperationState.RUNNING.value,
        )
    )
    result = session.execute(
        update(PaperOrder)
        .where(PaperOrder.id == paper_order_id, authority.exists())
        .values(**dict(values))
        .execution_options(synchronize_session=False)
    )
    return _rowcount(result)


# ---------------------------------------------------------------------------
# S1-R3: takeover (acquire_execution), send authorization (T1), late completion
# ---------------------------------------------------------------------------


def _lock_classid_objid(key: int) -> tuple[int, int]:
    unsigned = key & 0xFFFFFFFFFFFFFFFF
    return (unsigned >> 32) & 0xFFFFFFFF, unsigned & 0xFFFFFFFF


def verify_run_lock_held(session: Session, lock: RunLock) -> bool:
    """True when ``pg_locks`` shows the advisory lock of ``lock`` granted to its backend."""

    classid, objid = _lock_classid_objid(lock.key)
    found = session.execute(
        text(
            "SELECT 1 FROM pg_locks WHERE locktype = 'advisory' AND granted "
            "AND pid = :pid AND classid::bigint = :classid AND objid::bigint = :objid "
            "AND objsubid = 1"
        ),
        {"pid": lock.backend_pid, "classid": classid, "objid": objid},
    ).first()
    return found is not None


@dataclass(frozen=True)
class ExecutionAcquisition:
    """Result of a takeover: the new epoch and whether the operation was paused."""

    operation_id: uuid.UUID
    epoch: int
    paused: bool
    in_doubt_intent_ids: tuple[uuid.UUID, ...]


def _in_doubt(fact: IntentFact) -> bool:
    """S1-R3 takeover rule: an order with no broker evidence whose submission class is not
    None / not_sent and not an established rejection is IN DOUBT, whether the old POST was
    never sent, is still in flight or was accepted."""

    order = fact.order
    if order is None or fact.row.disposition != IntentDisposition.OPEN.value:
        return False
    if order.broker_order_id is not None:
        return False
    if order.status not in (
        OrderLifecycleState.PENDING_SUBMISSION,
        OrderLifecycleState.SUBMISSION_FAILED,
        OrderLifecycleState.UNKNOWN,
    ):
        return False
    submission_class = classify_submission(fact.attempts)
    if submission_class in (
        SubmissionClass.AMBIGUOUS,
        SubmissionClass.EXISTS_REPORTED,
        SubmissionClass.ACCEPTED,
    ):
        return True
    # An UNKNOWN order with no attempt row at all (a legacy order) is unestablished too; an
    # UNKNOWN order whose attempts all prove not-sent is left to the recovery liveness path.
    return order.status == OrderLifecycleState.UNKNOWN and submission_class is None


def acquire_execution(
    operation_id: uuid.UUID,
    job_id: uuid.UUID,
    lock: RunLock,
    *,
    settings: Settings | None = None,
) -> ExecutionAcquisition:
    """S1 takeover: take execution authority of a ``running`` operation for ``job_id``.

    Requires the session advisory lock (``RunLock`` from ``session_run_lock``, verified in
    ``pg_locks``). Transaction A locks the operation row, increments ``execution_epoch`` and
    sets ``executor_job_id``, and COMMITS. Only AFTER that commit does transaction B read the
    operation's attempt rows (so a late completion by the previous executor lands on one side
    of the epoch change). Every in-doubt intent (submission class not None / not_sent: an
    attempt with an empty outcome, a recorded ambiguous outcome such as a timeout, or
    exists_reported; no broker evidence) has its order set UNKNOWN through the legal
    transition boundary and the operation becomes ``paused`` / ``outcome_unresolved`` with a
    fenced CAS. In-doubt is a property of the durable attempt row, not of any executor. A
    crash-left ``running`` operation is normalized only here.
    """

    resolved = settings or load_settings()
    with session_scope(resolved) as session:
        if not verify_run_lock_held(session, lock):
            raise RunLockNotHeldError(
                f"Advisory lock for '{lock.strategy_id}' {lock.session_date} is not held."
            )
        operation = _load_locked(session, operation_id)
        if OperationState(operation.state) is not OperationState.RUNNING:
            raise OperationConflictError(
                operation_id, f"operation is {operation.state}, not running"
            )
        public_id = session.execute(
            select(Strategy.strategy_id).where(Strategy.id == operation.strategy_id)
        ).scalar_one()
        if public_id != lock.strategy_id or operation.as_of_session != lock.session_date:
            raise RunLockNotHeldError(
                "The held advisory lock does not belong to this operation's strategy and session."
            )
        operation.execution_epoch = operation.execution_epoch + 1
        operation.executor_job_id = job_id
        epoch = operation.execution_epoch
    # Transaction A committed. Attempts are read only now.
    fence = Fence(operation_id=operation_id, epoch=epoch, job_id=job_id)
    with session_scope(resolved) as session:
        facts = load_intent_facts(session, [operation_id])[operation_id]
        in_doubt = [fact for fact in facts if _in_doubt(fact)]
        paused = False
        if in_doubt:
            for fact in in_doubt:
                order = fact.order
                assert order is not None
                if order.status != OrderLifecycleState.UNKNOWN:
                    _mark_unknown(session, order, fact)
            paused = (
                cas_update_operation(
                    session,
                    fence,
                    {
                        "state": OperationState.PAUSED.value,
                        "reason": PausedReason.OUTCOME_UNRESOLVED.value,
                        "reason_detail": None,
                        "state_changed_at": clock.now_utc(),
                    },
                )
                == 1
            )
    return ExecutionAcquisition(
        operation_id=operation_id,
        epoch=epoch,
        paused=paused,
        in_doubt_intent_ids=tuple(fact.row.id for fact in in_doubt),
    )


def _mark_unknown(session: Session, order: PaperOrder, fact: IntentFact) -> None:
    details = {
        "reason": "takeover_in_doubt",
        "attempt_outcomes": [
            a.outcome_class.value if a.outcome_class is not None else "incomplete"
            for a in fact.attempts
        ],
    }
    try:
        apply_order_transition(
            order.id,
            OrderTransitionRequest(
                strategy_run_id=order.strategy_run_id,
                event_type=OrderTransitionEventType.BROKER_STATUS_UNKNOWN,
                details=details,
            ),
            session=session,
        )
    except IllegalOrderTransition:  # pragma: no cover - guarded by _in_doubt's status filter
        logger.warning("in-doubt order %s could not be parked UNKNOWN", order.id)
        return
    session.add(
        ExecutionEvent(
            strategy_run_id=order.strategy_run_id,
            paper_order_id=order.id,
            event_type="operation_takeover_in_doubt",
            severity="error",
            blocks_execution=True,
            event_at=clock.now_utc(),
            message=(
                "Execution takeover found an intent whose submission outcome is in doubt; "
                "it was parked UNKNOWN and will not be re-sent."
            ),
            details=details,
        )
    )
    session.flush()


@dataclass(frozen=True)
class SendAuthorization:
    """T1 result: the committed begin-attempt row and the deadlines of its POST."""

    attempt_id: uuid.UUID
    attempt_number: int
    paper_order_id: uuid.UUID
    execution_epoch: int
    authorization_deadline: datetime
    #: Executor-local WALL-clock deadline (local wall time at T1 commit + the TTL). The send
    #: path re-reads the wall clock (never a monotonic clock) immediately before handing the
    #: request to the HTTP client and sends nothing when this has passed.
    local_deadline: datetime


def authorize_send(
    operation_id: uuid.UUID,
    intent_id: uuid.UUID,
    epoch: int,
    job_id: uuid.UUID,
    *,
    lease_owner: str,
    ttl_seconds: int = DEFAULT_SEND_AUTHORIZATION_TTL_SECONDS,
    settings: Settings | None = None,
) -> SendAuthorization:
    """Transaction T1 (S1-R3): authority and the attempt record, durably, BEFORE the POST.

    One committed transaction: lock the operation row FOR UPDATE; require state ``running``,
    ``execution_epoch == epoch`` and ``executor_job_id == job_id``; require the Job's lease
    (``lease_owner`` holds it and it expires after the DATABASE clock now); require the
    intent open, registered and sendable by BROKER CERTAINTY (its whole attempt history is
    proven not sent, ``attempts.proven_not_sent``; any empty, ambiguous, exists_reported,
    accepted or rejected attempt refuses, with no exception) and the 20.1-10 recovery
    predicate not reporting an unresolved outcome for the strategy (G2); then insert the
    begin-attempt row (outcome NULL; epoch, Job id and ``authorization_deadline`` = database
    clock at insert + ``ttl_seconds``), set ``last_guarded_at`` and COMMIT. Every HTTP
    attempt, including in-loop retries after ``pre_connection``, needs its own call. Raises
    ``SendRefusedError`` and sends nothing when any check fails; the caller POSTs only after
    this returns.
    """

    resolved = settings or load_settings()
    with session_scope(resolved) as session:
        operation = session.execute(
            select(ExecutionOperation)
            .where(ExecutionOperation.id == operation_id)
            .with_for_update()
        ).scalar_one_or_none()
        if operation is None:
            raise SendRefusedError(SendRefusal.OPERATION_NOT_FOUND)
        if OperationState(operation.state) is not OperationState.RUNNING:
            raise SendRefusedError(SendRefusal.OPERATION_NOT_RUNNING, operation.state)
        if operation.execution_epoch != epoch:
            raise SendRefusedError(SendRefusal.STALE_EPOCH)
        if operation.executor_job_id != job_id:
            raise SendRefusedError(SendRefusal.WRONG_EXECUTOR)
        now_db = _db_now(session)
        lease_ok = session.execute(
            select(Job.id).where(
                Job.id == job_id,
                Job.status == JobStatus.RUNNING,
                Job.lease_owner == lease_owner,
                Job.lease_expires_at > now_db,
            )
        ).scalar_one_or_none()
        if lease_ok is None:
            raise SendRefusedError(SendRefusal.LEASE_LOST)
        intent = session.execute(
            select(ExecutionOperationIntent).where(
                ExecutionOperationIntent.id == intent_id,
                ExecutionOperationIntent.operation_id == operation_id,
            )
        ).scalar_one_or_none()
        if intent is None:
            raise SendRefusedError(SendRefusal.INTENT_NOT_FOUND)
        if intent.disposition != IntentDisposition.OPEN.value:
            raise SendRefusedError(SendRefusal.INTENT_NOT_OPEN, intent.disposition)
        if intent.paper_order_id is None:
            raise SendRefusedError(SendRefusal.INTENT_NOT_REGISTERED)
        order = session.execute(
            select(PaperOrder).where(PaperOrder.id == intent.paper_order_id).with_for_update()
        ).scalar_one_or_none()
        if order is None:
            raise SendRefusedError(SendRefusal.INTENT_NOT_REGISTERED)
        attempt_rows = (
            session.execute(
                select(OrderSubmissionAttempt)
                .where(OrderSubmissionAttempt.paper_order_id == order.id)
                .order_by(OrderSubmissionAttempt.attempt_number)
            )
            .scalars()
            .all()
        )
        attempts = [_attempt_record(row) for row in attempt_rows]
        registered = _attempt_log_registered(intent, order, attempts)
        retryable = order.status in (
            OrderLifecycleState.PENDING_SUBMISSION,
            OrderLifecycleState.SUBMISSION_FAILED,
        )
        if not (retryable and proven_not_sent(order, attempts, attempt_log_registered=registered)):
            submission_class = classify_submission(attempts)
            raise SendRefusedError(
                SendRefusal.INTENT_NOT_SENDABLE,
                submission_class.value if submission_class is not None else order.status.value,
            )
        public_id = session.execute(
            select(Strategy.strategy_id).where(Strategy.id == operation.strategy_id)
        ).scalar_one()
        status = strategy_recovery_status(session, public_id, now=clock.now_utc())
        if status.gate_code is GateCode.OUTCOME_UNRESOLVED:
            raise SendRefusedError(SendRefusal.OUTCOME_UNRESOLVED)
        number = (attempt_rows[-1].attempt_number if attempt_rows else 0) + 1
        deadline = now_db + timedelta(seconds=ttl_seconds)
        attempt = OrderSubmissionAttempt(
            paper_order_id=order.id,
            strategy_run_id=order.strategy_run_id,
            attempt_number=number,
            started_at=now_db,
            execution_epoch=epoch,
            executor_job_id=job_id,
            authorization_deadline=deadline,
        )
        session.add(attempt)
        operation.last_guarded_at = now_db
        session.flush()
        attempt_id = attempt.id
        order_id = order.id
    # T1 committed (session_scope exit). The wall-clock deadline starts now.
    return SendAuthorization(
        attempt_id=attempt_id,
        attempt_number=number,
        paper_order_id=order_id,
        execution_epoch=epoch,
        authorization_deadline=deadline,
        local_deadline=datetime.now(UTC) + timedelta(seconds=ttl_seconds),
    )


@dataclass(frozen=True)
class LateCompletion:
    attempt_id: uuid.UUID
    outcome_class: AttemptOutcomeClass
    stale: bool


def complete_attempt_late(
    attempt_id: uuid.UUID,
    outcome_class: AttemptOutcomeClass,
    *,
    executor_job_id: uuid.UUID,
    execution_epoch: int,
    http_status: int | None = None,
    error_type: str | None = None,
    broker_message: str | None = None,
    settings: Settings | None = None,
) -> LateCompletion:
    """Complete-once evidence write of an attempt row plus a ``late_attempt_outcome`` event.

    Allowed for a STALE executor (the attempt row is evidence, not authority) but only for
    the executor that created the row: the caller's ``executor_job_id`` and
    ``execution_epoch`` must equal the row's, otherwise ``AttemptOwnershipError`` and the row
    stays NULL. Takeover and recovery never complete a NULL attempt, so ``deadline_expired``
    (the pre-handoff wall-clock check refused the request; 20.1-16 v3) is accepted only from
    the send path of the creating executor. Completing writes the outcome the executor's own
    send actually produced and never touches the PaperOrder (order state is established only
    by broker sync and recovery). A second completion raises ``AttemptAlreadyCompletedError``.
    The event attaches to the order's run (the attempt's own run may have been nulled out).
    """

    resolved = settings or load_settings()
    outcome = AttemptOutcomeClass(outcome_class)
    with session_scope(resolved) as session:
        attempt = session.execute(
            select(OrderSubmissionAttempt)
            .where(OrderSubmissionAttempt.id == attempt_id)
            .with_for_update()
        ).scalar_one_or_none()
        if attempt is None:
            raise LookupError(f"Submission attempt '{attempt_id}' was not found.")
        if attempt.completed_at is not None:
            raise AttemptAlreadyCompletedError(
                f"Submission attempt '{attempt_id}' already has an outcome."
            )
        if (
            attempt.executor_job_id is None
            or attempt.executor_job_id != executor_job_id
            or attempt.execution_epoch != execution_epoch
        ):
            raise AttemptOwnershipError(
                f"Attempt '{attempt_id}' was not created by executor job '{executor_job_id}' "
                f"at epoch {execution_epoch}; outcome '{outcome.value}' is refused."
            )
        order = session.get(PaperOrder, attempt.paper_order_id)
        assert order is not None
        authority = session.execute(
            select(ExecutionOperation.id)
            .join(
                ExecutionOperationIntent,
                ExecutionOperationIntent.operation_id == ExecutionOperation.id,
            )
            .where(
                ExecutionOperationIntent.paper_order_id == order.id,
                ExecutionOperation.state == OperationState.RUNNING.value,
                ExecutionOperation.execution_epoch == execution_epoch,
                ExecutionOperation.executor_job_id == executor_job_id,
            )
        ).first()
        stale = authority is None
        completed_at = _db_now(session)
        attempt.outcome_class = outcome.value
        attempt.http_status = http_status
        attempt.error_type = error_type[:64] if error_type else None
        attempt.broker_message = broker_message[:500] if broker_message else None
        attempt.completed_at = completed_at
        session.add(
            ExecutionEvent(
                strategy_run_id=order.strategy_run_id,
                paper_order_id=order.id,
                event_type="late_attempt_outcome",
                severity="warning" if stale else "info",
                blocks_execution=False,
                event_at=completed_at,
                message=(
                    f"Attempt {attempt.attempt_number} of intent '{order.client_order_id}' "
                    f"completed with outcome '{outcome.value}'"
                    + (" after the executor lost authority." if stale else ".")
                ),
                details={
                    "attempt_id": str(attempt_id),
                    "attempt_number": attempt.attempt_number,
                    "outcome_class": outcome.value,
                    "http_status": http_status,
                    "execution_epoch": execution_epoch,
                    "executor_job_id": str(executor_job_id),
                    "stale_executor": stale,
                },
            )
        )
        session.flush()
    return LateCompletion(attempt_id=attempt_id, outcome_class=outcome, stale=stale)


__all__ = [
    "BLOCKING_OUTCOME_UNRESOLVED",
    "BLOCKING_WORKING_ORDER",
    "DEFAULT_SEND_AUTHORIZATION_TTL_SECONDS",
    "RISK_LIMIT_FAILED_PREFIX",
    "RISK_LIMIT_PORTFOLIO_CODES",
    "AttemptOwnershipError",
    "EffectiveState",
    "EndResult",
    "ExecutionAcquisition",
    "Fence",
    "IllegalOperationTransition",
    "IntentDisposition",
    "IntentFact",
    "LateCompletion",
    "NextAction",
    "OperationConflictError",
    "OperationError",
    "OperationJobMode",
    "OperationNotFoundError",
    "OperationNotOpenError",
    "OperationOpenError",
    "OperationRunningError",
    "OperationState",
    "PausedReason",
    "PlannedIntent",
    "ReevaluationReason",
    "RiskRunAlreadyOperatedError",
    "RunLockNotHeldError",
    "SendAuthorization",
    "SendRefusal",
    "SendRefusedError",
    "TerminatedReason",
    "TouchResult",
    "WindowVerdict",
    "acquire_execution",
    "authorize_send",
    "begin_continuation",
    "cas_set_intent_disposition",
    "cas_update_operation",
    "cas_update_order",
    "complete_attempt_late",
    "compute_effective_state",
    "create_operation",
    "effective_state",
    "end_operation",
    "intent_state",
    "is_reevaluation_reason",
    "is_working_order",
    "live_job_ids",
    "load_intent_facts",
    "next_action",
    "operation_unresolved_intents",
    "operation_working_orders",
    "risk_limit_failed",
    "terminate_operation",
    "touch_operation",
    "transition",
    "validate_state_reason",
    "verify_run_lock_held",
    "window_verdict",
]
