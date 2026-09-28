"""Tests for ``trading_platform.jobs.handlers.payload_fields`` (D-21..D-25).

Field-validator behavior (``parse_iso_date``/``normalize_symbols``) is
exercised through a small local pydantic model, following the
``backtest_submission.py``/``test_backtest_job_type.py`` precedent, so the
precedence rules in ``map_validation_error`` are proven against a real
``ValidationError`` rather than a hand-built one.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError, field_validator

from trading_platform.core.settings import load_settings
from trading_platform.jobs.handlers import payload_fields as pf
from trading_platform.jobs.registry import InvalidJobPayloadError
from trading_platform.services.calendar import get_calendar


class _SampleSymbolsPayload(BaseModel):
    """Minimal payload model wiring the shared validators, mirroring how a
    real Phase 20 spec's pydantic model would use them."""

    model_config = ConfigDict(extra="forbid")

    strategy_id: StrictStr = Field(min_length=1, max_length=64)
    as_of_session: date
    symbols: list[str]

    @field_validator("as_of_session", mode="before")
    @classmethod
    def _parse_as_of(cls, value: Any) -> date:
        return pf.parse_iso_date(value)

    @field_validator("symbols", mode="before")
    @classmethod
    def _normalize(cls, value: Any) -> list[str]:
        return pf.normalize_symbols(value)


