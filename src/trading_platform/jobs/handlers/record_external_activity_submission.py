"""RecordExternalActivitySubmissionSpec: the public input contract for the
``record-external-activity`` Job type (EXT-01, D-10).

The payload is exactly ``{order_ids: [str, ...], reason: str}`` and is strict: unknown
keys (including ``strategy_id`` and ``as_of_session``) are rejected, because a recorded
external item is never attributed to a strategy. The payload carries ids and a reason
only, never order content: the handler re-fetches every order from the broker.

Validation is typed and closed (``RecordExternalActivityPayloadRejection``, one
parametrized test per value). Normalization: ids are stripped (original order kept),
the reason is trimmed. Nothing is defaulted.

The type is broker-touching (reads), so it registers the SER admission hook: the
ownership singleton is taken FOR SHARE inside the Job-insert transaction, which makes
the handover checks (20.1-12 A1) see a queued recording. It performs no ownership
check of its own: recording is account-level and needs no owner. Cancellable only while
queued.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, StrictStr, ValidationError

from trading_platform.core.settings import Settings
from trading_platform.jobs.handlers.payload_fields import map_validation_error
from trading_platform.jobs.registry import InvalidJobPayloadError, JobCancellationMode
from trading_platform.services.active_paper_strategy import lock_active_paper_strategy_shared
from trading_platform.services.broker_jobs import (
    RECORD_EXTERNAL_ACTIVITY_JOB_TYPE,
    BrokerEffect,
)

#: At most this many broker order ids per request.
MAX_ORDER_IDS = 50
MAX_ORDER_ID_LENGTH = 64
MAX_REASON_LENGTH = 500


class RecordExternalActivityPayloadRejection(StrEnum):
    """Closed, stable set of machine-readable ``record-external-activity`` payload
    rejection reasons. One parametrized test case exists per value."""

    UNKNOWN_PAYLOAD_KEYS = "unknown_payload_keys"
    MISSING_REQUIRED_FIELD = "missing_required_field"
    INVALID_FIELD_TYPE = "invalid_field_type"
    ORDER_IDS_EMPTY = "order_ids_empty"
    ORDER_IDS_TOO_MANY = "order_ids_too_many"
    DUPLICATE_ORDER_IDS = "duplicate_order_ids"
    INVALID_ORDER_ID = "invalid_order_id"
    INVALID_REASON = "invalid_reason"


class _RecordExternalActivityPayload(BaseModel):
    """Shape validation only; the semantic checks follow in ``validate_payload``."""

    model_config = ConfigDict(extra="forbid")

    order_ids: list[StrictStr]
    reason: StrictStr


def _reject(reason: RecordExternalActivityPayloadRejection) -> InvalidJobPayloadError:
    return InvalidJobPayloadError(job_type=RECORD_EXTERNAL_ACTIVITY_JOB_TYPE, reason=reason.value)


class RecordExternalActivitySubmissionSpec:
    """Transport-neutral validation and normalization for the
    ``record-external-activity`` public Job type (``JobSubmissionSpec`` protocol)."""

    job_type = RECORD_EXTERNAL_ACTIVITY_JOB_TYPE
    description = (
        "Record terminal, net-zero external broker orders as verified, immutable evidence "
        "(no strategy, no position), then run a fresh account reconciliation. Queued-only."
    )
    cancellation_mode = JobCancellationMode.QUEUED_ONLY
    broker_effect = BrokerEffect.READS_BROKER

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def validate_payload(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        try:
            parsed = _RecordExternalActivityPayload.model_validate(dict(payload))
        except ValidationError as exc:
            shape_reason = RecordExternalActivityPayloadRejection(map_validation_error(exc).value)
            raise _reject(shape_reason) from exc

        if not parsed.order_ids:
            raise _reject(RecordExternalActivityPayloadRejection.ORDER_IDS_EMPTY)
        if len(parsed.order_ids) > MAX_ORDER_IDS:
            raise _reject(RecordExternalActivityPayloadRejection.ORDER_IDS_TOO_MANY)

        order_ids = [value.strip() for value in parsed.order_ids]
        if any(
            not value or len(value) > MAX_ORDER_ID_LENGTH or "\x00" in value for value in order_ids
        ):
            raise _reject(RecordExternalActivityPayloadRejection.INVALID_ORDER_ID)
        if len(set(order_ids)) != len(order_ids):
            raise _reject(RecordExternalActivityPayloadRejection.DUPLICATE_ORDER_IDS)

        reason = parsed.reason.strip()
        if not reason or len(reason) > MAX_REASON_LENGTH or "\x00" in reason:
            raise _reject(RecordExternalActivityPayloadRejection.INVALID_REASON)

        return {"order_ids": order_ids, "reason": reason}

    def lock_admission(self, *, session: Any) -> None:
        """SER lock step alone: the ownership singleton FOR SHARE (fails closed)."""

        lock_active_paper_strategy_shared(session)

    def check_admission(self, payload: Mapping[str, Any], *, session: Any) -> None:
        """SER admission: recording reads the broker and writes recorded evidence, so
        its admission serializes with handover on the ownership singleton (FOR SHARE).
        It is account-level: NO ownership check runs here (D-09)."""

        self.lock_admission(session=session)

    def submission_defaults(self) -> None:
        """No console pre-fill: the operator lists the unrecognized order ids."""

        return None
