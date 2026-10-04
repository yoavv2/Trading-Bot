"""PaperSessionSubmissionSpec: the public input contract for the
``paper-session`` Job type (D-01, D-03, D-19, D-23, D-28).

Validation is strict and typed: unknown payload keys, a missing required
field, a wrong-typed/blank ``strategy_id``, a malformed ``as_of_session``,
an unregistered ``strategy_id``, an ``as_of_session`` in the future (judged
against the exchange-local date, never the host's local date), an
``as_of_session`` that is not an actual exchange trading session, a
``risk_run_id`` that is not a well-formed UUID, and a ``risk_run_id`` that
does not reference a SUCCEEDED risk-evaluation run for the same strategy
and session each raise a typed ``InvalidJobPayloadError`` with a stable,
closed rejection reason. Nothing is ever defaulted inside
``validate_payload`` -- the Job payload records exactly what will run.
``submission_defaults`` is a separate, read-only method computed at
catalog-read time only; it performs no validation and never raises for a
bad payload (there is none to validate). ``risk_run_id`` is deliberately
NOT defaulted here (D-23) -- the console form always sends an explicit
``null`` when the operator leaves the field blank.

The ``paper-session`` Job type is cancellable only while queued (D-01):
it performs broker submission inside one opaque service call, so a
RUNNING Job of this type is never cancellable (``JobOrchestrationService``
rejects the request per D-02). It also declares a D-19 reconcile-first
retry prerequisite: a FAILED, outcome-uncertain ``paper-session`` Job may
only be retried after a newer SUCCEEDED ``reconciliation`` Job exists for
the same strategy.

Eligibility (D-23, COR-04): after the ownership check a paper session is accepted
only for the fresh evaluation session inside its execution window. The
closed 409 codes ``calendar_data_unavailable`` > ``historical_execution_rejected``
> ``evaluation_data_not_ready`` > ``outside_execution_window`` come from the
services-layer ``paper_execution_eligibility`` read, judged at the injected clock.
Research, backtests and evaluation of past sessions are never gated by it.

Provenance (D-25, PROV-01): as the LAST validation step the risk run that will be
used (the pinned ``risk_run_id``, else the latest eligible one) has its stored
evaluation input manifest verified against the current source data and signal
settings. A mismatch is a typed 409 ``evaluation_data_changed`` (data) or
``strategy_settings_changed`` (signal settings); no eligible run, or a run with
no manifest (evaluated before 20.1-06), is ``evaluation_data_not_ready``. Cash,
positions, open orders and risk limits are NOT provenance (D-26): they are
re-checked fresh at execution. The resolved run id is never written into the
normalized payload.

Ownership (D-03, PAPER-01): after every payload check, a strategy that is not
the active paper strategy is refused with a typed ``JobSubmissionConflictError``
(HTTP 409 ``strategy_not_active_paper_strategy``; ``no_active_paper_strategy``
when no strategy owns the account). The check is repeated inside the Job-insert
transaction under the ownership singleton lock (``check_admission``) and again
at run time before every broker action.

Field-level parsing and the shared semantic checks are delegated to
``jobs/handlers/payload_fields.py`` (P19 D-08/D-09 precedent, generalized
in Phase 20).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError, field_validator

from trading_platform.core.settings import Settings
from trading_platform.db.session import session_scope
from trading_platform.jobs.handlers.payload_fields import (
    evaluation_session_default,
    map_validation_error,
    parse_iso_date,
    require_active_paper_strategy,
    require_registered_strategy,
    require_trading_session_not_future,
)
from trading_platform.jobs.registry import (
    InvalidJobPayloadError,
    JobCancellationMode,
    JobSubmissionConflictError,
)
from trading_platform.services.active_paper_strategy import lock_active_paper_strategy_shared
from trading_platform.services.calendar_facts import (
    EligibilityRejection,
    paper_execution_eligibility,
)
from trading_platform.services.evaluation_manifest import ManifestVerificationStatus
from trading_platform.services.risk import (
    is_eligible_risk_run,
    latest_eligible_risk_run_id,
    verify_risk_run_manifest,
)

PAPER_SESSION_JOB_TYPE = "paper-session"


class PaperSessionPayloadRejection(StrEnum):
    """Closed, stable set of machine-readable ``paper-session`` payload
    rejection reasons. One parametrized test case exists per value. The six
    shared values equal the corresponding shared ``PayloadFieldRejection``
    value; ``invalid_risk_run_id``/``risk_run_not_eligible`` are specific to
    this type's optional pinned risk run (D-23)."""

    UNKNOWN_PAYLOAD_KEYS = "unknown_payload_keys"
    MISSING_REQUIRED_FIELD = "missing_required_field"
    INVALID_FIELD_TYPE = "invalid_field_type"
    INVALID_DATE = "invalid_date"
    UNKNOWN_STRATEGY_ID = "unknown_strategy_id"
    AS_OF_SESSION_IN_FUTURE = "as_of_session_in_future"
    AS_OF_SESSION_NOT_TRADING_SESSION = "as_of_session_not_trading_session"
    AS_OF_SESSION_OUT_OF_CALENDAR_RANGE = "as_of_session_out_of_calendar_range"
    INVALID_RISK_RUN_ID = "invalid_risk_run_id"
    RISK_RUN_NOT_ELIGIBLE = "risk_run_not_eligible"


