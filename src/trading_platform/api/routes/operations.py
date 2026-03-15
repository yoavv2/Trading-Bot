"""Versioned operational inspection endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from trading_platform.api.dependencies import (
    build_collection_response,
    get_operator_read_filters,
    get_operator_read_service,
    get_strategy_registry,
    resolve_strategy_metadata,
)
from trading_platform.services.operator_reads import OperatorReadFilters, OperatorReadService
from trading_platform.strategies.registry import StrategyRegistry

router = APIRouter(prefix="/api/v1/operations", tags=["operations"])


@router.get("/orders")
def list_orders(
    filters: Annotated[OperatorReadFilters, Depends(get_operator_read_filters)],
    operator_reads: Annotated[OperatorReadService, Depends(get_operator_read_service)],
    registry: Annotated[StrategyRegistry, Depends(get_strategy_registry)],
) -> dict[str, object]:
    resolve_strategy_metadata(strategy_id=filters.strategy_id, registry=registry)
    return build_collection_response(filters=filters, items=operator_reads.list_paper_orders(filters))


@router.get("/fills")
def list_fills(
    filters: Annotated[OperatorReadFilters, Depends(get_operator_read_filters)],
    operator_reads: Annotated[OperatorReadService, Depends(get_operator_read_service)],
    registry: Annotated[StrategyRegistry, Depends(get_strategy_registry)],
) -> dict[str, object]:
    resolve_strategy_metadata(strategy_id=filters.strategy_id, registry=registry)
    return build_collection_response(filters=filters, items=operator_reads.list_paper_fills(filters))


@router.get("/positions")
def list_positions(
    filters: Annotated[OperatorReadFilters, Depends(get_operator_read_filters)],
    operator_reads: Annotated[OperatorReadService, Depends(get_operator_read_service)],
    registry: Annotated[StrategyRegistry, Depends(get_strategy_registry)],
) -> dict[str, object]:
    resolve_strategy_metadata(strategy_id=filters.strategy_id, registry=registry)
    return build_collection_response(filters=filters, items=operator_reads.list_positions(filters))


@router.get("/account-snapshots")
def list_account_snapshots(
    filters: Annotated[OperatorReadFilters, Depends(get_operator_read_filters)],
    operator_reads: Annotated[OperatorReadService, Depends(get_operator_read_service)],
    registry: Annotated[StrategyRegistry, Depends(get_strategy_registry)],
) -> dict[str, object]:
    resolve_strategy_metadata(strategy_id=filters.strategy_id, registry=registry)
    return build_collection_response(filters=filters, items=operator_reads.list_account_snapshots(filters))


@router.get("/risk-events")
def list_risk_events(
    filters: Annotated[OperatorReadFilters, Depends(get_operator_read_filters)],
    operator_reads: Annotated[OperatorReadService, Depends(get_operator_read_service)],
    registry: Annotated[StrategyRegistry, Depends(get_strategy_registry)],
) -> dict[str, object]:
    resolve_strategy_metadata(strategy_id=filters.strategy_id, registry=registry)
    return build_collection_response(filters=filters, items=operator_reads.list_risk_events(filters))


@router.get("/execution-events")
def list_execution_events(
    filters: Annotated[OperatorReadFilters, Depends(get_operator_read_filters)],
    operator_reads: Annotated[OperatorReadService, Depends(get_operator_read_service)],
    registry: Annotated[StrategyRegistry, Depends(get_strategy_registry)],
) -> dict[str, object]:
    resolve_strategy_metadata(strategy_id=filters.strategy_id, registry=registry)
    return build_collection_response(filters=filters, items=operator_reads.list_execution_events(filters))
