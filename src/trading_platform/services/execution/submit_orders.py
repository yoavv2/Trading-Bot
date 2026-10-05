"""Paper order submission + session orchestration + intent-decision logic.

STRUCT-04 part 2 (12-04): submission-side split of the former monolithic
`services/paper_execution.py`. Broker-state sync (orders/fills/positions/
account) lives in the sibling `sync_orders.py`; shared dataclasses and
cross-cutting helpers live in `_paper_common.py`.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, case, select, update
from sqlalchemy.orm import Session, joinedload

from trading_platform.core import clock
from trading_platform.core.logging import build_log_context, emit_structured_log, get_logger
from trading_platform.core.settings import Settings, load_settings
from trading_platform.db.models import (
    OPEN_OPERATION_STATES,
    AttemptOutcomeClass,
    ExecutionEvent,
    ExecutionOperation,
    ExecutionOperationIntent,
    IntentDisposition,
    Job,
    OperationState,
    OrderLifecycleState,
    OrderTransitionEventType,
    PaperOrder,
    RiskEvent,
    Strategy,
    StrategyRun,
    StrategyRunStatus,
    StrategyRunType,
)
from trading_platform.db.models.symbol import Symbol
from trading_platform.db.session import session_scope
from trading_platform.services import operator_controls
from trading_platform.services.active_paper_strategy import (
    BLOCKED_REASON_NOT_ACTIVE_PAPER_STRATEGY,
    lock_active_paper_strategy_shared,
)
from trading_platform.services.alpaca import (
    AlpacaClient,
    AlpacaExecutionService,
    AlpacaPriceSource,
    AmbiguousOrderSubmissionError,
    OrderNotSentError,
    OrderRejectedError,
)
from trading_platform.services.bootstrap import ensure_strategy_record
from trading_platform.services.concurrency_guard import ConcurrentRunLockedError, session_run_lock
from trading_platform.services.execution._paper_common import (
    PaperExecutionCandidate,
    PaperExecutionRunReport,
    PaperIntentDecision,
    PaperSessionPlan,
    PaperSessionRunReport,
    _broker_transition_event,
    _record_intent_decision_event,
)
from trading_platform.services.execution.attempts import (
    SubmissionClass,
    SubmissionIntentState,
    bind_attempt_log,
    classify_submission,
    load_submission_attempts,
    summarize_attempts,
)
from trading_platform.services.execution.continuation import settled_blocker
from trading_platform.services.execution.contracts import (
    ExecutionService,
    OrderIntent,
    OrderSide,
    OrderSubmissionResult,
)
from trading_platform.services.execution.idempotency import (
    DerivedOrderIdentity,
    derive_order_identity,
)
from trading_platform.services.execution.idempotency import (
    build_client_order_id as _build_client_order_id,
)
from trading_platform.services.execution.intent_identity import (
    BasisRows,
    CandidateKey,
    VersionBypassRefusedError,
    classify_candidate,
    decision_fingerprint,
    decision_inputs_digest,
    load_basis_verification_rows,
    load_earlier_intents,
    load_strategy_order_facts,
    portfolio_state_digest,
    risk_config_digest,
    unestablished_orders,
    verify_evaluation_basis,
)
from trading_platform.services.execution.operations import (
    Fence,
    OperationConflictError,
    OperationExecutorActiveError,
    OperationNotFoundError,
    OperationOpenError,
    PausedReason,
    PlannedIntent,
    ReevaluationReason,
    RiskRunAlreadyOperatedError,
    SendRefusal,
    SendRefusedError,
    acquire_execution,
    adopt_running_operation,
    authorize_send,
    begin_continuation,
    cas_update_operation,
    create_operation,
    load_intent_facts,
    next_action,
    touch_operation,
    transition,
    validate_state_reason,
)
from trading_platform.services.execution.permission import (
    PermissionOutcome,
    PermissionVerdict,
    PinnedIntent,
    PriceSource,
    check_intent_permission,
    strategy_working_orders,
)
from trading_platform.services.execution.send_guard import GuardedAttemptLog, fence_held
from trading_platform.services.execution.transition import (
    OrderTransitionRequest,
    apply_order_transition,
)
from trading_platform.services.market_data_access import latest_completed_session
from trading_platform.services.operator_controls import (
    BLOCKED_REASON_GLOBAL_KILL_SWITCH,
    ensure_strategy_control_state,
    read_trading_gate_state,
)
from trading_platform.services.reconciliation import (
    apply_reconciliation_corrections,
    load_broker_state,
    reconcile_paper_execution,
    recover_inflight_paper_orders,
)
from trading_platform.services.recovery import REQUIRED_JOB_TYPE, GateCode, strategy_recovery_status
from trading_platform.services.stale_runs import reclaim_stale_runs
from trading_platform.strategies.registry import StrategyRegistry, build_default_registry


def resolve_submission_session(
    *,
    settings: Settings,
    as_of_arg: str | None,
) -> date:
    if as_of_arg is not None:
        return date.fromisoformat(as_of_arg)
    with session_scope(settings) as session:
        latest = latest_completed_session(session, exchange=settings.market_data.calendar.exchange)
    if latest is not None:
        return latest
    return date.today() - timedelta(days=1)


def build_client_order_id(
    *,
    prefix: str,
    strategy_id: str,
    session_date: date,
    symbol: str,
    side: OrderSide | str,
    quantity: Decimal,
) -> str:
    return _build_client_order_id(
        prefix=prefix,
        strategy_id=strategy_id,
        session_date=session_date,
        symbol=symbol,
        side=side,
        quantity=quantity,
    )


def run_paper_order_submission(
    strategy_id: str,
    *,
    as_of_session: date,
    risk_run_id: str | None = None,
    trigger_source: str = "paper_orders_script",
    settings: Settings | None = None,
    registry: StrategyRegistry | None = None,
    execution_service: ExecutionService | None = None,
    job_id: uuid.UUID | None = None,
    price_source: PriceSource | None = None,
) -> PaperExecutionRunReport:
    """Lock-guarded entrypoint (LOCK-01/02/03/05): resolve pure state, then
    acquire the (strategy_id, session_date) advisory lock BEFORE any write or
    broker call. All side effects happen inside `_run_paper_order_submission_guarded`,
    which runs entirely within the lock's `with` block below.

    ``job_id`` is an opaque originating-Job identifier (D-09/D-28): when
    provided, it is written on the created execution ``StrategyRun`` in the
    same transaction that creates the run. This module imports nothing from
    ``jobs``/ -- the caller (a Job handler) owns that dependency, not this
    service.
    """
    logger = get_logger("trading_platform.paper_execution")
    resolved_settings = settings or load_settings()
    resolved_registry = registry or build_default_registry(resolved_settings)
    strategy = resolved_registry.resolve(strategy_id)
    metadata = strategy.metadata

    try:
        with session_run_lock(
            strategy_id=strategy_id,
            session_date=as_of_session,
            settings=resolved_settings,
        ):
            return _run_paper_order_submission_guarded(
                logger,
                strategy_id=strategy_id,
                metadata=metadata,
                as_of_session=as_of_session,
                risk_run_id=risk_run_id,
                trigger_source=trigger_source,
                resolved_settings=resolved_settings,
                resolved_registry=resolved_registry,
                execution_service=execution_service,
                job_id=job_id,
                price_source=price_source,
            )
    except ConcurrentRunLockedError:
        # The context manager raises before its body ever runs -- this
        # attempt made zero writes and zero broker calls (LOCK-01). The
        # caller/CLI maps this to CONCURRENT_RUN_LOCK_EXIT_CODE.
        emit_structured_log(
            logger,
            logging.WARNING,
            "paper_execution_lock_denied",
            strategy_id=strategy_id,
            session_date=as_of_session.isoformat(),
            trigger_source=trigger_source,
        )
        raise


def _run_paper_order_submission_guarded(
    logger: logging.Logger,
    *,
    strategy_id: str,
    metadata,
    as_of_session: date,
    risk_run_id: str | None,
    trigger_source: str,
    resolved_settings: Settings,
    resolved_registry: StrategyRegistry,
    execution_service: ExecutionService | None,
    job_id: uuid.UUID | None = None,
    price_source: PriceSource | None = None,
) -> PaperExecutionRunReport:
    """Guarded body -- only ever called from inside `session_run_lock`.

    Ordering is load-bearing (LOCK-03/LOCK-05): the running row below is the
    literal first persisted write for this run; stale reclaim runs
    immediately after that row exists (so it can never self-reclaim, since
    its own started_at is inside the timeout window); kill-switch/control
    state is read only after that, so those checks are provably post-lock.
    """
    run_id = _create_paper_execution_run(
        resolved_settings,
        metadata,
        trigger_source=trigger_source,
        as_of_session=as_of_session,
        requested_risk_run_id=risk_run_id,
        job_id=job_id,
    )

    # DURABILITY: this reclaim -- like every write below -- commits on its
    # own short-lived session_scope connection, never on session_run_lock's
    # dedicated connection. A mid-run crash still leaves whatever was
    # already committed (the running row, reclaimed predecessors, per-order
    # writes) durable on disk for a later run's stale-reclaim pass to find;
    # only the advisory lock itself is released immediately on connection
    # drop.
    with session_scope(resolved_settings) as session:
        reclaim_stale_runs(
            session,
            strategy_public_id=strategy_id,
            session_date=as_of_session,
            timeout_minutes=resolved_settings.execution.safety.stale_run_timeout_minutes,
            reclaiming_run_id=run_id,
        )

    control_state = ensure_strategy_control_state(
        strategy_id,
        settings=resolved_settings,
        registry=resolved_registry,
    )
    # R-Q1: kill switch, owner and owner status come from ONE gate statement.
    gate = read_trading_gate_state(resolved_settings)
    kill_switch_state = gate.kill_switch
    if kill_switch_state.is_tripped:
        report = _finalize_blocked_paper_execution_run(
            resolved_settings,
            run_id,
            strategy_id=strategy_id,
            as_of_session=as_of_session,
            requested_risk_run_id=risk_run_id,
            trigger_source=trigger_source,
            strategy_status=control_state.status,
            blocked_reason=BLOCKED_REASON_GLOBAL_KILL_SWITCH,
            action="blocked_global_kill_switch",
            message=(
                "Global kill switch is tripped; paper execution halted before broker submission begins."
            ),
            extra_details={"kill_switch": kill_switch_state.to_dict()},
        )
        emit_structured_log(
            logger,
            logging.WARNING,
            "paper_execution_blocked",
            strategy_id=strategy_id,
            run_id=report.run_id,
            session_date=as_of_session.isoformat(),
            strategy_status=control_state.status,
            kill_switch_state=kill_switch_state.state,
            blocked_reason=BLOCKED_REASON_GLOBAL_KILL_SWITCH,
            trigger_source=trigger_source,
        )
        return report
    # D-03: ownership is re-read (fresh, uncached) immediately before any
    # broker action, after the kill-switch check and before the disabled check.
    ownership_block = gate.ownership_block_for(strategy_id)
    if ownership_block is not None:
        report = _finalize_blocked_paper_execution_run(
            resolved_settings,
            run_id,
            strategy_id=strategy_id,
            as_of_session=as_of_session,
            requested_risk_run_id=risk_run_id,
            trigger_source=trigger_source,
            strategy_status=control_state.status,
            blocked_reason=BLOCKED_REASON_NOT_ACTIVE_PAPER_STRATEGY,
            action="blocked_not_active_paper_strategy",
            message=(
                f"Strategy '{strategy_id}' is not the active paper strategy "
                f"({ownership_block.value}); paper execution halted before broker submission begins."
            ),
            extra_details={
                "ownership_block": ownership_block.value,
                "active_paper_strategy": gate.owner.to_dict(),
            },
        )
        emit_structured_log(
            logger,
            logging.WARNING,
            "paper_execution_blocked",
            strategy_id=strategy_id,
            run_id=report.run_id,
            session_date=as_of_session.isoformat(),
            strategy_status=control_state.status,
            blocked_reason=BLOCKED_REASON_NOT_ACTIVE_PAPER_STRATEGY,
            ownership_block=ownership_block.value,
            trigger_source=trigger_source,
        )
        return report
    if not control_state.is_execution_enabled:
        report = _finalize_blocked_paper_execution_run(
            resolved_settings,
            run_id,
            strategy_id=strategy_id,
            as_of_session=as_of_session,
            requested_risk_run_id=risk_run_id,
            trigger_source=trigger_source,
            strategy_status=control_state.status,
            blocked_reason="strategy_disabled",
            action="blocked_strategy_disabled",
        )
        emit_structured_log(
            logger,
            logging.WARNING,
            "paper_execution_blocked",
            strategy_id=strategy_id,
            run_id=report.run_id,
            session_date=as_of_session.isoformat(),
            strategy_status=control_state.status,
            blocked_reason="strategy_disabled",
            trigger_source=trigger_source,
        )
        return report

    _update_paper_execution_run(
        resolved_settings,
        run_id,
        status=StrategyRunStatus.RUNNING,
        result_summary={
            "stage": "running",
            "strategy_id": metadata.strategy_id,
            "as_of_session": as_of_session.isoformat(),
            "requested_risk_run_id": risk_run_id,
            "strategy_status": control_state.status,
        },
    )

    owns_execution_service = execution_service is None
    broker_execution = execution_service or AlpacaExecutionService(resolved_settings.broker.alpaca)
    owns_price_source = price_source is None
    resolved_price_source = price_source or _default_price_source(resolved_settings)
    source_risk_run: StrategyRun | None = None
    strategy_row_id: uuid.UUID | None = None
    context: _ExecutionContext | None = None
    start_result: _StartResult | None = None
    loop: _LoopState | None = None
    candidates: list[PaperExecutionCandidate] = []

    try:
        with session_scope(resolved_settings) as session:
            ensure_strategy_record(session, metadata)
            source_risk_run = _resolve_source_risk_run(
                session,
                strategy_id=strategy_id,
                as_of_session=as_of_session,
                requested_risk_run_id=risk_run_id,
            )
            strategy_row_id = source_risk_run.strategy_id
            candidates = _load_submission_candidates(session, source_risk_run.id)

        # S1-R3 / D-16: the operation is created at the FIRST submission, after the run-time gates
        # passed, in one transaction that holds the ownership singleton FOR SHARE (SER).
        start_result = _prepare_start(
            resolved_settings,
            strategy_id=strategy_id,
            strategy_row_id=strategy_row_id,
            source_risk_run_id=source_risk_run.id,
            as_of_session=as_of_session,
            candidates=candidates,
            job_id=job_id,
        )
        if isinstance(start_result, _StartBlocked):
            report = _finalize_blocked_paper_execution_run(
                resolved_settings,
                run_id,
                strategy_id=strategy_id,
                as_of_session=as_of_session,
                requested_risk_run_id=risk_run_id,
                trigger_source=trigger_source,
                strategy_status=control_state.status,
                blocked_reason=start_result.reason,
                action=start_result.action,
                message=start_result.message,
                extra_details={
                    "candidate_dispositions": start_result.dispositions,
                    **start_result.extra,
                },
            )
            emit_structured_log(
                logger,
                logging.WARNING,
                "paper_execution_blocked",
                strategy_id=strategy_id,
                run_id=report.run_id,
                session_date=as_of_session.isoformat(),
                strategy_status=control_state.status,
                blocked_reason=start_result.reason,
                trigger_source=trigger_source,
            )
            return report
        if isinstance(start_result, _StartNoop):
            noop_summary: dict[str, Any] = {
                "stage": "completed",
                "action": "noop_existing_orders",
                "strategy_id": strategy_id,
                "as_of_session": as_of_session.isoformat(),
                "requested_risk_run_id": risk_run_id,
                "source_risk_run_id": str(source_risk_run.id),
                "approved_candidate_count": len(candidates),
                "submitted_count": 0,
                "existing_count": len(start_result.existing_orders),
                "reused_count": len(start_result.existing_orders),
                "versioned_count": 0,
                "skipped_by_kill_switch_count": 0,
                "skipped_by_ownership_count": 0,
                "submitted_orders": [],
                "existing_orders": start_result.existing_orders,
                "reused_orders": start_result.existing_orders,
                "versioned_orders": [],
                "skipped_by_kill_switch": [],
                "skipped_by_ownership": [],
                "candidate_dispositions": start_result.dispositions,
                "broker_provider": resolved_settings.broker.provider,
                "execution_defaults": resolved_settings.execution.model_dump(mode="json"),
            }
            report = _update_paper_execution_run(
                resolved_settings,
                run_id,
                status=StrategyRunStatus.SUCCEEDED,
                completed_at=datetime.now(UTC),
                result_summary=noop_summary,
            )
            emit_structured_log(
                logger,
                logging.INFO,
                "paper_execution_completed",
                strategy_id=strategy_id,
                run_id=report.run_id,
                session_date=as_of_session.isoformat(),
                strategy_status=control_state.status,
                trigger_source=trigger_source,
                submitted_count=0,
                existing_count=len(start_result.existing_orders),
            )
            return report

        context = _ExecutionContext(
            settings=resolved_settings,
            logger=logger,
            strategy_id=strategy_id,
            strategy_row_id=strategy_row_id,
            as_of_session=as_of_session,
            risk_run_id=source_risk_run.id,
            run_id=run_id,
            trigger_source=trigger_source,
            fence=Fence(
                operation_id=start_result.operation_id,
                epoch=start_result.epoch,
                job_id=start_result.executor_job_id,
            ),
            lease_owner=start_result.lease_owner,
            broker_execution=broker_execution,
            price_source=resolved_price_source,
            failure_threshold=resolved_settings.execution.safety.repeated_failure_threshold,
        )
        loop = _run_operation_loop(context)
        # Candidates this risk run had already realised keep reporting as existing orders.
        loop.existing_orders = [*start_result.existing_orders, *loop.existing_orders]
        loop.reused_orders = [*start_result.existing_orders, *loop.reused_orders]

        halted_mid_run = loop.halt == "kill_switch"
        halted_by_ownership = loop.halt == "ownership"
        mid_run_kill_switch = loop.kill_switch
        mid_run_ownership_block = loop.ownership_block
        operation_summary = _operation_summary(resolved_settings, start_result.operation_id)
        summary: dict[str, Any] = {
            "stage": "blocked_mid_run" if (halted_mid_run or halted_by_ownership) else "completed",
            "strategy_id": strategy_id,
            "as_of_session": as_of_session.isoformat(),
            "requested_risk_run_id": risk_run_id,
            "source_risk_run_id": str(source_risk_run.id),
            "approved_candidate_count": len(candidates),
            "submitted_count": len(loop.submitted_orders),
            "existing_count": len(loop.existing_orders),
            "reused_count": len(loop.reused_orders),
            "versioned_count": len(loop.versioned_orders),
            "skipped_by_kill_switch_count": len(loop.skipped_by_kill_switch),
            "skipped_by_ownership_count": len(loop.skipped_by_ownership),
            "submitted_orders": loop.submitted_orders,
            "existing_orders": loop.existing_orders,
            "reused_orders": loop.reused_orders,
            "versioned_orders": loop.versioned_orders,
            "skipped_by_kill_switch": loop.skipped_by_kill_switch,
            "skipped_by_ownership": loop.skipped_by_ownership,
            "rejected_orders": loop.rejected_orders,
            "candidate_dispositions": start_result.dispositions,
            "operation": operation_summary,
            "broker_provider": resolved_settings.broker.provider,
            "execution_defaults": resolved_settings.execution.model_dump(mode="json"),
        }
        halted_message: str | None = None
        if halted_mid_run and mid_run_kill_switch is not None:
            halted_message = (
                "Global kill switch tripped during session; "
                f"{len(loop.skipped_by_kill_switch)} pending candidate(s) halted before broker submission."
            )
            summary["blocked_reason"] = BLOCKED_REASON_GLOBAL_KILL_SWITCH
            summary["action"] = "blocked_mid_run_global_kill_switch"
            summary["message"] = halted_message
            summary["kill_switch"] = mid_run_kill_switch
        elif halted_by_ownership and mid_run_ownership_block is not None:
            halted_message = (
                "Strategy is no longer the active paper strategy "
                f"({mid_run_ownership_block}); "
                f"{len(loop.skipped_by_ownership)} pending candidate(s) halted before broker submission."
            )
            summary["blocked_reason"] = BLOCKED_REASON_NOT_ACTIVE_PAPER_STRATEGY
            summary["action"] = "blocked_mid_run_not_active_paper_strategy"
            summary["message"] = halted_message
            summary["ownership_block"] = mid_run_ownership_block
    except Exception as exc:
        if context is not None:
            _best_effort_pause(context)
        _update_paper_execution_run(
            resolved_settings,
            run_id,
            status=StrategyRunStatus.FAILED,
            completed_at=datetime.now(UTC),
            error_message=str(exc),
            result_summary={
                "stage": "failed",
                "strategy_id": strategy_id,
                "as_of_session": as_of_session.isoformat(),
                "requested_risk_run_id": risk_run_id,
                "source_risk_run_id": str(source_risk_run.id)
                if source_risk_run is not None
                else None,
                "strategy_status": control_state.status,
            },
        )
        logger.exception(
            "paper_execution_failed",
            extra={
                "context": build_log_context(
                    strategy_id=strategy_id,
                    run_id=str(run_id),
                    session_date=as_of_session.isoformat(),
                    strategy_status=control_state.status,
                    trigger_source=trigger_source,
                )
            },
        )
        raise
    finally:
        if owns_execution_service and hasattr(broker_execution, "close"):
            broker_execution.close()
        if owns_price_source and hasattr(resolved_price_source, "close"):
            resolved_price_source.close()

    completed_at = datetime.now(UTC)
    if halted_by_ownership and mid_run_ownership_block is not None:
        report = _finalize_mid_run_halt(
            resolved_settings,
            run_id,
            completed_at=completed_at,
            summary=summary,
            message=halted_message
            or "Strategy lost paper ownership during session; pending candidates halted before broker submission.",
        )
        emit_structured_log(
            logger,
            logging.WARNING,
            "paper_execution_blocked",
            strategy_id=strategy_id,
            run_id=report.run_id,
            session_date=as_of_session.isoformat(),
            strategy_status=control_state.status,
            blocked_reason=BLOCKED_REASON_NOT_ACTIVE_PAPER_STRATEGY,
            ownership_block=mid_run_ownership_block,
            trigger_source=trigger_source,
            submitted_count=summary["submitted_count"],
            skipped_by_ownership_count=summary["skipped_by_ownership_count"],
        )
        return report
    if halted_mid_run and mid_run_kill_switch is not None:
        report = _finalize_mid_run_halt(
            resolved_settings,
            run_id,
            completed_at=completed_at,
            summary=summary,
            message=halted_message
            or "Global kill switch tripped during session; pending candidates halted before broker submission.",
        )
        emit_structured_log(
            logger,
            logging.WARNING,
            "paper_execution_blocked",
            strategy_id=strategy_id,
            run_id=report.run_id,
            session_date=as_of_session.isoformat(),
            strategy_status=control_state.status,
            kill_switch_state=mid_run_kill_switch.get("state"),
            blocked_reason=BLOCKED_REASON_GLOBAL_KILL_SWITCH,
            trigger_source=trigger_source,
            submitted_count=summary["submitted_count"],
            skipped_by_kill_switch_count=summary["skipped_by_kill_switch_count"],
        )
        return report

    report = _update_paper_execution_run(
        resolved_settings,
        run_id,
        status=StrategyRunStatus.SUCCEEDED,
        completed_at=completed_at,
        result_summary=summary,
    )
    emit_structured_log(
        logger,
        logging.INFO,
        "paper_execution_completed",
        strategy_id=strategy_id,
        run_id=report.run_id,
        session_date=as_of_session.isoformat(),
        strategy_status=control_state.status,
        trigger_source=trigger_source,
        submitted_count=summary["submitted_count"],
        existing_count=summary["existing_count"],
    )
    return report


#: Domain action of a paper session refused by the D-15 recovery defence.
BLOCKED_ACTION_OUTCOME_UNRESOLVED = "blocked_outcome_unresolved"

#: Run-time execution blocks raised while creating the operation (zero POST; Job SUCCEEDED).
_SUBMISSION_BLOCK_ACTIONS = frozenset(
    {
        BLOCKED_ACTION_OUTCOME_UNRESOLVED,
        "blocked_evaluation_basis_unverified",
        "blocked_not_active_paper_strategy",
    }
)


def _recovery_gate_code(settings: Settings, strategy_id: str) -> str | None:
    """The unresolved-outcome gate code of ``strategy_id`` (read-only, 2 statements), or None."""

    with session_scope(settings) as session:
        gate = strategy_recovery_status(session, strategy_id).gate_code
    return gate.value if gate is not None else None


def run_paper_session(
    strategy_id: str,
    *,
    as_of_session: date,
    risk_run_id: str | None = None,
    trigger_source: str | None = None,
    settings: Settings | None = None,
    registry: StrategyRegistry | None = None,
    execution_service: ExecutionService | None = None,
    broker_client: AlpacaClient | None = None,
    job_id: uuid.UUID | None = None,
    price_source: PriceSource | None = None,
) -> PaperSessionRunReport:
    """The START mode of a paper session. Continue (20.1-16, D-19) is ``run_paper_continuation``:
    a strategy and a session are explicit here (20.1-01 pins that signature), while a continuation
    takes both from the paused operation.

    ``job_id`` (D-08/D-09) is threaded to BOTH runs this function may
    create: the internal reconciliation ``StrategyRun`` (via
    ``reconcile_paper_execution``) and the paper_execution ``StrategyRun``
    (via ``run_paper_order_submission``, including the
    ``blocked_strategy_disabled`` path). ``reconciliation_run_id`` on every
    returned ``PaperSessionRunReport`` names the internal reconciliation run
    (``None`` when reconciliation did not run this call).
    """
    logger = get_logger("trading_platform.paper_execution")
    resolved_settings = settings or load_settings()
    runner_settings = resolved_settings.execution.paper_session_runner
    resolved_strategy_id = strategy_id
    resolved_trigger_source = trigger_source or runner_settings.trigger_source
    reconciliation_report = None

    with session_scope(resolved_settings) as session:
        session_plan = _build_paper_session_plan(
            session,
            strategy_id=resolved_strategy_id,
            as_of_session=as_of_session,
            requested_risk_run_id=risk_run_id,
            failure_threshold=resolved_settings.execution.safety.repeated_failure_threshold,
            client_order_id_prefix=resolved_settings.execution.client_order_id_prefix,
        )

    control_state = ensure_strategy_control_state(
        resolved_strategy_id,
        settings=resolved_settings,
        registry=registry,
    )
    # R-Q1: kill switch and owner from ONE fresh gate statement; the owner is
    # read BEFORE load_broker_state so a non-owner makes zero broker calls.
    session_gate = read_trading_gate_state(resolved_settings)
    kill_switch_state = session_gate.kill_switch
    session_ownership_block = session_gate.ownership_block_for(resolved_strategy_id)
    existing_orders = list(session_plan.existing_orders)
    base_summary = {
        "strategy_id": resolved_strategy_id,
        "as_of_session": as_of_session.isoformat(),
        "source_risk_run_id": str(session_plan.source_risk_run_id),
        "approved_candidate_count": len(session_plan.candidates),
        "existing_count": len(session_plan.existing_orders),
        "missing_count": len(session_plan.missing_candidates),
        "existing_orders": existing_orders,
        "strategy_status": control_state.status,
        "kill_switch": kill_switch_state.to_dict(),
    }

    if session_ownership_block is not None:
        # The guarded body of run_paper_order_submission re-reads ownership and
        # creates the blocked paper_execution run; no broker call is made here.
        blocked_execution_report = run_paper_order_submission(
            resolved_strategy_id,
            as_of_session=as_of_session,
            risk_run_id=str(session_plan.source_risk_run_id),
            trigger_source=resolved_trigger_source,
            settings=resolved_settings,
            registry=registry,
            execution_service=execution_service,
            job_id=job_id,
        )
        result_summary = dict(blocked_execution_report.result_summary)
        result_summary["session_preflight"] = base_summary
        emit_structured_log(
            logger,
            logging.WARNING,
            "paper_session_blocked",
            strategy_id=resolved_strategy_id,
            run_id=blocked_execution_report.run_id,
            session_date=as_of_session.isoformat(),
            strategy_status=control_state.status,
            blocked_reason=BLOCKED_REASON_NOT_ACTIVE_PAPER_STRATEGY,
            ownership_block=session_ownership_block.value,
            trigger_source=resolved_trigger_source,
        )
        return PaperSessionRunReport(
            strategy_id=resolved_strategy_id,
            session_date=as_of_session.isoformat(),
            trigger_source=resolved_trigger_source,
            source_risk_run_id=str(session_plan.source_risk_run_id),
            action="blocked_not_active_paper_strategy",
            execution_run_id=blocked_execution_report.run_id,
            execution_status=blocked_execution_report.status,
            result_summary=result_summary,
            reconciliation_run_id=None,
        )

    if not control_state.is_execution_enabled:
        blocked_execution_report = run_paper_order_submission(
            resolved_strategy_id,
            as_of_session=as_of_session,
            risk_run_id=str(session_plan.source_risk_run_id),
            trigger_source=resolved_trigger_source,
            settings=resolved_settings,
            registry=registry,
            execution_service=execution_service,
            job_id=job_id,
        )
        result_summary = dict(blocked_execution_report.result_summary)
        result_summary["session_preflight"] = base_summary
        emit_structured_log(
            logger,
            logging.WARNING,
            "paper_session_blocked",
            strategy_id=resolved_strategy_id,
            run_id=blocked_execution_report.run_id,
            session_date=as_of_session.isoformat(),
            strategy_status=control_state.status,
            blocked_reason="strategy_disabled",
            trigger_source=resolved_trigger_source,
        )
        return PaperSessionRunReport(
            strategy_id=resolved_strategy_id,
            session_date=as_of_session.isoformat(),
            trigger_source=resolved_trigger_source,
            source_risk_run_id=str(session_plan.source_risk_run_id),
            action="blocked_strategy_disabled",
            execution_run_id=blocked_execution_report.run_id,
            execution_status=blocked_execution_report.status,
            result_summary=result_summary,
            reconciliation_run_id=None,
        )

    session_recovery_gate = _recovery_gate_code(resolved_settings, resolved_strategy_id)
    if session_recovery_gate is not None:
        # D-15 run-time defence (REC-01): a Job queued before an uncertain outcome appeared
        # must not reach the broker while that outcome is unresolved. Zero broker reads and
        # zero POSTs: the blocked paper_execution run (linked to the Job) is created and
        # finalized right here, before any broker call and without entering the submission
        # body (the 20.1-02 per-intent guard there is unchanged).
        metadata = (
            (registry or build_default_registry(resolved_settings))
            .resolve(resolved_strategy_id)
            .metadata
        )
        blocked_run_id = _create_paper_execution_run(
            resolved_settings,
            metadata,
            trigger_source=resolved_trigger_source,
            as_of_session=as_of_session,
            requested_risk_run_id=str(session_plan.source_risk_run_id),
            job_id=job_id,
        )
        blocked_execution_report = _finalize_blocked_paper_execution_run(
            resolved_settings,
            blocked_run_id,
            strategy_id=resolved_strategy_id,
            as_of_session=as_of_session,
            requested_risk_run_id=str(session_plan.source_risk_run_id),
            trigger_source=resolved_trigger_source,
            strategy_status=control_state.status,
            blocked_reason=session_recovery_gate,
            action=BLOCKED_ACTION_OUTCOME_UNRESOLVED,
            message=(
                f"An uncertain order outcome of strategy '{resolved_strategy_id}' is unresolved "
                f"({session_recovery_gate}); paper execution halted before broker submission "
                "begins."
            ),
            extra_details={"required_job_type": REQUIRED_JOB_TYPE},
        )
        result_summary = dict(blocked_execution_report.result_summary)
        result_summary["session_preflight"] = base_summary
        emit_structured_log(
            logger,
            logging.WARNING,
            "paper_session_blocked",
            strategy_id=resolved_strategy_id,
            run_id=blocked_execution_report.run_id,
            session_date=as_of_session.isoformat(),
            strategy_status=control_state.status,
            blocked_reason=session_recovery_gate,
            trigger_source=resolved_trigger_source,
        )
        return PaperSessionRunReport(
            strategy_id=resolved_strategy_id,
            session_date=as_of_session.isoformat(),
            trigger_source=resolved_trigger_source,
            source_risk_run_id=str(session_plan.source_risk_run_id),
            action=BLOCKED_ACTION_OUTCOME_UNRESOLVED,
            execution_run_id=blocked_execution_report.run_id,
            execution_status=blocked_execution_report.status,
            result_summary=result_summary,
            reconciliation_run_id=None,
        )

    if broker_client is not None or execution_service is None:
        broker_state = load_broker_state(
            settings=resolved_settings,
            broker_client=broker_client,
        )
        recovered_order_count = recover_inflight_paper_orders(
            resolved_strategy_id,
            settings=resolved_settings,
            registry=registry,
            broker_state=broker_state,
        )
        reconciliation_report = reconcile_paper_execution(
            resolved_strategy_id,
            as_of_session=as_of_session,
            settings=resolved_settings,
            registry=registry,
            broker_client=broker_client,
            broker_state=broker_state,
            recovered_order_count=recovered_order_count,
            trigger_source=f"{resolved_trigger_source}_reconciliation",
            job_id=job_id,
        )
        base_summary["reconciliation"] = reconciliation_report.to_dict()
        # Explicit corrective step (RECON-04), invoked as its own call AFTER the
        # read-only report is produced -- never inside reconcile_paper_execution itself.
        apply_reconciliation_corrections(
            resolved_strategy_id,
            report=reconciliation_report,
            settings=resolved_settings,
            registry=registry,
        )

    reconciliation_run_id = (
        reconciliation_report.run_id if reconciliation_report is not None else None
    )

    if (
        reconciliation_report is not None
        and reconciliation_report.blocks_execution
        and resolved_settings.execution.safety.block_on_unresolved_reconciliation
    ):
        emit_structured_log(
            logger,
            logging.WARNING,
            "paper_session_blocked",
            strategy_id=resolved_strategy_id,
            run_id=reconciliation_report.run_id,
            session_date=as_of_session.isoformat(),
            strategy_status=control_state.status,
            blocked_reason="reconciliation_blocks_execution",
            trigger_source=resolved_trigger_source,
        )
        return PaperSessionRunReport(
            strategy_id=resolved_strategy_id,
            session_date=as_of_session.isoformat(),
            trigger_source=resolved_trigger_source,
            source_risk_run_id=str(session_plan.source_risk_run_id),
            action="blocked_reconciliation",
            execution_run_id=None,
            execution_status=None,
            result_summary=base_summary,
            reconciliation_run_id=reconciliation_run_id,
        )

    if not session_plan.candidates:
        emit_structured_log(
            logger,
            logging.INFO,
            "paper_session_noop",
            strategy_id=resolved_strategy_id,
            session_date=as_of_session.isoformat(),
            strategy_status=control_state.status,
            trigger_source=resolved_trigger_source,
            action="noop_no_candidates",
        )
        return PaperSessionRunReport(
            strategy_id=resolved_strategy_id,
            session_date=as_of_session.isoformat(),
            trigger_source=resolved_trigger_source,
            source_risk_run_id=str(session_plan.source_risk_run_id),
            action="noop_no_candidates",
            execution_run_id=None,
            execution_status=None,
            result_summary=base_summary,
            reconciliation_run_id=reconciliation_run_id,
        )

    if not session_plan.missing_candidates:
        # Round 6: the persisted per-candidate dispositions (never recomputed by a read model).
        base_summary["candidate_dispositions"] = noop_candidate_dispositions(
            resolved_settings,
            strategy_id=resolved_strategy_id,
            source_risk_run_id=session_plan.source_risk_run_id,
            candidates=session_plan.candidates,
        )
        emit_structured_log(
            logger,
            logging.INFO,
            "paper_session_noop",
            strategy_id=resolved_strategy_id,
            session_date=as_of_session.isoformat(),
            strategy_status=control_state.status,
            trigger_source=resolved_trigger_source,
            action="noop_existing_orders",
        )
        return PaperSessionRunReport(
            strategy_id=resolved_strategy_id,
            session_date=as_of_session.isoformat(),
            trigger_source=resolved_trigger_source,
            source_risk_run_id=str(session_plan.source_risk_run_id),
            action="noop_existing_orders",
            execution_run_id=None,
            execution_status=None,
            result_summary=base_summary,
            reconciliation_run_id=reconciliation_run_id,
        )

    execution_report = run_paper_order_submission(
        resolved_strategy_id,
        as_of_session=as_of_session,
        risk_run_id=str(session_plan.source_risk_run_id),
        trigger_source=resolved_trigger_source,
        settings=resolved_settings,
        registry=registry,
        execution_service=execution_service,
        job_id=job_id,
        price_source=price_source,
    )
    result_summary = dict(execution_report.result_summary)
    result_summary["session_preflight"] = base_summary
    blocked_reason = result_summary.get("blocked_reason")
    operation = result_summary.get("operation")
    operation = operation if isinstance(operation, dict) else {}
    if result_summary.get("action") == "noop_existing_orders":
        # Every candidate was a replay / already-submitted action (S3-R4): a no-op, zero POST.
        action = "noop_existing_orders"
        emit_structured_log(
            logger,
            logging.INFO,
            "paper_session_noop",
            strategy_id=resolved_strategy_id,
            session_date=as_of_session.isoformat(),
            strategy_status=control_state.status,
            trigger_source=resolved_trigger_source,
            action=action,
        )
    elif result_summary.get("action") in _SUBMISSION_BLOCK_ACTIONS:
        action = str(result_summary["action"])
        emit_structured_log(
            logger,
            logging.WARNING,
            "paper_session_blocked",
            strategy_id=resolved_strategy_id,
            run_id=execution_report.run_id,
            session_date=as_of_session.isoformat(),
            strategy_status=control_state.status,
            blocked_reason=blocked_reason,
            trigger_source=resolved_trigger_source,
        )
    elif blocked_reason == BLOCKED_REASON_GLOBAL_KILL_SWITCH:
        action = "blocked_global_kill_switch"
        emit_structured_log(
            logger,
            logging.WARNING,
            "paper_session_blocked",
            strategy_id=resolved_strategy_id,
            run_id=execution_report.run_id,
            session_date=as_of_session.isoformat(),
            strategy_status=control_state.status,
            kill_switch_state=kill_switch_state.state,
            blocked_reason=BLOCKED_REASON_GLOBAL_KILL_SWITCH,
            trigger_source=resolved_trigger_source,
        )
    else:
        action = "submitted_missing_orders"
        emit_structured_log(
            logger,
            logging.INFO,
            "paper_session_completed",
            strategy_id=resolved_strategy_id,
            run_id=execution_report.run_id,
            session_date=as_of_session.isoformat(),
            strategy_status=control_state.status,
            trigger_source=resolved_trigger_source,
            action=action,
        )

    return PaperSessionRunReport(
        strategy_id=resolved_strategy_id,
        session_date=as_of_session.isoformat(),
        trigger_source=resolved_trigger_source,
        source_risk_run_id=str(session_plan.source_risk_run_id),
        action=action,
        execution_run_id=execution_report.run_id,
        execution_status=execution_report.status,
        result_summary=result_summary,
        reconciliation_run_id=reconciliation_run_id,
        operation_id=operation.get("id"),
        operation_state=operation.get("state"),
        operation_reason=operation.get("reason"),
    )


# ---------------------------------------------------------------------------
# Continue session (REC-02, D-19, S1-R3 takeover)
# ---------------------------------------------------------------------------

#: Domain action of a continuation that found an in-doubt intent while taking authority.
CONTINUE_ACTION = "continued_session"
CONTINUE_ACTION_TERMINATED = "continue_operation_terminated"


def run_paper_continuation(
    operation_id: uuid.UUID,
    *,
    trigger_source: str | None = None,
    settings: Settings | None = None,
    registry: StrategyRegistry | None = None,
    execution_service: ExecutionService | None = None,
    job_id: uuid.UUID | None = None,
    price_source: PriceSource | None = None,
) -> PaperSessionRunReport:
    """Continue a paused execution operation (D-19): its own mode, never an OPS-07 retry.

    The (strategy, evaluation session) advisory lock is taken FIRST and non-blocking: a lock
    that cannot be acquired ends the Job as ``operation_executor_active`` with zero writes and
    zero broker calls. Inside the lock: lazy expiry (``touch_operation``), the paused ->
    running compare-and-set (or adoption of a crash-left ``running`` operation), the S1
    takeover (``acquire_execution``: epoch CAS, then in-doubt intents parked UNKNOWN) and the
    SAME permission-checked, guarded loop as a start, with ``continuation=True``. Only unsent
    intents are ever sent, under their original identity. No reconciliation, no correction and
    no recovery pass runs here: broker calls are the guarded POSTs and the read-only price GET.
    """

    resolved_settings = settings or load_settings()
    logger = get_logger("trading_platform.paper_execution")
    executor_job_id, lease_owner = _executor_identity(resolved_settings, job_id)
    with session_scope(resolved_settings) as session:
        operation = session.get(ExecutionOperation, operation_id)
        if operation is None:
            raise OperationNotFoundError(operation_id)
        strategy_row_id = operation.strategy_id
        as_of_session = operation.as_of_session
        risk_run_id = operation.risk_run_id
        strategy_id = session.execute(
            select(Strategy.strategy_id).where(Strategy.id == strategy_row_id)
        ).scalar_one()
    resolved_registry = registry or build_default_registry(resolved_settings)
    metadata = resolved_registry.resolve(strategy_id).metadata
    source = trigger_source or "continue"
    try:
        with session_run_lock(
            strategy_id=strategy_id, session_date=as_of_session, settings=resolved_settings
        ) as lock:
            run_id = _create_paper_execution_run(
                resolved_settings,
                metadata,
                trigger_source=source,
                as_of_session=as_of_session,
                requested_risk_run_id=str(risk_run_id),
                job_id=executor_job_id,
            )
            fence: Fence | None = None
            context: _ExecutionContext | None = None
            owns_service = execution_service is None
            owns_price = price_source is None
            broker_execution: ExecutionService | None = execution_service
            resolved_price: PriceSource | None = price_source
            try:
                with session_scope(resolved_settings) as session:
                    reclaim_stale_runs(
                        session,
                        strategy_public_id=strategy_id,
                        session_date=as_of_session,
                        timeout_minutes=resolved_settings.execution.safety.stale_run_timeout_minutes,
                        reclaiming_run_id=run_id,
                    )
                with session_scope(resolved_settings) as session:
                    touched = touch_operation(
                        session,
                        operation_id,
                        now=clock.now_utc(),
                        settings=resolved_settings,
                        exclude_job_id=executor_job_id,
                    )
                if touched.state is OperationState.TERMINATED:
                    return _continuation_report(
                        resolved_settings,
                        run_id=run_id,
                        strategy_id=strategy_id,
                        as_of_session=as_of_session,
                        risk_run_id=risk_run_id,
                        operation_id=operation_id,
                        trigger_source=source,
                        action=CONTINUE_ACTION_TERMINATED,
                        loop=None,
                    )
                with session_scope(resolved_settings) as session:
                    persisted = OperationState(
                        session.execute(
                            select(ExecutionOperation.state).where(
                                ExecutionOperation.id == operation_id
                            )
                        ).scalar_one()
                    )
                    if persisted is OperationState.PAUSED:
                        begin_continuation(session, operation_id, executor_job_id)
                    elif persisted is OperationState.RUNNING:
                        adopt_running_operation(session, operation_id, executor_job_id)
                    else:
                        raise OperationConflictError(
                            operation_id, f"operation is {persisted.value}, not paused"
                        )
                acquisition = acquire_execution(
                    operation_id, executor_job_id, lock, settings=resolved_settings
                )
                fence = Fence(operation_id, acquisition.epoch, executor_job_id)
                if acquisition.paused:
                    # In-doubt intents were parked UNKNOWN; the recovery gate refuses Continue
                    # until recovery resolves them. Nothing is sent.
                    return _continuation_report(
                        resolved_settings,
                        run_id=run_id,
                        strategy_id=strategy_id,
                        as_of_session=as_of_session,
                        risk_run_id=risk_run_id,
                        operation_id=operation_id,
                        trigger_source=source,
                        action=BLOCKED_ACTION_OUTCOME_UNRESOLVED,
                        loop=None,
                    )
                broker_execution = broker_execution or AlpacaExecutionService(
                    resolved_settings.broker.alpaca
                )
                resolved_price = resolved_price or _default_price_source(resolved_settings)
                context = _ExecutionContext(
                    settings=resolved_settings,
                    logger=logger,
                    strategy_id=strategy_id,
                    strategy_row_id=strategy_row_id,
                    as_of_session=as_of_session,
                    risk_run_id=risk_run_id,
                    run_id=run_id,
                    trigger_source=source,
                    fence=fence,
                    lease_owner=lease_owner,
                    broker_execution=broker_execution,
                    price_source=resolved_price,
                    failure_threshold=resolved_settings.execution.safety.repeated_failure_threshold,
                )
                loop = _run_operation_loop(context, continuation=True)
                return _continuation_report(
                    resolved_settings,
                    run_id=run_id,
                    strategy_id=strategy_id,
                    as_of_session=as_of_session,
                    risk_run_id=risk_run_id,
                    operation_id=operation_id,
                    trigger_source=source,
                    action=CONTINUE_ACTION,
                    loop=loop,
                )
            except Exception as exc:
                if context is not None:
                    _best_effort_pause(context)
                _update_paper_execution_run(
                    resolved_settings,
                    run_id,
                    status=StrategyRunStatus.FAILED,
                    completed_at=datetime.now(UTC),
                    error_message=str(exc),
                    result_summary={
                        "stage": "failed",
                        "mode": "continue",
                        "operation_id": str(operation_id),
                        "strategy_id": strategy_id,
                        "as_of_session": as_of_session.isoformat(),
                        "source_risk_run_id": str(risk_run_id),
                    },
                )
                logger.warning(
                    "paper_continuation_failed",
                    extra={
                        "context": build_log_context(
                            strategy_id=strategy_id,
                            run_id=str(run_id),
                            session_date=as_of_session.isoformat(),
                            trigger_source=source,
                        )
                    },
                    exc_info=True,
                )
                raise
            finally:
                if (
                    owns_service
                    and broker_execution is not None
                    and hasattr(broker_execution, "close")
                ):
                    broker_execution.close()
                if owns_price and resolved_price is not None and hasattr(resolved_price, "close"):
                    resolved_price.close()
    except ConcurrentRunLockedError as exc:
        emit_structured_log(
            logger,
            logging.WARNING,
            "paper_continuation_lock_denied",
            strategy_id=strategy_id,
            session_date=as_of_session.isoformat(),
            operation_id=str(operation_id),
        )
        raise OperationExecutorActiveError(operation_id, "session advisory lock is held") from exc


def _continuation_report(
    settings: Settings,
    *,
    run_id: uuid.UUID,
    strategy_id: str,
    as_of_session: date,
    risk_run_id: uuid.UUID,
    operation_id: uuid.UUID,
    trigger_source: str,
    action: str,
    loop: _LoopState | None,
) -> PaperSessionRunReport:
    """Persist the continuation's run summary and build the session report."""

    operation_summary = _operation_summary(settings, operation_id)
    state = loop or _LoopState()
    summary: dict[str, Any] = {
        "stage": "blocked_mid_run" if state.halt else "completed",
        "mode": "continue",
        "action": action,
        "operation_id": str(operation_id),
        "strategy_id": strategy_id,
        "as_of_session": as_of_session.isoformat(),
        "requested_risk_run_id": str(risk_run_id),
        "source_risk_run_id": str(risk_run_id),
        "submitted_count": len(state.submitted_orders),
        "existing_count": len(state.existing_orders),
        "reused_count": len(state.reused_orders),
        "versioned_count": 0,
        "skipped_by_kill_switch_count": len(state.skipped_by_kill_switch),
        "skipped_by_ownership_count": len(state.skipped_by_ownership),
        "submitted_orders": state.submitted_orders,
        "existing_orders": state.existing_orders,
        "reused_orders": state.reused_orders,
        "versioned_orders": [],
        "skipped_by_kill_switch": state.skipped_by_kill_switch,
        "skipped_by_ownership": state.skipped_by_ownership,
        "rejected_orders": state.rejected_orders,
        "operation": operation_summary,
        "broker_provider": settings.broker.provider,
    }
    if state.halt == "kill_switch" and state.kill_switch is not None:
        summary["blocked_reason"] = BLOCKED_REASON_GLOBAL_KILL_SWITCH
        summary["kill_switch"] = state.kill_switch
    elif state.halt == "ownership":
        summary["blocked_reason"] = BLOCKED_REASON_NOT_ACTIVE_PAPER_STRATEGY
        summary["ownership_block"] = state.ownership_block
    if action == BLOCKED_ACTION_OUTCOME_UNRESOLVED:
        summary["blocked_reason"] = PausedReason.OUTCOME_UNRESOLVED.value
    report = _update_paper_execution_run(
        settings,
        run_id,
        status=StrategyRunStatus.SUCCEEDED,
        completed_at=datetime.now(UTC),
        result_summary=summary,
    )
    return PaperSessionRunReport(
        strategy_id=strategy_id,
        session_date=as_of_session.isoformat(),
        trigger_source=trigger_source,
        source_risk_run_id=str(risk_run_id),
        action=action,
        execution_run_id=report.run_id,
        execution_status=report.status,
        result_summary=report.result_summary,
        reconciliation_run_id=None,
        operation_id=operation_summary["id"],
        operation_state=operation_summary["state"],
        operation_reason=operation_summary["reason"],
    )


