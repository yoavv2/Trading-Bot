"""Pure tests for ``derive_net_position`` (D-08: positions derive from owned fills)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trading_platform.services.execution.positions import DerivationFill, derive_net_position

T0 = datetime(2024, 1, 5, 14, 30, tzinfo=UTC)


def _fill(
    side: str, quantity: str, price: str, *, minutes: int = 0, fill_id: str = "f"
) -> DerivationFill:
    return DerivationFill(
        fill_id=fill_id,
        side=side,
        quantity=Decimal(quantity),
        price=Decimal(price),
        filled_at=T0 + timedelta(minutes=minutes),
    )


def test_empty_is_flat() -> None:
    position = derive_net_position([])
    assert position.quantity == Decimal("0")
    assert position.average_entry_price == Decimal("0")
    assert position.cost_basis == Decimal("0")
    assert position.is_flat


def test_single_buy() -> None:
    position = derive_net_position([_fill("buy", "10", "100")])
    assert position.quantity == Decimal("10")
    assert position.average_entry_price == Decimal("100")
    assert position.cost_basis == Decimal("1000")
    assert not position.is_flat


def test_two_buys_use_weighted_average_cost() -> None:
    position = derive_net_position(
        [_fill("buy", "10", "100", minutes=0, fill_id="a"), _fill("buy", "30", "104", minutes=1, fill_id="b")]
    )
    assert position.quantity == Decimal("40")
    assert position.average_entry_price == Decimal("103")
    assert position.cost_basis == Decimal("4120")


def test_partial_sell_keeps_the_average() -> None:
    position = derive_net_position(
        [_fill("buy", "10", "100", minutes=0), _fill("sell", "4", "120", minutes=1)]
    )
    assert position.quantity == Decimal("6")
    assert position.average_entry_price == Decimal("100")
    assert position.cost_basis == Decimal("600")


def test_full_sell_closes_the_position() -> None:
    position = derive_net_position(
        [_fill("buy", "10", "100", minutes=0), _fill("sell", "10", "120", minutes=1)]
    )
    assert position.is_flat
    assert position.quantity == Decimal("0")
    assert position.average_entry_price == Decimal("0")
    assert position.cost_basis == Decimal("0")


def test_flip_resets_average_to_the_flipping_fill_price() -> None:
    position = derive_net_position(
        [_fill("buy", "10", "100", minutes=0), _fill("sell", "15", "120", minutes=1)]
    )
    assert position.quantity == Decimal("-5")
    assert position.average_entry_price == Decimal("120")
    assert position.cost_basis == Decimal("-600")


def test_short_then_cover_partially() -> None:
    position = derive_net_position(
        [_fill("sell", "10", "50", minutes=0), _fill("buy", "4", "40", minutes=1)]
    )
    assert position.quantity == Decimal("-6")
    assert position.average_entry_price == Decimal("50")


def test_chronological_order_then_id_tie_break() -> None:
    # supplied out of order: the sell happens first (short), then the larger buy flips it
    out_of_order = [
        _fill("buy", "15", "100", minutes=1, fill_id="b"),
        _fill("sell", "10", "90", minutes=0, fill_id="a"),
    ]
    position = derive_net_position(out_of_order)
    assert position.quantity == Decimal("5")
    assert position.average_entry_price == Decimal("100")

    # same timestamp: id decides the order
    tied = [
        _fill("sell", "10", "90", minutes=0, fill_id="b"),
        _fill("buy", "10", "100", minutes=0, fill_id="a"),
    ]
    position = derive_net_position(tied)
    assert position.is_flat


def test_average_is_quantized_to_six_places() -> None:
    position = derive_net_position(
        [_fill("buy", "1", "100", minutes=0), _fill("buy", "2", "100.01", minutes=1)]
    )
    assert position.average_entry_price == Decimal("100.006667")
    assert position.quantity == Decimal("3")
