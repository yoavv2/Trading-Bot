"""CLI-first paper-order submission flow for approved risk decisions."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import case, select

from trading_platform.core.logging import build_log_context, emit_structured_log
from trading_platform.core.settings import Settings, load_settings
from trading_platform.db.models import (
    AccountSnapshot,
    ExecutionEvent,
    PaperFill,
    PaperOrder,
    Position,
    RiskEvent,
    Strategy,
    StrategyRun,
    StrategyRunStatus,
    StrategyRunType,
)
from trading_platform.db.models.symbol import Symbol
from trading_platform.db.session import session_scope
from trading_platform.services.alpaca import (
    AlpacaClient,
    AlpacaExecutionService,
    BrokerAccountSnapshot,
    BrokerFillSnapshot,
    BrokerOrderSnapshot,
    BrokerPositionSnapshot,
)
from trading_platform.services.bootstrap import ensure_strategy_record
from trading_platform.services.execution import ExecutionOrderStatus, ExecutionService, OrderIntent, OrderSide
from trading_platform.services.market_data_access import latest_completed_session
from trading_platform.services.operator_controls import load_strategy_control_state
from trading_platform.services.reconciliation import (
    load_broker_state,
    reconcile_paper_execution,
    recover_inflight_paper_orders,
)
from trading_platform.strategies.registry import StrategyRegistry, build_default_registry


@dataclass(frozen=True)
class PaperExecutionCandidate:
    risk_event_id: uuid.UUID
    source_risk_run_id: uuid.UUID
    symbol_id: uuid.UUID
    symbol: str
    session_date: date
    side: OrderSide
    quantity: Decimal
    reference_price: Decimal
    signal_reason: str
    decision_reason: str
    risk_metadata: dict[str, Any]


@dataclass(frozen=True)
class PaperExecutionRunReport:
    run_id: str
    strategy_id: str
    status: str
    trigger_source: str
    started_at: str
    completed_at: str | None
    result_summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "strategy_id": self.strategy_id,
            "status": self.status,
            "trigger_source": self.trigger_source,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "result_summary": self.result_summary,
        }


@dataclass(frozen=True)
class PaperSessionRunReport:
    strategy_id: str
    session_date: str
    trigger_source: str
    source_risk_run_id: str
    action: str
    execution_run_id: str | None
    execution_status: str | None
    result_summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "session_date": self.session_date,
            "trigger_source": self.trigger_source,
            "source_risk_run_id": self.source_risk_run_id,
            "action": self.action,
            "execution_run_id": self.execution_run_id,
            "execution_status": self.execution_status,
            "result_summary": self.result_summary,
        }


@dataclass(frozen=True)
class PaperSessionPlan:
    source_risk_run_id: uuid.UUID
    candidates: tuple[PaperExecutionCandidate, ...]
    existing_orders: tuple[PaperOrder, ...]
    missing_candidates: tuple[PaperExecutionCandidate, ...]


@dataclass(frozen=True)
class PaperStateSyncReport:
    strategy_id: str
    session_date: str
    synced_at: str
    orders_synced: int
    fills_ingested: int
    positions_opened: int
    positions_closed: int
    open_positions: int
    account_snapshot_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "session_date": self.session_date,
            "synced_at": self.synced_at,
            "orders_synced": self.orders_synced,
            "fills_ingested": self.fills_ingested,
            "positions_opened": self.positions_opened,
            "positions_closed": self.positions_closed,
            "open_positions": self.open_positions,
            "account_snapshot_id": self.account_snapshot_id,
        }


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
    session_date: date,
    symbol: str,
    risk_event_id: uuid.UUID,
) -> str:
    normalized_symbol = "".join(char for char in symbol.lower() if char.isalnum())[:8]
    return f"{prefix}-{session_date.strftime('%Y%m%d')}-{normalized_symbol}-{risk_event_id.hex[:12]}"


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
    logger = logging.getLogger("trading_platform.paper_execution")
    resolved_settings = settings or load_settings()
    resolved_registry = registry or build_default_registry(resolved_settings)
    strategy = resolved_registry.resolve(strategy_id)
    metadata = strategy.metadata
    control_state = load_strategy_control_state(
        strategy_id,
        settings=resolved_settings,
        registry=resolved_registry,
    )

    run_id = _create_paper_execution_run(
        resolved_settings,
        metadata,
        trigger_source=trigger_source,
        as_of_session=as_of_session,
        requested_risk_run_id=risk_run_id,
        strategy_status=control_state.status,
    )
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

    try:
        with session_scope(resolved_settings) as session:
            ensure_strategy_record(session, metadata)
            source_risk_run = _resolve_source_risk_run(
                session,
                strategy_id=strategy_id,
                as_of_session=as_of_session,
                requested_risk_run_id=risk_run_id,
            )
            candidates = _load_submission_candidates(session, source_risk_run.id)

        submitted_orders: list[dict[str, Any]] = []
        existing_orders: list[dict[str, Any]] = []
        safety_threshold = resolved_settings.execution.safety.repeated_failure_threshold

        for candidate in candidates:
            order_type = resolved_settings.execution.default_order_type
            time_in_force = resolved_settings.execution.default_time_in_force

            with session_scope(resolved_settings) as session:
                existing_order = session.execute(
                    select(PaperOrder).where(PaperOrder.source_risk_event_id == candidate.risk_event_id)
                ).scalar_one_or_none()
                if existing_order is not None and not _is_resubmittable_order(
                    existing_order,
                    failure_threshold=safety_threshold,
                ):
                    existing_orders.append(_paper_order_payload(existing_order))
                    continue

                if existing_order is None:
                    client_order_id = build_client_order_id(
                        prefix=resolved_settings.execution.client_order_id_prefix,
                        session_date=candidate.session_date,
                        symbol=candidate.symbol,
                        risk_event_id=candidate.risk_event_id,
                    )
                    paper_order = PaperOrder(
                        strategy_run_id=run_id,
                        source_risk_event_id=candidate.risk_event_id,
                        symbol_id=candidate.symbol_id,
                        intended_session_date=candidate.session_date,
                        side=candidate.side.value,
                        quantity=candidate.quantity,
                        order_type=order_type,
                        time_in_force=time_in_force,
                        client_order_id=client_order_id,
                        status="pending_submission",
                        broker_payload={},
                    )
                    session.add(paper_order)
                    session.flush()
                    pending_order = paper_order
                else:
                    pending_order = existing_order
                    client_order_id = existing_order.client_order_id

                pending_order.strategy_run_id = run_id
                pending_order.status = "pending_submission"
                pending_order.submission_attempt_count += 1
                pending_order.last_submission_attempt_at = datetime.now(UTC)
                pending_order.last_submission_error = None
                session.flush()
                pending_order_id = pending_order.id

            intent = OrderIntent(
                strategy_id=strategy_id,
                symbol=candidate.symbol,
                side=candidate.side,
                quantity=candidate.quantity,
                intended_session=candidate.session_date,
                client_order_id=client_order_id,
                reference_price=candidate.reference_price,
                metadata={
                    "signal_reason": candidate.signal_reason,
                    "decision_reason": candidate.decision_reason,
                    "risk_metadata": candidate.risk_metadata,
                    "source_risk_run_id": str(candidate.source_risk_run_id),
                    "source_risk_event_id": str(candidate.risk_event_id),
                },
            )

            try:
                result = broker_execution.submit_order(intent)
            except Exception as exc:
                with session_scope(resolved_settings) as session:
                    pending_order = session.get(PaperOrder, pending_order_id)
                    if pending_order is not None:
                        pending_order.status = "submission_failed"
                        pending_order.last_submission_error = str(exc)
                        pending_order.broker_payload = {"error": str(exc)}
                raise

            with session_scope(resolved_settings) as session:
                persisted_order = session.get(PaperOrder, pending_order_id)
                if persisted_order is None:
                    raise LookupError(f"Missing pending paper_order '{pending_order_id}'.")

                persisted_order.status = (
                    "submission_rejected"
                    if result.status == ExecutionOrderStatus.REJECTED
                    else "submitted"
                )
                persisted_order.broker_order_id = result.broker_order_id or None
                persisted_order.broker_status = result.broker_status
                persisted_order.submitted_at = result.submitted_at
                persisted_order.last_submission_error = None
                persisted_order.broker_payload = result.raw_payload
                session.flush()
                session.refresh(persisted_order)
                submitted_orders.append(_paper_order_payload(persisted_order))

        summary = {
            "stage": "completed",
            "strategy_id": strategy_id,
            "as_of_session": as_of_session.isoformat(),
            "requested_risk_run_id": risk_run_id,
            "source_risk_run_id": str(source_risk_run.id),
            "approved_candidate_count": len(candidates),
            "submitted_count": len(submitted_orders),
            "existing_count": len(existing_orders),
            "submitted_orders": submitted_orders,
            "existing_orders": existing_orders,
            "broker_provider": resolved_settings.broker.provider,
            "execution_defaults": resolved_settings.execution.model_dump(mode="json"),
        }
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
                "source_risk_run_id": str(source_risk_run.id) if source_risk_run is not None else None,
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

    report = _update_paper_execution_run(
        resolved_settings,
        run_id,
        status=StrategyRunStatus.SUCCEEDED,
        completed_at=datetime.now(UTC),
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
    logger = logging.getLogger("trading_platform.paper_execution")
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
        )

    control_state = load_strategy_control_state(
        resolved_strategy_id,
        settings=resolved_settings,
        registry=registry,
    )
    existing_orders = [_paper_order_payload(order) for order in session_plan.existing_orders]
    base_summary = {
        "strategy_id": resolved_strategy_id,
        "as_of_session": as_of_session.isoformat(),
        "source_risk_run_id": str(session_plan.source_risk_run_id),
        "approved_candidate_count": len(session_plan.candidates),
        "existing_count": len(session_plan.existing_orders),
        "missing_count": len(session_plan.missing_candidates),
        "existing_orders": existing_orders,
        "strategy_status": control_state.status,
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
    emit_structured_log(
        logger,
        logging.INFO,
        "paper_session_completed",
        strategy_id=resolved_strategy_id,
        run_id=execution_report.run_id,
        session_date=as_of_session.isoformat(),
        strategy_status=control_state.status,
        trigger_source=resolved_trigger_source,
        action="submitted_missing_orders",
    )

    return PaperSessionRunReport(
        strategy_id=resolved_strategy_id,
        session_date=as_of_session.isoformat(),
        trigger_source=resolved_trigger_source,
        source_risk_run_id=str(session_plan.source_risk_run_id),
        action="submitted_missing_orders",
        execution_run_id=execution_report.run_id,
        execution_status=execution_report.status,
        result_summary=result_summary,
    )


def sync_paper_state(
    strategy_id: str | None = None,
    *,
    as_of_session: date,
    settings: Settings | None = None,
    registry: StrategyRegistry | None = None,
    broker_client: AlpacaClient | None = None,
) -> PaperStateSyncReport:
    resolved_settings = settings or load_settings()
    resolved_registry = registry or build_default_registry(resolved_settings)
    resolved_strategy_id = strategy_id or resolved_settings.execution.paper_session_runner.default_strategy_id
    strategy = resolved_registry.resolve(resolved_strategy_id)
    synced_at = datetime.now(UTC)

    owns_broker_client = broker_client is None
    client = broker_client or AlpacaClient(resolved_settings.broker.alpaca)

    try:
        broker_orders = client.list_orders()
        broker_fills = client.list_fills()
        broker_positions = client.list_positions()
        broker_account = client.get_account()

        with session_scope(resolved_settings) as session:
            strategy_record = ensure_strategy_record(session, strategy.metadata)
            local_orders = session.execute(
                select(PaperOrder)
                .join(StrategyRun, StrategyRun.id == PaperOrder.strategy_run_id)
                .where(StrategyRun.strategy_id == strategy_record.id)
                .order_by(PaperOrder.created_at.asc())
            ).scalars().all()
            local_orders_by_broker_id = {
                order.broker_order_id: order for order in local_orders if order.broker_order_id
            }
            local_orders_by_client_id = {
                order.client_order_id: order for order in local_orders if order.client_order_id
            }

            orders_synced = _sync_paper_orders(
                broker_orders,
                local_orders_by_broker_id=local_orders_by_broker_id,
                local_orders_by_client_id=local_orders_by_client_id,
                synced_at=synced_at,
            )
            fills_ingested = _ingest_paper_fills(
                session,
                broker_fills,
                local_orders_by_broker_id=local_orders_by_broker_id,
            )
            positions_opened, positions_closed = _sync_positions_from_broker(
                session,
                strategy_record.id,
                broker_positions,
                as_of_session=as_of_session,
                synced_at=synced_at,
            )
            snapshot = _record_broker_account_snapshot(
                session,
                strategy_record.id,
                broker_account,
                broker_positions,
                synced_at=synced_at,
            )

            return PaperStateSyncReport(
                strategy_id=resolved_strategy_id,
                session_date=as_of_session.isoformat(),
                synced_at=synced_at.isoformat(),
                orders_synced=orders_synced,
                fills_ingested=fills_ingested,
                positions_opened=positions_opened,
                positions_closed=positions_closed,
                open_positions=len(broker_positions),
                account_snapshot_id=str(snapshot.id),
            )
    finally:
        if owns_broker_client:
            client.close()


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


def _load_submission_candidates(session, source_risk_run_id: uuid.UUID) -> list[PaperExecutionCandidate]:
    side_priority = case((RiskEvent.signal_direction == "exit", 0), else_=1)
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
        if risk_event.proposed_quantity is None or risk_event.proposed_quantity <= 0:
            continue
        candidates.append(
            PaperExecutionCandidate(
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
        )
    return candidates


def _build_paper_session_plan(
    session,
    *,
    strategy_id: str,
    as_of_session: date,
    requested_risk_run_id: str | None,
    failure_threshold: int,
) -> PaperSessionPlan:
    source_risk_run = _resolve_source_risk_run(
        session,
        strategy_id=strategy_id,
        as_of_session=as_of_session,
        requested_risk_run_id=requested_risk_run_id,
    )
    candidates = _load_submission_candidates(session, source_risk_run.id)
    existing_orders: list[PaperOrder] = []

    if candidates:
        candidate_ids = [candidate.risk_event_id for candidate in candidates]
        existing_orders = session.execute(
            select(PaperOrder)
            .where(PaperOrder.source_risk_event_id.in_(candidate_ids))
            .order_by(PaperOrder.client_order_id.asc())
        ).scalars().all()

    existing_ids = {
        order.source_risk_event_id
        for order in existing_orders
        if not _is_resubmittable_order(order, failure_threshold=failure_threshold)
    }
    missing_candidates = [candidate for candidate in candidates if candidate.risk_event_id not in existing_ids]

    return PaperSessionPlan(
        source_risk_run_id=source_risk_run.id,
        candidates=tuple(candidates),
        existing_orders=tuple(existing_orders),
        missing_candidates=tuple(missing_candidates),
    )


def _create_paper_execution_run(
    settings: Settings,
    metadata,
    *,
    trigger_source: str,
    as_of_session: date,
    requested_risk_run_id: str | None,
    strategy_status: str,
) -> uuid.UUID:
    with session_scope(settings) as session:
        strategy_record = ensure_strategy_record(session, metadata)
        strategy_run = StrategyRun(
            strategy_id=strategy_record.id,
            run_type=StrategyRunType.PAPER_EXECUTION,
            status=StrategyRunStatus.PENDING,
            trigger_source=trigger_source,
            parameters_snapshot={
                "strategy": metadata.to_public_dict(),
                "as_of_session": as_of_session.isoformat(),
                "requested_risk_run_id": requested_risk_run_id,
                "strategy_status": strategy_status,
                "broker": settings.broker.model_dump(mode="json"),
                "execution": settings.execution.model_dump(mode="json"),
            },
            result_summary={
                "stage": "pending",
                "strategy_id": metadata.strategy_id,
                "as_of_session": as_of_session.isoformat(),
                "requested_risk_run_id": requested_risk_run_id,
                "strategy_status": strategy_status,
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
            completed_at=strategy_run.completed_at.isoformat() if strategy_run.completed_at else None,
            result_summary=strategy_run.result_summary,
        )


def _paper_order_payload(paper_order: PaperOrder) -> dict[str, Any]:
    return {
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
    }


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
) -> PaperExecutionRunReport:
    completed_at = datetime.now(UTC)
    message = (
        f"Strategy '{strategy_id}' is disabled; paper execution blocked before broker submission begins."
    )
    result_summary = {
        "stage": "blocked",
        "action": "blocked_strategy_disabled",
        "strategy_id": strategy_id,
        "as_of_session": as_of_session.isoformat(),
        "requested_risk_run_id": requested_risk_run_id,
        "blocked_reason": blocked_reason,
        "strategy_status": strategy_status,
        "trigger_source": trigger_source,
        "message": message,
    }

    with session_scope(settings) as session:
        strategy_run = session.get(StrategyRun, run_id)
        if strategy_run is None:
            raise LookupError(f"Missing strategy_run '{run_id}'.")

        strategy_run.status = StrategyRunStatus.FAILED
        strategy_run.completed_at = completed_at
        strategy_run.error_message = message
        strategy_run.result_summary = result_summary
        session.add(
            ExecutionEvent(
                strategy_run_id=strategy_run.id,
                paper_order_id=None,
                event_type="paper_execution_blocked",
                severity="warning",
                blocks_execution=True,
                event_at=completed_at,
                message=message,
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
            completed_at=strategy_run.completed_at.isoformat() if strategy_run.completed_at else None,
            result_summary=strategy_run.result_summary,
        )


def _is_resubmittable_order(paper_order: PaperOrder, *, failure_threshold: int) -> bool:
    if paper_order.broker_order_id:
        return False
    if paper_order.status == "pending_submission":
        return True
    return (
        paper_order.status == "submission_failed"
        and paper_order.submission_attempt_count < failure_threshold
    )


def _paper_order_status_from_broker_status(status: ExecutionOrderStatus) -> str:
    if status in {ExecutionOrderStatus.PENDING, ExecutionOrderStatus.ACCEPTED}:
        return "submitted"
    if status == ExecutionOrderStatus.PARTIALLY_FILLED:
        return "partially_filled"
    if status == ExecutionOrderStatus.FILLED:
        return "filled"
    if status == ExecutionOrderStatus.CANCELED:
        return "canceled"
    if status == ExecutionOrderStatus.REJECTED:
        return "rejected"
    if status == ExecutionOrderStatus.EXPIRED:
        return "expired"
    return "unknown"


def _sync_paper_orders(
    broker_orders: list[BrokerOrderSnapshot],
    *,
    local_orders_by_broker_id: dict[str, PaperOrder],
    local_orders_by_client_id: dict[str, PaperOrder],
    synced_at: datetime,
) -> int:
    synced_count = 0
    for broker_order in broker_orders:
        local_order = local_orders_by_broker_id.get(broker_order.broker_order_id)
        if local_order is None:
            local_order = local_orders_by_client_id.get(broker_order.client_order_id)
        if local_order is None:
            continue

        if not local_order.broker_order_id:
            local_order.broker_order_id = broker_order.broker_order_id
        local_order.status = _paper_order_status_from_broker_status(broker_order.status)
        local_order.broker_status = broker_order.broker_status
        local_order.submitted_at = broker_order.submitted_at or local_order.submitted_at
        local_order.filled_at = broker_order.filled_at
        local_order.canceled_at = broker_order.canceled_at
        local_order.last_broker_update_at = broker_order.updated_at
        local_order.last_synced_at = synced_at
        local_order.broker_payload = broker_order.raw_payload

        if local_order.broker_order_id:
            local_orders_by_broker_id[local_order.broker_order_id] = local_order
        synced_count += 1
    return synced_count


def _ingest_paper_fills(
    session,
    broker_fills: list[BrokerFillSnapshot],
    *,
    local_orders_by_broker_id: dict[str, PaperOrder],
) -> int:
    existing_fill_ids = set(session.execute(select(PaperFill.broker_fill_id)).scalars().all())
    ingested = 0

    for broker_fill in broker_fills:
        if broker_fill.broker_fill_id in existing_fill_ids:
            continue
        local_order = local_orders_by_broker_id.get(broker_fill.broker_order_id)
        if local_order is None:
            continue

        session.add(
            PaperFill(
                paper_order_id=local_order.id,
                symbol_id=local_order.symbol_id,
                broker_fill_id=broker_fill.broker_fill_id,
                broker_order_id=broker_fill.broker_order_id,
                side=broker_fill.side.value,
                quantity=broker_fill.quantity,
                price=broker_fill.price,
                filled_at=broker_fill.filled_at,
                broker_payload=broker_fill.raw_payload,
            )
        )
        existing_fill_ids.add(broker_fill.broker_fill_id)
        if local_order.filled_at is None or broker_fill.filled_at > local_order.filled_at:
            local_order.filled_at = broker_fill.filled_at
        ingested += 1

    return ingested


def _sync_positions_from_broker(
    session,
    strategy_row_id: uuid.UUID,
    broker_positions: list[BrokerPositionSnapshot],
    *,
    as_of_session: date,
    synced_at: datetime,
) -> tuple[int, int]:
    existing_open_positions = session.execute(
        select(Position).where(
            Position.strategy_id == strategy_row_id,
            Position.status == "open",
        )
    ).scalars().all()
    existing_by_symbol = {position.symbol_ref.ticker: position for position in existing_open_positions}
    opened = 0
    closed = 0

    for broker_position in broker_positions:
        symbol_row = _ensure_symbol(session, broker_position.symbol)
        existing_position = existing_by_symbol.pop(broker_position.symbol, None)
        if existing_position is None:
            session.add(
                Position(
                    strategy_id=strategy_row_id,
                    symbol_id=symbol_row.id,
                    status="open",
                    quantity=broker_position.quantity,
                    average_entry_price=broker_position.average_entry_price,
                    cost_basis=broker_position.cost_basis,
                    opened_session_date=as_of_session,
                    opened_at=synced_at,
                )
            )
            opened += 1
            continue

        existing_position.quantity = broker_position.quantity
        existing_position.average_entry_price = broker_position.average_entry_price
        existing_position.cost_basis = broker_position.cost_basis
        existing_position.status = "open"
        existing_position.opened_session_date = existing_position.opened_session_date or as_of_session
        existing_position.opened_at = existing_position.opened_at or synced_at
        existing_position.closed_session_date = None
        existing_position.closed_at = None

    for stale_position in existing_by_symbol.values():
        stale_position.status = "closed"
        stale_position.closed_session_date = as_of_session
        stale_position.closed_at = synced_at
        closed += 1

    return opened, closed


def _record_broker_account_snapshot(
    session,
    strategy_row_id: uuid.UUID,
    broker_account: BrokerAccountSnapshot,
    broker_positions: list[BrokerPositionSnapshot],
    *,
    synced_at: datetime,
) -> AccountSnapshot:
    gross_exposure = sum((abs(position.market_value) for position in broker_positions), start=Decimal("0"))
    snapshot = AccountSnapshot(
        strategy_id=strategy_row_id,
        source_run_id=None,
        snapshot_source="broker_sync",
        snapshot_at=synced_at,
        cash=broker_account.cash,
        gross_exposure=gross_exposure,
        total_equity=broker_account.equity,
        buying_power=broker_account.buying_power,
        open_positions=len(broker_positions),
    )
    session.add(snapshot)
    session.flush()
    return snapshot


def _ensure_symbol(session, ticker: str) -> Symbol:
    symbol_row = session.execute(select(Symbol).where(Symbol.ticker == ticker)).scalar_one_or_none()
    if symbol_row is not None:
        return symbol_row

    symbol_row = Symbol(ticker=ticker, active=True)
    session.add(symbol_row)
    session.flush()
    return symbol_row
