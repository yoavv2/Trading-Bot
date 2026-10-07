"""Public input contract for the ``ingest-tiingo-bars`` Job type (research mode).

Strict and typed like ``ingest-bars``: unknown keys, missing fields, malformed dates,
inverted ranges, a future ``to_date`` and every asset normalization failure raise a
typed ``InvalidJobPayloadError`` with a closed reason. Nothing is defaulted inside
``validate_payload``; the Job payload records exactly what will be downloaded.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from trading_platform.core.settings import Settings
from trading_platform.jobs.handlers.payload_fields import (
    map_validation_error,
    normalize_symbols,
    parse_iso_date,
    require_date_range,
)
from trading_platform.jobs.registry import (
    ConsoleSubmission,
    InvalidJobPayloadError,
    JobCancellationMode,
)

INGEST_TIINGO_BARS_JOB_TYPE = "ingest-tiingo-bars"


class IngestTiingoBarsPayloadRejection(StrEnum):
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


class _IngestTiingoBarsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_date: date
    to_date: date
    assets: list[str]

    @field_validator("from_date", "to_date", mode="before")
    @classmethod
    def _parse_date(cls, value: Any) -> date:
        return parse_iso_date(value)

    @field_validator("assets", mode="before")
    @classmethod
    def _normalize_assets(cls, value: Any) -> list[str]:
        return normalize_symbols(value)


class IngestTiingoBarsSubmissionSpec:
    job_type = INGEST_TIINGO_BARS_JOB_TYPE
    description = "Download daily bars from Tiingo for explicit assets and an explicit date range (research)."
    cancellation_mode = JobCancellationMode.STEP_BOUNDARY
    console_submission = ConsoleSubmission.API_ONLY

    def __init__(self, settings: Settings, *, clock: Callable[[], datetime] | None = None) -> None:
        self._settings = settings
        self._clock = clock or _default_clock

    def validate_payload(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        try:
            parsed = _IngestTiingoBarsPayload.model_validate(dict(payload))
        except ValidationError as exc:
            reason = IngestTiingoBarsPayloadRejection(map_validation_error(exc).value)
            raise InvalidJobPayloadError(job_type=INGEST_TIINGO_BARS_JOB_TYPE, reason=reason.value) from exc

        require_date_range(
            self._settings,
            self._clock,
            parsed.from_date,
            parsed.to_date,
            job_type=INGEST_TIINGO_BARS_JOB_TYPE,
        )
        return {
            "from_date": parsed.from_date.isoformat(),
            "to_date": parsed.to_date.isoformat(),
            "assets": list(parsed.assets),
        }

    def submission_defaults(self) -> dict[str, str] | None:
        return None
