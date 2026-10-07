"""Pure evaluation of compiled specifications over a bar list (contract §3, §5, §6).

No database access. ``bars`` is the ascending list the access layer returned for the
asset on or before the evaluation date. Every formula below is pinned to the original
Python strategies:

* ``sma``: arithmetic mean of the last ``window`` values (``_compute_sma``);
* ``rsi``: Wilder's smoothing seeded by the simple means of the first ``window`` changes
  of the ``history``-bar list, then ``avg = (avg * (window - 1) + x) / window`` over the
  remaining changes; both zero -> 50, loss zero -> 100, gain zero -> 0 (``_compute_rsi``);
* ``highest`` / ``lowest``: max of ``high`` / min of ``low`` (or the chosen source) over the
  ``window`` bars ending ``shift`` sessions back (``_compute_channels`` with ``shift: 1``);
* ``lag``: the value ``periods`` sessions back (``closes[-(lookback + 1)]``);
* ``ema``: seed = simple mean of the first ``window`` values, then
  ``ema = alpha * x + (1 - alpha) * ema`` with ``alpha = 2 / (window + 1)``;
* ``change_pct``: ``x[t] / x[t - periods] - 1``.

Slicing (contract §5): the value of a term at offset ``o`` comes from
``bars[n - (W + shift + o) : n - (shift + o)]``. A crossing condition evaluates both
operands at offsets 0 and 1 with their own complete windows; the offset-1 value of a
recursive term is a fresh recursion, never the previous iterate of the offset-0 one.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from trading_platform.strategies.signals import SignalDirection, SignalReason
from trading_platform.strategies.spec.validate import CompiledSpec, Leaf, Node, Term


class BarLike(Protocol):
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


def _series(bars: list[Any], source: str) -> list[Decimal]:
    if source == "volume":
        return [Decimal(bar.volume) for bar in bars]
    return [getattr(bar, source) for bar in bars]


def _slice(bars: list[Any], term: Term, offset: int) -> list[Any] | None:
    n = len(bars)
    needed = term.bars_needed(offset)
    if n < needed:
        return None
    end = n - (term.shift + offset)
    start = end - term.window
    return bars[start:end]


def compute_sma(values: list[Decimal], window: int) -> Decimal | None:
    if len(values) < window:
        return None
    window_values = values[-window:]
    return sum(window_values, Decimal(0)) / Decimal(window)


def compute_rsi(values: list[Decimal], window: int) -> Decimal | None:
    """Wilder RSI over the full ``values`` list, exactly as the original strategy."""

    if len(values) < window + 1:
        return None
    changes = [current - previous for previous, current in zip(values, values[1:])]
    initial = changes[:window]
    average_gain = sum((max(change, Decimal(0)) for change in initial), Decimal(0)) / Decimal(window)
    average_loss = sum((max(-change, Decimal(0)) for change in initial), Decimal(0)) / Decimal(window)
    for change in changes[window:]:
        gain = max(change, Decimal(0))
        loss = max(-change, Decimal(0))
        average_gain = ((average_gain * Decimal(window - 1)) + gain) / Decimal(window)
        average_loss = ((average_loss * Decimal(window - 1)) + loss) / Decimal(window)
    if average_gain == 0 and average_loss == 0:
        return Decimal(50)
    if average_loss == 0:
        return Decimal(100)
    if average_gain == 0:
        return Decimal(0)
    relative_strength = average_gain / average_loss
    return Decimal(100) - (Decimal(100) / (Decimal(1) + relative_strength))


def compute_ema(values: list[Decimal], window: int) -> Decimal | None:
    if len(values) < window:
        return None
    alpha = Decimal(2) / Decimal(window + 1)
    ema = sum(values[:window], Decimal(0)) / Decimal(window)
    for value in values[window:]:
        ema = alpha * value + (Decimal(1) - alpha) * ema
    return ema


def term_value(term: Term, bars: list[Any], offset: int = 0) -> Decimal | None:
    """Value of ``term`` at ``offset`` sessions before the last bar (None if undefined)."""

    window_bars = _slice(bars, term, offset)
    if window_bars is None:
        return None
    if term.kind == "series":
        return _series(window_bars, term.source)[-1]
    values = _series(window_bars, term.source)
    if term.kind == "sma":
        return compute_sma(values, term.window)
    if term.kind == "rsi":
        return compute_rsi(values, term.window_param)
    if term.kind == "ema":
        return compute_ema(values, term.window_param)
    if term.kind == "highest":
        return max(values)
    if term.kind == "lowest":
        return min(values)
    if term.kind == "lag":
        return values[0]
    if term.kind == "change_pct":
        if values[0] == 0:
            return None
        return values[-1] / values[0] - Decimal(1)
    raise ValueError(f"unsupported term kind {term.kind!r}")


def _operand(value: Term | Decimal, bars: list[Any], offset: int) -> Decimal | None:
    if isinstance(value, Decimal):
        return value
    return term_value(value, bars, offset)


def evaluate_leaf(leaf: Leaf, bars: list[Any]) -> bool | None:
    """True/False, or None when any operand is undefined."""

    left0 = _operand(leaf.left, bars, 0)
    right0 = _operand(leaf.right, bars, 0)
    if left0 is None or right0 is None:
        return None
    if leaf.op == "gt":
        return left0 > right0
    if leaf.op == "ge":
        return left0 >= right0
    if leaf.op == "lt":
        return left0 < right0
    if leaf.op == "le":
        return left0 <= right0
    left1 = _operand(leaf.left, bars, 1)
    right1 = _operand(leaf.right, bars, 1)
    if left1 is None or right1 is None:
        return None
    if leaf.op == "crosses_above":
        return left0 > right0 and left1 <= right1
    if leaf.op == "crosses_below":
        return left0 < right0 and left1 >= right1
    raise ValueError(f"unsupported operator {leaf.op!r}")


def evaluate_node(node: Node, bars: list[Any]) -> bool | None:
    if isinstance(node, Leaf):
        return evaluate_leaf(node, bars)
    results = [evaluate_node(child, bars) for child in node.children]
    if any(result is None for result in results):
        return None
    if node.combinator == "all_of":
        return all(results)
    return any(results)


@dataclass(frozen=True)
class Evaluation:
    direction: SignalDirection
    reason: SignalReason
    values: dict[str, Decimal | None]
    bars_available: int


def evaluate(compiled: CompiledSpec, bars: list[Any]) -> Evaluation:
    """Stateless evaluation: insufficient history -> FLAT; exit first; then entry; else FLAT."""

    values: dict[str, Decimal | None] = {}
    if len(bars) < compiled.history_required:
        return Evaluation(
            SignalDirection.FLAT, SignalReason.RULE_INSUFFICIENT_HISTORY, values, len(bars)
        )
    for name, term in compiled.terms.items():
        values[name] = term_value(term, bars, 0)
    exit_true = evaluate_node(compiled.exit, bars)
    entry_true = evaluate_node(compiled.entry, bars)
    if exit_true is None or entry_true is None:
        return Evaluation(
            SignalDirection.FLAT, SignalReason.RULE_INSUFFICIENT_HISTORY, values, len(bars)
        )
    if exit_true:
        return Evaluation(SignalDirection.EXIT, SignalReason.RULE_EXIT, values, len(bars))
    if entry_true:
        return Evaluation(SignalDirection.LONG, SignalReason.RULE_ENTRY, values, len(bars))
    return Evaluation(SignalDirection.FLAT, SignalReason.RULE_NO_SIGNAL, values, len(bars))
