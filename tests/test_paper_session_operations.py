"""Paper session as a sequential, pausable execution operation (REC-02, 20.1-15).

Real PostgreSQL (a throwaway migrated database per test), the production loop, the real S1
send authorization (transaction T1) and the real permission check. Only the broker, the price
source (``FreshPriceSource`` unless a test scripts one) and the calendar / manifest answers of
suites whose subject is not those (``tests/support/paper_execution_seams.py``) are replaced.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select, update
from tests.support.basis_fixtures import seed_verified_basis  # noqa: F401
from tests.support.calendar_facts import et, seed_calendar
from tests.support.paper_eligibility import allow_paper_execution
from tests.support.paper_execution_seams import allow_direct_paper_execution
from tests.support.paper_ownership import seed_strategy, set_active_paper_strategy
from tests.support.price_source import FreshPriceSource, ScriptedPriceSource, observation
from tests.test_paper_execution import (
    _begin_attempt_through_bound_log,
    migrated_paper_db,  # noqa: F401  (database fixture)
)

from trading_platform.core import clock
from trading_platform.core.settings import AlpacaBrokerSettings, load_settings
from trading_platform.db.models import (
    AttemptOutcomeClass,
    ExecutionOperation,
    ExecutionOperationIntent,
    Job,
    JobStatus,
    OrderLifecycleState,
    OrderSubmissionAttempt,
    PaperOrder,
    RiskEvent,
    StrategyRun,
    StrategyRunStatus,
    StrategyRunType,
)
from trading_platform.db.models.symbol import Symbol
from trading_platform.db.session import session_scope
from trading_platform.jobs.handlers.paper_session_submission import (
    PaperSessionSubmissionSpec,
    PaperSessionSubmitConflict,
)
from trading_platform.jobs.registry import JobSubmissionConflictError
from trading_platform.services import alpaca as alpaca_module
from trading_platform.services.alpaca import (
    AlpacaClient,
    AlpacaExecutionService,
    AmbiguousOrderSubmissionError,
    OrderNotSentError,
    OrderRejectedError,
    PriceFailure,
)
from trading_platform.services.execution import (
    ExecutionOrderStatus,
    ExecutionService,
    OrderIntent,
    OrderSubmissionResult,
    run_paper_order_submission,
    run_paper_session,
)
from trading_platform.services.execution import permission as permission_module
from trading_platform.services.execution import submit_orders as submit_orders_module
from trading_platform.services.execution.attempts import (
    SubmissionClass,
    SubmissionIntentState,
    load_submission_attempts,
)
from trading_platform.services.execution.operations import (
    OperationConflictError,
    OperationOpenError,
    RiskRunAlreadyOperatedError,
    WindowVerdict,
    end_operation,
    load_intent_facts,
)
from trading_platform.services.execution.permission import WindowFacts, strategy_working_orders
from trading_platform.services.operator_controls import OperatorControlService

STRATEGY = "trend_following_daily"
SESSION = date(2024, 1, 5)


@pytest.fixture(autouse=True)
def _seams(monkeypatch: pytest.MonkeyPatch) -> FreshPriceSource:
    """Submit-time eligibility / manifest stubs plus the run-time seam (see the module doc)."""

    allow_paper_execution(monkeypatch)
    return allow_direct_paper_execution(monkeypatch)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

DEFAULT_BATCH: tuple[tuple[str, str, str], ...] = (
    ("AAPL", "10", "120"),
    ("MSFT", "5", "300"),
    ("NVDA", "2", "500"),
)


def seed_batch(
    batch: Sequence[tuple[str, str, str]] = DEFAULT_BATCH,
    *,
    session_date: date = SESSION,
    owner: bool = True,
    side: str = "long",
    manifest: dict[str, Any] | None = None,
    started_at: datetime | None = None,
    signal_reason: str = "trend_entry",
) -> tuple[uuid.UUID, dict[str, uuid.UUID]]:
    """A succeeded risk evaluation with one approved event per ``(ticker, quantity, price)``."""

    settings = load_settings()
    metadata = (
        __import__("trading_platform.strategies.registry", fromlist=["x"])
        .build_default_registry(settings)
        .resolve(STRATEGY)
        .metadata
    )
    with session_scope(settings) as session:
        strategy = seed_strategy(session, metadata, enabled=True)
        if owner:
            set_active_paper_strategy(session, STRATEGY)
        summary: dict[str, Any] = {"stage": "completed", "as_of_session": session_date.isoformat()}
        if manifest is not None:
            summary["evaluation_manifest"] = manifest
        run = StrategyRun(
            strategy_id=strategy.id,
            run_type=StrategyRunType.RISK_EVALUATION,
            status=StrategyRunStatus.SUCCEEDED,
            trigger_source="test_suite",
            parameters_snapshot={"as_of_session": session_date.isoformat()},
            result_summary=summary,
            completed_at=datetime.now(UTC),
        )
        if started_at is not None:
            run.started_at = started_at
        session.add(run)
        session.flush()
        events: dict[str, uuid.UUID] = {}
        for ticker, quantity, price in batch:
            symbol = session.execute(select(Symbol).where(Symbol.ticker == ticker)).scalar_one_or_none()
            if symbol is None:
                symbol = Symbol(ticker=ticker, active=True)
                session.add(symbol)
                session.flush()
            event = RiskEvent(
                strategy_run_id=run.id,
                symbol_id=symbol.id,
                session_date=session_date,
                signal_direction=side,
                signal_reason=signal_reason,
                outcome="approved",
                decision_code="approved",
                decision_reason="Approved for paper execution.",
                reference_price=Decimal(price),
                proposed_quantity=Decimal(quantity),
                proposed_notional=Decimal(quantity) * Decimal(price),
                risk_metadata={},
            )
            session.add(event)
            session.flush()
            events[ticker] = event.id
        return run.id, events


@dataclass
class ScriptedExecutionService(ExecutionService):
    """A broker double driven by a per-call script, behaving like the real client: it begins its
    attempt through the bound log BEFORE the POST (asserting the committed row exists at that
    moment) and records the outcome class the real client would.

    Script items: ``accept`` | ``fill`` (accepted and already terminal) | ``partial`` |
    ``reject`` | ``ambiguous`` | ``not_sent`` | ``boom`` | a callable (run first, may trip the
    kill switch, then the next item decides) given as ``(callable, item)``.
    """

    script: Sequence[Any] = ("accept",)
    submitted_intents: list[OrderIntent] = field(default_factory=list)
    post_attempts: int = 0

    def describe(self) -> dict[str, object]:
        return {"service": "execution", "status": "available", "provider": "scripted"}

    def _result(self, intent: OrderIntent, status: ExecutionOrderStatus, raw: str) -> OrderSubmissionResult:
        return OrderSubmissionResult(
            client_order_id=intent.client_order_id,
            broker_order_id=f"scripted-{intent.symbol.lower()}-{uuid.uuid4().hex[:8]}",
            symbol=intent.symbol,
            side=intent.side,
            quantity=intent.quantity,
            order_type=intent.order_type,
            time_in_force=intent.time_in_force,
            status=status,
            broker_status=raw,
            submitted_at=datetime(2024, 1, 5, 14, 35, tzinfo=UTC),
            raw_payload={"status": raw},
        )

    def submit_order(self, intent: OrderIntent) -> OrderSubmissionResult:
        index = len(self.submitted_intents)
        item = self.script[min(index, len(self.script) - 1)]
        hook: Callable[[], None] | None = None
        if isinstance(item, tuple):
            hook, item = item
        if item == "boom":  # fails before the client ever begins an attempt
            self.submitted_intents.append(intent)
            raise RuntimeError("broker exploded")
        began = _begin_attempt_through_bound_log()
        self.post_attempts += 1
        self.submitted_intents.append(intent)
        if hook is not None:
            hook()
        log, number = began if began is not None else (None, 0)

        def done(outcome: AttemptOutcomeClass, status: int | None = None) -> None:
            if log is not None:
                log.complete_attempt(number, outcome_class=outcome, http_status=status)  # type: ignore[attr-defined]

        if item == "reject":
            done(AttemptOutcomeClass.REJECTED, 403)
            raise OrderRejectedError(
                "rejected",
                submission_class=SubmissionClass.REJECTED,
                attempts=[(1, "rejected")],
                http_status=403,
            )
        if item == "ambiguous":
            done(AttemptOutcomeClass.AMBIGUOUS)
            raise AmbiguousOrderSubmissionError(
                "ambiguous", submission_class=SubmissionClass.AMBIGUOUS, attempts=[(1, "ambiguous")]
            )
        if item == "not_sent":
            done(AttemptOutcomeClass.PRE_CONNECTION)
            raise OrderNotSentError(
                "not sent",
                submission_class=SubmissionClass.NOT_SENT,
                attempts=[(1, "pre_connection")],
            )
        done(AttemptOutcomeClass.ACCEPTED)
        if item == "fill":
            return self._result(intent, ExecutionOrderStatus.FILLED, "filled")
        if item == "partial":
            return self._result(intent, ExecutionOrderStatus.PARTIALLY_FILLED, "partially_filled")
        return self._result(intent, ExecutionOrderStatus.PENDING, "new")


def _start(
    service: ExecutionService,
    *,
    risk_run_id: uuid.UUID | None = None,
    price_source: Any = None,
    trigger_source: str = "pytest",
    session_date: date = SESSION,
) -> Any:
    return run_paper_order_submission(
        STRATEGY,
        as_of_session=session_date,
        risk_run_id=str(risk_run_id) if risk_run_id else None,
        settings=load_settings(),
        execution_service=service,
        trigger_source=trigger_source,
        price_source=price_source,
    )


def operation_row(operation_id: str | uuid.UUID | None = None) -> ExecutionOperation:
    with session_scope(load_settings()) as session:
        statement = select(ExecutionOperation)
        if operation_id is not None:
            statement = statement.where(ExecutionOperation.id == uuid.UUID(str(operation_id)))
        operation = session.execute(statement.order_by(ExecutionOperation.created_at.desc())).scalars().first()
        assert operation is not None
        session.expunge(operation)
        return operation


def intent_states(operation_id: uuid.UUID) -> list[tuple[str, SubmissionIntentState]]:
    with session_scope(load_settings()) as session:
        facts = load_intent_facts(session, [operation_id])[operation_id]
        return [(fact.ticker, fact.state) for fact in facts]


def count(model: Any) -> int:
    with session_scope(load_settings()) as session:
        return int(session.execute(select(func.count()).select_from(model)).scalar_one())


def finish_jobs() -> None:
    """The Job of a finished run is terminal (a RUNNING Job of an operation blocks End/expiry)."""

    with session_scope(load_settings()) as session:
        session.execute(
            update(Job).values(status=JobStatus.SUCCEEDED, completed_at=datetime.now(UTC))
        )


# ---------------------------------------------------------------------------
# Task 2: the sequential loop
# ---------------------------------------------------------------------------


def test_first_order_working_pauses_and_preserves_unsent_intents(migrated_paper_db: str) -> None:  # noqa: F811
    seed_batch()
    service = ScriptedExecutionService(["accept", "accept", "accept"])

    report = _start(service)

    operation = report.result_summary["operation"]
    assert operation["state"] == "paused"
    assert operation["reason"] == "working_order_commitments_unaccounted"
    assert operation["next_action"] == "wait_for_order_then_sync_and_continue"
    assert service.post_attempts == 1  # POST count 1
    assert report.result_summary["submitted_count"] == 1
    states = intent_states(uuid.UUID(operation["id"]))
    assert states == [
        ("AAPL", SubmissionIntentState.SUBMITTED),
        ("MSFT", SubmissionIntentState.PLANNED),
        ("NVDA", SubmissionIntentState.PLANNED),
    ]
    with session_scope(load_settings()) as session:
        assert session.execute(select(func.count()).select_from(PaperOrder)).scalar_one() == 1
        unsent = session.execute(
            select(ExecutionOperationIntent).where(ExecutionOperationIntent.paper_order_id.is_(None))
        ).scalars().all()
        assert sorted(item.quantity for item in unsent) == [Decimal("2"), Decimal("5")]
        # never registered, never sent: their identities are preserved on the pinned rows
        assert all(item.client_order_id.startswith("tp-20240105-") for item in unsent)
        assert all(item.disposition == "open" for item in unsent)


def test_immediate_fill_not_synced_pauses_tl1(migrated_paper_db: str) -> None:  # noqa: F811
    seed_batch()
    service = ScriptedExecutionService(["fill", "accept", "accept"])

    report = _start(service)

    operation = report.result_summary["operation"]
    assert (operation["state"], operation["reason"]) == ("paused", "working_order_commitments_unaccounted")
    assert service.post_attempts == 1
    with session_scope(load_settings()) as session:
        order = session.execute(select(PaperOrder)).scalar_one()
        assert order.status == OrderLifecycleState.FILLED
        assert order.last_synced_at is None  # terminal but never synced: fills may not be ingested
        working = strategy_working_orders(session, STRATEGY)
    assert [item.symbol for item in working] == ["AAPL"]


def test_ambiguous_result_pauses_outcome_unresolved(migrated_paper_db: str) -> None:  # noqa: F811
    seed_batch()
    service = ScriptedExecutionService(["ambiguous", "accept", "accept"])

    with pytest.raises(AmbiguousOrderSubmissionError):  # the Job still fails outcome_uncertain
        _start(service)

    operation = operation_row()
    assert (operation.state, operation.reason) == ("paused", "outcome_unresolved")
    assert service.post_attempts == 1
    with session_scope(load_settings()) as session:
        order = session.execute(select(PaperOrder)).scalar_one()
        assert order.status == OrderLifecycleState.UNKNOWN
        attempts = load_submission_attempts(session, order.id)
    assert [a.outcome_class for a in attempts] == [AttemptOutcomeClass.AMBIGUOUS]
    assert [state for _ticker, state in intent_states(operation.id)] == [
        SubmissionIntentState.AMBIGUOUS,
        SubmissionIntentState.PLANNED,
        SubmissionIntentState.PLANNED,
    ]


def test_rejected_order_continues_after_permission_check(migrated_paper_db: str) -> None:  # noqa: F811
    seed_batch()
    service = ScriptedExecutionService(["reject", "accept", "accept"])
    prices = FreshPriceSource()

    report = _start(service, price_source=prices)

    assert service.post_attempts == 2
    assert report.result_summary["operation"]["reason"] == "working_order_commitments_unaccounted"
    assert len(report.result_summary["rejected_orders"]) == 1
    assert report.result_summary["submitted_count"] == 1
    # one fresh price lookup per sent intent: the permission check ran again before intent 2
    assert len(prices.calls) == 2
    assert intent_states(uuid.UUID(report.result_summary["operation"]["id"])) == [
        ("AAPL", SubmissionIntentState.REJECTED),
        ("MSFT", SubmissionIntentState.SUBMITTED),
        ("NVDA", SubmissionIntentState.PLANNED),
    ]


def test_all_intents_terminal_completes_operation(migrated_paper_db: str) -> None:  # noqa: F811
    seed_batch(DEFAULT_BATCH[:2])
    service = ScriptedExecutionService(["reject", "reject"])

    report = _start(service)

    operation = operation_row()
    assert (operation.state, operation.reason) == ("completed", None)
    assert operation.executor_job_id is None and operation.execution_epoch >= 1
    assert report.result_summary["operation"]["state"] == "completed"
    assert report.result_summary["operation"]["next_action"] == "none"
    assert report.result_summary["submitted_count"] == 0
    assert len(report.result_summary["rejected_orders"]) == 2


def test_all_pre_connection_failures_pause_broker_unavailable(  # noqa: F811
    migrated_paper_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The REAL client + attempt log: attempt 1 is the pre-authorized T1 row, every retry runs its
    own T1; all fail before a connection exists, so the outcome is certain and the Job ends
    normally with the operation paused/broker_unavailable and the intent still retryable."""
    seed_batch(DEFAULT_BATCH[:2])
    monkeypatch.setattr(alpaca_module.time, "sleep", lambda *_: None)
    posts: list[httpx.Request] = []

    def refuse(request: httpx.Request) -> httpx.Response:
        posts.append(request)
        raise httpx.ConnectError("refused", request=request)

    settings = AlpacaBrokerSettings(
        api_key="k", api_secret="s", max_retries=2, retry_backoff_factor=0.0
    )
    client = AlpacaClient(
        settings,
        http_client=httpx.Client(
            transport=httpx.MockTransport(refuse), base_url="https://paper-api.alpaca.markets"
        ),
    )
    service = AlpacaExecutionService(settings, client=client)

    report = _start(service)

    operation = report.result_summary["operation"]
    assert (operation["state"], operation["reason"]) == ("paused", "broker_unavailable")
    assert len(posts) == 3  # initial attempt + two in-loop retries, each behind its own T1
    with session_scope(load_settings()) as session:
        order = session.execute(select(PaperOrder)).scalar_one()
        attempts = load_submission_attempts(session, order.id)
        rows = session.execute(select(OrderSubmissionAttempt)).scalars().all()
    assert order.status == OrderLifecycleState.SUBMISSION_FAILED
    assert [a.outcome_class for a in attempts] == [AttemptOutcomeClass.PRE_CONNECTION] * 3
    assert all(row.execution_epoch is not None and row.executor_job_id is not None for row in rows)
    assert [state for _t, state in intent_states(uuid.UUID(operation["id"]))] == [
        SubmissionIntentState.NOT_SENT,
        SubmissionIntentState.PLANNED,
    ]


