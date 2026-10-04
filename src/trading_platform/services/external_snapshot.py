"""Pure canonical encoding and content hash of external broker snapshots (EXT-01, D-10).

A recorded external order is explained only while the broker still shows exactly what
was verified at record time. That comparison is a SHA-256 over a canonical encoding of
the broker order (typed fields only, never the raw payload) and its fills sorted by
activity id. The same function is used when recording and on every later check, so the
check needs no extra broker call: it recomputes the hash from the broker lists it
already loaded.

This module is PURE: no ORM, no DB session, no HTTP client at runtime (broker snapshot
types are imported under ``TYPE_CHECKING`` only), so the pure attribution classifier can
use it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from trading_platform.services.alpaca import BrokerFillSnapshot, BrokerOrderSnapshot


def _decimal(value: Decimal | None) -> str | None:
    """Fixed-point string; numerically equal values encode identically (10 == 10.000000)."""

    if value is None:
        return None
    return format(value.normalize(), "f")


def _timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return aware.astimezone(UTC).isoformat()


def _raw_timestamp(value: Any) -> str | None:
    """Normalize a raw broker timestamp string; missing or malformed is ``None``."""

    if not isinstance(value, str) or not value:
        return None
    try:
        return _timestamp(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


def canonical_order(order: BrokerOrderSnapshot) -> dict[str, Any]:
    """Typed, JSON-safe encoding of a broker order (``updated_at`` and raw payload excluded)."""

    return {
        "broker_order_id": order.broker_order_id,
        "client_order_id": order.client_order_id or None,
        "symbol": order.symbol,
        "side": str(order.side),
        "qty": _decimal(order.quantity),
        "status": order.broker_status,
        "order_type": order.order_type,
        "created_at": _timestamp(order.created_at),
        "submitted_at": _timestamp(order.submitted_at),
        "filled_at": _timestamp(order.filled_at),
        "canceled_at": _timestamp(order.canceled_at),
        "filled_qty": _decimal(order.filled_quantity),
        "filled_avg_price": _decimal(order.filled_avg_price),
        "replaces_order_id": order.replaces_order_id,
        "successor_order_id": order.successor_order_id,
    }


def canonical_fill(fill: BrokerFillSnapshot) -> dict[str, Any]:
    """Typed, JSON-safe encoding of a broker fill.

    ``transaction_time`` comes from the raw activity (the typed ``filled_at`` falls back
    to the clock when the broker omits it, which would make the hash non-deterministic).
    """

    return {
        "id": fill.broker_fill_id,
        "order_id": fill.broker_order_id,
        "symbol": fill.symbol,
        "side": str(fill.side),
        "qty": _decimal(fill.quantity),
        "price": _decimal(fill.price),
        "transaction_time": _raw_timestamp(fill.raw_payload.get("transaction_time")),
    }


def canonical_fills(fills: Iterable[BrokerFillSnapshot]) -> list[dict[str, Any]]:
    """Canonical fills sorted by activity id (input order never matters)."""

    return sorted((canonical_fill(fill) for fill in fills), key=lambda item: str(item["id"]))


def hash_canonical(order: Mapping[str, Any], fills: Sequence[Mapping[str, Any]]) -> str:
    """SHA-256 hex of the canonical encoding (sorted keys, fills re-sorted by id)."""

    ordered_fills = sorted((dict(item) for item in fills), key=lambda item: str(item["id"]))
    encoded = json.dumps(
        {"order": dict(order), "fills": ordered_fills},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(encoded.encode("ascii")).hexdigest()


def content_hash(order: BrokerOrderSnapshot, fills: Iterable[BrokerFillSnapshot]) -> str:
    """Deterministic content hash of one broker order and its fills."""

    return hash_canonical(canonical_order(order), canonical_fills(fills))


@dataclass(frozen=True)
class RecordedExternalItem:
    """The latest recorded snapshot of one external order, as stored (no owner, no position)."""

    broker_order_id: str
    content_hash: str
    origin_tag: str
    symbol: str
    side: str
    filled_qty: Decimal

    @property
    def signed_filled_qty(self) -> Decimal:
        return self.filled_qty if self.side == "buy" else -self.filled_qty


__all__ = [
    "RecordedExternalItem",
    "canonical_fill",
    "canonical_fills",
    "canonical_order",
    "content_hash",
    "hash_canonical",
]
