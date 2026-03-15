"""Versioned analytics endpoints backed by shared Phase 6 services."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from trading_platform.api.dependencies import (
    get_strategy_analytics_service,
    get_strategy_registry,
    resolve_strategy_metadata,
)
from trading_platform.services.analytics import StrategyAnalyticsService
from trading_platform.strategies.registry import StrategyRegistry

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])


@router.get("/strategies/{strategy_id}")
def strategy_analytics(
    strategy_id: str,
    analytics_service: Annotated[StrategyAnalyticsService, Depends(get_strategy_analytics_service)],
    registry: Annotated[StrategyRegistry, Depends(get_strategy_registry)],
    backtest_run_id: UUID | None = Query(None),
    paper_run_id: UUID | None = Query(None),
    inspection_limit: int = Query(5, ge=1, le=100),
) -> dict[str, object]:
    resolve_strategy_metadata(strategy_id=strategy_id, registry=registry)
    try:
        return analytics_service.summarize_strategy(
            strategy_id=strategy_id,
            backtest_run_id=str(backtest_run_id) if backtest_run_id is not None else None,
            paper_run_id=str(paper_run_id) if paper_run_id is not None else None,
            inspection_limit=inspection_limit,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
