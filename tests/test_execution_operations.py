"""Execution operation domain tests (REC-02, 20.1-11): state machine, lazy expiry, End, S1.

DB-backed (a throwaway migrated PostgreSQL database per test). Calendar facts use XNYS:
evaluation session S = Tuesday 2025-12-02, execution session D = Wednesday 2025-12-03,
window open 09:30 ET until 15:45 ET (15 minute cutoff).
"""

from __future__ import annotations

import inspect
import re
import threading
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select, text
from tests.support.calendar_facts import et, seed_calendar
from tests.support.migrated_db import migrated_database
from tests.support.operation_fixtures import (
    arrange_sendable_gate,
    seed_operation,
    seed_operation_intent,
    seed_operation_job,
)
from tests.support.query_counter import count_queries
from tests.support.recovery_fixtures import OTHER, OWNER, seed_job, seed_paper_run, strategy_row

from trading_platform.core import clock
from trading_platform.core.settings import load_settings
from trading_platform.db.models import (
    AttemptOutcomeClass,
    ExecutionEvent,
    ExecutionOperation,
    ExecutionOperationIntent,
    JobStatus,
    OrderLifecycleState,
    OrderSubmissionAttempt,
    PaperOrder,
    RecoveryRecord,
)
from trading_platform.db.models import execution_operation as operation_models
from trading_platform.db.session import get_engine, session_scope
from trading_platform.services import recovery
from trading_platform.services.concurrency_guard import RunLock, session_run_lock
from trading_platform.services.execution import attempts as attempts_module
from trading_platform.services.execution import operations as ops
from trading_platform.services.execution.attempts import (
    AttemptAlreadyCompletedError,
    AttemptRecord,
    SubmissionIntentState,
    proven_not_sent,
    reached_or_may_have_reached_broker,
)
from trading_platform.services.execution.operations import (
    AttemptOwnershipError,
    Fence,
    IntentDisposition,
    NextAction,
    OperationConflictError,
    OperationNotOpenError,
    OperationOpenError,
    OperationRunningError,
    OperationState,
    PausedReason,
    ReevaluationReason,
    RiskRunAlreadyOperatedError,
    RunLockNotHeldError,
    SendRefusal,
    SendRefusedError,
    TerminatedReason,
    WindowVerdict,
)
from trading_platform.services.risk import RiskDecisionCode

S = date(2025, 12, 2)
LIVE_UNTIL = datetime(2099, 1, 1, tzinfo=UTC)
PRE_CONNECTION = AttemptOutcomeClass.PRE_CONNECTION
AMBIGUOUS = AttemptOutcomeClass.AMBIGUOUS
ACCEPTED = AttemptOutcomeClass.ACCEPTED


_REAL_NOW = clock.now_utc
_OPS_MONKEYPATCH: list[pytest.MonkeyPatch] = []


@pytest.fixture()
def ops_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    _OPS_MONKEYPATCH[:] = [monkeypatch]
    with migrated_database(monkeypatch, "execution_operations") as name:
        seed_calendar(date(2025, 11, 24), date(2026, 1, 9))
        yield name
    _OPS_MONKEYPATCH.clear()


def _now(monkeypatch: pytest.MonkeyPatch, instant: datetime) -> None:
    monkeypatch.setattr(clock, "now_utc", lambda: instant)


IN_WINDOW = et(2025, 12, 3, 10, 0)
BEFORE_OPEN = et(2025, 12, 3, 9, 0)
PAST_CUTOFF = et(2025, 12, 3, 15, 50)
NEXT_DAY = et(2025, 12, 4, 10, 0)


# ---------------------------------------------------------------------------
# Closed vocabularies
# ---------------------------------------------------------------------------


def test_closed_enum_value_sets_are_pinned() -> None:
    assert {m.value for m in OperationState} == {
        "running",
        "paused",
        "requires_reevaluation",
        "terminated",
        "completed",
    }
    assert {m.value for m in PausedReason} == set(operation_models.PAUSED_REASON_VALUES)
    assert len(PausedReason) == 12
    assert {m.value for m in PausedReason} >= {
        "price_unavailable",
        "not_active_paper_strategy",
        "price_moved_beyond_tolerance",
    }
    assert {m.value for m in ReevaluationReason} == set(
        operation_models.REEVALUATION_FIXED_REASON_VALUES
    )
    assert {m.value for m in TerminatedReason} == set(operation_models.TERMINATED_REASON_VALUES)
    assert {m.value for m in IntentDisposition} == {"open", "expired_unsent", "cancelled_unsent"}
    assert {m.value for m in NextAction} == {
        "wait_for_order_then_sync_and_continue",
        "sync_reconcile_then_continue",
        "reset_kill_switch_then_continue",
        "enable_strategy_then_continue",
        "resolve_reconciliation_then_continue",
        "record_external_activity_then_continue",
        "recover_outcome_then_continue",
        "retry_when_broker_available",
        "wait_for_price_then_continue",
        "wait_for_window_open_then_continue",
        "end_operation_then_reevaluate",
        "new_evaluation_required",
        "none",
    }
    assert {m.value for m in SubmissionIntentState} == {
        "planned",
        "registered_unsent",
        "not_sent",
        "submitted",
        "ambiguous",
        "rejected",
        "expired_unsent",
        "cancelled_unsent",
    }
    assert {m.value for m in SendRefusal} == {
        "operation_not_found",
        "operation_not_running",
        "stale_epoch",
        "wrong_executor",
        "lease_lost",
        "intent_not_found",
        "intent_not_open",
        "intent_not_registered",
        "intent_not_sendable",
        "outcome_unresolved",
        "kill_switch_tripped",
        "strategy_disabled",
        "not_active_paper_strategy",
        "execution_window_closed",
        "price_stale",
    }


def test_risk_limit_codes_are_real_risk_decision_codes() -> None:
    real = {c.value for c in RiskDecisionCode}
    assert real >= ops.RISK_LIMIT_PORTFOLIO_CODES
    assert "approved" not in ops.RISK_LIMIT_PORTFOLIO_CODES
    assert ops.risk_limit_failed(RiskDecisionCode.INSUFFICIENT_CASH) == (
        "risk_limit_failed:insufficient_cash"
    )
    with pytest.raises(ValueError):
        ops.risk_limit_failed(RiskDecisionCode.APPROVED)
    with pytest.raises(ValueError):
        ops.risk_limit_failed("made_up")


def _legal_pairs() -> list[tuple[str, str | None]]:
    pairs: list[tuple[str, str | None]] = [("running", None), ("completed", None)]
    pairs += [("paused", m.value) for m in PausedReason]
    pairs += [("requires_reevaluation", m.value) for m in ReevaluationReason]
    pairs += [
        ("requires_reevaluation", ops.risk_limit_failed(c))
        for c in sorted(ops.RISK_LIMIT_PORTFOLIO_CODES)
    ]
    pairs += [("terminated", m.value) for m in TerminatedReason]
    return pairs


@pytest.mark.parametrize(("state", "reason"), _legal_pairs())
def test_next_action_is_total_over_every_legal_pair(state: str, reason: str | None) -> None:
    action = ops.next_action(state, reason)
    assert isinstance(action, NextAction)
    if state in ("running", "completed"):
        assert action is NextAction.NONE
    else:
        assert action is not NextAction.NONE
    ops.validate_state_reason(state, reason)


@pytest.mark.parametrize(
    ("state", "reason"),
    [
        ("running", "awaiting_reconciliation"),
        ("completed", "cancelled_by_operator"),
        ("paused", None),
        ("paused", "evaluation_data_changed"),
        ("requires_reevaluation", "risk_limit_failed:approved"),
        ("requires_reevaluation", None),
        ("terminated", None),
        ("terminated", "price_unavailable"),
    ],
)
def test_next_action_rejects_pairs_the_database_would_reject(
    state: str, reason: str | None
) -> None:
    with pytest.raises(ValueError):
        ops.next_action(state, reason)


