"""Pure, entrypoint-agnostic config validation core.

`validate_config(payload, *, mode)` validates the RAW pre-pydantic settings
payload (as produced by `settings.build_settings_payload()`) and returns a
constructed `Settings`, or raises a single aggregated `ConfigValidationError`
naming every failed field.

Validating the raw payload — not an already-constructed `Settings` — is what
makes CFG-05 (tolerance bounds) reachable as a named validation failure.
`Settings` fields already carry pydantic `Field(ge=/le=)` bounds; if this
module validated a *constructed* `Settings` instead, a bare
`pydantic.ValidationError` would already have been raised (and the bad value
already rejected) at `Settings.model_validate()` time, before any semantic
validator here could see it. So `validate_config` owns the
`Settings.model_validate()` call itself and translates any
`pydantic.ValidationError` into a typed, field-named `ConfigValidationError`
(CFG-05, CFG-07) — it does not duplicate pydantic's declared bounds in a
hand-maintained table (that would drift).

`ExecutionMode` is a closed enum passed in explicitly by the caller. There is
deliberately no corresponding field on `Settings`: a config-file mode field
could contradict the command an entrypoint is actually running (e.g. a
paper-mode config value while `backtest` is invoked). 10-05 wires an
entrypoint-selected `mode` into this function; this module has no opinion on
where a process boots.

This module performs zero I/O — no DB, no filesystem, no logging. Wiring this
into entrypoints and translating a raised `ConfigValidationError` into
process-exit behavior is explicitly out of scope here (plan 10-05).
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import ValidationError

from trading_platform.core.settings import Settings


class ExecutionMode(str, Enum):
    """Closed set of execution modes a raw config payload can be validated for.

    Always passed explicitly by the caller/entrypoint (see module docstring)
    — never read from a config field.
    """

    BACKTEST = "backtest"
    PAPER = "paper"
    LIVE = "live"


class ConfigValidationError(Exception):
    """Raised when a raw config payload fails validation for a given mode.

    Carries every failed field discovered in a single pass — both raw
    pydantic shape/bounds failures (translated, never surfaced directly) and
    semantic per-mode/cross-field failures — as a `failures` list of
    `(dotted_field_path, expected_shape)` tuples, so an operator sees every
    problem in one actionable message (CFG-07) instead of fixing them one
    pydantic error at a time.
    """

    def __init__(self, failures: list[tuple[str, str]]) -> None:
        if not failures:
            raise ValueError("ConfigValidationError requires at least one failure")
        self.failures: list[tuple[str, str]] = list(failures)
        super().__init__(str(self))

    def __str__(self) -> str:
        lines = ["Configuration invalid:"]
        lines.extend(f" - {field}: expected {shape}" for field, shape in self.failures)
        return "\n".join(lines)


def _dotted_path(loc: tuple[int | str, ...]) -> str:
    return ".".join(str(part) for part in loc)


def _translate_pydantic_errors(exc: ValidationError) -> list[tuple[str, str]]:
    """Translate pydantic's raw error list into (dotted_field, expected_shape) pairs."""
    return [(_dotted_path(error["loc"]), error["msg"]) for error in exc.errors()]


def validate_config(
    payload: dict[str, Any],
    *,
    mode: ExecutionMode = ExecutionMode.BACKTEST,
) -> Settings:
    """Validate a raw pre-pydantic settings payload for `mode` and return Settings.

    Raises `ConfigValidationError` naming every failed field instead of
    letting a raw `pydantic.ValidationError` escape. Performs zero I/O.

    Two passes:
    1. `Settings.model_validate(payload)` — a failure here (including any
       out-of-range CFG-05 tolerance) raises immediately; semantic checks
       below need a constructed `Settings` to run against.
    2. Semantic per-mode / cross-field / mutual-exclusion checks (CFG-01,
       CFG-02, CFG-03) against the validated `Settings`, aggregated into one
       `ConfigValidationError` if any fail.
    """
    try:
        settings = Settings.model_validate(payload)
    except ValidationError as exc:
        raise ConfigValidationError(_translate_pydantic_errors(exc)) from exc

    failures = _semantic_failures(settings, mode=mode)
    if failures:
        raise ConfigValidationError(failures)

    return settings


_PAPER_BASE_URL_MARKER = "paper-api"


def _semantic_failures(settings: Settings, *, mode: ExecutionMode) -> list[tuple[str, str]]:
    """CFG-01/02/03 checks against an already-constructed, shape-valid Settings.

    Backtest mode with empty broker keys is deliberately never a failure here
    — an empty-keys backtest boot must succeed (see plan `must_haves`).

    There is no separate `mode` field or distinct live-broker settings block
    in this codebase today (see plan `key_facts`) — `broker.alpaca.base_url`
    is the only signal of which environment a set of credentials targets.
    That single field is therefore what both CFG-02 (mode=paper forbids a
    configured live endpoint) and CFG-03 (paper vs live are mutually
    exclusive — a paper-mode config can't simultaneously point at a live
    endpoint, and a live-mode config can't point at the paper endpoint) key
    off; they are not two independently-checkable conditions in the current
    config surface.
    """
    if mode is ExecutionMode.BACKTEST or settings.broker.provider != "alpaca":
        return []

    alpaca = settings.broker.alpaca
    is_paper_endpoint = _PAPER_BASE_URL_MARKER in alpaca.base_url
    failures: list[tuple[str, str]] = []

    # CFG-01: required secrets for the active mode.
    if not alpaca.api_key:
        failures.append(
            ("broker.alpaca.api_key", f"non-empty string (required for {mode.value} mode)")
        )
    if not alpaca.api_secret:
        failures.append(
            ("broker.alpaca.api_secret", f"non-empty string (required for {mode.value} mode)")
        )

    # CFG-02 / CFG-03: the configured endpoint must match the active mode —
    # a paper-mode config cannot point at a live endpoint and vice versa.
    if mode is ExecutionMode.PAPER and not is_paper_endpoint:
        failures.append(
            (
                "broker.alpaca.base_url",
                "the paper trading endpoint (containing 'paper-api') while mode=paper",
            )
        )
    elif mode is ExecutionMode.LIVE and is_paper_endpoint:
        failures.append(
            (
                "broker.alpaca.base_url",
                "a live trading endpoint (not the paper-api endpoint) while mode=live",
            )
        )

    return failures
