"""Broker-to-local reconciliation and unsafe-state persistence."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from trading_platform.core.logging import build_log_context, emit_structured_log, get_logger
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
from trading_platform.services.config.tolerances import MONEY_TOLERANCE
from trading_platform.services.execution import ExecutionOrderStatus, OrderSide
from trading_platform.services.execution.transition import (
    OrderTransitionRequest,
    apply_order_transition,
    resolve_transition_target,
)
from trading_platform.services.reconciliation_matcher import match_snapshots
from trading_platform.services.reconciliation_types import (
    Finding,
    LocalAccountSnapshot,
    LocalFillSnapshot,
    LocalOrderSnapshot,
    LocalPositionSnapshot,
)
from trading_platform.strategies.registry import StrategyRegistry, build_default_registry

_ACTIVE_LOCAL_ORDER_STATUSES = {
    OrderLifecycleState.PENDING_SUBMISSION,
    OrderLifecycleState.SUBMITTED,
    OrderLifecycleState.PARTIALLY_FILLED,
}


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
            if local_order.client_order_id:
                broker_order = broker_by_client_id.get(local_order.client_order_id)
            if broker_order is None and local_order.broker_order_id:
                broker_order = broker_by_broker_id.get(local_order.broker_order_id)
            if broker_order is None:
                continue

            if _apply_broker_order_snapshot(session, local_order, broker_order, synced_at=synced_at):
                local_order.last_submission_error = None
                recovered += 1

    return recovered


def apply_reconciliation_corrections(
    strategy_id: str,
    *,
    report: ReconciliationReport | None = None,
    findings: tuple[ReconciliationFinding, ...] | None = None,
    settings: Settings | None = None,
    registry: StrategyRegistry | None = None,
    checked_at: datetime | None = None,
) -> int:
    """Explicit, separately-invoked corrective entrypoint (RECON-04).

    This is the ONLY path that mutates ``PaperOrder.sync_failure_count`` /
    ``last_sync_error`` / ``last_sync_failure_at``. ``reconcile_paper_execution`` is a
    strictly read-only orchestrator and never calls this function; callers (e.g. the
    paper-session runner) must invoke reconcile first to obtain a report, then call this
    function as its own distinct, explicitly-invoked step.

    Accepts either a full ``report`` (its ``.findings`` are used) or a bare ``findings``
    tuple directly -- exactly one should be supplied; if both are omitted, an empty
    findings set is treated as "no errors this run" (every order's sync-failure state is
    reset to zero, mirroring a clean run).

    Mirrors the pre-09-03 ``_apply_sync_failure_state`` write behavior exactly: for every
    local order, if a finding's message names that order, increment
    ``sync_failure_count`` and stamp ``last_sync_error``/``last_sync_failure_at``;
    otherwise reset all three fields to their zero/None state. This function owns ONLY
    the WRITE/increment -- the repeated-failure THRESHOLD *evaluation* that feeds
    ``blocks_execution`` stays read-only in ``reconcile_paper_execution``
    (``_evaluate_threshold_breach``, decision D2) and is not reintroduced here.

    Returns the number of ``PaperOrder`` rows whose sync-failure state changed.
    """
    resolved_settings = settings or load_settings()
    resolved_registry = registry or build_default_registry(resolved_settings)
    strategy = resolved_registry.resolve(strategy_id)
    resolved_checked_at = checked_at or datetime.now(UTC)
    resolved_findings: tuple[ReconciliationFinding, ...] = (
        findings if findings is not None else (report.findings if report is not None else ())
    )

    order_error_messages: dict[uuid.UUID, str] = {}
    for finding in resolved_findings:
        if finding.paper_order_id is None:
            continue
        order_error_messages.setdefault(uuid.UUID(finding.paper_order_id), finding.message)

    mutated_count = 0
    with session_scope(resolved_settings) as session:
        strategy_record = ensure_strategy_record(session, strategy.metadata)
        local_orders = session.execute(
            select(PaperOrder)
            .join(StrategyRun, StrategyRun.id == PaperOrder.strategy_run_id)
            .where(StrategyRun.strategy_id == strategy_record.id)
            .order_by(PaperOrder.created_at.asc())
        ).scalars().all()

        for local_order in local_orders:
            error_message = order_error_messages.get(local_order.id)
            if error_message is None:
                if (
                    local_order.sync_failure_count != 0
                    or local_order.last_sync_error is not None
                    or local_order.last_sync_failure_at is not None
                ):
                    mutated_count += 1
                local_order.sync_failure_count = 0
                local_order.last_sync_error = None
                local_order.last_sync_failure_at = None
                continue

            local_order.sync_failure_count += 1
            local_order.last_sync_error = error_message
            local_order.last_sync_failure_at = resolved_checked_at
            mutated_count += 1

    return mutated_count


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
    logger = get_logger("trading_platform.reconciliation")
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

            # READ-ONLY projection boundary (RECON-03/05): ORM rows are projected into
            # the typed 09-01 snapshots here; no ORM instance crosses this boundary into
            # the pure matcher or the account/threshold evaluations below.
            local_order_snapshots = [_project_local_order(order) for order in local_orders]
            local_fill_snapshots = [_project_local_fill(fill) for fill in local_fills]
            local_position_snapshots = [_project_local_position(position) for position in local_positions]
            local_account_snapshot = (
                _project_local_account(latest_snapshot) if latest_snapshot is not None else None
            )

            findings = match_snapshots(
                local_orders=local_order_snapshots,
                local_fills=local_fill_snapshots,
                local_positions=local_position_snapshots,
                broker_orders=list(effective_broker_state.orders),
                broker_fills=list(effective_broker_state.fills),
                broker_positions=list(effective_broker_state.positions),
            )
            # increment moved to corrective path (09-04): reconcile no longer writes
            # PaperOrder.sync_failure_count / last_sync_error / last_sync_failure_at.

            account_divergence = _evaluate_account_divergence(
                latest_snapshot=local_account_snapshot,
                broker_account=effective_broker_state.account,
                broker_positions=effective_broker_state.positions,
                local_positions_present=bool(local_positions),
            )
            threshold_breach = _evaluate_threshold_breach(
                local_orders=local_orders,
                findings=findings,
                failure_threshold=safety_settings.repeated_failure_threshold,
            )

            session.add_all(
                [
                    ExecutionEvent(
                        strategy_run_id=run_id,
                        paper_order_id=(
                            uuid.UUID(event_dict["paper_order_id"]) if event_dict["paper_order_id"] else None
                        ),
                        event_type=event_dict["event_type"],
                        severity=event_dict["severity"],
                        blocks_execution=event_dict["blocks_execution"],
                        event_at=checked_at,
                        message=event_dict["message"],
                        details=event_dict["details"],
                    )
                    for event_dict in (_finding_event_dict(finding) for finding in findings)
                ]
            )

        blocking_count = sum(1 for finding in findings if finding.blocks_execution)
        blocks_execution = bool(findings) or bool(account_divergence) or bool(threshold_breach)
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
                "finding_count": len(findings),
                "blocking_count": blocking_count,
                "blocks_execution": blocks_execution,
                "account_divergence": account_divergence,
                "threshold_breach": threshold_breach,
                "findings": [_finding_event_dict(finding) for finding in findings],
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


def _project_local_order(order: PaperOrder) -> LocalOrderSnapshot:
    """Project a ``PaperOrder`` ORM row into the typed 09-01 snapshot (read-only)."""
    return LocalOrderSnapshot(
        paper_order_id=str(order.id),
        strategy_run_id=str(order.strategy_run_id),
        symbol=order.symbol_ref.ticker,
        side=OrderSide(order.side),
        quantity=order.quantity,
        client_order_id=order.client_order_id,
        broker_order_id=order.broker_order_id,
        status=order.status.value,
        broker_status=order.broker_status,
        submission_attempt_count=order.submission_attempt_count,
        sync_failure_count=order.sync_failure_count,
    )


def _project_local_fill(fill: PaperFill) -> LocalFillSnapshot:
    """Project a ``PaperFill`` ORM row into the typed 09-01 snapshot (read-only)."""
    return LocalFillSnapshot(
        broker_fill_id=fill.broker_fill_id,
        broker_order_id=fill.broker_order_id,
        symbol=fill.symbol_ref.ticker,
        side=OrderSide(fill.side),
        quantity=fill.quantity,
        price=fill.price,
        filled_at=fill.filled_at,
    )


def _project_local_position(position: Position) -> LocalPositionSnapshot:
    """Project a ``Position`` ORM row into the typed 09-01 snapshot (read-only)."""
    return LocalPositionSnapshot(
        symbol=position.symbol_ref.ticker,
        quantity=position.quantity,
        average_entry_price=position.average_entry_price,
        cost_basis=position.cost_basis,
        status=position.status,
    )


def _project_local_account(snapshot: AccountSnapshot) -> LocalAccountSnapshot:
    """Project the latest ``AccountSnapshot`` ORM row into the typed 09-01 snapshot."""
    return LocalAccountSnapshot(
        cash=snapshot.cash,
        gross_exposure=snapshot.gross_exposure,
        total_equity=snapshot.total_equity,
        buying_power=snapshot.buying_power,
        open_positions=snapshot.open_positions,
    )


def _evaluate_account_divergence(
    *,
    latest_snapshot: LocalAccountSnapshot | None,
    broker_account: BrokerAccountSnapshot,
    broker_positions: tuple[BrokerPositionSnapshot, ...],
    local_positions_present: bool,
) -> dict[str, Any]:
    """Read-only account-divergence evaluation (decision D1). Preserves all three
    pre-rewrite account branches exactly, with NO row writes:

    - (B1) no AccountSnapshot has ever been persisted AND positions exist (broker or
      local) -> truthy ``account_snapshot_missing_locally`` sub-flag (BLOCKS).
    - (B2) no AccountSnapshot has ever been persisted AND the book is flat -> empty
      dict (NON-blocking).
    - (B3) an AccountSnapshot exists AND cash/buying_power/equity/gross_exposure/
      open_positions deltas exceed tolerance -> populated deltas (BLOCKS).
    """
    if latest_snapshot is None:
        if broker_positions or local_positions_present:
            return {
                "account_snapshot_missing_locally": True,
                "broker_position_count": len(broker_positions),
            }
        return {}

    broker_gross_exposure = sum(
        (abs(position.market_value) for position in broker_positions),
        start=Decimal("0"),
    )
    divergence: dict[str, dict[str, str | int]] = {}
    if _decimal_differs(latest_snapshot.cash, broker_account.cash, tolerance=MONEY_TOLERANCE):
        divergence["cash"] = {"local": str(latest_snapshot.cash), "broker": str(broker_account.cash)}
    if _decimal_differs(latest_snapshot.buying_power, broker_account.buying_power, tolerance=MONEY_TOLERANCE):
        divergence["buying_power"] = {
            "local": str(latest_snapshot.buying_power),
            "broker": str(broker_account.buying_power),
        }
    if _decimal_differs(latest_snapshot.total_equity, broker_account.equity, tolerance=MONEY_TOLERANCE):
        divergence["total_equity"] = {
            "local": str(latest_snapshot.total_equity),
            "broker": str(broker_account.equity),
        }
    if _decimal_differs(latest_snapshot.gross_exposure, broker_gross_exposure, tolerance=MONEY_TOLERANCE):
        divergence["gross_exposure"] = {
            "local": str(latest_snapshot.gross_exposure),
            "broker": str(broker_gross_exposure),
        }
    if latest_snapshot.open_positions != len(broker_positions):
        divergence["open_positions"] = {
            "local": latest_snapshot.open_positions,
            "broker": len(broker_positions),
        }
    return divergence


def _evaluate_threshold_breach(
    *,
    local_orders: list[PaperOrder],
    findings: tuple[Finding, ...],
    failure_threshold: int,
) -> list[dict[str, Any]]:
    """Read-only repeated-failure-threshold evaluation (decision D2, READ half only).

    Evaluates the SAME two predicates the pre-rewrite code used
    (submission_attempt_count >= failure_threshold for SUBMISSION_FAILED orders;
    sync_failure_count + 1 >= failure_threshold for orders with a sync error this run,
    derived from the matcher's findings) as a pure read. Performs NO increment and NO
    row write — the WRITE half moves to the corrective path in 09-04.
    """
    finding_messages_by_order_id: dict[str, str] = {}
    for finding in findings:
        if finding.paper_order_id is not None:
            finding_messages_by_order_id.setdefault(finding.paper_order_id, finding.message)

    breaches: list[dict[str, Any]] = []
    for local_order in local_orders:
        if (
            local_order.status == OrderLifecycleState.SUBMISSION_FAILED
            and local_order.submission_attempt_count >= failure_threshold
        ):
            breaches.append(
                {
                    "reason": "submission_failure_threshold_exceeded",
                    "paper_order_id": str(local_order.id),
                    "client_order_id": local_order.client_order_id,
                    "submission_attempt_count": local_order.submission_attempt_count,
                }
            )

        error_message = finding_messages_by_order_id.get(str(local_order.id))
        if error_message is not None and local_order.sync_failure_count + 1 >= failure_threshold:
            breaches.append(
                {
                    "reason": "sync_failure_threshold_exceeded",
                    "paper_order_id": str(local_order.id),
                    "client_order_id": local_order.client_order_id,
                    "sync_failure_count": local_order.sync_failure_count + 1,
                    "last_sync_error": error_message,
                }
            )

    return breaches


def _finding_event_dict(finding: Finding) -> dict[str, Any]:
    """Serialize a matcher ``Finding`` into the ExecutionEvent persistence shape,
    tying it back to its source snapshot (RECON-09): the identity (symbol, account,
    side) and source ids (paper_order_id / broker_order_id) are folded into
    ``details`` alongside whatever the matcher's finding builder already populated.
    """
    event_dict = finding.to_event_dict()
    details = dict(event_dict["details"])
    if finding.identity is not None:
        details.setdefault("symbol", finding.identity.symbol)
        details.setdefault("account", finding.identity.account)
        details.setdefault("side", finding.identity.side.value)
    if finding.paper_order_id is not None:
        details.setdefault("paper_order_id", finding.paper_order_id)
    if finding.broker_order_id is not None:
        details.setdefault("broker_order_id", finding.broker_order_id)
    event_dict["details"] = details
    return event_dict


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
