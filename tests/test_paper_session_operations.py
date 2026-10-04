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
from tests.support.paper_eligibility import allow_paper_execution
from tests.support.paper_execution_seams import allow_direct_paper_execution
from tests.support.paper_ownership import seed_strategy, set_active_paper_strategy
from tests.support.price_source import FreshPriceSource, ScriptedPriceSource, observation
from tests.test_paper_execution import (
    _begin_attempt_through_bound_log,
    migrated_paper_db,  # noqa: F401  (database fixture)
)

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
            broker_order_id=f"scripted-{intent.symbol.lower()}-001",
            symbol=intent.symbol,
            side=intent.side,
            quantity=intent.quantity,
            order_type=intent.order_type,
            time_in_force=intent.time_in_force,
            status=status,
            broker_status=raw,
            submitted_at=datetime(2024, 1, 5, 14, 35, tzinfo=UTC),
            raw_payload={"id": f"scripted-{intent.symbol.lower()}-001", "status": raw},
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
) -> Any:
    return run_paper_order_submission(
        STRATEGY,
        as_of_session=SESSION,
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
