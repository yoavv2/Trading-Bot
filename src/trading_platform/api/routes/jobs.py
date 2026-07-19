"""Read-only Job observation endpoints (JOB-07, D-15).

Scope boundary: this router is read-only. No POST, PUT, PATCH, or DELETE
handler belongs here. Job submission and cancellation endpoints are
Phase 18 (ORCH-01..04); D-15 scopes Phase 17 to observation only.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from trading_platform.api.dependencies import (
    build_collection_response,
    get_job_read_filters,
    get_job_read_service,
    serialize_job_filters,
)
from trading_platform.services.job_reads import (
    DEFAULT_LOG_PAGE_SIZE,
    MAX_LOG_PAGE_SIZE,
    JobReadFilters,
    JobReadService,
)

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


@router.get("")
def list_jobs(
    filters: Annotated[JobReadFilters, Depends(get_job_read_filters)],
    job_reads: Annotated[JobReadService, Depends(get_job_read_service)],
) -> dict[str, object]:
    return build_collection_response(
        filters=filters,
        items=job_reads.list_jobs(filters),
        serializer=serialize_job_filters,
    )


@router.get("/{job_id}")
def job_detail(
    job_id: UUID,
    job_reads: Annotated[JobReadService, Depends(get_job_read_service)],
) -> dict[str, object]:
    try:
        return job_reads.get_job_detail(str(job_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{job_id}/progress")
def job_progress(
    job_id: UUID,
    job_reads: Annotated[JobReadService, Depends(get_job_read_service)],
) -> dict[str, object]:
    try:
        return job_reads.get_job_progress(str(job_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{job_id}/logs")
def job_logs(
    job_id: UUID,
    job_reads: Annotated[JobReadService, Depends(get_job_read_service)],
    after_sequence: int | None = Query(None, ge=0),
    limit: int = Query(DEFAULT_LOG_PAGE_SIZE, ge=1, le=MAX_LOG_PAGE_SIZE),
) -> dict[str, object]:
    try:
        return job_reads.list_job_logs(str(job_id), after_sequence=after_sequence, limit=limit)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{job_id}/events")
def job_events(
    job_id: UUID,
    job_reads: Annotated[JobReadService, Depends(get_job_read_service)],
    limit: int = Query(DEFAULT_LOG_PAGE_SIZE, ge=1, le=MAX_LOG_PAGE_SIZE),
) -> dict[str, object]:
    try:
        items = job_reads.list_job_events(str(job_id), limit=limit)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"job_id": str(job_id), "count": len(items), "items": items}
