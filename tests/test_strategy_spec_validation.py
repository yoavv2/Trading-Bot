"""Specification v1: closed error codes, history derivation, units, canonical hash, explanation."""

from __future__ import annotations

import copy
import hashlib
from typing import Any

import pytest

from trading_platform.strategies.spec import (
    SpecErrorCode,
    SpecValidationError,
    explain,
    validate_spec,
    validate_yaml,
)
from trading_platform.strategies.spec.validate import SCALE_DEPENDENT, SCALE_FREE


def base_spec(**overrides: Any) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "spec_version": 1,
        "name": "Base",
        "description": "",
        "timeframe": "daily",
        "direction": "long_only",
        "indicators": {
            "sma_fast": {"type": "sma", "source": "close", "window": 50},
            "sma_slow": {"type": "sma", "source": "close", "window": 200},
        },
        "entry": {"all_of": [{"left": "close", "op": "gt", "right": "sma_slow"}]},
        "exit": {"any_of": [{"left": "close", "op": "lt", "right": "sma_fast"}]},
    }
    spec.update(overrides)
    return spec


def codes_of(raw: dict[str, Any]) -> list[SpecErrorCode]:
    with pytest.raises(SpecValidationError) as info:
        validate_spec(raw)
    return info.value.codes


# ---------------------------------------------------------------------------
# Error codes: one fixture per code
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (lambda s: s.update({"universe": ["AAPL"]}), SpecErrorCode.UNKNOWN_FIELD),
        (lambda s: s.pop("entry"), SpecErrorCode.MISSING_FIELD),
        (lambda s: s.update({"name": 12}), SpecErrorCode.INVALID_TYPE),
        (lambda s: s.update({"spec_version": 2}), SpecErrorCode.SPEC_VERSION_UNSUPPORTED),
        (lambda s: s.update({"timeframe": "1h"}), SpecErrorCode.TIMEFRAME_NOT_SUPPORTED),
        (lambda s: s.update({"direction": "long_short"}), SpecErrorCode.DIRECTION_NOT_SUPPORTED),
        (lambda s: s["indicators"].update({"macd": {"type": "macd", "window": 12}}), SpecErrorCode.UNSUPPORTED_INDICATOR),
        (lambda s: s["indicators"]["sma_fast"].update({"source": "vwap"}), SpecErrorCode.UNSUPPORTED_SOURCE),
        (lambda s: s["entry"]["all_of"][0].update({"op": "eq"}), SpecErrorCode.UNSUPPORTED_OPERATOR),
        (lambda s: s["indicators"]["sma_fast"].update({"window": 0}), SpecErrorCode.PARAMETER_OUT_OF_BOUNDS),
        (lambda s: s["indicators"].update({"r": {"type": "rsi", "window": 14, "history": 10}}), SpecErrorCode.HISTORY_BELOW_MINIMUM),
        (lambda s: s["entry"]["all_of"][0].update({"right": "nope"}), SpecErrorCode.UNDEFINED_REFERENCE),
        (lambda s: s["indicators"].update({"close": {"type": "sma", "window": 5}}), SpecErrorCode.SELF_REFERENCE),
        (lambda s: s["entry"]["all_of"][0].update({"right": "volume"}), SpecErrorCode.UNIT_MISMATCH),
        (lambda s: s["indicators"]["sma_fast"].update({"shift": -1}), SpecErrorCode.LOOKAHEAD_REFERENCE_NOT_SUPPORTED),
        (lambda s: s.update({"stop_loss": 0.05}), SpecErrorCode.STOP_OR_TARGET_PRICE_NOT_SUPPORTED),
        (lambda s: s.update({"position_size": 0.1}), SpecErrorCode.POSITION_SIZING_IN_STRATEGY_NOT_SUPPORTED),
        (lambda s: s["entry"]["all_of"][0].update({"right": "SPY:close"}), SpecErrorCode.MULTI_ASSET_CONDITION_NOT_SUPPORTED),
        (lambda s: s["indicators"].update({"Bad-Name": {"type": "sma", "window": 5}}), SpecErrorCode.NAME_INVALID),
        (lambda s: s.update({"description": "x" * 2001}), SpecErrorCode.DESCRIPTION_TOO_LONG),
    ],
)
def test_each_error_code_has_a_fixture(mutation, expected: SpecErrorCode) -> None:
    raw = base_spec()
    mutation(raw)
    assert expected in codes_of(raw)


