"""Semantic fidelity of the provider-shape converter (assistant contract s5-v2).

The union-free provider shape (``combine`` / ``conditions`` / ``subgroups``, integers ``0``
for unused fields, ``right`` as text) is an adapter onto the canonical specification. For
each supported feature the converted output must compile to the *same* canonical
specification as a hand-written one (equal ``spec_sha256``) and the interpreter must
answer identically on deterministic synthetic bars; malformed output must yield findings
and never lose a condition silently. Approval stays a separate action of the user, so
nothing here touches drafts or versions.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import pytest

from trading_platform.services.research.assistant import (
    OUTPUT_SCHEMA,
    evaluate_output,
    render_spec_yaml,
)
from trading_platform.strategies.signals import SignalDirection
from trading_platform.strategies.spec import validate_spec
from trading_platform.strategies.spec.evaluate import evaluate
from trading_platform.strategies.spec.validate import validate_yaml

# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def ind(
    name: str,
    type_: str,
    *,
    source: str = "close",
    window: int = 0,
    history: int = 0,
    periods: int = 0,
    shift: int = 0,
) -> dict[str, Any]:
    """One provider-shape indicator (every integer present; 0 = not applicable)."""

    return {
        "name": name,
        "type": type_,
        "source": source,
        "window": window,
        "history": history,
        "periods": periods,
        "shift": shift,
    }


def cond(left: str, op: str, right: str) -> dict[str, str]:
    return {"left": left, "op": op, "right": right}


def group(
    combine: str, *conditions: dict[str, str], subgroups: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    return {"combine": combine, "conditions": list(conditions), "subgroups": subgroups or []}


def sub(combine: str, *conditions: dict[str, str]) -> dict[str, Any]:
    return {"combine": combine, "conditions": list(conditions)}


def provider_output(
    indicators: list[dict[str, Any]],
    entry: dict[str, Any],
    exit_: dict[str, Any],
    *,
    name: str = "T",
    description: str = "",
) -> str:
    return json.dumps(
        {
            "specification": {
                "name": name,
                "description": description,
                "indicators": indicators,
                "entry": entry,
                "exit": exit_,
            },
            "unsupported_requests": [],
            "note": "",
        }
    )


def canonical(
    indicators: dict[str, Any],
    entry: dict[str, Any],
    exit_: dict[str, Any],
    *,
    name: str = "T",
    description: str = "",
) -> dict[str, Any]:
    """The hand-written canonical mapping the converter must reproduce."""

    return {
        "spec_version": 1,
        "name": name,
        "description": description,
        "timeframe": "daily",
        "direction": "long_only",
        "indicators": indicators,
        "entry": entry,
        "exit": exit_,
    }


class Bar:
    def __init__(
        self,
        close: Decimal | int | str,
        *,
        high: Decimal | int | str | None = None,
        low: Decimal | int | str | None = None,
        volume: int = 1000,
    ) -> None:
        self.close = Decimal(str(close))
        self.open = self.close
        self.high = Decimal(str(high)) if high is not None else self.close
        self.low = Decimal(str(low)) if low is not None else self.close
        self.volume = volume


def bars(closes: list[Any]) -> list[Bar]:
    return [Bar(c) for c in closes]


def convert_and_compare(
    indicators: list[dict[str, Any]],
    entry: dict[str, Any],
    exit_: dict[str, Any],
    expected: dict[str, Any],
):
    """Convert the provider output, assert it equals the hand-written canonical spec, and
    return the compiled specification (the converter's)."""

    _parsed, yaml_text, outcome = evaluate_output(provider_output(indicators, entry, exit_))
    assert outcome.valid, outcome.errors
    assert outcome.compiled is not None
    reference = validate_spec(expected)
    assert outcome.compiled.spec_sha256 == reference.spec_sha256, (
        "converted specification differs from the hand-written one:\n" + (yaml_text or "")
    )
    assert outcome.compiled.canonical_json == reference.canonical_json
    # The rendered YAML round-trips through the YAML path of the editor to the same hash.
    assert validate_yaml(yaml_text or "").spec_sha256 == reference.spec_sha256
    return outcome.compiled, reference


def assert_same_signals(compiled, reference, series: list[Any]) -> list[SignalDirection]:
    """Interpreter answers on every prefix of the series must match."""

    out: list[SignalDirection] = []
    for n in range(1, len(series) + 1):
        window = bars(series[:n])
        a = evaluate(compiled, window)
        b = evaluate(reference, window)
        assert (a.direction, a.reason) == (b.direction, b.reason), n
        out.append(a.direction)
    return out


# ---------------------------------------------------------------------------
# Nested combinations within the supported depth
# ---------------------------------------------------------------------------


def test_all_of_with_any_of_subgroup_matches_hand_written_rule_and_signals() -> None:
    indicators = [ind("fast", "sma", window=2), ind("slow", "sma", window=3)]
    entry = group(
        "all_of",
        cond("close", "gt", "slow"),
        subgroups=[sub("any_of", cond("fast", "gt", "slow"), cond("close", "gt", "100"))],
    )
    exit_ = group("any_of", cond("close", "lt", "fast"))
    expected = canonical(
        {
            "fast": {"type": "sma", "source": "close", "window": 2},
            "slow": {"type": "sma", "source": "close", "window": 3},
        },
        {
            "all_of": [
                {"left": "close", "op": "gt", "right": "slow"},
                {
                    "any_of": [
                        {"left": "fast", "op": "gt", "right": "slow"},
                        {"left": "close", "op": "gt", "right": 100},
                    ]
                },
            ]
        },
        {"any_of": [{"left": "close", "op": "lt", "right": "fast"}]},
    )
    compiled, reference = convert_and_compare(indicators, entry, exit_, expected)
    # closes: flat 10, then a rise (entry: close>slow and fast>slow), then a drop (exit: close<fast)
    directions = assert_same_signals(compiled, reference, [10, 10, 10, 12, 14, 9, 9])
    assert directions[4] == SignalDirection.LONG and directions[5] == SignalDirection.EXIT
    # the any_of alternative alone (close > 100) must also open the trade
    _c, _r = compiled, reference
    assert evaluate(compiled, bars([10, 10, 10, 10, 101])).direction == SignalDirection.LONG
    assert evaluate(reference, bars([10, 10, 10, 10, 101])).direction == SignalDirection.LONG


def test_any_of_with_all_of_subgroup_and_two_subgroups() -> None:
    indicators = [ind("a", "sma", window=2), ind("b", "sma", window=4)]
    entry = group(
        "any_of",
        cond("close", "gt", "1000"),
        subgroups=[
            sub("all_of", cond("a", "gt", "b"), cond("close", "gt", "a")),
            sub("all_of", cond("close", "ge", "20")),
        ],
    )
    exit_ = group("all_of", cond("a", "lt", "b"))
    expected = canonical(
        {
            "a": {"type": "sma", "source": "close", "window": 2},
            "b": {"type": "sma", "source": "close", "window": 4},
        },
        {
            "any_of": [
                {"left": "close", "op": "gt", "right": 1000},
                {
                    "all_of": [
                        {"left": "a", "op": "gt", "right": "b"},
                        {"left": "close", "op": "gt", "right": "a"},
                    ]
                },
                {"all_of": [{"left": "close", "op": "ge", "right": 20}]},
            ]
        },
        {"all_of": [{"left": "a", "op": "lt", "right": "b"}]},
    )
    compiled, reference = convert_and_compare(indicators, entry, exit_, expected)
    directions = assert_same_signals(compiled, reference, [10, 10, 10, 10, 11, 13, 8, 7])
    assert directions[5] == SignalDirection.LONG and directions[-1] == SignalDirection.EXIT
    # each any_of branch opens on its own
    assert evaluate(compiled, bars([10, 10, 10, 10, 2000])).direction == SignalDirection.LONG
    assert evaluate(compiled, bars([10, 10, 10, 10, 25])).direction == SignalDirection.LONG


def test_subgroup_order_and_count_are_preserved() -> None:
    indicators = [ind("s", "sma", window=2)]
    entry = group(
        "all_of",
        cond("close", "gt", "s"),
        subgroups=[
            sub("any_of", cond("close", "gt", "5")),
            sub("any_of", cond("close", "gt", "6")),
            sub("all_of", cond("close", "gt", "7")),
        ],
    )
    exit_ = group("any_of", cond("close", "lt", "s"))
    _parsed, yaml_text, outcome = evaluate_output(provider_output(indicators, entry, exit_))
    assert outcome.valid, outcome.errors
    assert outcome.compiled is not None
    assert len(outcome.compiled.entry.children) == 4  # one leaf + three subgroups, nothing dropped
    assert [getattr(child, "combinator", "leaf") for child in outcome.compiled.entry.children] == [
        "leaf",
        "any_of",
        "any_of",
        "all_of",
    ]
    assert (yaml_text or "").count("- any_of:") == 2 and (yaml_text or "").count("- all_of:") == 1


# ---------------------------------------------------------------------------
# Cross operators
# ---------------------------------------------------------------------------


def test_crosses_above_and_below_keep_their_two_bar_semantics() -> None:
    indicators = [ind("a", "sma", window=3), ind("b", "sma", window=5)]
    entry = group("all_of", cond("a", "crosses_above", "b"))
    exit_ = group("all_of", cond("a", "crosses_below", "b"))
    expected = canonical(
        {
            "a": {"type": "sma", "source": "close", "window": 3},
            "b": {"type": "sma", "source": "close", "window": 5},
        },
        {"all_of": [{"left": "a", "op": "crosses_above", "right": "b"}]},
        {"all_of": [{"left": "a", "op": "crosses_below", "right": "b"}]},
    )
    compiled, reference = convert_and_compare(indicators, entry, exit_, expected)
    assert compiled.history_required == 6 and compiled.operators_used == (
        "crosses_above",
        "crosses_below",
    )
    rising = [10, 10, 10, 10, 10, 10, 30]  # a jumps above b on the last bar only
    assert evaluate(compiled, bars(rising)).direction == SignalDirection.LONG
    assert_same_signals(compiled, reference, rising)
    already_above = [10, 10, 10, 10, 10, 30, 30]  # a was above b on the previous bar too: no cross
    assert evaluate(compiled, bars(already_above)).direction == SignalDirection.FLAT
    falling = [30, 30, 30, 30, 30, 30, 1]  # a drops below b on the last bar: exit
    assert evaluate(compiled, bars(falling)).direction == SignalDirection.EXIT
    assert_same_signals(compiled, reference, falling)


def test_cross_against_a_constant_passes_through_as_a_constant() -> None:
    """The converter never rewrites operands: a cross against a numeric constant reaches the
    validator exactly as requested (the validator accepts a constant level) and the
    interpreter compares the two bars against that level."""

    indicators = [ind("a", "sma", window=3)]
    expected = canonical(
        {"a": {"type": "sma", "source": "close", "window": 3}},
        {"all_of": [{"left": "a", "op": "crosses_above", "right": 10}]},
        {"all_of": [{"left": "a", "op": "crosses_below", "right": 10}]},
    )
    compiled, reference = convert_and_compare(
        indicators,
        group("all_of", cond("a", "crosses_above", "10")),
        group("all_of", cond("a", "crosses_below", "10")),
        expected,
    )
    assert compiled.leaves[0].right == Decimal("10")
    directions = assert_same_signals(compiled, reference, [9, 9, 9, 9, 15, 15, 15, 1, 1])
    assert SignalDirection.LONG in directions and directions[-1] == SignalDirection.EXIT


# ---------------------------------------------------------------------------
# References versus constants
# ---------------------------------------------------------------------------


def test_references_and_series_stay_references_and_numeric_text_becomes_a_constant() -> None:
    indicators = [ind("s", "sma", window=2), ind("r", "rsi", window=2, history=10)]
    entry = group(
        "all_of", cond("close", "gt", "s"), cond("s", "gt", "open"), cond("r", "lt", "70")
    )
    exit_ = group("any_of", cond("close", "lt", "low"), cond("r", "gt", "70"))
    expected = canonical(
        {
            "s": {"type": "sma", "source": "close", "window": 2},
            "r": {"type": "rsi", "source": "close", "window": 2, "history": 10},
        },
        {
            "all_of": [
                {"left": "close", "op": "gt", "right": "s"},
                {"left": "s", "op": "gt", "right": "open"},
                {"left": "r", "op": "lt", "right": 70},
            ]
        },
        {
            "any_of": [
                {"left": "close", "op": "lt", "right": "low"},
                {"left": "r", "op": "gt", "right": 70},
            ]
        },
    )
    compiled, _reference = convert_and_compare(indicators, entry, exit_, expected)
    leaves = {(leaf.left.name, leaf.op): leaf.right for leaf in compiled.leaves}
    assert leaves[("close", "gt")].name == "s"  # reference stayed a Term
    assert leaves[("s", "gt")].name == "open"  # series reference stayed a Term
    assert leaves[("r", "lt")] == Decimal("70")  # numeric text became a constant
    assert leaves[("close", "lt")].name == "low"


def test_a_reference_that_looks_numeric_is_not_invented() -> None:
    """A name such as "s1" is a reference, never a number; an unknown name is a finding."""

    indicators = [ind("s1", "sma", window=2)]
    _parsed, _yaml, outcome = evaluate_output(
        provider_output(
            indicators,
            group("all_of", cond("close", "gt", "s1")),
            group("all_of", cond("close", "lt", "s2")),
        )
    )
    assert not outcome.valid
    assert [e["code"] for e in outcome.errors] == ["undefined_reference"] and outcome.errors[0][
        "path"
    ].startswith("exit")


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("0", Decimal("0")),
        ("-0.02", Decimal("-0.02")),
        ("0.05", Decimal("0.05")),
        ("30.0", Decimal("30")),
        (" 1e-2 ", Decimal("0.01")),
        ("+3", Decimal("3")),
    ],
)
def test_zero_negative_decimal_and_exponent_constants(text: str, value: Decimal) -> None:
    indicators = [ind("c", "change_pct", periods=1)]
    entry = group("all_of", cond("c", "gt", text))
    exit_ = group("all_of", cond("c", "lt", text))
    expected = canonical(
        {"c": {"type": "change_pct", "source": "close", "periods": 1}},
        {"all_of": [{"left": "c", "op": "gt", "right": value}]},
        {"all_of": [{"left": "c", "op": "lt", "right": value}]},
    )
    compiled, reference = convert_and_compare(indicators, entry, exit_, expected)
    assert compiled.leaves[0].right == value
    # change_pct over one bar: 100 -> 103 = +0.03, 100 -> 97 = -0.03
    directions = assert_same_signals(compiled, reference, [100, 103, 97])

    def expect(move: Decimal) -> SignalDirection:  # exit is evaluated first, then entry
        if move < value:
            return SignalDirection.EXIT
        return SignalDirection.LONG if move > value else SignalDirection.FLAT

    assert directions[1] == expect(Decimal("0.03")) and directions[2] == expect(Decimal("-0.03"))