# ---------------------------------------------------------------------------
# Execution operation: start, sequential loop, guarded single send path (REC-02, D-17, S1-R3)
# ---------------------------------------------------------------------------


class ExecutorJobRequiredError(RuntimeError):
    """An execution operation needs the Job that holds execution authority (S1-R3).

    Nothing was created or sent when this is raised."""


class ExecutionAuthorityLostAfterSendError(RuntimeError):
    """The executor lost its authority after the broker answered; its outcome could not be
    recorded locally. Never mapped to a domain conflict: the POST WAS sent, so the Job fails
    with an uncertain outcome and recovery (broker-order-sync) establishes the order."""


#: The only intent decisions a continuation may bind (``create_new`` is the never-registered
#: planned intent; ``create_new_version`` is unreachable on this path).
_CONTINUATION_ACTIONS = frozenset({"reuse_existing", "retry_existing", "create_new"})


class ContinuationIdentityChangedError(RuntimeError):
    """The intent resolver returned an action a continuation may never take (a new version).
    Nothing was registered or sent; the Job fails and the operation is paused best-effort."""

    def __init__(self, action: str) -> None:
        super().__init__(
            f"continuation_identity_changed: the intent resolver returned '{action}'; a "
            "continuation sends the pinned intent under its original identity only."
        )
        self.action = action