def test_nesting_too_deep_and_too_many_conditions_and_indicators() -> None:
    leaf = {"left": "close", "op": "gt", "right": "sma_slow"}
    deep = {"all_of": [{"any_of": [{"all_of": [{"any_of": [leaf]}]}]}]}
    assert SpecErrorCode.NESTING_TOO_DEEP in codes_of(base_spec(entry=deep))
    many = {"all_of": [dict(leaf) for _ in range(17)]}
    assert SpecErrorCode.TOO_MANY_CONDITIONS in codes_of(base_spec(entry=many))
    indicators = {f"i{n}": {"type": "sma", "window": n + 1} for n in range(13)}
    assert SpecErrorCode.TOO_MANY_INDICATORS in codes_of(base_spec(indicators=indicators))


def test_wrong_parameter_for_indicator_type_is_unknown_field() -> None:
    raw = base_spec()
    raw["indicators"]["sma_fast"]["history"] = 100
    assert SpecErrorCode.UNKNOWN_FIELD in codes_of(raw)


def test_recursive_indicator_requires_explicit_history() -> None:
    raw = base_spec()
    raw["indicators"]["r"] = {"type": "rsi", "window": 14}
    assert SpecErrorCode.MISSING_FIELD in codes_of(raw)


def test_all_findings_are_reported_not_only_the_first() -> None:
    raw = base_spec()
    raw["stop_loss"] = 1
    raw["position_size"] = 1
    codes = codes_of(raw)
    assert SpecErrorCode.STOP_OR_TARGET_PRICE_NOT_SUPPORTED in codes
    assert SpecErrorCode.POSITION_SIZING_IN_STRATEGY_NOT_SUPPORTED in codes


# ---------------------------------------------------------------------------
# History derivation (contract §5 worked examples)
# ---------------------------------------------------------------------------


def _spec_with(indicators: dict[str, Any], entry_leaf: dict[str, Any]) -> dict[str, Any]:
    return base_spec(indicators=indicators, entry={"all_of": [entry_leaf]}, exit={"any_of": [dict(entry_leaf, op="lt")]})


@pytest.mark.parametrize(
    ("indicators", "leaf", "required", "minimum"),
    [
        ({"r": {"type": "rsi", "window": 14, "history": 100}}, {"left": "r", "op": "lt", "right": 30}, 100, 15),
        ({"r": {"type": "rsi", "window": 14, "history": 100, "shift": 20}}, {"left": "r", "op": "lt", "right": 30}, 120, 35),
        ({"r": {"type": "rsi", "window": 14, "history": 100}}, {"left": "r", "op": "crosses_above", "right": 30}, 101, 16),
        ({"a": {"type": "sma", "window": 50}, "b": {"type": "sma", "window": 200}}, {"left": "a", "op": "crosses_above", "right": "b"}, 201, 201),
        ({"h": {"type": "highest", "source": "high", "window": 55, "shift": 1}}, {"left": "close", "op": "gt", "right": "h"}, 56, 56),
        ({"l": {"type": "lag", "source": "close", "periods": 252}}, {"left": "close", "op": "gt", "right": "l"}, 253, 253),
        ({"e": {"type": "ema", "window": 10, "history": 60, "shift": 2}}, {"left": "close", "op": "gt", "right": "e"}, 62, 12),
        ({"c": {"type": "change_pct", "source": "close", "periods": 20}}, {"left": "c", "op": "gt", "right": 0}, 21, 21),
    ],
)
def test_history_derivation_matches_the_contract(indicators, leaf, required, minimum) -> None:
    compiled = validate_spec(_spec_with(indicators, leaf))
    assert compiled.history_required == required
    assert compiled.history_minimum == minimum


# ---------------------------------------------------------------------------
# Units and scale class
# ---------------------------------------------------------------------------


