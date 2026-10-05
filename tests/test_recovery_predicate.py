"""DB-backed tests for the uncertain-outcome recovery predicate (REC-01, D-12/D-14, W-1 declined).

Every named test below pins one 04 acceptance bullet. The predicate is a pure read over
persisted evidence; evidence is appended only inside broker-sync passes
(``assess_unestablished_intents``) and by the broker-statement writer.
"""

from __future__ import annotations

import inspect
import uuid
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from tests.support.migrated_db import migrated_database
from tests.support.query_counter import count_queries
from tests.support.recovery_fixtures import (
    OTHER,
    OWNER,
    at,
    seed_account_run,
    seed_intent,
    seed_job,
    seed_operation_bound_intent,
    seed_paper_run,
    seed_recording,
    seed_strategy_reconciliation,
    seed_uncertain_session,
    strategy_row,
)
from tests.test_attribution_reconciliation import FakeBroker, _broker_order

from trading_platform.core import clock
from trading_platform.core.settings import load_settings
from trading_platform.db.models import (
    AttemptOutcomeClass,
    Job,
    JobStatus,
    OrderLifecycleState,
    OrderSubmissionAttempt,
    PaperOrder,
    RecoveryRecord,
)
from trading_platform.db.session import session_scope
from trading_platform.services import recovery
from trading_platform.services.alpaca import AlpacaClientError, BrokerOrderSnapshot
from trading_platform.services.execution import ExecutionOrderStatus, OrderSide
from trading_platform.services.execution.sync_orders import sync_account_state, sync_paper_state
from trading_platform.services.reconciliation import (
    latest_broker_effect_at,
    latest_standalone_reconciliation,
    reconcile_account,
)
from trading_platform.services.recovery import (
    AbsenceEvidenceItem,
    EvidenceResult,
    GateCode,
    IntentNotFoundError,
    IntentNotOnMissingOrderPathError,
    InvalidBrokerStatementError,
    RecoveryClassification,
    StatementConflictError,
    UncertainOutcomeIssue,
    UnresolvedReason,
    account_recovery_status,
    get_job_recovery,
    outcome_issue_inputs,
    record_broker_statement,
    strategy_recovery_status,
)

AMBIGUOUS = AttemptOutcomeClass.AMBIGUOUS
PRE_CONNECTION = AttemptOutcomeClass.PRE_CONNECTION
DEADLINE = AttemptOutcomeClass.DEADLINE_EXPIRED


@pytest.fixture()
def recovery_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    with migrated_database(monkeypatch, "recovery_predicate") as name:
        yield name


class LookupBroker(FakeBroker):
    """FakeBroker with an order-by-client-id lookup and a POST counter.

    ``lookup`` maps a client_order_id to a snapshot (found) or ``None`` (a clean 404);
    ``lookup_error`` makes the lookup raise instead. ``submit_order`` must never be called.
    """

    def __init__(self, *, lookup: dict[str, BrokerOrderSnapshot | None] | None = None, **kw: Any):
        super().__init__(**kw)
        self.lookup = lookup or {}
        self.lookup_error: Exception | None = None
        self.lookup_calls = 0
        self.post_count = 0

    def get_order_by_client_order_id(self, client_order_id: str) -> BrokerOrderSnapshot | None:
        self.lookup_calls += 1
        if self.lookup_error is not None:
            raise self.lookup_error
        return self.lookup.get(client_order_id)

    def submit_order(self, *_args: object, **_kwargs: object) -> None:
        self.post_count += 1
        raise AssertionError("recovery must never submit an order")


def _snapshot_for(
    order_id: uuid.UUID,
    *,
    broker_status: str = "filled",
    broker_order_id: str = "b-found-1",
    quantity: str = "10",
    symbol: str = "AAPL",
    order_type: str = "market",
    created_delta: timedelta = timedelta(minutes=5),
    **overrides: Any,
) -> BrokerOrderSnapshot:
    """A broker record for the seeded intent, with the raw fields the D-07 check reads."""

    with session_scope(load_settings()) as session:
        order = session.get(PaperOrder, order_id)
        assert order is not None
        client_order_id = order.client_order_id
        created = order.created_at + created_delta
    status = {
        "filled": ExecutionOrderStatus.FILLED,
        "canceled": ExecutionOrderStatus.CANCELED,
        "rejected": ExecutionOrderStatus.REJECTED,
        "expired": ExecutionOrderStatus.EXPIRED,
        "partially_filled": ExecutionOrderStatus.PARTIALLY_FILLED,
        "replaced": ExecutionOrderStatus.REPLACED,
    }.get(broker_status, ExecutionOrderStatus.PENDING)
    base = _broker_order(
        broker_order_id=broker_order_id,
        client_order_id=client_order_id,
        symbol=symbol,
        quantity=quantity,
        broker_status=broker_status,
        created_at=created,
    )
    return replace(
        base,
        status=status,
        order_type=order_type,
        raw_payload={
            "id": broker_order_id,
            "client_order_id": client_order_id,
            "qty": quantity,
            "type": order_type,
            "created_at": created.isoformat(),
        },
        **overrides,
    )


