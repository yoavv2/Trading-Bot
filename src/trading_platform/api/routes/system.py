"""Minimal versioned system surface."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from trading_platform.api.dependencies import build_operator_read_catalog, get_settings, get_strategy_registry
from trading_platform.core.settings import Settings
from trading_platform.strategies.registry import StrategyRegistry

router = APIRouter(prefix="/api/v1", tags=["system"])


@router.get("/system")
def system(
    settings: Settings = Depends(get_settings),
    registry: StrategyRegistry = Depends(get_strategy_registry),
) -> dict[str, object]:

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
        "strategy_catalog": registry.list_public(),
        "database": {
            "driver": settings.database.driver,
            "host": settings.database.host,
            "port": settings.database.port,
            "name": settings.database.name,
            "readiness_checks_enabled": settings.readiness.dependency_checks_enabled,
            "readiness_required": settings.readiness.require_database,
            "schema_managed_by": "alembic",
        },
        "operator_read_api": build_operator_read_catalog(settings.api.base_path),
    }
