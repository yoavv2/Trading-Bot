"""Versioned run inspection endpoints."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from trading_platform.api.dependencies import (
    build_collection_response,
    get_operator_read_filters,
    get_operator_read_service,
    get_strategy_registry,
    resolve_strategy_metadata,
)
from trading_platform.services.operator_reads import OperatorReadFilters, OperatorReadService
from trading_platform.strategies.registry import StrategyRegistry

router = APIRouter(prefix="/api/v1/runs", tags=["runs"])


@router.get("")
def list_runs(
    filters: Annotated[OperatorReadFilters, Depends(get_operator_read_filters)],
    operator_reads: Annotated[OperatorReadService, Depends(get_operator_read_service)],
    registry: Annotated[StrategyRegistry, Depends(get_strategy_registry)],
) -> dict[str, object]:
    resolve_strategy_metadata(strategy_id=filters.strategy_id, registry=registry)
    return build_collection_response(
        filters=filters,
        items=operator_reads.list_runs(filters),
    )


@router.get("/{run_id}")
def run_detail(
    run_id: UUID,
    operator_reads: Annotated[OperatorReadService, Depends(get_operator_read_service)],
) -> dict[str, object]:
    try:
        return operator_reads.get_run_detail(str(run_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
