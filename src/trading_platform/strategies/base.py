"""Base strategy contracts for the trading platform."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any

from trading_platform.core.settings import Settings

if TYPE_CHECKING:
    from sqlalchemy.orm import Session as DbSession

    from trading_platform.strategies.signals import SignalBatch


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
    """Abstract strategy contract used by the registry and dry-run flow.

    Phase 2 extension: subclasses may implement ``generate_signals`` to
    emit typed ``SignalBatch`` output from persisted bar data.  The
    base implementation raises ``NotImplementedError`` so Phase 1
    strategies that do not yet need signal generation remain valid.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Registry metadata
    # ------------------------------------------------------------------

    @property
    def metadata(self) -> StrategyMetadata:
        return self.build_metadata()

    @abstractmethod
    def build_metadata(self) -> StrategyMetadata:
        """Assemble registry metadata from the current settings."""

    # ------------------------------------------------------------------
    # Phase 1 bootstrap (non-trading)
    # ------------------------------------------------------------------

    @abstractmethod
    def dry_run(self, services: object) -> StrategyBootstrapResult:
        """Execute the non-trading bootstrap proof for Phase 1."""

    # ------------------------------------------------------------------
    # Phase 2 signal generation
    # ------------------------------------------------------------------

    def generate_signals(
        self,
        db_session: "DbSession",
        as_of: date,
    ) -> "SignalBatch":
        """Evaluate the strategy as of *as_of* and return typed signals.

        Implementations read persisted daily bars through the session-aware
        market-data access layer and emit a ``SignalBatch`` covering every
        symbol in the configured universe.

        The base implementation raises ``NotImplementedError``; subclasses
        that extend signal generation must override this method.

        Args:
            db_session: Active SQLAlchemy session connected to the platform DB.
            as_of: The trading session date to evaluate.  Must be a date for
                which bars have been persisted (i.e. a past completed session).

        Returns:
            A ``SignalBatch`` containing one ``Signal`` per universe symbol.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not implement generate_signals. "
            "Override this method to emit strategy signals."
        )

    @property
    def warmup_periods(self) -> int:
        """Minimum number of daily bars required before signals are emitted.

        Defaults to 0 (no warmup required).  Strategies that compute
        moving averages should override this to return their longest window.
        """
        return 0