class PinnedIdentityMismatchError(RuntimeError):
    """The identity the executor would send differs from the PINNED intent (SAF-03 / D-19 / S2-R3).

    Raised inside the registration transaction before any write, so it rolls back with nothing
    registered, re-linked or sent. ``field`` is the FIRST differing one in the fixed order symbol,
    side, quantity, client_order_id: the client_order_id embeds ``intent_hash[:18]`` over symbol,
    side and quantity, so a material drift is attributed to the material field and a bare
    client_order_id mismatch can only come from the prefix / settings."""

    def __init__(self, field: str, *, pinned: str, derived: str) -> None:
        super().__init__(f"pinned_identity_mismatch:{field}")
        self.field = field
        self.pinned = pinned
        self.derived = derived


def _assert_pinned_identity(
    session: Session,
    view: _IntentView,
    decision: PaperIntentDecision,
    candidate: PaperExecutionCandidate,
) -> None:
    """Assert, before any registration write, that what the branch would send IS the pinned row.

    New orders (create_new / create_new_version) are checked through the candidate and the freshly
    derived client_order_id; an existing order (reuse_existing / retry_existing) is checked through
    its own persisted identity, never through the freshly derived client_order_id (an order
    registered under an older prefix keeps its identity)."""

    existing = (
        session.get(PaperOrder, decision.existing_order_id)
        if decision.existing_order_id is not None
        else None
    )
    if existing is not None:
        symbol_matches = existing.symbol_id == view.symbol_id
        symbol_pair = (str(existing.symbol_id), str(view.symbol_id))
        side, quantity, client_order_id = (
            existing.side,
            existing.quantity,
            existing.client_order_id,
        )
    else:
        symbol_matches = candidate.symbol == view.symbol and candidate.symbol_id == view.symbol_id
        symbol_pair = (candidate.symbol, view.symbol)
        side, quantity, client_order_id = (
            candidate.side.value,
            candidate.quantity,
            decision.identity.client_order_id,
        )
    if not symbol_matches:
        raise PinnedIdentityMismatchError("symbol", pinned=symbol_pair[1], derived=symbol_pair[0])
    if side != view.side:
        raise PinnedIdentityMismatchError("side", pinned=view.side, derived=str(side))
    if Decimal(quantity) != view.quantity:
        raise PinnedIdentityMismatchError(
            "quantity", pinned=str(view.quantity), derived=str(quantity)
        )
    if client_order_id != view.client_order_id:
        raise PinnedIdentityMismatchError(
            "client_order_id", pinned=view.client_order_id, derived=str(client_order_id)
        )


