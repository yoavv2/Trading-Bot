"""BrokerOrderSyncSubmissionSpec: the public input contract for the
``broker-order-sync`` Job type (D-01, D-19, D-21, D-22, D-25).

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

The ``broker-order-sync`` Job type is cancellable only while queued (D-01):
it writes broker-derived state inside one opaque service call, so a
RUNNING Job of this type is never cancellable
(``JobOrchestrationService`` rejects the request per D-02). It also
declares a D-19 reconcile-first retry prerequisite: a FAILED,
outcome-uncertain ``broker-order-sync`` Job may only be retried after a
newer SUCCEEDED reconciliation Job exists for the same strategy.

Scope (ACCT-01, D-09): ``scope`` is ``strategy`` (the default when omitted --
today's payload and behaviour, byte-identical) or ``account``. Account scope is
the owner-less account-level sync: it forbids ``strategy_id``
(``account_scope_forbids_strategy_id``) and makes ``as_of_session`` optional
(validated exactly as before when present, absent otherwise). The spec declares
``broker_effect = reads_broker`` (catalog field).

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
    JobScope,
    evaluation_session_default,
    map_validation_error,
    parse_iso_date,
    require_registered_strategy,
    require_trading_session_not_future,
    split_scope,
)
from trading_platform.jobs.registry import InvalidJobPayloadError, JobCancellationMode
from trading_platform.services.active_paper_strategy import lock_active_paper_strategy_shared
from trading_platform.services.broker_jobs import BrokerEffect

BROKER_ORDER_SYNC_JOB_TYPE = "broker-order-sync"


class BrokerOrderSyncPayloadRejection(StrEnum):
    """Closed, stable set of machine-readable ``broker-order-sync`` payload
    rejection reasons. One parametrized test case exists per value. Each
    value equals the corresponding shared ``PayloadFieldRejection`` value."""

    UNKNOWN_PAYLOAD_KEYS = "unknown_payload_keys"
    MISSING_REQUIRED_FIELD = "missing_required_field"
    INVALID_FIELD_TYPE = "invalid_field_type"
    INVALID_DATE = "invalid_date"
    UNKNOWN_STRATEGY_ID = "unknown_strategy_id"
    AS_OF_SESSION_IN_FUTURE = "as_of_session_in_future"
    AS_OF_SESSION_NOT_TRADING_SESSION = "as_of_session_not_trading_session"
    AS_OF_SESSION_OUT_OF_CALENDAR_RANGE = "as_of_session_out_of_calendar_range"
    # ACCT-01: the ``scope`` field.
    INVALID_SCOPE = "invalid_scope"
    ACCOUNT_SCOPE_FORBIDS_STRATEGY_ID = "account_scope_forbids_strategy_id"


def _default_clock() -> datetime:
    return datetime.now(UTC)


class _BrokerOrderSyncPayload(BaseModel):
    """Shape validation only -- semantic checks (registry lookup, future-date
    rejection, trading-session membership) happen after this model
    validates, inside ``BrokerOrderSyncSubmissionSpec.validate_payload``."""

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


class _BrokerOrderSyncAccountPayload(BaseModel):
    """Account scope: no ``strategy_id``; ``as_of_session`` is optional."""

    model_config = ConfigDict(extra="forbid")

    as_of_session: date | None = None

    @field_validator("as_of_session", mode="before")
    @classmethod
    def _parse_as_of_session(cls, value: Any) -> date:
        return parse_iso_date(value)


class BrokerOrderSyncSubmissionSpec:
    """Transport-neutral validation and normalization for the
    ``broker-order-sync`` public Job type (``JobSubmissionSpec`` protocol)."""

    job_type = BROKER_ORDER_SYNC_JOB_TYPE
    description = (
        "Sync paper order lifecycle, fills, positions and account state from the broker "
        "for one session; scope 'account' syncs known orders and fills without an owner. "
        "Cancellable only while queued."
    )
    cancellation_mode = JobCancellationMode.QUEUED_ONLY
    broker_effect = BrokerEffect.READS_BROKER
    # D-19: a FAILED, outcome_uncertain broker-order-sync Job may only be
    # retried after a newer SUCCEEDED reconciliation Job exists for the
    # same strategy.
    retry_prerequisite_job_type = "reconciliation"

    def __init__(self, settings: Settings, *, clock: Callable[[], datetime] | None = None) -> None:
        self._settings = settings
        self._clock = clock or _default_clock

    def validate_payload(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        scope, body = split_scope(payload, job_type=BROKER_ORDER_SYNC_JOB_TYPE)
        if scope is JobScope.ACCOUNT:
            return self._validate_account_payload(body)

        try:
            parsed = _BrokerOrderSyncPayload.model_validate(body)
        except ValidationError as exc:
            reason = BrokerOrderSyncPayloadRejection(map_validation_error(exc).value)
            raise InvalidJobPayloadError(
                job_type=BROKER_ORDER_SYNC_JOB_TYPE, reason=reason.value
            ) from exc

        strategy_id = parsed.strategy_id
        as_of_session = parsed.as_of_session

        require_registered_strategy(
            self._settings, strategy_id, job_type=BROKER_ORDER_SYNC_JOB_TYPE
        )
        require_trading_session_not_future(
            self._settings, self._clock, as_of_session, job_type=BROKER_ORDER_SYNC_JOB_TYPE
        )

        return {
            "strategy_id": strategy_id,
            "as_of_session": as_of_session.isoformat(),
        }

    def _validate_account_payload(self, body: Mapping[str, Any]) -> Mapping[str, Any]:
        try:
            parsed = _BrokerOrderSyncAccountPayload.model_validate(dict(body))
        except ValidationError as exc:
            reason = BrokerOrderSyncPayloadRejection(map_validation_error(exc).value)
            raise InvalidJobPayloadError(
                job_type=BROKER_ORDER_SYNC_JOB_TYPE, reason=reason.value
            ) from exc

        normalized: dict[str, Any] = {"scope": JobScope.ACCOUNT.value}
        if parsed.as_of_session is not None:
            require_trading_session_not_future(
                self._settings,
                self._clock,
                parsed.as_of_session,
                job_type=BROKER_ORDER_SYNC_JOB_TYPE,
            )
            normalized["as_of_session"] = parsed.as_of_session.isoformat()
        return normalized

    def lock_admission(self, *, session: Any) -> None:
        """SER lock step alone: the ownership singleton FOR SHARE (fails closed)."""

        lock_active_paper_strategy_shared(session)

    def check_admission(self, payload: Mapping[str, Any], *, session: Any) -> None:
        """SER admission: broker-order-sync touches the broker, so its admission
        serializes with handover on the ownership singleton (FOR SHARE). It is
        NOT gated by ownership (D-03): no ownership check runs here."""

        self.lock_admission(session=session)

    def submission_defaults(self) -> dict[str, str] | None:
        """Console pre-fill, computed at read time (D-24): the EVALUATION candidate
        session (latest calendar-completed persisted session), never "latest session
        with bars". Returns ``None`` when the calendar does not cover the clock's date."""

        latest = evaluation_session_default(self._settings, self._clock)
        if latest is None:
            return None
        return {"as_of_session": latest.isoformat()}
