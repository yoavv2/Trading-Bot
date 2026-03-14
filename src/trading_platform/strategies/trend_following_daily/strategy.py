"""TrendFollowingDailyV1 strategy — indicator computation and signal generation."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from trading_platform.core.settings import PROJECT_ROOT, get_strategy_config
from trading_platform.services.market_data_access import bars_for_sessions
from trading_platform.strategies.base import BaseStrategy, StrategyBootstrapResult, StrategyMetadata
from trading_platform.strategies.signals import (
    IndicatorSnapshot,
    Signal,
    SignalBatch,
    SignalDirection,
    SignalReason,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session as DbSession


class TrendFollowingDailyStrategy(BaseStrategy):
    """Trend-following strategy using dual SMA crossover for long-only signals.

    Entry rule:  close > SMA(long_window) AND SMA(short_window) > SMA(long_window)
    Exit rule:   close < SMA(exit_window)  (typically SMA(short_window))
    No signal:   fewer bars than warmup_periods are available
    """

    @property
    def strategy_id(self) -> str:
        return "trend_following_daily"

    @property
    def version(self) -> str:
        return "v1"

    @property
    def description(self) -> str:
        return (
            "Long-only trend-following strategy using dual SMA crossover. "
            "Entry when close > SMA200 and SMA50 > SMA200; exit when close < SMA50. "
            "Signals are deterministic and derived exclusively from persisted daily bars."
        )

    @property
    def warmup_periods(self) -> int:
        config = get_strategy_config(self.settings, self.strategy_id)
        return config.indicators.warmup_periods

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

    # ------------------------------------------------------------------
    # Signal generation
    # ------------------------------------------------------------------

    def generate_signals(
        self,
        db_session: "DbSession",
        as_of: date,
    ) -> SignalBatch:
        """Evaluate the strategy for every configured symbol as of *as_of*.

        For each symbol this method:
        1. Loads the last ``warmup_periods`` bars from the persisted market-data layer.
        2. Computes SMA(short_window) and SMA(long_window) on the full window.
        3. Applies the entry/exit rules and emits a typed ``Signal``.

        No risk sizing, order intent, or broker logic is performed here.
        """
        config = get_strategy_config(self.settings, self.strategy_id)
        short_window = config.indicators.short_window
        long_window = config.indicators.long_window
        warmup = config.indicators.warmup_periods
        exit_window = config.exits.exit_window

        signals: list[Signal] = []

        for ticker in config.universe:
            bars = bars_for_sessions(
                db_session,
                symbol=ticker,
                n_sessions=warmup,
                as_of=as_of,
            )

            snapshot, signal = self._evaluate_symbol(
                ticker=ticker,
                bars=bars,
                as_of=as_of,
                short_window=short_window,
                long_window=long_window,
                warmup=warmup,
                exit_window=exit_window,
            )
            signals.append(
                Signal(
                    strategy_id=self.strategy_id,
                    symbol=ticker,
                    session_date=as_of,
                    direction=signal[0],
                    reason=signal[1],
                    indicators=snapshot,
                )
            )

        return SignalBatch(
            strategy_id=self.strategy_id,
            as_of_session=as_of,
            signals=tuple(signals),
        )

    # ------------------------------------------------------------------
    # Internal helpers — kept separate so tests can exercise them directly
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_sma(closes: list[Decimal], window: int) -> Decimal | None:
        """Return the simple moving average of the last *window* values.

        Returns None when the list has fewer values than *window*.
        """
        if len(closes) < window:
            return None
        window_values = closes[-window:]
        return sum(window_values, Decimal(0)) / Decimal(window)

    def _evaluate_symbol(
        self,
        *,
        ticker: str,
        bars: list,
        as_of: date,
        short_window: int,
        long_window: int,
        warmup: int,
        exit_window: int,
    ) -> tuple[IndicatorSnapshot, tuple[SignalDirection, SignalReason]]:
        """Compute indicators and apply entry/exit rules for a single symbol.

        Returns a (IndicatorSnapshot, (direction, reason)) pair.
        """
        closes = [bar.close for bar in bars]
        bars_available = len(closes)

        sma_short = self._compute_sma(closes, short_window)
        sma_long = self._compute_sma(closes, long_window)
        close = closes[-1] if closes else Decimal(0)

        snapshot = IndicatorSnapshot(
            symbol=ticker,
            session_date=as_of,
            close=close,
            sma_short=sma_short,
            sma_long=sma_long,
            bars_available=bars_available,
        )

        # Gate: insufficient history
        if bars_available < warmup or sma_long is None or sma_short is None:
            return snapshot, (SignalDirection.FLAT, SignalReason.INSUFFICIENT_HISTORY)

        # Exit rule: close < exit moving-average (exit_window = short_window by default)
        sma_exit = self._compute_sma(closes, exit_window)
        if sma_exit is not None and close < sma_exit:
            return snapshot, (SignalDirection.EXIT, SignalReason.CLOSE_BELOW_EXIT_MA)

        # Entry rule: close > SMA_long AND SMA_short > SMA_long
        if close > sma_long and sma_short > sma_long:
            return snapshot, (SignalDirection.LONG, SignalReason.TREND_ENTRY)

        # No trend confirmation
        return snapshot, (SignalDirection.FLAT, SignalReason.TREND_NOT_CONFIRMED)
