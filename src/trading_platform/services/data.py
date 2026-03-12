"""Placeholder market-data service contracts."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class MarketDataService(ABC):
    @abstractmethod
    def describe(self) -> dict[str, Any]:
        """Describe the service capability exposed to the platform."""

    @abstractmethod
    def get_daily_bars(self, symbols: tuple[str, ...]) -> object:
        """Fetch market data once the real provider exists."""


class PlaceholderMarketDataService(MarketDataService):
    def describe(self) -> dict[str, Any]:
        return {
            "service": "market_data",
            "status": "deferred",
            "detail": "Deferred to Phase 2 data ingestion.",
        }

    def get_daily_bars(self, symbols: tuple[str, ...]) -> object:
        raise NotImplementedError("Market-data integration is deferred to Phase 2.")
