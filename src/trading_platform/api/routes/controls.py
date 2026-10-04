"""Synchronous safety-control HTTP routes (CTRL-01/02, D-10/D-11).

The mutating routes here (kill switch, strategy status, active paper strategy) call ``OperatorControlService`` directly and
synchronously (no Job, no worker) -- they are the immediate safety-control
path the roadmap's D1 decision reserves alongside the Job orchestration
path used by every long-running operation.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool

from trading_platform.api.dependencies import (
    get_operator_control_service,
    get_settings,
    get_strategy_registry,
    require_mutations_enabled,
)
from trading_platform.services.operator_controls import (
    AccountCheckFailedError,
    ControlStateUnavailableError,
    ControlWriteError,
    OperatorControlService,
    StrategyArchivedError,
    load_strategy_control_state,
)
from trading_platform.strategies.registry import StrategyRegistry, UnknownStrategyError

router = APIRouter(prefix="/api/v1/controls", tags=["controls"])

MAX_CONTROL_REASON_LENGTH = 500
CONTROL_ACTOR = "local_operator"
CONTROL_TRIGGER_SOURCE = "api_control"

_KILL_SWITCH_TARGETS = {"tripped", "armed"}
_STRATEGY_STATUS_TARGETS = {"enabled", "disabled"}


def _error(status_code: int, code: str, **detail: Any) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, **detail})


async def _read_body(request: Request, allowed_keys: set[str]) -> dict[str, Any]:
    """Parse a strict JSON object body whose keys are a subset of ``allowed_keys``.

    Deliberately does not declare a pydantic body model / Literal fields:
    FastAPI's default 422 body (a list of error objects) is unparseable by
    the console's error-detail contract, which expects ``detail`` to always
    be a JSON object.
    """

    try:
        parsed = json.loads(await request.body())
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as exc:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_control_request") from exc
    if not isinstance(parsed, dict) or not set(parsed).issubset(allowed_keys):
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_control_request")
    return parsed


def _validate_reason(value: Any) -> str:
    if not isinstance(value, str):
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_control_reason")
    trimmed = value.strip()
    # PostgreSQL rejects NUL in text columns; catch it here so it is a typed
    # 422 with zero writes rather than a 500 after the request was accepted.
    if not trimmed or len(trimmed) > MAX_CONTROL_REASON_LENGTH or "\x00" in trimmed:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_control_reason")
    return trimmed


@router.put("/kill-switch", dependencies=[Depends(require_mutations_enabled)])
async def set_kill_switch(
    request: Request,
    service: Annotated[OperatorControlService, Depends(get_operator_control_service)],
) -> dict[str, Any]:
    body = await _read_body(request, {"state", "reason"})
    target_state = body.get("state")
    if not isinstance(target_state, str) or target_state not in _KILL_SWITCH_TARGETS:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_control_target")
    reason = _validate_reason(body.get("reason"))

    mutator = service.trip_kill_switch if target_state == "tripped" else service.reset_kill_switch
    try:
        report = await run_in_threadpool(
            mutator,
            reason=reason,
            actor=CONTROL_ACTOR,
            trigger_source=CONTROL_TRIGGER_SOURCE,
        )
    except ControlStateUnavailableError as exc:
        raise _error(
            status.HTTP_503_SERVICE_UNAVAILABLE, "control_state_unavailable"
        ) from exc
    except ControlWriteError as exc:
        raise _error(status.HTTP_503_SERVICE_UNAVAILABLE, "control_write_failed") from exc
    return {"state": report.current_state, "changed": report.changed, "run_id": report.run_id}


@router.put("/strategies/{strategy_id}", dependencies=[Depends(require_mutations_enabled)])
async def set_strategy_status(
    strategy_id: str,
    request: Request,
    registry: Annotated[StrategyRegistry, Depends(get_strategy_registry)],
    service: Annotated[OperatorControlService, Depends(get_operator_control_service)],
) -> dict[str, Any]:
    body = await _read_body(request, {"status", "reason"})
    try:
        registry.resolve(strategy_id)
    except UnknownStrategyError as exc:
        raise _error(
            status.HTTP_404_NOT_FOUND, "strategy_not_found", strategy_id=strategy_id
        ) from exc

    target_status = body.get("status")
    if not isinstance(target_status, str) or target_status not in _STRATEGY_STATUS_TARGETS:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_control_target")
    reason = _validate_reason(body.get("reason"))

    mutator = service.enable_strategy if target_status == "enabled" else service.disable_strategy
    try:
        report = await run_in_threadpool(
            mutator,
            strategy_id,
            reason=reason,
            actor=CONTROL_ACTOR,
            trigger_source=CONTROL_TRIGGER_SOURCE,
        )
    except StrategyArchivedError as exc:
        raise _error(
            status.HTTP_409_CONFLICT, "strategy_archived", strategy_id=strategy_id
        ) from exc
    except ControlStateUnavailableError as exc:
        raise _error(
            status.HTTP_503_SERVICE_UNAVAILABLE, "control_state_unavailable"
        ) from exc
    except ControlWriteError as exc:
        raise _error(status.HTTP_503_SERVICE_UNAVAILABLE, "control_write_failed") from exc
    return {
        "strategy_id": strategy_id,
        "status": "enabled" if report.current_status == "active" else "disabled",
        "changed": report.changed,
        "run_id": report.run_id,
    }


@router.get("/strategies/{strategy_id}")
async def get_strategy_control_status(
    strategy_id: str,
    request: Request,
    registry: Annotated[StrategyRegistry, Depends(get_strategy_registry)],
) -> dict[str, Any]:
    """D-13/D-31: the true DB control status, not the static config flag
    served by ``GET /api/v1/strategies/{id}`` (that flag never changes when
    an operator disables a strategy)."""

    try:
        registry.resolve(strategy_id)
    except UnknownStrategyError as exc:
        raise _error(
            status.HTTP_404_NOT_FOUND, "strategy_not_found", strategy_id=strategy_id
        ) from exc

    try:
        state = await run_in_threadpool(
            load_strategy_control_state,
            strategy_id,
            settings=get_settings(request),
        )
    except ControlStateUnavailableError as exc:
        raise _error(
            status.HTTP_503_SERVICE_UNAVAILABLE, "control_state_unavailable"
        ) from exc
    return {
        "strategy_id": state.strategy_id,
        "status": "enabled" if state.status == "active" else "disabled",
        "updated_at": state.updated_at,
    }


@router.get("/active-paper-strategy")
async def get_active_paper_strategy(request: Request) -> dict[str, Any]:
    """PAPER-01 / R1: read-only view of the strategy that owns the paper account.

    Returns ``{strategy_id, display_name, since, reason, set_by_run_id}`` plus the
    account ``checks`` (A1..A6, A7 only when an owner exists), ``seeding_available``,
    ``handover_available`` and ``as_of``; ``strategy_id`` is ``null`` when no strategy owns
    the account (the initial state). Performs no write, no get-or-create and no broker
    call; a missing singleton row is a typed 503 ``control_state_unavailable``.
    """

    service = OperatorControlService(settings=get_settings(request))
    try:
        view = await run_in_threadpool(service.get_active_paper_strategy_view)
    except ControlStateUnavailableError as exc:
        raise _error(
            status.HTTP_503_SERVICE_UNAVAILABLE, "control_state_unavailable"
        ) from exc
    return view.to_dict()


@router.put("/active-paper-strategy", dependencies=[Depends(require_mutations_enabled)])
async def set_active_paper_strategy(
    request: Request,
    registry: Annotated[StrategyRegistry, Depends(get_strategy_registry)],
    service: Annotated[OperatorControlService, Depends(get_operator_control_service)],
) -> dict[str, Any]:
    """PAPER-02 / D-04: seed (none -> B), hand over (A -> B), release (A -> none) or
    reaffirm the single paper-account owner.

    Synchronous, no Job, no worker, no broker call. The body is exactly
    ``{"strategy_id": <registered id> | null, "reason": <text>}``; ``null`` releases. A
    failing account check is a typed 409 ``check_failed:A1`` .. ``check_failed:A7`` naming the
    first failing check, with every evaluated check in the body and zero writes. The new
    owner is always left disabled; enabling is the separate strategy control.
    """

    body = await _read_body(request, {"strategy_id", "reason"})
    if "strategy_id" not in body:
        # A missing key is not the same as an explicit null (which releases the owner).
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_control_request")
    target = body["strategy_id"]
    if target is not None and not isinstance(target, str):
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_control_target")
    if target is not None:
        try:
            registry.resolve(target)
        except UnknownStrategyError as exc:
            raise _error(
                status.HTTP_404_NOT_FOUND, "strategy_not_found", strategy_id=target
            ) from exc
    reason = _validate_reason(body.get("reason"))

    try:
        report = await run_in_threadpool(
            service.set_active_paper_strategy,
            target,
            reason=reason,
            actor=CONTROL_ACTOR,
            trigger_source=CONTROL_TRIGGER_SOURCE,
        )
    except AccountCheckFailedError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=exc.to_detail()) from exc
    except StrategyArchivedError as exc:
        raise _error(
            status.HTTP_409_CONFLICT, "strategy_archived", strategy_id=exc.strategy_id
        ) from exc
    except ControlStateUnavailableError as exc:
        raise _error(
            status.HTTP_503_SERVICE_UNAVAILABLE, "control_state_unavailable"
        ) from exc
    except ControlWriteError as exc:
        raise _error(status.HTTP_503_SERVICE_UNAVAILABLE, "control_write_failed") from exc
    return report.to_dict()
