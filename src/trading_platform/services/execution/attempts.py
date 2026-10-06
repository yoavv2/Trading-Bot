"""Order-submission attempt log: taxonomy, classification, persistence (COR-06, D-12).

Every HTTP attempt of an order POST is logged durably BEFORE and AFTER the
attempt, in its own committed transaction (``DbSubmissionAttemptLog``). A crash
between the two leaves a row with a NULL outcome, which reads back as
``ambiguous`` and is never re-sent.

Taxonomy (closed):

* ``AttemptOutcomeClass`` (per attempt, stored; see ``db.models``):
  pre_connection, deadline_expired, ambiguous, duplicate_reported, rejected,
  accepted. NULL only while the attempt is incomplete.
* ``SubmissionClass`` (derived over ALL attempts of an intent): not_sent,
  ambiguous, exists_reported, rejected, accepted. Precedence:
  ambiguous (incl. any NULL-outcome row) > exists_reported > accepted >
  rejected > not_sent. ``not_sent`` only if EVERY attempt of the intent's whole
  history has a complete outcome of pre_connection or deadline_expired (positive
  evidence, recorded by the sending executor, that the request never left the
  process). A later pre_connection or deadline_expired attempt never downgrades
  an earlier or later ambiguous or incomplete attempt.
* ``SubmissionIntentState`` (derived from the local order + attempt log, plus the two
  stored unsent terminal dispositions of an execution operation, 20.1-11):
  planned, registered_unsent, not_sent, submitted, ambiguous, rejected,
  expired_unsent, cancelled_unsent. (Round 5 removed ``broker_confirmed_not_received``:
  a broker statement never changes an intent state.)

Fail-closed begin (S1-R3, 20.1-15): ``DbSubmissionAttemptLog.begin_attempt`` works ONLY inside
transaction T1 (``operations.authorize_send``, which opens ``send_authorization_scope``), so no
attempt row, and therefore no POST, can exist without a committed authorization. Every other
caller gets ``AttemptNotAuthorizedError``.

This module deliberately does not import the Alpaca client module (the client
imports this one) and nothing under ``jobs/``. The attempt table is append-only here: there is
no delete path and no update of ``started_at``; an outcome is written once.
Only broker response text and exception CLASS NAMES are stored, never headers or
credentials.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trading_platform.core.settings import Settings
from trading_platform.db.models import (
    AttemptOutcomeClass,
    ExecutionOperationIntent,
    OrderLifecycleState,
    OrderSubmissionAttempt,
    PaperOrder,
)
from trading_platform.db.session import session_scope

BROKER_MESSAGE_MAX_CHARS = 500
ERROR_TYPE_MAX_CHARS = 64
DUPLICATE_CLIENT_ORDER_ID_MESSAGE = "client_order_id must be unique"

NOT_SENT_OUTCOMES: frozenset[AttemptOutcomeClass] = frozenset(
    {AttemptOutcomeClass.PRE_CONNECTION, AttemptOutcomeClass.DEADLINE_EXPIRED}
)
"""Outcomes that are positive evidence the request never left the process."""


class SubmissionClass(StrEnum):
    """Submission class computed over all attempts of one intent (D-12)."""

    NOT_SENT = "not_sent"
    AMBIGUOUS = "ambiguous"
    EXISTS_REPORTED = "exists_reported"
    REJECTED = "rejected"
    ACCEPTED = "accepted"


class SubmissionIntentState(StrEnum):
    """Closed intent state (eight values).

    ``derive_intent_state`` yields the first six from the local order and attempt log;
    ``expired_unsent`` / ``cancelled_unsent`` are the two stored dispositions an execution
    operation (20.1-11) writes for intents it proved were never sent.
    """

    PLANNED = "planned"
    REGISTERED_UNSENT = "registered_unsent"
    NOT_SENT = "not_sent"
    SUBMITTED = "submitted"
    AMBIGUOUS = "ambiguous"
    REJECTED = "rejected"
    EXPIRED_UNSENT = "expired_unsent"
    CANCELLED_UNSENT = "cancelled_unsent"


class AttemptNotAuthorizedError(RuntimeError):
    """``begin_attempt`` was called outside transaction T1 (the send authorization)."""


_T1_ACTIVE: ContextVar[bool] = ContextVar("submission_attempt_t1_active", default=False)


@contextmanager
def send_authorization_scope() -> Iterator[None]:
    """Mark the enclosed block as transaction T1 (S1-R3); only ``authorize_send`` opens it."""

    token = _T1_ACTIVE.set(True)
    try:
        yield
    finally:
        _T1_ACTIVE.reset(token)


class AttemptAlreadyCompletedError(RuntimeError):
    """Raised when an attempt outcome would be written a second time."""


@dataclass(frozen=True)
class AttemptRecord:
    """Immutable projection of one ``order_submission_attempts`` row."""

    attempt_number: int
    started_at: datetime | None = None
    completed_at: datetime | None = None
    outcome_class: AttemptOutcomeClass | None = None
    http_status: int | None = None
    error_type: str | None = None
    broker_message: str | None = None


# ---------------------------------------------------------------------------
# Classification (pure)
# ---------------------------------------------------------------------------


def classify_attempt_exception(exc: BaseException) -> AttemptOutcomeClass:
    """Class of an attempt that ended in an exception instead of a response.

    ConnectError / ConnectTimeout / PoolTimeout mean the request never left the
    process (pre_connection). Every other exception is ambiguous: httpx gives no
    byte-level guarantee that the request was not sent.
    """

    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)):
        return AttemptOutcomeClass.PRE_CONNECTION
    return AttemptOutcomeClass.AMBIGUOUS


def classify_http_response(status_code: int, message: str | None) -> AttemptOutcomeClass:
    """Class of an attempt that received an HTTP response."""

    if 200 <= status_code < 300:
        return AttemptOutcomeClass.ACCEPTED
    if status_code == 422 and DUPLICATE_CLIENT_ORDER_ID_MESSAGE in (message or ""):
        return AttemptOutcomeClass.DUPLICATE_REPORTED
    if status_code == 429 or status_code >= 500:
        return AttemptOutcomeClass.AMBIGUOUS
    if 400 <= status_code < 500:
        return AttemptOutcomeClass.REJECTED
    # 1xx/3xx on an order POST is not a decision we can interpret.
    return AttemptOutcomeClass.AMBIGUOUS


def classify_submission(attempts: Sequence[AttemptRecord]) -> SubmissionClass | None:
    """Submission class over ALL attempts; ``None`` when no attempt was logged."""

    if not attempts:
        return None
    outcomes = [attempt.outcome_class for attempt in attempts]
    if any(o is None or o == AttemptOutcomeClass.AMBIGUOUS for o in outcomes):
        return SubmissionClass.AMBIGUOUS
    if any(o == AttemptOutcomeClass.DUPLICATE_REPORTED for o in outcomes):
        return SubmissionClass.EXISTS_REPORTED
    if any(o == AttemptOutcomeClass.ACCEPTED for o in outcomes):
        return SubmissionClass.ACCEPTED
    if any(o == AttemptOutcomeClass.REJECTED for o in outcomes):
        return SubmissionClass.REJECTED
    # Every attempt is complete and pre_connection or deadline_expired.
    return SubmissionClass.NOT_SENT


def summarize_attempts(attempts: Sequence[AttemptRecord]) -> list[tuple[int, str]]:
    """Attempt numbers with closed outcome-class names ("incomplete" for NULL) only."""

    return [
        (
            attempt.attempt_number,
            attempt.outcome_class.value if attempt.outcome_class is not None else "incomplete",
        )
        for attempt in attempts
    ]


_SUBMITTED_STATUSES = frozenset(
    {
        OrderLifecycleState.SUBMITTED,
        OrderLifecycleState.PARTIALLY_FILLED,
        OrderLifecycleState.FILLED,
        OrderLifecycleState.CANCELED,
        OrderLifecycleState.EXPIRED,
    }
)


def derive_intent_state(
    order: PaperOrder | None, attempts: Sequence[AttemptRecord]
) -> SubmissionIntentState:
    """Closed intent state from the local order status and the attempt log.

    Broker evidence on the order (REJECTED, a broker id, a broker-driven status)
    resolves the state first. Without it the ambiguous rule is evaluated before
    the not_sent rule, so an intent whose history holds any NULL or ambiguous
    attempt is ``ambiguous`` whatever later pre_connection or deadline_expired
    attempts follow.
    """

    if order is None:
        return SubmissionIntentState.PLANNED
    if order.status == OrderLifecycleState.REJECTED:
        return SubmissionIntentState.REJECTED
    if order.broker_order_id or order.status in _SUBMITTED_STATUSES:
        return SubmissionIntentState.SUBMITTED
    submission_class = classify_submission(attempts)
    if order.status == OrderLifecycleState.UNKNOWN or submission_class in (
        SubmissionClass.AMBIGUOUS,
        SubmissionClass.EXISTS_REPORTED,
    ):
        return SubmissionIntentState.AMBIGUOUS
    if submission_class == SubmissionClass.REJECTED:
        return SubmissionIntentState.REJECTED
    if submission_class == SubmissionClass.ACCEPTED:
        return SubmissionIntentState.SUBMITTED
    if submission_class == SubmissionClass.NOT_SENT:
        return SubmissionIntentState.NOT_SENT
    return SubmissionIntentState.REGISTERED_UNSENT


class SubmissionEvidence(StrEnum):
    """The ONE classification of "was this order sent?", closed to four values.

    Shared by the run-time send guard (G2), the takeover rule, basis verification and the
    recovery predicate (20.1-17; VERIFICATION gap SC4/REC-01, REVIEW SAF-01).
    """

    BROKER_EVIDENCE = "broker_evidence"
    REJECTED = "rejected"
    PROVEN_NOT_SENT = "proven_not_sent"
    UNESTABLISHED = "unestablished"


def attempt_log_registered(
    *,
    has_attempts: bool,
    order_created_at: datetime,
    first_intent_created_at: datetime | None,
) -> bool:
    """Registered under the attempt-log invariant (S1-R3).

    True with at least one attempt row, or when the order was registered as the realisation of
    a pinned operation intent: created at or after the EARLIEST intent row that ever referenced
    it (an order retried by a later operation's intent is still operation-bound; the row that
    first referenced it pre-dates its registration). False when no pinned intent ever referenced
    the order, and for a legacy order that only a LATER reuse row references.
    """

    if has_attempts:
        return True
    if first_intent_created_at is None:
        return False
    return _utc(order_created_at) >= _utc(first_intent_created_at)


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def classify_submission_evidence(
    *,
    status: OrderLifecycleState | str,
    broker_order_id: str | None,
    attempts: Sequence[AttemptRecord],
    attempt_log_registered: bool,
) -> SubmissionEvidence:
    """Closed verdict for one order, evaluated in this order:

    1. broker evidence (a broker id or a broker-applied status): ``BROKER_EVIDENCE``;
    2. a locally REJECTED order: ``REJECTED``;
    3. attempt rows present: ``PROVEN_NOT_SENT`` only when the whole history is complete
       pre_connection / deadline_expired, ``REJECTED`` for a recorded 4xx class, otherwise
       ``UNESTABLISHED`` (NULL, ambiguous, exists_reported or accepted without a broker id);
    4. no attempt row: ``PROVEN_NOT_SENT`` only for an attempt-log-registered order in
       ``pending_submission`` or ``submission_failed``; a legacy order (and any ``unknown``
       order) is ``UNESTABLISHED``.
    """

    order_status = OrderLifecycleState(status)
    if broker_order_id or order_status in _SUBMITTED_STATUSES:
        return SubmissionEvidence.BROKER_EVIDENCE
    if order_status == OrderLifecycleState.REJECTED:
        return SubmissionEvidence.REJECTED
    if attempts:
        submission_class = classify_submission(attempts)
        if submission_class is SubmissionClass.NOT_SENT:
            return SubmissionEvidence.PROVEN_NOT_SENT
        if submission_class is SubmissionClass.REJECTED:
            return SubmissionEvidence.REJECTED
        return SubmissionEvidence.UNESTABLISHED
    if attempt_log_registered and order_status in (
        OrderLifecycleState.PENDING_SUBMISSION,
        OrderLifecycleState.SUBMISSION_FAILED,
    ):
        return SubmissionEvidence.PROVEN_NOT_SENT
    return SubmissionEvidence.UNESTABLISHED


def proven_not_sent(
    order: PaperOrder | None,
    attempts: Sequence[AttemptRecord],
    *,
    attempt_log_registered: bool,
) -> bool:
    """True only with POSITIVE evidence that no request for this intent ever left the process.

    ``classify_submission_evidence(...) is PROVEN_NOT_SENT`` (S1-R3, round 5): an intent is
    proven not sent only when EVERY attempt of its whole history is ``pre_connection`` or
    ``deadline_expired`` with a complete outcome. Zero attempt rows prove it ONLY for an
    intent registered under the attempt-log invariant (``attempt_log_registered``) in
    ``pending_submission`` OR ``submission_failed`` (SAF-01 B, 20.1-17: a SUBMISSION_FAILED order
    with no attempt row exists only when the failure happened before T1 committed, so no request
    can have left the process), because that invariant makes a POST without an earlier committed
    attempt row impossible. A LEGACY order (no attempt rows, not operation-bound) is NEVER
    proven not sent, and neither is a NULL outcome, a timeout or transport error after connect,
    a 5xx/429, exists_reported, accepted or rejected, nor any broker evidence (broker id,
    broker-applied status, fills). No order at all (a planned intent) was never sent.
    """

    if order is None:
        return True
    return (
        classify_submission_evidence(
            status=order.status,
            broker_order_id=order.broker_order_id,
            attempts=attempts,
            attempt_log_registered=attempt_log_registered,
        )
        is SubmissionEvidence.PROVEN_NOT_SENT
    )


def reached_or_may_have_reached_broker(
    order: PaperOrder | None,
    attempts: Sequence[AttemptRecord],
    *,
    attempt_log_registered: bool,
) -> bool:
    """S3-R4 'reached or may have reached the broker': exactly the negation of ``proven_not_sent``."""

    return not proven_not_sent(order, attempts, attempt_log_registered=attempt_log_registered)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _record_from_row(row: OrderSubmissionAttempt) -> AttemptRecord:
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


def load_submission_attempts(session: Session, paper_order_id: uuid.UUID) -> list[AttemptRecord]:
    """All attempts of one order in attempt order (one SELECT)."""

    rows = session.execute(
        select(OrderSubmissionAttempt)
        .where(OrderSubmissionAttempt.paper_order_id == paper_order_id)
        .order_by(OrderSubmissionAttempt.attempt_number)
    ).scalars()
    return [_record_from_row(row) for row in rows]


def load_submission_evidence(
    session: Session, orders: Sequence[PaperOrder]
) -> dict[uuid.UUID, SubmissionEvidence]:
    """The ONE batch form of the shared verdict for consumers that already hold the order rows
    (reconciliation, 20.1-32, G-1); exactly two statements for any number of orders (none for
    no orders). The provenance input is the EARLIEST intent row of ANY operation, as in recovery
    ``_ORDER_COLUMNS`` and ``intent_identity.load_strategy_order_facts``; the complete verdict is
    returned (all four values), never a reduced boolean."""

    if not orders:
        return {}
    order_ids = [order.id for order in orders]
    attempts_by_order: dict[uuid.UUID, list[AttemptRecord]] = {}
    for row in session.execute(
        select(OrderSubmissionAttempt)
        .where(OrderSubmissionAttempt.paper_order_id.in_(order_ids))
        .order_by(OrderSubmissionAttempt.paper_order_id, OrderSubmissionAttempt.attempt_number)
    ).scalars():
        attempts_by_order.setdefault(row.paper_order_id, []).append(_record_from_row(row))
    first_intent_at = dict(
        session.execute(
            select(
                ExecutionOperationIntent.paper_order_id,
                func.min(ExecutionOperationIntent.created_at),
            )
            .where(ExecutionOperationIntent.paper_order_id.in_(order_ids))
            .group_by(ExecutionOperationIntent.paper_order_id)
        ).all()
    )
    verdicts: dict[uuid.UUID, SubmissionEvidence] = {}
    for order in orders:
        attempts = attempts_by_order.get(order.id, [])
        registered = attempt_log_registered(
            has_attempts=bool(attempts),
            order_created_at=order.created_at,
            first_intent_created_at=first_intent_at.get(order.id),
        )
        verdicts[order.id] = classify_submission_evidence(
            status=order.status,
            broker_order_id=order.broker_order_id,
            attempts=attempts,
            attempt_log_registered=registered,
        )
    return verdicts


class SubmissionAttemptLog(Protocol):
    """Durable attempt log consumed by the Alpaca order submit path."""

    def existing_attempts(self) -> Sequence[AttemptRecord]:
        """All earlier attempts of the order, across sessions (one SELECT)."""

    def intent_registered_at(self) -> datetime | None:
        """When the local intent was registered (lower bound for D-07), if known."""

    def begin_attempt(self) -> int:
        """Commit a new attempt row (outcome NULL) and return its number."""

    def complete_attempt(
        self,
        number: int,
        *,
        outcome_class: AttemptOutcomeClass,
        http_status: int | None = None,
        error_type: str | None = None,
        broker_message: str | None = None,
    ) -> None:
        """Write the outcome of attempt ``number`` exactly once."""


class DbSubmissionAttemptLog:
    """Attempt log backed by ``order_submission_attempts``.

    Each method opens its OWN ``session_scope`` and so commits independently of
    the caller, unless an outer ``session`` is passed (20.1-11/15 write the row
    inside their authorization transaction; the row is still committed before
    the POST because that transaction commits first).
    """

    def __init__(
        self,
        settings: Settings,
        *,
        paper_order_id: uuid.UUID,
        strategy_run_id: uuid.UUID | None,
    ) -> None:
        self._settings = settings
        self._paper_order_id = paper_order_id
        self._strategy_run_id = strategy_run_id

    @property
    def paper_order_id(self) -> uuid.UUID:
        return self._paper_order_id

    def existing_attempts(self) -> list[AttemptRecord]:
        with session_scope(self._settings) as session:
            return load_submission_attempts(session, self._paper_order_id)

    def intent_registered_at(self) -> datetime | None:
        with session_scope(self._settings) as session:
            return session.execute(
                select(PaperOrder.created_at).where(PaperOrder.id == self._paper_order_id)
            ).scalar_one_or_none()

    def begin_attempt(self, session: Session | None = None, **columns: object) -> int:
        """Insert the begin-attempt row (outcome NULL). Fails closed outside transaction T1:
        raises ``AttemptNotAuthorizedError`` unless called inside ``send_authorization_scope``
        (``columns`` carry the T1 fencing columns: started_at, execution_epoch, executor_job_id,
        authorization_deadline)."""

        if not _T1_ACTIVE.get():
            raise AttemptNotAuthorizedError(
                "begin_attempt is callable only inside transaction T1 (authorize_send); "
                "refusing to open an attempt row without a committed authorization."
            )
        if session is not None:
            return self._begin(session, columns)
        with session_scope(self._settings) as own_session:
            return self._begin(own_session, columns)

    def _begin(self, session: Session, columns: dict[str, object]) -> int:
        # attempt_number spans ALL earlier attempts of the order (across sessions).
        highest = session.execute(
            select(func.max(OrderSubmissionAttempt.attempt_number)).where(
                OrderSubmissionAttempt.paper_order_id == self._paper_order_id
            )
        ).scalar_one()
        number = int(highest or 0) + 1
        values: dict[str, object] = {"started_at": datetime.now(UTC), **columns}
        session.add(
            OrderSubmissionAttempt(
                paper_order_id=self._paper_order_id,
                strategy_run_id=self._strategy_run_id,
                attempt_number=number,
                **values,
            )
        )
        session.flush()
        return number

    def complete_attempt(
        self,
        number: int,
        *,
        outcome_class: AttemptOutcomeClass,
        http_status: int | None = None,
        error_type: str | None = None,
        broker_message: str | None = None,
        session: Session | None = None,
    ) -> None:
        if session is not None:
            self._complete(
                session,
                number,
                outcome_class=outcome_class,
                http_status=http_status,
                error_type=error_type,
                broker_message=broker_message,
            )
            return
        with session_scope(self._settings) as own_session:
            self._complete(
                own_session,
                number,
                outcome_class=outcome_class,
                http_status=http_status,
                error_type=error_type,
                broker_message=broker_message,
            )

    def _complete(
        self,
        session: Session,
        number: int,
        *,
        outcome_class: AttemptOutcomeClass,
        http_status: int | None,
        error_type: str | None,
        broker_message: str | None,
    ) -> None:
        row = session.execute(
            select(OrderSubmissionAttempt)
            .where(
                OrderSubmissionAttempt.paper_order_id == self._paper_order_id,
                OrderSubmissionAttempt.attempt_number == number,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if row is None:
            raise LookupError(
                f"No submission attempt {number} for paper_order '{self._paper_order_id}'."
            )
        if row.completed_at is not None:
            raise AttemptAlreadyCompletedError(
                f"Submission attempt {number} of paper_order '{self._paper_order_id}' "
                "already has an outcome."
            )
        row.outcome_class = AttemptOutcomeClass(outcome_class).value
        row.http_status = http_status
        row.error_type = error_type[:ERROR_TYPE_MAX_CHARS] if error_type else None
        row.broker_message = broker_message[:BROKER_MESSAGE_MAX_CHARS] if broker_message else None
        row.completed_at = datetime.now(UTC)
        session.flush()


class NullSubmissionAttemptLog:
    """No-op log for unit tests ONLY; production fails closed when no log is bound."""

    def existing_attempts(self) -> Sequence[AttemptRecord]:
        return ()

    def intent_registered_at(self) -> datetime | None:
        return None

    def begin_attempt(self) -> int:
        return 0

    def complete_attempt(
        self,
        number: int,
        *,
        outcome_class: AttemptOutcomeClass,
        http_status: int | None = None,
        error_type: str | None = None,
        broker_message: str | None = None,
    ) -> None:
        return None


_ATTEMPT_LOG: ContextVar[SubmissionAttemptLog | None] = ContextVar(
    "submission_attempt_log", default=None
)


@contextmanager
def bind_attempt_log(log: SubmissionAttemptLog) -> Iterator[SubmissionAttemptLog]:
    """Bind ``log`` as the current attempt log for the enclosed broker call."""

    token = _ATTEMPT_LOG.set(log)
    try:
        yield log
    finally:
        _ATTEMPT_LOG.reset(token)


def current_attempt_log() -> SubmissionAttemptLog | None:
    """The bound attempt log, or ``None`` when none is bound."""

    return _ATTEMPT_LOG.get()
