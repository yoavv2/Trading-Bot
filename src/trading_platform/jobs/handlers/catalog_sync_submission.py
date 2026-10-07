"""Public input contract for the ``catalog-sync`` Job type (research mode)."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, StrictBool, ValidationError

from trading_platform.core.settings import Settings
from trading_platform.jobs.handlers.payload_fields import map_validation_error
from trading_platform.jobs.registry import (
    ConsoleSubmission,
    InvalidJobPayloadError,
    JobCancellationMode,
)

CATALOG_SYNC_JOB_TYPE = "catalog-sync"


class CatalogSyncPayloadRejection(StrEnum):
    UNKNOWN_PAYLOAD_KEYS = "unknown_payload_keys"
    MISSING_REQUIRED_FIELD = "missing_required_field"
    INVALID_FIELD_TYPE = "invalid_field_type"


class _CatalogSyncPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    include_names: StrictBool


class CatalogSyncSubmissionSpec:
    job_type = CATALOG_SYNC_JOB_TYPE
    description = "Refresh the research asset catalog from public ticker and name directories."
    cancellation_mode = JobCancellationMode.STEP_BOUNDARY
    console_submission = ConsoleSubmission.API_ONLY

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def validate_payload(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        try:
            parsed = _CatalogSyncPayload.model_validate(dict(payload))
        except ValidationError as exc:
            reason = CatalogSyncPayloadRejection(map_validation_error(exc).value)
            raise InvalidJobPayloadError(job_type=CATALOG_SYNC_JOB_TYPE, reason=reason.value) from exc
        return {"include_names": parsed.include_names}

    def submission_defaults(self) -> dict[str, Any] | None:
        return None
