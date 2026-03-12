"""FastAPI application bootstrap for the trading platform."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI

from trading_platform.api.routes.health import router as health_router
from trading_platform.api.routes.system import router as system_router
from trading_platform.core.logging import configure_logging
from trading_platform.core.settings import load_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    configure_logging(settings.logging)
    logger = logging.getLogger("trading_platform.bootstrap")

    app.state.settings = settings
    app.state.started_at = datetime.now(UTC).isoformat()
    app.state.bootstrapped = True

    logger.info(
        "application_started",
        extra={
            "context": {
                "environment": settings.app.environment,
                "version": settings.app.version,
                "database_host": settings.database.host,
            }
        },
    )
    try:
        yield
    finally:
        logger.info(
            "application_stopped",
            extra={"context": {"environment": settings.app.environment}},
        )
        app.state.bootstrapped = False


def create_app() -> FastAPI:
    app = FastAPI(
        title="Trading Strategy Platform",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.include_router(health_router)
    app.include_router(system_router)
    return app


app = create_app()


def main() -> None:
    import uvicorn

    settings = load_settings()
    uvicorn.run(
        "trading_platform.api.app:app",
        host=settings.api.host,
        port=settings.api.port,
        reload=False,
    )