def test_non_numeric_constant_text_is_an_undefined_reference_finding() -> None:
    indicators = [ind("c", "change_pct", periods=1)]
    _parsed, _yaml, outcome = evaluate_output(
        provider_output(
            indicators,
            group("all_of", cond("c", "gt", "five percent")),
            group("all_of", cond("c", "lt", "0")),
        )
    )
    assert not outcome.valid and outcome.errors[0]["code"] == "undefined_reference"


# ---------------------------------------------------------------------------
# Recursive history and shifts
# ---------------------------------------------------------------------------


def test_rsi_and_ema_history_are_carried_and_drive_history_required() -> None:
    indicators = [ind("r", "rsi", window=14, history=100), ind("e", "ema", window=10, history=40)]
    entry = group("all_of", cond("r", "lt", "30"), cond("close", "gt", "e"))
    exit_ = group("any_of", cond("r", "gt", "70"))
    expected = canonical(
        {
            "r": {"type": "rsi", "source": "close", "window": 14, "history": 100},
            "e": {"type": "ema", "source": "close", "window": 10, "history": 40},
        },
        {
            "all_of": [
                {"left": "r", "op": "lt", "right": 30},
                {"left": "close", "op": "gt", "right": "e"},
            ]
        },
        {"any_of": [{"left": "r", "op": "gt", "right": 70}]},
    )
    compiled, _reference = convert_and_compare(indicators, entry, exit_, expected)
    assert compiled.history_required == 100 and compiled.history_minimum == 15
    assert (
        compiled.terms["r"].window == 100 and compiled.terms["e"].window == 40
    )  # bars fed to the recursion


