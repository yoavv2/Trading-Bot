"""Pure, evidence-based attribution of broker activity (COR-05; D-07, D-08, D-11).

Every broker order is classified ``owned`` / ``recorded_external`` / ``unrecognized``:

- ``owned`` requires a local intent registered BEFORE submission, matched by
  client_order_id or broker_order_id, whose symbol, side, quantity and order type equal
  the broker order's, whose broker ``created_at`` is not earlier than the local
  registration, and whose registration is covered by an ownership period of its own
  strategy (once periods exist). A mismatch is a BLOCKING anomaly from a closed set; an
  id (or an id FORMAT) alone is never ownership evidence.
- ``recorded_external`` is a stored, VERIFIED snapshot (EXT-01, D-10): the caller supplies
  the latest recorded item per broker order id, and the class applies only while the
  content hash recomputed from the CURRENT broker order and its fills still equals the
  stored one. A mismatch is ``unrecognized`` again (preserved origin tag plus
  ``recorded_snapshot_mismatch``); a recorded id that no longer appears in the broker
  orders is a blocking ``recorded_external_missing`` item. Nothing here calls the broker.
- everything else is ``unrecognized`` with a closed origin tag.

Fills inherit their order's class through ``broker_order_id``; a fill whose order is
absent from the broker order list is unrecognized. Unexplained exposure per symbol is
``broker signed quantity - net quantity of owned and recorded-external fills``.

This module is PURE: typed inputs in, typed result out. It imports no ORM, no DB
session and no broker HTTP client at runtime (the broker snapshot types are imported
under ``TYPE_CHECKING`` only).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from trading_platform.services.broker_status import BrokerStatusClass, classify_broker_status
from trading_platform.services.config.tolerances import QUANTITY_TOLERANCE
from trading_platform.services.execution.idempotency import client_order_id_prefix_fragment
from trading_platform.services.external_snapshot import RecordedExternalItem, content_hash

if TYPE_CHECKING:
    from trading_platform.services.alpaca import (
        BrokerFillSnapshot,
        BrokerOrderSnapshot,
        BrokerPositionSnapshot,
    )

#: Upper bound on per-item lists in ``AttributionResult.to_dict`` (counts stay exact).
MAX_REPORTED_ITEMS = 100


class OrderClass(StrEnum):
    OWNED = "owned"
    RECORDED_EXTERNAL = "recorded_external"
    UNRECOGNIZED = "unrecognized"


class OriginTag(StrEnum):
    """Why an unrecognized order is unrecognized (closed set)."""

    PLATFORM_FORMAT_UNVERIFIED = "platform_format_unverified"
    EXTERNAL_FORMAT = "external_format"
    STATUS_UNMAPPED = "status_unmapped"
    REPLACED_BY_SUCCESSOR = "replaced_by_successor"


class AttributionAnomaly(StrEnum):
    """Blocking mismatch between a local intent and the broker order it claims (closed set)."""

    OWNED_ORDER_ATTRIBUTE_MISMATCH = "owned_order_attribute_mismatch"
    OWNED_ORDER_BEFORE_REGISTRATION = "owned_order_before_registration"
    OWNED_ORDER_CREATED_AT_MISSING = "owned_order_created_at_missing"
    OWNED_ORDER_OUTSIDE_OWNERSHIP_PERIOD = "owned_order_outside_ownership_period"


class UnresolvedReason(StrEnum):
    """Reasons the broker history could not be fully examined (closed set)."""

    BROKER_HISTORY_EXCEEDS_CAP = "broker_history_exceeds_cap"


@dataclass(frozen=True)
class LocalIntentRecord:
    """A locally registered order intent (one per persisted paper order)."""

    strategy_id: str
    client_order_id: str
    broker_order_id: str | None
    symbol: str
    side: str
    quantity: Decimal
    order_type: str
    registered_at: datetime


@dataclass(frozen=True)
class OwnershipPeriod:
    """A recorded span during which ``strategy_id`` owned the paper account."""

    strategy_id: str
    start: datetime
    end: datetime | None = None


@dataclass(frozen=True)
class OrderAttribution:
    broker_order_id: str
    client_order_id: str
    symbol: str
    side: str
    quantity: Decimal
    order_class: OrderClass
    owner_strategy_id: str | None = None
    origin_tag: OriginTag | None = None
    anomaly: AttributionAnomaly | None = None
    # True when a recorded external snapshot exists for this order but the broker data
    # no longer matches its stored content hash (EXT-01); the order is then unrecognized.
    recorded_snapshot_mismatch: bool = False


@dataclass(frozen=True)
class FillAttribution:
    broker_fill_id: str
    broker_order_id: str
    symbol: str
    side: str
    quantity: Decimal
    order_class: OrderClass
    owner_strategy_id: str | None = None


@dataclass(frozen=True)
class AttributionResult:
    orders: tuple[OrderAttribution, ...] = ()
    fills: tuple[FillAttribution, ...] = ()
    unexplained_exposure: dict[str, Decimal] = field(default_factory=dict)
    unresolved_reasons: tuple[UnresolvedReason, ...] = ()
    # Recorded external orders (latest stored snapshot) absent from the broker orders.
    recorded_external_missing: tuple[str, ...] = ()

    @property
    def unrecognized_orders(self) -> tuple[OrderAttribution, ...]:
        return tuple(o for o in self.orders if o.order_class is OrderClass.UNRECOGNIZED)

    @property
    def unrecognized_fills(self) -> tuple[FillAttribution, ...]:
        return tuple(f for f in self.fills if f.order_class is OrderClass.UNRECOGNIZED)

    @property
    def anomalies(self) -> tuple[OrderAttribution, ...]:
        return tuple(o for o in self.orders if o.anomaly is not None)

    @property
    def blocks_execution(self) -> bool:
        return bool(
            self.unrecognized_orders
            or self.unrecognized_fills
            or self.anomalies
            or self.unexplained_exposure
            or self.unresolved_reasons
            or self.recorded_external_missing
        )

    def excluded_order_ids_for(self, strategy_id: str) -> frozenset[str]:
        """Broker order ids that are EXPLAINED outside ``strategy_id``'s own reconciliation.

        These are ``recorded_external`` orders and orders cleanly owned by ANOTHER
        strategy; a strategy-scoped reconciliation removes them (and their fills) from
        matching. An owned order with an anomaly is never excluded.
        """

        return frozenset(
            order.broker_order_id
            for order in self.orders
            if order.anomaly is None
            and (
                order.order_class is OrderClass.RECORDED_EXTERNAL
                or (
                    order.order_class is OrderClass.OWNED
                    and order.owner_strategy_id is not None
                    and order.owner_strategy_id != strategy_id
                )
            )
        )

    def to_dict(self) -> dict[str, Any]:
        """Bounded serialization: exact counts, capped item lists, no per-owned-order items."""

        order_counts = {cls.value: 0 for cls in OrderClass}
        for order in self.orders:
            order_counts[order.order_class.value] += 1
        fill_counts = {cls.value: 0 for cls in OrderClass}
        for fill in self.fills:
            fill_counts[fill.order_class.value] += 1

        unrecognized_orders = self.unrecognized_orders
        unrecognized_fills = self.unrecognized_fills
        anomalies = self.anomalies
        payload: dict[str, Any] = {
            "blocks_execution": self.blocks_execution,
            "orders": order_counts,
            "fills": fill_counts,
            "unrecognized_orders": [
                {
                    "broker_order_id": o.broker_order_id,
                    "client_order_id": o.client_order_id,
                    "symbol": o.symbol,
                    "side": o.side,
                    "quantity": str(o.quantity),
                    "origin_tag": o.origin_tag.value if o.origin_tag is not None else None,
                    # Only when true, so the item shape is unchanged for every other order.
                    **(
                        {"recorded_snapshot_mismatch": True} if o.recorded_snapshot_mismatch else {}
                    ),
                }
                for o in unrecognized_orders[:MAX_REPORTED_ITEMS]
            ],
            "unrecognized_orders_truncated": max(0, len(unrecognized_orders) - MAX_REPORTED_ITEMS),
            "unrecognized_fills": [
                {
                    "broker_fill_id": f.broker_fill_id,
                    "broker_order_id": f.broker_order_id,
                    "symbol": f.symbol,
                    "side": f.side,
                    "quantity": str(f.quantity),
                }
                for f in unrecognized_fills[:MAX_REPORTED_ITEMS]
            ],
            "unrecognized_fills_truncated": max(0, len(unrecognized_fills) - MAX_REPORTED_ITEMS),
            "anomalies": [
                {
                    "broker_order_id": o.broker_order_id,
                    "client_order_id": o.client_order_id,
                    "symbol": o.symbol,
                    "anomaly": o.anomaly.value if o.anomaly is not None else None,
                    "owner_strategy_id": o.owner_strategy_id,
                }
                for o in anomalies[:MAX_REPORTED_ITEMS]
            ],
            "anomalies_truncated": max(0, len(anomalies) - MAX_REPORTED_ITEMS),
            "unexplained_exposure": {
                symbol: str(quantity) for symbol, quantity in self.unexplained_exposure.items()
            },
            "unresolved_reasons": [reason.value for reason in self.unresolved_reasons],
        }
        if self.recorded_external_missing:
            # Only when non-empty: the key is absent (and the shape unchanged) otherwise.
            payload["recorded_external_missing"] = list(
                self.recorded_external_missing[:MAX_REPORTED_ITEMS]
            )
        return payload


def looks_like_platform_client_order_id(client_order_id: str, *, prefix: str) -> bool:
    """True iff the id has the platform shape for ``prefix`` (shape only; NEVER ownership)."""

    fragment = re.escape(client_order_id_prefix_fragment(prefix))
    return bool(
        re.fullmatch(rf"{fragment}-\d{{8}}-[a-z0-9]{{1,8}}-[0-9a-f]{{18}}", client_order_id)
    )


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _differs(left: Decimal, right: Decimal) -> bool:
    return abs(left - right) > QUANTITY_TOLERANCE


def _covered(period: OwnershipPeriod, registered_at: datetime) -> bool:
    return _aware(period.start) <= registered_at and (
        period.end is None or registered_at < _aware(period.end)
    )


def _anomaly_for(
    order: BrokerOrderSnapshot,
    record: LocalIntentRecord,
    *,
    ownership_periods: Sequence[OwnershipPeriod],
    earliest_period_start: datetime | None,
) -> AttributionAnomaly | None:
    if (
        order.symbol != record.symbol
        or str(order.side) != record.side
        or _differs(order.quantity, record.quantity)
        or order.order_type != record.order_type
    ):
        return AttributionAnomaly.OWNED_ORDER_ATTRIBUTE_MISMATCH

    registered_at = _aware(record.registered_at)
    if order.created_at is None:
        return AttributionAnomaly.OWNED_ORDER_CREATED_AT_MISSING
    if _aware(order.created_at) < registered_at:
        return AttributionAnomaly.OWNED_ORDER_BEFORE_REGISTRATION

    if earliest_period_start is not None and registered_at >= earliest_period_start:
        own_periods = (p for p in ownership_periods if p.strategy_id == record.strategy_id)
        if not any(_covered(period, registered_at) for period in own_periods):
            return AttributionAnomaly.OWNED_ORDER_OUTSIDE_OWNERSHIP_PERIOD
    return None


def _origin_tag_for(order: BrokerOrderSnapshot, *, platform_prefix: str) -> OriginTag:
    if order.replaces_order_id:
        return OriginTag.REPLACED_BY_SUCCESSOR
    if (
        order.status_reason is not None
        or classify_broker_status(order.broker_status) is BrokerStatusClass.UNKNOWN
    ):
        return OriginTag.STATUS_UNMAPPED
    if looks_like_platform_client_order_id(order.client_order_id, prefix=platform_prefix):
        return OriginTag.PLATFORM_FORMAT_UNVERIFIED
    return OriginTag.EXTERNAL_FORMAT


def _signed(side: str, quantity: Decimal) -> Decimal:
    return quantity if str(side) == "buy" else -quantity


def classify_broker_activity(
    *,
    broker_orders: Iterable[BrokerOrderSnapshot],
    broker_fills: Iterable[BrokerFillSnapshot],
    broker_positions: Iterable[BrokerPositionSnapshot],
    local_intents: Iterable[LocalIntentRecord],
    ownership_periods: Sequence[OwnershipPeriod] = (),
    recorded_external: Mapping[str, RecordedExternalItem] = MappingProxyType({}),
    platform_prefix: str,
    unresolved_reasons: Iterable[UnresolvedReason] = (),
) -> AttributionResult:
    """Classify all broker orders and fills and compute unexplained exposure per symbol.

    ``recorded_external`` maps broker order id to the latest stored snapshot; it explains
    an order only while the hash recomputed from ``broker_orders``/``broker_fills`` still
    matches (no broker call, no extra query).
    """

    intents = tuple(local_intents)
    by_client_id = {r.client_order_id: r for r in intents if r.client_order_id}
    by_broker_id = {r.broker_order_id: r for r in intents if r.broker_order_id}
    periods = tuple(ownership_periods)
    earliest_start = min((_aware(p.start) for p in periods), default=None)

    fills_by_order: dict[str, list[BrokerFillSnapshot]] = {}
    if recorded_external:
        for broker_fill in broker_fills:
            fills_by_order.setdefault(broker_fill.broker_order_id, []).append(broker_fill)
    seen_order_ids: set[str] = set()

    order_results: list[OrderAttribution] = []
    class_by_broker_order_id: dict[str, OrderAttribution] = {}
    for order in broker_orders:
        record = by_client_id.get(order.client_order_id)
        if record is None:
            record = by_broker_id.get(order.broker_order_id)

        if record is not None:
            result = OrderAttribution(
                broker_order_id=order.broker_order_id,
                client_order_id=order.client_order_id,
                symbol=order.symbol,
                side=str(order.side),
                quantity=order.quantity,
                order_class=OrderClass.OWNED,
                owner_strategy_id=record.strategy_id,
                anomaly=_anomaly_for(
                    order,
                    record,
                    ownership_periods=periods,
                    earliest_period_start=earliest_start,
                ),
            )
        elif order.broker_order_id in recorded_external:
            item = recorded_external[order.broker_order_id]
            verified = (
                content_hash(order, fills_by_order.get(order.broker_order_id, ()))
                == item.content_hash
            )
            result = OrderAttribution(
                broker_order_id=order.broker_order_id,
                client_order_id=order.client_order_id,
                symbol=order.symbol,
                side=str(order.side),
                quantity=order.quantity,
                order_class=OrderClass.RECORDED_EXTERNAL if verified else OrderClass.UNRECOGNIZED,
                origin_tag=None if verified else OriginTag(item.origin_tag),
                recorded_snapshot_mismatch=not verified,
            )
        else:
            result = OrderAttribution(
                broker_order_id=order.broker_order_id,
                client_order_id=order.client_order_id,
                symbol=order.symbol,
                side=str(order.side),
                quantity=order.quantity,
                order_class=OrderClass.UNRECOGNIZED,
                origin_tag=_origin_tag_for(order, platform_prefix=platform_prefix),
            )
        order_results.append(result)
        class_by_broker_order_id[order.broker_order_id] = result
        seen_order_ids.add(order.broker_order_id)

    fill_results: list[FillAttribution] = []
    explained_net: dict[str, Decimal] = {}
    for fill in broker_fills:
        parent = class_by_broker_order_id.get(fill.broker_order_id)
        fill_class = parent.order_class if parent is not None else OrderClass.UNRECOGNIZED
        fill_results.append(
            FillAttribution(
                broker_fill_id=fill.broker_fill_id,
                broker_order_id=fill.broker_order_id,
                symbol=fill.symbol,
                side=str(fill.side),
                quantity=fill.quantity,
                order_class=fill_class,
                owner_strategy_id=parent.owner_strategy_id if parent is not None else None,
            )
        )
        if fill_class is not OrderClass.UNRECOGNIZED:
            explained_net[fill.symbol] = explained_net.get(fill.symbol, Decimal("0")) + _signed(
                str(fill.side), fill.quantity
            )

    broker_net: dict[str, Decimal] = {}
    for position in broker_positions:
        broker_net[position.symbol] = broker_net.get(position.symbol, Decimal("0")) + (
            position.quantity
        )

    unexplained: dict[str, Decimal] = {}
    for symbol in sorted(broker_net.keys() | explained_net.keys()):
        residual = broker_net.get(symbol, Decimal("0")) - explained_net.get(symbol, Decimal("0"))
        if abs(residual) > QUANTITY_TOLERANCE:
            unexplained[symbol] = residual

    return AttributionResult(
        orders=tuple(order_results),
        fills=tuple(fill_results),
        unexplained_exposure=unexplained,
        unresolved_reasons=tuple(unresolved_reasons),
        recorded_external_missing=tuple(sorted(set(recorded_external) - seen_order_ids)),
    )
