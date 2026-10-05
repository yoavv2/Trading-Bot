"""The guarded attempt log of the single send path (S1-R3, 20.1-15).

``GuardedAttemptLog`` is the ``SubmissionAttemptLog`` the order client sees while one intent is
sent. Transaction T1 (``operations.authorize_send``) has ALREADY committed the first begin-attempt
row (outcome NULL; epoch, Job id and authorization deadline) before the client is invoked, so:

* the client's FIRST ``begin_attempt()`` returns that pre-authorized attempt number (no second
  row); every LATER call (the in-loop retries after a ``pre_connection`` failure) runs a fresh
  T1, so an executor that lost authority cannot open attempt #2 after a refused connection;
* ``existing_attempts()`` excludes the pre-authorized row (the history the client judges is the
  history BEFORE this send);
* ``send_deadline_passed(number)`` re-reads the WALL clock (never a monotonic clock, which stops
  during system sleep): once the executor-local deadline of that attempt's authorization has
  passed the client sends nothing and records ``deadline_expired`` (a suspension AFTER the check
  is not covered: L5);
* ``complete_attempt`` writes the outcome through the normal path while the executor's fence
  holds (the operation row is locked FOR SHARE, so a takeover cannot slip between the check and
  the write) and through ``complete_attempt_late`` otherwise (the attempt row is evidence, not
  authority; it records a ``late_attempt_outcome`` event).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_platform.core.settings import Settings
from trading_platform.db.models import (
    AttemptOutcomeClass,
    ExecutionOperation,
    OperationState,
    OrderSubmissionAttempt,
    PaperOrder,
)
from trading_platform.db.session import session_scope
from trading_platform.services.execution.attempts import (
    AttemptRecord,
    DbSubmissionAttemptLog,
    load_submission_attempts,
)
from trading_platform.services.execution.operations import (
    Fence,
    SendAuthorization,
    authorize_send,
    complete_attempt_late,
)


def fence_held(session: Session, fence: Fence, *, lock: bool = True) -> bool:
    """True while the executor's authority holds: the operation is ``running`` at the fence's
    epoch and Job. With ``lock`` the row is locked FOR SHARE until the caller's transaction ends,
    so ``acquire_execution`` (FOR UPDATE) cannot interleave between this check and the write."""

    statement = select(ExecutionOperation.id).where(
        ExecutionOperation.id == fence.operation_id,
        ExecutionOperation.execution_epoch == fence.epoch,
        ExecutionOperation.executor_job_id == fence.job_id,
        ExecutionOperation.state == OperationState.RUNNING.value,
    )
    if lock:
        statement = statement.with_for_update(read=True)
    return session.execute(statement).scalar_one_or_none() is not None


class GuardedAttemptLog:
    """Attempt log carrying the T1 authorization of one intent's send (see module docstring)."""

    def __init__(
        self,
        settings: Settings,
        *,
        fence: Fence,
        lease_owner: str,
        intent_id: uuid.UUID,
        paper_order_id: uuid.UUID,
        strategy_run_id: uuid.UUID | None,
        authorization: SendAuthorization,
        price_observed_at: datetime | None = None,
    ) -> None:
        self._settings = settings
        self._price_observed_at = price_observed_at
        self._fence = fence
        self._lease_owner = lease_owner
        self._intent_id = intent_id
        self._paper_order_id = paper_order_id
        self._strategy_run_id = strategy_run_id
        self._first = authorization
        self._authorizations: dict[int, SendAuthorization] = {
            authorization.attempt_number: authorization
        }
        self._first_taken = False

    @property
    def paper_order_id(self) -> uuid.UUID:
        return self._paper_order_id

    @property
    def first_attempt_taken(self) -> bool:
        """Whether the client began (took) the pre-authorized attempt."""

        return self._first_taken

    @property
    def first_attempt_number(self) -> int:
        return self._first.attempt_number

    # -- SubmissionAttemptLog protocol -------------------------------------------------

    def existing_attempts(self) -> Sequence[AttemptRecord]:
        with session_scope(self._settings) as session:
            return [
                attempt
                for attempt in load_submission_attempts(session, self._paper_order_id)
                if attempt.attempt_number < self._first.attempt_number
            ]

    def intent_registered_at(self) -> datetime | None:
        with session_scope(self._settings) as session:
            return session.execute(
                select(PaperOrder.created_at).where(PaperOrder.id == self._paper_order_id)
            ).scalar_one_or_none()

    def begin_attempt(self) -> int:
        if not self._first_taken:
            self._first_taken = True
            return self._first.attempt_number
        # A retry after a pre_connection failure: its own T1 (raises SendRefusedError when the
        # executor lost authority or the intent is no longer provably unsent).
        authorization = authorize_send(
            self._fence.operation_id,
            self._intent_id,
            self._fence.epoch,
            self._fence.job_id,
            lease_owner=self._lease_owner,
            settings=self._settings,
            price_observed_at=self._price_observed_at,
            strategy_run_id=self._strategy_run_id,
        )
        self._authorizations[authorization.attempt_number] = authorization
        return authorization.attempt_number

    def send_deadline_passed(self, number: int) -> bool:
        """The WALL clock is past the executor-local deadline of attempt ``number``."""

        authorization = self._authorizations.get(number)
        if authorization is None:
            return True
        return datetime.now(UTC) > authorization.local_deadline

    def complete_attempt(
        self,
        number: int,
        *,
        outcome_class: AttemptOutcomeClass,
        http_status: int | None = None,
        error_type: str | None = None,
        broker_message: str | None = None,
    ) -> None:
        late = False
        with session_scope(self._settings) as session:
            if fence_held(session, self._fence, lock=True):
                DbSubmissionAttemptLog(
                    self._settings,
                    paper_order_id=self._paper_order_id,
                    strategy_run_id=self._strategy_run_id,
                )._complete(
                    session,
                    number,
                    outcome_class=outcome_class,
                    http_status=http_status,
                    error_type=error_type,
                    broker_message=broker_message,
                )
            else:
                late = True
        if late:
            attempt_id = self._attempt_id(number)
            complete_attempt_late(
                attempt_id,
                outcome_class,
                executor_job_id=self._fence.job_id,
                execution_epoch=self._fence.epoch,
                http_status=http_status,
                error_type=error_type,
                broker_message=broker_message,
                settings=self._settings,
            )

    # -- helpers -------------------------------------------------------------------------

    def _attempt_id(self, number: int) -> uuid.UUID:
        authorization = self._authorizations.get(number)
        if authorization is not None:
            return authorization.attempt_id
        with session_scope(self._settings) as session:
            found: Any = session.execute(
                select(OrderSubmissionAttempt.id).where(
                    OrderSubmissionAttempt.paper_order_id == self._paper_order_id,
                    OrderSubmissionAttempt.attempt_number == number,
                )
            ).scalar_one()
        return uuid.UUID(str(found))


__all__ = ["GuardedAttemptLog", "fence_held"]