def _executor_identity(settings: Settings, job_id: uuid.UUID | None) -> tuple[uuid.UUID, str]:
    """The Job that will hold execution authority and the lease owner it holds NOW.

    ``JobContext`` is frozen and carries no lease owner, so the owner is read once from the
    Job row when the session starts (this process certainly holds the lease then); every T1
    re-verifies it, so a lease taken over later refuses the next send. A Job-less call cannot
    hold authority: tests that call the service directly use ``tests/support``'s seam.
    """

    if job_id is None:
        raise ExecutorJobRequiredError(
            "A paper execution operation needs an executor Job (job_id); none was given."
        )
    with session_scope(settings) as session:
        lease_owner = session.execute(
            select(Job.lease_owner).where(Job.id == job_id)
        ).scalar_one_or_none()
    return job_id, lease_owner or ""


def _default_price_source(settings: Settings) -> PriceSource:
    """The production S2-R3 price source (read-only latest-trade GET); tests patch this."""

    return AlpacaPriceSource(settings.broker.alpaca)


@dataclass(frozen=True)
class _ExecutionContext:
    """What every step of an operation's loop needs (shared with Continue, 20.1-16)."""

    settings: Settings
    logger: logging.Logger
    strategy_id: str
    strategy_row_id: uuid.UUID
    as_of_session: date
    risk_run_id: uuid.UUID
    run_id: uuid.UUID
    trigger_source: str
    fence: Fence
    lease_owner: str
    broker_execution: ExecutionService
    price_source: PriceSource
    failure_threshold: int


@dataclass(frozen=True)
class _StartBlocked:
    """A run-time execution block raised while creating the operation (zero POST)."""

    reason: str
    action: str
    message: str
    dispositions: list[dict[str, Any]] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class _StartNoop:
    """No candidate became a new intent: ``noop_existing_orders`` with the dispositions."""

    dispositions: list[dict[str, Any]]
    existing_orders: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class _OperationStart:
    operation_id: uuid.UUID
    epoch: int
    executor_job_id: uuid.UUID
    lease_owner: str
    dispositions: list[dict[str, Any]]
    existing_orders: list[dict[str, Any]] = field(default_factory=list)


_StartResult = _StartBlocked | _StartNoop | _OperationStart


@dataclass(frozen=True)
class _EligibleCandidate:
    candidate: PaperExecutionCandidate
    fingerprint: str
    prior_execution_refs: tuple[str, ...]


def _candidate_dispositions(
    session: Session,
    settings: Settings,
    *,
    strategy_id: str,
    risk_run: StrategyRun,
    candidates: Sequence[PaperExecutionCandidate],
    rows: BasisRows,
) -> tuple[list[dict[str, Any]], list[_EligibleCandidate], list[PaperExecutionCandidate]]:
    """S3-R4 checks 4-6 for every candidate, in plan order: the closed dispositions of the ones
    that do NOT become a new intent (``[{symbol, side, disposition}]``, round 6), the eligible
    ones with their ``decision_fingerprint`` and the candidates this very risk run already
    realised (reported as existing orders)."""

    manifest = (risk_run.result_summary or {}).get("evaluation_manifest")
    inputs = decision_inputs_digest(
        manifest if isinstance(manifest, Mapping) else None,
        risk_config=risk_config_digest(settings, strategy_id),
    )
    working = strategy_working_orders(session, strategy_id)
    portfolio_digest = portfolio_state_digest(
        rows.basis_positions, [(order.symbol, order.side, order.quantity) for order in working]
    )
    earlier = load_earlier_intents(session, strategy_id, rows.orders)
    basis_positions = rows.basis_positions if rows.basis_source is not None else None
    dispositions: list[dict[str, Any]] = []
    eligible: list[_EligibleCandidate] = []
    # A candidate this very risk run already realised as an order that reached (or may have
    # reached) the broker is the SAME intent (case a), reported as an existing order, never as
    # a disposition; one that never reached the broker stays a candidate (a retry).
    realised_here = {
        (order.symbol, order.side, order.session_date, order.quantity)
        for order in rows.orders
        if order.source_risk_run_id == risk_run.id and order.reached_broker
    }
    already_realised: list[PaperExecutionCandidate] = []
    for candidate in candidates:
        if (
            candidate.symbol,
            candidate.side.value,
            candidate.session_date,
            candidate.quantity,
        ) in realised_here:
            already_realised.append(candidate)
            continue
        fingerprint = decision_fingerprint(
            strategy_id=strategy_id,
            session_date=candidate.session_date,
            symbol=candidate.symbol,
            side=candidate.side.value,
            quantity=candidate.quantity,
            inputs_digest=inputs,
            portfolio_digest=portfolio_digest,
        )
        verdict = classify_candidate(
            CandidateKey(
                symbol=candidate.symbol,
                side=candidate.side.value,
                session_date=candidate.session_date,
                quantity=candidate.quantity,
            ),
            fingerprint,
            orders=rows.orders,
            earlier_intents=earlier,
            basis_positions=basis_positions,
        )
        if verdict.disposition is None:
            eligible.append(
                _EligibleCandidate(candidate, fingerprint, verdict.prior_execution_refs)
            )
            continue
        entry: dict[str, Any] = {
            "symbol": candidate.symbol,
            "side": candidate.side.value,
            "disposition": verdict.disposition.value,
        }
        if verdict.earlier_intent_id is not None:
            entry["earlier_intent_id"] = str(verdict.earlier_intent_id)
        dispositions.append(entry)
    return dispositions, eligible, already_realised


def _existing_order_payloads(
    session: Session,
    settings: Settings,
    *,
    strategy_id: str,
    strategy_row_id: uuid.UUID,
    candidates: Sequence[PaperExecutionCandidate],
) -> list[dict[str, Any]]:
    """Payloads of the orders this risk run already realised (Phase 20 ``existing_orders``)."""

    payloads: list[dict[str, Any]] = []
    for candidate in candidates:
        decision = _resolve_paper_intent_decision(
            session,
            strategy_row_id=strategy_row_id,
            strategy_id=strategy_id,
            prefix=settings.execution.client_order_id_prefix,
            candidate=candidate,
            failure_threshold=settings.execution.safety.repeated_failure_threshold,
        )
        if decision.existing_order_id is None:
            continue
        existing = session.get(PaperOrder, decision.existing_order_id)
        if existing is not None:
            payloads.append(
                _paper_order_payload(
                    existing,
                    intent_decision=decision.summary,
                    supersedes_client_order_id=decision.supersedes_client_order_id,
                )
            )
    return payloads


def noop_candidate_dispositions(
    settings: Settings,
    *,
    strategy_id: str,
    source_risk_run_id: uuid.UUID,
    candidates: Sequence[PaperExecutionCandidate],
) -> list[dict[str, Any]]:
    """The persisted per-candidate dispositions of a start whose every candidate already has an
    order (the preflight ``noop_existing_orders`` path). Read-only; never raises."""

    try:
        with session_scope(settings) as session:
            risk_run = session.get(StrategyRun, source_risk_run_id)
            if risk_run is None:
                return []
            rows = load_basis_verification_rows(
                session, strategy_public_id=strategy_id, risk_run=risk_run
            )
            dispositions, _eligible, _realised = _candidate_dispositions(
                session,
                settings,
                strategy_id=strategy_id,
                risk_run=risk_run,
                candidates=candidates,
                rows=rows,
            )
            return dispositions
    except Exception:  # reporting only: a failure here must never change the run's result
        return []


def _prepare_start(
    settings: Settings,
    *,
    strategy_id: str,
    strategy_row_id: uuid.UUID,
    source_risk_run_id: uuid.UUID,
    as_of_session: date,
    candidates: Sequence[PaperExecutionCandidate],
    job_id: uuid.UUID | None,
) -> _StartResult:
    """Run-time operation creation (D-16, S3-R4): ONE transaction that holds the ownership
    singleton FOR SHARE (SER), re-checks the owner, lazily expires a stale open operation
    (D-21), then decides the start: a completed operation of the pinned risk run is a no-op, a
    terminated or open one is a typed conflict, an unresolved outcome or an unverified basis
    blocks with zero POST, candidates failing the replay / justification / allowance checks are
    listed and not registered, and the remaining candidates are persisted as ``planned``
    intents in plan order. The database constraints (one open operation per strategy, one
    operation per pinned risk run) decide races, not a prior read.
    """

    now = clock.now_utc()
    with session_scope(settings) as session:
        lock_active_paper_strategy_shared(session)
        gate = operator_controls.load_trading_gate_state(session, strategy_id=strategy_id)
        block = gate.ownership_block_for(strategy_id)
        if block is not None:
            return _StartBlocked(
                reason=BLOCKED_REASON_NOT_ACTIVE_PAPER_STRATEGY,
                action="blocked_not_active_paper_strategy",
                message=(
                    f"Strategy '{strategy_id}' is not the active paper strategy "
                    f"({block.value}); paper execution halted before broker submission begins."
                ),
                extra={
                    "ownership_block": block.value,
                    "active_paper_strategy": gate.owner.to_dict(),
                },
            )

        open_operation = (
            session.execute(
                select(ExecutionOperation).where(
                    ExecutionOperation.strategy_id == strategy_row_id,
                    ExecutionOperation.state.in_([s.value for s in OPEN_OPERATION_STATES]),
                )
            )
            .scalars()
            .first()
        )
        still_open = False
        if open_operation is not None:
            touched = touch_operation(session, open_operation.id, now=now, settings=settings)
            still_open = touched.state in OPEN_OPERATION_STATES
        risk_run = session.get(StrategyRun, source_risk_run_id)
        if risk_run is None:
            raise LookupError(f"Missing risk evaluation run '{source_risk_run_id}'.")
        rows = load_basis_verification_rows(
            session, strategy_public_id=strategy_id, risk_run=risk_run
        )
        dispositions, eligible, realised = _candidate_dispositions(
            session,
            settings,
            strategy_id=strategy_id,
            risk_run=risk_run,
            candidates=candidates,
            rows=rows,
        )
        existing_payloads = _existing_order_payloads(
            session,
            settings,
            strategy_id=strategy_id,
            strategy_row_id=strategy_row_id,
            candidates=realised,
        )
        if candidates and len(realised) == len(candidates):
            # An idempotent re-run on the SAME risk run: every candidate is already an order
            # that reached the broker. Phase 20 result (existing orders, no POST), whatever the
            # state of that run's operation.
            return _StartNoop([], existing_payloads)
        if still_open and open_operation is not None:
            raise OperationOpenError(strategy_row_id, open_operation.id)
        pinned = session.execute(
            select(ExecutionOperation).where(
                ExecutionOperation.strategy_id == strategy_row_id,
                ExecutionOperation.risk_run_id == source_risk_run_id,
            )
        ).scalar_one_or_none()
        if pinned is not None:
            if OperationState(pinned.state) is OperationState.COMPLETED:
                return _StartNoop([])
            raise RiskRunAlreadyOperatedError(strategy_row_id, source_risk_run_id)

        # (1) no uncertainty: no submission of the strategy may still produce an execution
        recovery = strategy_recovery_status(session, strategy_id, now=now)
        # G2: an order whose shared classification (attempts.classify_submission_evidence) is
        # UNESTABLISHED reached or may have reached the broker with no broker evidence and no
        # established rejection, and never proven not sent (a legacy zero-attempt order included,
        # TL-4): it may still produce an execution, so nothing else is authorized, whether or not
        # a Job links it.
        local_unestablished = unestablished_orders(rows.orders)
        if recovery.gate_code is GateCode.OUTCOME_UNRESOLVED or local_unestablished:
            return _StartBlocked(
                reason=GateCode.OUTCOME_UNRESOLVED.value,
                action=BLOCKED_ACTION_OUTCOME_UNRESOLVED,
                message=(
                    f"An uncertain order outcome of strategy '{strategy_id}' is unresolved; "
                    "paper execution halted before any intent was registered."
                ),
                extra={"required_job_type": REQUIRED_JOB_TYPE},
            )
        # (2) verified basis
        verification = verify_evaluation_basis(rows)
        if verification.failure is not None:
            return _StartBlocked(
                reason="evaluation_basis_unverified",
                action="blocked_evaluation_basis_unverified",
                message=(
                    "The evaluation's portfolio basis is not verified against the strategy's "
                    f"executions ({verification.failure.value}); nothing was registered or sent."
                ),
                extra={"detail": verification.failure.value},
            )
        # (4)-(6) replay, justification, session allowance (computed above)
        if not eligible:
            return _StartNoop(dispositions, existing_payloads)

        executor_job_id, lease_owner = _executor_identity(settings, job_id)
        planned = [
            PlannedIntent(
                symbol_id=item.candidate.symbol_id,
                side=item.candidate.side.value,
                quantity=item.candidate.quantity,
                client_order_id=derive_order_identity(
                    prefix=settings.execution.client_order_id_prefix,
                    strategy_id=strategy_id,
                    session_date=item.candidate.session_date,
                    symbol=item.candidate.symbol,
                    side=item.candidate.side,
                    quantity=item.candidate.quantity,
                ).client_order_id,
                decision_fingerprint=item.fingerprint,
                risk_event_id=item.candidate.risk_event_id,
                reference_price=item.candidate.reference_price,
                prior_execution_refs=item.prior_execution_refs,
            )
            for item in eligible
        ]
        operation = create_operation(
            session,
            strategy_pk=strategy_row_id,
            as_of_session=as_of_session,
            risk_run_id=source_risk_run_id,
            intents=planned,
            job_id=executor_job_id,
            basis_verification=verification.record,
        )
        return _OperationStart(
            operation_id=operation.id,
            epoch=operation.execution_epoch,
            executor_job_id=executor_job_id,
            lease_owner=lease_owner,
            dispositions=dispositions,
            existing_orders=existing_payloads,
        )


