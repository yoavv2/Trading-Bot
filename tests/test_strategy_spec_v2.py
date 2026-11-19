"""Specification v2: the required ``risk`` block (stop, target, minimum reward-to-risk).

v1 documents stay valid and hash exactly as before; every v2 rejection has a closed code.
"""

from __future__ import annotations

import copy
from decimal import Decimal
from typing import Any

import pytest
import yaml

from trading_platform.services.research.examples import load_example_specs
from trading_platform.strategies.spec import (
    SpecErrorCode,
    SpecValidationError,
    explain,
    validate_spec,
)
from trading_platform.strategies.trade_rules import RiskPlan

V1: dict[str, Any] = {
    "spec_version": 1,
    "name": "Base",
    "description": "",
    "timeframe": "daily",
    "direction": "long_only",
    "indicators": {"sma_fast": {"type": "sma", "source": "close", "window": 50}},
    "entry": {"all_of": [{"left": "close", "op": "gt", "right": "sma_fast"}]},
    "exit": {"any_of": [{"left": "close", "op": "lt", "right": "sma_fast"}]},
}
RISK: dict[str, Any] = {
    "stop": {"type": "percent", "value": 5},
    "target": {"type": "risk_multiple", "value": 2},
    "min_reward_risk": 1.5,
}


def v2(**risk_overrides: Any) -> dict[str, Any]:
    raw = copy.deepcopy(V1)
    raw["spec_version"] = 2
    raw["risk"] = copy.deepcopy(RISK) | risk_overrides
    return raw


def codes_of(raw: dict[str, Any]) -> list[SpecErrorCode]:
    with pytest.raises(SpecValidationError) as info:
        validate_spec(raw)
    return info.value.codes


def test_v2_compiles_to_a_risk_plan() -> None:
    compiled = validate_spec(v2())
    assert compiled.spec_version == 2
    assert compiled.risk == RiskPlan(Decimal(5), Decimal(2), Decimal("1.5"))
    # The condition trees and history derivation are the v1 ones.
    assert compiled.history_required == validate_spec(V1).history_required


def test_v1_has_no_risk_plan_and_its_hash_is_unchanged() -> None:
    compiled = validate_spec(V1)
    assert compiled.risk is None and compiled.spec_version == 1
    assert "risk" not in compiled.canonical_json


def test_the_four_examples_still_hash_to_their_pinned_digests() -> None:
    """v1 documents evaluate and hash exactly as before v2 existed."""

    assert {e.key: e.compiled.spec_sha256 for e in load_example_specs()} == PINNED_EXAMPLE_DIGESTS


