"""Validation, derivation and canonicalisation of a strategy specification.

``validate_spec(raw)`` turns a parsed YAML/JSON mapping into a ``CompiledSpec`` or
raises ``SpecValidationError`` carrying every finding with a closed code. Nothing is
approximated: an unsupported request is an error, never a silent substitution.

History derivation (contract §5): a term evaluated at offset ``o`` needs
``W + shift + o`` bars where ``W`` is its own window (``window`` for sma/highest/
lowest, ``history`` for ema/rsi, ``periods + 1`` for lag/change_pct, 1 for a series).
A crossing condition evaluates both operands at offsets 0 and 1.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from pydantic import ValidationError

from trading_platform.strategies.spec.errors import SpecError, SpecErrorCode, SpecValidationError
from trading_platform.strategies.spec.schema import (
    CROSS_OPERATORS,
    MAX_CONDITIONS,
    MAX_INDICATORS,
    MAX_NESTING,
    MAX_RSI_WINDOW,
    MIN_RSI_WINDOW,
    PERIOD_TYPES,
    RECURSIVE_TYPES,
    SERIES_NAMES,
    WINDOW_TYPES,
    Condition,
    ConditionGroup,
    IndicatorSpec,
    StrategySpec,
)

NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,31}$")

STOP_TARGET_KEYS = frozenset(
    {"stop_loss", "take_profit", "trailing_stop", "stop", "target", "stop_price", "target_price"}
)
SIZING_KEYS = frozenset({"position_size", "sizing", "risk", "quantity", "allocation", "leverage"})

UNIT_PRICE = "price"
UNIT_VOLUME = "volume"
UNIT_DIMENSIONLESS = "dimensionless"
SCALE_FREE = "price_scale_free"
SCALE_DEPENDENT = "price_scale_dependent"


# ---------------------------------------------------------------------------
# Compiled form
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Term:
    """A series or indicator reference resolved to its evaluation parameters."""

    name: str
    kind: str  # "series" | indicator type
    source: str
    window: int  # the slice length W (contract §5)
    shift: int
    minimum_window: int  # mathematical minimum slice length
    unit: str
    window_param: int = 0  # recursion window for ema/rsi (0 for other kinds)

    def bars_needed(self, offset: int) -> int:
        return self.window + self.shift + offset

    def minimum_bars_needed(self, offset: int) -> int:
        return self.minimum_window + self.shift + offset


@dataclass(frozen=True)
class Leaf:
    left: Term
    op: str
    right: Term | Decimal

    @property
    def is_cross(self) -> bool:
        return self.op in CROSS_OPERATORS

    def bars_needed(self) -> int:
        offset = 1 if self.is_cross else 0
        needed = self.left.bars_needed(offset)
        if isinstance(self.right, Term):
            needed = max(needed, self.right.bars_needed(offset))
        return needed

    def minimum_bars_needed(self) -> int:
        offset = 1 if self.is_cross else 0
        needed = self.left.minimum_bars_needed(offset)
        if isinstance(self.right, Term):
            needed = max(needed, self.right.minimum_bars_needed(offset))
        return needed


@dataclass(frozen=True)
class Group:
    combinator: str  # "all_of" | "any_of"
    children: tuple["Group | Leaf", ...]


Node = Group | Leaf


@dataclass(frozen=True)
class CompiledSpec:
    spec: StrategySpec
    terms: dict[str, Term]
    entry: Node
    exit: Node
    history_required: int
    history_minimum: int
    scale_class: str
    terms_used: tuple[str, ...]
    operators_used: tuple[str, ...]
    canonical_json: str
    spec_sha256: str
    pine_equivalent_available: bool = True
    leaves: tuple[Leaf, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Pre-checks on the raw mapping (closed codes for requests the schema cannot name)
# ---------------------------------------------------------------------------


def _precheck(raw: Any) -> list[SpecError]:
    errors: list[SpecError] = []
    if not isinstance(raw, dict):
        return [SpecError(SpecErrorCode.INVALID_TYPE, "", "specification must be a mapping")]
    for key in raw:
        if key in STOP_TARGET_KEYS:
            errors.append(
                SpecError(
                    SpecErrorCode.STOP_OR_TARGET_PRICE_NOT_SUPPORTED,
                    key,
                    "intrabar stop/target prices are not supported in v1; express exits on the close",
                )
            )
        elif key in SIZING_KEYS:
            errors.append(
                SpecError(
                    SpecErrorCode.POSITION_SIZING_IN_STRATEGY_NOT_SUPPORTED,
                    key,
                    "position sizing is a study setting, not part of a strategy",
                )
            )
    timeframe = raw.get("timeframe")
    if timeframe is not None and timeframe != "daily":
        errors.append(SpecError(SpecErrorCode.TIMEFRAME_NOT_SUPPORTED, "timeframe", f"{timeframe!r} is not supported"))
    direction = raw.get("direction")
    if direction is not None and direction != "long_only":
        errors.append(SpecError(SpecErrorCode.DIRECTION_NOT_SUPPORTED, "direction", f"{direction!r} is not supported"))
    version = raw.get("spec_version")
    if version is not None and version != 1:
        errors.append(SpecError(SpecErrorCode.SPEC_VERSION_UNSUPPORTED, "spec_version", f"{version!r} is not supported"))
    indicators = raw.get("indicators")
    if isinstance(indicators, dict):
        for name, body in indicators.items():
            if isinstance(body, dict):
                shift = body.get("shift")
                if isinstance(shift, int) and not isinstance(shift, bool) and shift < 0:
                    errors.append(
                        SpecError(
                            SpecErrorCode.LOOKAHEAD_REFERENCE_NOT_SUPPORTED,
                            f"indicators.{name}.shift",
                            "negative shift would read future bars",
                        )
                    )
                itype = body.get("type")
                if isinstance(itype, str) and itype not in {"sma", "ema", "rsi", "highest", "lowest", "lag", "change_pct"}:
                    errors.append(
                        SpecError(SpecErrorCode.UNSUPPORTED_INDICATOR, f"indicators.{name}.type", f"{itype!r} is not supported")
                    )
    for section in ("entry", "exit"):
        _precheck_conditions(raw.get(section), section, errors)
    return errors


def _precheck_conditions(node: Any, path: str, errors: list[SpecError]) -> None:
    if isinstance(node, dict):
        for key in ("left", "right"):
            value = node.get(key)
            if isinstance(value, str) and ":" in value:
                errors.append(
                    SpecError(
                        SpecErrorCode.MULTI_ASSET_CONDITION_NOT_SUPPORTED,
                        f"{path}.{key}",
                        "conditions may reference only the evaluated asset",
                    )
                )
        op = node.get("op")
        if isinstance(op, str) and op not in {"gt", "ge", "lt", "le", "crosses_above", "crosses_below"}:
            errors.append(SpecError(SpecErrorCode.UNSUPPORTED_OPERATOR, f"{path}.op", f"{op!r} is not supported"))
        for key in ("all_of", "any_of"):
            children = node.get(key)
            if isinstance(children, list):
                for index, child in enumerate(children):
                    _precheck_conditions(child, f"{path}.{key}[{index}]", errors)


# ---------------------------------------------------------------------------
# Pydantic error mapping
# ---------------------------------------------------------------------------


def _map_pydantic(exc: ValidationError) -> list[SpecError]:
    errors: list[SpecError] = []
    for item in exc.errors():
        loc = ".".join(str(part) for part in item["loc"])
        etype = item["type"]
        last = str(item["loc"][-1]) if item["loc"] else ""
        if etype == "extra_forbidden":
            code = SpecErrorCode.UNKNOWN_FIELD
        elif etype == "missing":
            code = SpecErrorCode.MISSING_FIELD
        elif etype in ("greater_than_equal", "less_than_equal", "greater_than", "less_than"):
            code = SpecErrorCode.PARAMETER_OUT_OF_BOUNDS
        elif etype in ("string_too_long", "string_too_short"):
            code = SpecErrorCode.DESCRIPTION_TOO_LONG if last == "description" else SpecErrorCode.NAME_INVALID
        elif etype in ("literal_error", "enum"):
            if last == "spec_version":
                code = SpecErrorCode.SPEC_VERSION_UNSUPPORTED
            elif last == "timeframe":
                code = SpecErrorCode.TIMEFRAME_NOT_SUPPORTED
            elif last == "direction":
                code = SpecErrorCode.DIRECTION_NOT_SUPPORTED
            elif last == "type":
                code = SpecErrorCode.UNSUPPORTED_INDICATOR
            elif last == "op":
                code = SpecErrorCode.UNSUPPORTED_OPERATOR
            elif last == "source":
                code = SpecErrorCode.UNSUPPORTED_SOURCE
            else:
                code = SpecErrorCode.INVALID_TYPE
        else:
            code = SpecErrorCode.INVALID_TYPE
        errors.append(SpecError(code, loc, item["msg"]))
    return errors


# ---------------------------------------------------------------------------
# Semantic validation and compilation
# ---------------------------------------------------------------------------


def _series_term(name: str, shift: int = 0) -> Term:
    unit = UNIT_VOLUME if name == "volume" else UNIT_PRICE
    return Term(name=name, kind="series", source=name, window=1, shift=shift, minimum_window=1, unit=unit)


def _indicator_term(name: str, spec: IndicatorSpec, errors: list[SpecError]) -> Term | None:
    path = f"indicators.{name}"
    source_unit = UNIT_VOLUME if spec.source == "volume" else UNIT_PRICE
    if spec.type in WINDOW_TYPES:
        if spec.window is None:
            errors.append(SpecError(SpecErrorCode.MISSING_FIELD, f"{path}.window", "window is required"))
            return None
        if spec.type in ("highest", "lowest"):
            unit = source_unit
        else:
            unit = source_unit
        return Term(name, spec.type, spec.source, spec.window, spec.shift, spec.window, unit)
    if spec.type in RECURSIVE_TYPES:
        if spec.window is None:
            errors.append(SpecError(SpecErrorCode.MISSING_FIELD, f"{path}.window", "window is required"))
            return None
        if spec.history is None:
            errors.append(
                SpecError(
                    SpecErrorCode.MISSING_FIELD,
                    f"{path}.history",
                    "history (bars fed to the recursion) is required for recursive indicators",
                )
            )
            return None
        minimum = spec.window + 1 if spec.type == "rsi" else spec.window
        if spec.type == "rsi" and not (MIN_RSI_WINDOW <= spec.window <= MAX_RSI_WINDOW):
            errors.append(
                SpecError(
                    SpecErrorCode.PARAMETER_OUT_OF_BOUNDS,
                    f"{path}.window",
                    f"rsi window must be between {MIN_RSI_WINDOW} and {MAX_RSI_WINDOW}",
                )
            )
            return None
        if spec.history < minimum:
            errors.append(
                SpecError(
                    SpecErrorCode.HISTORY_BELOW_MINIMUM,
                    f"{path}.history",
                    f"history {spec.history} is below the mathematical minimum {minimum}",
                )
            )
            return None
        unit = UNIT_DIMENSIONLESS if spec.type == "rsi" else source_unit
        return Term(name, spec.type, spec.source, spec.history, spec.shift, minimum, unit, spec.window)
    if spec.type in PERIOD_TYPES:
        if spec.periods is None:
            errors.append(SpecError(SpecErrorCode.MISSING_FIELD, f"{path}.periods", "periods is required"))
            return None
        unit = UNIT_DIMENSIONLESS if spec.type == "change_pct" else source_unit
        return Term(name, spec.type, spec.source, spec.periods + 1, spec.shift, spec.periods + 1, unit)
    errors.append(SpecError(SpecErrorCode.UNSUPPORTED_INDICATOR, f"{path}.type", spec.type))
    return None


def _check_indicator_params(name: str, spec: IndicatorSpec, errors: list[SpecError]) -> None:
    path = f"indicators.{name}"
    allowed = {"type", "source", "shift"}
    if spec.type in WINDOW_TYPES:
        allowed |= {"window"}
    elif spec.type in RECURSIVE_TYPES:
        allowed |= {"window", "history"}
    elif spec.type in PERIOD_TYPES:
        allowed |= {"periods"}
    for param in ("window", "history", "periods"):
        if param not in allowed and getattr(spec, param) is not None:
            errors.append(
                SpecError(SpecErrorCode.UNKNOWN_FIELD, f"{path}.{param}", f"{param} does not apply to {spec.type}")
            )


def _resolve_operand(
    value: str | Decimal, path: str, terms: dict[str, Term], errors: list[SpecError]
) -> Term | Decimal | None:
    if isinstance(value, Decimal):
        return value
    if value in SERIES_NAMES:
        return _series_term(value)
    term = terms.get(value)
    if term is None:
        errors.append(SpecError(SpecErrorCode.UNDEFINED_REFERENCE, path, f"{value!r} is not a series or indicator"))
        return None
    return term


def _compile_node(
    node: ConditionGroup | Condition,
    path: str,
    depth: int,
    terms: dict[str, Term],
    errors: list[SpecError],
    leaves: list[Leaf],
) -> Node | None:
    if isinstance(node, Condition):
        left = _resolve_operand(node.left, f"{path}.left", terms, errors)
        right = _resolve_operand(node.right, f"{path}.right", terms, errors)
        if left is None or right is None or isinstance(left, Decimal):
            if isinstance(left, Decimal):
                errors.append(SpecError(SpecErrorCode.INVALID_TYPE, f"{path}.left", "left must be a series or indicator"))
            return None
        if isinstance(right, Term) and left.unit != right.unit:
            errors.append(
                SpecError(
                    SpecErrorCode.UNIT_MISMATCH,
                    path,
                    f"{left.unit} compared with {right.unit}",
                )
            )
            return None
        leaf = Leaf(left=left, op=node.op, right=right)
        leaves.append(leaf)
        return leaf
    if depth > MAX_NESTING:
        errors.append(SpecError(SpecErrorCode.NESTING_TOO_DEEP, path, f"nesting deeper than {MAX_NESTING}"))
        return None
    has_all = node.all_of is not None
    has_any = node.any_of is not None
    if has_all == has_any:
        errors.append(SpecError(SpecErrorCode.INVALID_TYPE, path, "a group needs exactly one of all_of / any_of"))
        return None
    combinator = "all_of" if has_all else "any_of"
    children_raw = node.all_of if has_all else node.any_of
    if not children_raw:
        errors.append(SpecError(SpecErrorCode.INVALID_TYPE, f"{path}.{combinator}", "group must not be empty"))
        return None
    children: list[Node] = []
    for index, child in enumerate(children_raw):
        compiled = _compile_node(child, f"{path}.{combinator}[{index}]", depth + 1, terms, errors, leaves)
        if compiled is not None:
            children.append(compiled)
    if len(children) != len(children_raw):
        return None
    return Group(combinator=combinator, children=tuple(children))


def _canonical(spec: StrategySpec) -> tuple[str, str]:
    payload = spec.model_dump(mode="json", exclude_none=False)
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return text, hashlib.sha256(text.encode("utf-8")).hexdigest()


def _collect_terms(node: Node, out: list[Term]) -> None:
    if isinstance(node, Leaf):
        out.append(node.left)
        if isinstance(node.right, Term):
            out.append(node.right)
        return
    for child in node.children:
        _collect_terms(child, out)


def validate_spec(raw: Any) -> CompiledSpec:
    errors = _precheck(raw)
    if errors and any(e.code == SpecErrorCode.INVALID_TYPE and e.path == "" for e in errors):
        raise SpecValidationError(errors)
    try:
        spec = StrategySpec.model_validate(raw)
    except ValidationError as exc:
        raise SpecValidationError(errors + _map_pydantic(exc)) from None

    if len(spec.indicators) > MAX_INDICATORS:
        errors.append(
            SpecError(SpecErrorCode.TOO_MANY_INDICATORS, "indicators", f"more than {MAX_INDICATORS} indicators")
        )
    terms: dict[str, Term] = {}
    for name, indicator in spec.indicators.items():
        if not NAME_PATTERN.match(name):
            errors.append(SpecError(SpecErrorCode.NAME_INVALID, f"indicators.{name}", "indicator names are ^[a-z][a-z0-9_]{0,31}$"))
            continue
        if name in SERIES_NAMES:
            errors.append(SpecError(SpecErrorCode.SELF_REFERENCE, f"indicators.{name}", "an indicator may not shadow a series name"))
            continue
        _check_indicator_params(name, indicator, errors)
        term = _indicator_term(name, indicator, errors)
        if term is not None:
            terms[name] = term

    leaves: list[Leaf] = []
    entry = _compile_node(spec.entry, "entry", 1, terms, errors, leaves)
    exit_ = _compile_node(spec.exit, "exit", 1, terms, errors, leaves)
    if len(leaves) > MAX_CONDITIONS:
        errors.append(SpecError(SpecErrorCode.TOO_MANY_CONDITIONS, "entry/exit", f"more than {MAX_CONDITIONS} conditions"))
    if errors or entry is None or exit_ is None:
        raise SpecValidationError(errors)

    history_required = max(1, max(leaf.bars_needed() for leaf in leaves))
    history_minimum = max(1, max(leaf.minimum_bars_needed() for leaf in leaves))
    scale_class = SCALE_FREE
    for leaf in leaves:
        if isinstance(leaf.right, Decimal) and leaf.left.unit == UNIT_PRICE:
            scale_class = SCALE_DEPENDENT
    used: list[Term] = []
    _collect_terms(entry, used)
    _collect_terms(exit_, used)
    canonical_text, digest = _canonical(spec)
    return CompiledSpec(
        spec=spec,
        terms=terms,
        entry=entry,
        exit=exit_,
        history_required=history_required,
        history_minimum=history_minimum,
        scale_class=scale_class,
        terms_used=tuple(sorted({t.name for t in used})),
        operators_used=tuple(sorted({leaf.op for leaf in leaves})),
        canonical_json=canonical_text,
        spec_sha256=digest,
        leaves=tuple(leaves),
    )


def validate_yaml(text: str) -> CompiledSpec:
    import yaml  # type: ignore[import-untyped]

    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise SpecValidationError([SpecError(SpecErrorCode.INVALID_TYPE, "", f"YAML parse error: {exc}")]) from None
    return validate_spec(raw)