@dataclass(frozen=True)
class _IntentView:
    """One pinned intent as the loop sees it (identity never changes)."""

    intent_id: uuid.UUID
    sequence: int
    symbol_id: uuid.UUID
    symbol: str
    side: str
    quantity: Decimal
    reference_price: Decimal | None
    client_order_id: str
    risk_event_id: uuid.UUID | None
    paper_order_id: uuid.UUID | None
    session_date: date


@dataclass
class _LoopState:
    submitted_orders: list[dict[str, Any]] = field(default_factory=list)
    existing_orders: list[dict[str, Any]] = field(default_factory=list)
    reused_orders: list[dict[str, Any]] = field(default_factory=list)
    versioned_orders: list[dict[str, Any]] = field(default_factory=list)
    rejected_orders: list[dict[str, Any]] = field(default_factory=list)
    skipped_by_kill_switch: list[dict[str, Any]] = field(default_factory=list)
    skipped_by_ownership: list[dict[str, Any]] = field(default_factory=list)
    halt: str | None = None
    kill_switch: dict[str, Any] | None = None
    ownership_block: str | None = None
    final_state: OperationState = OperationState.RUNNING
    final_reason: str | None = None


def _load_open_intents(session: Session, operation_id: uuid.UUID) -> list[_IntentView]:
    """The operation's still-unsent intents in plan order: open, and ``planned`` /
    ``registered_unsent`` / ``not_sent`` (an intent that was submitted, rejected or is in doubt
    is never sent again by this loop; it is the SAME intent, S3-R4 case a)."""

    operation = session.get(ExecutionOperation, operation_id)
    assert operation is not None
    facts = load_intent_facts(session, [operation_id])[operation_id]
    sendable = {
        SubmissionIntentState.PLANNED,
        SubmissionIntentState.REGISTERED_UNSENT,
        SubmissionIntentState.NOT_SENT,
    }
    return [
        _IntentView(
            intent_id=fact.row.id,
            sequence=fact.row.sequence,
            symbol_id=fact.row.symbol_id,
            symbol=fact.ticker,
            side=fact.row.side,
            quantity=fact.row.quantity,
            reference_price=fact.row.reference_price,
            client_order_id=fact.row.client_order_id,
            risk_event_id=fact.row.risk_event_id,
            paper_order_id=fact.row.paper_order_id,
            session_date=operation.as_of_session,
        )
        for fact in facts
        if fact.row.disposition == IntentDisposition.OPEN.value and fact.state in sendable
    ]


def _skipped_entry(view: _IntentView) -> dict[str, Any]:
    return {
        "symbol": view.symbol,
        "side": view.side,
        "quantity": float(view.quantity),
        "session_date": view.session_date.isoformat(),
        "source_risk_event_id": str(view.risk_event_id) if view.risk_event_id else None,
    }


def _operation_summary(settings: Settings, operation_id: uuid.UUID) -> dict[str, Any]:
    with session_scope(settings) as session:
        operation = session.get(ExecutionOperation, operation_id)
        if operation is None:
            raise LookupError(f"Missing execution operation '{operation_id}'.")
        session.refresh(operation)
        state = OperationState(operation.state)
        return {
            "id": str(operation.id),
            "state": state.value,
            "reason": operation.reason,
            "reason_detail": operation.reason_detail,
            "next_action": next_action(state, operation.reason).value,
        }


#: SAF-02: T1 refusals that pause the operation through the existing path, with EXISTING closed
#: pause reasons only (no new state or reason). An elapsed / superseded window pauses here as
#: ``execution_window_not_open`` on purpose: T1 must not run the termination write inside the send
#: transaction; the lazy expiry (D-21, ``touch_operation``) terminates it on its next touch.
_SEND_REFUSAL_PAUSE: dict[SendRefusal, PausedReason] = {
    SendRefusal.KILL_SWITCH_TRIPPED: PausedReason.KILL_SWITCH_TRIPPED,
    SendRefusal.STRATEGY_DISABLED: PausedReason.STRATEGY_DISABLED,
    SendRefusal.NOT_ACTIVE_PAPER_STRATEGY: PausedReason.NOT_ACTIVE_PAPER_STRATEGY,
    SendRefusal.EXECUTION_WINDOW_CLOSED: PausedReason.EXECUTION_WINDOW_NOT_OPEN,
    SendRefusal.PRICE_STALE: PausedReason.PRICE_UNAVAILABLE,
}


def _move_running(
    ctx: _ExecutionContext,
    to_state: OperationState,
    reason: str,
    detail: str | None = None,
) -> None:
    """Fenced move of a RUNNING operation to a paused / requires_reevaluation state.

    A stale executor (lost epoch, Job or state) changes nothing and raises
    ``OperationConflictError``."""

    validate_state_reason(to_state, reason)
    with session_scope(ctx.settings) as session:
        updated = cas_update_operation(
            session,
            ctx.fence,
            {
                "state": to_state.value,
                "reason": reason,
                "reason_detail": detail[:64] if detail else None,
                "state_changed_at": clock.now_utc(),
            },
        )
    if updated != 1:
        raise OperationConflictError(ctx.fence.operation_id, "stale executor fence")


def _best_effort_pause(ctx: _ExecutionContext) -> None:
    """A crash inside the loop pauses a still-RUNNING operation (paused/awaiting_reconciliation)
    before the exception propagates, never overwriting a more specific pause already written
    (an ambiguous result's paused/outcome_unresolved always wins) and never raising."""

    try:
        with session_scope(ctx.settings) as session:
            cas_update_operation(
                session,
                ctx.fence,
                {
                    "state": OperationState.PAUSED.value,
                    "reason": PausedReason.AWAITING_RECONCILIATION.value,
                    "reason_detail": None,
                    "state_changed_at": clock.now_utc(),
                },
                from_states=(OperationState.RUNNING,),
            )
    except Exception:
        ctx.logger.warning("paper_operation_best_effort_pause_failed", exc_info=True)


def _persist_fenced(ctx: _ExecutionContext, write: Callable[[Session], None]) -> bool:
    """Run ``write`` in one transaction while the executor's fence holds (the operation row is
    locked FOR SHARE, so a takeover cannot interleave). Returns False, writing nothing, once
    authority is lost."""

    with session_scope(ctx.settings) as session:
        if not fence_held(session, ctx.fence, lock=True):
            return False
        write(session)
    return True


def _run_operation_loop(ctx: _ExecutionContext, *, continuation: bool = False) -> _LoopState:
    """The sequential, pause-aware submission loop (D-17).

    Intents are submitted one at a time in plan order. Before EVERY broker action the fixed-
    precedence permission check runs; after every broker result whose effects are not accounted
    (accepted: working, partially filled, or already terminal but not yet synced) the operation
    PAUSES and the remaining intents stay ``planned`` (never registered, never sent). A rejected
    order continues to the next intent after its own permission check; an ambiguous result
    pauses ``outcome_unresolved`` and the exception propagates (the Job lands uncertain). When
    every intent was rejected the operation is ``completed``. Continue (20.1-16) reuses this
    loop with ``continuation=True``.
    """

    state = _LoopState()
    with session_scope(ctx.settings) as session:
        pending = _load_open_intents(session, ctx.fence.operation_id)
    if continuation and not pending:
        # Nothing left to send: complete only when every submitted order is terminal and
        # synced, nothing is unresolved and a clean reconciliation followed (20.1-16).
        with session_scope(ctx.settings) as session:
            blocker = settled_blocker(session, ctx.strategy_id)
        if blocker is not None:
            _move_running(ctx, OperationState.PAUSED, blocker.value)
            state.final_state = OperationState.PAUSED
            state.final_reason = blocker.value
            return state
    for index, view in enumerate(pending):
        with session_scope(ctx.settings) as session:
            outcome = check_intent_permission(
                session,
                strategy_id=ctx.strategy_id,
                as_of_session=ctx.as_of_session,
                risk_run_id=ctx.risk_run_id,
                intent=PinnedIntent(
                    symbol=view.symbol,
                    side=view.side,
                    quantity=view.quantity,
                    reference_price=view.reference_price,
                    client_order_id=view.client_order_id,
                    intent_id=view.intent_id,
                ),
                price_source=ctx.price_source,
                settings=ctx.settings,
                continuation=continuation,
                now=clock.now_utc(),
            )
        if not outcome.ok:
            _apply_permission_outcome(ctx, state, outcome, pending[index:])
            return state
        if not _execute_pinned_intent(ctx, state, view, outcome, continuation=continuation):
            return state
    # Every intent ended REJECTED (an accepted order would have paused the operation).
    with session_scope(ctx.settings) as session:
        transition(
            session,
            ctx.fence.operation_id,
            OperationState.COMPLETED,
            fence=ctx.fence,
            now=clock.now_utc(),
        )
    state.final_state = OperationState.COMPLETED
    return state


def _apply_permission_outcome(
    ctx: _ExecutionContext,
    state: _LoopState,
    outcome: PermissionOutcome,
    remaining: Sequence[_IntentView],
) -> None:
    """Persist a refused permission check: pause, requires_reevaluation or terminate. Nothing
    further is registered or sent; the unsent intents are preserved with unchanged identity."""

    assert outcome.reason is not None
    if outcome.verdict is PermissionVerdict.PAUSE:
        _move_running(ctx, OperationState.PAUSED, outcome.reason, outcome.detail)
        state.final_state = OperationState.PAUSED
        if outcome.reason == PausedReason.KILL_SWITCH_TRIPPED.value:
            state.halt = "kill_switch"
            state.kill_switch = outcome.details.get("kill_switch")
            state.skipped_by_kill_switch = [_skipped_entry(view) for view in remaining]
        elif outcome.reason == PausedReason.NOT_ACTIVE_PAPER_STRATEGY.value:
            state.halt = "ownership"
            state.ownership_block = outcome.detail
            state.skipped_by_ownership = [
                {**_skipped_entry(view), "ownership_block": outcome.detail} for view in remaining
            ]
    elif outcome.verdict is PermissionVerdict.REEVALUATE:
        _move_running(ctx, OperationState.REQUIRES_REEVALUATION, outcome.reason, outcome.detail)
        state.final_state = OperationState.REQUIRES_REEVALUATION
    else:
        with session_scope(ctx.settings) as session:
            transition(
                session,
                ctx.fence.operation_id,
                OperationState.TERMINATED,
                outcome.reason,
                fence=ctx.fence,
                ended_by="executor",
                now=clock.now_utc(),
            )
        state.final_state = OperationState.TERMINATED
    state.final_reason = outcome.reason


def _candidate_for_view(
    session: Session, view: _IntentView, risk_run_id: uuid.UUID
) -> PaperExecutionCandidate:
    """The execution candidate of a pinned intent, rebuilt from its approved risk event."""

    event = session.get(RiskEvent, view.risk_event_id) if view.risk_event_id else None
    if event is not None:
        symbol = session.get(Symbol, event.symbol_id)
        assert symbol is not None
        candidate = _candidate_from_risk_event(event, symbol)
        if candidate is not None:
            return candidate
    return PaperExecutionCandidate(
        risk_event_id=view.risk_event_id or uuid.UUID(int=0),
        source_risk_run_id=risk_run_id,
        symbol_id=view.symbol_id,
        symbol=view.symbol,
        session_date=view.session_date,
        side=OrderSide(view.side),
        quantity=view.quantity,
        reference_price=view.reference_price or Decimal("0"),
        signal_reason="",
        decision_reason="",
        risk_metadata={},
    )


def create_new_version(
    session: Session,
    *,
    strategy_id: str,
    candidate: PaperExecutionCandidate,
    identity: DerivedOrderIdentity,
) -> PaperIntentDecision:
    """Register ``intent_version + 1`` of a (strategy, session, symbol, side) identity (S3-R4).

    Refused with ``VersionBypassRefusedError`` (``version_bypass_refused``) when ANY earlier
    version of the identity reached or may have reached the broker (accepted in any state,
    rejected, ambiguous, or a legacy order without proof): a new version is never a way around
    the session allowance or an unresolved outcome. Unreachable from the start loop for intents
    that already belong to an operation (a start only creates new intents for a new
    operation); this assertion guards every other caller."""

    orders = load_strategy_order_facts(session, strategy_id)
    same_key = [
        order
        for order in orders
        if order.symbol == candidate.symbol
        and order.side == candidate.side.value
        and order.session_date == candidate.session_date
    ]
    for order in same_key:
        if order.reached_broker:
            raise VersionBypassRefusedError(None)
    predecessor = same_key[-1] if same_key else None
    version = (
        (max(_order_version(session, o.paper_order_id) for o in same_key) + 1) if same_key else 1
    )
    return PaperIntentDecision(
        action="create_new_version" if same_key else "create_new",
        identity=identity,
        intent_version=version,
        existing_order_id=None,
        supersedes_paper_order_id=predecessor.paper_order_id if predecessor else None,
        supersedes_client_order_id=None,
        summary={
            "action": "create_new_version" if same_key else "create_new",
            "reason": "material_change_never_sent" if same_key else "new_material_intent",
            "client_order_id": identity.client_order_id,
            "intent_hash": identity.intent_hash,
            "intent_version": version,
            "source_risk_event_id": str(candidate.risk_event_id),
        },
    )


def _order_version(session: Session, paper_order_id: uuid.UUID) -> int:
    order = session.get(PaperOrder, paper_order_id)
    return order.intent_version if order is not None else 1


def _refuse_pinned_identity_mismatch(
    ctx: _ExecutionContext,
    state: _LoopState,
    view: _IntentView,
    mismatch: PinnedIdentityMismatchError,
) -> bool:
    """SAF-03: nothing was registered or sent; the operation moves to ``requires_reevaluation``
    through the existing closed reasons and the loop stops WITHOUT raising (a failed paper-session
    Job is forced outcome_uncertain, which with zero orders would land in the TL-4
    execution_path_unproven state)."""

    reason = (
        ReevaluationReason.STRATEGY_SETTINGS_CHANGED
        if mismatch.field == "client_order_id"
        else ReevaluationReason.EVALUATION_DATA_CHANGED
    )
    detail = f"pinned_identity_mismatch:{mismatch.field}"

    def record(session: Session) -> None:
        session.add(
            ExecutionEvent(
                strategy_run_id=ctx.run_id,
                paper_order_id=None,
                event_type="pinned_identity_mismatch",
                severity="warning",
                blocks_execution=False,
                event_at=datetime.now(UTC),
                message=(
                    f"The identity to send for pinned intent '{view.client_order_id}' differs "
                    f"from the pinned identity ({mismatch.field}); nothing was registered or "
                    "sent and the operation requires re-evaluation."
                ),
                details={
                    "field": mismatch.field,
                    "pinned": mismatch.pinned,
                    "derived": mismatch.derived,
                    "intent_id": str(view.intent_id),
                },
            )
        )

    if not _persist_fenced(ctx, record):
        raise OperationConflictError(ctx.fence.operation_id, "stale executor fence")
    _move_running(ctx, OperationState.REQUIRES_REEVALUATION, reason.value, detail)
    state.final_state = OperationState.REQUIRES_REEVALUATION
    state.final_reason = reason.value
    return False


