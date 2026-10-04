"""IngestBarsSubmissionSpec: the public input contract for the
``ingest-bars`` Job type (OPS-05, D-24, D-25).

Validation is strict and typed: unknown payload keys, a missing required
field, a wrong-typed field, a malformed date, an inverted date range, a
``to_date`` in the future, and every ``symbols`` normalization/whitelist
failure each raise a typed ``InvalidJobPayloadError`` with a stable, closed
rejection reason. ``ingest-bars`` is its own Job type with its own spec and
handler -- neither this module nor its handler carries a mode/behavior flag
field (OPS-05). Nothing is ever defaulted inside ``validate_payload`` --
the Job payload records exactly what will run. ``submission_defaults`` is a
separate, read-only method computed at catalog-read time only; it performs
no validation and never raises for a bad payload (there is none to
validate).

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
    evaluation_session_default,
    format_symbols_default,
    map_validation_error,
    normalize_symbols,
    parse_iso_date,
    require_date_range,
)
from trading_platform.jobs.registry import InvalidJobPayloadError, JobCancellationMode

INGEST_BARS_JOB_TYPE = "ingest-bars"


class IngestBarsPayloadRejection(StrEnum):
    """Closed, stable set of machine-readable ``ingest-bars`` payload
    rejection reasons. One parametrized test case exists per value. Each
    value equals the corresponding shared ``PayloadFieldRejection`` value."""

    UNKNOWN_PAYLOAD_KEYS = "unknown_payload_keys"
    MISSING_REQUIRED_FIELD = "missing_required_field"
    INVALID_FIELD_TYPE = "invalid_field_type"
    INVALID_DATE = "invalid_date"
    FROM_DATE_AFTER_TO_DATE = "from_date_after_to_date"
    TO_DATE_IN_FUTURE = "to_date_in_future"
    EMPTY_SYMBOLS = "empty_symbols"
    INVALID_SYMBOL = "invalid_symbol"
    TOO_MANY_SYMBOLS = "too_many_symbols"


def _default_clock() -> datetime:
    return datetime.now(UTC)


class _IngestBarsPayload(BaseModel):
    """Shape validation only -- semantic checks (date ordering, future-date
    rejection) happen after this model validates, inside
    ``IngestBarsSubmissionSpec.validate_payload``."""

    model_config = ConfigDict(extra="forbid")

    from_date: date
    to_date: date
    symbols: list[str]

    @field_validator("from_date", "to_date", mode="before")
    @classmethod
    def _parse_date(cls, value: Any) -> date:
        return parse_iso_date(value)

    @field_validator("symbols", mode="before")
    @classmethod
    def _normalize_symbols(cls, value: Any) -> list[str]:
        return normalize_symbols(value)


class IngestBarsSubmissionSpec:
    """Transport-neutral validation and normalization for the
    ``ingest-bars`` public Job type (``JobSubmissionSpec`` protocol)."""

    job_type = INGEST_BARS_JOB_TYPE
    description = (
        "Ingest daily bars from Polygon for an explicit date range and symbol list."
    )
    cancellation_mode = JobCancellationMode.STEP_BOUNDARY

    def __init__(self, settings: Settings, *, clock: Callable[[], datetime] | None = None) -> None:
        self._settings = settings
        self._clock = clock or _default_clock

    def validate_payload(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        try:
            parsed = _IngestBarsPayload.model_validate(dict(payload))
        except ValidationError as exc:
            reason = IngestBarsPayloadRejection(map_validation_error(exc).value)
            raise InvalidJobPayloadError(
                job_type=INGEST_BARS_JOB_TYPE, reason=reason.value
            ) from exc

        from_date = parsed.from_date
        to_date = parsed.to_date

        require_date_range(
            self._settings,
            self._clock,
            from_date,
            to_date,
            job_type=INGEST_BARS_JOB_TYPE,
        )

        return {
            "from_date": from_date.isoformat(),
            "to_date": to_date.isoformat(),
            "symbols": list(parsed.symbols),
        }

    def submission_defaults(self) -> dict[str, str] | None:
        """D-24: console pre-fill, computed at read time. The window ends at the
        EVALUATION candidate session (never "latest session with bars");
        ``None`` when the calendar does not cover the clock's date."""

        latest = evaluation_session_default(self._settings, self._clock)
        if latest is None:
            return None

        lookback_days = self._settings.market_data.ingest.default_lookback_days
        from_date = latest - timedelta(days=lookback_days)
        return {
            "from_date": from_date.isoformat(),
            "to_date": latest.isoformat(),
            "symbols": format_symbols_default(self._settings.market_data.ingest.universe),
        }
