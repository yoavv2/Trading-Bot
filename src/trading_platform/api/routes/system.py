"""Minimal versioned system surface."""

from __future__ import annotations

from fastapi import APIRouter, Request

from trading_platform.core.settings import Settings
from trading_platform.strategies.registry import build_default_registry

router = APIRouter(prefix="/api/v1", tags=["system"])


def _get_settings(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[return-value]


@router.get("/system")
def system(request: Request) -> dict[str, object]:
    settings = _get_settings(request)
    registry = build_default_registry(settings)

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
    }
