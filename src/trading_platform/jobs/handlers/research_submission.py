"""Public input contracts for the research study Job types (research mode only):
``research-freeze``, ``research-backtest`` and ``research-evaluate``.

These Jobs are submitted by the study service as a dependency graph, never typed by an
operator; the specs still validate strictly so a hand-submitted payload cannot smuggle
an unknown key or a malformed id.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, StrictBool, StrictStr, ValidationError

from trading_platform.core.settings import Settings
from trading_platform.jobs.handlers.payload_fields import map_validation_error
from trading_platform.jobs.registry import (
    ConsoleSubmission,
    InvalidJobPayloadError,
    JobCancellationMode,
)

RESEARCH_FREEZE_JOB_TYPE = "research-freeze"
RESEARCH_BACKTEST_JOB_TYPE = "research-backtest"
RESEARCH_EVALUATE_JOB_TYPE = "research-evaluate"


class ResearchPayloadRejection(StrEnum):
    UNKNOWN_PAYLOAD_KEYS = "unknown_payload_keys"
    MISSING_REQUIRED_FIELD = "missing_required_field"
    INVALID_FIELD_TYPE = "invalid_field_type"


class _FreezePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    study_revision_id: uuid.UUID


class _BacktestPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    study_revision_id: uuid.UUID
    strategy_version_id: uuid.UUID | None
    asset: StrictStr
    window_role: Literal["development", "validation", "final_test"]
    rerun_of: uuid.UUID | None = None


class _EvaluatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    study_revision_id: uuid.UUID
    scope: Literal["initial", "final_test"]
    is_rerun: StrictBool = False


class _ResearchSubmissionSpec:
    cancellation_mode = JobCancellationMode.STEP_BOUNDARY
    console_submission = ConsoleSubmission.API_ONLY
    job_type = ""
    description = ""
    model: type[BaseModel] = _FreezePayload

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def validate_payload(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        try:
            parsed = self.model.model_validate(dict(payload))
        except ValidationError as exc:
            reason = ResearchPayloadRejection(map_validation_error(exc).value)
            raise InvalidJobPayloadError(job_type=self.job_type, reason=reason.value) from exc
        return parsed.model_dump(mode="json")

    def submission_defaults(self) -> dict[str, Any] | None:
        return None


class ResearchFreezeSubmissionSpec(_ResearchSubmissionSpec):
    job_type = RESEARCH_FREEZE_JOB_TYPE
    description = "Integrity-check and freeze the downloaded inputs of a study revision (research)."
    model = _FreezePayload


class ResearchBacktestSubmissionSpec(_ResearchSubmissionSpec):
    job_type = RESEARCH_BACKTEST_JOB_TYPE
    description = "One single-asset research backtest (strategy version or benchmark) on one window."
    model = _BacktestPayload


class ResearchEvaluateSubmissionSpec(_ResearchSubmissionSpec):
    job_type = RESEARCH_EVALUATE_JOB_TYPE
    description = "Metrics, evidence, ranking and verdict (or the final-test outcome) of a study revision."
    model = _EvaluatePayload
