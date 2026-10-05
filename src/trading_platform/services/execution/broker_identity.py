"""D-07 identity evidence for binding a broker record to a local intent (SAF-07).

The one check used by the recovery lookup, the bulk sync and the pre-lock recovery.
A ``client_order_id`` alone is never ownership evidence: symbol, side, quantity and
order type must match and the broker ``created_at`` must not precede the local
registration. A mismatch binds nothing and is recorded as a blocking
``broker_order_identity_mismatch`` execution event.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from trading_platform.core import clock
from trading_platform.db.models import ExecutionEvent, PaperOrder
from trading_platform.services.alpaca import BrokerOrderSnapshot

IDENTITY_MISMATCH_EVENT_TYPE = "broker_order_identity_mismatch"


def _parse_dt(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def broker_record_mismatch(
    order: PaperOrder, ticker: str, snapshot: BrokerOrderSnapshot
) -> str | None:
    """Why a broker record is NOT evidence for the local intent (D-07), or ``None``.

    Typed snapshot fields are read first; the raw payload is only a fallback for a typed
    field that is absent (list snapshots carry typed fields while their payload may omit
    ``qty``/``type``).
    """

    raw = snapshot.raw_payload or {}
    if snapshot.client_order_id != order.client_order_id:
        return "client_order_id"
    if snapshot.symbol != ticker:
        return "symbol"
    if snapshot.side.value != order.side:
        return "side"
    quantity = snapshot.quantity if snapshot.quantity is not None else raw.get("qty")
    if quantity is None or Decimal(str(quantity)) != Decimal(str(order.quantity)):
        return "quantity"
    order_type = snapshot.order_type or str(raw.get("type") or "")
    if order_type != order.order_type:
        return "type"
    created = snapshot.created_at or _parse_dt(raw.get("created_at"))
    registered = order.created_at
    if registered is not None:
        if created is None:
            return "created_at"
        if registered.tzinfo is None:
            registered = registered.replace(tzinfo=created.tzinfo)
        if created < registered:
            return "created_at"
    return None


def record_identity_mismatch(
    session: Any, order: PaperOrder, snapshot: BrokerOrderSnapshot, field: str
) -> None:
    """Record the blocking evidence that a broker record was refused for binding."""

    session.add(
        ExecutionEvent(
            strategy_run_id=order.strategy_run_id,
            paper_order_id=order.id,
            event_type=IDENTITY_MISMATCH_EVENT_TYPE,
            severity="error",
            blocks_execution=True,
            event_at=clock.now_utc(),
            message=(
                f"Broker order '{snapshot.broker_order_id}' shares the client_order_id of "
                f"intent '{order.client_order_id}' but its {field} differs; it was not bound."
            ),
            details={
                "field": field,
                "client_order_id": order.client_order_id,
                "broker_order_id": snapshot.broker_order_id,
                "broker_symbol": snapshot.symbol,
            },
        )
    )
    session.flush()


def local_ticker(order: PaperOrder) -> str:
    """The local order's ticker (empty when the symbol relationship is unavailable)."""

    return order.symbol_ref.ticker if order.symbol_ref is not None else ""
