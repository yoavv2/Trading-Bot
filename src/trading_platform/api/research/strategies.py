"""Research strategy authoring routes (plan S2): ``/api/v1/research/strategies/*``.

Synchronous HTTP writes on the research database, mounted only in research mode
(``create_app``). Every write carries ``require_mutations_enabled`` like the trading
mutations. ``POST .../validate`` is the one POST that writes nothing: it validates
submitted YAML text so the editor can check every change without saving a draft.

Error bodies are ``{"detail": {"code": ...}}`` objects (the console contract), never
FastAPI's list-shaped 422, so request bodies are parsed by hand.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse

from trading_platform.api.dependencies import get_settings, require_mutations_enabled
from trading_platform.services.research.strategies import (
    DraftInvalidError,
    DraftNotFoundError,
    InvalidDraftInputError,
    ResearchStrategyService,
    VersionNotFoundError,
    validate_yaml_text,
)

router = APIRouter(prefix="/api/v1/research/strategies", tags=["research-strategies"])


def get_research_strategy_service(request: Request) -> ResearchStrategyService:
    return ResearchStrategyService(get_settings(request))


Service = Annotated[ResearchStrategyService, Depends(get_research_strategy_service)]


def _error(status_code: int, code: str, **detail: Any) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, **detail})


def _as_of() -> str:
    return datetime.now(UTC).isoformat()


async def _read_body(request: Request, allowed_keys: set[str], *, required: set[str] = frozenset()) -> dict[str, Any]:
    try:
        parsed = json.loads(await request.body())
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as exc:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_request", reason="body_not_json") from exc
    if not isinstance(parsed, dict) or not set(parsed).issubset(allowed_keys):
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_request", reason="unknown_or_malformed_keys")
    missing = sorted(required - set(parsed))
    if missing:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_request", reason="missing_field", field=missing[0])
    return parsed


def _uuid(value: str, code: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise _error(status.HTTP_404_NOT_FOUND, code) from exc


def _translate(exc: Exception) -> HTTPException:
    if isinstance(exc, DraftNotFoundError):
        return _error(status.HTTP_404_NOT_FOUND, exc.code, draft_id=str(exc.draft_id))
    if isinstance(exc, VersionNotFoundError):
        return _error(status.HTTP_404_NOT_FOUND, exc.code, version_id=str(exc.version_id))
    if isinstance(exc, DraftInvalidError):
        return _error(status.HTTP_422_UNPROCESSABLE_CONTENT, exc.code, draft_id=str(exc.draft_id), errors=exc.errors)
    if isinstance(exc, InvalidDraftInputError):
        return _error(status.HTTP_422_UNPROCESSABLE_CONTENT, exc.code, field=exc.field, reason=exc.reason)
    raise exc


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


@router.get("")
def list_families(service: Service) -> dict[str, Any]:
    families = service.list_families()
    return {"as_of": _as_of(), "count": len(families), "families": families}


@router.get("/drafts")
def list_drafts(service: Service) -> dict[str, Any]:
    drafts = service.list_drafts()
    return {"as_of": _as_of(), "count": len(drafts), "drafts": drafts}


@router.get("/drafts/{draft_id}")
def get_draft(draft_id: str, service: Service) -> dict[str, Any]:
    try:
        draft = service.get_draft(_uuid(draft_id, "draft_not_found"))
    except DraftNotFoundError as exc:
        raise _translate(exc) from exc
    return {"as_of": _as_of(), "draft": draft}


@router.get("/drafts/{draft_id}/validation")
def get_draft_validation(draft_id: str, service: Service) -> dict[str, Any]:
    try:
        outcome = service.validate_draft(_uuid(draft_id, "draft_not_found"))
    except DraftNotFoundError as exc:
        raise _translate(exc) from exc
    return {"as_of": _as_of(), "validation": outcome}


@router.get("/versions")
def list_versions(service: Service, strategy_id: str | None = None) -> dict[str, Any]:
    family = _uuid(strategy_id, "version_not_found") if strategy_id else None
    versions = service.list_versions(strategy_id=family)
    return {"as_of": _as_of(), "count": len(versions), "versions": versions}


@router.get("/versions/{version_id}")
def get_version(version_id: str, service: Service) -> dict[str, Any]:
    try:
        version = service.get_version(_uuid(version_id, "version_not_found"))
    except VersionNotFoundError as exc:
        raise _translate(exc) from exc
    return {"as_of": _as_of(), "version": version}


@router.get("/versions/{version_id}/lineage")
def get_lineage(version_id: str, service: Service) -> dict[str, Any]:
    try:
        lineage = service.lineage(_uuid(version_id, "version_not_found"))
    except VersionNotFoundError as exc:
        raise _translate(exc) from exc
    return {"as_of": _as_of(), "count": len(lineage), "lineage": lineage}


# ---------------------------------------------------------------------------
# Ad-hoc validation (no write)
# ---------------------------------------------------------------------------


@router.post("/validate")
async def validate_text(request: Request) -> dict[str, Any]:
    body = await _read_body(request, {"yaml_text"}, required={"yaml_text"})
    if not isinstance(body["yaml_text"], str):
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_request", reason="yaml_text_not_string")
    return {"as_of": _as_of(), "validation": validate_yaml_text(body["yaml_text"]).to_dict()}


# ---------------------------------------------------------------------------
# Writes (research database only; guarded like every trading mutation)
# ---------------------------------------------------------------------------


@router.post("/drafts", dependencies=[Depends(require_mutations_enabled)])
async def create_draft(request: Request, service: Service) -> JSONResponse:
    body = await _read_body(request, {"title", "yaml_text"}, required={"title", "yaml_text"})
    try:
        draft = service.create_draft(title=body["title"], yaml_text=body["yaml_text"])
    except InvalidDraftInputError as exc:
        raise _translate(exc) from exc
    return JSONResponse(status_code=status.HTTP_201_CREATED, content={"draft": draft})


@router.put("/drafts/{draft_id}", dependencies=[Depends(require_mutations_enabled)])
async def update_draft(draft_id: str, request: Request, service: Service) -> dict[str, Any]:
    body = await _read_body(request, {"title", "yaml_text"})
    try:
        draft = service.update_draft(
            _uuid(draft_id, "draft_not_found"), title=body.get("title"), yaml_text=body.get("yaml_text")
        )
    except (DraftNotFoundError, InvalidDraftInputError) as exc:
        raise _translate(exc) from exc
    return {"draft": draft}


@router.delete("/drafts/{draft_id}", dependencies=[Depends(require_mutations_enabled)])
def delete_draft(draft_id: str, service: Service) -> dict[str, Any]:
    try:
        return service.delete_draft(_uuid(draft_id, "draft_not_found"))
    except DraftNotFoundError as exc:
        raise _translate(exc) from exc


@router.post("/drafts/{draft_id}/duplicate", dependencies=[Depends(require_mutations_enabled)])
def duplicate_draft(draft_id: str, service: Service) -> JSONResponse:
    try:
        draft = service.duplicate_draft(_uuid(draft_id, "draft_not_found"))
    except DraftNotFoundError as exc:
        raise _translate(exc) from exc
    return JSONResponse(status_code=status.HTTP_201_CREATED, content={"draft": draft})


@router.post("/drafts/{draft_id}/approve", dependencies=[Depends(require_mutations_enabled)])
def approve_draft(draft_id: str, service: Service) -> JSONResponse:
    try:
        version = service.approve_draft(_uuid(draft_id, "draft_not_found"))
    except (DraftNotFoundError, DraftInvalidError) as exc:
        raise _translate(exc) from exc
    return JSONResponse(status_code=status.HTTP_201_CREATED, content={"version": version})


@router.post("/versions/{version_id}/edit", dependencies=[Depends(require_mutations_enabled)])
def edit_version(version_id: str, service: Service) -> JSONResponse:
    try:
        draft = service.draft_from_version(_uuid(version_id, "version_not_found"), mode="edit")
    except VersionNotFoundError as exc:
        raise _translate(exc) from exc
    return JSONResponse(status_code=status.HTTP_201_CREATED, content={"draft": draft})


@router.post("/versions/{version_id}/duplicate", dependencies=[Depends(require_mutations_enabled)])
def duplicate_version(version_id: str, service: Service) -> JSONResponse:
    try:
        draft = service.draft_from_version(_uuid(version_id, "version_not_found"), mode="duplicate")
    except VersionNotFoundError as exc:
        raise _translate(exc) from exc
    return JSONResponse(status_code=status.HTTP_201_CREATED, content={"draft": draft})
