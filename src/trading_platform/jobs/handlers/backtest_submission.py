"""BacktestSubmissionSpec: the public input contract for the ``backtest`` Job
type (D-08, D-09, D-10).

Validation is strict and typed (D-09): unknown payload keys, a missing
required field, a wrong-typed/blank ``strategy_id``, a malformed date, an
unregistered ``strategy_id``, an inverted date range, and a ``to_date`` in
the future (judged against the exchange-local date, never the host's local
date -- see ``_default_clock``/``validate_payload``) each raise a typed
``InvalidJobPayloadError`` with a stable, closed rejection reason. Nothing
is ever defaulted inside ``validate_payload`` (D-08) -- the Job payload
records exactly what will run. ``submission_defaults`` (D-10) is a
separate, read-only method computed at catalog-read time only; it performs
no validation and never raises for a bad payload (there is none to
validate).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError, field_validator

from trading_platform.core.settings import Settings
from trading_platform.db.session import session_scope
from trading_platform.jobs.registry import InvalidJobPayloadError, JobCancellationMode
from trading_platform.services.calendar import get_calendar
from trading_platform.services.market_data_access import latest_completed_session
from trading_platform.strategies.registry import UnknownStrategyError
from trading_platform.strategies.registry import (
    build_default_registry as build_default_strategy_registry,
)

BACKTEST_JOB_TYPE = "backtest"

_ISO_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class BacktestPayloadRejection(StrEnum):
    """Closed, stable set of machine-readable ``backtest`` payload rejection
    reasons (D-09). One parametrized test case exists per value."""

    UNKNOWN_PAYLOAD_KEYS = "unknown_payload_keys"
    MISSING_REQUIRED_FIELD = "missing_required_field"
    INVALID_FIELD_TYPE = "invalid_field_type"
    INVALID_DATE = "invalid_date"
    UNKNOWN_STRATEGY_ID = "unknown_strategy_id"
    FROM_DATE_AFTER_TO_DATE = "from_date_after_to_date"
    TO_DATE_IN_FUTURE = "to_date_in_future"


def _default_clock() -> datetime:
    return datetime.now(UTC)


class _BacktestPayload(BaseModel):
    """Shape validation only -- semantic checks (registry lookup, date
    ordering, future-date rejection) happen after this model validates,
    inside ``BacktestSubmissionSpec.validate_payload``."""

    model_config = ConfigDict(extra="forbid")

    strategy_id: StrictStr = Field(min_length=1, max_length=64)
    from_date: date
    to_date: date

    @field_validator("strategy_id", mode="before")
    @classmethod
    def _strip_strategy_id(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("from_date", "to_date", mode="before")
    @classmethod
    def _parse_iso_date(cls, value: Any) -> date:
        if not isinstance(value, str) or not _ISO_DATE_PATTERN.match(value):
            raise ValueError("must be an ISO date string (YYYY-MM-DD)")
        return date.fromisoformat(value)


def _map_validation_error(exc: ValidationError) -> BacktestPayloadRejection:
    """Fixed precedence (D-09): extra keys, then missing fields, then a
    strategy_id-located error, else the remaining case is always a date
    shape problem (from_date/to_date are the only other fields)."""

    errors = exc.errors()
    if any(error["type"] == "extra_forbidden" for error in errors):
        return BacktestPayloadRejection.UNKNOWN_PAYLOAD_KEYS
    if any(error["type"] == "missing" for error in errors):
        return BacktestPayloadRejection.MISSING_REQUIRED_FIELD
    if any(error["loc"] and error["loc"][0] == "strategy_id" for error in errors):
        return BacktestPayloadRejection.INVALID_FIELD_TYPE
    return BacktestPayloadRejection.INVALID_DATE


class BacktestSubmissionSpec:
    """Transport-neutral validation and normalization for the ``backtest``
    public Job type (``JobSubmissionSpec`` protocol)."""

    job_type = BACKTEST_JOB_TYPE
    description = (
        "Run a historical backtest of a registered strategy over an explicit date range."
    )
    cancellation_mode = JobCancellationMode.STEP_BOUNDARY

    def __init__(self, settings: Settings, *, clock: Callable[[], datetime] | None = None) -> None:
        self._settings = settings
        self._clock = clock or _default_clock

    def validate_payload(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        try:
            parsed = _BacktestPayload.model_validate(dict(payload))
        except ValidationError as exc:
            reason = _map_validation_error(exc)
            raise InvalidJobPayloadError(job_type=BACKTEST_JOB_TYPE, reason=reason.value) from exc

        strategy_id = parsed.strategy_id
        from_date = parsed.from_date
        to_date = parsed.to_date

        strategy_registry = build_default_strategy_registry(self._settings)
        try:
            strategy_registry.resolve(strategy_id)
        except UnknownStrategyError as exc:
            raise InvalidJobPayloadError(
                job_type=BACKTEST_JOB_TYPE,
                reason=BacktestPayloadRejection.UNKNOWN_STRATEGY_ID.value,
            ) from exc

        if from_date > to_date:
            raise InvalidJobPayloadError(
                job_type=BACKTEST_JOB_TYPE,
                reason=BacktestPayloadRejection.FROM_DATE_AFTER_TO_DATE.value,
            )

        exchange_today = self._clock().astimezone(
            get_calendar(self._settings.market_data.calendar.exchange).tz
        ).date()
        if to_date > exchange_today:
            raise InvalidJobPayloadError(
                job_type=BACKTEST_JOB_TYPE,
                reason=BacktestPayloadRejection.TO_DATE_IN_FUTURE.value,
            )

        return {
            "strategy_id": strategy_id,
            "from_date": from_date.isoformat(),
            "to_date": to_date.isoformat(),
        }

    def submission_defaults(self) -> dict[str, str] | None:
        """D-10: console pre-fill, computed at read time. Returns ``None``
        when no completed session exists to derive a window from. Exceptions
        propagate (the catalog route is responsible for omitting this field
        on failure, per the catalog-resilience discretion note)."""

        with session_scope(self._settings) as session:
            latest = latest_completed_session(
                session, exchange=self._settings.market_data.calendar.exchange
            )
        if latest is None:
            return None

        lookback_days = self._settings.market_data.ingest.default_lookback_days
        from_date = latest - timedelta(days=lookback_days)
        return {"from_date": from_date.isoformat(), "to_date": latest.isoformat()}
