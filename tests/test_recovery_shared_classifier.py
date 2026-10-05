"""One shared classification of 'was this order sent?' for every consumer (20.1-17).

Closes VERIFICATION gap SC4/REC-01 (W-3 / W-4) and REVIEW SAF-01: the run-time send guard (G2),
the takeover rule, basis verification and the recovery predicate (A5, the Continue gate, R3, the
per-intent permission check) all read ``attempts.classify_submission_evidence``.

The pure table lives here; the DB-backed consumer-agreement tests are appended below it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from trading_platform.db.models import AttemptOutcomeClass, OrderLifecycleState
from trading_platform.services.execution.attempts import (
    AttemptRecord,
    SubmissionEvidence,
    attempt_log_registered,
    classify_submission_evidence,
    proven_not_sent,
    reached_or_may_have_reached_broker,
)

PRE = AttemptOutcomeClass.PRE_CONNECTION
DEADLINE = AttemptOutcomeClass.DEADLINE_EXPIRED
AMBIGUOUS = AttemptOutcomeClass.AMBIGUOUS
ACCEPTED = AttemptOutcomeClass.ACCEPTED
DUPLICATE = AttemptOutcomeClass.DUPLICATE_REPORTED
REJECTED_ATTEMPT = AttemptOutcomeClass.REJECTED

PENDING = OrderLifecycleState.PENDING_SUBMISSION
FAILED = OrderLifecycleState.SUBMISSION_FAILED
UNKNOWN = OrderLifecycleState.UNKNOWN
FILLED = OrderLifecycleState.FILLED
REJECTED_STATUS = OrderLifecycleState.REJECTED

NOW = datetime(2026, 1, 1, tzinfo=UTC)


class _Order:
    def __init__(self, status: OrderLifecycleState, broker_order_id: str | None = None) -> None:
        self.status = status
        self.broker_order_id = broker_order_id


def _history(*outcomes: AttemptOutcomeClass | None) -> list[AttemptRecord]:
    return [
        AttemptRecord(
            attempt_number=index + 1,
            started_at=NOW,
            completed_at=None if outcome is None else NOW,
            outcome_class=outcome,
        )
        for index, outcome in enumerate(outcomes)
    ]


# (status, broker_order_id, outcomes, attempt_log_registered, expected verdict)
_TABLE: list[tuple[OrderLifecycleState, str | None, tuple[Any, ...], bool, SubmissionEvidence]] = [
    (FILLED, None, (), True, SubmissionEvidence.BROKER_EVIDENCE),
    (PENDING, "b-1", (), True, SubmissionEvidence.BROKER_EVIDENCE),
    (FAILED, "b-1", (PRE,), True, SubmissionEvidence.BROKER_EVIDENCE),
    (REJECTED_STATUS, None, (), False, SubmissionEvidence.REJECTED),
    (FAILED, None, (REJECTED_ATTEMPT,), True, SubmissionEvidence.REJECTED),
    (PENDING, None, (PRE, DEADLINE), False, SubmissionEvidence.PROVEN_NOT_SENT),
    (FAILED, None, (PRE,), True, SubmissionEvidence.PROVEN_NOT_SENT),
    (UNKNOWN, None, (PRE,), True, SubmissionEvidence.PROVEN_NOT_SENT),
    (PENDING, None, (AMBIGUOUS,), True, SubmissionEvidence.UNESTABLISHED),
    (FAILED, None, (None,), True, SubmissionEvidence.UNESTABLISHED),
    (FAILED, None, (PRE, AMBIGUOUS), True, SubmissionEvidence.UNESTABLISHED),
    (FAILED, None, (DUPLICATE,), True, SubmissionEvidence.UNESTABLISHED),
    (FAILED, None, (ACCEPTED,), True, SubmissionEvidence.UNESTABLISHED),
    (PENDING, None, (), True, SubmissionEvidence.PROVEN_NOT_SENT),
    (FAILED, None, (), True, SubmissionEvidence.PROVEN_NOT_SENT),  # SAF-01 B
    (UNKNOWN, None, (), True, SubmissionEvidence.UNESTABLISHED),
    (PENDING, None, (), False, SubmissionEvidence.UNESTABLISHED),  # legacy
    (FAILED, None, (), False, SubmissionEvidence.UNESTABLISHED),  # legacy, TL-4
    (UNKNOWN, None, (), False, SubmissionEvidence.UNESTABLISHED),
]


def test_submission_evidence_is_a_closed_set_of_four() -> None:
    assert {member.value for member in SubmissionEvidence} == {
        "broker_evidence",
        "rejected",
        "proven_not_sent",
        "unestablished",
    }


@pytest.mark.parametrize(("status", "broker_id", "outcomes", "registered", "expected"), _TABLE)
def test_classify_submission_evidence_table(
    status: OrderLifecycleState,
    broker_id: str | None,
    outcomes: tuple[Any, ...],
    registered: bool,
    expected: SubmissionEvidence,
) -> None:
    verdict = classify_submission_evidence(
        status=status,
        broker_order_id=broker_id,
        attempts=_history(*outcomes),
        attempt_log_registered=registered,
    )
    assert verdict is expected
    # A plain string status (the recovery SQL returns text) classifies identically.
    assert (
        classify_submission_evidence(
            status=status.value,
            broker_order_id=broker_id,
            attempts=_history(*outcomes),
            attempt_log_registered=registered,
        )
        is expected
    )


def test_attempt_log_registered_rule() -> None:
    first = NOW
    # Any attempt row registers the order.
    assert attempt_log_registered(
        has_attempts=True, order_created_at=NOW, first_intent_created_at=None
    )
    # No pinned intent ever referenced it: legacy.
    assert not attempt_log_registered(
        has_attempts=False, order_created_at=NOW, first_intent_created_at=None
    )
    # Created before the first intent row that references it: legacy reuse.
    assert not attempt_log_registered(
        has_attempts=False,
        order_created_at=first - timedelta(seconds=1),
        first_intent_created_at=first,
    )
    # At or after the first intent row: operation-bound.
    assert attempt_log_registered(
        has_attempts=False, order_created_at=first, first_intent_created_at=first
    )
    assert attempt_log_registered(
        has_attempts=False,
        order_created_at=first + timedelta(seconds=1),
        first_intent_created_at=first,
    )
    # Naive and aware values compare without raising (both are read as UTC).
    assert attempt_log_registered(
        has_attempts=False,
        order_created_at=first.replace(tzinfo=None),
        first_intent_created_at=first,
    )


@pytest.mark.parametrize(("status", "broker_id", "outcomes", "registered", "expected"), _TABLE)
def test_proven_not_sent_is_the_shared_classifier(
    status: OrderLifecycleState,
    broker_id: str | None,
    outcomes: tuple[Any, ...],
    registered: bool,
    expected: SubmissionEvidence,
) -> None:
    order = _Order(status, broker_id)
    attempts = _history(*outcomes)
    proven = proven_not_sent(order, attempts, attempt_log_registered=registered)  # type: ignore[arg-type]
    assert proven is (expected is SubmissionEvidence.PROVEN_NOT_SENT)
    assert reached_or_may_have_reached_broker(
        order,  # type: ignore[arg-type]
        attempts,
        attempt_log_registered=registered,
    ) is (not proven)
    assert proven_not_sent(None, attempts, attempt_log_registered=registered)
