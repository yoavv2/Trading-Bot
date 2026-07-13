"""Phase 10 startup hardening: pure config-validation core (CFG-01/02/03/05/07).

`validate_config` is a pure, entrypoint-agnostic function operating on the RAW
pre-pydantic payload produced by `settings.build_settings_payload()`. These
tests drive that raw payload dict directly (mutating a copy) rather than an
already-constructed `Settings`, because CFG-05 (tolerance bounds) is only
reachable this way: pydantic's own `Field(ge/le)` bounds reject out-of-range
values at `Settings.model_validate()` time before any semantic validator
could see them.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trading_platform.core.config_validation import (
    ConfigValidationError,
    ExecutionMode,
    validate_config,
)
from trading_platform.core.settings import Settings, build_settings_payload


def _raw_payload() -> dict[str, Any]:
    """A fresh, deep-copied raw pre-pydantic payload for mutation in tests."""
    return copy.deepcopy(build_settings_payload())


# --- ExecutionMode: closed enum -------------------------------------------------


def test_execution_mode_is_closed_three_member_enum() -> None:
    assert {member.value for member in ExecutionMode} == {"backtest", "paper", "live"}
    assert len(ExecutionMode) == 3


# --- ConfigValidationError: typed, not a bare ValueError ------------------------


def test_config_validation_error_is_not_a_bare_value_error() -> None:
    assert issubclass(ConfigValidationError, Exception)
    assert not issubclass(ConfigValidationError, ValueError)


# --- CFG-05: tolerance bounds reachable as a named ConfigValidationError --------


def test_tolerance_bounds_out_of_range_risk_per_trade_raises_named_field() -> None:
    payload = _raw_payload()
    payload["strategies"]["trend_following_daily"]["risk"]["risk_per_trade"] = 2.0

    with pytest.raises(ConfigValidationError) as exc_info:
        validate_config(payload, mode=ExecutionMode.BACKTEST)

    fields = [field for field, _ in exc_info.value.failures]
    assert "strategies.trend_following_daily.risk.risk_per_trade" in fields


def test_tolerance_bounds_out_of_range_stale_run_timeout_raises_named_field() -> None:
    payload = _raw_payload()
    payload["execution"]["safety"]["stale_run_timeout_minutes"] = 0

    with pytest.raises(ConfigValidationError) as exc_info:
        validate_config(payload, mode=ExecutionMode.BACKTEST)

    fields = [field for field, _ in exc_info.value.failures]
    assert "execution.safety.stale_run_timeout_minutes" in fields


def test_error_message_names_field_and_expected_shape() -> None:
    payload = _raw_payload()
    payload["strategies"]["trend_following_daily"]["risk"]["risk_per_trade"] = 2.0

    with pytest.raises(ConfigValidationError) as exc_info:
        validate_config(payload, mode=ExecutionMode.BACKTEST)

    message = str(exc_info.value)
    assert "Configuration invalid:" in message
    assert "strategies.trend_following_daily.risk.risk_per_trade" in message
    # CFG-07: never a bare, unnamed "config invalid" with no field detail.
    assert message.strip() != "Configuration invalid:"


# --- Valid payload returns a constructed Settings --------------------------------


def test_valid_payload_returns_settings() -> None:
    payload = _raw_payload()

    settings = validate_config(payload, mode=ExecutionMode.BACKTEST)

    assert isinstance(settings, Settings)


# --- CFG-01: required secrets per active mode -------------------------------


def test_paper_mode_with_empty_alpaca_keys_raises_naming_api_key() -> None:
    payload = _raw_payload()
    payload["broker"]["alpaca"]["api_key"] = ""
    payload["broker"]["alpaca"]["api_secret"] = ""

    with pytest.raises(ConfigValidationError) as exc_info:
        validate_config(payload, mode=ExecutionMode.PAPER)

    fields = [field for field, _ in exc_info.value.failures]
    assert "broker.alpaca.api_key" in fields


def test_backtest_mode_with_empty_alpaca_keys_returns_settings() -> None:
    """Empty broker keys must not block a backtest boot."""
    payload = _raw_payload()
    payload["broker"]["alpaca"]["api_key"] = ""
    payload["broker"]["alpaca"]["api_secret"] = ""

    settings = validate_config(payload, mode=ExecutionMode.BACKTEST)

    assert isinstance(settings, Settings)
    assert settings.broker.alpaca.api_key == ""


def test_live_mode_with_empty_alpaca_keys_raises_naming_api_key() -> None:
    payload = _raw_payload()
    payload["broker"]["alpaca"]["api_key"] = ""
    payload["broker"]["alpaca"]["api_secret"] = ""
    payload["broker"]["alpaca"]["base_url"] = "https://api.alpaca.markets"

    with pytest.raises(ConfigValidationError) as exc_info:
        validate_config(payload, mode=ExecutionMode.LIVE)

    fields = [field for field, _ in exc_info.value.failures]
    assert "broker.alpaca.api_key" in fields


# --- CFG-02: cross-field — mode=paper forbids a configured live endpoint ----


def test_live_endpoint_configured_while_mode_paper_raises_naming_base_url() -> None:
    payload = _raw_payload()
    payload["broker"]["alpaca"]["api_key"] = "key"
    payload["broker"]["alpaca"]["api_secret"] = "secret"
    payload["broker"]["alpaca"]["base_url"] = "https://api.alpaca.markets"

    with pytest.raises(ConfigValidationError) as exc_info:
        validate_config(payload, mode=ExecutionMode.PAPER)

    fields = [field for field, _ in exc_info.value.failures]
    assert "broker.alpaca.base_url" in fields


# --- CFG-03: mutual exclusion — paper vs live endpoint cannot both hold -----


def test_paper_endpoint_configured_while_mode_live_raises_naming_base_url() -> None:
    payload = _raw_payload()
    payload["broker"]["alpaca"]["api_key"] = "key"
    payload["broker"]["alpaca"]["api_secret"] = "secret"
    # base_url defaults to the paper endpoint; mode=LIVE conflicts with it.

    with pytest.raises(ConfigValidationError) as exc_info:
        validate_config(payload, mode=ExecutionMode.LIVE)

    fields = [field for field, _ in exc_info.value.failures]
    assert "broker.alpaca.base_url" in fields


# --- Fully-valid paper payload returns Settings with no error --------------


def test_fully_valid_paper_payload_returns_settings() -> None:
    payload = _raw_payload()
    payload["broker"]["alpaca"]["api_key"] = "key"
    payload["broker"]["alpaca"]["api_secret"] = "secret"
    # base_url already defaults to the paper endpoint; tolerances already in range.

    settings = validate_config(payload, mode=ExecutionMode.PAPER)

    assert isinstance(settings, Settings)
    assert settings.broker.alpaca.api_key == "key"