def _clock_2026_01_06() -> datetime:
    """2026-01-06T03:00Z == 2026-01-05 in America/New_York (XNYS)."""
    return datetime(2026, 1, 6, 3, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# PayloadFieldRejection: closed set
# ---------------------------------------------------------------------------


def test_payload_field_rejection_is_closed() -> None:
    assert {member.value for member in pf.PayloadFieldRejection} == {
        "unknown_payload_keys",
        "missing_required_field",
        "invalid_field_type",
        "invalid_date",
        "unknown_strategy_id",
        "as_of_session_in_future",
        "as_of_session_not_trading_session",
        "as_of_session_out_of_calendar_range",
        "from_date_after_to_date",
        "to_date_in_future",
        "date_range_out_of_calendar_range",
        "empty_symbols",
        "invalid_symbol",
        "too_many_symbols",
    }


def test_max_symbols_is_500() -> None:
    assert pf.MAX_SYMBOLS == 500


# ---------------------------------------------------------------------------
# normalize_symbols
# ---------------------------------------------------------------------------


def test_normalize_symbols_strips_uppercases_dedupes_sorts() -> None:
    assert pf.normalize_symbols([" spy", "aapl", "SPY", ""]) == ["AAPL", "SPY"]


def test_normalize_symbols_rejects_non_list() -> None:
    with pytest.raises(ValidationError) as exc_info:
        _SampleSymbolsPayload.model_validate(
            {"strategy_id": "x", "as_of_session": "2024-01-02", "symbols": "AAPL"}
        )
    assert pf.map_validation_error(exc_info.value) == pf.PayloadFieldRejection.INVALID_FIELD_TYPE


def test_normalize_symbols_rejects_non_str_member() -> None:
    with pytest.raises(ValidationError) as exc_info:
        _SampleSymbolsPayload.model_validate(
            {"strategy_id": "x", "as_of_session": "2024-01-02", "symbols": ["AAPL", 1]}
        )
    assert pf.map_validation_error(exc_info.value) == pf.PayloadFieldRejection.INVALID_FIELD_TYPE


def test_normalize_symbols_empty_result_raises() -> None:
    with pytest.raises(ValidationError) as exc_info:
        _SampleSymbolsPayload.model_validate(
            {"strategy_id": "x", "as_of_session": "2024-01-02", "symbols": [" ", ""]}
        )
    assert pf.map_validation_error(exc_info.value) == pf.PayloadFieldRejection.EMPTY_SYMBOLS


def test_normalize_symbols_rejects_bad_ticker() -> None:
    with pytest.raises(ValidationError) as exc_info:
        _SampleSymbolsPayload.model_validate(
            {"strategy_id": "x", "as_of_session": "2024-01-02", "symbols": ["$$BAD"]}
        )
    assert pf.map_validation_error(exc_info.value) == pf.PayloadFieldRejection.INVALID_SYMBOL


def test_normalize_symbols_rejects_too_many() -> None:
    symbols = [f"SYM{i}" for i in range(501)]
    with pytest.raises(ValidationError) as exc_info:
        _SampleSymbolsPayload.model_validate(
            {"strategy_id": "x", "as_of_session": "2024-01-02", "symbols": symbols}
        )
    assert pf.map_validation_error(exc_info.value) == pf.PayloadFieldRejection.TOO_MANY_SYMBOLS


# ---------------------------------------------------------------------------
# map_validation_error precedence
# ---------------------------------------------------------------------------


def test_map_validation_error_unknown_payload_keys() -> None:
    with pytest.raises(ValidationError) as exc_info:
        _SampleSymbolsPayload.model_validate(
            {
                "strategy_id": "x",
                "as_of_session": "2024-01-02",
                "symbols": ["AAPL"],
                "extra": 1,
            }
        )
    assert pf.map_validation_error(exc_info.value) == pf.PayloadFieldRejection.UNKNOWN_PAYLOAD_KEYS


def test_map_validation_error_missing_required_field() -> None:
    with pytest.raises(ValidationError) as exc_info:
        _SampleSymbolsPayload.model_validate({"strategy_id": "x", "symbols": ["AAPL"]})
    assert pf.map_validation_error(exc_info.value) == pf.PayloadFieldRejection.MISSING_REQUIRED_FIELD


def test_map_validation_error_invalid_date() -> None:
    with pytest.raises(ValidationError) as exc_info:
        _SampleSymbolsPayload.model_validate(
            {"strategy_id": "x", "as_of_session": "2024/01/02", "symbols": ["AAPL"]}
        )
    assert pf.map_validation_error(exc_info.value) == pf.PayloadFieldRejection.INVALID_DATE


def test_map_validation_error_residual_falls_back_to_invalid_field_type() -> None:
    # A wrong-typed strategy_id (StrictStr) produces a native pydantic
    # "string_type" error -- not extra_forbidden, not missing, and not a
    # PayloadFieldRejection value -- exercising map_validation_error's final
    # fallback branch.
    with pytest.raises(ValidationError) as exc_info:
        _SampleSymbolsPayload.model_validate(
            {"strategy_id": 123, "as_of_session": "2024-01-02", "symbols": ["AAPL"]}
        )
    assert pf.map_validation_error(exc_info.value) == pf.PayloadFieldRejection.INVALID_FIELD_TYPE


# ---------------------------------------------------------------------------
# require_trading_session_not_future (D-21)
# ---------------------------------------------------------------------------


def test_require_trading_session_not_future_accepts_exchange_local_today() -> None:
    settings = load_settings()
    pf.require_trading_session_not_future(
        settings, _clock_2026_01_06, date(2026, 1, 5), job_type="test"
    )


def test_require_trading_session_not_future_rejects_future_date() -> None:
    settings = load_settings()
    with pytest.raises(InvalidJobPayloadError) as exc_info:
        pf.require_trading_session_not_future(
            settings, _clock_2026_01_06, date(2026, 1, 6), job_type="test"
        )
    assert exc_info.value.reason == pf.PayloadFieldRejection.AS_OF_SESSION_IN_FUTURE.value


def test_require_trading_session_not_future_rejects_non_session_date() -> None:
    settings = load_settings()
    with pytest.raises(InvalidJobPayloadError) as exc_info:
        # 2026-01-03 is a Saturday -- not a trading session.
        pf.require_trading_session_not_future(
            settings, _clock_2026_01_06, date(2026, 1, 3), job_type="test"
        )
    assert (
        exc_info.value.reason
        == pf.PayloadFieldRejection.AS_OF_SESSION_NOT_TRADING_SESSION.value
    )


@pytest.mark.parametrize("out_of_window", [date(2000, 1, 3), date(1, 1, 1)])
def test_require_trading_session_not_future_rejects_out_of_calendar_range(
    out_of_window: date,
) -> None:
    """CR-A-01: dates outside exchange_calendars' rolling window raise
    ``DateOutOfBounds``/``OverflowError`` inside the calendar; the helper must
    surface a typed rejection instead."""

    settings = load_settings()
    with pytest.raises(InvalidJobPayloadError) as exc_info:
        pf.require_trading_session_not_future(
            settings, _clock_2026_01_06, out_of_window, job_type="test"
        )
    assert (
        exc_info.value.reason
        == pf.PayloadFieldRejection.AS_OF_SESSION_OUT_OF_CALENDAR_RANGE.value
    )


# ---------------------------------------------------------------------------
# require_date_range
# ---------------------------------------------------------------------------


def test_require_date_range_rejects_inverted_range() -> None:
    settings = load_settings()
    with pytest.raises(InvalidJobPayloadError) as exc_info:
        pf.require_date_range(
            settings, _clock_2026_01_06, date(2026, 1, 5), date(2026, 1, 1), job_type="test"
        )
    assert exc_info.value.reason == pf.PayloadFieldRejection.FROM_DATE_AFTER_TO_DATE.value


def test_require_date_range_rejects_future_to_date() -> None:
    settings = load_settings()
    with pytest.raises(InvalidJobPayloadError) as exc_info:
        pf.require_date_range(
            settings, _clock_2026_01_06, date(2026, 1, 1), date(2026, 1, 6), job_type="test"
        )
    assert exc_info.value.reason == pf.PayloadFieldRejection.TO_DATE_IN_FUTURE.value


def test_require_date_range_accepts_valid_range() -> None:
    settings = load_settings()
    pf.require_date_range(
        settings, _clock_2026_01_06, date(2026, 1, 1), date(2026, 1, 5), job_type="test"
    )


def test_require_date_range_within_calendar_accepts_window_edges() -> None:
    settings = load_settings()
    calendar = get_calendar(settings.market_data.calendar.exchange)
    pf.require_date_range_within_calendar(
        settings,
        calendar.first_session.date(),
        calendar.first_session.date(),
        job_type="test",
    )


@pytest.mark.parametrize("from_date", [date(2000, 1, 3), date(1, 1, 1)])
def test_require_date_range_within_calendar_rejects_pre_window_from_date(
    from_date: date,
) -> None:
    """WR-A-03: a pre-window ``from_date`` would raise ``DateOutOfBounds`` /
    ``OverflowError`` inside the run-time calendar sync; reject it at submit."""

    settings = load_settings()
    with pytest.raises(InvalidJobPayloadError) as exc_info:
        pf.require_date_range_within_calendar(
            settings, from_date, date(2024, 1, 10), job_type="test"
        )
    assert (
        exc_info.value.reason
        == pf.PayloadFieldRejection.DATE_RANGE_OUT_OF_CALENDAR_RANGE.value
    )


# ---------------------------------------------------------------------------
# require_registered_strategy (D-22)
# ---------------------------------------------------------------------------


def test_require_registered_strategy_rejects_unknown_id() -> None:
    settings = load_settings()
    with pytest.raises(InvalidJobPayloadError) as exc_info:
        pf.require_registered_strategy(settings, "nope", job_type="test")
    assert exc_info.value.reason == pf.PayloadFieldRejection.UNKNOWN_STRATEGY_ID.value


def test_require_registered_strategy_accepts_known_id() -> None:
    settings = load_settings()
    pf.require_registered_strategy(settings, "trend_following_daily", job_type="test")


# ---------------------------------------------------------------------------
# format_symbols_default (UI-SPEC)
# ---------------------------------------------------------------------------


def test_format_symbols_default_normalizes_and_joins() -> None:
    assert pf.format_symbols_default(("spy", "AAPL")) == "AAPL,SPY"
