"""Audited recording of external broker activity (EXT-01; D-08, D-10; 03 section 3.5).

``record_external_orders`` re-fetches each listed order from the broker (by broker order
id) together with that order's fills, never trusting caller-supplied order content. It
stores an immutable snapshot only when ALL preconditions hold:

- every order can be returned by the broker (else ``broker_record_unavailable``);
- no listed order is currently owned by a strategy (else ``order_owned_by_strategy``;
  a recorded external item is never an owned item, so there is no adoption);
- every listed order is terminal at the broker, a ``replaced`` order only together with
  its terminal successor, and an unmapped status counts as NOT terminal (else
  ``external_order_not_terminal``);
- the net filled exposure of all previously recorded plus to-be-recorded external
  activity is zero in EVERY symbol (else ``external_exposure_nonzero``).

Any failed precondition raises ``ExternalActivityRejectedError`` with zero writes. The
rows carry no owner and no link to local order, fill or holding records: recording
explains broker activity, it never creates local state. Recording by itself lifts no
block; the caller (the Job handler) runs a fresh account-level reconciliation whose
stored result is the only thing a gate reads.

Later checks re-verify recorded items from the broker lists they already load
(``attribution.classify_broker_activity`` recomputes the content hash), so a recorded
item that the broker later contradicts is unrecognized again.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from trading_platform.core.settings import Settings, load_settings
from trading_platform.db.models import ExternalActivityOriginTag, ExternalBrokerActivity
from trading_platform.db.session import session_scope
from trading_platform.services.alpaca import AlpacaClient
from trading_platform.services.attribution import OrderClass, classify_broker_activity
from trading_platform.services.attribution_inputs import (
    load_local_intent_records,
    load_ownership_periods,
    load_recorded_external,
)
from trading_platform.services.broker_status import BrokerStatusClass, classify_broker_status
from trading_platform.services.config.tolerances import QUANTITY_TOLERANCE
from trading_platform.services.external_snapshot import (
    RecordedExternalItem,
    canonical_fills,
    canonical_order,
    content_hash,
    hash_canonical,
)

if TYPE_CHECKING:
    from trading_platform.services.alpaca import BrokerFillSnapshot, BrokerOrderSnapshot

#: Serializes concurrent recordings so two overlapping batches that are each net-zero
#: cannot race into a non-zero total (transaction-scoped advisory lock).
_RECORDING_LOCK_SQL = text("SELECT pg_advisory_xact_lock(hashtext('external_broker_activity'))")

_ORIGIN_VALUES = frozenset(member.value for member in ExternalActivityOriginTag)


class ExternalActivityRejection(StrEnum):
    """Closed reasons a recording request is refused (one test per value)."""

    EXTERNAL_ORDER_NOT_TERMINAL = "external_order_not_terminal"
    EXTERNAL_EXPOSURE_NONZERO = "external_exposure_nonzero"
    BROKER_RECORD_UNAVAILABLE = "broker_record_unavailable"
    ORDER_OWNED_BY_STRATEGY = "order_owned_by_strategy"


class ExternalActivityRejectedError(Exception):
    """A recording request refused before ANY write (nothing stored, nothing sent)."""

    def __init__(self, reason: ExternalActivityRejection, order_ids: Sequence[str]) -> None:
        self.reason = reason
        self.order_ids = tuple(order_ids)
        super().__init__(self.failure_message())

    def failure_message(self) -> str:
        """The closed reason is the first token (the Job's ``failure_message``)."""

        return f"{self.reason.value}: {', '.join(self.order_ids)}"


@dataclass(frozen=True)
class ExternalActivityRecordResult:
    """What one recording call stored."""

    recorded_activity_ids: tuple[str, ...] = ()
    already_recorded_order_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class _VerifiedOrder:
    order: BrokerOrderSnapshot
    fills: tuple[BrokerFillSnapshot, ...]

    @property
    def net_by_symbol(self) -> dict[str, Decimal]:
        net: dict[str, Decimal] = {}
        for fill in self.fills:
            signed = fill.quantity if str(fill.side) == "buy" else -fill.quantity
            net[fill.symbol] = net.get(fill.symbol, Decimal("0")) + signed
        return net


def _fetch_from_broker(
    order_ids: Sequence[str],
    *,
    settings: Settings,
    broker_client: AlpacaClient | None,
) -> list[_VerifiedOrder]:
    """Broker READS only, before any database session is opened.

    Any lookup, transport, auth or pagination-cap failure, an HTTP 404, an id the broker
    answers differently, or fills that do not add up to the order's filled quantity is
    ``broker_record_unavailable`` (fail closed: nothing is inferred from absence).
    """

    owns_client = broker_client is None
    client = broker_client or AlpacaClient(settings.broker.alpaca)
    try:
        orders: list[BrokerOrderSnapshot] = []
        for order_id in order_ids:
            order = client.get_order_by_broker_order_id(order_id)
            if order is None or order.broker_order_id != order_id:
                raise ExternalActivityRejectedError(
                    ExternalActivityRejection.BROKER_RECORD_UNAVAILABLE, [order_id]
                )
            orders.append(order)
        all_fills = client.list_fills()
    except ExternalActivityRejectedError:
        raise
    except Exception as exc:
        raise ExternalActivityRejectedError(
            ExternalActivityRejection.BROKER_RECORD_UNAVAILABLE, order_ids
        ) from exc
    finally:
        if owns_client:
            client.close()

    fills_by_order: dict[str, list[BrokerFillSnapshot]] = {}
    for fill in all_fills:
        fills_by_order.setdefault(fill.broker_order_id, []).append(fill)

    verified: list[_VerifiedOrder] = []
    for order in orders:
        fills = tuple(fills_by_order.get(order.broker_order_id, ()))
        if order.filled_quantity is not None:
            fill_total = sum((fill.quantity for fill in fills), start=Decimal("0"))
            if abs(fill_total - order.filled_quantity) > QUANTITY_TOLERANCE:
                raise ExternalActivityRejectedError(
                    ExternalActivityRejection.BROKER_RECORD_UNAVAILABLE, [order.broker_order_id]
                )
        verified.append(_VerifiedOrder(order=order, fills=fills))
    return verified


def _non_terminal_ids(verified: Sequence[_VerifiedOrder]) -> list[str]:
    """Listed orders that are not provably terminal (unknown status is NOT terminal)."""

    listed = {item.order.broker_order_id for item in verified}
    offenders: list[str] = []
    for item in verified:
        order = item.order
        status_class = classify_broker_status(order.broker_status)
        if status_class is BrokerStatusClass.TERMINAL:
            continue
        if (
            status_class is BrokerStatusClass.TERMINAL_WITH_SUCCESSOR
            and order.successor_order_id
            and order.successor_order_id in listed
        ):
            continue
        offenders.append(order.broker_order_id)
    return offenders


def _nonzero_exposure(
    verified: Sequence[_VerifiedOrder], recorded: dict[str, RecordedExternalItem]
) -> dict[str, Decimal]:
    """Net signed filled quantity per symbol over recorded plus listed orders.

    Keyed by broker order id with the CURRENT broker snapshot of every listed order
    winning, so an already-recorded order listed again is never counted twice.
    """

    listed = {item.order.broker_order_id: item for item in verified}
    net: dict[str, Decimal] = {}
    for order_id, prior in recorded.items():
        if order_id in listed:
            continue
        net[prior.symbol] = net.get(prior.symbol, Decimal("0")) + prior.signed_filled_qty
    for current in listed.values():
        for symbol, quantity in current.net_by_symbol.items():
            net[symbol] = net.get(symbol, Decimal("0")) + quantity
    return {symbol: qty for symbol, qty in net.items() if abs(qty) > QUANTITY_TOLERANCE}


def record_external_orders(
    order_ids: Sequence[str],
    reason: str,
    job_id: uuid.UUID | None = None,
    settings: Settings | None = None,
    broker_client: AlpacaClient | None = None,
) -> ExternalActivityRecordResult:
    """Verify and record external broker orders (all-or-nothing; zero writes on refusal)."""

    resolved = settings or load_settings()
    clean_reason = reason.strip()
    if not clean_reason:
        raise ValueError("A non-blank reason is required to record external activity.")
    listed_ids = list(dict.fromkeys(order_ids))
    if not listed_ids:
        raise ValueError("At least one broker order id is required.")

    verified = _fetch_from_broker(listed_ids, settings=resolved, broker_client=broker_client)

    with session_scope(resolved) as session:
        session.execute(_RECORDING_LOCK_SQL)
        recorded = load_recorded_external(session)
        attribution = classify_broker_activity(
            broker_orders=[item.order for item in verified],
            broker_fills=[fill for item in verified for fill in item.fills],
            broker_positions=(),
            local_intents=load_local_intent_records(session),
            ownership_periods=load_ownership_periods(session),
            recorded_external=recorded,
            platform_prefix=resolved.execution.client_order_id_prefix,
        )
        by_id = {order.broker_order_id: order for order in attribution.orders}

        owned = [oid for oid in listed_ids if by_id[oid].order_class is OrderClass.OWNED]
        if owned:
            raise ExternalActivityRejectedError(
                ExternalActivityRejection.ORDER_OWNED_BY_STRATEGY, owned
            )
        not_terminal = _non_terminal_ids(verified)
        if not_terminal:
            raise ExternalActivityRejectedError(
                ExternalActivityRejection.EXTERNAL_ORDER_NOT_TERMINAL, not_terminal
            )
        if _nonzero_exposure(verified, recorded):
            raise ExternalActivityRejectedError(
                ExternalActivityRejection.EXTERNAL_EXPOSURE_NONZERO, listed_ids
            )

        already = {
            oid for oid in listed_ids if by_id[oid].order_class is OrderClass.RECORDED_EXTERNAL
        }
        rows: list[dict[str, object]] = []
        for item in verified:
            order = item.order
            if order.broker_order_id in already:
                continue
            origin = by_id[order.broker_order_id].origin_tag
            if origin is None or origin.value not in _ORIGIN_VALUES:
                # status_unmapped cannot be proven terminal; unreachable after the check above.
                raise ExternalActivityRejectedError(
                    ExternalActivityRejection.EXTERNAL_ORDER_NOT_TERMINAL,
                    [order.broker_order_id],
                )
            snapshot = canonical_order(order)
            fills = canonical_fills(item.fills)
            fill_total = sum((fill.quantity for fill in item.fills), start=Decimal("0"))
            rows.append(
                {
                    "id": uuid.uuid4(),
                    "job_id": job_id,
                    "broker_order_id": order.broker_order_id,
                    "client_order_id": order.client_order_id or None,
                    "symbol": order.symbol,
                    "side": str(order.side),
                    "status": order.broker_status,
                    "qty": order.quantity,
                    "filled_qty": (
                        order.filled_quantity if order.filled_quantity is not None else fill_total
                    ),
                    "filled_avg_price": order.filled_avg_price,
                    "broker_created_at": order.created_at,
                    "successor_broker_order_id": order.successor_order_id,
                    "origin_tag": origin.value,
                    "reason": clean_reason,
                    "order_snapshot": snapshot,
                    "fills": fills,
                    "content_hash": hash_canonical(snapshot, fills),
                }
            )

        inserted: dict[str, str] = {}
        if rows:
            returned = session.execute(
                pg_insert(ExternalBrokerActivity)
                .values(rows)
                .on_conflict_do_nothing(constraint="uq_external_broker_activity_order_hash")
                .returning(ExternalBrokerActivity.id, ExternalBrokerActivity.broker_order_id)
            ).all()
            inserted = {str(row[1]): str(row[0]) for row in returned}

    recorded_ids = tuple(inserted[oid] for oid in listed_ids if oid in inserted)
    already_ids = tuple(oid for oid in listed_ids if oid not in inserted)
    return ExternalActivityRecordResult(
        recorded_activity_ids=recorded_ids, already_recorded_order_ids=already_ids
    )


__all__ = [
    "ExternalActivityRecordResult",
    "ExternalActivityRejectedError",
    "ExternalActivityRejection",
    "RecordedExternalItem",
    "content_hash",
    "load_recorded_external",
    "record_external_orders",
]