class Timeline:
    """Patches ``core.clock.now_utc`` so evidence timestamps are deterministic."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, start: datetime) -> None:
        self.now = start
        monkeypatch.setattr(clock, "now_utc", lambda: self.now)

    def advance(self, **delta: float) -> datetime:
        self.now += timedelta(**delta)
        return self.now


def _status(strategy_id: str = OWNER, **kwargs: Any) -> recovery.RecoveryStatus:
    with session_scope(load_settings()) as session:
        return strategy_recovery_status(session, strategy_id, **kwargs)


def _account_status(**kwargs: Any) -> recovery.AccountRecoveryStatus:
    with session_scope(load_settings()) as session:
        return account_recovery_status(session, **kwargs)


def _issue_kinds() -> list[UncertainOutcomeIssue]:
    with session_scope(load_settings()) as session:
        return sorted(row.kind for row in outcome_issue_inputs(session))


def _arrange(builder: Any) -> Any:
    with session_scope(load_settings()) as session:
        return builder(session)


def _clean_account_run(minute: int) -> None:
    _arrange(lambda s: seed_account_run(s, completed_at=at(minute)))


def _sync_account(broker: LookupBroker) -> None:
    sync_account_state(settings=load_settings(), broker_client=broker)


def _records(order_id: uuid.UUID | None = None) -> list[RecoveryRecord]:
    with session_scope(load_settings()) as session:
        stmt = select(RecoveryRecord).order_by(RecoveryRecord.created_at, RecoveryRecord.id)
        if order_id is not None:
            stmt = stmt.where(RecoveryRecord.paper_order_id == order_id)
        rows = list(session.execute(stmt).scalars())
        session.expunge_all()
        return rows


# ---------------------------------------------------------------------------
# Closed vocabularies and the clock
# ---------------------------------------------------------------------------


def test_gate_codes_are_exactly_the_three_typed_conflict_codes() -> None:
    assert {c.value for c in GateCode} == {
        "outcome_unresolved",
        "reconciliation_required",
        "reconciliation_not_clean",
    }
    assert recovery.RECOVERY_GATE_CODES == {c.value for c in GateCode}


def test_now_utc_is_timezone_aware_and_patchable(monkeypatch: pytest.MonkeyPatch) -> None:
    assert clock.now_utc().tzinfo is not None
    assert clock.now_utc().utcoffset() == timedelta(0)
    fixed = datetime(2030, 1, 2, 3, 4, tzinfo=UTC)
    monkeypatch.setattr(clock, "now_utc", lambda: fixed)
    assert clock.now_utc() == fixed


def test_every_unresolved_reason_is_a_closed_member_mapping_to_unresolved_issue() -> None:
    assert {r.value for r in UnresolvedReason} == {
        "lookup_error",
        "undocumented_not_found_response",
        "page_cap_reached",
        "unmapped_status",
        "id_mismatch",
        "execution_path_unproven",
    }
    assert set(recovery.UNRESOLVED_REASON_ISSUE) == set(UnresolvedReason)
    assert set(recovery.UNRESOLVED_REASON_ISSUE.values()) == {
        UncertainOutcomeIssue.OUTCOME_UNCERTAIN_UNRESOLVED
    }


# ---------------------------------------------------------------------------
# No uncertainty, and the 29 Sep fixture
# ---------------------------------------------------------------------------


def test_zero_uncertain_items_is_resolved_without_any_reconciliation(recovery_db: str) -> None:
    _arrange(lambda s: strategy_row(s, OWNER))
    status = _status()
    assert status.resolved and status.gate_code is None
    assert status.intents == () and status.jobs == ()
    assert _issue_kinds() == []
    # A successful (non-uncertain) Job is not an uncertain outcome.
    _arrange(lambda s: seed_job(s, uncertain=False, status=JobStatus.SUCCEEDED))
    assert _status().resolved


def test_29_sep_jobs_nothing_submitted_then_resolved_after_fresh_clean_account_reconciliation(
    recovery_db: str,
) -> None:
    def build(session: Any) -> None:
        # Two uncertain paper-session Jobs (no linked paper_execution run) + one uncertain
        # broker-order-sync Job, all for the strategy, none with an order row.
        seed_job(session, job_type="paper-session", completed_at=at(0))
        seed_job(session, job_type="paper-session", completed_at=at(1))
        seed_job(session, job_type="broker-order-sync", completed_at=at(2))

    _arrange(build)
    status = _status()
    assert {i.classification for i in status.intents} == {RecoveryClassification.NOTHING_SUBMITTED}
    assert len(status.intents) == 3 and all(i.established for i in status.intents)
    assert not status.resolved and status.gate_code is GateCode.RECONCILIATION_REQUIRED
    assert _issue_kinds() == [UncertainOutcomeIssue.OUTCOME_UNCERTAIN_UNVERIFIED] * 3

    # A reconciliation completed BEFORE the latest broker-touching effect does not count.
    _clean_account_run(1)
    assert _status().gate_code is GateCode.RECONCILIATION_REQUIRED
    # Fresh clean account-level reconciliation after the latest effect -> RESOLVED.
    _clean_account_run(3)
    after = _status()
    assert after.resolved and after.gate_code is None
    assert _issue_kinds() == []
    assert _account_status().resolved


def test_account_level_uncertain_job_blocks_every_strategy(recovery_db: str) -> None:
    def build(session: Any) -> None:
        strategy_row(session, OTHER)
        seed_job(session, job_type="broker-order-sync", strategy_id=None, completed_at=at(0))

    _arrange(build)
    for strategy_id in (OWNER, OTHER):
        status = _status(strategy_id)
        assert status.gate_code is GateCode.RECONCILIATION_REQUIRED, strategy_id
    # The account subject needs an ACCOUNT run: a strategy-level run does not cover it.
    _arrange(lambda s: seed_strategy_reconciliation(s, strategy_id=OWNER, completed_at=at(5)))
    assert _status(OWNER).resolved  # "either" scope for the strategy's own gate
    assert not _account_status().resolved  # the account subject still needs an account run
    _clean_account_run(6)
    assert _account_status().resolved
    assert _status(OTHER).resolved


def test_historical_uncertain_job_reopens_after_any_later_broker_effect(recovery_db: str) -> None:
    """Pinned (D-14, literal): the reconciliation must follow the latest effect, evaluated now.

    ``outcome_uncertain`` never clears, so a strategy with uncertain history needs a fresh
    standalone reconciliation after every later paper-session or sync. Fails closed.
    """

    _arrange(lambda s: seed_job(s, job_type="broker-order-sync", completed_at=at(0)))
    _clean_account_run(1)
    assert _status().resolved
    _arrange(lambda s: seed_job(s, uncertain=False, completed_at=at(2), status=_succeeded()))
    assert _status().gate_code is GateCode.RECONCILIATION_REQUIRED
    _clean_account_run(3)
    assert _status().resolved


def _succeeded() -> JobStatus:
    return JobStatus.SUCCEEDED


# ---------------------------------------------------------------------------
# Reconciliation qualification
# ---------------------------------------------------------------------------


def test_succeeded_but_blocking_reconciliation_never_resolves(recovery_db: str) -> None:
    """The 15:46:50 fixture: status succeeded, blocks_execution true."""

    _arrange(lambda s: seed_job(s, job_type="broker-order-sync", completed_at=at(0)))
    _arrange(lambda s: seed_account_run(s, completed_at=at(1), blocks=True, status="succeeded"))
    status = _status()
    assert not status.resolved and status.gate_code is GateCode.RECONCILIATION_NOT_CLEAN
    assert status.reconciliation is not None and status.reconciliation.status == "succeeded"
    assert _issue_kinds() == [UncertainOutcomeIssue.OUTCOME_UNCERTAIN_UNRESOLVED]
    # Unresolved reasons on an otherwise succeeded, non-blocking run also read as not clean.
    _arrange(
        lambda s: seed_account_run(s, completed_at=at(2), unresolved_reasons=("broker_history",))
    )
    assert _status().gate_code is GateCode.RECONCILIATION_NOT_CLEAN
    _arrange(lambda s: seed_account_run(s, completed_at=at(3), status="failed"))
    assert _status().gate_code is GateCode.RECONCILIATION_NOT_CLEAN
    _clean_account_run(4)
    assert _status().resolved


def test_in_session_reconciliation_never_qualifies(recovery_db: str) -> None:
    _arrange(lambda s: seed_job(s, job_type="broker-order-sync", completed_at=at(0)))
    _arrange(
        lambda s: seed_strategy_reconciliation(
            s,
            completed_at=at(5),
            trigger_source="paper_execution_reconciliation",
            job_type="paper-session",
        )
    )
    assert _status().gate_code is GateCode.RECONCILIATION_REQUIRED
    # A strategy-level run created by a job of another type does not qualify either.
    _arrange(
        lambda s: seed_strategy_reconciliation(
            s, completed_at=at(6), trigger_source="job", job_type="broker-order-sync"
        )
    )
    assert _status().gate_code is GateCode.RECONCILIATION_REQUIRED
    # A real standalone strategy-scope reconciliation qualifies.
    _arrange(lambda s: seed_strategy_reconciliation(s, completed_at=at(7)))
    assert _status().resolved


def test_recording_effect_time_is_the_boundary_not_the_job_completion(recovery_db: str) -> None:
    _arrange(lambda s: seed_job(s, job_type="broker-order-sync", completed_at=at(0)))
    _arrange(lambda s: seed_recording(s, created_at=at(10)))
    _clean_account_run(5)
    assert _status().gate_code is GateCode.RECONCILIATION_REQUIRED
    _clean_account_run(11)
    assert _status().resolved


def test_combined_read_agrees_with_latest_standalone_reconciliation(recovery_db: str) -> None:
    """Parity: the predicate's two-statement read equals the 20.1-08/09 reader semantics."""

    def build(session: Any) -> None:
        seed_job(session, job_type="broker-order-sync", completed_at=at(0))
        seed_job(
            session,
            job_type="paper-session",
            uncertain=False,
            completed_at=at(4),
            status=_succeeded(),
        )
        seed_recording(session, created_at=at(2))
        seed_account_run(session, completed_at=at(1))
        seed_strategy_reconciliation(session, completed_at=at(3), blocks=True)
        seed_strategy_reconciliation(
            session,
            completed_at=at(4),
            trigger_source="paper_execution_reconciliation",
            job_type="paper-session",
        )
        seed_account_run(session, completed_at=at(5), blocks=True)

    _arrange(build)
    status = _status()
    with session_scope(load_settings()) as session:
        boundary = latest_broker_effect_at(session, OWNER)
        expected = latest_standalone_reconciliation(
            session, OWNER, "either", completed_after=boundary
        )
    assert boundary == at(4) == status.boundary
    assert expected is not None and status.reconciliation is not None
    assert (status.reconciliation.run_id, status.reconciliation.is_clean) == (
        expected.run_id,
        expected.is_clean,
    )
    assert status.gate_code is GateCode.RECONCILIATION_NOT_CLEAN
    # And with nothing qualifying after the boundary both report none.
    _arrange(lambda s: seed_job(s, uncertain=False, completed_at=at(9), status=_succeeded()))
    with session_scope(load_settings()) as session:
        boundary = latest_broker_effect_at(session, OWNER)
        assert (
            latest_standalone_reconciliation(session, OWNER, "either", completed_after=boundary)
            is None
        )
    assert _status().gate_code is GateCode.RECONCILIATION_REQUIRED