def _execute_pinned_intent(
    ctx: _ExecutionContext,
    state: _LoopState,
    view: _IntentView,
    permission: PermissionOutcome,
    *,
    continuation: bool = False,
) -> bool:
    """Register one pinned intent, send it through the guarded single send path and classify the
    result. Returns True to continue with the next intent, False when the loop must stop."""

    run_id = ctx.run_id
    logger = ctx.logger
    order_type = ctx.settings.execution.default_order_type
    time_in_force = ctx.settings.execution.default_time_in_force

    # ---- registration (fenced): existing intent decision logic, unchanged identities --------
    registration: dict[str, Any] = {}

    def register(session: Session) -> None:
        candidate = _candidate_for_view(session, view, ctx.risk_run_id)
        decision = _resolve_paper_intent_decision(
            session,
            strategy_row_id=ctx.strategy_row_id,
            strategy_id=ctx.strategy_id,
            prefix=ctx.settings.execution.client_order_id_prefix,
            candidate=candidate,
            failure_threshold=ctx.failure_threshold,
        )
        registration["candidate"] = candidate
        registration["decision"] = decision
        # SAF-03: BEFORE any branch write (and before the continuation-action refusal below, so a
        # drifted identity is a re-evaluation rather than a failed Job) the identity that would be
        # sent must equal the pinned intent; a mismatch rolls this transaction back.
        _assert_pinned_identity(session, view, decision, candidate)
        if continuation and decision.action not in _CONTINUATION_ACTIONS:
            # D-19 / R-6: Continue only ever sends the pinned intent under its ORIGINAL identity;
            # a new version is never a way to send "the same" action again.
            raise ContinuationIdentityChangedError(decision.action)
        if decision.action == "create_new_version":
            create_new_version(
                session,
                strategy_id=ctx.strategy_id,
                candidate=candidate,
                identity=decision.identity,
            )
        if decision.action == "reuse_existing":
            existing_order = session.get(PaperOrder, decision.existing_order_id)
            if existing_order is None:
                raise LookupError(f"Missing reusable paper_order '{decision.existing_order_id}'.")
            _record_intent_decision_event(
                session,
                strategy_run_id=run_id,
                paper_order_id=existing_order.id,
                event_type="paper_order_reused",
                message=(
                    f"Reused existing intent '{existing_order.client_order_id}' for identical "
                    "material order inputs; no new submission was attempted."
                ),
                details=decision.summary,
            )
            payload = _paper_order_payload(
                existing_order,
                intent_decision=decision.summary,
                supersedes_client_order_id=decision.supersedes_client_order_id,
            )
            state.existing_orders.append(payload)
            state.reused_orders.append(payload)
            _link_intent(session, view.intent_id, existing_order.id)
            registration["reused_not_sent"] = True
            return

        if decision.action == "retry_existing" and decision.existing_order_id is not None:
            guarded_order = session.get(PaperOrder, decision.existing_order_id)
            if guarded_order is None:
                raise LookupError(f"Missing retryable paper_order '{decision.existing_order_id}'.")
            prior_attempts = load_submission_attempts(session, guarded_order.id)
            prior_class = classify_submission(prior_attempts)
            if prior_class is not None and prior_class != SubmissionClass.NOT_SENT:
                guard_error = AmbiguousOrderSubmissionError(
                    "Order submission history is not clean "
                    f"(submission_class={prior_class.value}); the intent was not re-sent.",
                    submission_class=prior_class,
                    attempts=summarize_attempts(prior_attempts),
                    reason="history_not_clean",
                )
                _park_intent_unknown(
                    session,
                    ctx.settings,
                    order=guarded_order,
                    run_id=run_id,
                    error=guard_error,
                    trigger_source=ctx.trigger_source,
                )
                registration["guard_error"] = guard_error
                return

        if decision.existing_order_id is None:
            paper_order = PaperOrder(
                strategy_run_id=run_id,
                source_risk_event_id=candidate.risk_event_id,
                symbol_id=candidate.symbol_id,
                intended_session_date=candidate.session_date,
                side=candidate.side.value,
                quantity=candidate.quantity,
                order_type=order_type,
                time_in_force=time_in_force,
                intent_hash=decision.identity.intent_hash,
                intent_version=decision.intent_version,
                supersedes_paper_order_id=decision.supersedes_paper_order_id,
                client_order_id=decision.identity.client_order_id,
                status=OrderLifecycleState.PENDING_SUBMISSION,
                broker_payload={},
            )
            session.add(paper_order)
            session.flush()
            pending_order = paper_order
            transition_event_type = OrderTransitionEventType.INTENT_REGISTERED
        else:
            retrieved_order = session.get(PaperOrder, decision.existing_order_id)
            if retrieved_order is None:
                raise LookupError(f"Missing retryable paper_order '{decision.existing_order_id}'.")
            pending_order = retrieved_order
            transition_event_type = OrderTransitionEventType.RETRY_REQUESTED

        pending_order.strategy_run_id = run_id
        apply_order_transition(
            pending_order.id,
            OrderTransitionRequest(
                strategy_run_id=run_id,
                event_type=transition_event_type,
                details={
                    "trigger_source": ctx.trigger_source,
                    "source_risk_event_id": str(candidate.risk_event_id),
                    "intent_decision": decision.summary,
                },
            ),
            session=session,
            settings=ctx.settings,
        )
        pending_order.submission_attempt_count += 1
        pending_order.last_submission_attempt_at = datetime.now(UTC)
        pending_order.last_submission_error = None
        session.flush()
        registration["pending_order_id"] = pending_order.id
        registration["client_order_id"] = pending_order.client_order_id
        registration["intent_hash"] = pending_order.intent_hash
        registration["intent_version"] = pending_order.intent_version
        _link_intent(session, view.intent_id, pending_order.id)
        # The permission check that allowed this send, with the fresh price observation (audit).
        session.add(
            ExecutionEvent(
                strategy_run_id=run_id,
                paper_order_id=pending_order.id,
                event_type="intent_permission_checked",
                severity="info",
                blocks_execution=False,
                event_at=datetime.now(UTC),
                message=(
                    f"Permission check passed for intent '{pending_order.client_order_id}' "
                    "(window, provenance, pauses, fresh price, portfolio risk)."
                ),
                details=permission.audit(),
            )
        )
        if decision.action == "create_new_version":
            _record_intent_decision_event(
                session,
                strategy_run_id=run_id,
                paper_order_id=pending_order.id,
                event_type="paper_order_versioned",
                message=(
                    f"Created intent version {pending_order.intent_version} after superseding "
                    f"broker-touched order '{decision.supersedes_client_order_id}'."
                ),
                details=decision.summary,
            )
        session.flush()

    try:
        registered = _persist_fenced(ctx, register)
    except PinnedIdentityMismatchError as mismatch:
        return _refuse_pinned_identity_mismatch(ctx, state, view, mismatch)
    if not registered:
        raise OperationConflictError(ctx.fence.operation_id, "stale executor fence")
    guard_error = registration.get("guard_error")
    if guard_error is not None:
        _move_running(ctx, OperationState.PAUSED, PausedReason.OUTCOME_UNRESOLVED.value)
        state.final_state = OperationState.PAUSED
        state.final_reason = PausedReason.OUTCOME_UNRESOLVED.value
        raise guard_error
    if registration.get("reused_not_sent"):
        # Identical material intent already exists and may not be re-sent (retry threshold):
        # nothing is sent and the operation pauses; End is available.
        _move_running(
            ctx,
            OperationState.PAUSED,
            PausedReason.BROKER_UNAVAILABLE.value,
            "retry_threshold_exceeded",
        )
        state.final_state = OperationState.PAUSED
        state.final_reason = PausedReason.BROKER_UNAVAILABLE.value
        return False

    candidate: PaperExecutionCandidate = registration["candidate"]
    decision: PaperIntentDecision = registration["decision"]
    pending_order_id: uuid.UUID = registration["pending_order_id"]
    order_intent = OrderIntent(
        strategy_id=ctx.strategy_id,
        # SAF-03: the identity sent is the PINNED view (asserted equal in register()), never the
        # candidate rebuilt from the risk event.
        symbol=view.symbol,
        side=OrderSide(view.side),
        quantity=view.quantity,
        intended_session=candidate.session_date,
        client_order_id=view.client_order_id,
        intent_hash=registration["intent_hash"],
        intent_version=registration["intent_version"],
        reference_price=candidate.reference_price,
        metadata={
            "signal_reason": candidate.signal_reason,
            "decision_reason": candidate.decision_reason,
            "risk_metadata": candidate.risk_metadata,
            "source_risk_run_id": str(candidate.source_risk_run_id),
            "source_risk_event_id": str(candidate.risk_event_id),
        },
    )

    # ---- the guarded single send path (S1-R3) -------------------------------------------------
    try:
        result = _send_authorized(
            ctx,
            intent_id=view.intent_id,
            paper_order_id=pending_order_id,
            order_intent=order_intent,
            price_observed_at=(
                permission.observation.observed_at if permission.observation is not None else None
            ),
        )
    except SendRefusedError as exc:
        # T1 refused: ZERO POST. An intent that is not provably unsent (or an unresolved outcome
        # elsewhere) pauses outcome_unresolved; a lost authority ends the executor.
        if exc.refusal in (SendRefusal.OUTCOME_UNRESOLVED, SendRefusal.INTENT_NOT_SENDABLE):
            _move_running(ctx, OperationState.PAUSED, PausedReason.OUTCOME_UNRESOLVED.value)
            state.final_state = OperationState.PAUSED
            state.final_reason = PausedReason.OUTCOME_UNRESOLVED.value
            return False
        # SAF-02: T1 re-read the gate / window / price and one of them now says no (first
        # attempt or an in-loop retry). Nothing was POSTed by this attempt; the operation pauses
        # through the existing path with an existing closed reason and the intent stays unsent.
        pause_reason = _SEND_REFUSAL_PAUSE.get(exc.refusal)
        if pause_reason is not None:
            detail = exc.detail or (
                "price_stale" if exc.refusal is SendRefusal.PRICE_STALE else None
            )
            _move_running(ctx, OperationState.PAUSED, pause_reason.value, detail)
            state.final_state = OperationState.PAUSED
            state.final_reason = pause_reason.value
            return False
        raise OperationConflictError(
            ctx.fence.operation_id, f"send_refused:{exc.refusal.value}"
        ) from exc
    except AmbiguousOrderSubmissionError as exc:
        ambiguous_error = exc

        # D-12: the request may have reached the broker. The intent is parked UNKNOWN (never
        # re-sent), the operation pauses outcome_unresolved in its own committed transaction and
        # the exception is RE-RAISED so the Job lands failed with outcome_uncertain.
        def park(session: Session) -> None:
            ambiguous_order = session.get(PaperOrder, pending_order_id)
            if ambiguous_order is not None:
                _park_intent_unknown(
                    session,
                    ctx.settings,
                    order=ambiguous_order,
                    run_id=run_id,
                    error=ambiguous_error,
                    trigger_source=ctx.trigger_source,
                )
            cas_update_operation(
                session,
                ctx.fence,
                {
                    "state": OperationState.PAUSED.value,
                    "reason": PausedReason.OUTCOME_UNRESOLVED.value,
                    "reason_detail": None,
                    "state_changed_at": clock.now_utc(),
                },
            )

        _persist_fenced(ctx, park)
        state.final_state = OperationState.PAUSED
        state.final_reason = PausedReason.OUTCOME_UNRESOLVED.value
        raise
    except OrderRejectedError as exc:
        rejected_error = exc
        # D-12: a 4xx refusal is final: the intent ends REJECTED and the loop continues to the
        # next intent after its own permission check.
        rejected: dict[str, Any] = {}

        def reject(session: Session) -> None:
            rejected_order = session.get(PaperOrder, pending_order_id)
            if rejected_order is None:
                return
            rejected_order.last_submission_error = str(rejected_error)
            rejected_order.broker_payload = {
                "error": str(rejected_error),
                "submission_class": rejected_error.submission_class.value,
                "http_status": rejected_error.http_status,
            }
            apply_order_transition(
                rejected_order.id,
                OrderTransitionRequest(
                    strategy_run_id=run_id,
                    event_type=OrderTransitionEventType.BROKER_REJECTED,
                    details={
                        "submission_class": rejected_error.submission_class.value,
                        "http_status": rejected_error.http_status,
                        "attempt_numbers": list(rejected_error.attempt_numbers),
                        "trigger_source": ctx.trigger_source,
                    },
                ),
                session=session,
                settings=ctx.settings,
            )
            session.flush()
            session.refresh(rejected_order)
            rejected.update(
                _paper_order_payload(
                    rejected_order,
                    intent_decision=decision.summary,
                    supersedes_client_order_id=decision.supersedes_client_order_id,
                )
            )

        if not _persist_fenced(ctx, reject):
            raise ExecutionAuthorityLostAfterSendError(
                f"Order '{order_intent.client_order_id}' was rejected after authority was lost."
            ) from exc
        state.rejected_orders.append(rejected)
        return True
    except OrderNotSentError as exc:
        not_sent_error = exc

        # Every attempt failed before a connection was made (or the authorization expired): the
        # outcome is CERTAIN. The intent stays SUBMISSION_FAILED (retryable), the operation
        # pauses broker_unavailable and the Job ends normally (no re-raise).
        def not_sent(session: Session) -> None:
            failed_order = session.get(PaperOrder, pending_order_id)
            if failed_order is None:
                return
            failed_order.last_submission_error = str(not_sent_error)
            failed_order.broker_payload = {"error": str(not_sent_error)}
            apply_order_transition(
                failed_order.id,
                OrderTransitionRequest(
                    strategy_run_id=run_id,
                    event_type=OrderTransitionEventType.SUBMISSION_FAILED,
                    details={"error": str(not_sent_error), "trigger_source": ctx.trigger_source},
                ),
                session=session,
                settings=ctx.settings,
            )
            cas_update_operation(
                session,
                ctx.fence,
                {
                    "state": OperationState.PAUSED.value,
                    "reason": PausedReason.BROKER_UNAVAILABLE.value,
                    "reason_detail": (not_sent_error.reason or "order_not_sent")[:64],
                    "state_changed_at": clock.now_utc(),
                },
            )

        if not _persist_fenced(ctx, not_sent):
            raise OperationConflictError(ctx.fence.operation_id, "stale executor fence") from exc
        state.final_state = OperationState.PAUSED
        state.final_reason = PausedReason.BROKER_UNAVAILABLE.value
        return False
    except Exception as exc:
        failure_error = exc

        # Any other failure before a result. The attempt history decides what is known: a failure
        # before T1 committed leaves zero attempt rows (proven not sent, 20.1-17), and an
        # accepted-but-unparseable reply no longer reaches this branch (it is an
        # AmbiguousOrderSubmissionError, SAF-06). SUBMISSION_FAILED (no reconciliation scheduled);
        # the caller pauses the operation best-effort (paused/awaiting_reconciliation) before the
        # exception propagates.
        def failed(session: Session) -> None:
            failed_order = session.get(PaperOrder, pending_order_id)
            if failed_order is None:
                return
            failed_order.last_submission_error = str(failure_error)
            failed_order.broker_payload = {"error": str(failure_error)}
            apply_order_transition(
                failed_order.id,
                OrderTransitionRequest(
                    strategy_run_id=run_id,
                    event_type=OrderTransitionEventType.SUBMISSION_FAILED,
                    details={"error": str(failure_error), "trigger_source": ctx.trigger_source},
                ),
                session=session,
                settings=ctx.settings,
            )

        _persist_fenced(ctx, failed)
        raise

    # ---- the broker accepted: record it, then PAUSE (TL-1: effects not yet accounted) ---------
    try:

        def accepted(session: Session) -> None:
            persisted_order = session.get(PaperOrder, pending_order_id)
            if persisted_order is None:
                raise LookupError(f"Missing pending paper_order '{pending_order_id}'.")
            transition_recorded_at = datetime.now(UTC)
            persisted_order.broker_order_id = result.broker_order_id or None
            persisted_order.broker_status = result.broker_status
            persisted_order.submitted_at = result.submitted_at
            persisted_order.last_submission_error = None
            persisted_order.broker_payload = result.raw_payload
            apply_order_transition(
                persisted_order.id,
                OrderTransitionRequest(
                    strategy_run_id=run_id,
                    event_type=_broker_transition_event(result.status),
                    details={
                        "broker_order_id": result.broker_order_id,
                        "broker_status": result.broker_status,
                        "trigger_source": ctx.trigger_source,
                    },
                    event_at=transition_recorded_at,
                ),
                session=session,
                settings=ctx.settings,
            )
            session.flush()
            session.refresh(persisted_order)
            payload = _paper_order_payload(
                persisted_order,
                intent_decision=decision.summary,
                supersedes_client_order_id=decision.supersedes_client_order_id,
            )
            state.submitted_orders.append(payload)
            if decision.action == "retry_existing":
                state.reused_orders.append(payload)
            if decision.action == "create_new_version":
                state.versioned_orders.append(payload)
            # TL-1 / D-17: every accepted order (working, partially filled, or already terminal
            # but not yet synced) pauses the operation in the SAME transaction as its record.
            cas_update_operation(
                session,
                ctx.fence,
                {
                    "state": OperationState.PAUSED.value,
                    "reason": PausedReason.WORKING_ORDER_COMMITMENTS_UNACCOUNTED.value,
                    "reason_detail": None,
                    "state_changed_at": clock.now_utc(),
                },
            )

        if not _persist_fenced(ctx, accepted):
            raise ExecutionAuthorityLostAfterSendError(
                f"Order '{order_intent.client_order_id}' was accepted by the broker after the "
                "executor lost authority; broker-order-sync and recovery establish it."
            )
    except Exception as exc:
        # DB-06: the broker has ALREADY accepted this order but the local write rolled back; a
        # reconciliation pass must be scheduled so the divergence is discovered and corrected.
        schedule_reconciliation_after_partial_failure(
            ctx.settings,
            logger=logger,
            strategy_id=ctx.strategy_id,
            run_id=run_id,
            paper_order_id=pending_order_id,
            session_date=candidate.session_date,
            client_order_id=order_intent.client_order_id,
            broker_order_id=result.broker_order_id,
            trigger_source=ctx.trigger_source,
            error=exc,
        )
        raise
    state.final_state = OperationState.PAUSED
    state.final_reason = PausedReason.WORKING_ORDER_COMMITMENTS_UNACCOUNTED.value
    return False


def _link_intent(session: Session, intent_id: uuid.UUID, paper_order_id: uuid.UUID) -> None:
    session.execute(
        update(ExecutionOperationIntent)
        .where(ExecutionOperationIntent.id == intent_id)
        .values(paper_order_id=paper_order_id)
        .execution_options(synchronize_session=False)
    )


