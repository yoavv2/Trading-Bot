"""Provider-agnostic execution contracts for paper-order submission."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any


class OrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderType(StrEnum):
    MARKET = "market"


class OrderTimeInForce(StrEnum):
    DAY = "day"


class ExecutionOrderStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class OrderIntent:
    strategy_id: str
    symbol: str
    side: OrderSide
    quantity: Decimal
    intended_session: date
    client_order_id: str
    order_type: OrderType = OrderType.MARKET
    time_in_force: OrderTimeInForce = OrderTimeInForce.DAY
    reference_price: Decimal | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OrderSubmissionResult:
    client_order_id: str
    broker_order_id: str
    symbol: str
    side: OrderSide
    quantity: Decimal
    order_type: OrderType
    time_in_force: OrderTimeInForce
    status: ExecutionOrderStatus
    broker_status: str
    submitted_at: datetime | None
    raw_payload: dict[str, Any] = field(default_factory=dict)


class ExecutionService(ABC):
    @abstractmethod
    def describe(self) -> dict[str, Any]:
        """Describe the execution capability exposed to the platform."""

    @abstractmethod
    def submit_order(self, intent: OrderIntent) -> OrderSubmissionResult:
        """Submit one provider-agnostic order intent."""


class PlaceholderExecutionService(ExecutionService):
    def describe(self) -> dict[str, Any]:
        return {
            "service": "execution",
            "status": "deferred",
            "detail": "Deferred to Phase 5 paper execution.",
        }

    def submit_order(self, intent: OrderIntent) -> OrderSubmissionResult:
        raise NotImplementedError("Execution is deferred to Phase 5.")