def test_latest_broker_effect_at_strategy_filter(recovery_db: str) -> None:
    def build(session: Any) -> None:
        strategy_row(session, OTHER)
        seed_job(
            session, strategy_id=OWNER, completed_at=at(1), uncertain=False, status=_succeeded()
        )
        seed_job(
            session, strategy_id=OTHER, completed_at=at(5), uncertain=False, status=_succeeded()
        )
        seed_job(
            session,
            job_type="broker-order-sync",
            strategy_id=None,
            completed_at=at(3),
            uncertain=False,
            status=_succeeded(),
        )

    _arrange(build)
    with session_scope(load_settings()) as session:
        assert latest_broker_effect_at(session) == at(5)
        assert latest_broker_effect_at(session, None) == at(5)
        assert latest_broker_effect_at(session, OWNER) == at(3)  # own + account-level only
        assert latest_broker_effect_at(session, OTHER) == at(5)
    _arrange(lambda s: seed_recording(s, created_at=at(8)))
    with session_scope(load_settings()) as session:
        assert latest_broker_effect_at(session, OWNER) == at(8)  # recordings always count


# ---------------------------------------------------------------------------
# Per-intent classification
# ---------------------------------------------------------------------------


def test_linked_run_with_zero_order_rows_is_unresolved_execution_path_unproven(
    recovery_db: str,
) -> None:
    def build(session: Any) -> None:
        job = seed_job(session, completed_at=at(0))
        seed_paper_run(session, job)

    _arrange(build)
    status = _status()
    (intent,) = status.intents
    assert intent.classification is RecoveryClassification.UNRESOLVED
    assert intent.unresolved_reason is UnresolvedReason.EXECUTION_PATH_UNPROVEN
    assert status.gate_code is GateCode.OUTCOME_UNRESOLVED
    # A closed unresolved reason maps to outcome_uncertain_unresolved even though nothing
    # was recorded: the reason itself is the evidence that it cannot be established.
    assert _issue_kinds() == [UncertainOutcomeIssue.OUTCOME_UNCERTAIN_UNRESOLVED]


@pytest.mark.parametrize(
    ("attempts", "expected"),
    [
        ((PRE_CONNECTION,), RecoveryClassification.NOT_SENT),
        ((PRE_CONNECTION, DEADLINE), RecoveryClassification.NOT_SENT),
        ((AttemptOutcomeClass.REJECTED,), RecoveryClassification.REJECTED_AT_SUBMISSION),
        ((AMBIGUOUS,), RecoveryClassification.NOT_FOUND),
        ((None,), RecoveryClassification.NOT_FOUND),  # a crash-left row reads as ambiguous
        ((), RecoveryClassification.NOT_FOUND),  # legacy: no attempt rows is never not_sent
        ((AMBIGUOUS, PRE_CONNECTION), RecoveryClassification.NOT_FOUND),
        ((None, DEADLINE), RecoveryClassification.NOT_FOUND),
        ((PRE_CONNECTION, AMBIGUOUS), RecoveryClassification.NOT_FOUND),
        ((AttemptOutcomeClass.DUPLICATE_REPORTED,), RecoveryClassification.NOT_FOUND),
    ],
)
def test_attempt_history_classification(
    recovery_db: str, attempts: tuple[AttemptOutcomeClass | None, ...], expected: Any
) -> None:
    _arrange(lambda s: seed_uncertain_session(s, attempts=attempts))
    (intent,) = _status().intents
    assert intent.classification is expected
    assert intent.established == (expected is not RecoveryClassification.NOT_FOUND)


def test_one_proven_not_sent_attempt_never_resolves_an_earlier_ambiguous_attempt(
    recovery_db: str,
) -> None:
    for index, attempts in enumerate(((AMBIGUOUS, PRE_CONNECTION), (None, DEADLINE))):
        strategy_id = (OWNER, OTHER)[index]
        _arrange(
            lambda s, a=attempts, sid=strategy_id: seed_uncertain_session(
                s, strategy_id=sid, attempts=a
            )
        )
        status = _status(strategy_id)
        assert status.gate_code is GateCode.OUTCOME_UNRESOLVED, attempts
        assert [i.classification for i in status.intents] == [RecoveryClassification.NOT_FOUND]


def test_legacy_pending_order_without_attempts_is_unestablished(recovery_db: str) -> None:
    _arrange(
        lambda s: seed_uncertain_session(
            s, status=OrderLifecycleState.PENDING_SUBMISSION, attempts=()
        )
    )
    status = _status()
    assert status.gate_code is GateCode.OUTCOME_UNRESOLVED
    assert status.intents[0].classification is RecoveryClassification.NOT_FOUND


def test_crash_left_pending_order_is_an_intent_even_without_a_flagged_job(recovery_db: str) -> None:
    def build(session: Any) -> None:
        run = seed_paper_run(session, None)
        seed_intent(session, run, status=OrderLifecycleState.PENDING_SUBMISSION, attempts=(None,))

    _arrange(build)
    status = _status()
    assert status.gate_code is GateCode.OUTCOME_UNRESOLVED
    assert status.jobs == () and len(status.intents) == 1

    # A LEGACY registered-but-unattempted order (no operation intent row) is never proven not sent.
    def plain(session: Any) -> None:
        run = seed_paper_run(session, None, OTHER)
        seed_intent(session, run, status=OrderLifecycleState.PENDING_SUBMISSION, attempts=())

    _arrange(plain)
    # 20.1-17 gap (a): was `_status(OTHER).resolved`. A legacy zero-attempt pending order is never
    # proven not sent (TL-4); run-time G2 already refused it, so the predicate must agree.
    other = _status(OTHER)
    assert other.gate_code is GateCode.OUTCOME_UNRESOLVED
    assert [i.classification for i in other.intents] == [RecoveryClassification.NOT_FOUND]


def test_operation_bound_unattempted_order_is_not_an_uncertain_intent(recovery_db: str) -> None:
    """The case the former assertion protected: the registered_unsent intent of a paused
    operation (an attempt-log-registered zero-attempt pending order) creates no gate."""

    def build(session: Any) -> None:
        run = seed_paper_run(session, None, OTHER)
        seed_operation_bound_intent(
            session,
            run,
            status=OrderLifecycleState.PENDING_SUBMISSION,
            attempts=(),
            strategy_id=OTHER,
        )

    _arrange(build)
    status = _status(OTHER)
    assert status.resolved and status.gate_code is None
    assert status.intents == ()
    assert _issue_kinds() == []


@pytest.mark.parametrize(
    ("status", "attempts", "expected"),
    [
        (OrderLifecycleState.PENDING_SUBMISSION, (), RecoveryClassification.NOT_SENT),
        (OrderLifecycleState.SUBMISSION_FAILED, (), RecoveryClassification.NOT_SENT),
        (OrderLifecycleState.UNKNOWN, (), RecoveryClassification.NOT_FOUND),
        (OrderLifecycleState.SUBMISSION_FAILED, (AMBIGUOUS,), RecoveryClassification.NOT_FOUND),
    ],
)
def test_operation_bound_attempt_history_classification(
    recovery_db: str,
    status: OrderLifecycleState,
    attempts: tuple[AttemptOutcomeClass | None, ...],
    expected: Any,
) -> None:
    """On a FLAGGED Job an attempt-log-registered order is classified by the shared function:
    zero attempts prove not-sent for pending_submission / submission_failed (SAF-01 A/B)."""

    def build(session: Any) -> None:
        job = seed_job(session, completed_at=at(0))
        run = seed_paper_run(session, job)
        seed_operation_bound_intent(session, run, status=status, attempts=attempts)

    _arrange(build)
    (intent,) = _status().intents
    assert intent.classification is expected
    assert intent.established == (expected is RecoveryClassification.NOT_SENT)