def test_kill_switch_between_steps_pauses_kill_switch_tripped_and_keeps_legacy_action(  # noqa: F811
    migrated_paper_db: str,
) -> None:
    seed_batch()
    settings = load_settings()

    def trip() -> None:
        OperatorControlService(settings=settings).trip_kill_switch(
            reason="between steps", actor="pytest", trigger_source="pytest"
        )

    service = ScriptedExecutionService([(trip, "reject"), "accept", "accept"])

    report = _start(service)

    summary = report.result_summary
    assert report.status == StrategyRunStatus.FAILED.value  # the Phase 20 mid-run halt shape
    assert summary["action"] == "blocked_mid_run_global_kill_switch"  # legacy action kept
    assert summary["blocked_reason"] == "global_kill_switch_tripped"
    assert summary["operation"]["state"] == "paused"
    assert summary["operation"]["reason"] == "kill_switch_tripped"
    assert summary["operation"]["next_action"] == "reset_kill_switch_then_continue"
    assert summary["skipped_by_kill_switch_count"] == 2
    assert service.post_attempts == 1
    assert [state for _t, state in intent_states(uuid.UUID(summary["operation"]["id"]))] == [
        SubmissionIntentState.REJECTED,
        SubmissionIntentState.PLANNED,
        SubmissionIntentState.PLANNED,
    ]


def test_blocked_before_first_intent_creates_no_operation(migrated_paper_db: str) -> None:  # noqa: F811
    seed_batch()
    settings = load_settings()
    OperatorControlService(settings=settings).trip_kill_switch(
        reason="halt", actor="pytest", trigger_source="pytest"
    )
    service = ScriptedExecutionService(["accept"])

    report = _start(service)

    assert report.result_summary["action"] == "blocked_global_kill_switch"
    assert "operation" not in report.result_summary
    assert count(ExecutionOperation) == 0 and service.post_attempts == 0


def test_blocked_session_by_disabled_strategy_and_noop_create_no_operation(  # noqa: F811
    migrated_paper_db: str,
) -> None:
    seed_batch()
    settings = load_settings()
    OperatorControlService(settings=settings).disable_strategy(
        STRATEGY, reason="maintenance", actor="pytest", trigger_source="pytest"
    )
    service = ScriptedExecutionService(["accept"])

    report = _start(service)

    assert report.result_summary["action"] == "blocked_strategy_disabled"
    assert count(ExecutionOperation) == 0


def test_operation_creation_pins_concrete_risk_run_id(migrated_paper_db: str) -> None:  # noqa: F811
    older, _ = seed_batch(DEFAULT_BATCH[:1], started_at=datetime(2024, 1, 5, 20, 0, tzinfo=UTC))
    newest, _ = seed_batch(DEFAULT_BATCH[:2], started_at=datetime(2024, 1, 5, 21, 0, tzinfo=UTC))
    service = ScriptedExecutionService(["accept"])

    report = run_paper_session(
        STRATEGY,
        as_of_session=SESSION,
        risk_run_id=None,  # the request pins nothing: resolved to a concrete id at first submission
        settings=load_settings(),
        execution_service=service,
    )

    operation = operation_row()
    assert operation.risk_run_id == newest != older
    assert report.source_risk_run_id == str(newest)
    assert report.operation_id == str(operation.id)
    assert (report.operation_state, report.operation_reason) == (
        "paused",
        "working_order_commitments_unaccounted",
    )
    # the start Job is linked and holds execution authority at creation
    assert operation.executor_job_id is not None