class PaperSessionSubmitConflict(StrEnum):
    """Closed set of state-conflict codes (HTTP 409) ``paper-session`` submission
    can raise via ``JobSubmissionConflictError`` (D-03, D-23, D-25): the two ownership
    refusals, the four execution-eligibility refusals and the two evaluation
    provenance refusals; later plans extend it. One parametrized test case exists
    per value."""

    NO_ACTIVE_PAPER_STRATEGY = "no_active_paper_strategy"
    STRATEGY_NOT_ACTIVE_PAPER_STRATEGY = "strategy_not_active_paper_strategy"
    HISTORICAL_EXECUTION_REJECTED = "historical_execution_rejected"
    OUTSIDE_EXECUTION_WINDOW = "outside_execution_window"
    EVALUATION_DATA_NOT_READY = "evaluation_data_not_ready"
    CALENDAR_DATA_UNAVAILABLE = "calendar_data_unavailable"
    EVALUATION_DATA_CHANGED = "evaluation_data_changed"
    STRATEGY_SETTINGS_CHANGED = "strategy_settings_changed"


def _default_clock() -> datetime:
    return datetime.now(UTC)


class _PaperSessionPayload(BaseModel):
    """Shape validation only -- semantic checks (registry lookup, future-date
    rejection, trading-session membership, risk-run eligibility) happen
    after this model validates, inside
    ``PaperSessionSubmissionSpec.validate_payload``.

    ``risk_run_id`` has NO default (D-23): the key must always be present in
    the payload, even when its value is ``None`` -- a payload that omits the
    key entirely is rejected as ``missing_required_field``, exactly like
    ``strategy_id``/``as_of_session``.
    """

    model_config = ConfigDict(extra="forbid")

    strategy_id: StrictStr = Field(min_length=1, max_length=64)
    as_of_session: date
    risk_run_id: StrictStr | None

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

    @field_validator("risk_run_id", mode="before")
    @classmethod
    def _strip_risk_run_id(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value


class PaperSessionSubmissionSpec:
    """Transport-neutral validation and normalization for the
    ``paper-session`` public Job type (``JobSubmissionSpec`` protocol)."""

    job_type = PAPER_SESSION_JOB_TYPE
    description = (
        "Run the daily paper-trading session (reconcile, correct, submit orders) for one strategy and session. "
        "Cancellable only while queued; once running, the session runs to completion."
    )
    cancellation_mode = JobCancellationMode.QUEUED_ONLY
    # D-19: a FAILED, outcome_uncertain paper-session Job may only be
    # retried after a newer SUCCEEDED reconciliation Job exists for the
    # same strategy.
    retry_prerequisite_job_type = "reconciliation"

    def __init__(self, settings: Settings, *, clock: Callable[[], datetime] | None = None) -> None:
        self._settings = settings
        self._clock = clock or _default_clock

    def validate_payload(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        try:
            parsed = _PaperSessionPayload.model_validate(dict(payload))
        except ValidationError as exc:
            reason = PaperSessionPayloadRejection(map_validation_error(exc).value)
            raise InvalidJobPayloadError(
                job_type=PAPER_SESSION_JOB_TYPE, reason=reason.value
            ) from exc

        strategy_id = parsed.strategy_id
        as_of_session = parsed.as_of_session
        risk_run_id = parsed.risk_run_id

        strategy = require_registered_strategy(
            self._settings, strategy_id, job_type=PAPER_SESSION_JOB_TYPE
        )
        require_trading_session_not_future(
            self._settings, self._clock, as_of_session, job_type=PAPER_SESSION_JOB_TYPE
        )

        canonical_risk_run_id: str | None = None
        pinned_risk_run_id: uuid.UUID | None = None
        if risk_run_id is not None:
            try:
                parsed_risk_run_id = uuid.UUID(risk_run_id)
            except ValueError as exc:
                raise InvalidJobPayloadError(
                    job_type=PAPER_SESSION_JOB_TYPE,
                    reason=PaperSessionPayloadRejection.INVALID_RISK_RUN_ID.value,
                ) from exc
            if not is_eligible_risk_run(
                risk_run_id=parsed_risk_run_id,
                strategy_id=strategy_id,
                as_of_session=as_of_session,
                settings=self._settings,
            ):
                raise InvalidJobPayloadError(
                    job_type=PAPER_SESSION_JOB_TYPE,
                    reason=PaperSessionPayloadRejection.RISK_RUN_NOT_ELIGIBLE.value,
                )
            canonical_risk_run_id = str(parsed_risk_run_id)
            pinned_risk_run_id = parsed_risk_run_id

        # D-03: state check LAST, after every shape/semantic payload check.
        require_active_paper_strategy(
            self._settings,
            strategy_id,
            job_type=PAPER_SESSION_JOB_TYPE,
            conflict_enum=PaperSessionSubmitConflict,
        )

        # D-23: execution eligibility at the injected clock (read-only).
        with session_scope(self._settings) as db_session:
            eligibility = paper_execution_eligibility(
                db_session,
                now=self._clock(),
                settings=self._settings,
                strategy=strategy,
                as_of_session=as_of_session,
            )
        if eligibility.rejection is not None:
            detail = {"strategy_id": strategy_id, "as_of_session": as_of_session.isoformat()}
            if eligibility.rejection is EligibilityRejection.EVALUATION_DATA_NOT_READY:
                evaluation = eligibility.evaluation_session
                detail["reason"] = evaluation.reason.value if evaluation.reason else ""
                detail["symbols"] = ",".join(evaluation.symbols)
            raise JobSubmissionConflictError(
                job_type=PAPER_SESSION_JOB_TYPE,
                code=PaperSessionSubmitConflict(eligibility.rejection.value).value,
                detail=detail,
            )

        self._require_matching_manifest(
            strategy_id=strategy_id,
            as_of_session=as_of_session,
            pinned_risk_run_id=pinned_risk_run_id,
        )

        return {
            "strategy_id": strategy_id,
            "as_of_session": as_of_session.isoformat(),
            "risk_run_id": canonical_risk_run_id,
        }

    def _require_matching_manifest(
        self,
        *,
        strategy_id: str,
        as_of_session: date,
        pinned_risk_run_id: uuid.UUID | None,
    ) -> None:
        """D-25: the evaluation that will be executed must still reflect the
        currently valid source data and signal settings (read-only)."""

        detail: dict[str, str] = {
            "strategy_id": strategy_id,
            "as_of_session": as_of_session.isoformat(),
        }
        run_id = pinned_risk_run_id or latest_eligible_risk_run_id(
            strategy_id=strategy_id, as_of_session=as_of_session, settings=self._settings
        )
        if run_id is None:
            detail["reason"] = "no_eligible_risk_run"
            raise JobSubmissionConflictError(
                job_type=PAPER_SESSION_JOB_TYPE,
                code=PaperSessionSubmitConflict.EVALUATION_DATA_NOT_READY.value,
                detail=detail,
            )
        detail["risk_run_id"] = str(run_id)
        verification = verify_risk_run_manifest(
            risk_run_id=run_id, strategy_id=strategy_id, settings=self._settings
        )
        status = verification.status
        if status is ManifestVerificationStatus.MATCHES:
            return
        if status is ManifestVerificationStatus.MANIFEST_MISSING:
            detail["reason"] = "manifest_missing"
            code = PaperSessionSubmitConflict.EVALUATION_DATA_NOT_READY
        elif status is ManifestVerificationStatus.STRATEGY_SETTINGS_CHANGED:
            code = PaperSessionSubmitConflict.STRATEGY_SETTINGS_CHANGED
        else:
            code = PaperSessionSubmitConflict.EVALUATION_DATA_CHANGED
            if verification.mismatched_request is not None:
                detail["request_kind"] = verification.mismatched_request.kind.value
        raise JobSubmissionConflictError(
            job_type=PAPER_SESSION_JOB_TYPE, code=code.value, detail=detail
        )

    def lock_admission(self, *, session: Any) -> None:
        """SER lock step alone: the ownership singleton FOR SHARE (fails closed)."""

        lock_active_paper_strategy_shared(session)

    def check_admission(self, payload: Mapping[str, Any], *, session: Any) -> None:
        """SER admission (inside the Job-insert transaction, before the insert):
        lock the ownership singleton FOR SHARE, then re-run the same ownership
        check against the transaction's own view."""

        self.lock_admission(session=session)
        require_active_paper_strategy(
            self._settings,
            payload["strategy_id"],
            job_type=PAPER_SESSION_JOB_TYPE,
            conflict_enum=PaperSessionSubmitConflict,
            session=session,
        )

    def submission_defaults(self) -> dict[str, str] | None:
        """Console pre-fill, computed at read time (D-24): the EVALUATION candidate
        session (latest calendar-completed persisted session), never "latest session
        with bars". Returns ``None`` when the calendar does not cover the clock's date. Deliberately never
        includes ``risk_run_id`` (D-23) -- the form sends an explicit
        ``null`` for it."""

        latest = evaluation_session_default(self._settings, self._clock)
        if latest is None:
            return None
        return {"as_of_session": latest.isoformat()}
