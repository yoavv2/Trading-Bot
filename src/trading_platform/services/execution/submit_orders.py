"""Paper order submission + session orchestration + intent-decision logic.

STRUCT-04 part 2 (12-04): submission-side split of the former monolithic
`services/paper_execution.py`. Broker-state sync (orders/fills/positions/
account) lives in the sibling `sync_orders.py`; shared dataclasses and
cross-cutting helpers live in `_paper_common.py`.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, case, or_, select
from sqlalchemy.orm import joinedload

from trading_platform.core.logging import build_log_context, emit_structured_log, get_logger
from trading_platform.core.settings import Settings, load_settings
from trading_platform.db.models import (
    ExecutionEvent,
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
from trading_platform.services.alpaca import AlpacaClient, AlpacaExecutionService
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
from trading_platform.services.execution.contracts import ExecutionService, OrderIntent, OrderSide
from trading_platform.services.execution.idempotency import (
    DerivedOrderIdentity,
    derive_order_identity,
)
from trading_platform.services.execution.idempotency import (
    build_client_order_id as _build_client_order_id,
)
from trading_platform.services.execution.transition import (
    OrderTransitionRequest,
    apply_order_transition,
)
from trading_platform.services.market_data_access import latest_completed_session
from trading_platform.services.operator_controls import (
    BLOCKED_REASON_GLOBAL_KILL_SWITCH,
    KillSwitchStateSnapshot,
    load_kill_switch_state,
    load_strategy_control_state,
)
from trading_platform.services.reconciliation import (
    apply_reconciliation_corrections,
    load_broker_state,
    reconcile_paper_execution,
    recover_inflight_paper_orders,
)
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
) -> PaperExecutionRunReport:
    """Lock-guarded entrypoint (LOCK-01/02/03/05): resolve pure state, then
    acquire the (strategy_id, session_date) advisory lock BEFORE any write or
    broker call. All side effects happen inside `_run_paper_order_submission_guarded`,
    which runs entirely within the lock's `with` block below.
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

    control_state = load_strategy_control_state(
        strategy_id,
        settings=resolved_settings,
        registry=resolved_registry,
    )
    kill_switch_state = load_kill_switch_state(
        settings=resolved_settings,
        registry=resolved_registry,
    )
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
    source_risk_run: StrategyRun | None = None
    strategy_row_id: uuid.UUID | None = None

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

        submitted_orders: list[dict[str, Any]] = []
        existing_orders: list[dict[str, Any]] = []
        reused_orders: list[dict[str, Any]] = []
        versioned_orders: list[dict[str, Any]] = []
        skipped_by_kill_switch: list[dict[str, Any]] = []
        safety_threshold = resolved_settings.execution.safety.repeated_failure_threshold
        mid_run_kill_switch: KillSwitchStateSnapshot | None = None

        for candidate in candidates:
            mid_run_kill_switch = load_kill_switch_state(
                settings=resolved_settings,
                registry=resolved_registry,
            )
            if mid_run_kill_switch.is_tripped:
                skipped_by_kill_switch.append(
                    {
                        "symbol": candidate.symbol,
                        "side": candidate.side.value,
                        "quantity": float(candidate.quantity),
                        "session_date": candidate.session_date.isoformat(),
                        "source_risk_event_id": str(candidate.risk_event_id),
                    }
                )
                emit_structured_log(
                    logger,
                    logging.WARNING,
                    "paper_execution_skipped",
                    strategy_id=strategy_id,
                    run_id=str(run_id),
                    session_date=as_of_session.isoformat(),
                    symbol=candidate.symbol,
                    kill_switch_state=mid_run_kill_switch.state,
                    blocked_reason=BLOCKED_REASON_GLOBAL_KILL_SWITCH,
                    trigger_source=trigger_source,
                )
                continue

            order_type = resolved_settings.execution.default_order_type
            time_in_force = resolved_settings.execution.default_time_in_force

            with session_scope(resolved_settings) as session:
                if strategy_row_id is None:
                    raise LookupError("Missing strategy row for paper execution.")

                intent_decision = _resolve_paper_intent_decision(
                    session,
                    strategy_row_id=strategy_row_id,
                    strategy_id=strategy_id,
                    prefix=resolved_settings.execution.client_order_id_prefix,
                    candidate=candidate,
                    failure_threshold=safety_threshold,
                )

                if intent_decision.action == "reuse_existing":
                    existing_order = session.get(PaperOrder, intent_decision.existing_order_id)
                    if existing_order is None:
                        raise LookupError(
                            f"Missing reusable paper_order '{intent_decision.existing_order_id}'."
                        )
                    _record_intent_decision_event(
                        session,
                        strategy_run_id=run_id,
                        paper_order_id=existing_order.id,
                        event_type="paper_order_reused",
                        message=(
                            f"Reused existing intent '{existing_order.client_order_id}' for identical "
                            "material order inputs; no new submission was attempted."
                        ),
                        details=intent_decision.summary,
                    )
                    payload = _paper_order_payload(
                        existing_order,
                        intent_decision=intent_decision.summary,
                        supersedes_client_order_id=intent_decision.supersedes_client_order_id,
                    )
                    existing_orders.append(payload)
                    reused_orders.append(payload)
                    continue

                if intent_decision.existing_order_id is None:
                    paper_order = PaperOrder(
                        strategy_run_id=run_id,
                        source_risk_event_id=candidate.risk_event_id,
                        symbol_id=candidate.symbol_id,
                        intended_session_date=candidate.session_date,
                        side=candidate.side.value,
                        quantity=candidate.quantity,
                        order_type=order_type,
                        time_in_force=time_in_force,
                        intent_hash=intent_decision.identity.intent_hash,
                        intent_version=intent_decision.intent_version,
                        supersedes_paper_order_id=intent_decision.supersedes_paper_order_id,
                        client_order_id=intent_decision.identity.client_order_id,
                        status=OrderLifecycleState.PENDING_SUBMISSION,
                        broker_payload={},
                    )
                    session.add(paper_order)
                    session.flush()
                    pending_order = paper_order
                    transition_event_type = OrderTransitionEventType.INTENT_REGISTERED
                else:
                    retrieved_order = session.get(PaperOrder, intent_decision.existing_order_id)
                    if retrieved_order is None:
                        raise LookupError(
                            f"Missing retryable paper_order '{intent_decision.existing_order_id}'."
                        )
                    pending_order = retrieved_order
                    transition_event_type = OrderTransitionEventType.RETRY_REQUESTED

                pending_order.strategy_run_id = run_id
                apply_order_transition(
                    pending_order.id,
                    OrderTransitionRequest(
                        strategy_run_id=run_id,
                        event_type=transition_event_type,
                        details={
                            "trigger_source": trigger_source,
                            "source_risk_event_id": str(candidate.risk_event_id),
                            "intent_decision": intent_decision.summary,
                        },
                    ),
                    session=session,
                    settings=resolved_settings,
                )
                pending_order.submission_attempt_count += 1
                pending_order.last_submission_attempt_at = datetime.now(UTC)
                pending_order.last_submission_error = None
                session.flush()
                pending_order_id = pending_order.id

                if intent_decision.action == "create_new_version":
                    _record_intent_decision_event(
                        session,
                        strategy_run_id=run_id,
                        paper_order_id=pending_order.id,
                        event_type="paper_order_versioned",
                        message=(
                            f"Created intent version {pending_order.intent_version} after superseding "
                            f"broker-touched order '{intent_decision.supersedes_client_order_id}'."
                        ),
                        details=intent_decision.summary,
                    )

            intent = OrderIntent(
                strategy_id=strategy_id,
                symbol=candidate.symbol,
                side=candidate.side,
                quantity=candidate.quantity,
                intended_session=candidate.session_date,
                client_order_id=pending_order.client_order_id,
                intent_hash=pending_order.intent_hash,
                intent_version=pending_order.intent_version,
                reference_price=candidate.reference_price,
                metadata={
                    "signal_reason": candidate.signal_reason,
                    "decision_reason": candidate.decision_reason,
                    "risk_metadata": candidate.risk_metadata,
                    "source_risk_run_id": str(candidate.source_risk_run_id),
                    "source_risk_event_id": str(candidate.risk_event_id),
                },
            )

            # DB-04/DB-05 invariant (explicit transaction boundary + commit-
            # after-both): the broker call below sits OUTSIDE any open
            # session/transaction -- the pre-broker `session_scope` above
            # already committed the PENDING_SUBMISSION intent (durable
            # idempotency), and the two branches below each open their OWN
            # fresh `session_scope`. The success branch's commit is
            # contingent on BOTH (a) the broker call having already
            # returned successfully (we are past `submit_order` without an
            # exception) AND (b) the state-transition write below flushing
            # cleanly -- if either is false, that success is never
            # committed as success. A broker exception never enters the
            # success-persist session at all (see the `except` branch).
            try:
                result = broker_execution.submit_order(intent)
            except Exception as exc:
                with session_scope(resolved_settings) as session:
                    failed_order = session.get(PaperOrder, pending_order_id)
                    if failed_order is not None:
                        failed_order.last_submission_error = str(exc)
                        failed_order.broker_payload = {"error": str(exc)}
                        apply_order_transition(
                            failed_order.id,
                            OrderTransitionRequest(
                                strategy_run_id=run_id,
                                event_type=OrderTransitionEventType.SUBMISSION_FAILED,
                                details={
                                    "error": str(exc),
                                    "trigger_source": trigger_source,
                                },
                            ),
                            session=session,
                            settings=resolved_settings,
                        )
                # No broker side effect occurred (submit_order raised before
                # returning) -- this is a clean failure, not a divergence
                # between broker and local state. Reconciliation is
                # deliberately NOT scheduled on this path (DB-06 scope).
                raise

            try:
                with session_scope(resolved_settings) as session:
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
                                "trigger_source": trigger_source,
                            },
                            event_at=transition_recorded_at,
                        ),
                        session=session,
                        settings=resolved_settings,
                    )
                    session.flush()
                    session.refresh(persisted_order)
                    payload = _paper_order_payload(
                        persisted_order,
                        intent_decision=intent_decision.summary,
                        supersedes_client_order_id=intent_decision.supersedes_client_order_id,
                    )
                    submitted_orders.append(payload)
                    if intent_decision.action == "retry_existing":
                        reused_orders.append(payload)
                    if intent_decision.action == "create_new_version":
                        versioned_orders.append(payload)
            except Exception as exc:
                # DB-06: the broker has ALREADY accepted this order (we are
                # past `submit_order` without an exception) but the local
                # write that would record that acceptance just rolled back
                # (`session_scope` rolls back on any exception). The broker
                # and the local DB are now divergent for this order --
                # rolling back the local write is necessary but not
                # sufficient: a reconciliation pass must be scheduled so the
                # divergence is discovered and corrected rather than
                # silently swallowed. The original exception always
                # propagates after scheduling.
                schedule_reconciliation_after_partial_failure(
                    resolved_settings,
                    logger=logger,
                    strategy_id=strategy_id,
                    run_id=run_id,
                    paper_order_id=pending_order_id,
                    session_date=candidate.session_date,
                    client_order_id=pending_order.client_order_id,
                    broker_order_id=result.broker_order_id,
                    trigger_source=trigger_source,
                    error=exc,
                )
                raise

        halted_mid_run = bool(skipped_by_kill_switch) and (
            mid_run_kill_switch is not None and mid_run_kill_switch.is_tripped
        )
        summary: dict[str, Any] = {
            "stage": "blocked_mid_run" if halted_mid_run else "completed",
            "strategy_id": strategy_id,
            "as_of_session": as_of_session.isoformat(),
            "requested_risk_run_id": risk_run_id,
            "source_risk_run_id": str(source_risk_run.id),
            "approved_candidate_count": len(candidates),
            "submitted_count": len(submitted_orders),
            "existing_count": len(existing_orders),
            "reused_count": len(reused_orders),
            "versioned_count": len(versioned_orders),
            "skipped_by_kill_switch_count": len(skipped_by_kill_switch),
            "submitted_orders": submitted_orders,
            "existing_orders": existing_orders,
            "reused_orders": reused_orders,
            "versioned_orders": versioned_orders,
            "skipped_by_kill_switch": skipped_by_kill_switch,
            "broker_provider": resolved_settings.broker.provider,
            "execution_defaults": resolved_settings.execution.model_dump(mode="json"),
        }
        halted_message: str | None = None
        if halted_mid_run and mid_run_kill_switch is not None:
            halted_message = (
                "Global kill switch tripped during session; "
                f"{len(skipped_by_kill_switch)} pending candidate(s) halted before broker submission."
            )
            summary["blocked_reason"] = BLOCKED_REASON_GLOBAL_KILL_SWITCH
            summary["action"] = "blocked_mid_run_global_kill_switch"
            summary["message"] = halted_message
            summary["kill_switch"] = mid_run_kill_switch.to_dict()
    except Exception as exc:
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

    completed_at = datetime.now(UTC)
    if halted_mid_run and mid_run_kill_switch is not None:
        report = _finalize_mid_run_kill_switch_halt(
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
            kill_switch_state=mid_run_kill_switch.state,
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


def run_paper_session(
    strategy_id: str | None = None,
    *,
    as_of_session: date,
    risk_run_id: str | None = None,
    trigger_source: str | None = None,
    settings: Settings | None = None,
    registry: StrategyRegistry | None = None,
    execution_service: ExecutionService | None = None,
    broker_client: AlpacaClient | None = None,
) -> PaperSessionRunReport:
    logger = get_logger("trading_platform.paper_execution")
    resolved_settings = settings or load_settings()
    runner_settings = resolved_settings.execution.paper_session_runner
    resolved_strategy_id = strategy_id or runner_settings.default_strategy_id
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

    control_state = load_strategy_control_state(
        resolved_strategy_id,
        settings=resolved_settings,
        registry=registry,
    )
    kill_switch_state = load_kill_switch_state(
        settings=resolved_settings,
        registry=registry,
    )
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

    if not control_state.is_execution_enabled:
        blocked_execution_report = run_paper_order_submission(
            resolved_strategy_id,
            as_of_session=as_of_session,
            risk_run_id=str(session_plan.source_risk_run_id),
            trigger_source=resolved_trigger_source,
            settings=resolved_settings,
            registry=registry,
            execution_service=execution_service,
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
        )

    if not session_plan.missing_candidates:
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
        )

    execution_report = run_paper_order_submission(
        resolved_strategy_id,
        as_of_session=as_of_session,
        risk_run_id=str(session_plan.source_risk_run_id),
        trigger_source=resolved_trigger_source,
        settings=resolved_settings,
        registry=registry,
        execution_service=execution_service,
    )
    result_summary = dict(execution_report.result_summary)
    result_summary["session_preflight"] = base_summary
    blocked_reason = result_summary.get("blocked_reason")
    if blocked_reason == BLOCKED_REASON_GLOBAL_KILL_SWITCH:
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
    )


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
        parameters_session = run.parameters_snapshot.get("as_of_session")
        summary_session = run.result_summary.get("as_of_session")
        if parameters_session == target_session or summary_session == target_session:
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
            or_(
                StrategyRun.parameters_snapshot["as_of_session"].as_string() == target_session,
                StrategyRun.result_summary["as_of_session"].as_string() == target_session,
            ),
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
) -> uuid.UUID:
    """Insert the run row at status=RUNNING -- the literal first persisted
    write for this run (LOCK-03), acquired before kill-switch/control state
    is even read. strategy_status is genuinely unknown at this point (it is
    loaded moments later, after stale reclaim runs against this row); the
    accurate value is written into result_summary by the very next update.
    """
    with session_scope(settings) as session:
        strategy_record = ensure_strategy_record(session, metadata)
        strategy_run = StrategyRun(
            strategy_id=strategy_record.id,
            run_type=StrategyRunType.PAPER_EXECUTION,
            status=StrategyRunStatus.RUNNING,
            trigger_source=trigger_source,
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


def _finalize_mid_run_kill_switch_halt(
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
