"""Pure, indexed broker/local matcher (RECON-06, RECON-08).

``match_snapshots`` is the algorithm core of the reconciliation rewrite: it consumes
already-typed snapshots (see ``reconciliation_types``) and emits only closed-enum
``Finding`` values. It is deliberately PURE — no DB session, no broker HTTP client, no
mutation of its inputs — so it stays trivially unit-testable and can be benchmarked for
comparison-count scaling independent of I/O.

Every entity kind (positions, orders, fills) is resolved through a dict keyed on a
stable identity, built with exactly one pass over each side, then looked up — never a
nested ``for x in local: for y in broker`` scan. This is what makes the matcher O(n) in
total entity count rather than O(n * m) (RECON-06, Phase 9 Success Criterion 2).

Positions key on ``ReconciliationIdentity`` (``symbol``, ``account``, ``side``), which
deliberately includes ``side``: a position that flips sign (e.g. local LONG 10, broker
SHORT 5) is two different identities, so it surfaces as a MISSING_BROKER + MISSING_LOCAL
pair rather than being silently interpreted as a same-identity quantity mismatch. Flat
positions (zero quantity) are filtered out of both sides before the key-union is built,
so a flat/flat pair — or a flat position present on only one side — never reaches the
finding loop (RECON-08).

Orders are matched by ``client_order_id`` first, falling back to ``broker_order_id``:
this preserves the existing "prefer client_order_id when a version-chain successor has
taken over an in-flight broker order" behavior. Fills are matched by ``broker_fill_id``.

This module has NO ``session_scope``, ``AlpacaClient``, or ``db.models`` (ORM) imports.
The concrete broker snapshot types are imported only under ``TYPE_CHECKING`` so the
``services.alpaca`` (httpx) import never happens at runtime, mirroring the pattern
established in ``reconciliation_types``.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from trading_platform.services.execution import ExecutionOrderStatus
from trading_platform.services.reconciliation_types import (
    Finding,
    LocalFillSnapshot,
    LocalOrderSnapshot,
    LocalPositionSnapshot,
    ReconciliationFinding,
    ReconciliationIdentity,
    identity_for_broker_position,
)

if TYPE_CHECKING:
    # Import only for type-checking so this module never pulls the broker HTTP client
    # (services.alpaca imports httpx) into its runtime import graph.
    from trading_platform.services.alpaca import (
        BrokerFillSnapshot,
        BrokerOrderSnapshot,
        BrokerPositionSnapshot,
    )

# Tolerances carried forward from the pre-rewrite `reconciliation.py` matching logic.
_MONEY_TOLERANCE = Decimal("0.01")
_QUANTITY_TOLERANCE = Decimal("0.000001")

# Local order lifecycle strings that count as "still active" from the broker's point of
# view. Expressed as plain strings (not the ORM `OrderLifecycleState` enum) so this
# module stays free of `db.models` imports.
_ACTIVE_LOCAL_ORDER_STATUSES = {"pending_submission", "submitted", "partially_filled"}

# Broker `ExecutionOrderStatus` -> the local lifecycle string it is expected to produce.
# Mirrors the pre-rewrite `_local_state_from_broker_status` mapping, but expressed with
# plain strings (rather than routing through `OrderTransitionEventType`/
# `OrderLifecycleState`, both ORM-adjacent enums) to keep this module ORM-free.
_BROKER_STATUS_TO_EXPECTED_LOCAL_STATUS: dict[ExecutionOrderStatus, str] = {
    ExecutionOrderStatus.PENDING: "submitted",
    ExecutionOrderStatus.ACCEPTED: "submitted",
    ExecutionOrderStatus.PARTIALLY_FILLED: "partially_filled",
    ExecutionOrderStatus.FILLED: "filled",
    ExecutionOrderStatus.CANCELED: "canceled",
    ExecutionOrderStatus.REJECTED: "rejected",
    ExecutionOrderStatus.EXPIRED: "expired",
}
_UNKNOWN_LOCAL_STATUS = "unknown"


def match_snapshots(
    *,
    local_orders: list[LocalOrderSnapshot],
    local_fills: list[LocalFillSnapshot],
    local_positions: list[LocalPositionSnapshot],
    broker_orders: list[BrokerOrderSnapshot],
    broker_fills: list[BrokerFillSnapshot],
    broker_positions: list[BrokerPositionSnapshot],
) -> tuple[Finding, ...]:
    """Match local and broker snapshots, emitting only closed-enum ``Finding`` values.

    Pure function: no DB, no I/O, no mutation of any input. Positions, orders, and
    fills are each resolved through a single keyed dict-lookup pass (RECON-06); flat
    positions never produce a finding (RECON-08).

    Delegates to ``match_snapshots_with_comparisons`` and returns only its findings, so
    there is a single code path for the actual matching logic (PERF-02).
    """
    findings, _ = match_snapshots_with_comparisons(
        local_orders=local_orders,
        local_fills=local_fills,
        local_positions=local_positions,
        broker_orders=broker_orders,
        broker_fills=broker_fills,
        broker_positions=broker_positions,
    )
    return findings


def match_snapshots_with_comparisons(
    *,
    local_orders: list[LocalOrderSnapshot],
    local_fills: list[LocalFillSnapshot],
    local_positions: list[LocalPositionSnapshot],
    broker_orders: list[BrokerOrderSnapshot],
    broker_fills: list[BrokerFillSnapshot],
    broker_positions: list[BrokerPositionSnapshot],
) -> tuple[tuple[Finding, ...], int]:
    """Match snapshots and also return the total comparison count (PERF-02).

    Same parameters and matching behavior as ``match_snapshots``, but additionally
    returns the summed comparison count across positions, orders, and fills, so a
    linear-scaling benchmark can assert on the actual public entry point rather than
    only on the private per-component matchers.
    """
    position_findings, position_comparisons = _match_positions(local_positions, broker_positions)
    order_findings, order_comparisons = _match_orders(local_orders, broker_orders)
    fill_findings, fill_comparisons = _match_fills(local_fills, broker_fills, local_orders=local_orders)
    total_comparisons = position_comparisons + order_comparisons + fill_comparisons
    return position_findings + order_findings + fill_findings, total_comparisons


def _match_positions(
    local_positions: list[LocalPositionSnapshot],
    broker_positions: list[BrokerPositionSnapshot],
) -> tuple[tuple[Finding, ...], int]:
    """Resolve positions via a single ``(symbol, account, side)``-keyed map.

    Flat (zero-quantity) positions are filtered out of BOTH sides before the key-union
    is built, so they never reach the finding loop below (RECON-08) — this covers
    flat/flat pairs AND a flat position present on only one side.

    Because ``side`` is part of the identity key, a position that flips sign between
    local and broker (e.g. local LONG 10, broker SHORT 5) resolves to two DIFFERENT
    identities, not one. That deliberately yields a MISSING_BROKER + MISSING_LOCAL pair
    rather than a single QUANTITY_MISMATCH — see module docstring.
    """
    local_by_identity: dict[ReconciliationIdentity, LocalPositionSnapshot] = {
        position.identity(): position for position in local_positions if position.quantity != 0
    }
    broker_by_identity: dict[ReconciliationIdentity, BrokerPositionSnapshot] = {
        identity_for_broker_position(position): position
        for position in broker_positions
        if position.quantity != 0
    }

    findings: list[Finding] = []
    comparisons = 0
    for identity in local_by_identity.keys() | broker_by_identity.keys():
        comparisons += 1
        local_position = local_by_identity.get(identity)
        broker_position = broker_by_identity.get(identity)

        if local_position is None:
            findings.append(_missing_local_position_finding(identity, broker_position))
            continue
        if broker_position is None:
            findings.append(_missing_broker_position_finding(identity, local_position))
            continue

        if _decimal_differs(local_position.quantity, broker_position.quantity, tolerance=_QUANTITY_TOLERANCE):
            findings.append(_quantity_mismatch_finding(identity, local_position, broker_position))
        if _decimal_differs(
            local_position.average_entry_price,
            broker_position.average_entry_price,
            tolerance=_MONEY_TOLERANCE,
        ):
            findings.append(_price_mismatch_finding(identity, local_position, broker_position))

    return tuple(findings), comparisons


def _match_orders(
    local_orders: list[LocalOrderSnapshot],
    broker_orders: list[BrokerOrderSnapshot],
) -> tuple[tuple[Finding, ...], int]:
    """Resolve orders via client_order_id first, then broker_order_id.

    Single pass to build both local index maps, single pass over broker orders to
    resolve/emit MISSING_LOCAL/STATE_MISMATCH, single pass over local orders to emit
    MISSING_BROKER for unmatched active orders — never a nested scan.
    """
    local_by_client_id = {
        order.client_order_id: order for order in local_orders if order.client_order_id
    }
    local_by_broker_id = {
        order.broker_order_id: order for order in local_orders if order.broker_order_id
    }

    findings: list[Finding] = []
    comparisons = 0
    matched_client_order_ids: set[str] = set()

    for broker_order in broker_orders:
        comparisons += 1
        local_order = local_by_client_id.get(broker_order.client_order_id)
        if local_order is None:
            local_order = local_by_broker_id.get(broker_order.broker_order_id)
        if local_order is None:
            findings.append(_missing_local_order_finding(broker_order))
            continue

        matched_client_order_ids.add(local_order.client_order_id)
        expected_status = _BROKER_STATUS_TO_EXPECTED_LOCAL_STATUS.get(
            broker_order.status, _UNKNOWN_LOCAL_STATUS
        )
        if local_order.status != expected_status or local_order.broker_status != broker_order.broker_status:
            findings.append(_state_mismatch_finding(local_order, broker_order, expected_status))

    for local_order in local_orders:
        comparisons += 1
        if local_order.client_order_id in matched_client_order_ids:
            continue
        if not _is_local_order_active(local_order):
            continue
        findings.append(_missing_broker_order_finding(local_order))

    return tuple(findings), comparisons


def _match_fills(
    local_fills: list[LocalFillSnapshot],
    broker_fills: list[BrokerFillSnapshot],
    *,
    local_orders: list[LocalOrderSnapshot],
) -> tuple[tuple[Finding, ...], int]:
    """Resolve fills via a single ``broker_fill_id``-keyed local set.

    A broker fill absent from the local fill set is attributed to its owning local
    order (via a single ``broker_order_id``-keyed map) when one exists, for
    ExecutionEvent ``paper_order_id`` linkage.
    """
    local_fill_ids = {fill.broker_fill_id for fill in local_fills}
    local_order_by_broker_id = {
        order.broker_order_id: order for order in local_orders if order.broker_order_id
    }

    findings: list[Finding] = []
    comparisons = 0
    for broker_fill in broker_fills:
        comparisons += 1
        if broker_fill.broker_fill_id in local_fill_ids:
            continue
        owning_order = local_order_by_broker_id.get(broker_fill.broker_order_id)
        findings.append(_missing_local_fill_finding(broker_fill, owning_order))

    return tuple(findings), comparisons


def _is_local_order_active(order: LocalOrderSnapshot) -> bool:
    if order.status == "submission_failed":
        return False
    if order.status == "pending_submission" and order.submission_attempt_count == 0:
        return False
    return order.status in _ACTIVE_LOCAL_ORDER_STATUSES


def _decimal_differs(left: Decimal, right: Decimal, *, tolerance: Decimal) -> bool:
    return abs(left - right) > tolerance


# --- Finding builders -----------------------------------------------------------------


def _missing_local_position_finding(
    identity: ReconciliationIdentity,
    broker_position: BrokerPositionSnapshot,
) -> Finding:
    return Finding(
        category=ReconciliationFinding.MISSING_LOCAL,
        identity=identity,
        severity="error",
        blocks_execution=True,
        message=(
            f"Broker reports a {identity.side.value} {identity.symbol} position that "
            "local storage does not track."
        ),
        details={
            "symbol": identity.symbol,
            "side": identity.side.value,
            "broker_quantity": str(broker_position.quantity),
            "broker_average_entry_price": str(broker_position.average_entry_price),
        },
    )


def _missing_broker_position_finding(
    identity: ReconciliationIdentity,
    local_position: LocalPositionSnapshot,
) -> Finding:
    return Finding(
        category=ReconciliationFinding.MISSING_BROKER,
        identity=identity,
        severity="error",
        blocks_execution=True,
        message=(
            f"Local storage reports a {identity.side.value} {identity.symbol} position "
            "that the broker does not show."
        ),
        details={
            "symbol": identity.symbol,
            "side": identity.side.value,
            "local_quantity": str(local_position.quantity),
            "local_average_entry_price": str(local_position.average_entry_price),
        },
    )


def _quantity_mismatch_finding(
    identity: ReconciliationIdentity,
    local_position: LocalPositionSnapshot,
    broker_position: BrokerPositionSnapshot,
) -> Finding:
    return Finding(
        category=ReconciliationFinding.QUANTITY_MISMATCH,
        identity=identity,
        severity="error",
        blocks_execution=True,
        message=f"Local {identity.symbol} position quantity diverges from the broker position.",
        details={
            "symbol": identity.symbol,
            "side": identity.side.value,
            "local_quantity": str(local_position.quantity),
            "broker_quantity": str(broker_position.quantity),
        },
    )


def _price_mismatch_finding(
    identity: ReconciliationIdentity,
    local_position: LocalPositionSnapshot,
    broker_position: BrokerPositionSnapshot,
) -> Finding:
    return Finding(
        category=ReconciliationFinding.PRICE_MISMATCH,
        identity=identity,
        severity="error",
        blocks_execution=True,
        message=f"Local {identity.symbol} position average entry price diverges from the broker position.",
        details={
            "symbol": identity.symbol,
            "side": identity.side.value,
            "local_average_entry_price": str(local_position.average_entry_price),
            "broker_average_entry_price": str(broker_position.average_entry_price),
        },
    )


def _missing_local_order_finding(broker_order: BrokerOrderSnapshot) -> Finding:
    return Finding(
        category=ReconciliationFinding.MISSING_LOCAL,
        identity=None,
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
        broker_order_id=broker_order.broker_order_id,
    )


def _missing_broker_order_finding(local_order: LocalOrderSnapshot) -> Finding:
    return Finding(
        category=ReconciliationFinding.MISSING_BROKER,
        identity=None,
        severity="error",
        blocks_execution=True,
        message=(
            f"Local order '{local_order.client_order_id}' is still '{local_order.status}' "
            "but the broker no longer reports it."
        ),
        details={
            "paper_order_id": local_order.paper_order_id,
            "client_order_id": local_order.client_order_id,
            "broker_order_id": local_order.broker_order_id,
            "local_status": local_order.status,
            "submission_attempt_count": local_order.submission_attempt_count,
        },
        paper_order_id=local_order.paper_order_id,
        broker_order_id=local_order.broker_order_id,
    )


def _state_mismatch_finding(
    local_order: LocalOrderSnapshot,
    broker_order: BrokerOrderSnapshot,
    expected_status: str,
) -> Finding:
    return Finding(
        category=ReconciliationFinding.STATE_MISMATCH,
        identity=None,
        severity="error",
        blocks_execution=True,
        message=(
            f"Local order '{local_order.client_order_id}' has status '{local_order.status}' "
            f"but the broker reports '{expected_status}' ('{broker_order.broker_status}')."
        ),
        details={
            "paper_order_id": local_order.paper_order_id,
            "client_order_id": local_order.client_order_id,
            "broker_order_id": broker_order.broker_order_id,
            "local_status": local_order.status,
            "local_broker_status": local_order.broker_status,
            "expected_local_status": expected_status,
            "broker_status": broker_order.broker_status,
        },
        paper_order_id=local_order.paper_order_id,
        broker_order_id=broker_order.broker_order_id,
    )


def _missing_local_fill_finding(
    broker_fill: BrokerFillSnapshot,
    owning_order: LocalOrderSnapshot | None,
) -> Finding:
    return Finding(
        category=ReconciliationFinding.MISSING_LOCAL,
        identity=None,
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
        paper_order_id=owning_order.paper_order_id if owning_order is not None else None,
        broker_order_id=broker_fill.broker_order_id,
    )
