"""FastAPI application bootstrap for the trading platform."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI

from trading_platform.api.routes.analytics import router as analytics_router
from trading_platform.api.routes.health import router as health_router
from trading_platform.api.routes.jobs import router as jobs_router
from trading_platform.api.routes.operations import router as operations_router
from trading_platform.api.routes.runs import router as runs_router
from trading_platform.api.routes.strategies import router as strategies_router
from trading_platform.api.routes.system import router as system_router
from trading_platform.core.logging import configure_logging, get_logger
from trading_platform.core.settings import load_settings
from trading_platform.core.startup import enforce_startup_config
from trading_platform.services.config.validation import ExecutionMode


@asynccontextmanager
async def lifespan(app: FastAPI):
    # The API is a read-only surface (mode=BACKTEST — no broker secret is
    # required) with its own configurable DB-readiness reporting at
    # GET /ready (readiness.require_database); require_database=False here
    # so an API-only boot without a live DB (a supported deployment mode,
    # exercised by test_app_boot.py) still succeeds. CFG-04's unreachable-DB
    # preflight is enforced at the write-side entrypoints (worker
    # subcommands, dry-bootstrap) where domain services actually need a
    # database connection to do anything.
    settings = enforce_startup_config(mode=ExecutionMode.BACKTEST, require_database=False)
    configure_logging(settings.logging)
    logger = get_logger("trading_platform.bootstrap")

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
    app.include_router(strategies_router)
    app.include_router(analytics_router)
    app.include_router(runs_router)
    app.include_router(jobs_router)
    app.include_router(operations_router)
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