def _send_authorized(
    ctx: _ExecutionContext,
    *,
    intent_id: uuid.UUID,
    paper_order_id: uuid.UUID,
    order_intent: OrderIntent,
    price_observed_at: datetime | None = None,
) -> OrderSubmissionResult:
    """The SINGLE send path: transaction T1 (``authorize_send``) commits the attempt row and the
    executor's authority BEFORE the POST; with no committed attempt (a refused T1) nothing is
    sent. The bound ``GuardedAttemptLog`` carries the authorization into the order client: its
    first ``begin_attempt`` returns the pre-authorized attempt, every retry runs its own T1, the
    wall-clock deadline is re-checked immediately before the request is handed to the HTTP
    client, and outcomes are recorded through the normal path while the fence holds, otherwise
    through ``complete_attempt_late``.
    """

    authorization = authorize_send(
        ctx.fence.operation_id,
        intent_id,
        ctx.fence.epoch,
        ctx.fence.job_id,
        lease_owner=ctx.lease_owner,
        settings=ctx.settings,
        price_observed_at=price_observed_at,
    )
    log = GuardedAttemptLog(
        ctx.settings,
        fence=ctx.fence,
        lease_owner=ctx.lease_owner,
        intent_id=intent_id,
        paper_order_id=paper_order_id,
        strategy_run_id=ctx.run_id,
        authorization=authorization,
        price_observed_at=price_observed_at,
    )
    number = authorization.attempt_number
    with bind_attempt_log(log):
        try:
            if log.send_deadline_passed(number):
                log.complete_attempt(
                    number,
                    outcome_class=AttemptOutcomeClass.DEADLINE_EXPIRED,
                    error_type="AuthorizationDeadlineExpired",
                )
                raise OrderNotSentError(
                    "Order was not sent: the send authorization expired before the request "
                    "was handed to the HTTP client.",
                    submission_class=SubmissionClass.NOT_SENT,
                    attempts=[(number, AttemptOutcomeClass.DEADLINE_EXPIRED.value)],
                    reason="deadline_expired",
                )
            result = ctx.broker_execution.submit_order(order_intent)
        except (AmbiguousOrderSubmissionError, OrderRejectedError, OrderNotSentError) as typed:
            if not log.first_attempt_taken:
                # A client that raised a typed outcome without recording its attempt: the
                # outcome class follows the type (an ambiguous result stays an open attempt).
                if isinstance(typed, OrderRejectedError):
                    _complete_untaken(
                        ctx,
                        log,
                        AttemptOutcomeClass.REJECTED,
                        error_type=type(typed).__name__,
                        http_status=typed.http_status,
                    )
                elif isinstance(typed, OrderNotSentError):
                    _complete_untaken(
                        ctx,
                        log,
                        AttemptOutcomeClass.PRE_CONNECTION,
                        error_type=type(typed).__name__,
                    )
            raise
        except Exception as exc:
            if not log.first_attempt_taken:
                # The client never began the pre-authorized attempt, so no request left the
                # process (the client contract: begin_attempt precedes every POST).
                _complete_untaken(
                    ctx, log, AttemptOutcomeClass.PRE_CONNECTION, error_type=type(exc).__name__
                )
            raise
    if not log.first_attempt_taken:
        _complete_untaken(ctx, log, AttemptOutcomeClass.ACCEPTED)
    return result


def _complete_untaken(
    ctx: _ExecutionContext,
    log: GuardedAttemptLog,
    outcome: AttemptOutcomeClass,
    *,
    error_type: str | None = None,
    http_status: int | None = None,
) -> None:
    try:
        log.complete_attempt(
            log.first_attempt_number,
            outcome_class=outcome,
            error_type=error_type,
            http_status=http_status,
        )
    except Exception:
        ctx.logger.warning("paper_send_attempt_not_completed", exc_info=True)


def _resolve_source_risk_run(
    session,
    *,
    strategy_id: str,
    as_of_session: date,
    requested_risk_run_id: str | None,
) -> StrategyRun:
    query = (
        select(StrategyRun)
        .join(Strategy, Strategy.id == StrategyRun.strategy_id)
        .where(
            Strategy.strategy_id == strategy_id,
            StrategyRun.run_type == StrategyRunType.RISK_EVALUATION,
        )
        .order_by(StrategyRun.started_at.desc())
    )

    if requested_risk_run_id is not None:
        resolved_run = session.get(StrategyRun, uuid.UUID(requested_risk_run_id))
        if resolved_run is None:
            raise LookupError(f"Missing risk evaluation run '{requested_risk_run_id}'.")
        if resolved_run.run_type != StrategyRunType.RISK_EVALUATION:
            raise ValueError(f"Run '{requested_risk_run_id}' is not a risk_evaluation batch.")
        if resolved_run.status != StrategyRunStatus.SUCCEEDED:
            raise ValueError(f"Risk evaluation run '{requested_risk_run_id}' is not succeeded.")
        resolved_strategy = session.get(Strategy, resolved_run.strategy_id)
        if resolved_strategy is None or resolved_strategy.strategy_id != strategy_id:
            raise ValueError(
                f"Risk evaluation run '{requested_risk_run_id}' does not belong to strategy '{strategy_id}'."
            )
        target_session = as_of_session.isoformat()
        parameters_session = resolved_run.parameters_snapshot.get("as_of_session")
        summary_session = resolved_run.result_summary.get("as_of_session")
        if parameters_session != target_session and summary_session != target_session:
            raise ValueError(
                f"Risk evaluation run '{requested_risk_run_id}' does not match session {target_session}."
            )
        return resolved_run

    target_session = as_of_session.isoformat()
    for run in session.execute(query).scalars():
        if run.status != StrategyRunStatus.SUCCEEDED:
            continue
        if run.parameters_snapshot.get("as_of_session") == target_session:
            return run

    raise LookupError(
        f"No succeeded risk_evaluation run exists for strategy '{strategy_id}' and session {target_session}."
    )


def _risk_event_side_priority():
    return case((RiskEvent.signal_direction == "exit", 0), else_=1)


def _candidate_from_risk_event(
    risk_event: RiskEvent, symbol: Symbol
) -> PaperExecutionCandidate | None:
    if risk_event.proposed_quantity is None or risk_event.proposed_quantity <= 0:
        return None
    return PaperExecutionCandidate(
        risk_event_id=risk_event.id,
        source_risk_run_id=risk_event.strategy_run_id,
        symbol_id=symbol.id,
        symbol=symbol.ticker,
        session_date=risk_event.session_date,
        side=OrderSide.SELL if risk_event.signal_direction == "exit" else OrderSide.BUY,
        quantity=risk_event.proposed_quantity,
        reference_price=risk_event.reference_price,
        signal_reason=risk_event.signal_reason,
        decision_reason=risk_event.decision_reason,
        risk_metadata=risk_event.risk_metadata,
    )


def _load_submission_candidates(
    session, source_risk_run_id: uuid.UUID
) -> list[PaperExecutionCandidate]:
    side_priority = _risk_event_side_priority()
    rows = session.execute(
        select(RiskEvent, Symbol)
        .join(Symbol, Symbol.id == RiskEvent.symbol_id)
        .where(
            RiskEvent.strategy_run_id == source_risk_run_id,
            RiskEvent.outcome == "approved",
            RiskEvent.decision_code == "approved",
        )
        .order_by(side_priority, Symbol.ticker.asc())
    ).all()

    candidates: list[PaperExecutionCandidate] = []
    for risk_event, symbol in rows:
        candidate = _candidate_from_risk_event(risk_event, symbol)
        if candidate is not None:
            candidates.append(candidate)
    return candidates


def _load_auto_resolve_candidates(
    session,
    *,
    strategy_id: str,
    as_of_session: date,
) -> tuple[uuid.UUID, uuid.UUID, list[PaperExecutionCandidate]]:
    """Q1 (auto-resolve path only, PERF-01): fold source-run resolution INTO
    the candidate load. One statement resolves the latest SUCCEEDED
    risk_evaluation StrategyRun for (strategy_id, as_of_session) as a
    LIMIT-1 subquery, then LEFT JOINs the approved RiskEvents (+ Symbol) for
    that run -- the approved/decision_code predicates live in the JOIN's ON
    clause (not WHERE) so a run with zero approved candidates still returns
    exactly one row (RiskEvent/Symbol columns NULL), preserving the
    'run resolved, candidates=[]' outcome. Zero rows overall means no
    matching run exists at all, matching `_resolve_source_risk_run`'s
    LookupError contract exactly.

    Returns (source_risk_run_id, strategy_row_id, candidates).
    """
    target_session = as_of_session.isoformat()
    resolved_run = (
        select(StrategyRun.id, StrategyRun.strategy_id)
        .select_from(StrategyRun)
        .join(Strategy, Strategy.id == StrategyRun.strategy_id)
        .where(
            Strategy.strategy_id == strategy_id,
            StrategyRun.run_type == StrategyRunType.RISK_EVALUATION,
            StrategyRun.status == StrategyRunStatus.SUCCEEDED,
            # 20.1-15 (20.1-06 handoff): the SAME predicate as the submit-time gate's
            # ``latest_eligible_risk_run_id`` / ``is_eligible_risk_run`` (parameters_snapshot
            # only), so the gate, the manifest check and the run-time pin name one run.
            StrategyRun.parameters_snapshot["as_of_session"].as_string() == target_session,
        )
        .order_by(StrategyRun.started_at.desc())
        .limit(1)
        .subquery("resolved_run")
    )

    side_priority = _risk_event_side_priority()
    rows = session.execute(
        select(resolved_run.c.id, resolved_run.c.strategy_id, RiskEvent, Symbol)
        .select_from(resolved_run)
        .join(
            RiskEvent,
            and_(
                RiskEvent.strategy_run_id == resolved_run.c.id,
                RiskEvent.outcome == "approved",
                RiskEvent.decision_code == "approved",
            ),
            isouter=True,
        )
        .join(Symbol, Symbol.id == RiskEvent.symbol_id, isouter=True)
        .order_by(side_priority, Symbol.ticker.asc())
    ).all()

    if not rows:
        raise LookupError(
            f"No succeeded risk_evaluation run exists for strategy '{strategy_id}' and session {target_session}."
        )

    source_risk_run_id, strategy_row_id = rows[0][0], rows[0][1]
    candidates: list[PaperExecutionCandidate] = []
    for _, _, risk_event, symbol in rows:
        if risk_event is None or symbol is None:
            continue
        candidate = _candidate_from_risk_event(risk_event, symbol)
        if candidate is not None:
            candidates.append(candidate)
    return source_risk_run_id, strategy_row_id, candidates


def _load_paper_order_index(
    session,
    *,
    strategy_row_id: uuid.UUID,
    candidates: list[PaperExecutionCandidate],
) -> tuple[dict[str, PaperOrder], dict[tuple[uuid.UUID, date, str], list[PaperOrder]]]:
    """Q2 (auto-resolve path only, PERF-01): ONE batched PaperOrder load
    covering every candidate's exact intent-hash match AND predecessor
    lineage match, instead of 2-3 queries per candidate. `supersedes_paper_order`
    is eager-loaded via `joinedload` (a many-to-one hop -- no row multiplication,
    stays inside this single statement) since its `client_order_id` is read in
    summaries; `selectinload` would fire a second statement and break the
    2-query bound.
    """
    if not candidates:
        return {}, {}

    session_dates = {candidate.session_date for candidate in candidates}
    rows = (
        session.execute(
            select(PaperOrder)
            .join(StrategyRun, StrategyRun.id == PaperOrder.strategy_run_id)
            .options(joinedload(PaperOrder.supersedes_paper_order))
            .where(
                StrategyRun.strategy_id == strategy_row_id,
                PaperOrder.intended_session_date.in_(session_dates),
            )
            .order_by(PaperOrder.intent_version.desc(), PaperOrder.created_at.desc())
        )
        .unique()
        .scalars()
        .all()
    )

    by_intent_hash: dict[str, PaperOrder] = {}
    predecessors_by_key: dict[tuple[uuid.UUID, date, str], list[PaperOrder]] = {}
    for order in rows:
        # UniqueConstraint("intent_hash") guarantees at most one row per hash.
        by_intent_hash[order.intent_hash] = order
        key = (order.symbol_id, order.intended_session_date, order.side)
        predecessors_by_key.setdefault(key, []).append(order)
    return by_intent_hash, predecessors_by_key


def _build_paper_session_plan(
    session,
    *,
    strategy_id: str,
    as_of_session: date,
    requested_risk_run_id: str | None,
    failure_threshold: int,
    client_order_id_prefix: str,
) -> PaperSessionPlan:
    """PERF-01: the auto-resolve path (`requested_risk_run_id is None`) issues
    exactly 2 SQL queries total regardless of candidate count -- Q1
    (`_load_auto_resolve_candidates`, folds source-run resolution into the
    candidate load) and Q2 (`_load_paper_order_index`, one batched PaperOrder
    load), with every intent decision then resolved in-memory
    (`_resolve_paper_intent_decision_from_index`). The `requested_risk_run_id`
    -PROVIDED branch is unchanged (out of PERF-01's scope; its per-candidate
    queries are not counted toward the 2-query bound).
    """
    existing_orders: list[dict[str, Any]] = []
    missing_candidates: list[PaperExecutionCandidate] = []

    if requested_risk_run_id is None:
        source_risk_run_id, strategy_row_id, candidates = _load_auto_resolve_candidates(
            session,
            strategy_id=strategy_id,
            as_of_session=as_of_session,
        )
        by_intent_hash, predecessors_by_key = _load_paper_order_index(
            session,
            strategy_row_id=strategy_row_id,
            candidates=candidates,
        )
        for candidate in candidates:
            intent_decision = _resolve_paper_intent_decision_from_index(
                strategy_id=strategy_id,
                prefix=client_order_id_prefix,
                candidate=candidate,
                failure_threshold=failure_threshold,
                by_intent_hash=by_intent_hash,
                predecessors_by_key=predecessors_by_key,
            )
            if intent_decision.action == "reuse_existing":
                existing_order = by_intent_hash[intent_decision.identity.intent_hash]
                existing_orders.append(
                    _paper_order_payload(
                        existing_order,
                        intent_decision=intent_decision.summary,
                        supersedes_client_order_id=intent_decision.supersedes_client_order_id,
                    )
                )
                continue
            missing_candidates.append(candidate)
    else:
        source_risk_run = _resolve_source_risk_run(
            session,
            strategy_id=strategy_id,
            as_of_session=as_of_session,
            requested_risk_run_id=requested_risk_run_id,
        )
        source_risk_run_id = source_risk_run.id
        candidates = _load_submission_candidates(session, source_risk_run.id)

        for candidate in candidates:
            intent_decision = _resolve_paper_intent_decision(
                session,
                strategy_row_id=source_risk_run.strategy_id,
                strategy_id=strategy_id,
                prefix=client_order_id_prefix,
                candidate=candidate,
                failure_threshold=failure_threshold,
            )
            if intent_decision.action == "reuse_existing":
                existing_order = session.get(PaperOrder, intent_decision.existing_order_id)
                if existing_order is None:
                    raise LookupError(
                        f"Missing reusable paper_order '{intent_decision.existing_order_id}'."
                    )
                existing_orders.append(
                    _paper_order_payload(
                        existing_order,
                        intent_decision=intent_decision.summary,
                        supersedes_client_order_id=intent_decision.supersedes_client_order_id,
                    )
                )
                continue
            missing_candidates.append(candidate)

    return PaperSessionPlan(
        source_risk_run_id=source_risk_run_id,
        candidates=tuple(candidates),
        existing_orders=tuple(existing_orders),
        missing_candidates=tuple(missing_candidates),
    )


def _build_intent_decision(
    *,
    identity: DerivedOrderIdentity,
    existing_order: PaperOrder | None,
    predecessor: PaperOrder | None,
    candidate: PaperExecutionCandidate,
    failure_threshold: int,
) -> PaperIntentDecision:
    """Pure decision core shared by both the query-based resolver (execution
    submission loop) and the in-memory-index resolver (preflight, PERF-01).
    Given an already-resolved exact `intent_hash` match and/or predecessor
    lineage row, decide reuse/retry/version/create -- no DB access here.
    """
    if existing_order is not None:
        action = (
            "retry_existing"
            if _is_resubmittable_order(existing_order, failure_threshold=failure_threshold)
            else "reuse_existing"
        )
        return PaperIntentDecision(
            action=action,
            identity=identity,
            intent_version=existing_order.intent_version,
            existing_order_id=existing_order.id,
            supersedes_paper_order_id=existing_order.supersedes_paper_order_id,
            supersedes_client_order_id=(
                existing_order.supersedes_paper_order.client_order_id
                if existing_order.supersedes_paper_order is not None
                else None
            ),
            summary={
                "action": action,
                "reason": "identical_material_intent",
                "paper_order_id": str(existing_order.id),
                "client_order_id": existing_order.client_order_id,
                "intent_hash": existing_order.intent_hash,
                "intent_version": existing_order.intent_version,
                "source_risk_event_id": str(candidate.risk_event_id),
                "persisted_source_risk_event_id": str(existing_order.source_risk_event_id),
            },
        )

    if predecessor is not None and _broker_has_touched_order(predecessor):
        next_version = predecessor.intent_version + 1
        return PaperIntentDecision(
            action="create_new_version",
            identity=identity,
            intent_version=next_version,
            existing_order_id=None,
            supersedes_paper_order_id=predecessor.id,
            supersedes_client_order_id=predecessor.client_order_id,
            summary={
                "action": "create_new_version",
                "reason": "material_change_after_broker_touch",
                "client_order_id": identity.client_order_id,
                "intent_hash": identity.intent_hash,
                "intent_version": next_version,
                "source_risk_event_id": str(candidate.risk_event_id),
                "supersedes_paper_order_id": str(predecessor.id),
                "supersedes_client_order_id": predecessor.client_order_id,
            },
        )

    return PaperIntentDecision(
        action="create_new",
        identity=identity,
        intent_version=1,
        existing_order_id=None,
        supersedes_paper_order_id=None,
        supersedes_client_order_id=None,
        summary={
            "action": "create_new",
            "reason": "new_material_intent",
            "client_order_id": identity.client_order_id,
            "intent_hash": identity.intent_hash,
            "intent_version": 1,
            "source_risk_event_id": str(candidate.risk_event_id),
        },
    )


