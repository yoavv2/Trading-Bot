"""Closed error vocabulary for the strategy specification (contract §7)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SpecErrorCode(StrEnum):
    UNKNOWN_FIELD = "unknown_field"
    MISSING_FIELD = "missing_field"
    INVALID_TYPE = "invalid_type"
    SPEC_VERSION_UNSUPPORTED = "spec_version_unsupported"
    TIMEFRAME_NOT_SUPPORTED = "timeframe_not_supported"
    DIRECTION_NOT_SUPPORTED = "direction_not_supported"
    UNSUPPORTED_INDICATOR = "unsupported_indicator"
    UNSUPPORTED_SOURCE = "unsupported_source"
    UNSUPPORTED_OPERATOR = "unsupported_operator"
    PARAMETER_OUT_OF_BOUNDS = "parameter_out_of_bounds"
    HISTORY_BELOW_MINIMUM = "history_below_minimum"
    UNDEFINED_REFERENCE = "undefined_reference"
    SELF_REFERENCE = "self_reference"
    UNIT_MISMATCH = "unit_mismatch"
    NESTING_TOO_DEEP = "nesting_too_deep"
    TOO_MANY_CONDITIONS = "too_many_conditions"
    TOO_MANY_INDICATORS = "too_many_indicators"
    LOOKAHEAD_REFERENCE_NOT_SUPPORTED = "lookahead_reference_not_supported"
    STOP_OR_TARGET_PRICE_NOT_SUPPORTED = "stop_or_target_price_not_supported"
    POSITION_SIZING_IN_STRATEGY_NOT_SUPPORTED = "position_sizing_in_strategy_not_supported"
    MULTI_ASSET_CONDITION_NOT_SUPPORTED = "multi_asset_condition_not_supported"
    NAME_INVALID = "name_invalid"
    DESCRIPTION_TOO_LONG = "description_too_long"
    OTHER_UNSUPPORTED_REQUEST = "other_unsupported_request"


@dataclass(frozen=True)
class SpecError:
    code: SpecErrorCode
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code.value, "path": self.path, "message": self.message}


class SpecValidationError(ValueError):
    """Raised by ``validate_spec`` with every finding, never just the first."""

    def __init__(self, errors: list[SpecError]) -> None:
        self.errors = list(errors)
        summary = "; ".join(f"{e.code.value}@{e.path}" for e in self.errors)
        super().__init__(f"Invalid strategy specification: {summary}")

    @property
    def codes(self) -> list[SpecErrorCode]:
        return [e.code for e in self.errors]