def test_constant_against_price_marks_scale_dependent() -> None:
    raw = base_spec(entry={"all_of": [{"left": "close", "op": "gt", "right": 100}]})
    assert validate_spec(raw).scale_class == SCALE_DEPENDENT


def test_constant_against_dimensionless_stays_scale_free() -> None:
    raw = base_spec(
        indicators={"r": {"type": "rsi", "window": 14, "history": 100}},
        entry={"all_of": [{"left": "r", "op": "lt", "right": 30}]},
        exit={"any_of": [{"left": "r", "op": "gt", "right": 70}]},
    )
    assert validate_spec(raw).scale_class == SCALE_FREE


def test_price_against_volume_is_unit_mismatch() -> None:
    assert SpecErrorCode.UNIT_MISMATCH in codes_of(base_spec(entry={"all_of": [{"left": "close", "op": "gt", "right": "volume"}]}))


# ---------------------------------------------------------------------------
# Canonical form, hash, explanation
# ---------------------------------------------------------------------------


def test_canonical_hash_is_independent_of_key_order_and_explicit_defaults() -> None:
    a = validate_spec(base_spec())
    reordered = dict(reversed(list(base_spec().items())))
    reordered["indicators"]["sma_fast"] = {"window": 50, "source": "close", "type": "sma", "shift": 0}
    b = validate_spec(reordered)
    assert a.spec_sha256 == b.spec_sha256
    assert a.spec_sha256 == hashlib.sha256(a.canonical_json.encode()).hexdigest()


def test_hash_changes_when_a_parameter_changes() -> None:
    a = validate_spec(base_spec())
    raw = base_spec()
    raw["indicators"]["sma_fast"]["window"] = 51
    assert validate_spec(raw).spec_sha256 != a.spec_sha256


def test_explanation_is_deterministic_and_tracks_the_spec() -> None:
    compiled = validate_spec(base_spec())
    text = explain(compiled)
    assert text == explain(validate_spec(copy.deepcopy(base_spec())))
    assert "History required: 200 session(s) (mathematical minimum 200)" in text
    assert "exit rule is checked first" in text
    assert "This strategy does not:" in text
    raw = base_spec()
    raw["indicators"]["sma_fast"]["window"] = 60
    assert explain(validate_spec(raw)) != text


def test_validate_yaml_round_trips() -> None:
    compiled = validate_yaml(
        """
spec_version: 1
name: Y
timeframe: daily
direction: long_only
indicators:
  r: {type: rsi, window: 14, history: 100}
entry: {all_of: [{left: r, op: lt, right: 30}]}
exit: {any_of: [{left: r, op: gt, right: 70}]}
"""
    )
    assert compiled.history_required == 100
    assert compiled.operators_used == ("gt", "lt")
    assert compiled.terms_used == ("r",)


# ---------------------------------------------------------------------------
# Review fixes (2026-10-07): rsi window bound per contract §3; recursive-term wording
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("window", [1, 201])
def test_rsi_window_outside_2_to_200_is_out_of_bounds(window: int) -> None:
    raw = base_spec(
        indicators={"r": {"type": "rsi", "window": window, "history": 400}},
        entry={"all_of": [{"left": "r", "op": "lt", "right": 30}]},
        exit={"any_of": [{"left": "r", "op": "gt", "right": 70}]},
    )
    assert SpecErrorCode.PARAMETER_OUT_OF_BOUNDS in codes_of(raw)
    ok = dict(raw)
    ok["indicators"] = {"r": {"type": "rsi", "window": 200, "history": 400}}
    assert validate_spec(ok).history_required == 400


def test_explanation_names_the_smoothing_window_and_the_history_of_recursive_terms() -> None:
    raw = base_spec(
        indicators={
            "r": {"type": "rsi", "window": 14, "history": 100},
            "e": {"type": "ema", "window": 10, "history": 60},
        },
        entry={"all_of": [{"left": "r", "op": "lt", "right": 30}]},
        exit={"any_of": [{"left": "close", "op": "lt", "right": "e"}]},
    )
    text = explain(validate_spec(raw))
    assert "r (the 14-session RSI of the close, computed over 100 bars of history)" in text
    assert "e (the 10-session exponential moving average of the close, computed over 60 bars of history)" in text