def test_v2_hash_is_stable_across_number_spellings_and_covers_the_risk_block() -> None:
    base = validate_spec(v2()).spec_sha256
    spelled = v2(stop={"type": "percent", "value": "5.0"}, min_reward_risk="1.50")
    assert validate_spec(spelled).spec_sha256 == base
    assert validate_spec(v2(min_reward_risk=1)).spec_sha256 != base
    assert validate_spec(v2(stop={"type": "percent", "value": 4})).spec_sha256 != base
    assert validate_spec(yaml.safe_load(yaml.safe_dump(v2()))).spec_sha256 == base


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (lambda s: s.pop("risk"), SpecErrorCode.MISSING_FIELD),
        (lambda s: s["risk"].pop("stop"), SpecErrorCode.MISSING_FIELD),
        (lambda s: s["risk"].pop("target"), SpecErrorCode.MISSING_FIELD),
        (lambda s: s["risk"].pop("min_reward_risk"), SpecErrorCode.MISSING_FIELD),
        (lambda s: s["risk"]["stop"].update({"type": "atr"}), SpecErrorCode.STOP_TYPE_NOT_SUPPORTED),
        (lambda s: s["risk"]["stop"].update({"type": "indicator"}), SpecErrorCode.STOP_TYPE_NOT_SUPPORTED),
        (lambda s: s["risk"]["stop"].update({"type": "trailing"}), SpecErrorCode.STOP_TYPE_NOT_SUPPORTED),
        (lambda s: s["risk"]["target"].update({"type": "percent"}), SpecErrorCode.TARGET_TYPE_NOT_SUPPORTED),
        (lambda s: s["risk"]["stop"].update({"value": 0}), SpecErrorCode.PARAMETER_OUT_OF_BOUNDS),
        (lambda s: s["risk"]["stop"].update({"value": 50.01}), SpecErrorCode.PARAMETER_OUT_OF_BOUNDS),
        (lambda s: s["risk"]["target"].update({"value": 0}), SpecErrorCode.PARAMETER_OUT_OF_BOUNDS),
        (lambda s: s["risk"]["target"].update({"value": 21}), SpecErrorCode.PARAMETER_OUT_OF_BOUNDS),
        (lambda s: s["risk"].update({"min_reward_risk": -0.1}), SpecErrorCode.PARAMETER_OUT_OF_BOUNDS),
        (lambda s: s["risk"].update({"min_reward_risk": 21}), SpecErrorCode.PARAMETER_OUT_OF_BOUNDS),
        (lambda s: s["risk"]["stop"].update({"value": "wide"}), SpecErrorCode.INVALID_TYPE),
        (lambda s: s["risk"]["stop"].update({"atr_window": 14}), SpecErrorCode.UNKNOWN_FIELD),
        (lambda s: s["risk"].update({"trailing_stop": 3}), SpecErrorCode.UNKNOWN_FIELD),
        (lambda s: s.update({"direction": "short"}), SpecErrorCode.DIRECTION_NOT_SUPPORTED),
        (lambda s: s.update({"direction": "long_short"}), SpecErrorCode.DIRECTION_NOT_SUPPORTED),
        (lambda s: s.update({"stop_loss": 0.05}), SpecErrorCode.STOP_OR_TARGET_PRICE_NOT_SUPPORTED),
        (lambda s: s.update({"position_size": 0.1}), SpecErrorCode.POSITION_SIZING_IN_STRATEGY_NOT_SUPPORTED),
    ],
)
def test_every_v2_rejection_has_a_closed_code(mutation, expected: SpecErrorCode) -> None:
    raw = v2()
    mutation(raw)
    assert expected in codes_of(raw)


def test_a_risk_block_on_a_v1_document_is_still_refused() -> None:
    raw = copy.deepcopy(V1) | {"risk": copy.deepcopy(RISK)}
    assert SpecErrorCode.POSITION_SIZING_IN_STRATEGY_NOT_SUPPORTED in codes_of(raw)


def test_the_explanation_renders_stop_target_and_minimum_reward_to_risk() -> None:
    text = explain(validate_spec(v2()))
    assert "Stop: 5% below the reference price." in text
    assert "Target: 2 times the planned risk" in text
    assert "reward-to-risk ratio below 1.5." in text
    assert "stop or target prices inside the bar" not in text
    assert text == explain(validate_spec(v2()))
    assert explain(validate_spec(v2(min_reward_risk=1))) != text


def test_the_v1_explanation_is_unchanged() -> None:
    text = explain(validate_spec(V1))
    assert "Stop:" not in text and "Signals fill at the next session's open." in text
    assert "stop or target prices inside the bar" in text


# Digests computed at commit 0a7d8e6, before specification v2 existed.
PINNED_EXAMPLE_DIGESTS: dict[str, str] = {
    "trend_following_daily": "4b16dde71652a6b272ca9b81a70bb245eca1ab2fe7aeae984a771f3eed9211b4",
    "rsi_mean_reversion_daily": "8864dbc17b284055af0e6168fce2bc02819f04847f51fc2643887002759f1ba2",
    "donchian_breakout_daily": "155d2cb20119d409b8e74bba51cdaf0e51417af9a16aa42c30c1f57b18bfed58",
    "time_series_momentum_daily": "475b1a80abbf36638256139527cfe14db48a600a58fdb891976e4ed96b849d95",
}
