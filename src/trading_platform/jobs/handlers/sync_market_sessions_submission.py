"""SyncMarketSessionsSubmissionSpec: the public input contract for the
``sync-market-sessions`` Job type (OPS-05, D-25).

Validation is strict and typed: unknown payload keys, a missing required
field, a wrong-typed/malformed date, an inverted date range, and a
``to_date`` in the future, and a range outside the exchange calendar's
supported window each raise a typed ``InvalidJobPayloadError`` with
a stable, closed rejection reason. ``sync-market-sessions`` is its own Job
type with its own spec and handler -- neither this module nor its handler
carries a mode/behavior flag field (OPS-05). Nothing is ever defaulted
inside ``validate_payload`` -- the Job payload records exactly what will
run. ``submission_defaults`` is a separate, read-only method computed at
catalog-read time only, derived purely from the injected clock's
exchange-local date (no database read, never ``None``) -- it performs no
validation and never raises for a bad payload (there is none to validate).

Field-level parsing and the shared semantic checks are delegated to
``jobs/handlers/payload_fields.py`` (P19 D-08/D-09 precedent, generalized
in Phase 20).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from trading_platform.core.settings import Settings
from trading_platform.jobs.handlers.payload_fields import (
    exchange_today,
    map_validation_error,
    parse_iso_date,
    require_date_range_within_calendar,
    require_date_range_within_horizon,
)
from trading_platform.jobs.registry import InvalidJobPayloadError, JobCancellationMode

SYNC_MARKET_SESSIONS_JOB_TYPE = "sync-market-sessions"


class SyncMarketSessionsPayloadRejection(StrEnum):
    """Closed, stable set of machine-readable ``sync-market-sessions``
    payload rejection reasons. Each value equals the corresponding shared
    ``PayloadFieldRejection`` value. A parametrized test case exists for
    every value reachable through this spec's own payload;
    ``INVALID_FIELD_TYPE`` is kept in the closed set as
    ``map_validation_error``'s residual fallback (matching the shared
    vocabulary every Phase 20 spec draws from) even though it is not
    reachable here -- both payload fields (``from_date``/``to_date``) go
    through the shared ``parse_iso_date`` before-validator, which maps
    every wrong-typed or malformed value to ``INVALID_DATE`` instead (same
    as ``ingest-bars``/``backtest``'s own date fields; both reach
    ``INVALID_FIELD_TYPE`` only via a non-date field this spec does not
    have)."""

    UNKNOWN_PAYLOAD_KEYS = "unknown_payload_keys"
    MISSING_REQUIRED_FIELD = "missing_required_field"
    INVALID_FIELD_TYPE = "invalid_field_type"
    INVALID_DATE = "invalid_date"
    FROM_DATE_AFTER_TO_DATE = "from_date_after_to_date"
    TO_DATE_BEYOND_COVERAGE_HORIZON = "to_date_beyond_coverage_horizon"
    DATE_RANGE_OUT_OF_CALENDAR_RANGE = "date_range_out_of_calendar_range"


def _default_clock() -> datetime:
    return datetime.now(UTC)


class _SyncMarketSessionsPayload(BaseModel):
    """Shape validation only -- semantic checks (date ordering, future-date
    rejection) happen after this model validates, inside
    ``SyncMarketSessionsSubmissionSpec.validate_payload``."""

    model_config = ConfigDict(extra="forbid")

    from_date: date
    to_date: date

    @field_validator("from_date", "to_date", mode="before")
    @classmethod
    def _parse_date(cls, value: Any) -> date:
        return parse_iso_date(value)


class SyncMarketSessionsSubmissionSpec:
    """Transport-neutral validation and normalization for the
    ``sync-market-sessions`` public Job type (``JobSubmissionSpec``
    protocol)."""

    job_type = SYNC_MARKET_SESSIONS_JOB_TYPE
    description = "Upsert exchange trading-session rows for an explicit date range."
    cancellation_mode = JobCancellationMode.STEP_BOUNDARY

    def __init__(self, settings: Settings, *, clock: Callable[[], datetime] | None = None) -> None:
        self._settings = settings
        self._clock = clock or _default_clock

    def validate_payload(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        try:
            parsed = _SyncMarketSessionsPayload.model_validate(dict(payload))
        except ValidationError as exc:
            reason = SyncMarketSessionsPayloadRejection(map_validation_error(exc).value)
            raise InvalidJobPayloadError(
                job_type=SYNC_MARKET_SESSIONS_JOB_TYPE, reason=reason.value
            ) from exc

        from_date = parsed.from_date
        to_date = parsed.to_date

        require_date_range_within_horizon(
            self._settings,
            self._clock,
            from_date,
            to_date,
            job_type=SYNC_MARKET_SESSIONS_JOB_TYPE,
        )
        require_date_range_within_calendar(
            self._settings,
            from_date,
            to_date,
            job_type=SYNC_MARKET_SESSIONS_JOB_TYPE,
        )

        return {
            "from_date": from_date.isoformat(),
            "to_date": to_date.isoformat(),
        }

    def submission_defaults(self) -> dict[str, str]:
        """Console pre-fill, computed at read time from the injected clock's
        exchange-local date. Never reads the database, never ``None``."""

        to_date = exchange_today(self._settings, self._clock)
        lookback_days = self._settings.market_data.ingest.default_lookback_days
        from_date = to_date - timedelta(days=lookback_days)
        return {
            "from_date": from_date.isoformat(),
            "to_date": to_date.isoformat(),
        }
