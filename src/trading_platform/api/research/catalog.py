"""Research catalog and saved-list routes: ``/api/v1/research/catalog/*`` (reads) and
``/api/v1/research/asset-lists/*`` (synchronous writes on the research database).
A catalog refresh is the ``catalog-sync`` Job, submitted through the generic Job route.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse

from trading_platform.api.dependencies import get_settings, require_mutations_enabled
from trading_platform.services.research.asset_lists import AssetCatalogService, AssetListError

catalog_router = APIRouter(prefix="/api/v1/research/catalog", tags=["research-catalog"])
asset_lists_router = APIRouter(prefix="/api/v1/research/asset-lists", tags=["research-catalog"])


def get_catalog_service(request: Request) -> AssetCatalogService:
    return AssetCatalogService(get_settings(request))


Service = Annotated[AssetCatalogService, Depends(get_catalog_service)]


def _error(status_code: int, code: str, **detail: Any) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, **detail})


def _as_of() -> str:
    return datetime.now(UTC).isoformat()


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise _error(status.HTTP_404_NOT_FOUND, "asset_list_not_found") from exc


def _translate(exc: AssetListError) -> HTTPException:
    detail: dict[str, Any] = {}
    if hasattr(exc, "field"):
        detail = {"field": exc.field, "reason": exc.reason}
    elif hasattr(exc, "list_id"):
        detail = {"list_id": str(exc.list_id)}
    return _error(exc.status, exc.code, **detail)


async def _read_body(request: Request, allowed: set[str], *, required: set[str] = frozenset()) -> dict[str, Any]:
    try:
        parsed = json.loads(await request.body() or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as exc:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_request", reason="body_not_json") from exc
    if not isinstance(parsed, dict) or not set(parsed).issubset(allowed):
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_request", reason="unknown_or_malformed_keys")
    missing = sorted(required - set(parsed))
    if missing:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_request", reason="missing_field", field=missing[0])
    return parsed


@catalog_router.get("")
def catalog_coverage(service: Service) -> dict[str, Any]:
    return {"as_of": _as_of(), "catalog": service.coverage()}


@catalog_router.get("/search")
def catalog_search(service: Service, q: str = Query(default="", max_length=64), limit: int = Query(default=20, ge=1, le=100)) -> dict[str, Any]:
    return {"as_of": _as_of(), **service.search(q, limit=limit)}


@catalog_router.get("/assets/{ticker}")
def catalog_asset(ticker: str, service: Service) -> dict[str, Any]:
    asset = service.asset(ticker)
    if asset is None:
        raise _error(status.HTTP_404_NOT_FOUND, "asset_not_in_catalog", ticker=ticker.upper())
    return {"as_of": _as_of(), "asset": asset}


@asset_lists_router.get("")
def list_asset_lists(service: Service) -> dict[str, Any]:
    lists = service.list_lists()
    return {"as_of": _as_of(), "count": len(lists), "lists": lists}


@asset_lists_router.post("", dependencies=[Depends(require_mutations_enabled)])
async def create_asset_list(request: Request, service: Service) -> JSONResponse:
    body = await _read_body(request, {"name", "tickers"}, required={"name", "tickers"})
    try:
        created = service.create_list(name=body["name"], tickers=body["tickers"])
    except AssetListError as exc:
        raise _translate(exc) from exc
    return JSONResponse(status_code=status.HTTP_201_CREATED, content={"list": created})


@asset_lists_router.get("/{list_id}")
def get_asset_list(list_id: str, service: Service) -> dict[str, Any]:
    try:
        return {"as_of": _as_of(), "list": service.get_list(_uuid(list_id))}
    except AssetListError as exc:
        raise _translate(exc) from exc


@asset_lists_router.put("/{list_id}", dependencies=[Depends(require_mutations_enabled)])
async def update_asset_list(list_id: str, request: Request, service: Service) -> dict[str, Any]:
    body = await _read_body(request, {"name", "tickers"})
    try:
        return {"list": service.update_list(_uuid(list_id), name=body.get("name"), tickers=body.get("tickers"))}
    except AssetListError as exc:
        raise _translate(exc) from exc


@asset_lists_router.delete("/{list_id}", dependencies=[Depends(require_mutations_enabled)])
def delete_asset_list(list_id: str, service: Service) -> dict[str, Any]:
    try:
        return service.delete_list(_uuid(list_id))
    except AssetListError as exc:
        raise _translate(exc) from exc
