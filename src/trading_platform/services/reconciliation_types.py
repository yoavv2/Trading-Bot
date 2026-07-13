"""Typed contracts for the reconciliation subsystem (RECON-05, RECON-06, RECON-07).

This module is the interface-first foundation the whole reconciliation rewrite is
built against. It intentionally holds ONLY pure value types and derivation helpers:

- ``ReconciliationFinding`` — a closed 5-member enum, so a finding category can never
  be an arbitrary string (RECON-07).
- Typed ``Local*Snapshot`` dataclasses mirroring the already-typed broker snapshots,
  so broker and local entities cross the reconciliation boundary as typed values
  rather than raw ORM objects or ``dict[str, Any]`` (RECON-05).
- ``ReconciliationIdentity`` — a hashable ``(symbol, account, side)`` key usable as a
  dict key so both sides of a position comparison map into one collection (RECON-06).

It deliberately has NO ORM, DB-session, or broker-client imports, so it stays trivially
unit-testable and import-cheap. ``OrderSide`` is imported from ``services.execution``
(the pure module that defines it) rather than ``services.alpaca`` (which pulls in the
HTTP broker client).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from trading_platform.services.execution import OrderSide

if TYPE_CHECKING:
    # Import only for type-checking so this module never pulls the broker client
    # (services.alpaca imports httpx) into its runtime import graph.
    from trading_platform.services.alpaca import BrokerPositionSnapshot


# Single-account paper setup: neither the local ``Position`` ORM row nor the broker's
# ``BrokerPositionSnapshot`` carries an account, so both sides of a reconciliation key on
# this one configured constant. Multi-account is deliberately out of scope for v1.1; when
# it lands, this becomes a real per-entity field rather than a module constant.
DEFAULT_ACCOUNT = "paper"


class PositionSide(enum.Enum):
    """Directional side of a position, derived purely from its signed quantity."""

    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"


def side_from_quantity(quantity: Decimal) -> PositionSide:
    """Derive a :class:`PositionSide` from a signed quantity (>0 LONG, <0 SHORT, 0 FLAT)."""
    if quantity > 0:
        return PositionSide.LONG
    if quantity < 0:
        return PositionSide.SHORT
    return PositionSide.FLAT


@dataclass(frozen=True)
class ReconciliationIdentity:
    """Hashable ``(symbol, account, side)`` key (RECON-06).

    Both the local and broker side of a position map into a single dict under this key,
    so the 09-02 matcher resolves counterparts by lookup instead of a nested scan.
    """

    symbol: str
    account: str
    side: PositionSide


class ReconciliationFinding(enum.Enum):
    """Closed set of reconciliation finding categories (RECON-07).

    Exactly these five members exist. Adding a member intentionally breaks the
    closedness test, and constructing one from an unknown string raises ``ValueError``
    — no string-classified finding can slip into the system.
    """

    MISSING_LOCAL = "MISSING_LOCAL"
    MISSING_BROKER = "MISSING_BROKER"
    QUANTITY_MISMATCH = "QUANTITY_MISMATCH"
    PRICE_MISMATCH = "PRICE_MISMATCH"
    STATE_MISMATCH = "STATE_MISMATCH"


@dataclass(frozen=True)
class Finding:
    """A single reconciliation finding as a typed value object.

    ``category`` is the closed enum, not a string. ``to_event_dict()`` serializes it via
    ``.name`` into exactly the shape ExecutionEvent rows are persisted from in 09-03, so
    the key names (``event_type``, ``severity``, ``blocks_execution``, ``paper_order_id``,
    ``details``) match what analytics/operator_reads read today.
    """

    category: ReconciliationFinding
    identity: ReconciliationIdentity | None
    severity: str
    blocks_execution: bool
    message: str
    details: dict[str, Any]
    paper_order_id: str | None = None
    broker_order_id: str | None = None

    def to_event_dict(self) -> dict[str, Any]:
        """Serialize into the ExecutionEvent persistence shape used by 09-03."""
        return {
            "event_type": self.category.name,
            "severity": self.severity,
            "blocks_execution": self.blocks_execution,
            "message": self.message,
            "paper_order_id": self.paper_order_id,
            "details": self.details,
        }


# --- Typed local snapshots (RECON-05) ------------------------------------------------
#
# These mirror the already-typed broker snapshots in ``services.alpaca`` so both sides of
# the reconciliation boundary are typed values, never raw ORM rows or ``dict[str, Any]``.
# The only ``dict[str, Any]`` fields anywhere are ``Finding.details`` and broker
# ``raw_payload`` passthroughs — never a snapshot's business fields.


@dataclass(frozen=True)
class LocalOrderSnapshot:
    """Typed projection of a ``PaperOrder`` ORM row across the reconciliation boundary."""

    paper_order_id: str
    strategy_run_id: str
    symbol: str
    side: OrderSide
    quantity: Decimal
    client_order_id: str
    broker_order_id: str | None
    status: str
    broker_status: str | None
    submission_attempt_count: int
    sync_failure_count: int


@dataclass(frozen=True)
class LocalFillSnapshot:
    """Typed projection of a ``PaperFill`` ORM row."""

    broker_fill_id: str
    broker_order_id: str
    symbol: str
    side: OrderSide
    quantity: Decimal
    price: Decimal
    filled_at: datetime


@dataclass(frozen=True)
class LocalPositionSnapshot:
    """Typed projection of a ``Position`` ORM row."""

    symbol: str
    quantity: Decimal
    average_entry_price: Decimal
    cost_basis: Decimal
    status: str

    def identity(self) -> ReconciliationIdentity:
        """Position identity keyed on ``(symbol, DEFAULT_ACCOUNT, side)``."""
        return ReconciliationIdentity(
            symbol=self.symbol,
            account=DEFAULT_ACCOUNT,
            side=side_from_quantity(self.quantity),
        )


@dataclass(frozen=True)
class LocalAccountSnapshot:
    """Typed projection of an ``AccountSnapshot`` ORM row."""

    cash: Decimal
    gross_exposure: Decimal
    total_equity: Decimal
    buying_power: Decimal
    open_positions: int


def identity_for_broker_position(pos: BrokerPositionSnapshot) -> ReconciliationIdentity:
    """Broker-position identity keyed on ``(symbol, DEFAULT_ACCOUNT, side)``.

    Mirrors :meth:`LocalPositionSnapshot.identity` so a local and broker position with the
    same symbol and direction resolve to the same map key in the 09-02 matcher.
    """
    return ReconciliationIdentity(
        symbol=pos.symbol,
        account=DEFAULT_ACCOUNT,
        side=side_from_quantity(pos.quantity),
    )
