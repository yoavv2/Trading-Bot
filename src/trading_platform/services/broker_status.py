"""Closed classification of raw Alpaca order statuses (D-13).

Pure module (no httpx, no ORM) so the attribution classifier can use the same
mapping as the broker adapter without importing the HTTP client. ``services.alpaca``
re-exports every public name for backward compatibility.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class BrokerStatusClass(StrEnum):
    """Closed classification of a raw Alpaca order status (D-13)."""

    WORKING = "working"
    TERMINAL = "terminal"
    TERMINAL_WITH_SUCCESSOR = "terminal_with_successor"
    UNKNOWN = "unknown"


UNMAPPED_BROKER_STATUS: Final = "unmapped_broker_status"

# Single source of truth for the status mapping: the 16 documented Alpaca order
# statuses (Placing Orders, accessed 2026-09-30) plus the legacy ``held``.
# ``done_for_day`` is working (it can resume); ``replaced`` is terminal with a
# successor order (the successor is unrecognized until verified); anything not
# listed here is ``unknown`` with reason ``unmapped_broker_status``.
BROKER_STATUS_CLASSES: Final[dict[str, BrokerStatusClass]] = {
    "new": BrokerStatusClass.WORKING,
    "partially_filled": BrokerStatusClass.WORKING,
    "filled": BrokerStatusClass.TERMINAL,
    "done_for_day": BrokerStatusClass.WORKING,
    "canceled": BrokerStatusClass.TERMINAL,
    "expired": BrokerStatusClass.TERMINAL,
    "replaced": BrokerStatusClass.TERMINAL_WITH_SUCCESSOR,
    "pending_cancel": BrokerStatusClass.WORKING,
    "pending_replace": BrokerStatusClass.WORKING,
    "accepted": BrokerStatusClass.WORKING,
    "pending_new": BrokerStatusClass.WORKING,
    "accepted_for_bidding": BrokerStatusClass.WORKING,
    "stopped": BrokerStatusClass.WORKING,
    "rejected": BrokerStatusClass.TERMINAL,
    "suspended": BrokerStatusClass.WORKING,
    "calculated": BrokerStatusClass.WORKING,
    "held": BrokerStatusClass.WORKING,
}

def classify_broker_status(raw: str | None) -> BrokerStatusClass:
    """Closed class of a raw broker status; any other or missing value is unknown."""

    if not raw:
        return BrokerStatusClass.UNKNOWN
    return BROKER_STATUS_CLASSES.get(raw, BrokerStatusClass.UNKNOWN)


def broker_status_reason(raw: str | None) -> str | None:
    """``unmapped_broker_status`` exactly when the status class is unknown."""

    if classify_broker_status(raw) == BrokerStatusClass.UNKNOWN:
        return UNMAPPED_BROKER_STATUS
    return None
