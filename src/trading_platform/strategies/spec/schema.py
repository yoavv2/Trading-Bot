"""Pydantic shape of the strategy specification v1 (contract §2–§4).

Shape only. Semantic validation (references, units, history minimums, bounds that
depend on other fields, derived values) lives in ``validate.py``.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Literal, Union

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator

SeriesName = Literal["open", "high", "low", "close", "volume"]
IndicatorType = Literal["sma", "ema", "rsi", "highest", "lowest", "lag", "change_pct"]
Operator = Literal["gt", "ge", "lt", "le", "crosses_above", "crosses_below"]

SERIES_NAMES: frozenset[str] = frozenset({"open", "high", "low", "close", "volume"})
WINDOW_TYPES: frozenset[str] = frozenset({"sma", "highest", "lowest"})
RECURSIVE_TYPES: frozenset[str] = frozenset({"ema", "rsi"})
PERIOD_TYPES: frozenset[str] = frozenset({"lag", "change_pct"})
CROSS_OPERATORS: frozenset[str] = frozenset({"crosses_above", "crosses_below"})

MAX_INDICATORS = 12
MAX_CONDITIONS = 16
MAX_NESTING = 3
MAX_WINDOW = 500
MIN_RSI_WINDOW = 2
MAX_RSI_WINDOW = 200  # contract section 3: rsi window 2..200
MAX_HISTORY = 1000
MAX_SHIFT = 20
MAX_NAME = 80
MAX_DESCRIPTION = 2000


class IndicatorSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: IndicatorType
    source: SeriesName = "close"
    window: int | None = Field(default=None, ge=1, le=MAX_WINDOW)
    history: int | None = Field(default=None, ge=1, le=MAX_HISTORY)
    periods: int | None = Field(default=None, ge=1, le=MAX_WINDOW)
    shift: int = Field(default=0, ge=0, le=MAX_SHIFT)


_NUMBER = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")


def normalize_constant(value: Decimal) -> Decimal:
    """One canonical Decimal per number (``30``, ``30.0`` and ``"30"`` hash the same)."""

    return Decimal(format(value.normalize(), "f"))


class Condition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    left: StrictStr
    op: Operator
    right: Union[StrictStr, Decimal]

    @field_validator("right", mode="before")
    @classmethod
    def _numeric_strings_are_constants(cls, value: object) -> object:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return normalize_constant(Decimal(str(value)))
        if isinstance(value, Decimal):
            return normalize_constant(value)
        if isinstance(value, str) and _NUMBER.match(value.strip()):
            return normalize_constant(Decimal(value.strip()))
        return value


class ConditionGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    all_of: list[Union["ConditionGroup", Condition]] | None = None
    any_of: list[Union["ConditionGroup", Condition]] | None = None


ConditionNode = Union[ConditionGroup, Condition]


class StrategySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec_version: Literal[1]
    name: StrictStr = Field(min_length=1, max_length=MAX_NAME)
    description: StrictStr = Field(default="", max_length=MAX_DESCRIPTION)
    timeframe: Literal["daily"]
    direction: Literal["long_only"]
    indicators: dict[str, IndicatorSpec] = Field(default_factory=dict)
    entry: ConditionNode
    exit: ConditionNode


ConditionGroup.model_rebuild()
StrategySpec.model_rebuild()
