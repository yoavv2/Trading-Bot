"""Base strategy contracts for the trading platform."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from trading_platform.core.settings import Settings


@dataclass(frozen=True)
class StrategyMetadata:
    """Public metadata that describes a registered strategy."""

    strategy_id: str
    display_name: str
    version: str
    enabled: bool
    description: str
    config_reference: str
    universe: tuple[str, ...]
    indicators: dict[str, Any]
    risk: dict[str, Any]
    exits: dict[str, Any]

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "display_name": self.display_name,
            "version": self.version,
            "enabled": self.enabled,
            "description": self.description,
            "config_reference": self.config_reference,
            "universe": list(self.universe),
            "universe_size": len(self.universe),
            "indicators": self.indicators,
            "risk": self.risk,
            "exits": self.exits,
        }


@dataclass(frozen=True)
class StrategyBootstrapResult:
    """Dry-run result returned by a strategy implementation."""

    status: str
    message: str
    details: dict[str, Any]


class BaseStrategy(ABC):
    """Abstract strategy contract used by the registry and dry-run flow."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    @abstractmethod
    def strategy_id(self) -> str:
        """Stable strategy identifier."""

    @property
    @abstractmethod
    def version(self) -> str:
        """Semantic strategy version."""

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable strategy description."""

    @property
    def metadata(self) -> StrategyMetadata:
        return self.build_metadata()

    @abstractmethod
    def build_metadata(self) -> StrategyMetadata:
        """Assemble registry metadata from the current settings."""

    @abstractmethod
    def dry_run(self, services: object) -> StrategyBootstrapResult:
        """Execute the non-trading bootstrap proof for Phase 1."""