def test_second_open_operation_race_is_a_typed_conflict(  # noqa: F811
    migrated_paper_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The race is decided by the DATABASE constraint (one open operation per strategy), not by a
    prior read: a competing operation commits between the read and the insert."""
    seed_batch()
    other_run, _ = seed_batch(DEFAULT_BATCH[:1], session_date=date(2024, 1, 4))
    settings = load_settings()
    real_identity = submit_orders_module._executor_identity

    def racing_identity(settings_arg: Any, job_id: Any) -> Any:
        identity = real_identity(settings_arg, job_id)
        with session_scope(settings) as session:  # a competing start commits its operation first
            run = session.get(StrategyRun, other_run)
            session.add(
                ExecutionOperation(
                    strategy_id=run.strategy_id,
                    as_of_session=date(2024, 1, 4),
                    risk_run_id=other_run,
                    state="paused",
                    reason="awaiting_reconciliation",
                )
            )
        return identity

    monkeypatch.setattr(submit_orders_module, "_executor_identity", racing_identity)
    service = ScriptedExecutionService(["accept"])

    with pytest.raises(OperationOpenError):
        _start(service, risk_run_id=None)

    assert service.post_attempts == 0  # zero broker calls
    assert count(PaperOrder) == 0
    assert count(ExecutionOperationIntent) == 0


def test_exception_in_loop_pauses_operation_best_effort(migrated_paper_db: str) -> None:  # noqa: F811
    seed_batch()
    service = ScriptedExecutionService(["boom"])

    with pytest.raises(RuntimeError, match="broker exploded"):
        _start(service)

    operation = operation_row()
    assert (operation.state, operation.reason) == ("paused", "awaiting_reconciliation")
    with session_scope(load_settings()) as session:
        order = session.execute(select(PaperOrder)).scalar_one()
        attempts = load_submission_attempts(session, order.id)
    assert order.status == OrderLifecycleState.SUBMISSION_FAILED
    # the client never began its attempt, so no request left the process: established not-sent
    assert [a.outcome_class for a in attempts] == [AttemptOutcomeClass.PRE_CONNECTION]


def test_a_more_specific_pause_wins_over_the_best_effort_pause(migrated_paper_db: str) -> None:  # noqa: F811
    seed_batch()
    # ambiguous: the exception propagates AFTER the specific pause; the crash handler never
    # overwrites paused/outcome_unresolved with paused/awaiting_reconciliation
    with pytest.raises(AmbiguousOrderSubmissionError):
        _start(ScriptedExecutionService(["ambiguous"]))

    assert operation_row().reason == "outcome_unresolved"


def test_run_report_is_additive(migrated_paper_db: str) -> None:  # noqa: F811
    seed_batch(DEFAULT_BATCH[:2])
    service = ScriptedExecutionService(["accept", "accept"])

    report = run_paper_session(
        STRATEGY, as_of_session=SESSION, settings=load_settings(), execution_service=service
    )

    summary = report.result_summary
    legacy_keys = {
        "stage",
        "strategy_id",
        "as_of_session",
        "requested_risk_run_id",
        "source_risk_run_id",
        "approved_candidate_count",
        "submitted_count",
        "existing_count",
        "reused_count",
        "versioned_count",
        "skipped_by_kill_switch_count",
        "skipped_by_ownership_count",
        "submitted_orders",
        "existing_orders",
        "reused_orders",
        "versioned_orders",
        "skipped_by_kill_switch",
        "skipped_by_ownership",
        "broker_provider",
        "execution_defaults",
        "session_preflight",
    }
    assert legacy_keys <= set(summary)
    assert report.action == "submitted_missing_orders"  # every existing action value keeps its meaning
    assert summary["stage"] == "completed"
    assert set(summary["operation"]) == {"id", "state", "reason", "reason_detail", "next_action"}
    assert report.to_dict()["operation_id"] == summary["operation"]["id"]


def test_stale_epoch_guard_refuses_before_post(  # noqa: F811
    migrated_paper_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_batch(DEFAULT_BATCH[:2])
    settings = load_settings()
    real_send = submit_orders_module._send_authorized

    def takeover_then_send(ctx: Any, **kwargs: Any) -> Any:
        with session_scope(settings) as session:  # a takeover bumps the fencing epoch
            session.execute(
                update(ExecutionOperation).values(
                    execution_epoch=ExecutionOperation.execution_epoch + 1
                )
            )
        return real_send(ctx, **kwargs)

    monkeypatch.setattr(submit_orders_module, "_send_authorized", takeover_then_send)
    service = ScriptedExecutionService(["accept"])

    with pytest.raises(OperationConflictError):
        _start(service)

    assert service.post_attempts == 0  # zero POSTs
    assert count(OrderSubmissionAttempt) == 0  # and zero attempt rows for that intent


def test_lost_lease_at_the_guard_refuses_before_post(  # noqa: F811
    migrated_paper_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_batch(DEFAULT_BATCH[:2])
    settings = load_settings()
    real_send = submit_orders_module._send_authorized

    def lose_lease_then_send(ctx: Any, **kwargs: Any) -> Any:
        with session_scope(settings) as session:
            session.execute(update(Job).values(lease_expires_at=datetime(2020, 1, 1, tzinfo=UTC)))
        return real_send(ctx, **kwargs)

    monkeypatch.setattr(submit_orders_module, "_send_authorized", lose_lease_then_send)
    service = ScriptedExecutionService(["accept"])

    with pytest.raises(OperationConflictError):
        _start(service)

    assert service.post_attempts == 0 and count(OrderSubmissionAttempt) == 0


def test_t1_refusal_ends_the_loop_with_zero_posts_and_leaves_the_intent_unsent(  # noqa: F811
    migrated_paper_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_batch(DEFAULT_BATCH[:2])
    from trading_platform.services.execution.operations import SendRefusal, SendRefusedError

    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise SendRefusedError(SendRefusal.WRONG_EXECUTOR)

    monkeypatch.setattr(submit_orders_module, "authorize_send", refuse)
    service = ScriptedExecutionService(["accept"])

    with pytest.raises(OperationConflictError):
        _start(service)

    assert service.post_attempts == 0
    assert count(OrderSubmissionAttempt) == 0
    with session_scope(load_settings()) as session:
        order = session.execute(select(PaperOrder)).scalar_one()
    assert order.status == OrderLifecycleState.PENDING_SUBMISSION  # registered, never sent


def test_a_service_that_never_touches_the_attempt_log_still_leaves_a_complete_attempt(  # noqa: F811
    migrated_paper_db: str,
) -> None:
    """The single send path completes the pre-authorized attempt itself when the client never
    began it (it returned a result, so the request was accepted)."""

    class Silent(ExecutionService):
        def __init__(self) -> None:
            self.submitted_intents: list[OrderIntent] = []

        def describe(self) -> dict[str, object]:
            return {"service": "execution", "status": "available", "provider": "silent"}

        def submit_order(self, intent: OrderIntent) -> OrderSubmissionResult:
            self.submitted_intents.append(intent)
            return ScriptedExecutionService()._result(intent, ExecutionOrderStatus.PENDING, "new")

    seed_batch(DEFAULT_BATCH[:1])

    _start(Silent())

    with session_scope(load_settings()) as session:
        order = session.execute(select(PaperOrder)).scalar_one()
        attempts = load_submission_attempts(session, order.id)
    assert [a.outcome_class for a in attempts] == [AttemptOutcomeClass.ACCEPTED]


def test_start_path_never_versions_existing_intents(  # noqa: F811
    migrated_paper_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The loop acts only on the operation's own intent rows: if the resolver ever produced
    `create_new_version` for an identity whose earlier version reached the broker, the guard raises
    `version_bypass_refused` and nothing is sent."""
    from trading_platform.services.execution.intent_identity import VersionBypassRefusedError

    seed_batch(DEFAULT_BATCH[:2])
    first = ScriptedExecutionService(["accept"])
    report = _start(first)
    operation_id = uuid.UUID(report.result_summary["operation"]["id"])
    real_resolve = submit_orders_module._resolve_paper_intent_decision

    def versioning(*args: Any, **kwargs: Any) -> Any:
        decision = real_resolve(*args, **kwargs)
        from dataclasses import replace

        return replace(decision, action="create_new_version")

    # a fresh, never-sent identity cannot be versioned; an identity that reached the broker can
    # never be (the guard is asserted directly on the first, accepted order's key)
    monkeypatch.setattr(submit_orders_module, "_resolve_paper_intent_decision", versioning)
    with session_scope(load_settings()) as session:
        order = session.execute(select(PaperOrder)).scalar_one()
        from trading_platform.services.execution.idempotency import derive_order_identity

        with pytest.raises(VersionBypassRefusedError):
            submit_orders_module.create_new_version(
                session,
                strategy_id=STRATEGY,
                candidate=submit_orders_module._candidate_from_risk_event(
                    session.execute(select(RiskEvent).where(RiskEvent.id == order.source_risk_event_id)).scalar_one(),
                    session.get(Symbol, order.symbol_id),
                ),
                identity=derive_order_identity(
                    prefix="tp",
                    strategy_id=STRATEGY,
                    session_date=SESSION,
                    symbol="AAPL",
                    side="buy",
                    quantity=order.quantity,
                ),
            )
    assert first.post_attempts == 1
    assert operation_row(operation_id).state == "paused"
    assert source_has_no_version_path_in_the_loop()


def source_has_no_version_path_in_the_loop() -> bool:
    import inspect

    loop_source = inspect.getsource(submit_orders_module._run_operation_loop)
    return "create_new_version" not in loop_source


# ---------------------------------------------------------------------------
# D-25 / D-26 per-intent provenance and risk (the loop's permission steps)
# ---------------------------------------------------------------------------


def test_manifest_change_requires_reevaluation_sends_nothing_then_end_cancels_unsent(  # noqa: F811
    migrated_paper_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trading_platform.services.evaluation_manifest import (
        ManifestVerification,
        ManifestVerificationStatus,
    )

    seed_batch(DEFAULT_BATCH[:2])
    monkeypatch.setattr(
        permission_module,
        "verify_risk_run_manifest",
        lambda **kwargs: ManifestVerification(ManifestVerificationStatus.EVALUATION_DATA_CHANGED),
    )
    service = ScriptedExecutionService(["accept"])

    report = _start(service)

    operation = report.result_summary["operation"]
    assert (operation["state"], operation["reason"]) == ("requires_reevaluation", "evaluation_data_changed")
    assert operation["next_action"] == "end_operation_then_reevaluate"
    assert service.post_attempts == 0 and count(PaperOrder) == 0
    finish_jobs()
    with session_scope(load_settings()) as session:
        result = end_operation(
            session, uuid.UUID(operation["id"]), operator_reason="data changed", actor="pytest"
        )
        assert len(result.unsent_cancelled) == 2
    assert operation_row().state == "terminated"
    assert [d for _t, d in intent_states(uuid.UUID(operation["id"]))] == [
        SubmissionIntentState.CANCELLED_UNSENT,
        SubmissionIntentState.CANCELLED_UNSENT,
    ]


def test_settings_digest_change_requires_reevaluation_strategy_settings_changed(  # noqa: F811
    migrated_paper_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trading_platform.services.evaluation_manifest import (
        ManifestVerification,
        ManifestVerificationStatus,
    )

    seed_batch(DEFAULT_BATCH[:1])
    monkeypatch.setattr(
        permission_module,
        "verify_risk_run_manifest",
        lambda **kwargs: ManifestVerification(ManifestVerificationStatus.STRATEGY_SETTINGS_CHANGED),
    )

    report = _start(ScriptedExecutionService(["accept"]))

    assert report.result_summary["operation"]["reason"] == "strategy_settings_changed"


def test_risk_limit_failure_for_the_first_intent_requires_reevaluation_without_replan(  # noqa: F811
    migrated_paper_db: str,
) -> None:
    """Cash is shorter than the first pinned intent at the fresh price: nothing is sent, the
    operation is requires_reevaluation/risk_limit_failed:insufficient_cash and the pinned
    identities are unchanged (no re-plan)."""
    seed_batch(DEFAULT_BATCH[:2])
    settings = load_settings()
    from trading_platform.db.models import AccountSnapshot

    with session_scope(settings) as session:
        session.add(
            AccountSnapshot(
                snapshot_source="broker_sync",
                snapshot_at=datetime(2024, 1, 5, 21, 0, tzinfo=UTC),
                cash=Decimal("100"),
                gross_exposure=Decimal("0"),
                total_equity=Decimal("100"),
                buying_power=Decimal("100"),
                open_positions=0,
            )
        )
    service = ScriptedExecutionService(["accept"])

    report = _start(service)

    operation = report.result_summary["operation"]
    assert operation["state"] == "requires_reevaluation"
    assert operation["reason"] == "risk_limit_failed:insufficient_cash"
    assert service.post_attempts == 0
    with session_scope(settings) as session:
        rows = session.execute(select(ExecutionOperationIntent)).scalars().all()
    assert sorted(row.quantity for row in rows) == [Decimal("5"), Decimal("10")]  # unchanged


def test_price_moved_beyond_tolerance_pauses_with_the_first_intent_unsent(  # noqa: F811
    migrated_paper_db: str,
) -> None:
    seed_batch(DEFAULT_BATCH[:2])
    prices = ScriptedPriceSource([observation("AAPL", "130")])  # +8.3% over the 120 reference

    report = _start(ScriptedExecutionService(["accept"]), price_source=prices)

    operation = report.result_summary["operation"]
    assert (operation["state"], operation["reason"]) == ("paused", "price_moved_beyond_tolerance")
    assert operation["next_action"] == "wait_for_price_then_continue"
    assert count(PaperOrder) == 0  # nothing registered, nothing sent


def test_unavailable_price_pauses_price_unavailable_with_detail(migrated_paper_db: str) -> None:  # noqa: F811
    seed_batch(DEFAULT_BATCH[:1])
    prices = ScriptedPriceSource([PriceFailure.FEED_NOT_AUTHORIZED])

    report = _start(ScriptedExecutionService(["accept"]), price_source=prices)

    operation = report.result_summary["operation"]
    assert (operation["state"], operation["reason"], operation["reason_detail"]) == (
        "paused",
        "price_unavailable",
        "feed_not_authorized",
    )


def test_window_elapsed_terminates_the_operation_and_expires_unsent(  # noqa: F811
    migrated_paper_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_batch(DEFAULT_BATCH[:2])
    monkeypatch.setattr(
        permission_module,
        "evaluation_window_facts",
        lambda *a, **k: WindowFacts(verdict=WindowVerdict.ELAPSED, session_opens_at=None),
    )

    report = _start(ScriptedExecutionService(["accept"]))

    operation = report.result_summary["operation"]
    assert (operation["state"], operation["reason"]) == ("terminated", "execution_window_elapsed")
    assert [d for _t, d in intent_states(uuid.UUID(operation["id"]))] == [
        SubmissionIntentState.EXPIRED_UNSENT,
        SubmissionIntentState.EXPIRED_UNSENT,
    ]
    assert count(PaperOrder) == 0


# ---------------------------------------------------------------------------
# Task 3: start-mode submit-time gates, OPS-07 retry rule
# ---------------------------------------------------------------------------


def _payload(risk_run_id: uuid.UUID | None = None) -> dict[str, Any]:
    return {
        "strategy_id": STRATEGY,
        "as_of_session": SESSION.isoformat(),
        "risk_run_id": str(risk_run_id) if risk_run_id else None,
    }


def _validate(risk_run_id: uuid.UUID | None = None, *, now: datetime | None = None) -> Any:
    spec = PaperSessionSubmissionSpec(
        load_settings(), clock=(lambda: now) if now is not None else (lambda: datetime.now(UTC))
    )
    return spec.validate_payload(_payload(risk_run_id))


def _conflict(risk_run_id: uuid.UUID | None = None, **kwargs: Any) -> JobSubmissionConflictError:
    with pytest.raises(JobSubmissionConflictError) as excinfo:
        _validate(risk_run_id, **kwargs)
    return excinfo.value


def _end_open_operation() -> None:
    finish_jobs()
    with session_scope(load_settings()) as session:
        operation = session.execute(select(ExecutionOperation)).scalars().first()
        end_operation(session, operation.id, operator_reason="test end", actor="pytest")


def test_new_session_while_operation_open_is_operation_open(migrated_paper_db: str) -> None:  # noqa: F811
    seed_batch(DEFAULT_BATCH[:2])
    report = _start(ScriptedExecutionService(["accept"]))
    finish_jobs()  # the run is over; the operation stays open (paused)

    error = _conflict()

    assert error.code == PaperSessionSubmitConflict.OPERATION_OPEN.value
    assert error.detail["operation_id"] == report.result_summary["operation"]["id"]
    assert error.detail["next_action"] == "continue"
    assert error.detail["operation_reason"] == "working_order_commitments_unaccounted"
    assert all(isinstance(value, str) for value in error.detail.values())
    assert count(ExecutionOperation) == 1  # the gate is read-only: nothing was written


def test_working_orders_gate_after_the_operation_ended(migrated_paper_db: str) -> None:  # noqa: F811
    seed_batch(DEFAULT_BATCH[:2])
    _start(ScriptedExecutionService(["accept"]))
    _end_open_operation()

    error = _conflict()

    # the working AAPL order survives the End (TL-2): the strategy stays blocked
    assert error.code == PaperSessionSubmitConflict.WORKING_ORDER_COMMITMENTS_UNACCOUNTED.value
    assert "tp-20240105-aapl-" in error.detail["working_orders"]


def test_risk_run_already_operated_only_when_the_pinned_run_is_terminated_and_a_completed_one_is_a_noop(  # noqa: F811
    migrated_paper_db: str,
) -> None:
    risk_run, _ = seed_batch(DEFAULT_BATCH[:2])
    _start(ScriptedExecutionService(["reject", "reject"]), risk_run_id=risk_run)  # completed
    finish_jobs()

    # completed: the request is NOT rejected; the run creates nothing and returns the Phase 20 no-op
    assert _validate(risk_run)["risk_run_id"] == str(risk_run)
    service = ScriptedExecutionService(["accept"])
    report = run_paper_session(
        STRATEGY,
        as_of_session=SESSION,
        risk_run_id=str(risk_run),
        settings=load_settings(),
        execution_service=service,
    )
    assert report.action == "noop_existing_orders"
    assert service.post_attempts == 0 and count(ExecutionOperation) == 1

    # terminated: a second pinned run (another symbol, verified basis), ended with one order sent
    second, _ = seed_batch(DEFAULT_BATCH[2:3], session_date=SESSION)
    with session_scope(load_settings()) as session:
        seed_verified_basis(session, risk_run_id=second)
    _start(
        ScriptedExecutionService(["accept"]),
        risk_run_id=second,
        price_source=ScriptedPriceSource([PriceFailure.PRICE_LOOKUP_FAILED]),
        trigger_source="second",
    )  # paused price_unavailable: nothing sent, so no working order survives the End
    finish_jobs()
    with session_scope(load_settings()) as session:
        operation = session.execute(
            select(ExecutionOperation).where(ExecutionOperation.risk_run_id == second)
        ).scalar_one()
        end_operation(session, operation.id, operator_reason="done", actor="pytest")
    error = _conflict(second)
    assert error.code == PaperSessionSubmitConflict.RISK_RUN_ALREADY_OPERATED.value
    assert error.detail["next_action"] == "new_evaluation_required"


def test_run_time_reports_the_typed_conflicts(migrated_paper_db: str) -> None:  # noqa: F811
    risk_run, _ = seed_batch(DEFAULT_BATCH[:2])
    _start(ScriptedExecutionService(["accept"]), risk_run_id=risk_run)
    finish_jobs()
    # a Job queued before the operation appeared reaches run time: typed operation_open
    with pytest.raises(OperationOpenError):
        _start(ScriptedExecutionService(["accept"]), risk_run_id=seed_batch(DEFAULT_BATCH[:1])[0])
    _end_open_operation()
    with pytest.raises(RiskRunAlreadyOperatedError):
        _start(ScriptedExecutionService(["accept"]), risk_run_id=risk_run)


def test_submit_precedence_of_start_mode_gates(migrated_paper_db: str, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    """Fixed precedence: recovery gate -> operation_open -> working_order_commitments_unaccounted ->
    risk_run_already_operated -> evaluation_basis_unverified. Each adjacent pair, both true."""
    risk_run, _ = seed_batch(DEFAULT_BATCH[:2])
    codes = PaperSessionSubmitConflict

    # 1. basis unverified alone: an earlier order from ANOTHER evaluation, never synced
    earlier_run, _ = seed_batch(DEFAULT_BATCH[:1], session_date=date(2024, 1, 4))
    _start(
        ScriptedExecutionService(["reject"]),
        risk_run_id=earlier_run,
        session_date=date(2024, 1, 4),
    )  # a completed operation whose order is never synced
    finish_jobs()
    assert _conflict(risk_run).code == codes.EVALUATION_BASIS_UNVERIFIED.value
    assert _conflict(risk_run).detail["reason"] == "predates_executions"

    # 2. a terminated operation of the pinned run beats the unverified basis
    with session_scope(load_settings()) as session:
        session.add(
            ExecutionOperation(
                strategy_id=session.get(StrategyRun, risk_run).strategy_id,
                as_of_session=SESSION,
                risk_run_id=risk_run,
                state="terminated",
                reason="cancelled_by_operator",
            )
        )
    assert _conflict(risk_run).code == codes.RISK_RUN_ALREADY_OPERATED.value

    # 3. a working order beats a terminated pinned operation
    with session_scope(load_settings()) as session:
        seed_working_order(session)
    assert _conflict(risk_run).code == codes.WORKING_ORDER_COMMITMENTS_UNACCOUNTED.value

    # 4. an open operation beats the working order
    with session_scope(load_settings()) as session:
        other = session.get(StrategyRun, risk_run)
        session.add(
            ExecutionOperation(
                strategy_id=other.strategy_id,
                as_of_session=date(2024, 1, 3),
                risk_run_id=seed_run_row(session, date(2024, 1, 3)),
                state="paused",
                reason="awaiting_reconciliation",
            )
        )
    assert _conflict(risk_run).code == codes.OPERATION_OPEN.value

    # 5. the recovery gate (an unresolved outcome) beats an open operation
    monkeypatch.setattr(
        "trading_platform.jobs.handlers.paper_session_submission.strategy_recovery_status",
        lambda session, strategy_id, **kwargs: type(
            "S", (), {"gate_code": type("G", (), {"value": "outcome_unresolved"})()}
        )(),
    )
    assert _conflict(risk_run).code == "outcome_unresolved"


def seed_run_row(session: Any, session_date: date) -> uuid.UUID:
    strategy_id = session.execute(select(StrategyRun.strategy_id).limit(1)).scalar_one()
    run = StrategyRun(
        strategy_id=strategy_id,
        run_type=StrategyRunType.RISK_EVALUATION,
        status=StrategyRunStatus.SUCCEEDED,
        trigger_source="tests",
        parameters_snapshot={"as_of_session": session_date.isoformat()},
        result_summary={},
    )
    session.add(run)
    session.flush()
    return run.id


def seed_working_order(session: Any) -> None:
    from tests.support.recovery_fixtures import seed_intent, seed_paper_run

    run = seed_paper_run(session, None)
    seed_intent(
        session,
        run,
        status=OrderLifecycleState.SUBMITTED,
        attempts=(AttemptOutcomeClass.ACCEPTED,),
        broker_order_id="working-1",
        broker_status="new",
    )


def test_retry_of_paper_session_job_resolves_to_continue(migrated_paper_db: str) -> None:  # noqa: F811
    """OPS-07 retry rule, start-mode half: ``retry()`` re-runs ``validate_payload``, so a retried
    start payload is rejected operation_open (details: operation_id, next_action 'continue') while
    the operation is open, risk_run_already_operated after it terminated and accepted (returning
    noop_existing_orders) after it completed; before any operation it is a fresh start."""
    import inspect

    from trading_platform.orchestration import job_mutations

    retry_source = inspect.getsource(job_mutations.JobOrchestrationService.retry)
    assert "validate_payload" in retry_source  # retry and submit share the gate point

    risk_run, _ = seed_batch(DEFAULT_BATCH[:2])
    assert _validate(risk_run)["risk_run_id"] == str(risk_run)  # before any operation: fresh start
    _start(ScriptedExecutionService(["accept"]), risk_run_id=risk_run)
    finish_jobs()
    open_error = _conflict(risk_run)
    assert open_error.code == "operation_open"
    assert open_error.detail["next_action"] == "continue"
    _end_open_operation()
    assert _conflict(risk_run).code in {
        "working_order_commitments_unaccounted",
        "risk_run_already_operated",
    }
    # the typed refusal never starts a second operation
    assert count(ExecutionOperation) == 1


def test_paused_operation_left_past_its_window_does_not_fail_the_next_days_start(  # noqa: F811
    migrated_paper_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run time touches the expired operation first (D-21): the paused operation of the previous
    evaluation is terminated (evaluation_superseded) and the next day's start proceeds."""
    seed_calendar(date(2025, 11, 24), date(2026, 1, 9))
    s1, s2 = date(2025, 12, 2), date(2025, 12, 3)
    first_run, _ = seed_batch(DEFAULT_BATCH[:1], session_date=s1)
    monkeypatch.setattr(clock, "now_utc", lambda: et(2025, 12, 3, 10, 0))
    first = run_paper_order_submission(
        STRATEGY,
        as_of_session=s1,
        risk_run_id=str(first_run),
        settings=load_settings(),
        execution_service=ScriptedExecutionService(["reject"]),
        trigger_source="day1",
    )
    del first
    # leave an open (paused) operation behind: a never-sent one on a second evaluation of day 1
    second_run, _ = seed_batch(DEFAULT_BATCH[1:2], session_date=s1)
    with session_scope(load_settings()) as session:
        seed_verified_basis(session, risk_run_id=second_run)
    run_paper_order_submission(
        STRATEGY,
        as_of_session=s1,
        risk_run_id=str(second_run),
        settings=load_settings(),
        execution_service=ScriptedExecutionService(["accept"]),
        price_source=ScriptedPriceSource([PriceFailure.PRICE_LOOKUP_FAILED]),
        trigger_source="day1b",
    )
    finish_jobs()
    assert operation_row().state == "paused"

    monkeypatch.setattr(clock, "now_utc", lambda: et(2025, 12, 4, 10, 0))
    next_run, _ = seed_batch(DEFAULT_BATCH[2:3], session_date=s2)
    with session_scope(load_settings()) as session:
        seed_verified_basis(session, risk_run_id=next_run)
    service = ScriptedExecutionService(["reject"])
    report = run_paper_order_submission(
        STRATEGY,
        as_of_session=s2,
        risk_run_id=str(next_run),
        settings=load_settings(),
        execution_service=service,
        trigger_source="day2",
    )

    assert service.post_attempts == 1
    assert report.result_summary["operation"]["state"] == "completed"
    with session_scope(load_settings()) as session:
        old = session.execute(
            select(ExecutionOperation).where(ExecutionOperation.risk_run_id == second_run)
        ).scalar_one()
    assert (old.state, old.reason) == ("terminated", "evaluation_superseded")


# ---------------------------------------------------------------------------
# S3-R4: intent identity and action justification (A1-A6, N1-N4)
# ---------------------------------------------------------------------------


def manifest(digest: str = "bars-d1", *, as_of: str = "2024-01-05", settings_digest: str = "sd1") -> dict[str, Any]:
    """An evaluation manifest whose RESOLVED as-of bound (``as_of``) is time-derived: a re-run
    over the same data yields the same decision inputs whatever that bound is."""

    return {
        "version": 1,
        "settings_digest": settings_digest,
        "requests": [
            {
                "kind": "bars_for_sessions",
                "params": {
                    "symbol": "AAPL",
                    "n_sessions": 50,
                    "as_of": as_of,
                    "exchange": "XNYS",
                    "adjusted": True,
                    "provider": "polygon",
                },
                "digest": digest,
                "count": 50,
            }
        ],
    }


def settle(
    ticker: str,
    status: OrderLifecycleState,
    *,
    side: str = "buy",
    fills: str | None = None,
    synced: bool = True,
) -> uuid.UUID:
    """Move the strategy's latest order of (ticker, side) to a local terminal / working state and
    ingest ``fills`` shares (direct writes: the sync Job's effect, arranged explicitly)."""

    from trading_platform.db.models import PaperFill

    with session_scope(load_settings()) as session:
        order = session.execute(
            select(PaperOrder)
            .join(Symbol, Symbol.id == PaperOrder.symbol_id)
            .where(Symbol.ticker == ticker, PaperOrder.side == side)
            .order_by(PaperOrder.created_at.desc())
        ).scalars().first()
        assert order is not None, (ticker, side)
        order.status = status
        order.broker_status = {
            OrderLifecycleState.FILLED: "filled",
            OrderLifecycleState.EXPIRED: "expired",
            OrderLifecycleState.CANCELED: "canceled",
            OrderLifecycleState.PARTIALLY_FILLED: "partially_filled",
        }.get(status, "new")
        order.last_broker_update_at = datetime.now(UTC)
        order.last_synced_at = datetime.now(UTC) if synced else None
        if fills is not None:
            session.add(
                PaperFill(
                    paper_order_id=order.id,
                    symbol_id=order.symbol_id,
                    broker_fill_id=f"fill-{uuid.uuid4().hex[:12]}",
                    broker_order_id=order.broker_order_id or "none",
                    side=side,
                    quantity=Decimal(fills),
                    price=Decimal("120"),
                    filled_at=datetime.now(UTC),
                    broker_payload={},
                )
            )
        order_id = order.id
    return order_id


def evaluation(
    batch: Sequence[tuple[str, str, str]],
    *,
    session_date: date = SESSION,
    side: str = "long",
    digest: str = "bars-d1",
    as_of: str | None = None,
    positions: Sequence[tuple[str, str]] = (),
    verified: bool = True,
    **basis_kwargs: Any,
) -> uuid.UUID:
    """A fresh risk evaluation (new run id) and, when ``verified``, the persisted records that
    verify its basis against everything executed so far."""

    run_id, _events = seed_batch(
        batch,
        session_date=session_date,
        side=side,
        manifest=manifest(digest, as_of=as_of or session_date.isoformat()),
    )
    if verified:
        with session_scope(load_settings()) as session:
            seed_verified_basis(session, risk_run_id=run_id, positions=positions, **basis_kwargs)
    return run_id


def put_position(ticker: str, quantity: str) -> None:
    """The sync's position derivation, arranged directly: the strategy's open position."""

    from trading_platform.db.models import Position

    with session_scope(load_settings()) as session:
        strategy_pk = session.execute(select(StrategyRun.strategy_id).limit(1)).scalar_one()
        symbol_id = session.execute(select(Symbol.id).where(Symbol.ticker == ticker)).scalar_one()
        position = session.execute(
            select(Position).where(Position.strategy_id == strategy_pk, Position.symbol_id == symbol_id)
        ).scalars().first()
        if position is None:
            session.add(
                Position(
                    strategy_id=strategy_pk,
                    symbol_id=symbol_id,
                    status="open",
                    quantity=Decimal(quantity),
                    average_entry_price=Decimal("120"),
                    cost_basis=Decimal(quantity) * Decimal("120"),
                )
            )
        else:
            position.quantity = Decimal(quantity)
            position.status = "open" if Decimal(quantity) != 0 else "closed"


def end_all_open_operations() -> None:
    finish_jobs()
    with session_scope(load_settings()) as session:
        for operation in session.execute(
            select(ExecutionOperation).where(
                ExecutionOperation.state.in_(["running", "paused", "requires_reevaluation"])
            )
        ).scalars():
            end_operation(session, operation.id, operator_reason="test end", actor="pytest")


def dispositions(report: Any) -> list[tuple[str, str, str]]:
    return [
        (d["symbol"], d["side"], d["disposition"])
        for d in report.result_summary.get("candidate_dispositions", [])
    ]


def test_repeated_evaluation_against_unchanged_inputs_is_a_replay_zero_post(migrated_paper_db: str) -> None:  # noqa: F811
    """A1: run 1 submits intent 1 (accepted, expires unfilled). After sync + clean reconciliation,
    two re-evaluations with identical data, settings, risk policy and portfolio (different run ids,
    wall-clock times and resolved as-of bounds) both return noop_existing_orders with
    replay_of_earlier_decision and zero POST."""
    first = evaluation(DEFAULT_BATCH[:1], verified=False)
    _start(ScriptedExecutionService(["accept"]), risk_run_id=first)
    first_intent = operation_intent_ids()[0]
    settle("AAPL", OrderLifecycleState.EXPIRED)
    end_all_open_operations()

    for n in range(2):
        again = evaluation(DEFAULT_BATCH[:1], as_of=f"2024-01-0{6 + n}")
        service = ScriptedExecutionService(["accept"])
        assert _validate(again)["risk_run_id"] == str(again)  # the submit gate accepts the request
        report = _start(service, risk_run_id=again)
        assert report.result_summary["action"] == "noop_existing_orders"
        assert dispositions(report) == [("AAPL", "buy", "replay_of_earlier_decision")]
        assert report.result_summary["candidate_dispositions"][0]["earlier_intent_id"] == str(first_intent)
        assert service.post_attempts == 0
    assert count(PaperOrder) == 1


def test_repeated_evaluation_after_a_broker_rejection_is_also_a_replay(migrated_paper_db: str) -> None:  # noqa: F811
    first = evaluation(DEFAULT_BATCH[:1], verified=False)
    _start(ScriptedExecutionService(["reject"]), risk_run_id=first)  # a completed operation
    finish_jobs()

    for n in range(2):
        again = evaluation(DEFAULT_BATCH[:1], as_of=f"2024-01-0{6 + n}")
        service = ScriptedExecutionService(["accept"])
        report = _start(service, risk_run_id=again)
        assert report.result_summary["action"] == "noop_existing_orders"
        assert dispositions(report) == [("AAPL", "buy", "replay_of_earlier_decision")]
        assert service.post_attempts == 0


def operation_intent_ids() -> list[uuid.UUID]:
    with session_scope(load_settings()) as session:
        return list(
            session.execute(
                select(ExecutionOperationIntent.id).order_by(ExecutionOperationIntent.created_at)
            ).scalars()
        )


def test_new_evaluation_after_a_verified_fill_needs_the_synced_and_reconciled_basis(migrated_paper_db: str) -> None:  # noqa: F811
    """A2: a start on an evaluation made before the sync -> evaluation_basis_unverified
    (predates_executions); after sync without a standalone reconciliation -> reconciliation_missing;
    after sync + clean reconciliation the held symbol is rejected (zero POST)."""
    first = evaluation(DEFAULT_BATCH[:1], verified=False)
    _start(ScriptedExecutionService(["accept"]), risk_run_id=first)
    settle("AAPL", OrderLifecycleState.FILLED, fills="10")
    end_all_open_operations()

    predating = evaluation(DEFAULT_BATCH[:1], digest="bars-d2", verified=False)
    assert _conflict(predating).code == "evaluation_basis_unverified"
    assert _conflict(predating).detail["reason"] == "predates_executions"
    blocked = _start(ScriptedExecutionService(["accept"]), risk_run_id=predating)
    assert blocked.result_summary["action"] == "blocked_evaluation_basis_unverified"
    assert blocked.result_summary["detail"] == "predates_executions"

    unreconciled = evaluation(
        DEFAULT_BATCH[:1], digest="bars-d2", positions=[("AAPL", "10")], with_reconciliation=False
    )
    assert _conflict(unreconciled).detail["reason"] == "reconciliation_missing"
    blocked = _start(ScriptedExecutionService(["accept"]), risk_run_id=unreconciled)
    assert blocked.result_summary["detail"] == "reconciliation_missing"

    verified = evaluation(DEFAULT_BATCH[:1], digest="bars-d2", positions=[("AAPL", "10")])
    service = ScriptedExecutionService(["accept"])
    assert _validate(verified)["risk_run_id"] == str(verified)
    report = _start(service, risk_run_id=verified)
    assert report.result_summary["action"] == "noop_existing_orders"
    assert dispositions(report) == [("AAPL", "buy", "duplicate_open_position")]
    assert service.post_attempts == 0


def test_partial_fills_remainder_is_not_pursued_and_the_ingestion_gap_is_refused(migrated_paper_db: str) -> None:  # noqa: F811
    """A3: a buy of 10 fills 4 and expires: basis position 4, the entry is rejected (held), the
    remainder 6 is not pursued (TL-11), position 4 is present in the risk state, zero POST. A
    fill-ingestion gap (broker filled 4, local 3) -> fills_not_ingested."""
    from trading_platform.db.models import Position
    from trading_platform.services.portfolio import PortfolioService

    first = evaluation(DEFAULT_BATCH[:1], verified=False)
    _start(ScriptedExecutionService(["accept"]), risk_run_id=first)
    order_id = settle("AAPL", OrderLifecycleState.EXPIRED, fills="4")
    end_all_open_operations()
    with session_scope(load_settings()) as session:
        strategy_pk = session.get(StrategyRun, first).strategy_id
        symbol_id = session.execute(select(Symbol.id).where(Symbol.ticker == "AAPL")).scalar_one()
        session.add(
            Position(
                strategy_id=strategy_pk,
                symbol_id=symbol_id,
                status="open",
                quantity=Decimal("4"),
                average_entry_price=Decimal("120"),
                cost_basis=Decimal("480"),
            )
        )

    gap = evaluation(
        DEFAULT_BATCH[:1],
        digest="bars-d2",
        positions=[("AAPL", "3")],
        broker_filled_qty={order_id: "5"},  # the broker says 5, local ingestion has 4
    )
    assert _conflict(gap).detail["reason"] == "fills_not_ingested"

    held = evaluation(DEFAULT_BATCH[:1], digest="bars-d3", positions=[("AAPL", "4")])
    service = ScriptedExecutionService(["accept"])
    report = _start(service, risk_run_id=held)
    assert dispositions(report) == [("AAPL", "buy", "duplicate_open_position")]
    assert service.post_attempts == 0
    assert count(PaperOrder) == 1  # the unfilled remainder is not topped up
    with session_scope(load_settings()) as session:
        state = PortfolioService(load_settings()).load_state(session, strategy_id=STRATEGY, as_of_session=SESSION)
    assert "AAPL" in state.open_symbols and state.position_count == 1  # position 4 in the risk state


def test_a_partially_filled_order_still_working_blocks_submission(migrated_paper_db: str) -> None:  # noqa: F811
    first = evaluation(DEFAULT_BATCH[:1], verified=False)
    _start(ScriptedExecutionService(["partial"]), risk_run_id=first)
    settle("AAPL", OrderLifecycleState.PARTIALLY_FILLED, fills="4", synced=True)
    end_all_open_operations()

    error = _conflict()

    assert error.code == "working_order_commitments_unaccounted"


def test_partial_exit_in_the_same_evaluation_session_is_action_already_submitted(migrated_paper_db: str) -> None:  # noqa: F811
    """A3 PARTIAL EXIT (TL-10): a sell of 10 fills 6 and expires; a new evaluation of the SAME
    session (corrected bars) -> action_already_submitted, zero POST, position 4 preserved."""
    earlier_session = date(2024, 1, 4)
    seed_buy = evaluation(DEFAULT_BATCH[:1], session_date=earlier_session, verified=False)
    _start(ScriptedExecutionService(["accept"]), risk_run_id=seed_buy, session_date=earlier_session)
    settle("AAPL", OrderLifecycleState.FILLED, fills="10")
    put_position("AAPL", "10")
    end_all_open_operations()

    exit_run = evaluation(DEFAULT_BATCH[:1], side="exit", positions=[("AAPL", "10")])
    service = ScriptedExecutionService(["accept"])
    _start(service, risk_run_id=exit_run)
    assert service.submitted_intents[0].side.value == "sell"
    settle("AAPL", OrderLifecycleState.EXPIRED, side="sell", fills="6")
    put_position("AAPL", "4")
    end_all_open_operations()

    corrected = evaluation(
        [("AAPL", "4", "120")], side="exit", digest="bars-corrected", positions=[("AAPL", "4")]
    )
    again = ScriptedExecutionService(["accept"])
    report = _start(again, risk_run_id=corrected)

    assert dispositions(report) == [("AAPL", "sell", "action_already_submitted")]
    assert again.post_attempts == 0


def test_end_with_nothing_sent_does_not_consume_the_allowance(migrated_paper_db: str) -> None:  # noqa: F811
    """A4(i): End with nothing sent, unchanged inputs -> the new evaluation is not a replay and the
    allowance is not consumed: its intent is sent through a new operation."""
    first = evaluation(DEFAULT_BATCH[:1], verified=False)
    paused = _start(
        ScriptedExecutionService(["accept"]),
        risk_run_id=first,
        price_source=ScriptedPriceSource([PriceFailure.PRICE_LOOKUP_FAILED]),
    )
    assert paused.result_summary["operation"]["reason"] == "price_unavailable"
    assert count(PaperOrder) == 0
    end_all_open_operations()

    again = evaluation(DEFAULT_BATCH[:1], as_of="2024-01-08", verified=False)
    service = ScriptedExecutionService(["accept"])
    report = _start(service, risk_run_id=again)

    assert service.post_attempts == 1
    assert report.result_summary["operation"]["state"] == "paused"
    assert count(ExecutionOperation) == 2


def test_end_after_one_fill_then_verified_evaluation_sends_only_justified_candidates(migrated_paper_db: str) -> None:  # noqa: F811
    """A4(ii) + A6 mixed run: after AAPL filled (End cancelled MSFT) the verified evaluation
    refuses the held AAPL and sends the never-sent MSFT; the run summary lists AAPL's disposition
    alongside MSFT's new intent."""
    first = evaluation(DEFAULT_BATCH[:2], verified=False)
    _start(ScriptedExecutionService(["accept"]), risk_run_id=first)
    settle("AAPL", OrderLifecycleState.FILLED, fills="10")
    end_all_open_operations()

    # corrected bars for the same session: AAPL (already submitted) and MSFT (never sent)
    second = evaluation(
        [("AAPL", "11", "120"), ("MSFT", "5", "300")], digest="bars-corrected", positions=[("AAPL", "10")]
    )
    service = ScriptedExecutionService(["accept"])
    report = _start(service, risk_run_id=second)

    assert [i.symbol for i in service.submitted_intents] == ["MSFT"]
    assert dispositions(report) == [("AAPL", "buy", "duplicate_open_position")]
    assert report.result_summary["submitted_count"] == 1
    assert report.result_summary["operation"]["state"] == "paused"


def test_corrected_bars_after_a_submission_are_action_already_submitted(migrated_paper_db: str) -> None:  # noqa: F811
    """A4(iii): corrected bars for the same evaluation session after a submission -> new
    fingerprints, but action_already_submitted for the (symbol, side) already submitted, zero POST."""
    first = evaluation(DEFAULT_BATCH[:1], verified=False)
    _start(ScriptedExecutionService(["accept"]), risk_run_id=first)
    settle("AAPL", OrderLifecycleState.EXPIRED)
    end_all_open_operations()

    corrected = evaluation([("AAPL", "12", "120")], digest="bars-corrected")
    service = ScriptedExecutionService(["accept"])
    report = _start(service, risk_run_id=corrected)

    assert dispositions(report) == [("AAPL", "buy", "action_already_submitted")]
    assert service.post_attempts == 0


def test_completed_evaluation_replay_is_noop_existing_orders_zero_post(migrated_paper_db: str) -> None:  # noqa: F811
    """A5."""
    run = evaluation(DEFAULT_BATCH[:2], verified=False)
    _start(ScriptedExecutionService(["reject", "reject"]), risk_run_id=run)
    finish_jobs()
    service = ScriptedExecutionService(["accept"])

    report = run_paper_session(
        STRATEGY,
        as_of_session=SESSION,
        risk_run_id=str(run),
        settings=load_settings(),
        execution_service=service,
    )

    assert report.action == "noop_existing_orders"
    assert service.post_attempts == 0
    assert count(ExecutionOperation) == 1


def test_broker_rejected_action_consumes_allowance_despite_changed_fingerprint(migrated_paper_db: str) -> None:  # noqa: F811
    """A6 (round 6): intent 1 (buy X) is rejected at submission (4xx). A new evaluation of the SAME
    session with corrected bars (changed fingerprint, portfolio still flat) -> action_already_submitted;
    with an unchanged fingerprint -> replay_of_earlier_decision; a mixed run records X's disposition
    alongside Y's new intent; and an intent of the same key that was proven not sent (cancelled_unsent
    after End) does not consume the allowance: the new evaluation sends it."""
    first = evaluation([("AAPL", "10", "120")], verified=False)
    _start(ScriptedExecutionService(["reject"]), risk_run_id=first)  # completed: rejected
    finish_jobs()

    changed = evaluation([("AAPL", "10", "120")], digest="bars-corrected")
    changed_service = ScriptedExecutionService(["accept"])
    report = _start(changed_service, risk_run_id=changed)
    assert dispositions(report) == [("AAPL", "buy", "action_already_submitted")]
    assert changed_service.post_attempts == 0

    unchanged = evaluation([("AAPL", "10", "120")], as_of="2024-01-09")
    report = _start(ScriptedExecutionService(["accept"]), risk_run_id=unchanged)
    assert dispositions(report) == [("AAPL", "buy", "replay_of_earlier_decision")]

    mixed = evaluation([("AAPL", "10", "120"), ("MSFT", "5", "300")], digest="bars-corrected-2")
    mixed_service = ScriptedExecutionService(["accept"])
    report = _start(mixed_service, risk_run_id=mixed)
    assert [i.symbol for i in mixed_service.submitted_intents] == ["MSFT"]
    assert dispositions(report) == [("AAPL", "buy", "action_already_submitted")]
    assert report.result_summary["submitted_count"] == 1


def test_a_proven_not_sent_intent_of_the_same_key_does_not_consume_the_allowance(migrated_paper_db: str) -> None:  # noqa: F811
    first = evaluation([("AAPL", "10", "120")], verified=False)
    paused = _start(ScriptedExecutionService(["not_sent"]), risk_run_id=first)  # registered, never sent
    assert paused.result_summary["operation"]["reason"] == "broker_unavailable"
    end_all_open_operations()  # the unsent intent becomes cancelled_unsent

    again = evaluation([("AAPL", "10", "120")], digest="bars-corrected")
    service = ScriptedExecutionService(["accept"])
    report = _start(service, risk_run_id=again)

    assert [i.symbol for i in service.submitted_intents] == ["AAPL"]
    assert report.result_summary.get("candidate_dispositions") == []


def test_legacy_order_without_attempts_is_never_proven_not_sent(migrated_paper_db: str) -> None:  # noqa: F811
    """A Phase 20 order with zero attempt rows and no operation is NEVER proven not sent: a FILLED
    one consumes the allowance and is a replay candidate; a SUBMISSION_FAILED one counts as
    reached for the allowance (so a follow-up evaluation cannot retry it)."""
    from tests.test_paper_execution import _seed_existing_paper_order

    run, events = seed_batch(DEFAULT_BATCH[:1], manifest=manifest())
    _seed_existing_paper_order(
        risk_run_id=run,
        risk_event_id=events["AAPL"],
        symbol="AAPL",
        session_date=SESSION,
        status="filled",
        broker_status="filled",
        last_synced_at=datetime(2024, 1, 5, 15, 5, tzinfo=UTC),
    )
    follow = evaluation(DEFAULT_BATCH[:1], digest="bars-corrected")
    service = ScriptedExecutionService(["accept"])
    report = _start(service, risk_run_id=follow)
    assert dispositions(report) == [("AAPL", "buy", "action_already_submitted")]
    assert service.post_attempts == 0


def test_legacy_submission_failed_order_is_not_retried_by_a_followup(migrated_paper_db: str) -> None:  # noqa: F811
    from tests.test_paper_execution import _seed_existing_paper_order

    run, events = seed_batch(DEFAULT_BATCH[:1], manifest=manifest())
    _seed_existing_paper_order(
        risk_run_id=run,
        risk_event_id=events["AAPL"],
        symbol="AAPL",
        session_date=SESSION,
        status="submission_failed",
        broker_order_id=None,
        broker_status=None,
        last_submission_error="timed out",
    )
    follow = evaluation(DEFAULT_BATCH[:1])
    service = ScriptedExecutionService(["accept"])

    report = _start(service, risk_run_id=follow)

    assert dispositions(report) == [("AAPL", "buy", "action_already_submitted")]
    assert service.post_attempts == 0


def test_changed_risk_configuration_alone_never_proves_a_new_intent(  # noqa: F811
    migrated_paper_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N1(a): after an accepted-then-expired-unfilled buy, a change of the risk-limit configuration
    alone -> a different fingerprint, but action_already_submitted, zero POST."""
    first = evaluation(DEFAULT_BATCH[:1], verified=False)
    _start(ScriptedExecutionService(["accept"]), risk_run_id=first)
    settle("AAPL", OrderLifecycleState.EXPIRED)
    end_all_open_operations()
    monkeypatch.setattr(submit_orders_module, "risk_config_digest", lambda settings, strategy_id: "other-limits")
    again = evaluation(DEFAULT_BATCH[:1])
    service = ScriptedExecutionService(["accept"])

    report = _start(service, risk_run_id=again)

    assert dispositions(report) == [("AAPL", "buy", "action_already_submitted")]
    assert service.post_attempts == 0


def test_unrelated_portfolio_change_changes_the_digest_but_not_the_allowance(migrated_paper_db: str) -> None:  # noqa: F811
    """N1(c): another symbol's position was closed by a later sync (portfolio digest changes); the
    (session, symbol, side) already submitted -> action_already_submitted, zero POST."""
    first = evaluation(DEFAULT_BATCH[:1], positions=[("ZZZ", "5")])
    first_report = _start(ScriptedExecutionService(["accept"]), risk_run_id=first)
    assert first_report.result_summary["submitted_count"] == 1
    settle("AAPL", OrderLifecycleState.EXPIRED)
    end_all_open_operations()
    again = evaluation(DEFAULT_BATCH[:1], positions=[])  # ZZZ closed
    service = ScriptedExecutionService(["accept"])

    report = _start(service, risk_run_id=again)

    assert dispositions(report) == [("AAPL", "buy", "action_already_submitted")]
    assert service.post_attempts == 0


def test_justified_later_session_exit_sends_exactly_one_whole_position_sell(migrated_paper_db: str) -> None:  # noqa: F811
    """N2: a verified held position and an evaluation of a LATER session whose exit rule fires ->
    exactly one sell POST of the whole position, a new intent whose prior_execution_refs name the
    buy; a second start on that risk run -> noop_existing_orders; corrected bars of that later
    session -> action_already_submitted, zero further POST."""
    buy_session, exit_session = date(2024, 1, 4), SESSION
    buy_run = evaluation(DEFAULT_BATCH[:1], session_date=buy_session, verified=False)
    _start(ScriptedExecutionService(["accept"]), risk_run_id=buy_run, session_date=buy_session)
    buy_order = settle("AAPL", OrderLifecycleState.FILLED, fills="10")
    put_position("AAPL", "10")
    end_all_open_operations()

    exit_run = evaluation(DEFAULT_BATCH[:1], session_date=exit_session, side="exit", positions=[("AAPL", "10")])
    service = ScriptedExecutionService(["accept"])
    report = _start(service, risk_run_id=exit_run)

    assert [(i.symbol, i.side.value, i.quantity) for i in service.submitted_intents] == [
        ("AAPL", "sell", Decimal("10.000000"))
    ]
    with session_scope(load_settings()) as session:
        sell_intent = session.execute(
            select(ExecutionOperationIntent).join(
                ExecutionOperation, ExecutionOperation.id == ExecutionOperationIntent.operation_id
            ).where(ExecutionOperation.risk_run_id == exit_run)
        ).scalar_one()
    assert str(buy_order) in sell_intent.prior_execution_refs
    assert report.result_summary["operation"]["state"] == "paused"

    again = _start(ScriptedExecutionService(["accept"]), risk_run_id=exit_run)
    assert again.result_summary["action"] == "noop_existing_orders"

    settle("AAPL", OrderLifecycleState.FILLED, side="sell", fills="10")
    put_position("AAPL", "0")
    end_all_open_operations()
    corrected = evaluation(
        DEFAULT_BATCH[:1], session_date=exit_session, side="exit", digest="bars-corrected", positions=[]
    )
    refused = ScriptedExecutionService(["accept"])
    report = _start(refused, risk_run_id=corrected)
    assert refused.post_attempts == 0
    assert dispositions(report) in (
        [("AAPL", "sell", "action_already_submitted")],
        [("AAPL", "sell", "no_open_position")],
    )


def test_unresolved_earlier_submission_blocks_despite_a_changed_fingerprint(migrated_paper_db: str) -> None:  # noqa: F811
    """N3: an in-doubt buy on X (timeout, never found) plus a new evaluation with changed data ->
    409 outcome_unresolved at submit and blocked_outcome_unresolved at run time; a candidate on a
    different symbol Y is blocked too; no intent_version + 1; zero POST."""
    first = evaluation([("AAPL", "10", "120")], verified=False)
    with pytest.raises(AmbiguousOrderSubmissionError):
        _start(ScriptedExecutionService(["ambiguous"]), risk_run_id=first)
    end_all_open_operations()

    changed = evaluation([("MSFT", "5", "300")], digest="bars-changed", verified=True)
    assert _conflict(changed).code == "outcome_unresolved"
    service = ScriptedExecutionService(["accept"])
    report = _start(service, risk_run_id=changed)
    assert report.result_summary["action"] == "blocked_outcome_unresolved"
    assert service.post_attempts == 0
    with session_scope(load_settings()) as session:
        assert session.execute(select(func.max(PaperOrder.intent_version))).scalar_one() == 1
        order = session.execute(select(PaperOrder)).scalar_one()
        assert order.status == OrderLifecycleState.UNKNOWN


# ---------------------------------------------------------------------------
# D-26 / H-2 / N4: re-running the loop on a paused operation (the Continue mechanics of 20.1-16)
# ---------------------------------------------------------------------------


def seed_clean_reconciliation_after_now() -> None:
    """A fresh clean standalone reconciliation completed after every Job of the session."""

    from datetime import timedelta

    from trading_platform.db.models import AccountReconciliationRun

    with session_scope(load_settings()) as session:
        session.add(
            AccountReconciliationRun(
                trigger_source="job",
                status="succeeded",
                completed_at=datetime.now(UTC) + timedelta(minutes=5),
                blocks_execution=False,
                unresolved_reasons=[],
            )
        )


def continue_operation(service: ExecutionService, *, price_source: Any = None) -> Any:
    """What 20.1-16's Continue does around the shared loop: take the paused operation back
    (paused -> running), take execution authority under the session lock (S1 takeover) and run the
    SAME permission-checked, guarded loop with ``continuation=True``."""

    from datetime import timedelta

    from trading_platform.services.concurrency_guard import session_run_lock
    from trading_platform.services.execution.operations import (
        Fence,
        acquire_execution,
        begin_continuation,
    )
    from trading_platform.strategies.registry import build_default_registry

    settings = load_settings()
    operation = operation_row()
    with session_scope(settings) as session:
        job = Job(
            job_type="paper-session",
            payload={"mode": "continue", "operation_id": str(operation.id)},
            status=JobStatus.RUNNING,
            lease_owner="continue-worker",
            lease_expires_at=datetime.now(UTC) + timedelta(days=1),
        )
        session.add(job)
        session.flush()
        job_id = job.id
        begin_continuation(session, operation.id, job_id)
    metadata = build_default_registry(settings).resolve(STRATEGY).metadata
    run_id = submit_orders_module._create_paper_execution_run(
        settings,
        metadata,
        trigger_source="continue",
        as_of_session=operation.as_of_session,
        requested_risk_run_id=str(operation.risk_run_id),
        job_id=job_id,
    )
    with session_run_lock(strategy_id=STRATEGY, session_date=operation.as_of_session, settings=settings) as lock:
        acquisition = acquire_execution(operation.id, job_id, lock, settings=settings)
        assert not acquisition.paused
        with session_scope(settings) as session:
            strategy_row = session.get(ExecutionOperation, operation.id).strategy_id
        context = submit_orders_module._ExecutionContext(
            settings=settings,
            logger=submit_orders_module.get_logger("tests.continue"),
            strategy_id=STRATEGY,
            strategy_row_id=strategy_row,
            as_of_session=operation.as_of_session,
            risk_run_id=operation.risk_run_id,
            run_id=run_id,
            trigger_source="continue",
            fence=Fence(operation.id, acquisition.epoch, job_id),
            lease_owner="continue-worker",
            broker_execution=service,
            price_source=price_source or FreshPriceSource(),
            failure_threshold=settings.execution.safety.repeated_failure_threshold,
        )
        return submit_orders_module._run_operation_loop(context, continuation=True)


def test_earlier_fill_does_not_request_reevaluation_and_intent_two_sent_unchanged(migrated_paper_db: str) -> None:  # noqa: F811
    """D-26/H-2: after intent 1 fills (cash reduced as planned) and sync + clean reconciliation, the
    continuation finds the manifest matching, intent 2 passes revalidate_pinned_intent against the
    refreshed portfolio and is sent UNCHANGED with its original client_order_id."""
    from trading_platform.db.models import AccountSnapshot

    run, _ = seed_batch(DEFAULT_BATCH[:2], manifest=manifest())
    first = ScriptedExecutionService(["accept", "accept"])
    report = _start(first, risk_run_id=run)
    operation_id = uuid.UUID(report.result_summary["operation"]["id"])
    with session_scope(load_settings()) as session:
        second_intent = session.execute(
            select(ExecutionOperationIntent).where(ExecutionOperationIntent.paper_order_id.is_(None))
        ).scalar_one()
        planned_client_order_id = second_intent.client_order_id
        planned_quantity = second_intent.quantity
    settle("AAPL", OrderLifecycleState.FILLED, fills="10")
    put_position("AAPL", "10")
    with session_scope(load_settings()) as session:  # cash reduced by the AAPL fill (10 x 120)
        session.add(
            AccountSnapshot(
                snapshot_source="broker_sync",
                snapshot_at=datetime.now(UTC),
                cash=Decimal("98800"),
                gross_exposure=Decimal("1200"),
                total_equity=Decimal("100000"),
                buying_power=Decimal("98800"),
                open_positions=1,
            )
        )
    finish_jobs()
    seed_clean_reconciliation_after_now()
    resumed = ScriptedExecutionService(["accept"])

    state = continue_operation(resumed)

    assert [i.client_order_id for i in resumed.submitted_intents] == [planned_client_order_id]
    assert resumed.submitted_intents[0].quantity == planned_quantity  # unchanged: no resize
    assert state.final_state.value == "paused"  # the continuation's own accepted order pauses again
    assert state.final_reason == "working_order_commitments_unaccounted"
    assert operation_row(operation_id).state == "paused"
    assert first.post_attempts == 1  # intent 1 was never resent


def test_cash_shortfall_after_a_fill_requires_reevaluation_insufficient_cash(migrated_paper_db: str) -> None:  # noqa: F811
    from trading_platform.db.models import AccountSnapshot

    run, _ = seed_batch(DEFAULT_BATCH[:2], manifest=manifest())
    _start(ScriptedExecutionService(["accept", "accept"]), risk_run_id=run)
    settle("AAPL", OrderLifecycleState.FILLED, fills="10")
    put_position("AAPL", "10")
    with session_scope(load_settings()) as session:
        session.add(
            AccountSnapshot(
                snapshot_source="broker_sync",
                snapshot_at=datetime.now(UTC),
                cash=Decimal("100"),  # a forced shortfall for MSFT (5 x 300)
                gross_exposure=Decimal("1200"),
                total_equity=Decimal("1300"),
                buying_power=Decimal("100"),
                open_positions=1,
            )
        )
    finish_jobs()
    seed_clean_reconciliation_after_now()
    resumed = ScriptedExecutionService(["accept"])

    state = continue_operation(resumed)

    assert resumed.post_attempts == 0
    assert state.final_state.value == "requires_reevaluation"
    assert state.final_reason == "risk_limit_failed:insufficient_cash"
    operation = operation_row()
    assert (operation.state, operation.reason) == (
        "requires_reevaluation",
        "risk_limit_failed:insufficient_cash",
    )
    # no re-plan: the pinned identities are unchanged
    with session_scope(load_settings()) as session:
        rows = session.execute(select(ExecutionOperationIntent)).scalars().all()
    assert sorted(row.quantity for row in rows) == [Decimal("5"), Decimal("10")]


def test_price_moved_pause_then_the_same_intent_is_sent_once_when_the_price_is_back(migrated_paper_db: str) -> None:  # noqa: F811
    """N4 (PD-1): +6% pauses with nothing sent; at +2% the continuation sends the same pinned
    intent once with its original client_order_id."""
    run, _ = seed_batch(DEFAULT_BATCH[:1], manifest=manifest())
    paused = _start(
        ScriptedExecutionService(["accept"]),
        risk_run_id=run,
        price_source=ScriptedPriceSource([observation("AAPL", "127.2")]),  # +6%
    )
    operation_id = uuid.UUID(paused.result_summary["operation"]["id"])
    assert paused.result_summary["operation"]["reason"] == "price_moved_beyond_tolerance"
    assert count(PaperOrder) == 0
    with session_scope(load_settings()) as session:
        planned = session.execute(select(ExecutionOperationIntent)).scalar_one()
        original_id = planned.client_order_id
    finish_jobs()
    seed_clean_reconciliation_after_now()
    resumed = ScriptedExecutionService(["accept"])

    state = continue_operation(resumed, price_source=ScriptedPriceSource([observation("AAPL", "122.4")]))

    assert [i.client_order_id for i in resumed.submitted_intents] == [original_id]
    assert state.final_reason == "working_order_commitments_unaccounted"
    assert operation_row(operation_id).state == "paused"


def test_fresh_price_cash_failure_then_end_and_unchanged_reevaluation_sends_once(migrated_paper_db: str) -> None:  # noqa: F811
    """N4 (second half): a fresh-price insufficient_cash -> requires_reevaluation; End + an
    UNCHANGED re-evaluation with a fresh price that passes -> one POST through a new operation, with
    no data, settings or portfolio change needed."""
    from trading_platform.db.models import AccountSnapshot

    first = evaluation(DEFAULT_BATCH[:1], verified=False)
    with session_scope(load_settings()) as session:
        snapshot = AccountSnapshot(
            snapshot_source="broker_sync",
            snapshot_at=datetime.now(UTC),
            cash=Decimal("1010"),  # covers 10 x 100... but not 10 x 126 at the fresh price
            gross_exposure=Decimal("0"),
            total_equity=Decimal("1010"),
            buying_power=Decimal("1010"),
            open_positions=0,
        )
        session.add(snapshot)
    refused = _start(
        ScriptedExecutionService(["accept"]),
        risk_run_id=first,
        price_source=ScriptedPriceSource([observation("AAPL", "124")]),  # +3.3%: within tolerance
    )
    assert refused.result_summary["operation"]["reason"] == "risk_limit_failed:insufficient_cash"
    end_all_open_operations()
    with session_scope(load_settings()) as session:
        session.add(
            AccountSnapshot(
                snapshot_source="broker_sync",
                snapshot_at=datetime.now(UTC),
                cash=Decimal("100000"),
                gross_exposure=Decimal("0"),
                total_equity=Decimal("100000"),
                buying_power=Decimal("100000"),
                open_positions=0,
            )
        )

    again = evaluation(DEFAULT_BATCH[:1], as_of="2024-01-08", verified=False)
    service = ScriptedExecutionService(["accept"])
    report = _start(service, risk_run_id=again, price_source=ScriptedPriceSource([observation("AAPL", "122")]))

    assert service.post_attempts == 1
    assert report.result_summary["operation"]["state"] == "paused"
    assert count(ExecutionOperation) == 2
