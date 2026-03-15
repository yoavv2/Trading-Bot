"""Strategy visibility routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from trading_platform.api.dependencies import (
    build_operator_read_catalog,
    build_strategy_operator_links,
    get_settings,
    get_strategy_registry,
    resolve_strategy_metadata,
)
from trading_platform.core.settings import Settings
from trading_platform.strategies.registry import StrategyRegistry

router = APIRouter(tags=["strategies"])


@router.get("/strategies", include_in_schema=False)
@router.get("/api/v1/strategies")
def list_strategies(
    settings: Settings = Depends(get_settings),
    registry: StrategyRegistry = Depends(get_strategy_registry),
) -> dict[str, object]:
    strategies = registry.list_public()
    return {
        "count": len(strategies),
        "strategies": strategies,
        "operator_read_api": build_operator_read_catalog(settings.api.base_path),
    }


@router.get("/api/v1/strategies/{strategy_id}")
def strategy_detail(
    strategy_id: str,
    settings: Settings = Depends(get_settings),
    registry: StrategyRegistry = Depends(get_strategy_registry),
) -> dict[str, object]:
    metadata = resolve_strategy_metadata(strategy_id=strategy_id, registry=registry)
    return {
        "strategy": metadata.to_public_dict(),
        "operator_reads": build_strategy_operator_links(
            base_path=settings.api.base_path,
            strategy_id=strategy_id,
        ),
    }
