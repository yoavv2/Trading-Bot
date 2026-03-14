"""Typed signal and indicator snapshot types for strategy output.

These types represent the stable, inspectable output of a strategy's
signal-generation step.  They are intentionally free of broker, order,
risk-sizing, and portfolio concepts so later phases can compose on top
without importing execution concerns.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Any


class SignalDirection(str, Enum):
    """Direction of a generated trading signal."""

    LONG = "long"
    EXIT = "exit"
    FLAT = "flat"  # no actionable signal for this session


class SignalReason(str, Enum):
    """Machine-readable reason explaining why a signal was or was not generated."""

    # Entry reasons
    TREND_ENTRY = "trend_entry"  # close > SMA_long and SMA_short > SMA_long

    # Exit reasons
    CLOSE_BELOW_EXIT_MA = "close_below_exit_ma"  # close < exit moving average

    # No-signal reasons
    INSUFFICIENT_HISTORY = "insufficient_history"  # fewer bars than warmup_periods
    TREND_NOT_CONFIRMED = "trend_not_confirmed"  # trend filters not satisfied


@dataclass(frozen=True)
class IndicatorSnapshot:
    """Point-in-time indicator values computed for a single symbol and session.

    All moving-average values are simple (arithmetic) means over their window.
    Fields are None when there is insufficient history for that window.
    """

    symbol: str
    session_date: date
    close: Decimal
    sma_short: Decimal | None  # SMA over indicators.short_window bars
    sma_long: Decimal | None  # SMA over indicators.long_window bars
    bars_available: int  # how many bars were available for computation

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "session_date": self.session_date.isoformat(),
            "close": float(self.close),
            "sma_short": float(self.sma_short) if self.sma_short is not None else None,
            "sma_long": float(self.sma_long) if self.sma_long is not None else None,
            "bars_available": self.bars_available,
        }


@dataclass(frozen=True)
class Signal:
    """A single strategy signal for one symbol evaluated at one session.

    A Signal is the atomic unit of strategy output.  It carries enough
    context (indicator snapshot + reason) to be replayed, audited, or
    compared across runs without needing the original bar data.
    """

    strategy_id: str
    symbol: str
    session_date: date
    direction: SignalDirection
    reason: SignalReason
    indicators: IndicatorSnapshot
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "symbol": self.symbol,
            "session_date": self.session_date.isoformat(),
            "direction": self.direction.value,
            "reason": self.reason.value,
            "indicators": self.indicators.to_dict(),
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class SignalBatch:
    """All signals emitted by a strategy for a given evaluation session.

    A batch groups the per-symbol signals so callers can process or
    serialize the full strategy output atomically.
    """

    strategy_id: str
    as_of_session: date
    signals: tuple[Signal, ...]

    @property
    def entry_signals(self) -> list[Signal]:
        return [s for s in self.signals if s.direction == SignalDirection.LONG]

    @property
    def exit_signals(self) -> list[Signal]:
        return [s for s in self.signals if s.direction == SignalDirection.EXIT]

    @property
    def flat_signals(self) -> list[Signal]:
        return [s for s in self.signals if s.direction == SignalDirection.FLAT]

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "as_of_session": self.as_of_session.isoformat(),
            "signal_count": len(self.signals),
            "entry_count": len(self.entry_signals),
            "exit_count": len(self.exit_signals),
            "flat_count": len(self.flat_signals),
            "signals": [s.to_dict() for s in self.signals],
        }