def test_specific_next_actions() -> None:
    assert ops.next_action("paused", "price_moved_beyond_tolerance") is (
        NextAction.WAIT_FOR_PRICE_THEN_CONTINUE
    )
    assert ops.next_action("paused", "price_unavailable") is NextAction.WAIT_FOR_PRICE_THEN_CONTINUE
    assert ops.next_action("paused", "not_active_paper_strategy") is (
        NextAction.END_OPERATION_THEN_REEVALUATE
    )
    assert ops.next_action("paused", "working_order_commitments_unaccounted") is (
        NextAction.WAIT_FOR_ORDER_THEN_SYNC_AND_CONTINUE
    )
    assert ops.next_action("requires_reevaluation", "evaluation_data_changed") is (
        NextAction.END_OPERATION_THEN_REEVALUATE
    )
    assert ops.next_action("terminated", "execution_window_elapsed") is (
        NextAction.NEW_EVALUATION_REQUIRED
    )


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_legacy_order_without_attempts_is_never_proven_not_sent() -> None:
    class _Order:
        def __init__(self, status: OrderLifecycleState, broker_order_id: str | None = None):
            self.status = status
            self.broker_order_id = broker_order_id

    pending = _Order(OrderLifecycleState.PENDING_SUBMISSION)
    # Registered under the attempt-log invariant: zero attempts proves not sent.
    assert proven_not_sent(pending, [], attempt_log_registered=True)  # type: ignore[arg-type]
    # LEGACY: no attempt rows and not operation-bound is never proven not sent.
    assert not proven_not_sent(pending, [], attempt_log_registered=False)  # type: ignore[arg-type]
    assert reached_or_may_have_reached_broker(pending, [], attempt_log_registered=False)  # type: ignore[arg-type]
    assert proven_not_sent(None, [], attempt_log_registered=False)
    # Broker evidence beats everything.
    assert not proven_not_sent(
        _Order(OrderLifecycleState.PENDING_SUBMISSION, "b1"),  # type: ignore[arg-type]
        [],
        attempt_log_registered=True,
    )
    for status in (
        OrderLifecycleState.FILLED,
        OrderLifecycleState.REJECTED,
        OrderLifecycleState.UNKNOWN,
    ):
        assert not proven_not_sent(_Order(status), [], attempt_log_registered=True)  # type: ignore[arg-type]
    # SAF-01 B (20.1-17): a registered SUBMISSION_FAILED order with no attempt row failed before
    # T1 committed; nothing can have been sent.
    failed = _Order(OrderLifecycleState.SUBMISSION_FAILED)
    assert proven_not_sent(failed, [], attempt_log_registered=True)  # type: ignore[arg-type]
    # LEGACY (TL-4): a failed submission with no attempt row and no operation is never proven.
    assert not proven_not_sent(failed, [], attempt_log_registered=False)  # type: ignore[arg-type]
    now = datetime(2026, 1, 1, tzinfo=UTC)

    def record(outcome: AttemptOutcomeClass | None) -> AttemptRecord:
        return AttemptRecord(
            attempt_number=1, started_at=now, completed_at=None, outcome_class=outcome
        )

    ok = [record(PRE_CONNECTION), record(AttemptOutcomeClass.DEADLINE_EXPIRED)]
    assert proven_not_sent(pending, ok, attempt_log_registered=False)  # type: ignore[arg-type]
    for bad in (None, AMBIGUOUS, ACCEPTED, AttemptOutcomeClass.REJECTED):
        assert not proven_not_sent(
            pending,  # type: ignore[arg-type]
            [record(PRE_CONNECTION), record(bad)],
            attempt_log_registered=True,
        )


@pytest.mark.parametrize(
    ("persisted", "has_live", "verdict", "expect_state", "expect_end", "expect_takeover"),
    [
        ("paused", False, WindowVerdict.OPEN, "paused", False, False),
        ("paused", False, WindowVerdict.ELAPSED, "terminated", True, False),
        ("paused", False, WindowVerdict.SUPERSEDED, "terminated", True, False),
        ("requires_reevaluation", False, WindowVerdict.ELAPSED, "terminated", True, False),
        ("running", True, WindowVerdict.ELAPSED, "running", False, False),
        ("running", False, WindowVerdict.ELAPSED, "terminated", True, False),
        ("running", False, WindowVerdict.OPEN, "running", False, True),
        ("running", True, WindowVerdict.OPEN, "running", False, False),
        ("terminated", False, WindowVerdict.ELAPSED, "terminated", False, False),
        ("completed", False, WindowVerdict.SUPERSEDED, "completed", False, False),
        ("paused", False, WindowVerdict.UNKNOWN, "paused", False, False),
        ("paused", False, WindowVerdict.NOT_YET_OPEN, "paused", False, False),
    ],
)
def test_compute_effective_state_table(
    persisted: str,
    has_live: bool,
    verdict: WindowVerdict,
    expect_state: str,
    expect_end: bool,
    expect_takeover: bool,
) -> None:
    reasons = {
        "paused": "awaiting_reconciliation",
        "requires_reevaluation": "evaluation_data_changed",
        "terminated": "cancelled_by_operator",
    }
    result = ops.compute_effective_state(
        state=persisted,
        reason=reasons.get(persisted),
        has_live_job=has_live,
        verdict=verdict,
        as_of=IN_WINDOW,
    )
    assert result.state.value == expect_state
    assert result.will_end is expect_end
    assert result.takeover_pending is expect_takeover
    assert isinstance(result.next_action, NextAction)
    if expect_takeover:
        assert result.next_action is NextAction.SYNC_RECONCILE_THEN_CONTINUE
        assert result.state is OperationState.RUNNING


# ---------------------------------------------------------------------------
# effective_state: clock cases, zero writes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("instant", "verdict"),
    [
        (et(2025, 12, 2, 18, 0), WindowVerdict.NOT_YET_OPEN),  # after S's close
        (BEFORE_OPEN, WindowVerdict.NOT_YET_OPEN),
        (IN_WINDOW, WindowVerdict.OPEN),
        (et(2025, 12, 3, 15, 44), WindowVerdict.OPEN),
        (PAST_CUTOFF, WindowVerdict.ELAPSED),
        (et(2025, 12, 3, 17, 0), WindowVerdict.ELAPSED),  # after the close, same trading day
        (NEXT_DAY, WindowVerdict.SUPERSEDED),
        (et(2025, 12, 6, 12, 0), WindowVerdict.SUPERSEDED),  # the weekend after
    ],
)
def test_window_verdict_clock_cases(ops_db: str, instant: datetime, verdict: WindowVerdict) -> None:
    from trading_platform.services.calendar_facts import load_calendar_window

    settings = load_settings()
    with session_scope(settings) as session:
        window = load_calendar_window(session, now=instant, settings=settings)
        assert ops.window_verdict(window, settings, S) is verdict


def test_window_verdict_unknown_when_the_calendar_is_missing(ops_db: str) -> None:
    from trading_platform.services.calendar_facts import load_calendar_window

    settings = load_settings()
    far = datetime(2031, 6, 3, 15, 0, tzinfo=UTC)
    with session_scope(settings) as session:
        window = load_calendar_window(session, now=far, settings=settings)
        assert ops.window_verdict(window, settings, S) is WindowVerdict.UNKNOWN


def test_effective_state_performs_zero_writes_and_reports_will_end(ops_db: str) -> None:
    with session_scope(load_settings()) as session:
        operation = seed_operation(
            session, state="paused", reason="awaiting_reconciliation", session_date=S
        )
        seed_operation_intent(session, operation)
        operation_id = operation.id
    engine = get_engine(load_settings())
    with session_scope(load_settings()) as session:
        before = session.execute(
            text("SELECT state, reason, execution_epoch, updated_at FROM execution_operations")
        ).all()
        operation = session.get(ExecutionOperation, operation_id)
        assert operation is not None
        with count_queries(engine) as counter:
            result = ops.effective_state(session, operation, now=PAST_CUTOFF)
        assert result.will_end is True
        assert result.state is OperationState.TERMINATED
        assert result.reason == "execution_window_elapsed"
        assert result.next_action is NextAction.NEW_EVALUATION_REQUIRED
        writes = [
            s
            for s in counter.statements
            if re.match(r"\s*(INSERT|UPDATE|DELETE)", s, re.IGNORECASE)
        ]
        assert writes == []
    with session_scope(load_settings()) as session:
        after = session.execute(
            text("SELECT state, reason, execution_epoch, updated_at FROM execution_operations")
        ).all()
    assert before == after


