"""Health and readiness routes."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from trading_platform.core.settings import Settings
from trading_platform.db.session import check_database_connection

router = APIRouter(tags=["health"])


def _get_settings(request: Request) -> Settings:
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        raise HTTPException(status_code=503, detail="Application settings not loaded yet.")
    return settings


@router.get("/health")
def health(request: Request) -> dict[str, object]:
    settings = _get_settings(request)
    return {
        "status": "ok",
        "service": settings.app.slug,
        "version": settings.app.version,
        "timestamp": datetime.now(UTC).isoformat(),
    }


@router.get("/ready")
def ready(request: Request) -> JSONResponse:
    settings = _get_settings(request)
    started_at = getattr(request.app.state, "started_at", None)
    bootstrapped = bool(getattr(request.app.state, "bootstrapped", False))
    database_enabled = settings.readiness.dependency_checks_enabled
    database_required = settings.readiness.require_database
    database_ok = True
    database_detail = "Database readiness checks disabled by configuration."

    if database_enabled:
        database_ok, database_detail = check_database_connection(settings)

    checks = {
        "application": {
            "status": "ok" if bootstrapped else "starting",
            "detail": "FastAPI lifespan bootstrap completed." if bootstrapped else "Bootstrap still in progress.",
        },
        "configuration": {
            "status": "ok",
            "detail": "Typed file-first settings loaded successfully.",
        },
        "database": {
            "status": "skipped" if not database_enabled else "ok" if database_ok else "error",
            "detail": database_detail,
            "required": database_required,
        },
    }
    is_ready = bootstrapped and (database_ok or not database_required)
    status_code = 200 if is_ready else 503

    return JSONResponse(
        status_code=status_code,
        content={
            "status": "ready" if is_ready else "degraded" if bootstrapped else "starting",
            "ready": is_ready,
            "started_at": started_at,
            "timestamp": datetime.now(UTC).isoformat(),
            "checks": checks,
        },
    )
