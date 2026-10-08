"""Research study routes (plan S3): ``/api/v1/research/studies/*`` and
``/api/v1/research/revisions/*`` (two routers so a revision path never collides with a
study id). Writes carry ``require_mutations_enabled``; long-running work is submitted as
Jobs and observed through the generic Job surface. Reads of results or an export flip
the revision's test-window exposures to ``results_inspected``.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse

from trading_platform.api.dependencies import get_settings, require_mutations_enabled
from trading_platform.orchestration.research_studies import build_study_service
from trading_platform.services.research.freeze import InputsChangedAfterFreezeError
from trading_platform.services.research.studies import StudyError, StudyService

studies_router = APIRouter(prefix="/api/v1/research/studies", tags=["research-studies"])
revisions_router = APIRouter(prefix="/api/v1/research/revisions", tags=["research-studies"])


def get_study_service(request: Request) -> StudyService:
    return build_study_service(get_settings(request))


Service = Annotated[StudyService, Depends(get_study_service)]


def _error(status_code: int, code: str, **detail: Any) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, **detail})


def _as_of() -> str:
    return datetime.now(UTC).isoformat()


def _uuid(value: str, code: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise _error(status.HTTP_404_NOT_FOUND, code) from exc


def _translate(exc: Exception) -> HTTPException:
    if isinstance(exc, StudyError):
        return _error(exc.status, exc.code, **exc.detail)
    if isinstance(exc, InputsChangedAfterFreezeError):
        return _error(status.HTTP_409_CONFLICT, exc.code, reasons=exc.reasons)
    raise exc


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


# ---------------------------------------------------------------------------
# Studies
# ---------------------------------------------------------------------------


@studies_router.get("")
def list_studies(service: Service) -> dict[str, Any]:
    studies = service.list_studies()
    return {"as_of": _as_of(), "count": len(studies), "studies": studies}


@studies_router.post("", dependencies=[Depends(require_mutations_enabled)])
async def create_study(request: Request, service: Service) -> JSONResponse:
    body = await _read_body(request, {"name", "kind", "settings"}, required={"name", "settings"})
    if not isinstance(body["settings"], dict):
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_request", reason="settings_not_object")
    try:
        created = service.create_study(name=body["name"], kind=body.get("kind", "substantive"), settings=body["settings"])
    except (StudyError, InputsChangedAfterFreezeError) as exc:
        raise _translate(exc) from exc
    return JSONResponse(status_code=status.HTTP_201_CREATED, content=created)


@studies_router.get("/{study_id}")
def get_study(study_id: str, service: Service) -> dict[str, Any]:
    try:
        return {"as_of": _as_of(), "study": service.get_study(_uuid(study_id, "study_not_found"))}
    except (StudyError, InputsChangedAfterFreezeError) as exc:
        raise _translate(exc) from exc


@studies_router.post("/{study_id}/revisions", dependencies=[Depends(require_mutations_enabled)])
async def create_revision(study_id: str, request: Request, service: Service) -> JSONResponse:
    body = await _read_body(request, {"settings"}, required={"settings"})
    if not isinstance(body["settings"], dict):
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_request", reason="settings_not_object")
    try:
        revision = service.create_revision(_uuid(study_id, "study_not_found"), settings=body["settings"])
    except (StudyError, InputsChangedAfterFreezeError) as exc:
        raise _translate(exc) from exc
    return JSONResponse(status_code=status.HTTP_201_CREATED, content={"revision": revision})


# ---------------------------------------------------------------------------
# Revisions
# ---------------------------------------------------------------------------


@revisions_router.get("/{revision_id}")
def get_revision(revision_id: str, service: Service) -> dict[str, Any]:
    try:
        return {"as_of": _as_of(), "revision": service.get_revision(_uuid(revision_id, "revision_not_found"))}
    except (StudyError, InputsChangedAfterFreezeError) as exc:
        raise _translate(exc) from exc


@revisions_router.get("/{revision_id}/readiness")
def get_readiness(revision_id: str, service: Service) -> dict[str, Any]:
    try:
        return {"as_of": _as_of(), "readiness": service.readiness(_uuid(revision_id, "revision_not_found"))}
    except (StudyError, InputsChangedAfterFreezeError) as exc:
        raise _translate(exc) from exc


@revisions_router.post("/{revision_id}/run", dependencies=[Depends(require_mutations_enabled)])
def run_revision(revision_id: str, service: Service) -> JSONResponse:
    try:
        submitted = service.run_initial(_uuid(revision_id, "revision_not_found"))
    except (StudyError, InputsChangedAfterFreezeError) as exc:
        raise _translate(exc) from exc
    return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=submitted)


@revisions_router.get("/{revision_id}/progress")
def get_progress(revision_id: str, service: Service) -> dict[str, Any]:
    try:
        return {"as_of": _as_of(), **service.progress(_uuid(revision_id, "revision_not_found"))}
    except (StudyError, InputsChangedAfterFreezeError) as exc:
        raise _translate(exc) from exc


@revisions_router.get("/{revision_id}/results")
def get_results(revision_id: str, service: Service) -> dict[str, Any]:
    try:
        return {"as_of": _as_of(), **service.results(_uuid(revision_id, "revision_not_found"))}
    except (StudyError, InputsChangedAfterFreezeError) as exc:
        raise _translate(exc) from exc


@revisions_router.get("/{revision_id}/comparison")
def get_comparison(revision_id: str, service: Service) -> dict[str, Any]:
    try:
        return {"as_of": _as_of(), **service.comparison(_uuid(revision_id, "revision_not_found"))}
    except (StudyError, InputsChangedAfterFreezeError) as exc:
        raise _translate(exc) from exc


@revisions_router.get("/{revision_id}/exposures")
def get_exposures(revision_id: str, service: Service) -> dict[str, Any]:
    try:
        return {"as_of": _as_of(), **service.exposures(_uuid(revision_id, "revision_not_found"))}
    except (StudyError, InputsChangedAfterFreezeError) as exc:
        raise _translate(exc) from exc


@revisions_router.get("/{revision_id}/runs/{run_id}/curve")
def get_run_curve(revision_id: str, run_id: str, service: Service) -> dict[str, Any]:
    try:
        return {"as_of": _as_of(), **service.run_curve(_uuid(revision_id, "revision_not_found"), _uuid(run_id, "revision_not_found"))}
    except (StudyError, InputsChangedAfterFreezeError) as exc:
        raise _translate(exc) from exc


@revisions_router.get("/{revision_id}/report", response_class=HTMLResponse)
def get_report(revision_id: str, service: Service, format: str = "html") -> Any:
    try:
        documents = service.report_documents(_uuid(revision_id, "revision_not_found"))
    except (StudyError, InputsChangedAfterFreezeError) as exc:
        raise _translate(exc) from exc
    if format == "md":
        return HTMLResponse(content=documents["markdown"], media_type="text/markdown; charset=utf-8")
    return HTMLResponse(content=documents["html"])


@revisions_router.post("/{revision_id}/freeze", dependencies=[Depends(require_mutations_enabled)])
async def freeze_revision(revision_id: str, request: Request, service: Service) -> JSONResponse:
    body = await _read_body(
        request,
        {"strategy_version_id", "asset", "acceptance", "co_leading_choice_reason"},
        required={"strategy_version_id", "asset", "acceptance"},
    )
    if not isinstance(body["acceptance"], dict) or not isinstance(body["asset"], str):
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_request", reason="acceptance_or_asset_malformed")
    try:
        frozen = service.freeze_candidate(
            _uuid(revision_id, "revision_not_found"),
            strategy_version_id=_uuid(str(body["strategy_version_id"]), "version_not_found"),
            asset=body["asset"],
            acceptance=body["acceptance"],
            co_leading_choice_reason=body.get("co_leading_choice_reason"),
        )
    except (StudyError, InputsChangedAfterFreezeError) as exc:
        raise _translate(exc) from exc
    return JSONResponse(status_code=status.HTTP_201_CREATED, content=frozen)


@revisions_router.post("/{revision_id}/final-test", dependencies=[Depends(require_mutations_enabled)])
async def run_final_test(revision_id: str, request: Request, service: Service) -> JSONResponse:
    body = await _read_body(request, {"rerun"})
    rerun = body.get("rerun", False)
    if not isinstance(rerun, bool):
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_request", reason="rerun_not_boolean")
    try:
        submitted = service.run_final_test(_uuid(revision_id, "revision_not_found"), rerun=rerun)
    except (StudyError, InputsChangedAfterFreezeError) as exc:
        raise _translate(exc) from exc
    return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=submitted)


@revisions_router.post("/{revision_id}/export", dependencies=[Depends(require_mutations_enabled)])
def export_revision(revision_id: str, service: Service) -> dict[str, Any]:
    try:
        return service.export(_uuid(revision_id, "revision_not_found"))
    except (StudyError, InputsChangedAfterFreezeError) as exc:
        raise _translate(exc) from exc
