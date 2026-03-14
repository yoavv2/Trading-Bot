"""CLI-first paper-order submission flow for approved risk decisions."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import case, select

from trading_platform.core.settings import Settings, load_settings
from trading_platform.db.models import PaperOrder, RiskEvent, Strategy, StrategyRun, StrategyRunStatus, StrategyRunType
from trading_platform.db.models.symbol import Symbol
from trading_platform.db.session import session_scope
from trading_platform.services.alpaca import AlpacaExecutionService
from trading_platform.services.bootstrap import ensure_strategy_record
from trading_platform.services.execution import ExecutionOrderStatus, ExecutionService, OrderIntent, OrderSide
from trading_platform.services.market_data_access import latest_completed_session
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
    resolved_settings = settings or load_settings()
    resolved_registry = registry or build_default_registry(resolved_settings)
    strategy = resolved_registry.resolve(strategy_id)
    metadata = strategy.metadata

    run_id = _create_paper_execution_run(
        resolved_settings,
        metadata,
        trigger_source=trigger_source,
        as_of_session=as_of_session,
        requested_risk_run_id=risk_run_id,
    )
    _update_paper_execution_run(
        resolved_settings,
        run_id,
        status=StrategyRunStatus.RUNNING,
        result_summary={
            "stage": "running",
            "strategy_id": metadata.strategy_id,
            "as_of_session": as_of_session.isoformat(),
            "requested_risk_run_id": risk_run_id,
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

        for candidate in candidates:
            client_order_id = build_client_order_id(
                prefix=resolved_settings.execution.client_order_id_prefix,
                session_date=candidate.session_date,
                symbol=candidate.symbol,
                risk_event_id=candidate.risk_event_id,
            )
            order_type = resolved_settings.execution.default_order_type
            time_in_force = resolved_settings.execution.default_time_in_force

            with session_scope(resolved_settings) as session:
                existing_order = session.execute(
                    select(PaperOrder).where(PaperOrder.source_risk_event_id == candidate.risk_event_id)
                ).scalar_one_or_none()
                if existing_order is not None:
                    existing_orders.append(_paper_order_payload(existing_order))
                    continue

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
                pending_order_id = paper_order.id

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
            },
        )
        raise
    finally:
        if owns_execution_service and hasattr(broker_execution, "close"):
            broker_execution.close()

    return _update_paper_execution_run(
        resolved_settings,
        run_id,
        status=StrategyRunStatus.SUCCEEDED,
        completed_at=datetime.now(UTC),
        result_summary=summary,
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


def _create_paper_execution_run(
    settings: Settings,
    metadata,
    *,
    trigger_source: str,
    as_of_session: date,
    requested_risk_run_id: str | None,
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
                "broker": settings.broker.model_dump(mode="json"),
                "execution": settings.execution.model_dump(mode="json"),
            },
            result_summary={
                "stage": "pending",
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
        "submitted_at": paper_order.submitted_at.isoformat() if paper_order.submitted_at else None,
    }