def _resolve_paper_intent_decision(
    session,
    *,
    strategy_row_id: uuid.UUID,
    strategy_id: str,
    prefix: str,
    candidate: PaperExecutionCandidate,
    failure_threshold: int,
) -> PaperIntentDecision:
    """Query-based resolver used ONLY by the execution submission loop
    (`_run_paper_order_submission_guarded`), which relies on mid-loop
    visibility of orders committed by earlier candidates in the same run --
    intentionally NOT batched (out of PERF-01's scope, which targets the
    preflight path only).
    """
    identity = derive_order_identity(
        prefix=prefix,
        strategy_id=strategy_id,
        session_date=candidate.session_date,
        symbol=candidate.symbol,
        side=candidate.side,
        quantity=candidate.quantity,
    )
    existing_order = session.execute(
        select(PaperOrder).where(PaperOrder.intent_hash == identity.intent_hash)
    ).scalar_one_or_none()
    if existing_order is not None:
        return _build_intent_decision(
            identity=identity,
            existing_order=existing_order,
            predecessor=None,
            candidate=candidate,
            failure_threshold=failure_threshold,
        )

    predecessor = (
        session.execute(
            select(PaperOrder)
            .join(StrategyRun, StrategyRun.id == PaperOrder.strategy_run_id)
            .where(
                StrategyRun.strategy_id == strategy_row_id,
                PaperOrder.symbol_id == candidate.symbol_id,
                PaperOrder.intended_session_date == candidate.session_date,
                PaperOrder.side == candidate.side.value,
            )
            .order_by(PaperOrder.intent_version.desc(), PaperOrder.created_at.desc())
        )
        .scalars()
        .first()
    )
    return _build_intent_decision(
        identity=identity,
        existing_order=None,
        predecessor=predecessor,
        candidate=candidate,
        failure_threshold=failure_threshold,
    )


def _resolve_paper_intent_decision_from_index(
    *,
    strategy_id: str,
    prefix: str,
    candidate: PaperExecutionCandidate,
    failure_threshold: int,
    by_intent_hash: dict[str, PaperOrder],
    predecessors_by_key: dict[tuple[uuid.UUID, date, str], list[PaperOrder]],
) -> PaperIntentDecision:
    """In-memory resolver used ONLY by the preflight (`_build_paper_session_plan`,
    PERF-01): resolves every candidate's decision purely from the two indexes
    built by one batched `_load_paper_order_index` load -- no DB access here.
    """
    identity = derive_order_identity(
        prefix=prefix,
        strategy_id=strategy_id,
        session_date=candidate.session_date,
        symbol=candidate.symbol,
        side=candidate.side,
        quantity=candidate.quantity,
    )
    existing_order = by_intent_hash.get(identity.intent_hash)
    if existing_order is not None:
        return _build_intent_decision(
            identity=identity,
            existing_order=existing_order,
            predecessor=None,
            candidate=candidate,
            failure_threshold=failure_threshold,
        )

    predecessor_key = (candidate.symbol_id, candidate.session_date, candidate.side.value)
    predecessor_list = predecessors_by_key.get(predecessor_key)
    predecessor = predecessor_list[0] if predecessor_list else None
    return _build_intent_decision(
        identity=identity,
        existing_order=None,
        predecessor=predecessor,
        candidate=candidate,
        failure_threshold=failure_threshold,
    )


def _create_paper_execution_run(
    settings: Settings,
    metadata,
    *,
    trigger_source: str,
    as_of_session: date,
    requested_risk_run_id: str | None,
    job_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Insert the run row at status=RUNNING -- the literal first persisted
    write for this run (LOCK-03), acquired before kill-switch/control state
    is even read. strategy_status is genuinely unknown at this point (it is
    loaded moments later, after stale reclaim runs against this row); the
    accurate value is written into result_summary by the very next update.

    ``job_id`` (D-08/D-09) is written on this row in the same transaction
    that creates it, when provided.
    """
    with session_scope(settings) as session:
        strategy_record = ensure_strategy_record(session, metadata)
        strategy_run = StrategyRun(
            strategy_id=strategy_record.id,
            run_type=StrategyRunType.PAPER_EXECUTION,
            status=StrategyRunStatus.RUNNING,
            trigger_source=trigger_source,
            job_id=job_id,
            parameters_snapshot={
                "strategy": metadata.to_public_dict(),
                "as_of_session": as_of_session.isoformat(),
                "requested_risk_run_id": requested_risk_run_id,
                "broker": settings.broker.model_dump(mode="json"),
                "execution": settings.execution.model_dump(mode="json"),
            },
            result_summary={
                "stage": "running",
                "strategy_id": metadata.strategy_id,
                "as_of_session": as_of_session.isoformat(),
                "requested_risk_run_id": requested_risk_run_id,
            },
        )
        session.add(strategy_run)
        session.flush()
        return strategy_run.id


def _update_paper_execution_run(
    settings: Settings,
    run_id: uuid.UUID,
    *,
    status: StrategyRunStatus,
    result_summary: dict[str, Any] | None = None,
    error_message: str | None = None,
    completed_at: datetime | None = None,
) -> PaperExecutionRunReport:
    with session_scope(settings) as session:
        strategy_run = session.get(StrategyRun, run_id)
        if strategy_run is None:
            raise LookupError(f"Missing strategy_run '{run_id}'.")

        strategy_run.status = status
        if result_summary is not None:
            strategy_run.result_summary = result_summary
        if error_message is not None:
            strategy_run.error_message = error_message
        if completed_at is not None:
            strategy_run.completed_at = completed_at

        session.flush()
        session.refresh(strategy_run)
        strategy = strategy_run.strategy

        return PaperExecutionRunReport(
            run_id=str(strategy_run.id),
            strategy_id=strategy.strategy_id if strategy is not None else "unknown",
            status=strategy_run.status.value,
            trigger_source=strategy_run.trigger_source,
            started_at=strategy_run.started_at.isoformat(),
            completed_at=strategy_run.completed_at.isoformat()
            if strategy_run.completed_at
            else None,
            result_summary=strategy_run.result_summary,
        )


def _paper_order_payload(
    paper_order: PaperOrder,
    *,
    intent_decision: dict[str, Any] | None = None,
    supersedes_client_order_id: str | None = None,
) -> dict[str, Any]:
    payload = {
        "paper_order_id": str(paper_order.id),
        "client_order_id": paper_order.client_order_id,
        "broker_order_id": paper_order.broker_order_id,
        "status": paper_order.status,
        "broker_status": paper_order.broker_status,
        "side": paper_order.side,
        "quantity": float(paper_order.quantity),
        "intended_session_date": paper_order.intended_session_date.isoformat(),
        "submission_attempt_count": paper_order.submission_attempt_count,
        "sync_failure_count": paper_order.sync_failure_count,
        "last_submission_error": paper_order.last_submission_error,
        "last_sync_error": paper_order.last_sync_error,
        "submitted_at": paper_order.submitted_at.isoformat() if paper_order.submitted_at else None,
        "intent_context": {
            "intent_hash": paper_order.intent_hash,
            "intent_version": paper_order.intent_version,
            "supersedes_paper_order_id": (
                str(paper_order.supersedes_paper_order_id)
                if paper_order.supersedes_paper_order_id is not None
                else None
            ),
            "supersedes_client_order_id": supersedes_client_order_id,
        },
    }
    if intent_decision is not None:
        payload["intent_decision"] = intent_decision
    return payload


def _finalize_blocked_paper_execution_run(
    settings: Settings,
    run_id: uuid.UUID,
    *,
    strategy_id: str,
    as_of_session: date,
    requested_risk_run_id: str | None,
    trigger_source: str,
    strategy_status: str,
    blocked_reason: str,
    action: str | None = None,
    message: str | None = None,
    extra_details: dict[str, Any] | None = None,
) -> PaperExecutionRunReport:
    completed_at = datetime.now(UTC)
    resolved_action = action or f"blocked_{blocked_reason}"
    resolved_message = message or (
        f"Strategy '{strategy_id}' is disabled; paper execution blocked before broker submission begins."
    )
    result_summary: dict[str, Any] = {
        "stage": "blocked",
        "action": resolved_action,
        "strategy_id": strategy_id,
        "as_of_session": as_of_session.isoformat(),
        "requested_risk_run_id": requested_risk_run_id,
        "blocked_reason": blocked_reason,
        "strategy_status": strategy_status,
        "trigger_source": trigger_source,
        "message": resolved_message,
    }
    if extra_details:
        result_summary.update(extra_details)

    with session_scope(settings) as session:
        strategy_run = session.get(StrategyRun, run_id)
        if strategy_run is None:
            raise LookupError(f"Missing strategy_run '{run_id}'.")

        strategy_run.status = StrategyRunStatus.FAILED
        strategy_run.completed_at = completed_at
        strategy_run.error_message = resolved_message
        strategy_run.result_summary = result_summary
        session.add(
            ExecutionEvent(
                strategy_run_id=strategy_run.id,
                paper_order_id=None,
                event_type="paper_execution_blocked",
                severity="warning",
                blocks_execution=True,
                event_at=completed_at,
                message=resolved_message,
                details=result_summary,
            )
        )
        session.flush()
        session.refresh(strategy_run)
        strategy = strategy_run.strategy

        return PaperExecutionRunReport(
            run_id=str(strategy_run.id),
            strategy_id=strategy.strategy_id if strategy is not None else strategy_id,
            status=strategy_run.status.value,
            trigger_source=strategy_run.trigger_source,
            started_at=strategy_run.started_at.isoformat(),
            completed_at=strategy_run.completed_at.isoformat()
            if strategy_run.completed_at
            else None,
            result_summary=strategy_run.result_summary,
        )


def _finalize_mid_run_halt(
    settings: Settings,
    run_id: uuid.UUID,
    *,
    completed_at: datetime,
    summary: dict[str, Any],
    message: str,
) -> PaperExecutionRunReport:
    with session_scope(settings) as session:
        strategy_run = session.get(StrategyRun, run_id)
        if strategy_run is None:
            raise LookupError(f"Missing strategy_run '{run_id}'.")

        strategy_run.status = StrategyRunStatus.FAILED
        strategy_run.completed_at = completed_at
        strategy_run.error_message = message
        strategy_run.result_summary = summary
        session.add(
            ExecutionEvent(
                strategy_run_id=strategy_run.id,
                paper_order_id=None,
                event_type="paper_execution_blocked",
                severity="warning",
                blocks_execution=True,
                event_at=completed_at,
                message=message,
                details=summary,
            )
        )
        session.flush()
        session.refresh(strategy_run)
        strategy = strategy_run.strategy

        return PaperExecutionRunReport(
            run_id=str(strategy_run.id),
            strategy_id=strategy.strategy_id if strategy is not None else "unknown",
            status=strategy_run.status.value,
            trigger_source=strategy_run.trigger_source,
            started_at=strategy_run.started_at.isoformat(),
            completed_at=strategy_run.completed_at.isoformat()
            if strategy_run.completed_at
            else None,
            result_summary=strategy_run.result_summary,
        )


def _park_intent_unknown(
    session,
    resolved_settings: Settings,
    *,
    order: PaperOrder,
    run_id: uuid.UUID,
    error: AmbiguousOrderSubmissionError,
    trigger_source: str,
) -> None:
    """Move an intent with an unclean attempt history to UNKNOWN and record the event.

    Details carry closed class names, HTTP status and attempt numbers only. UNKNOWN
    without a broker_order_id is not resubmittable and counts as broker-touched.
    """

    order.last_submission_error = str(error)
    order.broker_payload = {
        "error": str(error),
        "submission_class": error.submission_class.value,
        "reason": error.reason,
    }
    details: dict[str, Any] = {
        "submission_class": error.submission_class.value,
        "reason": error.reason,
        "http_status": error.http_status,
        "attempt_numbers": list(error.attempt_numbers),
        "attempt_outcomes": [outcome for _, outcome in error.attempts],
        "trigger_source": trigger_source,
    }
    apply_order_transition(
        order.id,
        OrderTransitionRequest(
            strategy_run_id=run_id,
            event_type=OrderTransitionEventType.BROKER_STATUS_UNKNOWN,
            details=details,
        ),
        session=session,
        settings=resolved_settings,
    )
    session.add(
        ExecutionEvent(
            strategy_run_id=run_id,
            paper_order_id=order.id,
            event_type="submission_outcome_uncertain",
            severity="error",
            blocks_execution=True,
            event_at=datetime.now(UTC),
            message=(
                f"Order submission outcome is uncertain "
                f"(submission_class={error.submission_class.value}); the intent was parked "
                "in UNKNOWN and will not be re-sent automatically."
            ),
            details=details,
        )
    )
    session.flush()


def _is_resubmittable_order(paper_order: PaperOrder, *, failure_threshold: int) -> bool:
    if paper_order.broker_order_id:
        return False
    if paper_order.status == OrderLifecycleState.PENDING_SUBMISSION:
        return True
    return (
        paper_order.status == OrderLifecycleState.SUBMISSION_FAILED
        and paper_order.submission_attempt_count < failure_threshold
    )


def _broker_has_touched_order(paper_order: PaperOrder) -> bool:
    if paper_order.broker_order_id or paper_order.submitted_at or paper_order.last_broker_update_at:
        return True
    return paper_order.status in {
        OrderLifecycleState.SUBMITTED,
        OrderLifecycleState.PARTIALLY_FILLED,
        OrderLifecycleState.FILLED,
        OrderLifecycleState.CANCELED,
        OrderLifecycleState.REJECTED,
        OrderLifecycleState.EXPIRED,
        OrderLifecycleState.UNKNOWN,
    }


def schedule_reconciliation_after_partial_failure(
    resolved_settings: Settings,
    *,
    logger: logging.Logger,
    strategy_id: str,
    run_id: uuid.UUID,
    paper_order_id: uuid.UUID,
    session_date: date,
    client_order_id: str,
    broker_order_id: str | None,
    trigger_source: str,
    error: Exception,
) -> None:
    """DB-06: durable reconciliation hand-off for a broker/DB divergence.

    Call this ONLY when the broker call already succeeded (`submit_order`
    returned) but the subsequent local state-transition persist rolled
    back. The broker-side effect already happened and nothing else will
    ever revisit it, so rolling back the local write is a necessary but not
    sufficient response -- this records a durable `ExecutionEvent` marker
    (on its own independent `session_scope`, so it lands even though the
    triggering transaction rolled back) and emits a structured WARNING log,
    so the next reconciliation pass (`reconcile_paper_execution`, Phase 9)
    discovers and corrects the divergence. The caller is responsible for
    re-raising the original exception after this returns -- scheduling
    reconciliation never masks the underlying failure.
    """
    emit_structured_log(
        logger,
        logging.WARNING,
        "paper_execution_reconciliation_scheduled",
        strategy_id=strategy_id,
        run_id=str(run_id),
        paper_order_id=str(paper_order_id),
        session_date=session_date.isoformat(),
        client_order_id=client_order_id,
        broker_order_id=broker_order_id,
        trigger_source=trigger_source,
        error=str(error),
    )
    with session_scope(resolved_settings) as session:
        session.add(
            ExecutionEvent(
                strategy_run_id=run_id,
                paper_order_id=paper_order_id,
                event_type="reconciliation_scheduled",
                severity="warning",
                blocks_execution=False,
                event_at=datetime.now(UTC),
                message=(
                    f"Broker accepted order '{client_order_id}' "
                    f"(broker_order_id={broker_order_id!r}) but the local "
                    f"state-transition persist rolled back: {error}. "
                    "Reconciliation scheduled to resolve the divergence."
                ),
                details={
                    "strategy_id": strategy_id,
                    "session_date": session_date.isoformat(),
                    "client_order_id": client_order_id,
                    "broker_order_id": broker_order_id,
                    "trigger_source": trigger_source,
                    "error": str(error),
                },
            )
        )
