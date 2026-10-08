"""Research assistant routes (plan S5): ``/api/v1/research/assistant/*``.

Mounted only in research mode. ``GET /assistant`` reports configuration, limits and usage
(never the key) so the console can show disabled / unconfigured / exhausted states without
blocking the editor. ``POST /assistant/proposals`` runs one bounded, accounted request and
returns the proposal (valid, or invalid after the single automatic retry); terminal
failures (timeout, refusal, provider errors, exhausted allowance) answer with closed codes
and the provenance row id. ``POST /assistant/proposals/{id}/apply`` is the explicit action
that writes the proposal into a draft; approval stays ``POST .../drafts/{id}/approve``.

Error bodies are ``{"detail": {"code": ...}}`` objects, like every research route.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from trading_platform.api.dependencies import get_settings, require_mutations_enabled
from trading_platform.api.research.strategies import _as_of, _error, _read_body, _uuid
from trading_platform.services.research.assistant import (
    CODE_AUTH_FAILED,
    CODE_BUSY,
    CODE_DAILY_LIMIT,
    CODE_DISABLED,
    CODE_INPUT_TOO_LONG,
    CODE_INVALID_INPUT,
    CODE_NOT_APPLICABLE,
    CODE_NOT_CONFIGURED,
    CODE_OUTPUT_TRUNCATED,
    CODE_PROVIDER_ERROR,
    CODE_PROVIDER_UNAVAILABLE,
    CODE_RATE_LIMITED,
    CODE_REFUSED,
    CODE_REVISION_LIMIT,
    CODE_TIMEOUT,
    AiDraftNotFoundError,
    AssistantError,
    ResearchAssistantService,
)
from trading_platform.services.research.strategies import DraftNotFoundError

router = APIRouter(prefix="/api/v1/research/assistant", tags=["research-assistant"])


def get_research_assistant_service(request: Request) -> ResearchAssistantService:
    override = getattr(request.app.state, "research_assistant_service", None)
    if override is not None:
        return override
    return ResearchAssistantService(get_settings(request))


Service = Annotated[ResearchAssistantService, Depends(get_research_assistant_service)]

_HTTP_STATUS = {
    CODE_DISABLED: status.HTTP_409_CONFLICT,
    CODE_NOT_CONFIGURED: status.HTTP_409_CONFLICT,
    CODE_INPUT_TOO_LONG: status.HTTP_422_UNPROCESSABLE_CONTENT,
    CODE_INVALID_INPUT: status.HTTP_422_UNPROCESSABLE_CONTENT,
    CODE_DAILY_LIMIT: status.HTTP_429_TOO_MANY_REQUESTS,
    CODE_REVISION_LIMIT: status.HTTP_429_TOO_MANY_REQUESTS,
    CODE_BUSY: status.HTTP_409_CONFLICT,
    CODE_TIMEOUT: status.HTTP_504_GATEWAY_TIMEOUT,
    CODE_REFUSED: status.HTTP_422_UNPROCESSABLE_CONTENT,
    CODE_OUTPUT_TRUNCATED: status.HTTP_422_UNPROCESSABLE_CONTENT,
    CODE_PROVIDER_ERROR: status.HTTP_502_BAD_GATEWAY,
    CODE_PROVIDER_UNAVAILABLE: status.HTTP_502_BAD_GATEWAY,
    CODE_AUTH_FAILED: status.HTTP_502_BAD_GATEWAY,
    CODE_RATE_LIMITED: status.HTTP_429_TOO_MANY_REQUESTS,
    CODE_NOT_APPLICABLE: status.HTTP_409_CONFLICT,
}


def _translate(exc: Exception) -> HTTPException:
    if isinstance(exc, AiDraftNotFoundError):
        return _error(status.HTTP_404_NOT_FOUND, exc.code, **exc.detail)
    if isinstance(exc, DraftNotFoundError):
        return _error(status.HTTP_404_NOT_FOUND, exc.code, draft_id=str(exc.draft_id))
    if isinstance(exc, AssistantError):
        return _error(
            _HTTP_STATUS.get(exc.code, status.HTTP_502_BAD_GATEWAY),
            exc.code,
            message=str(exc),
            **exc.detail,
        )
    raise exc


@router.get("")
def assistant_status(service: Service, draft_id: str | None = None) -> dict[str, Any]:
    parsed = _uuid(draft_id, "draft_not_found") if draft_id else None
    return {"as_of": _as_of(), "assistant": service.status(draft_id=parsed)}


@router.get("/proposals/{ai_draft_id}")
def get_proposal(ai_draft_id: str, service: Service) -> dict[str, Any]:
    try:
        return {
            "as_of": _as_of(),
            "proposal": service.get_proposal(_uuid(ai_draft_id, "ai_draft_not_found")),
        }
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/proposals", dependencies=[Depends(require_mutations_enabled)])
async def create_proposal(request: Request, service: Service) -> JSONResponse:
    body = await _read_body(
        request,
        {"user_text", "draft_id", "base_yaml_text", "parent_ai_draft_id", "request_token"},
        required={"user_text"},
    )
    draft_id = _uuid(body["draft_id"], "draft_not_found") if body.get("draft_id") else None
    parent = (
        _uuid(body["parent_ai_draft_id"], "ai_draft_not_found")
        if body.get("parent_ai_draft_id")
        else None
    )
    try:
        # The provider call blocks for up to the deadline; it must not block the event loop
        # (every other request, /health included, would queue behind it).
        proposal = await run_in_threadpool(
            service.propose,
            user_text=body["user_text"],
            draft_id=draft_id,
            base_yaml_text=body.get("base_yaml_text"),
            parent_ai_draft_id=parent,
            request_token=body.get("request_token"),
        )
    except Exception as exc:
        raise _translate(exc) from exc
    return JSONResponse(
        status_code=status.HTTP_200_OK, content={"as_of": _as_of(), "proposal": proposal}
    )


@router.post("/proposals/{ai_draft_id}/apply", dependencies=[Depends(require_mutations_enabled)])
async def apply_proposal(ai_draft_id: str, request: Request, service: Service) -> JSONResponse:
    body = await _read_body(request, {"draft_id", "title"})
    draft_id = _uuid(body["draft_id"], "draft_not_found") if body.get("draft_id") else None
    try:
        applied = await run_in_threadpool(
            service.apply,
            _uuid(ai_draft_id, "ai_draft_not_found"),
            draft_id=draft_id,
            title=body.get("title"),
        )
    except Exception as exc:
        raise _translate(exc) from exc
    return JSONResponse(status_code=status.HTTP_200_OK, content={"as_of": _as_of(), **applied})
