"""Pure local-position derivation from owned fills (D-08).

Local positions are DERIVED from the platform's own recorded fills (weighted-average
cost, signed quantity); broker positions are only ever compared against them, never
copied. This module is deliberately free of any ORM or broker-client import.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

_SIX_PLACES = Decimal("0.000001")
_ZERO = Decimal("0")


@dataclass(frozen=True)
class DerivationFill:
    """One owned fill, reduced to what position derivation needs."""

    fill_id: str
    side: str
    quantity: Decimal
    price: Decimal
    filled_at: datetime


@dataclass(frozen=True)
class NetPosition:
    """Derived position: signed quantity, average entry price and signed cost basis."""

    quantity: Decimal
    average_entry_price: Decimal
    cost_basis: Decimal

    @property
    def is_flat(self) -> bool:
        return self.quantity == _ZERO


FLAT_POSITION = NetPosition(quantity=_ZERO, average_entry_price=_ZERO, cost_basis=_ZERO)


def _signed_quantity(fill: DerivationFill) -> Decimal:
    return fill.quantity if fill.side == "buy" else -fill.quantity


def derive_net_position(fills: Iterable[DerivationFill]) -> NetPosition:
    """Fold fills chronologically (``filled_at`` then ``fill_id``) into a net position.

    Buys add and sells reduce. A reduction keeps the average entry price; a flip through
    zero resets it to the flipping fill's price; an exact close returns to flat. The
    returned average and cost basis are quantized to the column scale (6 places).
    """

    quantity = _ZERO
    average = _ZERO
    for fill in sorted(fills, key=lambda item: (item.filled_at, item.fill_id)):
        delta = _signed_quantity(fill)
        if delta == _ZERO:
            continue
        if quantity == _ZERO:
            quantity = delta
            average = fill.price
        elif (quantity > _ZERO) == (delta > _ZERO):
            total = abs(quantity) + abs(delta)
            average = (abs(quantity) * average + abs(delta) * fill.price) / total
            quantity += delta
        elif abs(delta) < abs(quantity):
            quantity += delta
        elif abs(delta) == abs(quantity):
            quantity = _ZERO
            average = _ZERO
        else:
            quantity += delta
            average = fill.price

    if quantity == _ZERO:
        return FLAT_POSITION
    average = average.quantize(_SIX_PLACES)
    return NetPosition(
        quantity=quantity,
        average_entry_price=average,
        cost_basis=(quantity * average).quantize(_SIX_PLACES),
    )