@pytest.mark.parametrize(
    "status", [OrderLifecycleState.PENDING_SUBMISSION, OrderLifecycleState.SUBMISSION_FAILED]
)
def test_operation_bound_unsent_order_on_a_flagged_job_waits_only_for_reconciliation(
    recovery_db: str, status: OrderLifecycleState
) -> None:
    """SAF-01 A/B: the attempt log proves it was never sent, so only the fresh clean
    reconciliation after the Job is missing; once it exists the strategy resolves."""

    def build(session: Any) -> None:
        job = seed_job(session, completed_at=at(0))
        run = seed_paper_run(session, job)
        seed_operation_bound_intent(session, run, status=status, attempts=())

    _arrange(build)
    assert _status().gate_code is GateCode.RECONCILIATION_REQUIRED
    _clean_account_run(5)
    assert _status().gate_code is None


@pytest.mark.parametrize(
    "status", [OrderLifecycleState.PENDING_SUBMISSION, OrderLifecycleState.SUBMISSION_FAILED]
)
@pytest.mark.parametrize(
    "outcomes",
    [(AMBIGUOUS,), (AttemptOutcomeClass.ACCEPTED,), (AttemptOutcomeClass.DUPLICATE_REPORTED,)],
)
def test_unflagged_unparked_ambiguous_order_is_an_intent(
    recovery_db: str, status: OrderLifecycleState, outcomes: tuple[AttemptOutcomeClass, ...]
) -> None:
    """20.1-17 gap (b): an order whose attempt completed ambiguous / accepted-without-id /
    exists_reported, never parked UNKNOWN, on a Job NOT flagged outcome_uncertain, is listed
    under its originating Job and gates the strategy."""

    def build(session: Any) -> uuid.UUID:
        job = seed_job(session, uncertain=False, status=JobStatus.CANCELLED, completed_at=at(0))
        run = seed_paper_run(session, job)
        seed_operation_bound_intent(session, run, status=status, attempts=outcomes)
        return job.id

    job_id = _arrange(build)
    status_read = _status()
    assert status_read.gate_code is GateCode.OUTCOME_UNRESOLVED
    (intent,) = status_read.intents
    assert intent.job_id == job_id
    assert intent.classification is RecoveryClassification.NOT_FOUND
    assert status_read.jobs == ()  # only flagged Jobs are listed as uncertain Jobs


def test_unflagged_shapes_that_prove_not_sent_create_no_gate(recovery_db: str) -> None:
    def build(session: Any) -> None:
        job = seed_job(session, uncertain=False, status=JobStatus.CANCELLED, completed_at=at(0))
        run = seed_paper_run(session, job)
        seed_operation_bound_intent(
            session, run, status=OrderLifecycleState.SUBMISSION_FAILED, attempts=(PRE_CONNECTION,)
        )
        seed_operation_bound_intent(
            session,
            run,
            status=OrderLifecycleState.SUBMISSION_FAILED,
            attempts=(AttemptOutcomeClass.REJECTED,),
            ticker="MSFT",
        )
        seed_operation_bound_intent(
            session, run, status=OrderLifecycleState.PENDING_SUBMISSION, attempts=(), ticker="NVDA"
        )

    _arrange(build)
    status = _status()
    assert status.resolved and status.intents == ()


def test_unflagged_unknown_order_with_a_not_sent_history_is_still_listed(recovery_db: str) -> None:
    """The existing UNKNOWN liveness behaviour: listed (as not_sent) so the next sync pass can
    move it to submission_failed; it awaits a reconciliation, it is not unestablished."""

    def build(session: Any) -> None:
        job = seed_job(session, uncertain=False, status=JobStatus.CANCELLED, completed_at=at(0))
        run = seed_paper_run(session, job)
        seed_operation_bound_intent(
            session, run, status=OrderLifecycleState.UNKNOWN, attempts=(PRE_CONNECTION,)
        )

    _arrange(build)
    status = _status()
    (intent,) = status.intents
    assert intent.classification is RecoveryClassification.NOT_SENT
    assert status.gate_code is GateCode.RECONCILIATION_REQUIRED


def test_unflagged_unknown_order_is_an_intent(recovery_db: str) -> None:
    def build(session: Any) -> None:
        run = seed_paper_run(session, None)
        seed_intent(session, run, status=OrderLifecycleState.UNKNOWN, attempts=(AMBIGUOUS,))

    _arrange(build)
    assert _status().gate_code is GateCode.OUTCOME_UNRESOLVED


@pytest.mark.parametrize(
    ("broker_status", "lifecycle", "state"),
    [
        ("new", OrderLifecycleState.SUBMITTED, "working"),
        ("partially_filled", OrderLifecycleState.PARTIALLY_FILLED, "partially_filled"),
        ("filled", OrderLifecycleState.FILLED, "filled"),
        ("canceled", OrderLifecycleState.CANCELED, "canceled_expired"),
        ("expired", OrderLifecycleState.EXPIRED, "canceled_expired"),
        ("rejected", OrderLifecycleState.REJECTED, "rejected"),
        ("replaced", OrderLifecycleState.UNKNOWN, "replaced"),
        ("done_for_day", OrderLifecycleState.SUBMITTED, "working"),
    ],
)
def test_found_in_any_broker_state_is_established(
    recovery_db: str, broker_status: str, lifecycle: OrderLifecycleState, state: str
) -> None:
    _arrange(
        lambda s: seed_uncertain_session(
            s,
            status=lifecycle,
            broker_order_id="b-1",
            broker_status=broker_status,
            completed_at=at(0),
        )
    )
    (intent,) = _status().intents
    assert intent.classification is RecoveryClassification.FOUND_VERIFIED
    assert intent.broker_state is not None and intent.broker_state.value == state
    assert intent.established
    assert _status().gate_code is GateCode.RECONCILIATION_REQUIRED
    _clean_account_run(5)
    assert _status().resolved


def test_unmapped_broker_status_is_unresolved(recovery_db: str) -> None:
    _arrange(
        lambda s: seed_uncertain_session(
            s, status=OrderLifecycleState.UNKNOWN, broker_order_id="b-1", broker_status="mystery"
        )
    )
    (intent,) = _status().intents
    assert intent.classification is RecoveryClassification.UNRESOLVED
    assert intent.unresolved_reason is UnresolvedReason.UNMAPPED_STATUS
    assert _issue_kinds() == [UncertainOutcomeIssue.OUTCOME_UNCERTAIN_UNRESOLVED]


# ---------------------------------------------------------------------------
# Evidence collection inside a broker-sync pass
# ---------------------------------------------------------------------------


