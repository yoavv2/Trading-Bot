"""Minimal versioned system surface."""

from __future__ import annotations

from fastapi import APIRouter, Request

from trading_platform.core.settings import Settings

router = APIRouter(prefix="/api/v1", tags=["system"])


def _get_settings(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[return-value]


@router.get("/system")
def system(request: Request) -> dict[str, object]:
    settings = _get_settings(request)
    strategy = settings.strategies.trend_following_daily

    return {
        "application": {
            "name": settings.app.name,
            "version": settings.app.version,
            "environment": settings.app.environment,
            "operator_mode": settings.app.operator_mode,
        },
        "api": {
            "base_path": settings.api.base_path,
            "host": settings.api.host,
            "port": settings.api.port,
        },
        "strategy_catalog": [
            {
                "strategy_id": strategy.strategy_id,
                "display_name": strategy.display_name,
                "enabled": strategy.enabled,
                "universe_size": len(strategy.universe),
                "short_window": strategy.indicators.short_window,
                "long_window": strategy.indicators.long_window,
            }
        ],
        "database": {
            "host": settings.database.host,
            "port": settings.database.port,
            "name": settings.database.name,
        },
    }

