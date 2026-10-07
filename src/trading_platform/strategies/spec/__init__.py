"""Strategy specification v1: schema, validation, evaluation, explanation."""

from trading_platform.strategies.spec.errors import SpecError, SpecErrorCode, SpecValidationError
from trading_platform.strategies.spec.explain import explain
from trading_platform.strategies.spec.validate import CompiledSpec, validate_spec, validate_yaml

__all__ = [
    "CompiledSpec",
    "SpecError",
    "SpecErrorCode",
    "SpecValidationError",
    "explain",
    "validate_spec",
    "validate_yaml",
]
