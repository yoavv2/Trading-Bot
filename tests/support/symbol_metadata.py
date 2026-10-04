"""Fixture helper for symbols that are ready for trading (D-29, 20.1-04).

Risk rejects a candidate whose symbol lacks required metadata with
``symbol_not_ready``; fixtures that model tradeable symbols must therefore seed
the metadata through this helper rather than ``Symbol(ticker=..., active=True)``.
"""

from __future__ import annotations

from typing import Any


def ready_symbol_fields() -> dict[str, Any]:
    """Symbol columns that satisfy every readiness requirement."""

    return {
        "market": "stocks",
        "symbol_type": "CS",
        "primary_exchange": "XNAS",
        "metadata_provider": "polygon",
        "active": True,
    }
