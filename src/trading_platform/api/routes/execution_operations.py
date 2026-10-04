"""REC-02 execution operation HTTP surface (05 R2 reads, M12 End control).

Two read routes (list and detail, write-free) and one mutating control, End operation, which
calls the control service synchronously (no Job, no worker). End terminates the operation and
its UNSENT intents only: it never cancels a broker order, never resolves an uncertain outcome,
never erases recovery evidence and never changes trading permission, so there is no cancel
route and no withdraw route, and none may exist. Continue goes through the Job route and adds
no route here. Body parsing mirrors ``controls.py`` (copied, not shared, so its behaviour cannot
change).
"""

from __future__ import annotations

import json
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.concurrency import run_in_threadpool

from trading_platform.api.dependencies import (
    get_operation_read_service,
    get_operator_control_service,
    require_mutations_enabled,
)
from trading_platform.services.operation_reads import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    InvalidStateFilterError,
    OperationReadService,
    StrategyNotFoundError,
)
from trading_platform.services.operator_controls import (
    ControlStateUnavailableError,
    ControlWriteError,
    OperationNotFoundError,
    OperationNotOpenError,
    OperationRunningError,
    OperatorControlService,
)

router = APIRouter(prefix="/api/v1/execution-operations", tags=["execution-operations"])

MAX_REASON_LENGTH = 500
END_ACTOR = "local_operator"
END_TRIGGER_SOURCE = "api_control"

_BODY_KEYS = {"reason"}


def _error(status_code: int, code: str, **detail: Any) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, **detail})


def _parse_operation_id(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        # A malformed id can name no operation.
        raise _error(status.HTTP_404_NOT_FOUND, "operation_not_found", operation_id=raw) from exc


async def _read_body(request: Request) -> dict[str, Any]:
    """Strict JSON object body whose keys are a subset of ``{reason}``."""

    try:
        parsed = json.loads(await request.body())
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as exc:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_control_request") from exc
    if not isinstance(parsed, dict) or not set(parsed).issubset(_BODY_KEYS):
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_control_request")
    return parsed


def _validate_reason(value: Any) -> str:
    if not isinstance(value, str):
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_control_reason")
    trimmed = value.strip()
    # PostgreSQL rejects NUL in text columns: a typed 422 with zero writes, never a 500.
    if not trimmed or len(trimmed) > MAX_REASON_LENGTH or "\x00" in trimmed:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_control_reason")
    return trimmed


@router.get("")
def list_execution_operations(
    reads: Annotated[OperationReadService, Depends(get_operation_read_service)],
    strategy_id: Annotated[str | None, Query()] = None,
    state: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> dict[str, Any]:
    try:
        items = reads.list_operations(strategy_id=strategy_id, state=state, limit=limit)
    except StrategyNotFoundError as exc:
        raise _error(
            status.HTTP_404_NOT_FOUND, "strategy_not_found", strategy_id=exc.strategy_id
        ) from exc
    except InvalidStateFilterError as exc:
        raise _error(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_state_filter", state=exc.state
        ) from exc
    return {
        "filters": {"strategy_id": strategy_id, "state": state, "limit": limit},
        "count": len(items),
        "items": items,
    }


@router.get("/{operation_id}")
def execution_operation_detail(
    operation_id: str,
    reads: Annotated[OperationReadService, Depends(get_operation_read_service)],
) -> dict[str, Any]:
    operation_uuid = _parse_operation_id(operation_id)
    try:
        return reads.get_operation(operation_uuid)
    except OperationNotFoundError as exc:
        raise _error(
            status.HTTP_404_NOT_FOUND, "operation_not_found", operation_id=operation_id
        ) from exc


@router.post("/{operation_id}/end", dependencies=[Depends(require_mutations_enabled)])
async def end_execution_operation(
    operation_id: str,
    request: Request,
    service: Annotated[OperatorControlService, Depends(get_operator_control_service)],
) -> dict[str, Any]:
    body = await _read_body(request)
    reason = _validate_reason(body.get("reason"))
    operation_uuid = _parse_operation_id(operation_id)
    try:
        report = await run_in_threadpool(
            service.end_operation,
            operation_uuid,
            reason=reason,
            actor=END_ACTOR,
            trigger_source=END_TRIGGER_SOURCE,
        )
    except OperationNotFoundError as exc:
        raise _error(
            status.HTTP_404_NOT_FOUND, "operation_not_found", operation_id=operation_id
        ) from exc
    except OperationRunningError as exc:
        raise _error(
            status.HTTP_409_CONFLICT,
            "operation_running",
            operation_id=operation_id,
            running_job_ids=[str(job_id) for job_id in exc.running_job_ids],
        ) from exc
    except OperationNotOpenError as exc:
        raise _error(
            status.HTTP_409_CONFLICT,
            "operation_not_open",
            operation_id=operation_id,
            state=exc.state,
        ) from exc
    except ControlStateUnavailableError as exc:
        raise _error(status.HTTP_503_SERVICE_UNAVAILABLE, "control_state_unavailable") from exc
    except ControlWriteError as exc:
        raise _error(status.HTTP_503_SERVICE_UNAVAILABLE, "control_write_failed") from exc
    return report.to_dict()
