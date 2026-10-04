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
validates/normalizes an explicit input or raises. ``evaluation_session_default``
and ``format_symbols_default`` are the read-only ``submission_defaults()``
helpers a spec calls separately, never from inside ``validate_payload``.
Session-scoped defaults come from the calendar-completed EVALUATION candidate
session (D-24), never from "the latest session that has bars".
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
from trading_platform.jobs.registry import InvalidJobPayloadError, JobSubmissionConflictError
from trading_platform.services.active_paper_strategy import ownership_block_for
from trading_platform.services.calendar import (
    CalendarOutOfBoundsError,
    calendar_horizon_end,
    get_calendar,
    is_trading_session,
)
from trading_platform.services.calendar_facts import evaluation_candidate_session
from trading_platform.strategies.base import BaseStrategy
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
    TO_DATE_BEYOND_COVERAGE_HORIZON = "to_date_beyond_coverage_horizon"
    DATE_RANGE_OUT_OF_CALENDAR_RANGE = "date_range_out_of_calendar_range"
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
) -> BaseStrategy:
    """Raise ``InvalidJobPayloadError(UNKNOWN_STRATEGY_ID)`` unless
    ``strategy_id`` resolves against the strategy registry (D-22); return the
    resolved strategy (callers that only validate may ignore it)."""

    registry = build_default_strategy_registry(settings)
    try:
        return registry.resolve(strategy_id)
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


def require_date_range_within_horizon(
    settings: Settings,
    clock: Callable[[], datetime],
    from_date: date,
    to_date: date,
    *,
    job_type: str,
) -> None:
    """D-24: ``sync-market-sessions`` may sync AHEAD. An inverted range first,
    then a ``to_date`` beyond the date of the N-th session after the
    exchange-local today (N = ``coverage_horizon_sessions``, clipped to the
    calendar library's last session) is ``to_date_beyond_coverage_horizon``.
    ``ingest-bars`` keeps ``require_date_range`` (bars cannot exist in the future)."""

    if from_date > to_date:
        raise InvalidJobPayloadError(
            job_type=job_type,
            reason=PayloadFieldRejection.FROM_DATE_AFTER_TO_DATE.value,
        )
    today = exchange_today(settings, clock)
    try:
        horizon_end = calendar_horizon_end(settings, today)
    except CalendarOutOfBoundsError as exc:
        raise InvalidJobPayloadError(
            job_type=job_type,
            reason=PayloadFieldRejection.DATE_RANGE_OUT_OF_CALENDAR_RANGE.value,
        ) from exc
    if to_date > horizon_end:
        raise InvalidJobPayloadError(
            job_type=job_type,
            reason=PayloadFieldRejection.TO_DATE_BEYOND_COVERAGE_HORIZON.value,
        )


def require_date_range_within_calendar(
    settings: Settings,
    from_date: date,
    to_date: date,
    *,
    job_type: str,
) -> None:
    """Reject a range that falls outside the exchange calendar's supported
    window (``first_session``..``last_session``). Job types whose handler
    resolves sessions from the calendar (``sync-market-sessions``) call this
    after ``require_date_range`` so an out-of-window range is a typed
    submit-time rejection (D-25) rather than a run-time
    ``DateOutOfBounds``/``OverflowError`` on the Job."""

    calendar = get_calendar(settings.market_data.calendar.exchange)
    if from_date < calendar.first_session.date() or to_date > calendar.last_session.date():
        raise InvalidJobPayloadError(
            job_type=job_type,
            reason=PayloadFieldRejection.DATE_RANGE_OUT_OF_CALENDAR_RANGE.value,
        )


def evaluation_session_default(
    settings: Settings, clock: Callable[[], datetime]
) -> date | None:
    """Read-only ``submission_defaults()`` helper (D-24): the EVALUATION
    candidate session -- the latest calendar-completed persisted session at
    the injected clock -- or ``None`` when the calendar does not cover it
    (``unknown(calendar_data_unavailable)``). Never the latest session that
    merely has bars. Never called from inside ``validate_payload`` (P19 D-08)."""

    with session_scope(settings) as session:
        candidate = evaluation_candidate_session(session, now=clock(), settings=settings)
    return candidate if isinstance(candidate, date) else None


def format_symbols_default(symbols: Iterable[str]) -> str:
    """Render a normalized ``symbols`` default as the comma-separated string
    the console form field expects (UI-SPEC)."""

    normalized = sorted({symbol.strip().upper() for symbol in symbols if symbol.strip()})
    return ",".join(normalized)


__all__ = [
    "MAX_SYMBOLS",
    "SYMBOL_PATTERN",
    "PayloadFieldRejection",
    "evaluation_session_default",
    "exchange_today",
    "format_symbols_default",
    "map_validation_error",
    "normalize_symbols",
    "parse_iso_date",
    "require_date_range",
    "require_date_range_within_calendar",
    "require_date_range_within_horizon",
    "require_registered_strategy",
    "require_trading_session_not_future",
]


def require_active_paper_strategy(
    settings: Settings,
    strategy_id: str,
    *,
    job_type: str,
    conflict_enum: type[StrEnum],
    session: Any = None,
) -> None:
    """D-03: refuse a non-owner (``strategy_not_active_paper_strategy``) or an
    ownerless account (``no_active_paper_strategy``) with a typed conflict.

    ``conflict_enum`` is the raising spec's own closed conflict enum, so a code
    outside that set cannot be raised. Pass ``session`` to evaluate inside the
    Job-insert transaction (SER admission re-check); the predicate is the same
    ``ownership_block_for`` callable either way and re-reads ownership each time.
    """

    block = ownership_block_for(strategy_id, settings=settings, session=session)
    if block is not None:
        raise JobSubmissionConflictError(
            job_type=job_type,
            code=conflict_enum(block.value).value,
            detail={"strategy_id": strategy_id},
        )
