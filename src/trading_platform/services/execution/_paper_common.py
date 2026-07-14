"""Shared dataclasses and cross-cutting helpers for the paper-execution split.

Package-internal (leading underscore): this module is the deliberate,
documented home for the dataclasses and helpers used by BOTH
``submit_orders.py`` and ``sync_orders.py`` (STRUCT-04 part 2, 12-04). It is a
legitimate implementation detail of the split, not an undeclared extra
surface -- the STRUCT-04 named files are submit_orders/sync_orders/
transition/idempotency.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from trading_platform.db.models import ExecutionEvent, OrderTransitionEventType
from trading_platform.db.models.symbol import Symbol
from trading_platform.services.execution.contracts import ExecutionOrderStatus, OrderSide
from trading_platform.services.execution.idempotency import DerivedOrderIdentity


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
    existing_orders: tuple[dict[str, Any], ...]
    missing_candidates: tuple[PaperExecutionCandidate, ...]


@dataclass(frozen=True)
class PaperIntentDecision:
    action: str
    identity: DerivedOrderIdentity
    intent_version: int
    existing_order_id: uuid.UUID | None
    supersedes_paper_order_id: uuid.UUID | None
    supersedes_client_order_id: str | None
    summary: dict[str, Any]


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


def _record_intent_decision_event(
    session,
    *,
    strategy_run_id: uuid.UUID,
    paper_order_id: uuid.UUID,
    event_type: str,
    message: str,
    details: dict[str, Any],
) -> None:
    session.add(
        ExecutionEvent(
            strategy_run_id=strategy_run_id,
            paper_order_id=paper_order_id,
            event_type=event_type,
            severity="info",
            blocks_execution=False,
            event_at=datetime.now(UTC),
            message=message,
            details=details,
        )
    )


def _ensure_symbol(session, ticker: str) -> Symbol:
    symbol_row = session.execute(select(Symbol).where(Symbol.ticker == ticker)).scalar_one_or_none()
    if symbol_row is not None:
        return symbol_row

    symbol_row = Symbol(ticker=ticker, active=True)
    session.add(symbol_row)
    session.flush()
    return symbol_row