def test_missing_history_on_a_recursive_indicator_is_a_finding_not_a_default() -> None:
    indicators = [ind("r", "rsi", window=14)]  # history 0 = unset
    _parsed, _yaml, outcome = evaluate_output(
        provider_output(
            indicators,
            group("all_of", cond("r", "lt", "30")),
            group("all_of", cond("r", "gt", "70")),
        )
    )
    assert not outcome.valid
    assert {(e["code"], e["path"]) for e in outcome.errors} >= {
        ("missing_field", "indicators.r.history")
    }


def test_shifted_channel_and_lag_keep_their_offsets() -> None:
    indicators = [
        ind("hi", "highest", source="high", window=3, shift=1),
        ind("lo", "lowest", source="low", window=2, shift=1),
        ind("ref", "lag", periods=2),
    ]
    entry = group("all_of", cond("close", "gt", "hi"), cond("close", "gt", "ref"))
    exit_ = group("any_of", cond("close", "lt", "lo"))
    expected = canonical(
        {
            "hi": {"type": "highest", "source": "high", "window": 3, "shift": 1},
            "lo": {"type": "lowest", "source": "low", "window": 2, "shift": 1},
            "ref": {"type": "lag", "source": "close", "periods": 2},
        },
        {
            "all_of": [
                {"left": "close", "op": "gt", "right": "hi"},
                {"left": "close", "op": "gt", "right": "ref"},
            ]
        },
        {"any_of": [{"left": "close", "op": "lt", "right": "lo"}]},
    )
    compiled, reference = convert_and_compare(indicators, entry, exit_, expected)
    assert (
        compiled.terms["hi"].shift == 1
        and compiled.terms["lo"].shift == 1
        and compiled.history_required == 4
    )
    # breakout above the preceding 3-bar high (shift 1 excludes the current bar) and above the close two bars ago
    series = [10, 10, 10, 10, 11, 12, 5]
    directions = assert_same_signals(compiled, reference, series)
    assert directions[4] == SignalDirection.LONG and directions[-1] == SignalDirection.EXIT
    # without the shift the current bar is part of the channel and can never exceed it
    unshifted = validate_spec(
        canonical(
            {**expected["indicators"], "hi": {"type": "highest", "source": "high", "window": 3}},
            expected["entry"],
            expected["exit"],
        )
    )
    assert evaluate(unshifted, bars(series[:5])).direction == SignalDirection.FLAT


