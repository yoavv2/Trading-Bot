"""Single source of truth for reconciliation comparison tolerances (STRUCT-07).

``MONEY_TOLERANCE`` and ``QUANTITY_TOLERANCE`` are the only sanctioned tolerances for
deciding whether two Decimal money/quantity values reconcile as equal. They were
previously duplicated as private per-file constants in ``services/reconciliation.py`` and
``services/reconciliation_matcher.py``; this module retires that duplication.

Note: these are *comparison* tolerances, distinct from ``MONEY_SCALE`` (a Decimal
quantization scale used in backtest/portfolio reporting) — do not conflate the two.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Final

#: Money values within this absolute Decimal delta reconcile as equal.
MONEY_TOLERANCE: Final[Decimal] = Decimal("0.01")

#: Quantity values within this absolute Decimal delta reconcile as equal.
QUANTITY_TOLERANCE: Final[Decimal] = Decimal("0.000001")
