"""SyncSymbolMetadataSubmissionSpec: the public input contract for the
``sync-symbol-metadata`` Job type (OPS-05, D-25).

Validation is strict and typed: unknown payload keys, a missing required
field, a wrong-typed field, and every ``symbols`` normalization/whitelist
failure each raise a typed ``InvalidJobPayloadError`` with a stable, closed
rejection reason. ``sync-symbol-metadata`` is its own Job type with its own
spec and handler, independent of ``ingest-bars`` and ``sync-market-sessions``
-- neither this module nor its handler carries a mode/behavior flag field
(OPS-05). Nothing is ever defaulted inside ``validate_payload`` -- the Job
payload records exactly what will run. ``submission_defaults`` is a
separate, read-only method computed at catalog-read time only, derived
purely from the configured metadata universe (no database read, always
available) -- it performs no validation and never raises for a bad payload
(there is none to validate).

Field-level parsing and the shared semantic checks are delegated to
``jobs/handlers/payload_fields.py`` (P19 D-08/D-09 precedent, generalized
in Phase 20).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from trading_platform.core.settings import Settings
from trading_platform.jobs.handlers.payload_fields import (
    format_symbols_default,
    map_validation_error,
    normalize_symbols,
)
from trading_platform.jobs.registry import InvalidJobPayloadError, JobCancellationMode

SYNC_SYMBOL_METADATA_JOB_TYPE = "sync-symbol-metadata"


class SyncSymbolMetadataPayloadRejection(StrEnum):
    """Closed, stable set of machine-readable ``sync-symbol-metadata``
    payload rejection reasons. One parametrized test case exists per value.
    Each value equals the corresponding shared ``PayloadFieldRejection``
    value."""

    UNKNOWN_PAYLOAD_KEYS = "unknown_payload_keys"
    MISSING_REQUIRED_FIELD = "missing_required_field"
    INVALID_FIELD_TYPE = "invalid_field_type"
    EMPTY_SYMBOLS = "empty_symbols"
    INVALID_SYMBOL = "invalid_symbol"
    TOO_MANY_SYMBOLS = "too_many_symbols"


def _default_clock() -> datetime:
    return datetime.now(UTC)


class _SyncSymbolMetadataPayload(BaseModel):
    """Shape validation only -- ``symbols`` normalization/whitelist checks
    happen inside the shared ``normalize_symbols`` before-validator."""

    model_config = ConfigDict(extra="forbid")

    symbols: list[str]

    @field_validator("symbols", mode="before")
    @classmethod
    def _normalize_symbols(cls, value: Any) -> list[str]:
        return normalize_symbols(value)


class SyncSymbolMetadataSubmissionSpec:
    """Transport-neutral validation and normalization for the
    ``sync-symbol-metadata`` public Job type (``JobSubmissionSpec``
    protocol)."""

    job_type = SYNC_SYMBOL_METADATA_JOB_TYPE
    description = "Sync reference metadata for an explicit symbol list from Polygon."
    cancellation_mode = JobCancellationMode.STEP_BOUNDARY

    def __init__(self, settings: Settings, *, clock: Callable[[], datetime] | None = None) -> None:
        self._settings = settings
        self._clock = clock or _default_clock

    def validate_payload(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        try:
            parsed = _SyncSymbolMetadataPayload.model_validate(dict(payload))
        except ValidationError as exc:
            reason = SyncSymbolMetadataPayloadRejection(map_validation_error(exc).value)
            raise InvalidJobPayloadError(
                job_type=SYNC_SYMBOL_METADATA_JOB_TYPE, reason=reason.value
            ) from exc

        return {"symbols": list(parsed.symbols)}

    def submission_defaults(self) -> dict[str, str] | None:
        """Console pre-fill, computed at read time from the configured
        metadata universe. Never reads the database -- always available."""

        return {"symbols": format_symbols_default(self._settings.market_data.metadata.universe)}
