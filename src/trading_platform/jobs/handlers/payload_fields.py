"""Shared strict payload-field validators for Phase 20 Job submission specs
(D-21, D-22, D-24, D-25).

Every Phase 20 Job type (``risk-evaluation``, ``paper-session``,
``reconciliation``, ``ingest-bars``, ``sync-symbol-metadata``,
``sync-market-sessions``, ``broker-order-sync``) validates its payload
strictly (``extra="forbid"``) and identically for the fields it shares with
the others -- ``strategy_id``, ``as_of_session``, ``from_date``/``to_date``,
and ``symbols``. This module is the single source of those field-level
validators and semantic checks, following the ``backtest_submission.py``
precedent (P19 D-08/D-09). Each spec still declares its own closed
``*PayloadRejection`` enum listing only the subset of ``PayloadFieldRejection``
values it can actually emit.

Nothing here defaults a payload value (P19 D-08) -- every function either
validates/normalizes an explicit input or raises. ``latest_completed_session_default``
and ``format_symbols_default`` are the read-only ``submission_defaults()``
helpers a spec calls separately, never from inside ``validate_payload``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import ValidationError
from pydantic_core import PydanticCustomError

from trading_platform.core.settings import Settings
from trading_platform.db.session import session_scope
from trading_platform.jobs.registry import InvalidJobPayloadError
from trading_platform.services.calendar import get_calendar, is_trading_session
from trading_platform.services.market_data_access import latest_completed_session
from trading_platform.strategies.registry import UnknownStrategyError
from trading_platform.strategies.registry import (
    build_default_registry as build_default_strategy_registry,
)

MAX_SYMBOLS = 500
SYMBOL_PATTERN = re.compile(r"^[A-Z][A-Z0-9.\-]{0,14}$")

_ISO_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class PayloadFieldRejection(StrEnum):
    """Closed, stable vocabulary of machine-readable payload rejection
    reasons shared across every Phase 20 Job type. Each ``*PayloadRejection``
    enum on a concrete spec lists only the subset it can emit."""

    UNKNOWN_PAYLOAD_KEYS = "unknown_payload_keys"
    MISSING_REQUIRED_FIELD = "missing_required_field"
    INVALID_FIELD_TYPE = "invalid_field_type"
    INVALID_DATE = "invalid_date"
    UNKNOWN_STRATEGY_ID = "unknown_strategy_id"
    AS_OF_SESSION_IN_FUTURE = "as_of_session_in_future"
    AS_OF_SESSION_NOT_TRADING_SESSION = "as_of_session_not_trading_session"
    AS_OF_SESSION_OUT_OF_CALENDAR_RANGE = "as_of_session_out_of_calendar_range"
    FROM_DATE_AFTER_TO_DATE = "from_date_after_to_date"
    TO_DATE_IN_FUTURE = "to_date_in_future"
    EMPTY_SYMBOLS = "empty_symbols"
    INVALID_SYMBOL = "invalid_symbol"
    TOO_MANY_SYMBOLS = "too_many_symbols"


def parse_iso_date(value: Any) -> date:
    """``field_validator(mode="before")`` helper: accept a ``date`` or an
    ISO ``YYYY-MM-DD`` string; else raise a ``PydanticCustomError`` whose
    ``type`` matches ``PayloadFieldRejection.INVALID_DATE``."""

    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str) and _ISO_DATE_PATTERN.match(value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    raise PydanticCustomError(
        PayloadFieldRejection.INVALID_DATE.value,
        "must be an ISO date string (YYYY-MM-DD)",
    )


def normalize_symbols(value: Any) -> list[str]:
    """``field_validator(mode="before")`` helper: normalize a raw ``symbols``
    payload field. Strips/upper-cases each entry, drops empties, de-duplicates
    and sorts, then enforces the ticker whitelist and the ``MAX_SYMBOLS`` cap
    (T-20-03-01/T-20-03-02). Raises a ``PydanticCustomError`` whose ``type``
    matches the corresponding ``PayloadFieldRejection`` value."""

    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise PydanticCustomError(
            PayloadFieldRejection.INVALID_FIELD_TYPE.value,
            "symbols must be a list of strings",
        )

    cleaned = sorted({item.strip().upper() for item in value if item.strip()})

    if not cleaned:
        raise PydanticCustomError(
            PayloadFieldRejection.EMPTY_SYMBOLS.value,
            "symbols must not be empty",
        )

    for symbol in cleaned:
        if not SYMBOL_PATTERN.match(symbol):
            raise PydanticCustomError(
                PayloadFieldRejection.INVALID_SYMBOL.value,
                "invalid ticker format: {symbol}",
                {"symbol": symbol},
            )

    if len(cleaned) > MAX_SYMBOLS:
        raise PydanticCustomError(
            PayloadFieldRejection.TOO_MANY_SYMBOLS.value,
            "too many symbols: max {max_symbols}",
            {"max_symbols": MAX_SYMBOLS},
        )

    return cleaned


def map_validation_error(exc: ValidationError) -> PayloadFieldRejection:
    """Fixed precedence (mirrors ``backtest_submission._map_validation_error``,
    generalized): extra keys first, then a missing field, then the first
    error whose ``type`` names a ``PayloadFieldRejection`` value (i.e. one
    raised by ``parse_iso_date``/``normalize_symbols`` or a spec's own
    semantic validator), else the residual case is a wrong-typed field."""

    errors = exc.errors()
    if any(error["type"] == "extra_forbidden" for error in errors):
        return PayloadFieldRejection.UNKNOWN_PAYLOAD_KEYS
    if any(error["type"] == "missing" for error in errors):
        return PayloadFieldRejection.MISSING_REQUIRED_FIELD

    rejection_values = {member.value for member in PayloadFieldRejection}
    for error in errors:
        if error["type"] in rejection_values:
            return PayloadFieldRejection(error["type"])

    return PayloadFieldRejection.INVALID_FIELD_TYPE


def exchange_today(settings: Settings, clock: Callable[[], datetime]) -> date:
    """Return "today" in the configured exchange's local calendar, derived
    from the injected clock (never the host's local date -- D-21)."""

    return clock().astimezone(get_calendar(settings.market_data.calendar.exchange).tz).date()


def require_registered_strategy(
    settings: Settings, strategy_id: str, *, job_type: str
) -> None:
    """Raise ``InvalidJobPayloadError(UNKNOWN_STRATEGY_ID)`` unless
    ``strategy_id`` resolves against the strategy registry (D-22)."""

    registry = build_default_strategy_registry(settings)
    try:
        registry.resolve(strategy_id)
    except UnknownStrategyError as exc:
        raise InvalidJobPayloadError(
            job_type=job_type,
            reason=PayloadFieldRejection.UNKNOWN_STRATEGY_ID.value,
        ) from exc


def require_trading_session_not_future(
    settings: Settings,
    clock: Callable[[], datetime],
    as_of_session: date,
    *,
    job_type: str,
) -> None:
    """D-21: ``as_of_session`` must be an actual exchange trading session on
    or before the exchange-local date of the injected clock. Never
    substitutes a default -- callers pre-fill via
    ``latest_completed_session_default`` separately. The future check runs
    first, then the trading-session check. A date outside the exchange
    calendar's supported window is rejected with its own closed reason."""

    today = exchange_today(settings, clock)
    if as_of_session > today:
        raise InvalidJobPayloadError(
            job_type=job_type,
            reason=PayloadFieldRejection.AS_OF_SESSION_IN_FUTURE.value,
        )
    try:
        is_session = is_trading_session(as_of_session, settings.market_data.calendar.exchange)
    except (ValueError, OverflowError) as exc:
        # exchange_calendars only knows a rolling window of sessions;
        # outside it ``is_session`` raises ``DateOutOfBounds`` (a
        # ``ValueError``) or, for absurd years, ``OverflowError``. Both must
        # surface as a typed rejection (D-25), never an untyped exception.
        raise InvalidJobPayloadError(
            job_type=job_type,
            reason=PayloadFieldRejection.AS_OF_SESSION_OUT_OF_CALENDAR_RANGE.value,
        ) from exc
    if not is_session:
        raise InvalidJobPayloadError(
            job_type=job_type,
            reason=PayloadFieldRejection.AS_OF_SESSION_NOT_TRADING_SESSION.value,
        )


def require_date_range(
    settings: Settings,
    clock: Callable[[], datetime],
    from_date: date,
    to_date: date,
    *,
    job_type: str,
) -> None:
    """Mirrors ``backtest_submission.BacktestSubmissionSpec.validate_payload``'s
    date-range checks: an inverted range, then a ``to_date`` after the
    exchange-local today (judged via the injected clock)."""

    if from_date > to_date:
        raise InvalidJobPayloadError(
            job_type=job_type,
            reason=PayloadFieldRejection.FROM_DATE_AFTER_TO_DATE.value,
        )

    today = exchange_today(settings, clock)
    if to_date > today:
        raise InvalidJobPayloadError(
            job_type=job_type,
            reason=PayloadFieldRejection.TO_DATE_IN_FUTURE.value,
        )


def latest_completed_session_default(settings: Settings) -> date | None:
    """Read-only ``submission_defaults()`` helper -- the same "latest
    completed session" query ``BacktestSubmissionSpec.submission_defaults``
    uses. Never called from inside ``validate_payload`` (P19 D-08)."""

    with session_scope(settings) as session:
        return latest_completed_session(
            session, exchange=settings.market_data.calendar.exchange
        )


def format_symbols_default(symbols: Iterable[str]) -> str:
    """Render a normalized ``symbols`` default as the comma-separated string
    the console form field expects (UI-SPEC)."""

    normalized = sorted({symbol.strip().upper() for symbol in symbols if symbol.strip()})
    return ",".join(normalized)


__all__ = [
    "MAX_SYMBOLS",
    "SYMBOL_PATTERN",
    "PayloadFieldRejection",
    "exchange_today",
    "format_symbols_default",
    "latest_completed_session_default",
    "map_validation_error",
    "normalize_symbols",
    "parse_iso_date",
    "require_date_range",
    "require_registered_strategy",
    "require_trading_session_not_future",
]