def test_history_on_a_window_indicator_or_window_on_lag_is_a_finding() -> None:
    _parsed, _yaml, outcome = evaluate_output(
        provider_output(
            [ind("s", "sma", window=5, history=20)],
            group("all_of", cond("close", "gt", "s")),
            group("all_of", cond("close", "lt", "s")),
        )
    )
    assert not outcome.valid and ("unknown_field", "indicators.s.history") in {
        (e["code"], e["path"]) for e in outcome.errors
    }
    _parsed, _yaml, outcome = evaluate_output(
        provider_output(
            [ind("g", "lag", window=3, periods=2)],
            group("all_of", cond("close", "gt", "g")),
            group("all_of", cond("close", "lt", "g")),
        )
    )
    assert not outcome.valid and ("unknown_field", "indicators.g.window") in {
        (e["code"], e["path"]) for e in outcome.errors
    }


# ---------------------------------------------------------------------------
# Entry and exit stay distinct; exit evaluated first
# ---------------------------------------------------------------------------


def test_entry_and_exit_are_not_swapped_or_merged() -> None:
    indicators = [ind("s", "sma", window=2)]
    entry = group("all_of", cond("close", "gt", "s"))
    exit_ = group(
        "any_of", cond("close", "lt", "s"), subgroups=[sub("all_of", cond("close", "gt", "1000"))]
    )
    expected = canonical(
        {"s": {"type": "sma", "source": "close", "window": 2}},
        {"all_of": [{"left": "close", "op": "gt", "right": "s"}]},
        {
            "any_of": [
                {"left": "close", "op": "lt", "right": "s"},
                {"all_of": [{"left": "close", "op": "gt", "right": 1000}]},
            ]
        },
    )
    compiled, reference = convert_and_compare(indicators, entry, exit_, expected)
    assert compiled.entry.combinator == "all_of" and compiled.exit.combinator == "any_of"
    assert len(compiled.entry.children) == 1 and len(compiled.exit.children) == 2
    assert_same_signals(compiled, reference, [10, 12, 9, 5000])
    # exit wins when both rules are true on the same close (close > s and close > 1000)
    assert evaluate(compiled, bars([10, 10, 5000])).direction == SignalDirection.EXIT


