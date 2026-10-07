"""Deterministic plain-language explanation of a compiled specification (contract §10).

Same specification, same text: the output is a pure function of the compiled form and
is hash-pinned by tests. This is the explanation a user approves; any assistant prose is
shown separately and labelled as the assistant's.
"""

from __future__ import annotations

from decimal import Decimal

from trading_platform.strategies.spec.validate import (
    SCALE_FREE,
    CompiledSpec,
    Leaf,
    Node,
    Term,
)

_OP_WORDS = {
    "gt": "is above",
    "ge": "is at or above",
    "lt": "is below",
    "le": "is at or below",
    "crosses_above": "crosses above",
    "crosses_below": "crosses below",
}

_NOT_DONE = (
    "short selling",
    "intraday or weekly bars",
    "stop or target prices inside the bar",
    "trailing stops",
    "position sizing",
    "conditions on other assets",
    "pyramiding",
)


def describe_term(term: Term) -> str:
    shift = f", {term.shift} session(s) earlier" if term.shift else ""
    if term.kind == "series":
        return f"the {term.source}{shift}"
    if term.kind in ("sma", "highest", "lowest"):
        words = {"sma": "simple moving average", "highest": "highest", "lowest": "lowest"}[term.kind]
        return f"{term.name} (the {term.window}-session {words} of the {term.source}{shift})"
    # Recursive terms: ``window_param`` is the smoothing window the user wrote; ``window``
    # is the history fed to the recursion (contract §3), which also shapes the value.
    if term.kind == "ema":
        return (
            f"{term.name} (the {term.window_param}-session exponential moving average of the "
            f"{term.source}, computed over {term.window} bars of history{shift})"
        )
    if term.kind == "rsi":
        return (
            f"{term.name} (the {term.window_param}-session RSI of the {term.source}, "
            f"computed over {term.window} bars of history{shift})"
        )
    if term.kind == "lag":
        return f"{term.name} (the {term.source} {term.window - 1} session(s) before{shift})"
    if term.kind == "change_pct":
        return f"{term.name} (the percent change of the {term.source} over {term.window - 1} session(s){shift})"
    return term.name


def _describe_leaf(leaf: Leaf) -> str:
    right = (
        format(leaf.right.normalize(), "f") if isinstance(leaf.right, Decimal) else describe_term(leaf.right)
    )
    return f"{describe_term(leaf.left)} {_OP_WORDS[leaf.op]} {right}"


def _lines(node: Node, indent: int, counter: list[int], out: list[str]) -> None:
    pad = "  " * indent
    if isinstance(node, Leaf):
        counter[0] += 1
        out.append(f"{pad}{counter[0]}. {_describe_leaf(node)}")
        return
    word = "all of" if node.combinator == "all_of" else "any of"
    out.append(f"{pad}{word}:")
    for child in node.children:
        _lines(child, indent + 1, counter, out)


def explain(compiled: CompiledSpec) -> str:
    spec = compiled.spec
    out: list[str] = [f"Strategy: {spec.name}"]
    if spec.description:
        out.append(f"Description: {spec.description}")
    out.append("Enter long when:")
    _lines(compiled.entry, 1, [0], out)
    out.append("Exit when:")
    _lines(compiled.exit, 1, [0], out)
    out.append(
        "Evaluation: on each session close the exit rule is checked first; otherwise the entry "
        "rule; otherwise no signal. Signals fill at the next session's open."
    )
    out.append(
        f"History required: {compiled.history_required} session(s) "
        f"(mathematical minimum {compiled.history_minimum})."
    )
    scale = "free of absolute price levels" if compiled.scale_class == SCALE_FREE else "depends on absolute price levels"
    out.append(f"Price scale: {scale}.")
    out.append("This strategy does not: " + ", ".join(_NOT_DONE) + ".")
    return "\n".join(out)
