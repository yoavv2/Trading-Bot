"""Dry bootstrap orchestration for the initial strategy platform proof."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from trading_platform.core.settings import Settings, load_settings
from trading_platform.db.models import Strategy, StrategyRun, StrategyRunStatus, StrategyRunType, StrategyStatus
from trading_platform.db.session import session_scope
from trading_platform.services.analytics import AnalyticsService, PlaceholderAnalyticsService
from trading_platform.services.data import MarketDataService, PlaceholderMarketDataService
from trading_platform.services.execution import ExecutionService, PlaceholderExecutionService
from trading_platform.services.risk import PlaceholderRiskService, RiskService
from trading_platform.strategies.base import StrategyMetadata
from trading_platform.strategies.registry import StrategyRegistry, build_default_registry


@dataclass(frozen=True)
class PlatformServices:
    data: MarketDataService
    risk: RiskService
    execution: ExecutionService
    analytics: AnalyticsService

    def describe(self) -> list[dict[str, Any]]:
        return [
            self.data.describe(),
            self.risk.describe(),
            self.execution.describe(),
            self.analytics.describe(),
        ]


@dataclass(frozen=True)
class DryRunReport:
    run_id: str
    strategy_id: str
    status: str
    trigger_source: str
    started_at: str
    completed_at: str | None
    result_summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "strategy_id": self.strategy_id,
            "status": self.status,
            "trigger_source": self.trigger_source,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "result_summary": self.result_summary,
        }


def build_placeholder_services() -> PlatformServices:
    return PlatformServices(
        data=PlaceholderMarketDataService(),
        risk=PlaceholderRiskService(),
        execution=PlaceholderExecutionService(),
        analytics=PlaceholderAnalyticsService(),
    )


def _strategy_payload(metadata: StrategyMetadata) -> dict[str, Any]:
    return {
        "strategy_id": metadata.strategy_id,
        "display_name": metadata.display_name,
        "version": metadata.version,
        "status": StrategyStatus.ACTIVE,
        "description": metadata.description,
        "config_reference": metadata.config_reference,
        "universe_symbols": list(metadata.universe),
        "settings_snapshot": {
            "indicators": metadata.indicators,
            "risk": metadata.risk,
            "exits": metadata.exits,
        },
    }


def ensure_strategy_record(session, metadata: StrategyMetadata) -> Strategy:
    existing = session.execute(
        select(Strategy).where(Strategy.strategy_id == metadata.strategy_id)
    ).scalar_one_or_none()
    payload = _strategy_payload(metadata)

    if existing is None:
        strategy = Strategy(**payload)
        session.add(strategy)
    else:
        strategy = existing
        for field_name, value in payload.items():
            setattr(strategy, field_name, value)

    session.flush()
    session.refresh(strategy)
    return strategy


def create_strategy_run(
    settings: Settings,
    metadata: StrategyMetadata,
    *,
    trigger_source: str,
    services: PlatformServices,
) -> uuid.UUID:
    with session_scope(settings) as session:
        strategy_record = ensure_strategy_record(session, metadata)
        strategy_run = StrategyRun(
            strategy_id=strategy_record.id,
            run_type=StrategyRunType.DRY_BOOTSTRAP,
            status=StrategyRunStatus.PENDING,
            trigger_source=trigger_source,
            result_summary={
                "stage": "pending",
                "strategy_id": metadata.strategy_id,
                "services": services.describe(),
            },
        )
        session.add(strategy_run)
        session.flush()
        return strategy_run.id


def update_strategy_run(
    settings: Settings,
    run_id: uuid.UUID,
    *,
    status: StrategyRunStatus,
    result_summary: dict[str, Any] | None = None,
    error_message: str | None = None,
    completed_at: datetime | None = None,
) -> DryRunReport:
    with session_scope(settings) as session:
        strategy_run = session.get(StrategyRun, run_id)
        if strategy_run is None:
            raise LookupError(f"Missing strategy_run '{run_id}'.")

        strategy_run.status = status
        if result_summary is not None:
            strategy_run.result_summary = result_summary
        if error_message is not None:
            strategy_run.error_message = error_message
        if completed_at is not None:
            strategy_run.completed_at = completed_at

        session.flush()
        session.refresh(strategy_run)
        strategy = session.get(Strategy, strategy_run.strategy_id)

        return DryRunReport(
            run_id=str(strategy_run.id),
            strategy_id=strategy.strategy_id if strategy is not None else "unknown",
            status=strategy_run.status.value,
            trigger_source=strategy_run.trigger_source,
            started_at=strategy_run.started_at.isoformat(),
            completed_at=strategy_run.completed_at.isoformat() if strategy_run.completed_at else None,
            result_summary=strategy_run.result_summary,
        )


def run_dry_bootstrap(
    strategy_id: str,
    *,
    trigger_source: str = "dry_run_script",
    settings: Settings | None = None,
    registry: StrategyRegistry | None = None,
) -> DryRunReport:
    resolved_settings = settings or load_settings()
    resolved_registry = registry or build_default_registry(resolved_settings)
    strategy = resolved_registry.resolve(strategy_id)
    metadata = strategy.metadata
    services = build_placeholder_services()
    logger = logging.getLogger("trading_platform.dry_run")

    run_id = create_strategy_run(
        resolved_settings,
        metadata,
        trigger_source=trigger_source,
        services=services,
    )

    started_context = {
        "run_id": str(run_id),
        "strategy_id": metadata.strategy_id,
        "trigger_source": trigger_source,
        "services": services.describe(),
    }
    logger.info("dry_run_started", extra={"context": started_context})

    update_strategy_run(
        resolved_settings,
        run_id,
        status=StrategyRunStatus.RUNNING,
        result_summary={
            "stage": "running",
            "strategy_id": metadata.strategy_id,
            "services": services.describe(),
        },
    )

    try:
        result = strategy.dry_run(services)
        completed_at = datetime.now(UTC)
        report = update_strategy_run(
            resolved_settings,
            run_id,
            status=StrategyRunStatus.SUCCEEDED,
            completed_at=completed_at,
            result_summary={
                "stage": "completed",
                "message": result.message,
                "strategy": metadata.to_public_dict(),
                "services": services.describe(),
                "details": result.details,
            },
        )
        logger.info(
            "dry_run_succeeded",
            extra={
                "context": {
                    "run_id": report.run_id,
                    "strategy_id": report.strategy_id,
                    "status": report.status,
                }
            },
        )
        return report
    except Exception as exc:
        completed_at = datetime.now(UTC)
        report = update_strategy_run(
            resolved_settings,
            run_id,
            status=StrategyRunStatus.FAILED,
            completed_at=completed_at,
            error_message=str(exc),
            result_summary={
                "stage": "failed",
                "strategy_id": metadata.strategy_id,
                "services": services.describe(),
            },
        )
        logger.exception(
            "dry_run_failed",
            extra={
                "context": {
                    "run_id": report.run_id,
                    "strategy_id": metadata.strategy_id,
                    "error": str(exc),
                }
            },
        )
        raise
