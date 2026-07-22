"""Job observation and mutation endpoints."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from trading_platform.api.dependencies import (
    build_collection_response,
    get_job_orchestration_service,
    get_job_read_filters,
    get_job_read_service,
    serialize_job_filters,
)
from trading_platform.jobs.registry import InvalidJobPayloadError
from trading_platform.orchestration.job_mutations import (
    IdempotencyConflictError,
    InvalidCancellationReasonError,
    InvalidIdempotencyKeyError,
    JobMutationNotFoundError,
    JobOrchestrationService,
    JobTerminalConflictError,
    MissingIdempotencyKeyError,
    UnknownJobTypeForSubmissionError,
)
from trading_platform.services.job_reads import (
    DEFAULT_LOG_PAGE_SIZE,
    MAX_LOG_PAGE_SIZE,
    JobReadFilters,
    JobReadService,
)

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


class SubmitJobRequest(BaseModel):
    """Generic public submission request; Job types validate their own payload."""

    job_type: str = Field(max_length=64)
    payload: dict[str, Any]

    @field_validator("job_type")
    @classmethod
    def normalize_job_type(cls, job_type: str) -> str:
        normalized = job_type.strip()
        if not normalized:
            raise ValueError("Job type must be nonblank.")
        return normalized


class CancelJobRequest(BaseModel):
    """Cancellation input normalized by the application orchestration service."""

    reason: str | None = None


def _error(status_code: int, code: str, **detail: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, **detail})


def _mutation_response(
    *,
    reference: Mapping[str, object],
    status_code: int,
    replayed: bool,
) -> JSONResponse:
    headers = {"Idempotency-Replayed": "true"} if replayed else None
    return JSONResponse(content=dict(reference), status_code=status_code, headers=headers)


@router.post("")
def submit_job(
    request: SubmitJobRequest,
    orchestration: Annotated[JobOrchestrationService, Depends(get_job_orchestration_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JSONResponse:
    try:
        result = orchestration.submit(
            job_type=request.job_type,
            payload=request.payload,
            idempotency_key=idempotency_key,
        )
    except MissingIdempotencyKeyError as exc:
        raise _error(status.HTTP_400_BAD_REQUEST, "missing_idempotency_key") from exc
    except InvalidIdempotencyKeyError as exc:
        raise _error(status.HTTP_400_BAD_REQUEST, "invalid_idempotency_key") from exc
    except UnknownJobTypeForSubmissionError as exc:
        raise _error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "unknown_job_type",
            job_type=exc.job_type,
        ) from exc
    except InvalidJobPayloadError as exc:
        raise _error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "invalid_job_payload",
            job_type=exc.job_type,
        ) from exc
    except IdempotencyConflictError as exc:
        raise _error(
            status.HTTP_409_CONFLICT,
            "idempotency_key_conflict",
            original_job_id=exc.original_job_id,
        ) from exc

    return _mutation_response(
        reference=result.reference.to_dict(),
        status_code=status.HTTP_200_OK if result.replayed else status.HTTP_202_ACCEPTED,
        replayed=result.replayed,
    )


@router.post("/{job_id}/cancel")
def cancel_job(
    job_id: UUID,
    request: CancelJobRequest,
    orchestration: Annotated[JobOrchestrationService, Depends(get_job_orchestration_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JSONResponse:
    try:
        result = orchestration.cancel(
            job_id=job_id,
            reason=request.reason,
            idempotency_key=idempotency_key,
        )
    except MissingIdempotencyKeyError as exc:
        raise _error(status.HTTP_400_BAD_REQUEST, "missing_idempotency_key") from exc
    except InvalidIdempotencyKeyError as exc:
        raise _error(status.HTTP_400_BAD_REQUEST, "invalid_idempotency_key") from exc
    except InvalidCancellationReasonError as exc:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_cancellation_reason") from exc
    except JobMutationNotFoundError as exc:
        raise _error(status.HTTP_404_NOT_FOUND, "job_not_found", job_id=str(exc.job_id)) from exc
    except JobTerminalConflictError as exc:
        raise _error(
            status.HTTP_409_CONFLICT,
            "job_not_cancellable",
            job_id=str(exc.job_id),
            status=exc.status,
        ) from exc
    except IdempotencyConflictError as exc:
        raise _error(
            status.HTTP_409_CONFLICT,
            "idempotency_key_conflict",
            original_job_id=exc.original_job_id,
        ) from exc

    return _mutation_response(
        reference=result.reference.to_dict(),
        status_code=status.HTTP_200_OK,
        replayed=result.replayed,
    )


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
