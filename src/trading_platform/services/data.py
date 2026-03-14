"""Market-data service contracts and typed request/response models."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any


# ---------------------------------------------------------------------------
# Request / response value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DailyBarRequest:
    """Parameters for a daily-bar fetch operation."""

    symbol: str
    from_date: date
    to_date: date
    adjusted: bool = True
    provider: str = "polygon"


@dataclass(frozen=True)
class DailyBar:
    """Normalized daily price bar for a single symbol and session."""

    symbol: str
    session_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    adjusted: bool
    provider: str
    provider_timestamp: datetime | None = None
    vwap: Decimal | None = None
    trade_count: int | None = None


@dataclass
class IngestionResult:
    """Summary of a completed ingestion run."""

    provider: str
    from_date: date
    to_date: date
    symbols_requested: list[str] = field(default_factory=list)
    bars_upserted: int = 0
    symbols_failed: list[str] = field(default_factory=list)
    request_metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def symbol_count(self) -> int:
        return len(self.symbols_requested)

    @property
    def failed_count(self) -> int:
        return len(self.symbols_failed)

    @property
    def succeeded(self) -> bool:
        return self.failed_count == 0


# ---------------------------------------------------------------------------
# Service boundary
# ---------------------------------------------------------------------------


class MarketDataService(ABC):
    """Abstract market-data boundary for provider-backed ingestion and reads."""

    @abstractmethod
    def describe(self) -> dict[str, Any]:
        """Describe the service capability exposed to the platform."""

    @abstractmethod
    def fetch_daily_bars(self, request: DailyBarRequest) -> list[DailyBar]:
        """Fetch normalized daily bars for a single symbol and date window."""

    def get_daily_bars(self, symbols: tuple[str, ...]) -> object:
        """Legacy placeholder shape — replaced by fetch_daily_bars in Phase 2."""
        raise NotImplementedError(
            "Use fetch_daily_bars(DailyBarRequest) for provider-backed ingestion."
        )


class PlaceholderMarketDataService(MarketDataService):
    def describe(self) -> dict[str, Any]:
        return {
            "service": "market_data",
            "status": "deferred",
            "detail": "Deferred to Phase 2 data ingestion.",
        }

    def fetch_daily_bars(self, request: DailyBarRequest) -> list[DailyBar]:
        raise NotImplementedError("Market-data integration is deferred to Phase 2.")
