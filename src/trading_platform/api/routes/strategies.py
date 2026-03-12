"""Strategy visibility routes."""

from __future__ import annotations

from fastapi import APIRouter, Request

from trading_platform.core.settings import Settings
from trading_platform.strategies.registry import build_default_registry

router = APIRouter(tags=["strategies"])


def _get_settings(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[return-value]


@router.get("/strategies")
def list_strategies(request: Request) -> dict[str, object]:
    settings = _get_settings(request)
    registry = build_default_registry(settings)
    strategies = registry.list_public()
    return {
        "count": len(strategies),
        "strategies": strategies,
    }
