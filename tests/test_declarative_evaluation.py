"""Interpreter rules the contract pins without an original to compare against (§5, §6, §9 items 8-10)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from tests.support.research_fixtures import make_bars, weekday_sessions

from trading_platform.strategies.signals import SignalDirection, SignalReason
from trading_platform.strategies.spec import validate_spec
from trading_platform.strategies.spec.evaluate import compute_ema, compute_rsi, evaluate, term_value

SESSIONS = weekday_sessions(date(2018, 1, 2), 700)
BARS = make_bars("X", SESSIONS, seed=11, volatility=0.03)


def spec(indicators: dict[str, Any], entry: dict[str, Any], exit_: dict[str, Any]) -> dict[str, Any]:
    return {
        "spec_version": 1,
        "name": "T",
        "timeframe": "daily",
        "direction": "long_only",
        "indicators": indicators,
        "entry": {"all_of": [entry]},
        "exit": {"any_of": [exit_]},
    }


# ---------------------------------------------------------------------------
# §9 item 8: shifted recursion
# ---------------------------------------------------------------------------


def test_shifted_rsi_requires_history_plus_shift_and_equals_unshifted_value_earlier() -> None:
    shifted = validate_spec(spec({"r": {"type": "rsi", "window": 14, "history": 100, "shift": 20}}, {"left": "r", "op": "lt", "right": 30}, {"left": "r", "op": "gt", "right": 70}))
    plain = validate_spec(spec({"r": {"type": "rsi", "window": 14, "history": 100}}, {"left": "r", "op": "lt", "right": 30}, {"left": "r", "op": "gt", "right": 70}))
    assert shifted.history_required == 120
    bars = BARS[:400]
    shifted_value = term_value(shifted.terms["r"], bars)
    earlier_value = term_value(plain.terms["r"], bars[:-20])
    assert shifted_value == earlier_value
    # Direct formula check: the full 100-bar recursion ending 20 bars back.
    closes = [b.close for b in bars[-120:-20]]
    assert shifted_value == compute_rsi(closes, 14)
    # Boundary: 119 bars -> insufficient history.
    result = evaluate(shifted, bars[-119:])
    assert (result.direction, result.reason) == (SignalDirection.FLAT, SignalReason.RULE_INSUFFICIENT_HISTORY)
    assert evaluate(shifted, bars[-120:]).reason != SignalReason.RULE_INSUFFICIENT_HISTORY


def test_shifted_ema_follows_the_same_slicing_rule() -> None:
    compiled = validate_spec(spec({"e": {"type": "ema", "window": 10, "history": 60, "shift": 5}}, {"left": "close", "op": "gt", "right": "e"}, {"left": "close", "op": "lt", "right": "e"}))
    assert compiled.history_required == 65
    bars = BARS[:300]
    value = term_value(compiled.terms["e"], bars)
    assert value == compute_ema([b.close for b in bars[-65:-5]], 10)


# ---------------------------------------------------------------------------
# §9 item 9: crossings use offset-1 values from their own complete windows
# ---------------------------------------------------------------------------


def test_sma_cross_derives_201_and_uses_previous_full_window_values() -> None:
    compiled = validate_spec(spec({"a": {"type": "sma", "window": 50}, "b": {"type": "sma", "window": 200}}, {"left": "a", "op": "crosses_above", "right": "b"}, {"left": "a", "op": "crosses_below", "right": "b"}))
    assert compiled.history_required == 201
    bars = BARS[:450]
    a, b = compiled.terms["a"], compiled.terms["b"]
    assert term_value(a, bars, 1) == term_value(a, bars[:-1], 0)
    assert term_value(b, bars, 1) == term_value(b, bars[:-1], 0)


def test_rsi_cross_offset_one_is_a_fresh_recursion_not_the_previous_iterate() -> None:
    compiled = validate_spec(spec({"r": {"type": "rsi", "window": 14, "history": 100}}, {"left": "r", "op": "crosses_above", "right": 30}, {"left": "r", "op": "crosses_below", "right": 70}))
    assert compiled.history_required == 101
    bars = BARS[:300]
    r = compiled.terms["r"]
    fresh = term_value(r, bars, 1)
    assert fresh == compute_rsi([b.close for b in bars[-101:-1]], 14)
    # The previous iterate of the offset-0 recursion is computed from a window that
    # starts one bar later; on real data it differs from the fresh recursion.
    closes0 = [b.close for b in bars[-100:]]
    changes = [c - p for p, c in zip(closes0, closes0[1:])]
    gain = sum((max(c, Decimal(0)) for c in changes[:14]), Decimal(0)) / Decimal(14)
    loss = sum((max(-c, Decimal(0)) for c in changes[:14]), Decimal(0)) / Decimal(14)
    for change in changes[14:-1]:
        gain = (gain * 13 + max(change, Decimal(0))) / 14
        loss = (loss * 13 + max(-change, Decimal(0))) / 14
    previous_iterate = Decimal(100) - Decimal(100) / (Decimal(1) + gain / loss)
    assert previous_iterate != fresh


def test_cross_semantics_require_both_offsets() -> None:
    compiled = validate_spec(spec({"a": {"type": "sma", "window": 3}, "b": {"type": "sma", "window": 5}}, {"left": "a", "op": "crosses_above", "right": "b"}, {"left": "a", "op": "crosses_below", "right": "b"}))
    assert compiled.history_required == 6
    rising = [Decimal(v) for v in (10, 10, 10, 10, 10, 10, 30)]  # a=16.67>b=14 now; a1=b1=10 before
    bars = [type("B", (), {"open": v, "high": v, "low": v, "close": v, "volume": 1})() for v in rising]
    result = evaluate(compiled, bars)
    assert result.direction == SignalDirection.LONG  # a crossed above b on the last bar
    flat = [Decimal(10)] * 7
    bars = [type("B", (), {"open": v, "high": v, "low": v, "close": v, "volume": 1})() for v in flat]
    assert evaluate(compiled, bars).direction == SignalDirection.FLAT


# ---------------------------------------------------------------------------
# §9 item 10: early-history boundaries for every term type
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("indicators", "entry", "exit_"),
    [
        ({"s": {"type": "sma", "window": 20}}, {"left": "close", "op": "gt", "right": "s"}, {"left": "close", "op": "lt", "right": "s"}),
        ({"e": {"type": "ema", "window": 10, "history": 40}}, {"left": "close", "op": "gt", "right": "e"}, {"left": "close", "op": "lt", "right": "e"}),
        ({"r": {"type": "rsi", "window": 14, "history": 50}}, {"left": "r", "op": "lt", "right": 30}, {"left": "r", "op": "gt", "right": 70}),
        ({"h": {"type": "highest", "source": "high", "window": 20, "shift": 1}}, {"left": "close", "op": "gt", "right": "h"}, {"left": "close", "op": "lt", "right": "h"}),
        ({"l": {"type": "lowest", "source": "low", "window": 20, "shift": 1}}, {"left": "close", "op": "lt", "right": "l"}, {"left": "close", "op": "gt", "right": "l"}),
        ({"g": {"type": "lag", "source": "close", "periods": 30}}, {"left": "close", "op": "gt", "right": "g"}, {"left": "close", "op": "le", "right": "g"}),
        ({"c": {"type": "change_pct", "source": "close", "periods": 10}}, {"left": "c", "op": "gt", "right": 0}, {"left": "c", "op": "lt", "right": 0}),
        ({"v": {"type": "sma", "source": "volume", "window": 5}}, {"left": "volume", "op": "gt", "right": "v"}, {"left": "volume", "op": "lt", "right": "v"}),
    ],
)
def test_boundaries_per_term_type(indicators, entry, exit_) -> None:
    compiled = validate_spec(spec(indicators, entry, exit_))
    n = compiled.history_required
    below = evaluate(compiled, BARS[: n - 1])
    assert (below.direction, below.reason) == (SignalDirection.FLAT, SignalReason.RULE_INSUFFICIENT_HISTORY)
    exact = evaluate(compiled, BARS[:n])
    assert exact.reason != SignalReason.RULE_INSUFFICIENT_HISTORY
    plus = evaluate(compiled, BARS[: n + 1])
    assert plus.reason != SignalReason.RULE_INSUFFICIENT_HISTORY
    # With one extra bar the values come from the newest window only.
    name = next(iter(indicators))
    assert plus.values[name] == term_value(compiled.terms[name], BARS[1 : n + 1])


def test_exit_wins_when_both_rules_are_true() -> None:
    compiled = validate_spec(spec({"s": {"type": "sma", "window": 3}}, {"left": "close", "op": "gt", "right": "s"}, {"left": "close", "op": "gt", "right": "s"}))
    rising = [Decimal(v) for v in (1, 2, 3, 10)]
    bars = [type("B", (), {"open": v, "high": v, "low": v, "close": v, "volume": 1})() for v in rising]
    result = evaluate(compiled, bars)
    assert (result.direction, result.reason) == (SignalDirection.EXIT, SignalReason.RULE_EXIT)


def test_evaluation_is_deterministic() -> None:
    compiled = validate_spec(spec({"r": {"type": "rsi", "window": 14, "history": 100}}, {"left": "r", "op": "lt", "right": 30}, {"left": "r", "op": "gt", "right": 70}))
    first = [evaluate(compiled, BARS[: 100 + k]) for k in range(50)]
    second = [evaluate(compiled, BARS[: 100 + k]) for k in range(50)]
    assert first == second
