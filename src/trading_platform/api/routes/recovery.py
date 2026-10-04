"""REC-01 recovery HTTP control (05 M14): the broker statement of an ambiguous order intent.

The single mutating route here calls ``OperatorControlService.record_broker_statement``
synchronously (no Job, no worker). A statement is audited EVIDENCE ONLY (round 5,
2026-10-04): it never resolves an intent and never authorizes a resend, so there is no
route that retracts a statement and none may exist. The strict body parsing and reason
validation mirror ``controls.py`` (copied, not shared, so ``controls.py`` behaviour cannot change).
"""

from __future__ import annotations

import json
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool

from trading_platform.api.dependencies import (
    get_operator_control_service,
    require_mutations_enabled,
)
from trading_platform.services.operator_controls import (
    ControlStateUnavailableError,
    ControlWriteError,
    IntentNotFoundError,
    IntentNotOnMissingOrderPathError,
    InvalidBrokerStatementError,
    OperatorControlService,
    StatementConflictError,
)

router = APIRouter(prefix="/api/v1/recovery", tags=["recovery"])

MAX_TEXT_LENGTH = 500
RECOVERY_ACTOR = "local_operator"
RECOVERY_TRIGGER_SOURCE = "api_control"

_STATEMENT_KINDS = {"not_received", "order_record"}
_BODY_KEYS = {"statement", "reference", "reason"}


def _error(status_code: int, code: str, **detail: Any) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, **detail})


async def _read_body(request: Request) -> dict[str, Any]:
    """Strict JSON object body whose keys are a subset of the three allowed keys."""

    try:
        parsed = json.loads(await request.body())
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as exc:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_control_request") from exc
    if not isinstance(parsed, dict) or not set(parsed).issubset(_BODY_KEYS):
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_control_request")
    return parsed


def _validate_text(value: Any, code: str) -> str:
    if not isinstance(value, str):
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, code)
    trimmed = value.strip()
    # PostgreSQL rejects NUL in text columns: a typed 422 with zero writes, never a 500.
    if not trimmed or len(trimmed) > MAX_TEXT_LENGTH or "\x00" in trimmed:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, code)
    return trimmed


@router.post(
    "/intents/{intent_id}/broker-statement",
    dependencies=[Depends(require_mutations_enabled)],
)
async def record_broker_statement(
    intent_id: str,
    request: Request,
    service: Annotated[OperatorControlService, Depends(get_operator_control_service)],
) -> dict[str, Any]:
    body = await _read_body(request)
    try:
        intent_uuid = uuid.UUID(intent_id)
    except ValueError as exc:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_control_target") from exc
    statement = body.get("statement")
    if not isinstance(statement, str) or statement not in _STATEMENT_KINDS:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_control_target")
    reference = _validate_text(body.get("reference"), "invalid_broker_reference")
    reason = _validate_text(body.get("reason"), "invalid_control_reason")

    try:
        report = await run_in_threadpool(
            service.record_broker_statement,
            intent_uuid,
            statement=statement,
            reference=reference,
            reason=reason,
            actor=RECOVERY_ACTOR,
            trigger_source=RECOVERY_TRIGGER_SOURCE,
        )
    except IntentNotFoundError as exc:
        raise _error(
            status.HTTP_404_NOT_FOUND, "intent_not_found", intent_id=str(intent_uuid)
        ) from exc
    except IntentNotOnMissingOrderPathError as exc:
        raise _error(
            status.HTTP_409_CONFLICT,
            "intent_not_on_missing_order_path",
            intent_id=str(intent_uuid),
        ) from exc
    except StatementConflictError as exc:
        raise _error(
            status.HTTP_409_CONFLICT, "statement_conflict", intent_id=str(intent_uuid)
        ) from exc
    except InvalidBrokerStatementError as exc:
        code = {
            "statement": "invalid_control_target",
            "reference": "invalid_broker_reference",
            "reason": "invalid_control_reason",
        }[exc.field_name]
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, code) from exc
    except ControlStateUnavailableError as exc:
        raise _error(status.HTTP_503_SERVICE_UNAVAILABLE, "control_state_unavailable") from exc
    except ControlWriteError as exc:
        raise _error(status.HTTP_503_SERVICE_UNAVAILABLE, "control_write_failed") from exc
    return {
        "intent_id": report.intent_id,
        "statement": report.statement,
        "changed": report.changed,
        "classification": report.classification,
        "run_id": report.run_id,
    }
