"""Per-mode required-secret / endpoint-mode semantic checks (CFG-01/02/03).

This module owns the semantic layer of config validation: given an
already-constructed, shape-valid `Settings` and the active `ExecutionMode`,
decide whether the required broker secrets are present and whether the
configured broker endpoint matches the active mode. It is deliberately thin
-- `services/config/validation.py` owns the pydantic shape pass and
orchestration; this module owns only the CFG-01/02/03 semantic checks that
were previously private to `core/config_validation.py`.
"""

from __future__ import annotations

from trading_platform.core.settings import Settings
from trading_platform.services.config.validation import ExecutionMode

_PAPER_BASE_URL_MARKER = "paper-api"


def semantic_failures(settings: Settings, *, mode: ExecutionMode) -> list[tuple[str, str]]:
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
