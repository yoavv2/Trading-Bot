"""Deterministic material-order identity helpers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal

from trading_platform.services.execution.contracts import OrderSide


@dataclass(frozen=True)
class MaterialOrderIdentity:
    strategy_id: str
    session_date: str
    symbol: str
    side: str
    quantity: str


@dataclass(frozen=True)
class DerivedOrderIdentity:
    material: MaterialOrderIdentity
    intent_hash: str
    client_order_id: str


def build_material_order_identity(
    *,
    strategy_id: str,
    session_date: date,
    symbol: str,
    side: OrderSide | str,
    quantity: Decimal,
) -> MaterialOrderIdentity:
    return MaterialOrderIdentity(
        strategy_id=strategy_id.strip(),
        session_date=session_date.isoformat(),
        symbol=_normalize_symbol(symbol),
        side=_normalize_side(side),
        quantity=_normalize_quantity(quantity),
    )


def build_intent_hash(
    *,
    strategy_id: str,
    session_date: date,
    symbol: str,
    side: OrderSide | str,
    quantity: Decimal,
) -> str:
    material = build_material_order_identity(
        strategy_id=strategy_id,
        session_date=session_date,
        symbol=symbol,
        side=side,
        quantity=quantity,
    )
    serialized = json.dumps(asdict(material), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def build_client_order_id(
    *,
    prefix: str,
    strategy_id: str,
    session_date: date,
    symbol: str,
    side: OrderSide | str,
    quantity: Decimal,
) -> str:
    material = build_material_order_identity(
        strategy_id=strategy_id,
        session_date=session_date,
        symbol=symbol,
        side=side,
        quantity=quantity,
    )
    intent_hash = build_intent_hash(
        strategy_id=strategy_id,
        session_date=session_date,
        symbol=symbol,
        side=side,
        quantity=quantity,
    )
    symbol_fragment = (
        "".join(char for char in material.symbol.lower() if char.isalnum())[:8] or "order"
    )
    prefix_fragment = "".join(char for char in prefix.lower() if char.isalnum())[:12] or "tp"
    return (
        f"{prefix_fragment}-{session_date.strftime('%Y%m%d')}-{symbol_fragment}-{intent_hash[:18]}"
    )


def derive_order_identity(
    *,
    prefix: str,
    strategy_id: str,
    session_date: date,
    symbol: str,
    side: OrderSide | str,
    quantity: Decimal,
) -> DerivedOrderIdentity:
    material = build_material_order_identity(
        strategy_id=strategy_id,
        session_date=session_date,
        symbol=symbol,
        side=side,
        quantity=quantity,
    )
    intent_hash = build_intent_hash(
        strategy_id=strategy_id,
        session_date=session_date,
        symbol=symbol,
        side=side,
        quantity=quantity,
    )
    return DerivedOrderIdentity(
        material=material,
        intent_hash=intent_hash,
        client_order_id=build_client_order_id(
            prefix=prefix,
            strategy_id=strategy_id,
            session_date=session_date,
            symbol=symbol,
            side=side,
            quantity=quantity,
        ),
    )


def _normalize_symbol(symbol: str) -> str:
    cleaned = "".join(char for char in symbol.strip().upper() if char.isalnum())
    if not cleaned:
        raise ValueError("Order identity requires a non-empty symbol.")
    return cleaned


def _normalize_side(side: OrderSide | str) -> str:
    if isinstance(side, OrderSide):
        return side.value
    cleaned = side.strip().lower()
    if not cleaned:
        raise ValueError("Order identity requires a non-empty side.")
    return cleaned


def _normalize_quantity(quantity: Decimal) -> str:
    normalized = quantity.normalize()
    as_text = format(normalized, "f")
    if "." in as_text:
        as_text = as_text.rstrip("0").rstrip(".")
    return as_text or "0"
