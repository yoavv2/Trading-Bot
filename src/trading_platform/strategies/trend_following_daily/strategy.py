"""Phase 1 shell for the first registered strategy module."""

from __future__ import annotations

from pathlib import Path

from trading_platform.core.settings import PROJECT_ROOT, get_strategy_config
from trading_platform.strategies.base import BaseStrategy, StrategyBootstrapResult, StrategyMetadata


class TrendFollowingDailyStrategy(BaseStrategy):
    """Thin strategy shell that proves discovery and bootstrap boundaries."""

    @property
    def strategy_id(self) -> str:
        return "trend_following_daily"

    @property
    def version(self) -> str:
        return "v1"

    @property
    def description(self) -> str:
        return (
            "Phase 1 bootstrap shell for the TrendFollowingDailyV1 strategy. "
            "It exposes metadata and a dry-run proof without market-data or broker integrations."
        )

    def build_metadata(self) -> StrategyMetadata:
        config = get_strategy_config(self.settings, self.strategy_id)
        strategy_path = self.settings.paths.strategy_config_dir / f"{self.strategy_id}.yaml"

        try:
            config_reference = str(strategy_path.relative_to(PROJECT_ROOT))
        except ValueError:
            config_reference = str(Path(strategy_path))

        return StrategyMetadata(
            strategy_id=config.strategy_id,
            display_name=config.display_name,
            version=self.version,
            enabled=config.enabled,
            description=self.description,
            config_reference=config_reference,
            universe=tuple(config.universe),
            indicators=config.indicators.model_dump(mode="json"),
            risk=config.risk.model_dump(mode="json"),
            exits=config.exits.model_dump(mode="json"),
        )

    def dry_run(self, services: object) -> StrategyBootstrapResult:
        metadata = self.metadata
        service_descriptions = []
        if hasattr(services, "describe"):
            service_descriptions = getattr(services, "describe")()

        return StrategyBootstrapResult(
            status="succeeded",
            message="Dry bootstrap completed without market-data, risk, or broker integrations.",
            details={
                "strategy_id": metadata.strategy_id,
                "display_name": metadata.display_name,
                "version": metadata.version,
                "enabled": metadata.enabled,
                "universe_size": len(metadata.universe),
                "services": service_descriptions,
            },
        )
