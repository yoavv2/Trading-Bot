"""Broker-to-local reconciliation and unsafe-state persistence."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from trading_platform.core.logging import build_log_context, emit_structured_log
from trading_platform.core.settings import Settings, load_settings
from trading_platform.db.models import (
    AccountSnapshot,
    ExecutionEvent,
    OrderLifecycleState,
    OrderTransitionEventType,
    PaperFill,
    PaperOrder,
    Position,
    Strategy,
    StrategyRun,
    StrategyRunStatus,
    StrategyRunType,
)
from trading_platform.db.session import session_scope
from trading_platform.services.alpaca import (
    AlpacaClient,
    BrokerAccountSnapshot,
    BrokerFillSnapshot,
    BrokerOrderSnapshot,
    BrokerPositionSnapshot,
)
from trading_platform.services.bootstrap import ensure_strategy_record
from trading_platform.services.execution import ExecutionOrderStatus
from trading_platform.services.order_state_machine import (
    OrderTransitionRequest,
    apply_order_transition,
    resolve_transition_target,
)
from trading_platform.strategies.registry import StrategyRegistry, build_default_registry

_ACTIVE_LOCAL_ORDER_STATUSES = {
    OrderLifecycleState.PENDING_SUBMISSION,
    OrderLifecycleState.SUBMITTED,
    OrderLifecycleState.PARTIALLY_FILLED,
}
_MONEY_TOLERANCE = Decimal("0.01")
_QUANTITY_TOLERANCE = Decimal("0.000001")


@dataclass(frozen=True)
class BrokerStateSnapshot:
    orders: tuple[BrokerOrderSnapshot, ...]
    fills: tuple[BrokerFillSnapshot, ...]
    positions: tuple[BrokerPositionSnapshot, ...]
    account: BrokerAccountSnapshot


@dataclass(frozen=True)
class ReconciliationFinding:
    event_type: str
    severity: str
    blocks_execution: bool
    message: str
    details: dict[str, Any]
    paper_order_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "severity": self.severity,
            "blocks_execution": self.blocks_execution,
            "message": self.message,
            "paper_order_id": self.paper_order_id,
            "details": self.details,
        }


@dataclass(frozen=True)
class ReconciliationReport:
    run_id: str
    strategy_id: str
    session_date: str
    checked_at: str
    finding_count: int
    blocking_count: int
    recovered_order_count: int
    blocks_execution: bool
    findings: tuple[ReconciliationFinding, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "strategy_id": self.strategy_id,
            "session_date": self.session_date,
            "checked_at": self.checked_at,
            "finding_count": self.finding_count,
            "blocking_count": self.blocking_count,
            "recovered_order_count": self.recovered_order_count,
            "blocks_execution": self.blocks_execution,
            "findings": [finding.to_dict() for finding in self.findings],
        }


def load_broker_state(
    *,
    settings: Settings | None = None,
    broker_client: AlpacaClient | None = None,
) -> BrokerStateSnapshot:
    resolved_settings = settings or load_settings()
    owns_broker_client = broker_client is None
    client = broker_client or AlpacaClient(resolved_settings.broker.alpaca)

    try:
        return BrokerStateSnapshot(
            orders=tuple(client.list_orders()),
            fills=tuple(client.list_fills()),
            positions=tuple(client.list_positions()),
            account=client.get_account(),
        )
    finally:
        if owns_broker_client:
            client.close()


def recover_inflight_paper_orders(
    strategy_id: str,
    *,
    settings: Settings | None = None,
    registry: StrategyRegistry | None = None,
    broker_state: BrokerStateSnapshot,
) -> int:
    resolved_settings = settings or load_settings()
    resolved_registry = registry or build_default_registry(resolved_settings)
    strategy = resolved_registry.resolve(strategy_id)
    recovered = 0
    synced_at = datetime.now(UTC)

    broker_by_broker_id = {
        order.broker_order_id: order for order in broker_state.orders if order.broker_order_id
    }
    broker_by_client_id = {
        order.client_order_id: order for order in broker_state.orders if order.client_order_id
    }

    with session_scope(resolved_settings) as session:
        strategy_record = ensure_strategy_record(session, strategy.metadata)
        local_orders = session.execute(
            select(PaperOrder)
            .join(StrategyRun, StrategyRun.id == PaperOrder.strategy_run_id)
            .where(
                StrategyRun.strategy_id == strategy_record.id,
                PaperOrder.status.in_(tuple(_ACTIVE_LOCAL_ORDER_STATUSES | {OrderLifecycleState.SUBMISSION_FAILED})),
            )
            .order_by(PaperOrder.created_at.asc())
        ).scalars().all()

        for local_order in local_orders:
            broker_order = None
            if local_order.broker_order_id:
                broker_order = broker_by_broker_id.get(local_order.broker_order_id)
            if broker_order is None:
                broker_order = broker_by_client_id.get(local_order.client_order_id)
            if broker_order is None:
                continue

            if _apply_broker_order_snapshot(session, local_order, broker_order, synced_at=synced_at):
                local_order.last_submission_error = None
                recovered += 1

    return recovered


def reconcile_paper_execution(
    strategy_id: str | None = None,
    *,
    as_of_session: date,
    settings: Settings | None = None,
    registry: StrategyRegistry | None = None,
    broker_client: AlpacaClient | None = None,
    broker_state: BrokerStateSnapshot | None = None,
    recovered_order_count: int = 0,
    trigger_source: str = "paper_reconciliation",
) -> ReconciliationReport:
    logger = logging.getLogger("trading_platform.reconciliation")
    resolved_settings = settings or load_settings()
    resolved_registry = registry or build_default_registry(resolved_settings)
    resolved_strategy_id = strategy_id or resolved_settings.execution.paper_session_runner.default_strategy_id
    strategy = resolved_registry.resolve(resolved_strategy_id)
    checked_at = datetime.now(UTC)
    safety_settings = resolved_settings.execution.safety
    effective_broker_state = broker_state or load_broker_state(
        settings=resolved_settings,
        broker_client=broker_client,
    )
    run_id = _create_reconciliation_run(
        resolved_settings,
        strategy.metadata,
        as_of_session=as_of_session,
        trigger_source=trigger_source,
    )

    try:
        with session_scope(resolved_settings) as session:
            strategy_record = ensure_strategy_record(session, strategy.metadata)
            local_orders = session.execute(
                select(PaperOrder)
                .join(StrategyRun, StrategyRun.id == PaperOrder.strategy_run_id)
                .where(StrategyRun.strategy_id == strategy_record.id)
                .order_by(PaperOrder.created_at.asc())
            ).scalars().all()
            local_fills = session.execute(
                select(PaperFill)
                .join(PaperOrder, PaperOrder.id == PaperFill.paper_order_id)
                .join(StrategyRun, StrategyRun.id == PaperOrder.strategy_run_id)
                .where(StrategyRun.strategy_id == strategy_record.id)
                .order_by(PaperFill.filled_at.asc())
            ).scalars().all()
            local_positions = session.execute(
                select(Position)
                .where(
                    Position.strategy_id == strategy_record.id,
                    Position.status == "open",
                )
                .order_by(Position.created_at.asc())
            ).scalars().all()
            latest_snapshot = session.execute(
                select(AccountSnapshot)
                .where(AccountSnapshot.strategy_id == strategy_record.id)
                .order_by(AccountSnapshot.snapshot_at.desc())
            ).scalars().first()

            findings = _build_findings(
                local_orders=local_orders,
                local_fills=local_fills,
                local_positions=local_positions,
                latest_snapshot=latest_snapshot,
                broker_state=effective_broker_state,
                failure_threshold=safety_settings.repeated_failure_threshold,
            )
            _apply_sync_failure_state(
                local_orders=local_orders,
                findings=findings,
                checked_at=checked_at,
            )

            persisted_findings = tuple(findings) if findings else (
                ReconciliationFinding(
                    event_type="reconciliation_clean",
                    severity="info",
                    blocks_execution=False,
                    message="Broker and local paper-execution state are aligned.",
                    details={
                        "order_count": len(effective_broker_state.orders),
                        "fill_count": len(effective_broker_state.fills),
                        "position_count": len(effective_broker_state.positions),
                    },
                ),
            )
            session.add_all(
                [
                    ExecutionEvent(
                        strategy_run_id=run_id,
                        paper_order_id=uuid.UUID(finding.paper_order_id) if finding.paper_order_id else None,
                        event_type=finding.event_type,
                        severity=finding.severity,
                        blocks_execution=finding.blocks_execution,
                        event_at=checked_at,
                        message=finding.message,
                        details=finding.details,
                    )
                    for finding in persisted_findings
                ]
            )

        blocking_count = sum(1 for finding in persisted_findings if finding.blocks_execution)
        report = _update_reconciliation_run(
            resolved_settings,
            run_id,
            status=StrategyRunStatus.SUCCEEDED,
            completed_at=checked_at,
            result_summary={
                "stage": "completed",
                "strategy_id": resolved_strategy_id,
                "as_of_session": as_of_session.isoformat(),
                "recovered_order_count": recovered_order_count,
                "finding_count": len(persisted_findings),
                "blocking_count": blocking_count,
                "blocks_execution": blocking_count > 0,
                "findings": [finding.to_dict() for finding in persisted_findings],
            },
        )
    except Exception as exc:
        _update_reconciliation_run(
            resolved_settings,
            run_id,
            status=StrategyRunStatus.FAILED,
            completed_at=checked_at,
            error_message=str(exc),
            result_summary={
                "stage": "failed",
                "strategy_id": resolved_strategy_id,
                "as_of_session": as_of_session.isoformat(),
            },
        )
        logger.exception(
            "paper_reconciliation_failed",
            extra={
                "context": build_log_context(
                    strategy_id=resolved_strategy_id,
                    run_id=str(run_id),
                    session_date=as_of_session.isoformat(),
                    trigger_source=trigger_source,
                )
            },
        )
        raise

    reconciliation_report = ReconciliationReport(
        run_id=report.run_id,
        strategy_id=report.strategy_id,
        session_date=as_of_session.isoformat(),
        checked_at=checked_at.isoformat(),
        finding_count=report.result_summary["finding_count"],
        blocking_count=report.result_summary["blocking_count"],
        recovered_order_count=recovered_order_count,
        blocks_execution=report.result_summary["blocks_execution"],
        findings=tuple(
            ReconciliationFinding(
                event_type=finding["event_type"],
                severity=finding["severity"],
                blocks_execution=finding["blocks_execution"],
                message=finding["message"],
                details=finding["details"],
                paper_order_id=finding.get("paper_order_id"),
            )
            for finding in report.result_summary["findings"]
        ),
    )
    emit_structured_log(
        logger,
        logging.INFO,
        "paper_reconciliation_completed",
        strategy_id=resolved_strategy_id,
        run_id=reconciliation_report.run_id,
        session_date=as_of_session.isoformat(),
        trigger_source=trigger_source,
        blocking_count=reconciliation_report.blocking_count,
        recovered_order_count=reconciliation_report.recovered_order_count,
    )
    return reconciliation_report


def _build_findings(
    *,
    local_orders: list[PaperOrder],
    local_fills: list[PaperFill],
    local_positions: list[Position],
    latest_snapshot: AccountSnapshot | None,
    broker_state: BrokerStateSnapshot,
    failure_threshold: int,
) -> list[ReconciliationFinding]:
    findings: list[ReconciliationFinding] = []
    local_orders_by_broker_id = {
        order.broker_order_id: order for order in local_orders if order.broker_order_id
    }
    local_orders_by_client_id = {
        order.client_order_id: order for order in local_orders if order.client_order_id
    }
    matched_order_ids: set[uuid.UUID] = set()

    for broker_order in broker_state.orders:
        local_order = local_orders_by_broker_id.get(broker_order.broker_order_id)
        if local_order is None:
            local_order = local_orders_by_client_id.get(broker_order.client_order_id)
        if local_order is None:
            findings.append(
                ReconciliationFinding(
                    event_type="order_missing_locally",
                    severity="error",
                    blocks_execution=True,
                    message=(
                        f"Broker order '{broker_order.broker_order_id}' for {broker_order.symbol} "
                        "has no persisted local paper_order record."
                    ),
                    details={
                        "broker_order_id": broker_order.broker_order_id,
                        "client_order_id": broker_order.client_order_id,
                        "symbol": broker_order.symbol,
                        "broker_status": broker_order.broker_status,
                    },
                )
            )
            continue

        matched_order_ids.add(local_order.id)
        expected_local_status = _local_state_from_broker_status(broker_order.status)
        if local_order.status != expected_local_status or local_order.broker_status != broker_order.broker_status:
            findings.append(
                ReconciliationFinding(
                    event_type="order_status_mismatch",
                    severity="error",
                    blocks_execution=True,
                    message=(
                        f"Local order '{local_order.client_order_id}' has status '{local_order.status}' "
                        f"but broker reports '{expected_local_status}'."
                    ),
                    details={
                        "paper_order_id": str(local_order.id),
                        "client_order_id": local_order.client_order_id,
                        "broker_order_id": broker_order.broker_order_id,
                        "local_status": local_order.status,
                        "local_broker_status": local_order.broker_status,
                        "broker_status": broker_order.broker_status,
                    },
                    paper_order_id=str(local_order.id),
                )
            )

    for local_order in local_orders:
        if local_order.id in matched_order_ids:
            continue
        if local_order.status == OrderLifecycleState.SUBMISSION_FAILED:
            continue
        if (
            local_order.status == OrderLifecycleState.PENDING_SUBMISSION
            and local_order.submission_attempt_count == 0
        ):
            continue
        if local_order.status not in _ACTIVE_LOCAL_ORDER_STATUSES:
            continue

        findings.append(
            ReconciliationFinding(
                event_type="order_missing_from_broker",
                severity="error",
                blocks_execution=True,
                message=(
                    f"Local order '{local_order.client_order_id}' is still '{local_order.status}' "
                    "but the broker no longer reports it."
                ),
                details={
                    "paper_order_id": str(local_order.id),
                    "client_order_id": local_order.client_order_id,
                    "broker_order_id": local_order.broker_order_id,
                    "local_status": local_order.status,
                    "submission_attempt_count": local_order.submission_attempt_count,
                },
                paper_order_id=str(local_order.id),
            )
        )

    local_fill_ids = {paper_fill.broker_fill_id for paper_fill in local_fills}
    for broker_fill in broker_state.fills:
        if broker_fill.broker_fill_id in local_fill_ids:
            continue
        paper_order = local_orders_by_broker_id.get(broker_fill.broker_order_id)
        findings.append(
            ReconciliationFinding(
                event_type="fill_missing_locally",
                severity="error",
                blocks_execution=True,
                message=(
                    f"Broker fill '{broker_fill.broker_fill_id}' for order '{broker_fill.broker_order_id}' "
                    "has not been persisted locally."
                ),
                details={
                    "broker_fill_id": broker_fill.broker_fill_id,
                    "broker_order_id": broker_fill.broker_order_id,
                    "symbol": broker_fill.symbol,
                    "quantity": str(broker_fill.quantity),
                    "price": str(broker_fill.price),
                },
                paper_order_id=str(paper_order.id) if paper_order is not None else None,
            )
        )

    local_positions_by_symbol = {position.symbol_ref.ticker: position for position in local_positions}
    broker_positions_by_symbol = {position.symbol: position for position in broker_state.positions}
    for symbol in sorted(set(local_positions_by_symbol) | set(broker_positions_by_symbol)):
        local_position = local_positions_by_symbol.get(symbol)
        broker_position = broker_positions_by_symbol.get(symbol)
        if local_position is None and broker_position is not None:
            findings.append(
                ReconciliationFinding(
                    event_type="position_missing_locally",
                    severity="error",
                    blocks_execution=True,
                    message=f"Broker reports an open {symbol} position that local storage does not track.",
                    details={
                        "symbol": symbol,
                        "broker_quantity": str(broker_position.quantity),
                        "broker_cost_basis": str(broker_position.cost_basis),
                    },
                )
            )
            continue
        if local_position is not None and broker_position is None:
            findings.append(
                ReconciliationFinding(
                    event_type="position_missing_from_broker",
                    severity="error",
                    blocks_execution=True,
                    message=f"Local storage reports an open {symbol} position that the broker does not show.",
                    details={
                        "symbol": symbol,
                        "local_quantity": str(local_position.quantity),
                        "local_cost_basis": str(local_position.cost_basis),
                    },
                )
            )
            continue
        if local_position is None or broker_position is None:
            continue
        if (
            _decimal_differs(local_position.quantity, broker_position.quantity, tolerance=_QUANTITY_TOLERANCE)
            or _decimal_differs(
                local_position.average_entry_price,
                broker_position.average_entry_price,
                tolerance=_MONEY_TOLERANCE,
            )
        ):
            findings.append(
                ReconciliationFinding(
                    event_type="position_mismatch",
                    severity="error",
                    blocks_execution=True,
                    message=f"Local {symbol} position sizing diverges from the broker position.",
                    details={
                        "symbol": symbol,
                        "local_quantity": str(local_position.quantity),
                        "broker_quantity": str(broker_position.quantity),
                        "local_average_entry_price": str(local_position.average_entry_price),
                        "broker_average_entry_price": str(broker_position.average_entry_price),
                    },
                )
            )

    if latest_snapshot is None:
        if broker_state.positions or local_positions:
            findings.append(
                ReconciliationFinding(
                    event_type="account_snapshot_missing_locally",
                    severity="error",
                    blocks_execution=True,
                    message="Broker positions exist but local account state has never been synced.",
                    details={"position_count": len(broker_state.positions)},
                )
            )
        else:
            findings.append(
                ReconciliationFinding(
                    event_type="account_snapshot_not_yet_synced",
                    severity="info",
                    blocks_execution=False,
                    message="No local broker account snapshot exists yet; reconciliation treated the flat account as safe.",
                    details={},
                )
            )
    else:
        broker_gross_exposure = sum(
            (abs(position.market_value) for position in broker_state.positions),
            start=Decimal("0"),
        )
        account_differences: dict[str, dict[str, str | int]] = {}
        if _decimal_differs(latest_snapshot.cash, broker_state.account.cash, tolerance=_MONEY_TOLERANCE):
            account_differences["cash"] = {
                "local": str(latest_snapshot.cash),
                "broker": str(broker_state.account.cash),
            }
        if _decimal_differs(
            latest_snapshot.buying_power,
            broker_state.account.buying_power,
            tolerance=_MONEY_TOLERANCE,
        ):
            account_differences["buying_power"] = {
                "local": str(latest_snapshot.buying_power),
                "broker": str(broker_state.account.buying_power),
            }
        if _decimal_differs(latest_snapshot.total_equity, broker_state.account.equity, tolerance=_MONEY_TOLERANCE):
            account_differences["total_equity"] = {
                "local": str(latest_snapshot.total_equity),
                "broker": str(broker_state.account.equity),
            }
        if _decimal_differs(latest_snapshot.gross_exposure, broker_gross_exposure, tolerance=_MONEY_TOLERANCE):
            account_differences["gross_exposure"] = {
                "local": str(latest_snapshot.gross_exposure),
                "broker": str(broker_gross_exposure),
            }
        if latest_snapshot.open_positions != len(broker_state.positions):
            account_differences["open_positions"] = {
                "local": latest_snapshot.open_positions,
                "broker": len(broker_state.positions),
            }
        if account_differences:
            findings.append(
                ReconciliationFinding(
                    event_type="account_snapshot_mismatch",
                    severity="error",
                    blocks_execution=True,
                    message="Latest local account snapshot diverges from the broker account state.",
                    details=account_differences,
                )
            )

    order_error_messages = _order_error_messages(findings)
    for local_order in local_orders:
        if (
            local_order.status == OrderLifecycleState.SUBMISSION_FAILED
            and local_order.submission_attempt_count >= failure_threshold
        ):
            findings.append(
                ReconciliationFinding(
                    event_type="submission_failure_threshold_exceeded",
                    severity="error",
                    blocks_execution=True,
                    message=(
                        f"Local order '{local_order.client_order_id}' hit the submission failure threshold "
                        f"({local_order.submission_attempt_count} attempts)."
                    ),
                    details={
                        "paper_order_id": str(local_order.id),
                        "client_order_id": local_order.client_order_id,
                        "submission_attempt_count": local_order.submission_attempt_count,
                        "last_submission_error": local_order.last_submission_error,
                    },
                    paper_order_id=str(local_order.id),
                )
            )
        if local_order.id in order_error_messages and local_order.sync_failure_count + 1 >= failure_threshold:
            findings.append(
                ReconciliationFinding(
                    event_type="sync_failure_threshold_exceeded",
                    severity="error",
                    blocks_execution=True,
                    message=(
                        f"Local order '{local_order.client_order_id}' hit the broker-sync failure threshold "
                        f"({local_order.sync_failure_count + 1} failed reconciliations)."
                    ),
                    details={
                        "paper_order_id": str(local_order.id),
                        "client_order_id": local_order.client_order_id,
                        "sync_failure_count": local_order.sync_failure_count + 1,
                        "last_sync_error": order_error_messages[local_order.id],
                    },
                    paper_order_id=str(local_order.id),
                )
            )

    return findings


def _order_error_messages(findings: list[ReconciliationFinding]) -> dict[uuid.UUID, str]:
    messages: dict[uuid.UUID, str] = {}
    for finding in findings:
        if finding.paper_order_id is None:
            continue
        paper_order_id = uuid.UUID(finding.paper_order_id)
        messages.setdefault(paper_order_id, finding.message)
    return messages


def _apply_sync_failure_state(
    *,
    local_orders: list[PaperOrder],
    findings: list[ReconciliationFinding],
    checked_at: datetime,
) -> None:
    order_error_messages = _order_error_messages(findings)
    for local_order in local_orders:
        error_message = order_error_messages.get(local_order.id)
        if error_message is None:
            local_order.sync_failure_count = 0
            local_order.last_sync_error = None
            local_order.last_sync_failure_at = None
            continue

        local_order.sync_failure_count += 1
        local_order.last_sync_error = error_message
        local_order.last_sync_failure_at = checked_at


def _apply_broker_order_snapshot(
    session,
    local_order: PaperOrder,
    broker_order: BrokerOrderSnapshot,
    *,
    synced_at: datetime,
) -> bool:
    before = (
        local_order.broker_order_id,
        local_order.status,
        local_order.broker_status,
        local_order.filled_at,
        local_order.canceled_at,
        local_order.last_broker_update_at,
    )

    if not local_order.broker_order_id:
        local_order.broker_order_id = broker_order.broker_order_id
    transition_event = _broker_transition_event(broker_order.status)
    transition_target = resolve_transition_target(
        from_state=local_order.status,
        event_type=transition_event,
    )
    if transition_target is not None and transition_target != local_order.status:
        apply_order_transition(
            local_order.id,
            OrderTransitionRequest(
                strategy_run_id=local_order.strategy_run_id,
                event_type=transition_event,
                details={
                    "broker_order_id": broker_order.broker_order_id,
                    "broker_status": broker_order.broker_status,
                },
                event_at=broker_order.updated_at,
            ),
            session=session,
        )
    local_order.broker_status = broker_order.broker_status
    local_order.submitted_at = broker_order.submitted_at or local_order.submitted_at
    local_order.filled_at = broker_order.filled_at
    local_order.canceled_at = broker_order.canceled_at
    local_order.last_broker_update_at = broker_order.updated_at
    local_order.last_synced_at = synced_at
    local_order.broker_payload = broker_order.raw_payload

    after = (
        local_order.broker_order_id,
        local_order.status,
        local_order.broker_status,
        local_order.filled_at,
        local_order.canceled_at,
        local_order.last_broker_update_at,
    )
    return before != after


def _broker_transition_event(status: ExecutionOrderStatus) -> OrderTransitionEventType:
    if status in {ExecutionOrderStatus.PENDING, ExecutionOrderStatus.ACCEPTED}:
        return OrderTransitionEventType.BROKER_ACKNOWLEDGED
    if status == ExecutionOrderStatus.PARTIALLY_FILLED:
        return OrderTransitionEventType.BROKER_PARTIALLY_FILLED
    if status == ExecutionOrderStatus.FILLED:
        return OrderTransitionEventType.BROKER_FILLED
    if status == ExecutionOrderStatus.CANCELED:
        return OrderTransitionEventType.BROKER_CANCELED
    if status == ExecutionOrderStatus.REJECTED:
        return OrderTransitionEventType.BROKER_REJECTED
    if status == ExecutionOrderStatus.EXPIRED:
        return OrderTransitionEventType.BROKER_EXPIRED
    return OrderTransitionEventType.BROKER_STATUS_UNKNOWN


def _local_state_from_broker_status(status: ExecutionOrderStatus) -> OrderLifecycleState:
    event_type = _broker_transition_event(status)
    mapping = {
        OrderTransitionEventType.BROKER_ACKNOWLEDGED: OrderLifecycleState.SUBMITTED,
        OrderTransitionEventType.BROKER_PARTIALLY_FILLED: OrderLifecycleState.PARTIALLY_FILLED,
        OrderTransitionEventType.BROKER_FILLED: OrderLifecycleState.FILLED,
        OrderTransitionEventType.BROKER_CANCELED: OrderLifecycleState.CANCELED,
        OrderTransitionEventType.BROKER_REJECTED: OrderLifecycleState.REJECTED,
        OrderTransitionEventType.BROKER_EXPIRED: OrderLifecycleState.EXPIRED,
        OrderTransitionEventType.BROKER_STATUS_UNKNOWN: OrderLifecycleState.UNKNOWN,
    }
    return mapping[event_type]


def _create_reconciliation_run(
    settings: Settings,
    metadata,
    *,
    as_of_session: date,
    trigger_source: str,
) -> uuid.UUID:
    with session_scope(settings) as session:
        strategy_record = ensure_strategy_record(session, metadata)
        strategy_run = StrategyRun(
            strategy_id=strategy_record.id,
            run_type=StrategyRunType.RECONCILIATION,
            status=StrategyRunStatus.PENDING,
            trigger_source=trigger_source,
            parameters_snapshot={
                "strategy": metadata.to_public_dict(),
                "as_of_session": as_of_session.isoformat(),
                "safety_policy": settings.execution.safety.model_dump(mode="json"),
            },
            result_summary={
                "stage": "pending",
                "strategy_id": metadata.strategy_id,
                "as_of_session": as_of_session.isoformat(),
            },
        )
        session.add(strategy_run)
        session.flush()
        return strategy_run.id


def _update_reconciliation_run(
    settings: Settings,
    run_id: uuid.UUID,
    *,
    status: StrategyRunStatus,
    result_summary: dict[str, Any] | None = None,
    error_message: str | None = None,
    completed_at: datetime | None = None,
):
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
        return type(
            "ReconciliationRunState",
            (),
            {
                "run_id": str(strategy_run.id),
                "strategy_id": strategy.strategy_id if strategy is not None else "unknown",
                "result_summary": strategy_run.result_summary,
            },
        )()


def _decimal_differs(left: Decimal, right: Decimal, *, tolerance: Decimal) -> bool:
    return abs(left - right) > tolerance
