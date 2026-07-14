"""Closed-enum reconciliation finding types (RECON-07).

This module holds the closed finding vocabulary the reconciliation subsystem is built
against:

- ``ReconciliationFinding`` — a closed 5-member enum, so a finding category can never
  be an arbitrary string (RECON-07).
- ``Finding`` — a typed value object whose ``category`` is that closed enum, not a
  string, with a ``to_event_dict()`` serializer into the ExecutionEvent persistence
  shape used by 09-03.

It deliberately has NO ORM, DB-session, or broker-client imports, so it stays trivially
unit-testable and import-cheap.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any

from trading_platform.services.reconciliation.snapshot import ReconciliationIdentity


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