def test_swapping_entry_and_exit_changes_the_hash_and_the_signals() -> None:
    indicators = [ind("s", "sma", window=2)]
    a = evaluate_output(
        provider_output(
            indicators,
            group("all_of", cond("close", "gt", "s")),
            group("all_of", cond("close", "lt", "s")),
        )
    )[2].compiled
    b = evaluate_output(
        provider_output(
            indicators,
            group("all_of", cond("close", "lt", "s")),
            group("all_of", cond("close", "gt", "s")),
        )
    )[2].compiled
    assert a is not None and b is not None and a.spec_sha256 != b.spec_sha256
    assert evaluate(a, bars([10, 12])).direction == SignalDirection.LONG
    assert evaluate(b, bars([10, 12])).direction == SignalDirection.EXIT


# ---------------------------------------------------------------------------
# Malformed output: findings, never silent loss
# ---------------------------------------------------------------------------


def test_bad_combine_keeps_every_condition_and_reports_it() -> None:
    entry = {
        "combine": "either",
        "conditions": [cond("close", "gt", "s"), cond("close", "gt", "5")],
        "subgroups": [],
    }
    _parsed, yaml_text, outcome = evaluate_output(
        provider_output(
            [ind("s", "sma", window=2)], entry, group("all_of", cond("close", "lt", "s"))
        )
    )
    assert not outcome.valid
    assert ("invalid_type", "entry.combine") in {(e["code"], e["path"]) for e in outcome.errors}
    assert (yaml_text or "").count(
        "- {left: close"
    ) == 3  # both entry conditions and the exit are still there


