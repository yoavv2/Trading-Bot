"""RiskEvaluationSubmissionSpec: the public input contract for the
``risk-evaluation`` Job type (D-21, D-22, D-25).

Validation is strict and typed: unknown payload keys, a missing required
field, a wrong-typed/blank ``strategy_id``, a malformed ``as_of_session``,
an unregistered ``strategy_id``, an ``as_of_session`` in the future (judged
against the exchange-local date, never the host's local date), and an
``as_of_session`` that is not an actual exchange trading session each raise
a typed ``InvalidJobPayloadError`` with a stable, closed rejection reason.
Nothing is ever defaulted inside ``validate_payload`` -- the Job payload
records exactly what will run. ``submission_defaults`` is a separate,
read-only method computed at catalog-read time only; it performs no
validation and never raises for a bad payload (there is none to validate).

Field-level parsing and the shared semantic checks are delegated to
``jobs/handlers/payload_fields.py`` (P19 D-08/D-09 precedent, generalized
in Phase 20).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError, field_validator

from trading_platform.core.settings import Settings
from trading_platform.jobs.handlers.payload_fields import (
    latest_completed_session_default,
    map_validation_error,
    parse_iso_date,
    require_registered_strategy,
    require_trading_session_not_future,
)
from trading_platform.jobs.registry import InvalidJobPayloadError, JobCancellationMode

RISK_EVALUATION_JOB_TYPE = "risk-evaluation"


class RiskEvaluationPayloadRejection(StrEnum):
    """Closed, stable set of machine-readable ``risk-evaluation`` payload
    rejection reasons. One parametrized test case exists per value. Each
    value equals the corresponding shared ``PayloadFieldRejection`` value."""

    UNKNOWN_PAYLOAD_KEYS = "unknown_payload_keys"
    MISSING_REQUIRED_FIELD = "missing_required_field"
    INVALID_FIELD_TYPE = "invalid_field_type"
    INVALID_DATE = "invalid_date"
    UNKNOWN_STRATEGY_ID = "unknown_strategy_id"
    AS_OF_SESSION_IN_FUTURE = "as_of_session_in_future"
    AS_OF_SESSION_NOT_TRADING_SESSION = "as_of_session_not_trading_session"


def _default_clock() -> datetime:
    return datetime.now(UTC)


class _RiskEvaluationPayload(BaseModel):
    """Shape validation only -- semantic checks (registry lookup, future-date
    rejection, trading-session membership) happen after this model
    validates, inside ``RiskEvaluationSubmissionSpec.validate_payload``."""

    model_config = ConfigDict(extra="forbid")

    strategy_id: StrictStr = Field(min_length=1, max_length=64)
    as_of_session: date

    @field_validator("strategy_id", mode="before")
    @classmethod
    def _strip_strategy_id(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("as_of_session", mode="before")
    @classmethod
    def _parse_as_of_session(cls, value: Any) -> date:
        return parse_iso_date(value)


class RiskEvaluationSubmissionSpec:
    """Transport-neutral validation and normalization for the
    ``risk-evaluation`` public Job type (``JobSubmissionSpec`` protocol)."""

    job_type = RISK_EVALUATION_JOB_TYPE
    description = (
        "Evaluate a strategy's signals against risk limits for one explicit trading session."
    )
    cancellation_mode = JobCancellationMode.STEP_BOUNDARY

    def __init__(self, settings: Settings, *, clock: Callable[[], datetime] | None = None) -> None:
        self._settings = settings
        self._clock = clock or _default_clock

    def validate_payload(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        try:
            parsed = _RiskEvaluationPayload.model_validate(dict(payload))
        except ValidationError as exc:
            reason = RiskEvaluationPayloadRejection(map_validation_error(exc).value)
            raise InvalidJobPayloadError(
                job_type=RISK_EVALUATION_JOB_TYPE, reason=reason.value
            ) from exc

        strategy_id = parsed.strategy_id
        as_of_session = parsed.as_of_session

        require_registered_strategy(
            self._settings, strategy_id, job_type=RISK_EVALUATION_JOB_TYPE
        )
        require_trading_session_not_future(
            self._settings, self._clock, as_of_session, job_type=RISK_EVALUATION_JOB_TYPE
        )

        return {
            "strategy_id": strategy_id,
            "as_of_session": as_of_session.isoformat(),
        }

    def submission_defaults(self) -> dict[str, str] | None:
        """Console pre-fill, computed at read time. Returns ``None`` when no
        completed session exists to derive a value from."""

        latest = latest_completed_session_default(self._settings)
        if latest is None:
            return None
        return {"as_of_session": latest.isoformat()}
