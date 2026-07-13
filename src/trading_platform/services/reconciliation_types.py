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
