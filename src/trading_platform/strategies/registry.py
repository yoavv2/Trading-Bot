"""Strategy registry used by the CLI and API surfaces."""

from __future__ import annotations

from dataclasses import dataclass

from trading_platform.core.settings import Settings, load_settings
from trading_platform.strategies.base import BaseStrategy, StrategyMetadata
from trading_platform.strategies.trend_following_daily import TrendFollowingDailyStrategy


@dataclass(frozen=True)
class UnknownStrategyError(KeyError):
    strategy_id: str

    def __str__(self) -> str:
        return f"Unknown strategy '{self.strategy_id}'."


class StrategyRegistry:
    """In-memory registry with explicit registration and resolution."""

    def __init__(self) -> None:
        self._strategies: dict[str, BaseStrategy] = {}

    def register(self, strategy: BaseStrategy) -> None:
        strategy_id = strategy.strategy_id
        if strategy_id in self._strategies:
            raise ValueError(f"Strategy '{strategy_id}' is already registered.")
        self._strategies[strategy_id] = strategy

    def resolve(self, strategy_id: str) -> BaseStrategy:
        try:
            return self._strategies[strategy_id]
        except KeyError as exc:
            raise UnknownStrategyError(strategy_id) from exc

    def list_metadata(self) -> list[StrategyMetadata]:
        return [self._strategies[strategy_id].metadata for strategy_id in sorted(self._strategies)]

    def list_public(self) -> list[dict[str, object]]:
        return [metadata.to_public_dict() for metadata in self.list_metadata()]


def build_default_registry(settings: Settings | None = None) -> StrategyRegistry:
    resolved_settings = settings or load_settings()
    registry = StrategyRegistry()
    registry.register(TrendFollowingDailyStrategy(resolved_settings))
    return registry