def test_non_object_conditions_and_subgroups_are_findings_and_the_rest_is_kept() -> None:
    entry = {
        "combine": "all_of",
        "conditions": [cond("close", "gt", "s"), "close above s", 7],
        "subgroups": ["nope", {"combine": "any_of", "conditions": [cond("close", "gt", "9")]}],
    }
    _parsed, yaml_text, outcome = evaluate_output(
        provider_output(
            [ind("s", "sma", window=2)], entry, group("all_of", cond("close", "lt", "s"))
        )
    )
    assert not outcome.valid
    paths = {(e["code"], e["path"]) for e in outcome.errors}
    assert {
        ("invalid_type", "entry.conditions[1]"),
        ("invalid_type", "entry.conditions[2]"),
        ("invalid_type", "entry.subgroups[0]"),
    } <= paths
    assert "- {left: close, op: gt, right: s}" in (
        yaml_text or ""
    ) and "- {left: close, op: gt, right: 9}" in (yaml_text or "")


def test_missing_conditions_empty_group_and_missing_fields_are_findings() -> None:
    _parsed, _yaml, outcome = evaluate_output(
        provider_output(
            [ind("s", "sma", window=2)],
            {"combine": "all_of", "subgroups": []},
            group("all_of", cond("close", "lt", "s")),
        )
    )
    assert ("missing_field", "entry.conditions") in {(e["code"], e["path"]) for e in outcome.errors}
    _parsed, _yaml, outcome = evaluate_output(
        provider_output(
            [ind("s", "sma", window=2)], group("all_of"), group("all_of", cond("close", "lt", "s"))
        )
    )
    assert not outcome.valid and any(
        e["path"].startswith("entry") and e["code"] == "invalid_type" for e in outcome.errors
    )
    _parsed, _yaml, outcome = evaluate_output(
        provider_output(
            [ind("s", "sma", window=2)],
            group("all_of", {"left": "close", "op": "gt"}),
            group("all_of", cond("close", "lt", "s")),
        )
    )
    assert not outcome.valid and any(
        e["code"] == "missing_field" and e["path"].startswith("entry") for e in outcome.errors
    )
    _parsed, _yaml, outcome = evaluate_output(
        provider_output(
            [ind("s", "sma", window=2)],
            group("all_of", {"left": "close", "op": "gt", "right": "s", "weight": 2}),
            group("all_of", cond("close", "lt", "s")),
        )
    )
    assert not outcome.valid and any(
        e["code"] == "unknown_field" and e["path"].startswith("entry") for e in outcome.errors
    )


