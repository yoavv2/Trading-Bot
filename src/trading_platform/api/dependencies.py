"""Shared FastAPI dependencies for the operator read surface."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

from fastapi import HTTPException, Query, Request

from trading_platform.core.settings import Settings
from trading_platform.db.models import JobStatus, StrategyRunStatus, StrategyRunType
from trading_platform.jobs.registry import JobRegistry, build_default_registry
from trading_platform.orchestration.job_mutations import JobOrchestrationService
from trading_platform.services.analytics import StrategyAnalyticsService
from trading_platform.services.job_reads import (
    DEFAULT_LIMIT as JOB_DEFAULT_LIMIT,
)
from trading_platform.services.job_reads import (
    DEFAULT_LOG_PAGE_SIZE as JOB_DEFAULT_LOG_PAGE_SIZE,
)
from trading_platform.services.job_reads import (
    MAX_LIMIT as JOB_MAX_LIMIT,
)
from trading_platform.services.job_reads import (
    MAX_LOG_PAGE_SIZE as JOB_MAX_LOG_PAGE_SIZE,
)
from trading_platform.services.job_reads import JobReadFilters, JobReadService
from trading_platform.services.operator_reads import OperatorReadFilters, OperatorReadService
from trading_platform.strategies.base import StrategyMetadata
from trading_platform.strategies.registry import (
    StrategyRegistry,
    UnknownStrategyError,
)
from trading_platform.strategies.registry import (
    build_default_registry as build_default_strategy_registry,
)

DEFAULT_STRATEGY_ID = "trend_following_daily"
DEFAULT_LIMIT = 20
MAX_LIMIT = 100

# Re-exported for route modules that need the log-pagination bounds without
# importing services.job_reads twice.
DEFAULT_LOG_PAGE_SIZE = JOB_DEFAULT_LOG_PAGE_SIZE
MAX_LOG_PAGE_SIZE = JOB_MAX_LOG_PAGE_SIZE


def get_settings(request: Request) -> Settings:
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        raise HTTPException(status_code=503, detail="Application settings not loaded yet.")
    return settings


def get_strategy_registry(request: Request) -> StrategyRegistry:
    return build_default_strategy_registry(get_settings(request))


def get_job_registry(request: Request) -> JobRegistry:
    registry = getattr(request.app.state, "job_registry", None)
    if registry is not None:
        return registry
    return build_default_registry(get_settings(request))


def get_job_orchestration_service(request: Request) -> JobOrchestrationService:
    return JobOrchestrationService(get_settings(request), get_job_registry(request))


def get_strategy_analytics_service(request: Request) -> StrategyAnalyticsService:
    return StrategyAnalyticsService(get_settings(request))


def get_operator_read_service(request: Request) -> OperatorReadService:
    return OperatorReadService(get_settings(request))


def get_job_read_service(request: Request) -> JobReadService:
    return JobReadService(get_settings(request))


def get_job_read_filters(
    status: JobStatus | None = Query(None),
    job_type: str | None = Query(None),
    limit: int = Query(JOB_DEFAULT_LIMIT, ge=1, le=JOB_MAX_LIMIT),
) -> JobReadFilters:
    return JobReadFilters(
        status=status.value if status is not None else None,
        job_type=job_type,
        limit=limit,
    )


def get_operator_read_filters(
    strategy_id: str = Query(DEFAULT_STRATEGY_ID),
    run_type: StrategyRunType | None = Query(None),
    status: StrategyRunStatus | None = Query(None),
    session_start: date | None = Query(None),
    session_end: date | None = Query(None),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
) -> OperatorReadFilters:
    return OperatorReadFilters(
        strategy_id=strategy_id,
        run_type=run_type.value if run_type is not None else None,
        status=status.value if status is not None else None,
        session_start=session_start,
        session_end=session_end,
        limit=limit,
    )


def resolve_strategy_metadata(
    *,
    strategy_id: str,
    registry: StrategyRegistry,
) -> StrategyMetadata:
    try:
        return registry.resolve(strategy_id).metadata
    except UnknownStrategyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def serialize_operator_filters(filters: OperatorReadFilters) -> dict[str, Any]:
    return {
        "strategy_id": filters.strategy_id,
        "run_type": filters.run_type,
        "status": filters.status,
        "session_start": filters.session_start.isoformat()
        if filters.session_start is not None
        else None,
        "session_end": filters.session_end.isoformat() if filters.session_end is not None else None,
        "limit": filters.limit,
    }


def serialize_job_filters(filters: JobReadFilters) -> dict[str, Any]:
    return {
        "status": filters.status,
        "job_type": filters.job_type,
        "limit": filters.limit,
    }


def build_collection_response(
    *,
    filters: OperatorReadFilters | JobReadFilters,
    items: list[dict[str, Any]],
    serializer: Callable[[Any], dict[str, Any]] = serialize_operator_filters,
) -> dict[str, Any]:
    return {
        "filters": serializer(filters),
        "count": len(items),
        "items": items,
    }


def build_operator_read_catalog(base_path: str) -> dict[str, Any]:
    normalized_base_path = base_path.rstrip("/")
    return {
        "status": "available",
        "version": 1,
        "read_only": True,
        "strategies": {
            "list": f"{normalized_base_path}/strategies",
            "detail": f"{normalized_base_path}/strategies/{{strategy_id}}",
        },
        "analytics": {
            "strategy_summary": f"{normalized_base_path}/analytics/strategies/{{strategy_id}}",
        },
        "runs": {
            "list": f"{normalized_base_path}/runs",
            "detail": f"{normalized_base_path}/runs/{{run_id}}",
        },
        "jobs": {
            "list": f"{normalized_base_path}/jobs",
            "detail": f"{normalized_base_path}/jobs/{{job_id}}",
            "progress": f"{normalized_base_path}/jobs/{{job_id}}/progress",
            "logs": f"{normalized_base_path}/jobs/{{job_id}}/logs",
            "events": f"{normalized_base_path}/jobs/{{job_id}}/events",
        },
        "operations": {
            "orders": f"{normalized_base_path}/operations/orders",
            "fills": f"{normalized_base_path}/operations/fills",
            "positions": f"{normalized_base_path}/operations/positions",
            "account_snapshots": f"{normalized_base_path}/operations/account-snapshots",
            "risk_events": f"{normalized_base_path}/operations/risk-events",
            "execution_events": f"{normalized_base_path}/operations/execution-events",
        },
    }


def build_strategy_operator_links(
    *,
    base_path: str,
    strategy_id: str,
) -> dict[str, str]:
    normalized_base_path = base_path.rstrip("/")
    return {
        "self": f"{normalized_base_path}/strategies/{strategy_id}",
        "analytics": f"{normalized_base_path}/analytics/strategies/{strategy_id}",
        "runs": f"{normalized_base_path}/runs?strategy_id={strategy_id}",
        "orders": f"{normalized_base_path}/operations/orders?strategy_id={strategy_id}",
        "fills": f"{normalized_base_path}/operations/fills?strategy_id={strategy_id}",
        "positions": f"{normalized_base_path}/operations/positions?strategy_id={strategy_id}",
        "account_snapshots": f"{normalized_base_path}/operations/account-snapshots?strategy_id={strategy_id}",
        "risk_events": f"{normalized_base_path}/operations/risk-events?strategy_id={strategy_id}",
        "execution_events": f"{normalized_base_path}/operations/execution-events?strategy_id={strategy_id}",
    }