def test_found_filled_after_ambiguous_post_resolves_without_statement_and_never_reposts(
    recovery_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, order = _arrange(lambda s: seed_uncertain_session(s, completed_at=at(0)))
    Timeline(monkeypatch, at(2))
    assert _status().gate_code is GateCode.OUTCOME_UNRESOLVED

    snapshot = _snapshot_for(order.id, broker_status="filled")
    broker = LookupBroker(lookup={snapshot.client_order_id: snapshot})
    _sync_account(broker)

    assert broker.post_count == 0 and broker.lookup_calls == 1
    with session_scope(load_settings()) as session:
        stored = session.get(PaperOrder, order.id)
        assert stored is not None
        assert stored.status is OrderLifecycleState.FILLED
        assert stored.broker_order_id == "b-found-1"
        attempts_after = session.execute(
            select(func.count()).select_from(OrderSubmissionAttempt)
        ).scalar_one()
    assert attempts_after == 1  # still the single original POST attempt
    (intent,) = _status().intents
    assert intent.classification is RecoveryClassification.FOUND_VERIFIED
    assert intent.statement is None  # resolved without any broker statement
    # Established, but resolution still needs the fresh clean standalone reconciliation.
    assert _status().gate_code is GateCode.RECONCILIATION_REQUIRED
    _clean_account_run(10)
    assert _status().resolved
    kinds = [(r.kind, r.classification) for r in _records(order.id)]
    assert ("classification", "found_verified") in kinds


def test_found_by_the_status_all_listing_resolves_through_the_normal_sync(
    recovery_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, order = _arrange(lambda s: seed_uncertain_session(s, completed_at=at(0)))
    Timeline(monkeypatch, at(2))
    snapshot = _snapshot_for(order.id, broker_status="new")
    broker = LookupBroker(orders=[snapshot])
    _sync_account(broker)
    assert broker.post_count == 0 and broker.lookup_calls == 0  # nothing left to look up
    (intent,) = _status().intents
    assert intent.classification is RecoveryClassification.FOUND_VERIFIED
    assert intent.broker_state is recovery.BrokerState.WORKING


def test_open_order_found_with_clean_reconciliation_resolves_but_is_never_resubmitted(
    recovery_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, order = _arrange(lambda s: seed_uncertain_session(s, completed_at=at(0)))
    Timeline(monkeypatch, at(2))
    snapshot = _snapshot_for(order.id, broker_status="new")
    broker = LookupBroker(lookup={snapshot.client_order_id: snapshot})
    _sync_account(broker)
    _clean_account_run(30)
    status = _status()
    assert status.resolved
    (intent,) = status.intents
    assert intent.broker_state is recovery.BrokerState.WORKING
    assert intent.resubmission_permitted is False
    assert broker.post_count == 0


def test_replaced_order_resolves_intent_and_successor_stays_unrecognized(
    recovery_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, order = _arrange(lambda s: seed_uncertain_session(s, completed_at=at(0)))
    Timeline(monkeypatch, at(2))
    replaced = _snapshot_for(order.id, broker_status="replaced", successor_order_id="b-successor")
    successor = _broker_order(
        broker_order_id="b-successor", client_order_id="successor-1", broker_status="new"
    )
    broker = LookupBroker(orders=[replaced, successor])
    _sync_account(broker)
    (intent,) = _status().intents
    assert intent.classification is RecoveryClassification.FOUND_VERIFIED
    assert intent.broker_state is recovery.BrokerState.REPLACED
    # The successor is unknown locally: a fresh account reconciliation blocks (unrecognized).
    reconcile_account(settings=load_settings(), broker_client=broker)
    status = _status()
    assert status.gate_code is GateCode.RECONCILIATION_NOT_CLEAN
    assert not status.resolved


def test_absence_evidence_during_validity_stays_unresolved(
    recovery_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, order = _arrange(lambda s: seed_uncertain_session(s, completed_at=at(0)))
    timeline = Timeline(monkeypatch, at(2))
    broker = LookupBroker(lookup={})  # a clean 404 for everything
    _sync_account(broker)
    timeline.advance(seconds=load_settings().execution.recovery_absence_grace_seconds + 1)
    _sync_account(broker)

    status = _status()
    (intent,) = status.intents
    assert intent.absence_evidence_complete  # items a (twice, grace apart), b, c, d confirmed
    assert intent.classification is RecoveryClassification.NOT_FOUND
    assert status.gate_code is GateCode.OUTCOME_UNRESOLVED
    assert intent.resubmission_permitted is False
    assert broker.post_count == 0
    assert _issue_kinds() == [UncertainOutcomeIssue.OUTCOME_UNCERTAIN_UNRESOLVED]
    # Even a fresh clean reconciliation afterwards does not resolve a never-found order.
    _clean_account_run(60)
    assert _status().gate_code is GateCode.OUTCOME_UNRESOLVED


@pytest.mark.parametrize("elapsed_hours", [0, 1, 24, 24 * 30, 24 * 365])
def test_absence_evidence_after_close_stays_unresolved(
    recovery_db: str, monkeypatch: pytest.MonkeyPatch, elapsed_hours: int
) -> None:
    """No clock/evidence combination resolves a never-found order (W-1 declined)."""

    _arrange(lambda s: seed_uncertain_session(s, completed_at=at(0)))
    timeline = Timeline(monkeypatch, at(2))
    broker = LookupBroker(lookup={})
    _sync_account(broker)
    timeline.advance(
        hours=elapsed_hours, seconds=load_settings().execution.recovery_absence_grace_seconds + 1
    )
    _sync_account(broker)
    _clean_account_run(60 + elapsed_hours * 60)
    status = _status(now=timeline.now + timedelta(days=3650))
    assert status.gate_code is GateCode.OUTCOME_UNRESOLVED
    assert status.unestablished[0].classification is RecoveryClassification.NOT_FOUND
    assert status.unestablished[0].resubmission_permitted is False


def test_absence_evidence_complete_flag_and_configurable_grace() -> None:
    def row(
        item: AbsenceEvidenceItem, minute: int, result: EvidenceResult = EvidenceResult.CONFIRMED
    ):
        return recovery.RecordView(
            id=uuid.uuid4(),
            kind=recovery.RecoveryRecordKind.ABSENCE_EVIDENCE,
            classification=None,
            broker_state=None,
            unresolved_reason=None,
            evidence_item=item,
            evidence_result=result,
            observed_at=at(minute),
            statement=None,
            reference=None,
            reason=None,
            recorded_by="test",
            created_at=at(minute),
        )

    a, b, c, d = (
        AbsenceEvidenceItem.A_CLIENT_ORDER_ID_404,
        AbsenceEvidenceItem.B_LIST_SCAN_NO_MATCH,
        AbsenceEvidenceItem.C_NO_FILL_REFERENCE,
        AbsenceEvidenceItem.D_NO_EXPOSURE_CHANGE,
    )
    evidence = [row(a, 0), row(a, 6), row(b, 6), row(c, 6), row(d, 6)]
    assert recovery.absence_evidence_complete(evidence, 300)  # 6 min >= 5 min
    assert not recovery.absence_evidence_complete(evidence, 600)  # grace is configurable
    assert recovery.absence_evidence_complete(evidence, 0)
    assert not recovery.absence_evidence_complete(evidence[:1] + evidence[2:], 0)  # one (a) only
    assert not recovery.absence_evidence_complete(
        [row(a, 0), row(a, 6, EvidenceResult.ERROR), row(b, 6), row(c, 6), row(d, 6)], 300
    )
    assert not recovery.absence_evidence_complete(evidence[:-1], 0)  # (d) missing
    assert load_settings().execution.recovery_absence_grace_seconds == 300


@pytest.mark.parametrize(
    ("raised", "reason"),
    [
        (AlpacaClientError("Alpaca request failed after 3 retries"), UnresolvedReason.LOOKUP_ERROR),
        (
            AlpacaClientError("Alpaca request failed with status 422: nope"),
            UnresolvedReason.UNDOCUMENTED_NOT_FOUND_RESPONSE,
        ),
        (RuntimeError("boom"), UnresolvedReason.LOOKUP_ERROR),
    ],
)
def test_lookup_failures_are_unresolved_with_a_closed_reason(
    recovery_db: str, monkeypatch: pytest.MonkeyPatch, raised: Exception, reason: UnresolvedReason
) -> None:
    _arrange(lambda s: seed_uncertain_session(s, completed_at=at(0)))
    Timeline(monkeypatch, at(2))
    broker = LookupBroker()
    broker.lookup_error = raised
    _sync_account(broker)
    (intent,) = _status().intents
    assert intent.classification is RecoveryClassification.UNRESOLVED
    assert intent.unresolved_reason is reason
    a_rows = [
        e
        for e in intent.absence_evidence
        if e.evidence_item is AbsenceEvidenceItem.A_CLIENT_ORDER_ID_404
    ]
    assert [e.evidence_result for e in a_rows] == [EvidenceResult.ERROR]
    assert _issue_kinds() == [UncertainOutcomeIssue.OUTCOME_UNCERTAIN_UNRESOLVED]
    # A later clean 404 supersedes the error classification (latest wins) but resolves nothing.
    broker.lookup_error = None
    _sync_account(broker)
    (later,) = _status().intents
    assert later.classification is RecoveryClassification.NOT_FOUND


def test_id_mismatch_lookup_result_is_not_applied(
    recovery_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, order = _arrange(lambda s: seed_uncertain_session(s, completed_at=at(0)))
    Timeline(monkeypatch, at(2))
    wrong = _snapshot_for(order.id, quantity="999")  # same id, different quantity
    broker = LookupBroker(lookup={wrong.client_order_id: wrong})
    _sync_account(broker)
    (intent,) = _status().intents
    assert intent.classification is RecoveryClassification.UNRESOLVED
    assert intent.unresolved_reason is UnresolvedReason.ID_MISMATCH
    with session_scope(load_settings()) as session:
        stored = session.get(PaperOrder, order.id)
        assert stored is not None and stored.broker_order_id is None
        assert stored.status is OrderLifecycleState.UNKNOWN


def test_page_cap_during_listing_records_unresolved_page_cap_reached(
    recovery_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_attribution_reconciliation import _cap_error

    _arrange(lambda s: seed_uncertain_session(s, completed_at=at(0)))
    Timeline(monkeypatch, at(2))
    with pytest.raises(type(_cap_error())):
        _sync_account(LookupBroker(orders_error=_cap_error()))
    (intent,) = _status().intents
    assert intent.unresolved_reason is UnresolvedReason.PAGE_CAP_REACHED
    assert _issue_kinds() == [UncertainOutcomeIssue.OUTCOME_UNCERTAIN_UNRESOLVED]


def test_strategy_scope_sync_collects_evidence_for_its_own_strategy_only(
    recovery_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import date

    def build(session: Any) -> tuple[uuid.UUID, uuid.UUID]:
        _, _, own = seed_uncertain_session(session, strategy_id=OWNER, completed_at=at(0))
        _, _, other = seed_uncertain_session(session, strategy_id=OTHER, completed_at=at(0))
        return own.id, other.id

    own_id, other_id = _arrange(build)
    Timeline(monkeypatch, at(2))
    broker = LookupBroker()
    sync_paper_state(
        OWNER, as_of_session=date(2024, 1, 5), settings=load_settings(), broker_client=broker
    )
    assert broker.lookup_calls == 1 and broker.post_count == 0
    assert _records(own_id) and not _records(other_id)


def test_sync_never_resolves_by_itself(recovery_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """D-15: a broker sync neither resolves anything nor changes the predicate result."""

    _arrange(lambda s: seed_uncertain_session(s, completed_at=at(0)))
    Timeline(monkeypatch, at(2))
    before = _status()
    _sync_account(LookupBroker())
    after = _status()
    assert (before.resolved, before.gate_code) == (after.resolved, after.gate_code)
    assert after.gate_code is GateCode.OUTCOME_UNRESOLVED


def test_not_sent_intent_left_unknown_returns_to_retryable_submission_failed(
    recovery_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, order = _arrange(
        lambda s: seed_uncertain_session(s, attempts=(PRE_CONNECTION, DEADLINE), completed_at=at(0))
    )
    Timeline(monkeypatch, at(2))
    _sync_account(LookupBroker())
    with session_scope(load_settings()) as session:
        stored = session.get(PaperOrder, order.id)
        assert stored is not None
        assert stored.status is OrderLifecycleState.SUBMISSION_FAILED
    assert [r.classification for r in _records(order.id) if r.kind == "classification"] == [
        "not_sent"
    ]


@pytest.mark.parametrize(
    "attempts",
    [(AMBIGUOUS,), (None,), (AMBIGUOUS, PRE_CONNECTION), (AttemptOutcomeClass.REJECTED,), ()],
)
def test_only_a_proven_not_sent_history_makes_an_unknown_intent_sendable(
    recovery_db: str, monkeypatch: pytest.MonkeyPatch, attempts: tuple[Any, ...]
) -> None:
    _, _, order = _arrange(lambda s: seed_uncertain_session(s, attempts=attempts))
    Timeline(monkeypatch, at(2))
    _sync_account(LookupBroker())
    with session_scope(load_settings()) as session:
        stored = session.get(PaperOrder, order.id)
        assert stored is not None
        assert stored.status is OrderLifecycleState.UNKNOWN


def test_a_statement_executor_termination_or_time_never_makes_an_intent_sendable(
    recovery_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, order = _arrange(lambda s: seed_uncertain_session(s, completed_at=at(0)))
    Timeline(monkeypatch, at(2))
    with session_scope(load_settings()) as session:
        record_broker_statement(
            session, order.id, "not_received", "ticket-9", "support said so", "op"
        )
    _sync_account(LookupBroker())
    with session_scope(load_settings()) as session:
        stored = session.get(PaperOrder, order.id)
        assert stored is not None
        assert stored.status is OrderLifecycleState.UNKNOWN


# ---------------------------------------------------------------------------
# Statements, the operation seam and J-2
# ---------------------------------------------------------------------------


def test_not_received_statement_is_evidence_only_and_never_permits_resend(
    recovery_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, order = _arrange(lambda s: seed_uncertain_session(s, completed_at=at(0)))
    timeline = Timeline(monkeypatch, at(2))
    broker = LookupBroker()
    _sync_account(broker)
    timeline.advance(seconds=load_settings().execution.recovery_absence_grace_seconds + 1)
    _sync_account(broker)

    class OpenView:
        def state_for_intent(self, session: Any, paper_order_id: Any) -> str:
            return "open"

    with session_scope(load_settings()) as session:
        result = record_broker_statement(
            session, order.id, "not_received", "ticket-1", "broker support: not received", "op"
        )
    assert result.changed and result.classification is RecoveryClassification.NOT_FOUND

    status = _status(operation_view=OpenView(), now=timeline.now + timedelta(hours=1))
    (intent,) = status.intents
    assert intent.absence_evidence_complete and intent.statement is not None
    assert status.gate_code is GateCode.OUTCOME_UNRESOLVED
    assert intent.resubmission_permitted is False
    assert intent.classification is RecoveryClassification.NOT_FOUND
    assert recovery.RESUBMISSION_UNAVAILABLE_REASON == "resubmission_unavailable"
    _clean_account_run(120)
    assert _status().gate_code is GateCode.OUTCOME_UNRESOLVED
    assert broker.post_count == 0

    # The late original shows up at the broker: found_verified resolves it.
    snapshot = _snapshot_for(order.id, broker_status="filled")
    broker.lookup[snapshot.client_order_id] = snapshot
    timeline.advance(minutes=5)
    _sync_account(broker)
    (found,) = _status().intents
    assert found.classification is RecoveryClassification.FOUND_VERIFIED
    assert found.statement is not None  # kept as audited evidence
    _clean_account_run(200)
    assert _status().resolved


def test_order_record_statement_does_not_resolve_until_found(
    recovery_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, order = _arrange(lambda s: seed_uncertain_session(s, completed_at=at(0)))
    timeline = Timeline(monkeypatch, at(2))
    with session_scope(load_settings()) as session:
        record_broker_statement(
            session, order.id, "order_record", "ORD-123", "support record", "op"
        )
    _clean_account_run(10)
    assert _status().gate_code is GateCode.OUTCOME_UNRESOLVED
    snapshot = _snapshot_for(order.id, broker_status="canceled")
    _sync_account(LookupBroker(lookup={snapshot.client_order_id: snapshot}))
    timeline.advance(minutes=1)
    _clean_account_run(500)
    assert _status().resolved


def test_ended_operation_keeps_ambiguous_intent_blocking_and_listed(recovery_db: str) -> None:
    """J-2: ending an operation resolves nothing; recovery stays available."""

    job, _, order = _arrange(lambda s: seed_uncertain_session(s, completed_at=at(0)))

    class TerminatedView:
        def state_for_intent(self, session: Any, paper_order_id: Any) -> str:
            return "terminated"

    open_status = _status()
    ended_status = _status(operation_view=TerminatedView())
    assert open_status.gate_code is ended_status.gate_code is GateCode.OUTCOME_UNRESOLVED
    assert [i.intent_id for i in ended_status.intents] == [order.id]
    with session_scope(load_settings()) as session:
        payload = get_job_recovery(session, job.id, operation_view=TerminatedView())
    assert payload["resolved"] is False and payload["gate_code"] == "outcome_unresolved"
    (listed,) = payload["intents"]
    assert listed["intent_id"] == str(order.id) and listed["operation_state"] == "terminated"
    assert listed["blocking"] is True and listed["resubmission_permitted"] is False


@pytest.mark.parametrize(
    "reason",
    [r for r in UnresolvedReason if r is not UnresolvedReason.EXECUTION_PATH_UNPROVEN],
)
def test_every_recorded_unresolved_reason_maps_to_outcome_uncertain_unresolved(
    recovery_db: str, reason: UnresolvedReason
) -> None:
    def build(session: Any) -> None:
        _, _, order = seed_uncertain_session(session, completed_at=at(0))
        session.add(
            RecoveryRecord(
                kind="classification",
                job_id=None,
                paper_order_id=order.id,
                strategy_public_id=OWNER,
                classification="unresolved",
                unresolved_reason=reason.value,
                recorded_by="test",
            )
        )

    _arrange(build)
    (intent,) = _status().intents
    assert intent.unresolved_reason is reason
    assert _issue_kinds() == [UncertainOutcomeIssue.OUTCOME_UNCERTAIN_UNRESOLVED]


def test_issue_inputs_unverified_until_evidence_then_unresolved(
    recovery_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _arrange(lambda s: seed_uncertain_session(s, completed_at=at(0)))
    Timeline(monkeypatch, at(2))
    assert _issue_kinds() == [UncertainOutcomeIssue.OUTCOME_UNCERTAIN_UNVERIFIED]
    _sync_account(LookupBroker())
    assert _issue_kinds() == [UncertainOutcomeIssue.OUTCOME_UNCERTAIN_UNRESOLVED]


# ---------------------------------------------------------------------------
# Statement writer
# ---------------------------------------------------------------------------


def test_statement_writer_idempotent_conflict_and_closed_errors(recovery_db: str) -> None:
    _, _, order = _arrange(lambda s: seed_uncertain_session(s, completed_at=at(0)))

    def write(
        statement: str = "not_received", reference: str = "ref-1", reason: str = "why"
    ) -> Any:
        with session_scope(load_settings()) as session:
            return record_broker_statement(session, order.id, statement, reference, reason, "op")

    first = write()
    assert first.changed is True and first.statement.value == "not_received"
    again = write(reason="a different reason")  # same statement + reference
    assert again.changed is False and again.record_id == first.record_id
    with pytest.raises(StatementConflictError):
        write(statement="order_record")
    with pytest.raises(StatementConflictError):
        write(reference="ref-2")
    assert len([r for r in _records(order.id) if r.kind == "broker_statement"]) == 1

    with pytest.raises(InvalidBrokerStatementError) as bad_statement:
        write(statement="resend")
    assert bad_statement.value.field_name == "statement"
    for bad_ref in ("", "   ", "x" * 501, "a\x00b"):
        with pytest.raises(InvalidBrokerStatementError) as bad:
            write(reference=bad_ref)
        assert bad.value.field_name == "reference"
    for bad_reason in ("", "  ", "y" * 501, "a\x00b"):
        with pytest.raises(InvalidBrokerStatementError) as bad:
            write(reason=bad_reason)
        assert bad.value.field_name == "reason"


def test_statement_writer_unknown_intent_and_not_on_the_missing_order_path(
    recovery_db: str,
) -> None:
    def build(session: Any) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
        _, _, found = seed_uncertain_session(
            session,
            status=OrderLifecycleState.FILLED,
            broker_order_id="b-9",
            broker_status="filled",
        )
        _, _, not_sent = seed_uncertain_session(
            session, strategy_id=OTHER, attempts=(PRE_CONNECTION,)
        )
        run = seed_paper_run(session, None)
        # An attempt-log-registered (operation-bound) unattempted order; a LEGACY one is
        # unestablished since 20.1-17 and therefore on the missing-order path.
        plain = seed_operation_bound_intent(
            session, run, status=OrderLifecycleState.PENDING_SUBMISSION, attempts=()
        )
        return found.id, not_sent.id, plain.id

    found_id, not_sent_id, plain_id = _arrange(build)
    with session_scope(load_settings()) as session:
        with pytest.raises(IntentNotFoundError):
            record_broker_statement(session, uuid.uuid4(), "not_received", "r", "why", "op")
        for intent_id in (found_id, not_sent_id, plain_id):
            with pytest.raises(IntentNotOnMissingOrderPathError):
                record_broker_statement(session, intent_id, "not_received", "r", "why", "op")
    assert _records() == []


# ---------------------------------------------------------------------------
# Bounded, read-only
# ---------------------------------------------------------------------------


def _grow_history(multiplier: int) -> None:
    def build(session: Any) -> None:
        for index in range(multiplier):
            job = seed_job(session, completed_at=at(index))
            run = seed_paper_run(session, job)
            seed_intent(session, run, attempts=(AMBIGUOUS,))
            seed_job(session, job_type="broker-order-sync", completed_at=at(index))
            seed_account_run(session, completed_at=at(index))
            seed_strategy_reconciliation(session, completed_at=at(index))

    _arrange(build)


def test_status_reads_issue_at_most_two_statements_independent_of_history(recovery_db: str) -> None:
    def measure() -> tuple[int, int, int]:
        with session_scope(load_settings()) as session:
            with count_queries(session) as strategy_counter:
                strategy_recovery_status(session, OWNER)
            with count_queries(session) as account_counter:
                account_recovery_status(session)
            with count_queries(session) as issue_counter:
                outcome_issue_inputs(session)
        return strategy_counter.count, account_counter.count, issue_counter.count

    _grow_history(1)
    small = measure()
    _grow_history(10)
    large = measure()
    assert small == large == (2, 2, 2)


def _grow_unflagged_shapes(multiplier: int) -> None:
    def build(session: Any) -> None:
        for index in range(multiplier):
            job = seed_job(
                session, uncertain=False, status=JobStatus.CANCELLED, completed_at=at(index)
            )
            run = seed_paper_run(session, job)
            seed_operation_bound_intent(
                session, run, status=OrderLifecycleState.SUBMISSION_FAILED, attempts=(AMBIGUOUS,)
            )
            seed_intent(
                session,
                run,
                status=OrderLifecycleState.PENDING_SUBMISSION,
                attempts=(),
                ticker="MSFT",
            )
            seed_operation_bound_intent(
                session,
                run,
                status=OrderLifecycleState.PENDING_SUBMISSION,
                attempts=(),
                ticker="NVDA",
            )

    _arrange(build)


def test_status_reads_stay_within_two_statements_with_unflagged_shapes(recovery_db: str) -> None:
    def measure() -> tuple[int, int, int]:
        with session_scope(load_settings()) as session:
            with count_queries(session) as strategy_counter:
                strategy_recovery_status(session, OWNER)
            with count_queries(session) as account_counter:
                account_recovery_status(session)
            with count_queries(session) as issue_counter:
                outcome_issue_inputs(session)
        return strategy_counter.count, account_counter.count, issue_counter.count

    _grow_unflagged_shapes(1)
    small = measure()
    _grow_unflagged_shapes(10)
    large = measure()
    assert small == large == (2, 2, 2)


def test_get_job_recovery_statement_count_is_bounded_independent_of_history(
    recovery_db: str,
) -> None:
    job, _, _ = _arrange(lambda s: seed_uncertain_session(s, completed_at=at(0)))

    def measure() -> int:
        with session_scope(load_settings()) as session:
            with count_queries(session) as counter:
                get_job_recovery(session, job.id)
        return counter.count

    _grow_history(1)
    small = measure()
    _grow_history(10)
    # Three statements (Job lookup + the two-statement status) plus ONE batched read of the
    # operation context of every intent (DbOperationView.states_for_intents, 20.1-11).
    assert measure() == small <= 4


def test_reads_perform_no_writes(recovery_db: str) -> None:
    job, _, order = _arrange(lambda s: seed_uncertain_session(s, completed_at=at(0)))

    def row_counts() -> tuple[int, ...]:
        with session_scope(load_settings()) as session:
            return tuple(
                session.execute(select(func.count()).select_from(model)).scalar_one()
                for model in (RecoveryRecord, PaperOrder, Job)
            )

    before = row_counts()
    with session_scope(load_settings()) as session:
        with count_queries(session) as counter:
            strategy_recovery_status(session, OWNER)
            account_recovery_status(session)
            outcome_issue_inputs(session)
            get_job_recovery(session, job.id)
    assert row_counts() == before
    writes = [
        s
        for s in counter.statements
        if s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
    ]
    assert writes == []


def test_get_job_recovery_unknown_job_and_non_uncertain_job(recovery_db: str) -> None:
    with session_scope(load_settings()) as session:
        with pytest.raises(LookupError):
            get_job_recovery(session, uuid.uuid4())
    plain = _arrange(lambda s: seed_job(s, uncertain=False, status=_succeeded()))
    with session_scope(load_settings()) as session:
        payload = get_job_recovery(session, plain.id)
    assert (
        payload["resolved"] is True
        and payload["intents"] == []
        and payload["evidence_package"] == []
    )


def test_get_job_recovery_evidence_package_shape(
    recovery_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    job, _, order = _arrange(lambda s: seed_uncertain_session(s, completed_at=at(0)))
    timeline = Timeline(monkeypatch, at(2))
    broker = LookupBroker()
    _sync_account(broker)
    timeline.advance(seconds=301)
    _sync_account(broker)
    with session_scope(load_settings()) as session:
        payload = get_job_recovery(session, job.id)
    assert payload["job_type"] == "paper-session" and payload["strategy_id"] == OWNER
    (intent,) = payload["intents"]
    assert intent["classification"] == "not_found" and intent["absence_evidence_complete"] is True
    assert {e["item"] for e in intent["absence_evidence"]} == {i.value for i in AbsenceEvidenceItem}
    assert all(e["observed_at"] for e in intent["absence_evidence"])
    (package,) = payload["evidence_package"]
    assert package["client_order_id"].startswith("tp-")
    assert package["grace_period_seconds"] == 300 and package["complete"] is True
    assert package["attempts"][0]["outcome_class"] == "ambiguous"
    assert len(package["lookup_results"]) == 2 and package["scan_results"]
    datetime.fromisoformat(payload["as_of"])


# ---------------------------------------------------------------------------
# Source guards
# ---------------------------------------------------------------------------


def test_recovery_module_never_updates_or_deletes_recovery_records() -> None:
    source = Path(inspect.getsourcefile(recovery) or "").read_text()
    for forbidden in (
        "update(RecoveryRecord",
        "delete(RecoveryRecord",
        "session.delete",
        ".delete(",
    ):
        assert forbidden not in source, forbidden
    assert "UPDATE recovery_records" not in source and "DELETE FROM recovery_records" not in source


def test_recovery_reads_never_import_a_resend_path() -> None:
    source = Path(inspect.getsourcefile(recovery) or "").read_text()
    assert "submit_order" not in source
    assert ".post(" not in source.lower()
    assert "withdraw" not in source.lower()


def test_order_status_lifecycle_unaffected_by_reads(recovery_db: str) -> None:
    _, _, order = _arrange(lambda s: seed_uncertain_session(s, completed_at=at(0)))
    for _ in range(3):
        _status()
    with session_scope(load_settings()) as session:
        stored = session.get(PaperOrder, order.id)
        assert stored is not None and stored.status is OrderLifecycleState.UNKNOWN
        assert Decimal(stored.quantity) == Decimal("10")
        assert OrderSide.BUY.value == "buy"


def test_grace_setting_is_validated_non_negative_and_configurable() -> None:
    from pydantic import ValidationError

    from trading_platform.core.settings import ExecutionSettings

    assert ExecutionSettings().recovery_absence_grace_seconds == 300
    assert ExecutionSettings(recovery_absence_grace_seconds=0).recovery_absence_grace_seconds == 0
    with pytest.raises(ValidationError):
        ExecutionSettings(recovery_absence_grace_seconds=-1)


# ---------------------------------------------------------------------------
# 20.1-11: the real OperationView replaces NullOperationView
# ---------------------------------------------------------------------------


def _link_operation(session: Any, order_id: Any, *, state: str, reason: str | None) -> None:
    from tests.support.operation_fixtures import seed_operation

    from trading_platform.db.models import ExecutionOperationIntent

    order = session.get(PaperOrder, order_id)
    operation = seed_operation(session, state=state, reason=reason)
    session.add(
        ExecutionOperationIntent(
            operation_id=operation.id,
            sequence=1,
            symbol_id=order.symbol_id,
            side=order.side,
            quantity=order.quantity,
            client_order_id=order.client_order_id,
            paper_order_id=order.id,
            decision_fingerprint="f" * 64,
            prior_execution_refs=[],
            disposition="open",
        )
    )
    session.flush()


@pytest.mark.parametrize(
    ("state", "reason", "expected"),
    [
        ("paused", "outcome_unresolved", "open"),
        ("running", None, "open"),
        ("requires_reevaluation", "evaluation_data_changed", "open"),
        ("terminated", "cancelled_by_operator", "terminated"),
        ("completed", None, "completed"),
    ],
)
def test_real_operation_view_reports_the_operation_context(
    recovery_db: str, state: str, reason: str | None, expected: str
) -> None:
    job, _, order = _arrange(lambda s: seed_uncertain_session(s, completed_at=at(0)))
    _arrange(lambda s: _link_operation(s, order.id, state=state, reason=reason))
    view = recovery.DbOperationView()
    with session_scope(load_settings()) as session:
        assert view.state_for_intent(session, order.id) == expected
        assert view.states_for_intents(session, [order.id, uuid.uuid4()])[order.id] == expected
        payload = get_job_recovery(session, job.id)  # the default view is the real one
    (listed,) = payload["intents"]
    assert listed["operation_state"] == expected
    assert listed["blocking"] is True


def test_real_view_reports_none_without_an_operation(recovery_db: str) -> None:
    job, _, order = _arrange(lambda s: seed_uncertain_session(s, completed_at=at(0)))
    with session_scope(load_settings()) as session:
        assert recovery.DbOperationView().state_for_intent(session, order.id) == "none"
        (listed,) = get_job_recovery(session, job.id)["intents"]
    assert listed["operation_state"] == "none"


def test_resubmission_is_never_permitted_whatever_the_real_operation_state(
    recovery_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Open operation + open window + not_received statement + complete absence evidence:
    still False with reason ``resubmission_unavailable``; a terminated operation too."""

    job, _, order = _arrange(lambda s: seed_uncertain_session(s, completed_at=at(0)))
    _arrange(lambda s: _link_operation(s, order.id, state="paused", reason="outcome_unresolved"))
    timeline = Timeline(monkeypatch, at(2))
    broker = LookupBroker()
    _sync_account(broker)
    timeline.advance(seconds=load_settings().execution.recovery_absence_grace_seconds + 1)
    _sync_account(broker)
    with session_scope(load_settings()) as session:
        record_broker_statement(
            session, order.id, "not_received", "ticket-9", "broker support: not received", "op"
        )
    status = _status(
        operation_view=recovery.DbOperationView(), now=timeline.now + timedelta(hours=1)
    )
    (intent,) = status.intents
    assert intent.absence_evidence_complete and intent.statement is not None
    assert intent.resubmission_permitted is False
    with session_scope(load_settings()) as session:
        (listed,) = get_job_recovery(session, job.id)["intents"]
    assert listed["resubmission_permitted"] is False
    assert listed["resubmission_reason"] == "resubmission_unavailable"
    assert listed["operation_state"] == "open"
    with session_scope(load_settings()) as session:
        from sqlalchemy import update

        from trading_platform.db.models import ExecutionOperation

        session.execute(
            update(ExecutionOperation).values(
                state="terminated", reason="cancelled_by_operator", ended_by="op"
            )
        )
    with session_scope(load_settings()) as session:
        (ended,) = get_job_recovery(session, job.id)["intents"]
    assert ended["operation_state"] == "terminated"
    assert ended["resubmission_permitted"] is False and ended["blocking"] is True
    assert broker.post_count == 0