def test_unsupported_operator_indicator_and_depth_are_findings() -> None:
    _parsed, _yaml, outcome = evaluate_output(
        provider_output(
            [ind("s", "sma", window=2)],
            group("all_of", cond("close", "between", "s")),
            group("all_of", cond("close", "lt", "s")),
        )
    )
    assert not outcome.valid and any(e["code"] == "unsupported_operator" for e in outcome.errors)
    _parsed, _yaml, outcome = evaluate_output(
        provider_output(
            [ind("m", "macd", window=12)],
            group("all_of", cond("m", "gt", "0")),
            group("all_of", cond("m", "lt", "0")),
        )
    )
    assert not outcome.valid and any(e["code"] == "unsupported_indicator" for e in outcome.errors)
    # the provider shape allows two levels; a deeper legacy-shaped group still reaches the validator's depth rule
    deep = {"all_of": [{"any_of": [{"all_of": [{"any_of": [cond("close", "gt", "s")]}]}]}]}
    _parsed, _yaml, outcome = evaluate_output(
        provider_output(
            [ind("s", "sma", window=2)], deep, group("all_of", cond("close", "lt", "s"))
        )
    )
    assert not outcome.valid and any(e["code"] == "nesting_too_deep" for e in outcome.errors)


def test_indicator_items_that_are_not_objects_or_repeat_a_name_are_findings() -> None:
    indicators = [ind("s", "sma", window=2), "sma 5", ind("s", "sma", window=5)]
    _parsed, yaml_text, outcome = evaluate_output(
        provider_output(
            indicators,
            group("all_of", cond("close", "gt", "s")),
            group("all_of", cond("close", "lt", "s")),
        )
    )
    assert not outcome.valid
    assert {("invalid_type", "indicators[1]"), ("name_invalid", "indicators.s")} <= {
        (e["code"], e["path"]) for e in outcome.errors
    }
    assert "  s: {type: sma, source: close, window: 2}" in (
        yaml_text or ""
    )  # the first definition is kept


def test_provider_schema_matches_the_builders_shape() -> None:
    """The fixtures above use exactly the keys the provider is asked for."""

    spec_props = OUTPUT_SCHEMA["properties"]["specification"]["properties"]
    assert set(spec_props["indicators"]["items"]["properties"]) == set(ind("x", "sma"))
    assert set(spec_props["entry"]["properties"]) == {"combine", "conditions", "subgroups"}
    assert set(OUTPUT_SCHEMA["$defs"]["subgroup"]["properties"]) == {"combine", "conditions"}
    assert set(OUTPUT_SCHEMA["$defs"]["condition"]["properties"]) == {"left", "op", "right"}
    assert render_spec_yaml(
        canonical(
            {}, {"all_of": [cond("close", "gt", "1")]}, {"all_of": [cond("close", "lt", "1")]}
        )
    ).startswith("spec_version: 1\n")
