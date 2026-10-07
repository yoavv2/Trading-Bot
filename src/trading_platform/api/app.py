"""FastAPI application bootstrap for the trading platform."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from trading_platform.api.research.strategies import router as research_strategies_router
from trading_platform.api.research.studies import revisions_router as research_revisions_router
from trading_platform.api.research.studies import studies_router as research_studies_router
from trading_platform.api.routes.analytics import router as analytics_router
from trading_platform.api.routes.controls import router as controls_router
from trading_platform.api.routes.execution_operations import (
    router as execution_operations_router,
)
from trading_platform.api.routes.health import router as health_router
from trading_platform.api.routes.job_types import router as job_types_router
from trading_platform.api.routes.jobs import router as jobs_router
from trading_platform.api.routes.market_data import router as market_data_router
from trading_platform.api.routes.operations import router as operations_router
from trading_platform.api.routes.recovery import router as recovery_router
from trading_platform.api.routes.runs import router as runs_router
from trading_platform.api.routes.strategies import router as strategies_router
from trading_platform.api.routes.system import router as system_router
from trading_platform.core.logging import configure_logging, get_logger
from trading_platform.core.settings import load_settings
from trading_platform.core.startup import enforce_startup_config
from trading_platform.jobs.registry import JobRegistry, build_registry_for
from trading_platform.services.config.validation import ExecutionMode


@asynccontextmanager
async def lifespan(app: FastAPI):
    # The mutation-capable API must not advertise Job operations until durable
    # PostgreSQL state is available. This gate runs before app state or the
    # production registry is initialized so a failed preflight never serves.
    settings = enforce_startup_config(mode=ExecutionMode.BACKTEST, require_database=True)
    configure_logging(settings.logging)
    logger = get_logger("trading_platform.bootstrap")

    # The router set was chosen at construction from the same settings. A process whose
    # environment changed in between would otherwise serve trading routes in research
    # mode (or the reverse); refuse to start rather than serve the wrong surface.
    if bool(settings.research.mode) != bool(getattr(app.state, "research_mode", False)):
        raise RuntimeError(
            "research.mode changed between application construction and startup; "
            "restart the process so the mounted routers match the settings."
        )

    if getattr(app.state, "job_registry", None) is None:
        app.state.job_registry = build_registry_for(settings)
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


async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Last-resort JSON body so no route ever returns a bare text 500.

    The console's mutation error contract requires ``detail`` to be an object
    carrying a ``code``; HTTPException and typed route errors already do, this
    covers anything that escapes them.
    """
    get_logger("trading_platform.api").error(
        "unhandled_api_error",
        exc_info=exc,
        extra={"context": {"path": request.url.path, "error_type": type(exc).__name__}},
    )
    return JSONResponse(status_code=500, content={"detail": {"code": "internal_error"}})


#: Infrastructure routers every process serves: liveness/readiness and the generic Job
#: surface (list, detail, progress, logs, events, submit/cancel/retry). In research mode the
#: Job routes act on the research-only registry, so no trading Job type is submittable.
_INFRASTRUCTURE_ROUTERS = (health_router, jobs_router, job_types_router)

#: Trading surface: strategy catalog, analytics, runs, operations, system, safety controls,
#: recovery and execution operations, market-data state. Mounted only outside research mode.
_TRADING_ROUTERS = (
    strategies_router,
    analytics_router,
    runs_router,
    operations_router,
    system_router,
    controls_router,
    recovery_router,
    execution_operations_router,
    market_data_router,
)

#: Research surface (``/api/v1/research/*``): mounted only in research mode, so no research
#: route exists on a trading process and no trading route exists on a research process.
_RESEARCH_ROUTERS = (research_strategies_router, research_studies_router, research_revisions_router)


def create_app(
    *, job_registry: JobRegistry | None = None, research_mode: bool | None = None
) -> FastAPI:
    """Build the application for one mode.

    ``research_mode`` defaults to ``settings.research.mode``. The two surfaces are
    disjoint apart from the infrastructure routers; the lifespan re-checks the mode
    against the startup settings and refuses to serve on a mismatch.
    """

    mode = load_settings().research.mode if research_mode is None else bool(research_mode)
    app = FastAPI(
        title="Trading Strategy Platform" if not mode else "Trading Strategy Platform (research)",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.research_mode = mode
    if job_registry is not None:
        app.state.job_registry = job_registry
    app.add_exception_handler(Exception, _unhandled_exception_handler)
    for router in _INFRASTRUCTURE_ROUTERS:
        app.include_router(router)
    for router in _RESEARCH_ROUTERS if mode else _TRADING_ROUTERS:
        app.include_router(router)
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
