"""Public input contracts of the ``signals-daily`` and ``signals-replay`` Job types
(research mode only).

``book`` names a configuration file in ``config/signals/``; ``session_date`` is the
session to run up to (``null``: the latest closed session); ``ingest`` says whether the
run downloads bars first. Everything else about the run is fixed by the book.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, StrictBool, StrictStr, ValidationError, field_validator

from trading_platform.core.settings import Settings
from trading_platform.jobs.handlers.payload_fields import map_validation_error, parse_iso_date
from trading_platform.jobs.registry import (
    ConsoleSubmission,
    InvalidJobPayloadError,
    JobCancellationMode,
)
from trading_platform.services.signals.publication import BOOK_NAME_PATTERN

SIGNALS_DAILY_JOB_TYPE = "signals-daily"


class SignalsDailyPayloadRejection(StrEnum):
    UNKNOWN_PAYLOAD_KEYS = "unknown_payload_keys"
    MISSING_REQUIRED_FIELD = "missing_required_field"
    INVALID_FIELD_TYPE = "invalid_field_type"
    INVALID_DATE = "invalid_date"
    INVALID_BOOK_NAME = "invalid_book_name"


class _Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    book: StrictStr
    session_date: date | None = None
    ingest: StrictBool = True

    @field_validator("session_date", mode="before")
    @classmethod
    def _session_date(cls, value: Any) -> date | None:
        return None if value is None else parse_iso_date(value)


class SignalsDailySubmissionSpec:
    job_type = SIGNALS_DAILY_JOB_TYPE
    description = "Run a signal book up to one session: bars, integrity, signals and publication decisions."
    cancellation_mode = JobCancellationMode.STEP_BOUNDARY
    console_submission = ConsoleSubmission.API_ONLY

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def validate_payload(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        try:
            parsed = _Payload.model_validate(dict(payload))
        except ValidationError as exc:
            reason = SignalsDailyPayloadRejection(map_validation_error(exc).value)
            raise InvalidJobPayloadError(job_type=self.job_type, reason=reason.value) from exc
        if not BOOK_NAME_PATTERN.fullmatch(parsed.book):
            raise InvalidJobPayloadError(
                job_type=self.job_type, reason=SignalsDailyPayloadRejection.INVALID_BOOK_NAME.value
            )
        return parsed.model_dump(mode="json")

    def submission_defaults(self) -> dict[str, Any] | None:
        return None


SIGNALS_REPLAY_JOB_TYPE = "signals-replay"


class _ReplayPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    book: StrictStr


class SignalsReplaySubmissionSpec:
    job_type = SIGNALS_REPLAY_JOB_TYPE
    description = "Replay a signal book from its kept inputs and report every difference from the record."
    cancellation_mode = JobCancellationMode.QUEUED_ONLY
    console_submission = ConsoleSubmission.API_ONLY

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def validate_payload(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        try:
            parsed = _ReplayPayload.model_validate(dict(payload))
        except ValidationError as exc:
            reason = SignalsDailyPayloadRejection(map_validation_error(exc).value)
            raise InvalidJobPayloadError(job_type=self.job_type, reason=reason.value) from exc
        if not BOOK_NAME_PATTERN.fullmatch(parsed.book):
            raise InvalidJobPayloadError(
                job_type=self.job_type, reason=SignalsDailyPayloadRejection.INVALID_BOOK_NAME.value
            )
        return parsed.model_dump(mode="json")

    def submission_defaults(self) -> dict[str, Any] | None:
        return None