def test_crash_left_running_operation_reports_takeover_pending(
    ops_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    with session_scope(load_settings()) as session:
        job = seed_operation_job(session, status=JobStatus.FAILED)
        operation = seed_operation(
            session, state="running", reason=None, session_date=S, jobs=[(job, "start")]
        )
        operation_id = operation.id
    with session_scope(load_settings()) as session:
        operation = session.get(ExecutionOperation, operation_id)
        assert operation is not None
        result = ops.effective_state(session, operation, now=IN_WINDOW)
        assert result.takeover_pending is True
        assert result.state is OperationState.RUNNING
        assert result.next_action is NextAction.SYNC_RECONCILE_THEN_CONTINUE
    # touch never persists the normalization: the row is unchanged.
    with session_scope(load_settings()) as session:
        touched = ops.touch_operation(session, operation_id, now=IN_WINDOW)
        assert touched.changed is False
    with session_scope(load_settings()) as session:
        stored = session.get(ExecutionOperation, operation_id)
        assert stored is not None and (stored.state, stored.reason) == ("running", None)


def test_running_operation_with_a_live_job_stays_running(ops_db: str) -> None:
    with session_scope(load_settings()) as session:
        job = seed_operation_job(session, status=JobStatus.RUNNING)
        operation = seed_operation(
            session, state="running", reason=None, session_date=S, jobs=[(job, "start")]
        )
        operation_id = operation.id
    with session_scope(load_settings()) as session:
        operation = session.get(ExecutionOperation, operation_id)
        assert operation is not None
        result = ops.effective_state(session, operation, now=IN_WINDOW)
        assert result.takeover_pending is False and result.state is OperationState.RUNNING
        # A live executor is never pulled from under: no expiry past the cutoff either.
        past = ops.effective_state(session, operation, now=PAST_CUTOFF)
        assert past.will_end is False
        assert ops.touch_operation(session, operation_id, now=PAST_CUTOFF).changed is False


def test_live_jobs_include_payload_matched_unlinked_continue_jobs(ops_db: str) -> None:
    with session_scope(load_settings()) as session:
        operation = seed_operation(
            session, state="paused", reason="awaiting_reconciliation", session_date=S
        )
        queued = seed_operation_job(
            session,
            status=JobStatus.QUEUED,
            payload={"mode": "continue", "operation_id": str(operation.id)},
        )
        seed_operation_job(
            session,
            status=JobStatus.SUCCEEDED,
            payload={"mode": "continue", "operation_id": str(operation.id)},
        )
        seed_operation_job(
            session, status=JobStatus.RUNNING, payload={"operation_id": str(uuid.uuid4())}
        )
        live = ops.live_job_ids(session, [operation.id])
        assert live[operation.id] == [queued.id]


# ---------------------------------------------------------------------------
# create_operation, transition, begin_continuation
# ---------------------------------------------------------------------------


def _planned(session: Any, ticker: str = "AAPL", side: str = "buy") -> ops.PlannedIntent:
    from tests.support.operation_fixtures import seed_symbol

    symbol = seed_symbol(session, ticker)
    return ops.PlannedIntent(
        symbol_id=symbol.id,
        side=side,
        quantity=__import__("decimal").Decimal("5"),
        client_order_id=f"tp-{uuid.uuid4().hex[:20]}",
        decision_fingerprint="a" * 64,
        reference_price=__import__("decimal").Decimal("100"),
    )


def test_create_operation_pins_the_risk_run_and_plans_intents_in_order(ops_db: str) -> None:
    from tests.support.operation_fixtures import seed_risk_run

    with session_scope(load_settings()) as session:
        strategy = strategy_row(session)
        risk_run = seed_risk_run(session)
        job = seed_operation_job(session, status=JobStatus.RUNNING)
        operation = ops.create_operation(
            session,
            strategy_pk=strategy.id,
            as_of_session=S,
            risk_run_id=risk_run.id,
            intents=[_planned(session, "AAPL"), _planned(session, "MSFT", "sell")],
            job_id=job.id,
        )
        assert operation.state == "running" and operation.reason is None
        assert operation.executor_job_id == job.id and operation.execution_epoch == 0
        rows = (
            session.execute(
                select(ExecutionOperationIntent)
                .where(ExecutionOperationIntent.operation_id == operation.id)
                .order_by(ExecutionOperationIntent.sequence)
            )
            .scalars()
            .all()
        )
        assert [r.sequence for r in rows] == [1, 2]
        assert {r.disposition for r in rows} == {"open"}
        facts = ops.load_intent_facts(session, [operation.id])[operation.id]
        assert [f.state for f in facts] == [SubmissionIntentState.PLANNED] * 2
        with pytest.raises(ValueError):
            ops.create_operation(
                session,
                strategy_pk=strategy.id,
                as_of_session=S,
                risk_run_id=risk_run.id,
                intents=[],
                job_id=None,
            )


def test_create_operation_raises_typed_errors_from_the_database_constraints(ops_db: str) -> None:
    from tests.support.operation_fixtures import seed_risk_run

    with session_scope(load_settings()) as session:
        strategy = strategy_row(session)
        first_run = seed_risk_run(session)
        second_run = seed_risk_run(session, session_date=date(2025, 12, 1))
        first = ops.create_operation(
            session,
            strategy_pk=strategy.id,
            as_of_session=S,
            risk_run_id=first_run.id,
            intents=[_planned(session)],
            job_id=None,
        )
        with pytest.raises(OperationOpenError) as open_exc:
            ops.create_operation(
                session,
                strategy_pk=strategy.id,
                as_of_session=date(2025, 12, 1),
                risk_run_id=second_run.id,
                intents=[_planned(session)],
                job_id=None,
            )
        assert open_exc.value.operation_id == first.id
        ops.transition(session, first.id, OperationState.TERMINATED, "cancelled_by_operator")
        # Terminated: a new open operation is fine, but the same risk run is already operated.
        with pytest.raises(RiskRunAlreadyOperatedError):
            ops.create_operation(
                session,
                strategy_pk=strategy.id,
                as_of_session=S,
                risk_run_id=first_run.id,
                intents=[_planned(session)],
                job_id=None,
            )
        ops.create_operation(
            session,
            strategy_pk=strategy.id,
            as_of_session=date(2025, 12, 1),
            risk_run_id=second_run.id,
            intents=[_planned(session)],
            job_id=None,
        )


_ALL_STATES = [s.value for s in OperationState]
_STATE_REASON = {
    "running": None,
    "completed": None,
    "paused": "awaiting_reconciliation",
    "requires_reevaluation": "evaluation_data_changed",
    "terminated": "cancelled_by_operator",
}


@pytest.mark.parametrize("to_state", _ALL_STATES)
@pytest.mark.parametrize("from_state", _ALL_STATES)
def test_transition_table_every_legal_and_illegal_pair(
    ops_db: str, from_state: str, to_state: str
) -> None:
    legal = {
        "running": {"paused", "requires_reevaluation", "terminated", "completed"},
        "paused": {"paused", "requires_reevaluation", "terminated"},
        "requires_reevaluation": {"terminated"},
        "terminated": set(),
        "completed": set(),
    }
    with session_scope(load_settings()) as session:
        operation = seed_operation(
            session, state=from_state, reason=_STATE_REASON[from_state], session_date=S
        )
        operation_id = operation.id
    with session_scope(load_settings()) as session:
        if to_state in legal[from_state]:
            moved = ops.transition(
                session, operation_id, OperationState(to_state), _STATE_REASON[to_state]
            )
            assert moved.state == to_state
            assert moved.reason == _STATE_REASON[to_state]
        else:
            with pytest.raises(ops.IllegalOperationTransition):
                ops.transition(
                    session, operation_id, OperationState(to_state), _STATE_REASON[to_state]
                )


def test_transition_to_a_final_state_bumps_the_epoch_and_clears_the_executor(ops_db: str) -> None:
    with session_scope(load_settings()) as session:
        job = seed_operation_job(session, status=JobStatus.RUNNING)
        operation = seed_operation(
            session, state="running", reason=None, session_date=S, epoch=4, executor_job=job
        )
        operation_id, job_id = operation.id, job.id
    with session_scope(load_settings()) as session:
        done = ops.transition(
            session, operation_id, OperationState.COMPLETED, fence=Fence(operation_id, 4, job_id)
        )
        assert done.execution_epoch == 5 and done.executor_job_id is None


def test_transition_rejects_a_reason_the_state_does_not_allow(ops_db: str) -> None:
    with session_scope(load_settings()) as session:
        operation = seed_operation(session, state="running", reason=None, session_date=S)
        with pytest.raises(ValueError):
            ops.transition(session, operation.id, OperationState.PAUSED, "cancelled_by_operator")


def test_transition_with_a_stale_fence_loses(ops_db: str) -> None:
    with session_scope(load_settings()) as session:
        job = seed_operation_job(session, status=JobStatus.RUNNING)
        operation = seed_operation(
            session, state="running", reason=None, session_date=S, epoch=2, executor_job=job
        )
        operation_id, job_id = operation.id, job.id
    with session_scope(load_settings()) as session:
        with pytest.raises(OperationConflictError):
            ops.transition(
                session,
                operation_id,
                OperationState.PAUSED,
                "kill_switch_tripped",
                fence=Fence(operation_id, 1, job_id),
            )
        paused = ops.transition(
            session,
            operation_id,
            OperationState.PAUSED,
            "kill_switch_tripped",
            fence=Fence(operation_id, 2, job_id),
        )
        assert paused.reason == "kill_switch_tripped"


def test_begin_continuation_cas_loser_raises_and_links_the_job(ops_db: str) -> None:
    with session_scope(load_settings()) as session:
        operation = seed_operation(
            session, state="paused", reason="awaiting_reconciliation", session_date=S
        )
        winner = seed_operation_job(session, status=JobStatus.RUNNING)
        loser = seed_operation_job(session, status=JobStatus.RUNNING)
        operation_id, winner_id, loser_id = operation.id, winner.id, loser.id
    with session_scope(load_settings()) as session:
        running = ops.begin_continuation(session, operation_id, winner_id)
        assert running.state == "running" and running.reason is None
    with session_scope(load_settings()) as session:
        with pytest.raises(OperationConflictError):
            ops.begin_continuation(session, operation_id, loser_id)
    with session_scope(load_settings()) as session:
        links = session.execute(
            text("SELECT job_id, mode FROM execution_operation_jobs WHERE operation_id = :o"),
            {"o": operation_id},
        ).all()
        assert [(r.job_id, r.mode) for r in links] == [(winner_id, "continue")]


# ---------------------------------------------------------------------------
# Intent states, lazy expiry (D-21)
# ---------------------------------------------------------------------------


def _mixed_operation(
    session: Any, *, state: str = "paused", reason: str | None = "awaiting_reconciliation"
):
    operation = seed_operation(session, state=state, reason=reason, session_date=S)
    planned = seed_operation_intent(session, operation, sequence=1, ticker="AAPL")
    registered = seed_operation_intent(
        session,
        operation,
        sequence=2,
        ticker="MSFT",
        order_status=OrderLifecycleState.PENDING_SUBMISSION,
    )
    not_sent = seed_operation_intent(
        session, operation, sequence=3, ticker="NVDA", attempts=[PRE_CONNECTION, PRE_CONNECTION]
    )
    submitted = seed_operation_intent(
        session,
        operation,
        sequence=4,
        ticker="TSLA",
        order_status=OrderLifecycleState.SUBMITTED,
        attempts=[ACCEPTED],
        broker_order_id="broker-1",
    )
    ambiguous = seed_operation_intent(
        session,
        operation,
        sequence=5,
        ticker="AMD",
        order_status=OrderLifecycleState.UNKNOWN,
        attempts=[AMBIGUOUS],
    )
    return operation, [planned, registered, not_sent, submitted, ambiguous]


def test_intent_states_cover_planned_through_ambiguous(ops_db: str) -> None:
    with session_scope(load_settings()) as session:
        operation, _seeded = _mixed_operation(session)
        operation_id = operation.id
    with session_scope(load_settings()) as session:
        facts = ops.load_intent_facts(session, [operation_id])[operation_id]
        assert [f.state for f in facts] == [
            SubmissionIntentState.PLANNED,
            SubmissionIntentState.REGISTERED_UNSENT,
            SubmissionIntentState.NOT_SENT,
            SubmissionIntentState.SUBMITTED,
            SubmissionIntentState.AMBIGUOUS,
        ]
        assert [f.proven_not_sent for f in facts] == [True, True, True, False, False]


def test_legacy_order_linked_to_an_intent_is_not_proven_not_sent(ops_db: str) -> None:
    with session_scope(load_settings()) as session:
        operation = seed_operation(
            session, state="paused", reason="awaiting_reconciliation", session_date=S
        )
        seeded = seed_operation_intent(
            session, operation, order_status=OrderLifecycleState.PENDING_SUBMISSION
        )
        assert seeded.order is not None
        seeded.order.created_at = seeded.row.created_at - timedelta(days=30)
        session.flush()
        operation_id = operation.id
    with session_scope(load_settings()) as session:
        (fact,) = ops.load_intent_facts(session, [operation_id])[operation_id]
        assert fact.attempt_log_registered is False
        assert fact.proven_not_sent is False
        # ... so neither End nor expiry may turn it into an unsent disposition.
        result = ops.end_operation(session, operation_id, operator_reason="done", actor="op")
        assert result.unsent_cancelled == ()


def test_expiry_terminates_unsent_only(ops_db: str) -> None:
    with session_scope(load_settings()) as session:
        operation, _seeded = _mixed_operation(session)
        operation_id = operation.id
    with session_scope(load_settings()) as session:
        result = ops.touch_operation(session, operation_id, now=PAST_CUTOFF)
        assert result.changed and result.reason == "execution_window_elapsed"
        assert len(result.expired_intent_ids) == 3
    with session_scope(load_settings()) as session:
        stored = session.get(ExecutionOperation, operation_id)
        assert stored is not None
        assert (stored.state, stored.reason) == ("terminated", "execution_window_elapsed")
        assert stored.execution_epoch == 1 and stored.executor_job_id is None
        facts = ops.load_intent_facts(session, [operation_id])[operation_id]
        assert [f.state for f in facts] == [
            SubmissionIntentState.EXPIRED_UNSENT,
            SubmissionIntentState.EXPIRED_UNSENT,
            SubmissionIntentState.EXPIRED_UNSENT,
            SubmissionIntentState.SUBMITTED,
            SubmissionIntentState.AMBIGUOUS,
        ]
        assert [f.row.disposition for f in facts][3:] == ["open", "open"]
        # Idempotent.
        assert ops.touch_operation(session, operation_id, now=PAST_CUTOFF).changed is False


def test_touch_operation_supersession_and_open_window(ops_db: str) -> None:
    with session_scope(load_settings()) as session:
        operation, _ = _mixed_operation(session)
        operation_id = operation.id
    with session_scope(load_settings()) as session:
        assert ops.touch_operation(session, operation_id, now=IN_WINDOW).changed is False
        assert ops.touch_operation(session, operation_id, now=BEFORE_OPEN).changed is False
        far = datetime(2031, 6, 3, 15, 0, tzinfo=UTC)
        assert ops.touch_operation(session, operation_id, now=far).changed is False  # unknown
        result = ops.touch_operation(session, operation_id, now=NEXT_DAY)
        assert result.reason == "evaluation_superseded"


def test_requires_reevaluation_frozen_intents_expire_too(ops_db: str) -> None:
    with session_scope(load_settings()) as session:
        operation, _ = _mixed_operation(
            session, state="requires_reevaluation", reason="risk_limit_failed:insufficient_cash"
        )
        operation_id = operation.id
    with session_scope(load_settings()) as session:
        result = ops.touch_operation(session, operation_id, now=PAST_CUTOFF)
        assert result.changed and len(result.expired_intent_ids) == 3


def test_expiry_terminates_unsent_only_and_never_touches_evidence(ops_db: str) -> None:
    with session_scope(load_settings()) as session:
        operation, _ = _mixed_operation(session)
        operation_id = operation.id
    counts_before = _evidence_counts()
    with session_scope(load_settings()) as session:
        ops.touch_operation(session, operation_id, now=PAST_CUTOFF)
    assert _evidence_counts() == counts_before


def _evidence_counts() -> tuple[int, int, int]:
    with session_scope(load_settings()) as session:
        return (
            session.execute(select(func.count()).select_from(RecoveryRecord)).scalar_one(),
            session.execute(select(func.count()).select_from(OrderSubmissionAttempt)).scalar_one(),
            session.execute(select(func.count()).select_from(PaperOrder)).scalar_one(),
        )


# ---------------------------------------------------------------------------
# End (D-20)
# ---------------------------------------------------------------------------


def test_end_operation_refused_while_job_running(ops_db: str) -> None:
    with session_scope(load_settings()) as session:
        job = seed_operation_job(session, status=JobStatus.RUNNING)
        operation = seed_operation(
            session, state="running", reason=None, session_date=S, jobs=[(job, "start")]
        )
        operation_id, job_id = operation.id, job.id
    with session_scope(load_settings()) as session:
        with pytest.raises(OperationRunningError) as exc:
            ops.end_operation(session, operation_id, operator_reason="stop", actor="op")
        assert exc.value.running_job_ids == (job_id,)


def test_end_refused_for_a_queued_continue_job_linked_only_by_payload(ops_db: str) -> None:
    with session_scope(load_settings()) as session:
        operation = seed_operation(
            session, state="paused", reason="awaiting_reconciliation", session_date=S
        )
        queued = seed_operation_job(
            session,
            status=JobStatus.QUEUED,
            payload={"mode": "continue", "operation_id": str(operation.id)},
        )
        operation_id, job_id = operation.id, queued.id
    with session_scope(load_settings()) as session:
        with pytest.raises(OperationRunningError) as exc:
            ops.end_operation(session, operation_id, operator_reason="stop", actor="op")
        assert exc.value.running_job_ids == (job_id,)


def test_end_operation_completed_is_not_open(ops_db: str) -> None:
    with session_scope(load_settings()) as session:
        operation = seed_operation(session, state="completed", reason=None, session_date=S)
        operation_id = operation.id
    with session_scope(load_settings()) as session:
        with pytest.raises(OperationNotOpenError):
            ops.end_operation(session, operation_id, operator_reason="x", actor="op")


def test_end_operation_cancels_unsent_only_and_is_idempotent(
    ops_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _now(monkeypatch, IN_WINDOW)
    with session_scope(load_settings()) as session:
        operation, _ = _mixed_operation(session)
        operation_id = operation.id
    counts_before = _evidence_counts()
    with session_scope(load_settings()) as session:
        first = ops.end_operation(session, operation_id, operator_reason="stop", actor="op")
    assert first.changed and first.state is OperationState.TERMINATED
    assert first.reason == "cancelled_by_operator" and not first.ended_by_expiry
    assert len(first.unsent_cancelled) == 3
    with session_scope(load_settings()) as session:
        facts = ops.load_intent_facts(session, [operation_id])[operation_id]
        assert [f.state for f in facts] == [
            SubmissionIntentState.CANCELLED_UNSENT,
            SubmissionIntentState.CANCELLED_UNSENT,
            SubmissionIntentState.CANCELLED_UNSENT,
            SubmissionIntentState.SUBMITTED,
            SubmissionIntentState.AMBIGUOUS,
        ]
        stored = session.get(ExecutionOperation, operation_id)
        assert stored is not None
        assert stored.ended_by == "op" and stored.end_reason == "stop"
        assert stored.execution_epoch == 1 and stored.executor_job_id is None
    with session_scope(load_settings()) as session:
        second = ops.end_operation(session, operation_id, operator_reason="again", actor="op")
    assert second.changed is False and second.reason == "cancelled_by_operator"
    assert second.unsent_cancelled == ()
    assert _evidence_counts() == counts_before


def test_end_after_the_window_elapsed_reports_expiry(
    ops_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    with session_scope(load_settings()) as session:
        operation, _ = _mixed_operation(session)
        operation_id = operation.id
    with session_scope(load_settings()) as session:
        result = ops.end_operation(
            session, operation_id, operator_reason="late", actor="op", now=PAST_CUTOFF
        )
    assert result.changed and result.ended_by_expiry
    assert result.reason == "execution_window_elapsed"
    assert result.unsent_cancelled == ()


class _CancelSpyBroker:
    """A broker stand-in that records any cancel call. End must never reach it."""

    def __init__(self) -> None:
        self.cancel_calls = 0

    def cancel_order(self, *_a: object, **_k: object) -> None:  # pragma: no cover
        self.cancel_calls += 1


def test_end_leaves_working_order_and_ambiguous_intent_blocking_with_zero_cancel_calls(
    ops_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trading_platform.services import alpaca

    broker = _CancelSpyBroker()

    def _no_client(*_a: object, **_k: object) -> None:
        raise AssertionError("End must not construct a broker client")

    monkeypatch.setattr(alpaca.AlpacaClient, "__init__", _no_client)
    with session_scope(load_settings()) as session:
        operation = seed_operation(
            session, state="paused", reason="awaiting_reconciliation", session_date=S
        )
        seed_operation_intent(
            session,
            operation,
            sequence=1,
            ticker="TSLA",
            order_status=OrderLifecycleState.SUBMITTED,
            attempts=[ACCEPTED],
            broker_order_id="broker-working",
        )
        seed_operation_intent(
            session,
            operation,
            sequence=2,
            ticker="AMD",
            order_status=OrderLifecycleState.UNKNOWN,
            attempts=[AMBIGUOUS],
        )
        seed_operation_intent(session, operation, sequence=3, ticker="NVDA")
        operation_id = operation.id
    counts_before = _evidence_counts()
    with session_scope(load_settings()) as session:
        result = ops.end_operation(
            session, operation_id, operator_reason="stop", actor="op", now=IN_WINDOW
        )
    assert result.state is OperationState.TERMINATED
    assert [w["blocking_effect"] for w in result.working_orders] == [
        "working_order_commitments_unaccounted"
    ]
    assert [u["blocking_effect"] for u in result.unresolved_intents] == ["outcome_unresolved"]
    assert len(result.unsent_cancelled) == 1
    # The ambiguous intent still blocks the strategy through the recovery predicate.
    with session_scope(load_settings()) as session:
        status = recovery.strategy_recovery_status(session, OWNER, now=IN_WINDOW)
        assert status.gate_code is recovery.GateCode.OUTCOME_UNRESOLVED
        working = ops.is_working_order(
            session.execute(
                select(PaperOrder).where(PaperOrder.broker_order_id == "broker-working")
            ).scalar_one()
        )
        assert working is True
    assert broker.cancel_calls == 0
    assert _evidence_counts() == counts_before


def test_operations_module_has_no_broker_client_or_cancel_call() -> None:
    source = Path(inspect.getfile(ops)).read_text()
    code = "\n".join(
        line for line in source.splitlines() if not line.strip().startswith(("#", '"""'))
    )
    assert "alpaca" not in code.lower()
    assert "broker_execution" not in code
    assert "cancel_order" not in code
    assert not re.search(r"\.cancel\w*\(", code)
    assert not re.search(r"^\s*(from|import)\s+.*(alpaca|broker_execution)", source, re.MULTILINE)


# ---------------------------------------------------------------------------
# S1-R3 primitives: authorize_send (T1)
# ---------------------------------------------------------------------------


def _t1_setup(
    *,
    attempts: list[AttemptOutcomeClass | None] | None = None,
    order_status: OrderLifecycleState = OrderLifecycleState.PENDING_SUBMISSION,
    lease_owner: str = "worker-1",
    lease_expires_at: datetime = LIVE_UNTIL,
    job_status: JobStatus = JobStatus.RUNNING,
    state: str = "running",
    epoch: int = 3,
    with_order: bool = True,
    disposition: str = "open",
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """A running operation at ``epoch`` held by one live Job with one registered intent."""

    # 20.1-20: T1 re-reads owner / enabled / kill switch / window; arrange them, and put the
    # application clock inside the window unless the test already moved it.
    if _OPS_MONKEYPATCH and clock.now_utc is _REAL_NOW:
        _OPS_MONKEYPATCH[0].setattr(clock, "now_utc", lambda: IN_WINDOW)
    with session_scope(load_settings()) as session:
        arrange_sendable_gate(session, now=IN_WINDOW)
        job = seed_operation_job(
            session,
            status=job_status,
            lease_owner=lease_owner,
            lease_expires_at=lease_expires_at,
        )
        operation = seed_operation(
            session,
            state=state,
            reason={"running": None, "paused": "awaiting_reconciliation"}.get(
                state, "cancelled_by_operator"
            ),
            session_date=S,
            epoch=epoch,
            executor_job=job,
            jobs=[(job, "start")],
        )
        seeded = seed_operation_intent(
            session,
            operation,
            order_status=order_status if with_order else None,
            attempts=attempts or (),
            with_order=with_order,
            disposition=disposition,
        )
        return operation.id, seeded.row.id, job.id


def _authorize(
    operation_id: uuid.UUID,
    intent_id: uuid.UUID,
    job_id: uuid.UUID,
    *,
    epoch: int = 3,
    lease_owner: str = "worker-1",
) -> ops.SendAuthorization:
    return ops.authorize_send(operation_id, intent_id, epoch, job_id, lease_owner=lease_owner)


def test_authorize_send_commits_the_begin_attempt_row_before_returning(ops_db: str) -> None:
    operation_id, intent_id, job_id = _t1_setup()
    auth = _authorize(operation_id, intent_id, job_id)
    assert auth.attempt_number == 1 and auth.execution_epoch == 3
    assert auth.local_deadline > datetime.now(UTC)
    # Visible from ANOTHER connection: the row was committed before the call returned.
    with session_scope(load_settings()) as session:
        row = session.execute(select(OrderSubmissionAttempt)).scalar_one()
        assert row.id == auth.attempt_id
        assert row.outcome_class is None and row.completed_at is None
        assert row.execution_epoch == 3 and row.executor_job_id == job_id
        assert row.authorization_deadline is not None
        assert row.authorization_deadline > row.started_at
        stored = session.get(ExecutionOperation, operation_id)
        assert stored is not None and stored.last_guarded_at is not None


@pytest.mark.parametrize(
    ("scenario", "refusal"),
    [
        ("stale_epoch", SendRefusal.STALE_EPOCH),
        ("wrong_executor", SendRefusal.WRONG_EXECUTOR),
        ("paused", SendRefusal.OPERATION_NOT_RUNNING),
        ("terminated", SendRefusal.OPERATION_NOT_RUNNING),
        ("lease_lost_owner", SendRefusal.LEASE_LOST),
        ("lease_expired", SendRefusal.LEASE_LOST),
        ("job_not_running", SendRefusal.LEASE_LOST),
        ("expired_disposition", SendRefusal.INTENT_NOT_OPEN),
        ("not_registered", SendRefusal.INTENT_NOT_REGISTERED),
        ("unknown_intent", SendRefusal.INTENT_NOT_FOUND),
        ("unknown_operation", SendRefusal.OPERATION_NOT_FOUND),
    ],
)
def test_authorize_send_refuses_and_sends_nothing(
    ops_db: str, scenario: str, refusal: SendRefusal
) -> None:
    kwargs: dict[str, Any] = {}
    call: dict[str, Any] = {}
    if scenario == "paused":
        kwargs["state"] = "paused"
    elif scenario == "terminated":
        kwargs["state"] = "terminated"
    elif scenario == "lease_expired":
        kwargs["lease_expires_at"] = datetime(2000, 1, 1, tzinfo=UTC)
    elif scenario == "lease_lost_owner":
        call["lease_owner"] = "worker-2"
    elif scenario == "job_not_running":
        kwargs["job_status"] = JobStatus.FAILED
    elif scenario == "expired_disposition":
        kwargs["disposition"] = "expired_unsent"
    elif scenario == "not_registered":
        kwargs["with_order"] = False
    operation_id, intent_id, job_id = _t1_setup(**kwargs)
    if scenario == "terminated":
        # seed_operation(reason) for terminated needs a legal reason.
        pass
    if scenario == "stale_epoch":
        call["epoch"] = 2
    elif scenario == "wrong_executor":
        job_id = uuid.uuid4()
    elif scenario == "unknown_intent":
        intent_id = uuid.uuid4()
    elif scenario == "unknown_operation":
        operation_id = uuid.uuid4()
    with pytest.raises(SendRefusedError) as exc:
        _authorize(operation_id, intent_id, job_id, **call)
    assert exc.value.refusal is refusal
    with session_scope(load_settings()) as session:
        assert (
            session.execute(select(func.count()).select_from(OrderSubmissionAttempt)).scalar_one()
            == 0
        )


@pytest.mark.parametrize(
    ("attempts", "status"),
    [
        ([None], OrderLifecycleState.PENDING_SUBMISSION),  # an attempt with an EMPTY outcome
        ([AMBIGUOUS], OrderLifecycleState.PENDING_SUBMISSION),  # a recorded read timeout
        ([PRE_CONNECTION, AMBIGUOUS], OrderLifecycleState.PENDING_SUBMISSION),
        ([ACCEPTED], OrderLifecycleState.PENDING_SUBMISSION),
        ([AttemptOutcomeClass.REJECTED], OrderLifecycleState.PENDING_SUBMISSION),
        ([AttemptOutcomeClass.DUPLICATE_REPORTED], OrderLifecycleState.PENDING_SUBMISSION),
        ([], OrderLifecycleState.UNKNOWN),
        ([PRE_CONNECTION], OrderLifecycleState.SUBMITTED),
        ([PRE_CONNECTION], OrderLifecycleState.FILLED),
    ],
)
def test_authorize_send_refuses_an_intent_whose_broker_outcome_is_uncertain(
    ops_db: str, attempts: list[AttemptOutcomeClass | None], status: OrderLifecycleState
) -> None:
    operation_id, intent_id, job_id = _t1_setup(attempts=attempts, order_status=status)
    with pytest.raises(SendRefusedError) as exc:
        _authorize(operation_id, intent_id, job_id)
    assert exc.value.refusal in (SendRefusal.INTENT_NOT_SENDABLE, SendRefusal.OUTCOME_UNRESOLVED)
    with session_scope(load_settings()) as session:
        # Nothing new was written: the pre-existing attempt rows are all that exist.
        assert session.execute(
            select(func.count()).select_from(OrderSubmissionAttempt)
        ).scalar_one() == len(attempts)


def test_authorize_send_allows_a_retry_after_established_not_sent_attempts(ops_db: str) -> None:
    operation_id, intent_id, job_id = _t1_setup(
        attempts=[PRE_CONNECTION, AttemptOutcomeClass.DEADLINE_EXPIRED],
        order_status=OrderLifecycleState.SUBMISSION_FAILED,
    )
    auth = _authorize(operation_id, intent_id, job_id)
    assert auth.attempt_number == 3  # every HTTP attempt needs its own T1


def test_authorize_send_refuses_a_legacy_order_without_attempts(ops_db: str) -> None:
    operation_id, intent_id, job_id = _t1_setup()
    with session_scope(load_settings()) as session:
        intent = session.get(ExecutionOperationIntent, intent_id)
        assert intent is not None and intent.paper_order_id is not None
        order = session.get(PaperOrder, intent.paper_order_id)
        assert order is not None
        order.created_at = intent.created_at - timedelta(days=30)
    with pytest.raises(SendRefusedError) as exc:
        _authorize(operation_id, intent_id, job_id)
    assert exc.value.refusal is SendRefusal.INTENT_NOT_SENDABLE


def test_authorize_send_refuses_while_another_intent_of_the_strategy_is_unresolved(
    ops_db: str,
) -> None:
    operation_id, intent_id, job_id = _t1_setup()
    with session_scope(load_settings()) as session:
        strategy_row(session)
        run = seed_paper_run(session, seed_job(session))
        from tests.support.recovery_fixtures import seed_intent

        seed_intent(
            session, run, status=OrderLifecycleState.UNKNOWN, attempts=[AMBIGUOUS], ticker="ZZZZ"
        )
    with pytest.raises(SendRefusedError) as exc:
        _authorize(operation_id, intent_id, job_id)
    assert exc.value.refusal is SendRefusal.OUTCOME_UNRESOLVED


def test_two_t1s_for_one_operation_serialize_on_the_operation_row(ops_db: str) -> None:
    operation_id, intent_id, job_id = _t1_setup()
    barrier = threading.Barrier(2)

    def attempt() -> str:
        barrier.wait()
        try:
            _authorize(operation_id, intent_id, job_id)
        except SendRefusedError as exc:
            return exc.refusal.value
        return "authorized"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = sorted(pool.map(lambda _i: attempt(), range(2)))
    # The second T1 sees the first's committed attempt row (outcome NULL) and refuses.
    assert results == ["authorized", "intent_not_sendable"]
    with session_scope(load_settings()) as session:
        assert (
            session.execute(select(func.count()).select_from(OrderSubmissionAttempt)).scalar_one()
            == 1
        )


def test_t1_blocks_on_the_operation_row_lock(ops_db: str) -> None:
    operation_id, intent_id, job_id = _t1_setup()
    holder_ready = threading.Event()
    release = threading.Event()
    done = threading.Event()

    def hold() -> None:
        with session_scope(load_settings()) as session:
            session.execute(
                select(ExecutionOperation.id)
                .where(ExecutionOperation.id == operation_id)
                .with_for_update()
            ).all()
            holder_ready.set()
            release.wait(10)

    def run_t1() -> None:
        _authorize(operation_id, intent_id, job_id)
        done.set()

    holder = threading.Thread(target=hold)
    holder.start()
    assert holder_ready.wait(10)
    worker = threading.Thread(target=run_t1)
    worker.start()
    assert not done.wait(0.5), "T1 must wait for the operation row lock"
    release.set()
    holder.join(10)
    worker.join(10)
    assert done.is_set()


# ---------------------------------------------------------------------------
# S1-R3 primitives: acquire_execution (takeover)
# ---------------------------------------------------------------------------


def test_acquire_execution_requires_the_advisory_lock(ops_db: str) -> None:
    operation_id, _intent_id, job_id = _t1_setup()
    fake = RunLock(strategy_id=OWNER, session_date=S, key=123456789, backend_pid=1)
    with pytest.raises(RunLockNotHeldError):
        ops.acquire_execution(operation_id, job_id, fake)
    # A held lock for ANOTHER strategy does not authorize this operation either.
    with session_scope(load_settings()) as session:
        strategy_row(session, OTHER)
    with session_run_lock(strategy_id=OTHER, session_date=S) as lock:
        with pytest.raises(RunLockNotHeldError):
            ops.acquire_execution(operation_id, job_id, lock)


def test_acquire_execution_commits_the_epoch_cas_then_marks_in_doubt_intents(
    ops_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    with session_scope(load_settings()) as session:
        old_job = seed_operation_job(session, status=JobStatus.FAILED)
        new_job = seed_operation_job(session, status=JobStatus.RUNNING)
        operation = seed_operation(
            session,
            state="running",
            reason=None,
            session_date=S,
            epoch=2,
            executor_job=old_job,
            jobs=[(old_job, "start"), (new_job, "continue")],
        )
        in_doubt_null = seed_operation_intent(
            session,
            operation,
            sequence=1,
            ticker="AAA",
            attempts=[None],
            order_status=OrderLifecycleState.PENDING_SUBMISSION,
        )
        in_doubt_timeout = seed_operation_intent(
            session,
            operation,
            sequence=2,
            ticker="BBB",
            attempts=[AMBIGUOUS],
            order_status=OrderLifecycleState.PENDING_SUBMISSION,
        )
        exists_reported = seed_operation_intent(
            session,
            operation,
            sequence=3,
            ticker="CCC",
            attempts=[AttemptOutcomeClass.DUPLICATE_REPORTED],
            order_status=OrderLifecycleState.PENDING_SUBMISSION,
        )
        clean = seed_operation_intent(
            session,
            operation,
            sequence=4,
            ticker="DDD",
            attempts=[PRE_CONNECTION],
            order_status=OrderLifecycleState.SUBMISSION_FAILED,
        )
        unsent = seed_operation_intent(session, operation, sequence=5, ticker="EEE")
        submitted = seed_operation_intent(
            session,
            operation,
            sequence=6,
            ticker="FFF",
            attempts=[ACCEPTED],
            order_status=OrderLifecycleState.SUBMITTED,
            broker_order_id="b-1",
        )
        operation_id, new_job_id = operation.id, new_job.id
        doubt_ids = {in_doubt_null.row.id, in_doubt_timeout.row.id, exists_reported.row.id}
        clean_order, submitted_order = clean.order, submitted.order
        assert clean_order is not None and submitted_order is not None
        clean_order_id, submitted_order_id = clean_order.id, submitted_order.id
        unsent_id = unsent.row.id

    observed: dict[str, int] = {}
    real_loader = ops.load_intent_facts

    def spying_loader(session: Any, ids: Any) -> Any:
        # Attempts are read only AFTER the epoch CAS committed: another connection sees it.
        with get_engine(load_settings()).connect() as other:
            observed["epoch"] = other.execute(
                text("SELECT execution_epoch FROM execution_operations")
            ).scalar_one()
        return real_loader(session, ids)

    monkeypatch.setattr(ops, "load_intent_facts", spying_loader)
    with session_run_lock(strategy_id=OWNER, session_date=S) as lock:
        result = ops.acquire_execution(operation_id, new_job_id, lock)
    assert observed["epoch"] == 3
    assert result.epoch == 3 and result.paused is True
    assert set(result.in_doubt_intent_ids) == doubt_ids
    with session_scope(load_settings()) as session:
        stored = session.get(ExecutionOperation, operation_id)
        assert stored is not None
        assert (stored.state, stored.reason) == ("paused", "outcome_unresolved")
        assert stored.execution_epoch == 3 and stored.executor_job_id == new_job_id
        statuses = {
            row.id: row.status
            for row in session.execute(select(PaperOrder.id, PaperOrder.status)).all()
        }
        unknown = [
            i
            for i in session.execute(select(ExecutionOperationIntent)).scalars()
            if i.id in doubt_ids
        ]
        assert all(statuses[i.paper_order_id] == OrderLifecycleState.UNKNOWN for i in unknown)
        assert statuses[clean_order_id] == OrderLifecycleState.SUBMISSION_FAILED
        assert statuses[submitted_order_id] == OrderLifecycleState.SUBMITTED
        assert unsent_id not in {i.row.id for i in []}
        # The strategy now reads as unresolved through the 20.1-10 predicate.
        status = recovery.strategy_recovery_status(session, OWNER)
        assert status.gate_code is recovery.GateCode.OUTCOME_UNRESOLVED


def test_acquire_execution_without_in_doubt_intents_keeps_running_under_the_new_epoch(
    ops_db: str,
) -> None:
    with session_scope(load_settings()) as session:
        old_job = seed_operation_job(session, status=JobStatus.FAILED)
        new_job = seed_operation_job(session, status=JobStatus.RUNNING)
        operation = seed_operation(
            session,
            state="running",
            reason=None,
            session_date=S,
            epoch=0,
            executor_job=old_job,
            jobs=[(old_job, "start"), (new_job, "continue")],
        )
        seed_operation_intent(session, operation, sequence=1)
        operation_id, new_job_id, old_job_id = operation.id, new_job.id, old_job.id
    with session_run_lock(strategy_id=OWNER, session_date=S) as lock:
        result = ops.acquire_execution(operation_id, new_job_id, lock)
    assert result.paused is False and result.epoch == 1
    # The previous executor's fence is now stale.
    with session_scope(load_settings()) as session:
        assert (
            ops.cas_update_operation(
                session, Fence(operation_id, 0, old_job_id), {"reason_detail": "x"}
            )
            == 0
        )
        assert (
            ops.cas_update_operation(
                session, Fence(operation_id, 1, new_job_id), {"reason_detail": "x"}
            )
            == 1
        )


def test_acquire_execution_refuses_a_non_running_operation(ops_db: str) -> None:
    with session_scope(load_settings()) as session:
        job = seed_operation_job(session, status=JobStatus.RUNNING)
        operation = seed_operation(
            session, state="paused", reason="awaiting_reconciliation", session_date=S
        )
        operation_id, job_id = operation.id, job.id
    with session_run_lock(strategy_id=OWNER, session_date=S) as lock:
        with pytest.raises(OperationConflictError):
            ops.acquire_execution(operation_id, job_id, lock)


# ---------------------------------------------------------------------------
# S1-R3 primitives: late completion and fenced CAS helpers
# ---------------------------------------------------------------------------


def _authorized_attempt() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    operation_id, intent_id, job_id = _t1_setup()
    auth = _authorize(operation_id, intent_id, job_id)
    return operation_id, job_id, auth.attempt_id, auth.paper_order_id


def test_a_late_completion_is_accepted_once_and_refused_the_second_time(ops_db: str) -> None:
    operation_id, job_id, attempt_id, _order_id = _authorized_attempt()
    # The executor lost authority (a takeover moved the epoch on) and still completes ITS row.
    with session_scope(load_settings()) as session:
        operation = session.get(ExecutionOperation, operation_id)
        assert operation is not None
        operation.execution_epoch = 4
    first = ops.complete_attempt_late(
        attempt_id, ACCEPTED, executor_job_id=job_id, execution_epoch=3, http_status=200
    )
    assert first.stale is True
    with pytest.raises(AttemptAlreadyCompletedError):
        ops.complete_attempt_late(
            attempt_id, ACCEPTED, executor_job_id=job_id, execution_epoch=3, http_status=200
        )
    with session_scope(load_settings()) as session:
        row = session.get(OrderSubmissionAttempt, attempt_id)
        assert row is not None and row.outcome_class == "accepted" and row.completed_at is not None
        events = (
            session.execute(
                select(ExecutionEvent).where(ExecutionEvent.event_type == "late_attempt_outcome")
            )
            .scalars()
            .all()
        )
        assert len(events) == 1
        assert events[0].details["stale_executor"] is True
        assert events[0].details["outcome_class"] == "accepted"
        # Order state is never written by the stale executor.
        order = session.execute(select(PaperOrder)).scalar_one()
        assert order.status == OrderLifecycleState.PENDING_SUBMISSION


def test_complete_attempt_late_by_the_live_executor_records_a_non_stale_event(ops_db: str) -> None:
    _operation_id, job_id, attempt_id, _order_id = _authorized_attempt()
    result = ops.complete_attempt_late(
        attempt_id, ACCEPTED, executor_job_id=job_id, execution_epoch=3
    )
    assert result.stale is False


def test_complete_attempt_late_refuses_deadline_expired_from_another_caller(ops_db: str) -> None:
    _operation_id, job_id, attempt_id, _order_id = _authorized_attempt()
    deadline = AttemptOutcomeClass.DEADLINE_EXPIRED
    for other_job, other_epoch in ((uuid.uuid4(), 3), (job_id, 99)):
        with pytest.raises(AttemptOwnershipError):
            ops.complete_attempt_late(
                attempt_id, deadline, executor_job_id=other_job, execution_epoch=other_epoch
            )
    with session_scope(load_settings()) as session:
        row = session.get(OrderSubmissionAttempt, attempt_id)
        assert row is not None and row.outcome_class is None and row.completed_at is None
    # The row's own stale executor may record it after its wall-clock check refused the send.
    with session_scope(load_settings()) as session:
        operation = session.execute(select(ExecutionOperation)).scalar_one()
        operation.execution_epoch = 4
    done = ops.complete_attempt_late(
        attempt_id, deadline, executor_job_id=job_id, execution_epoch=3
    )
    assert done.outcome_class is deadline and done.stale is True


def test_attempt_rows_without_executor_identity_are_never_completed_late(ops_db: str) -> None:
    operation_id, intent_id, job_id = _t1_setup(attempts=[None])
    with session_scope(load_settings()) as session:
        row = session.execute(select(OrderSubmissionAttempt)).scalar_one()
        attempt_id = row.id
    with pytest.raises(AttemptOwnershipError):
        ops.complete_attempt_late(attempt_id, ACCEPTED, executor_job_id=job_id, execution_epoch=3)


def test_fenced_cas_update_operation_affects_zero_rows_when_stale(ops_db: str) -> None:
    """SAF-11: the dead, non-atomic fenced intent/order helpers are deleted; the only fenced
    CAS writer left is ``cas_update_operation``."""

    assert [name for name in dir(ops) if name.startswith("cas_")] == ["cas_update_operation"]
    operation_id, _intent_id, job_id = _t1_setup()
    stale = Fence(operation_id, 2, job_id)
    wrong_job = Fence(operation_id, 3, uuid.uuid4())
    good = Fence(operation_id, 3, job_id)
    with session_scope(load_settings()) as session:
        for fence in (stale, wrong_job):
            assert ops.cas_update_operation(session, fence, {"reason_detail": "x"}) == 0
        assert ops.cas_update_operation(session, good, {"reason_detail": "x"}) == 1
        # A paused operation is no longer writable by its former executor.
        ops.cas_update_operation(
            session, good, {"state": "paused", "reason": "kill_switch_tripped"}
        )
        assert ops.cas_update_operation(session, good, {"reason_detail": "y"}) == 0


def test_attempts_module_is_unchanged_in_scope_and_has_no_alpaca_import() -> None:
    source = Path(inspect.getfile(attempts_module)).read_text()
    assert "import httpx" in source  # classification only
    assert "services.alpaca" not in source


def test_stale_fence_terminate_changes_nothing(ops_db: str) -> None:
    operation_id, intent_id, job_id = _t1_setup(order_status=OrderLifecycleState.PENDING_SUBMISSION)
    with session_scope(load_settings()) as session:
        before = _operation_row(session, operation_id)
        for fence in (Fence(operation_id, 2, job_id), Fence(operation_id, 3, uuid.uuid4())):
            with pytest.raises(OperationConflictError):
                ops.transition(
                    session,
                    operation_id,
                    OperationState.TERMINATED,
                    "execution_window_elapsed",
                    fence=fence,
                )
        locked = session.execute(
            select(ExecutionOperation).where(ExecutionOperation.id == operation_id)
        ).scalar_one()
        with pytest.raises(OperationConflictError):
            ops.terminate_operation(
                session,
                locked,
                TerminatedReason.EXECUTION_WINDOW_ELAPSED,
                ended_by="executor",
                fence=Fence(operation_id, 1, job_id),
            )
        assert _operation_row(session, operation_id) == before
        intent = session.get(ExecutionOperationIntent, intent_id)
        assert intent is not None and intent.disposition == "open"


def _operation_row(session: Any, operation_id: uuid.UUID) -> tuple[Any, ...]:
    session.expire_all()
    row = session.execute(
        select(
            ExecutionOperation.state,
            ExecutionOperation.reason,
            ExecutionOperation.execution_epoch,
            ExecutionOperation.executor_job_id,
        ).where(ExecutionOperation.id == operation_id)
    ).one()
    return tuple(row)


@pytest.mark.parametrize(
    ("reason", "disposition"),
    [
        ("execution_window_elapsed", "expired_unsent"),
        ("evaluation_superseded", "expired_unsent"),
        ("cancelled_by_operator", "cancelled_unsent"),
    ],
)
def test_transition_to_terminated_moves_the_proven_unsent_intents(
    ops_db: str, reason: str, disposition: str
) -> None:
    with session_scope(load_settings()) as session:
        job = seed_operation_job(session, status=JobStatus.RUNNING)
        operation = seed_operation(
            session, state="running", reason=None, session_date=S, epoch=5, executor_job=job
        )
        planned = seed_operation_intent(session, operation, sequence=1, ticker="AAA")
        registered = seed_operation_intent(
            session,
            operation,
            sequence=2,
            ticker="BBB",
            order_status=OrderLifecycleState.PENDING_SUBMISSION,
        )
        submitted = seed_operation_intent(
            session,
            operation,
            sequence=3,
            ticker="CCC",
            attempts=[ACCEPTED],
            order_status=OrderLifecycleState.SUBMITTED,
            broker_order_id="b-9",
        )
        ambiguous = seed_operation_intent(
            session,
            operation,
            sequence=4,
            ticker="DDD",
            attempts=[AMBIGUOUS],
            order_status=OrderLifecycleState.UNKNOWN,
        )
        operation_id, job_id = operation.id, job.id
        ids = (planned.row.id, registered.row.id, submitted.row.id, ambiguous.row.id)
    with session_scope(load_settings()) as session:
        done = ops.transition(
            session,
            operation_id,
            OperationState.TERMINATED,
            reason,
            fence=Fence(operation_id, 5, job_id),
        )
        assert done.state == "terminated" and done.reason == reason
        assert done.execution_epoch == 6 and done.executor_job_id is None
    with session_scope(load_settings()) as session:
        rows = {
            i.id: i.disposition for i in session.execute(select(ExecutionOperationIntent)).scalars()
        }
        assert rows[ids[0]] == rows[ids[1]] == disposition
        assert rows[ids[2]] == rows[ids[3]] == "open"
