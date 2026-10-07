"""Daily long-only Donchian breakout strategy."""

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


class DonchianBreakoutDailyStrategy(BaseStrategy):
    """Turtle System 2-inspired daily breakout using prior-session channels."""

    @property
    def strategy_id(self) -> str:
        return "donchian_breakout_daily"

    @property
    def version(self) -> str:
        return "v1"

    @property
    def description(self) -> str:
        return (
            "Long-only Donchian breakout: enter above the preceding 55-session high "
            "and exit below the preceding 20-session low."
        )

    @property
    def warmup_periods(self) -> int:
        return get_strategy_config(
            self.settings, "donchian_breakout_daily"
        ).indicators.warmup_periods

    def build_metadata(self) -> StrategyMetadata:
        config = get_strategy_config(self.settings, "donchian_breakout_daily")
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
        service_descriptions = (
            getattr(services, "describe")() if hasattr(services, "describe") else []
        )
        return StrategyBootstrapResult(
            status="succeeded",
            message="Dry bootstrap completed without market-data, risk, or broker integrations.",
            details={
                "strategy_id": self.strategy_id,
                "display_name": self.metadata.display_name,
                "version": self.version,
                "enabled": self.metadata.enabled,
                "universe_size": len(self.metadata.universe),
                "services": service_descriptions,
            },
        )

    def generate_signals(self, db_session: "DbSession", as_of: date) -> SignalBatch:
        config = get_strategy_config(self.settings, "donchian_breakout_daily")
        params = config.indicators
        signals: list[Signal] = []
        provider, adjusted = self.bar_source()
        for ticker in config.universe:
            bars = bars_for_sessions(
                db_session,
                symbol=ticker,
                n_sessions=params.warmup_periods,
                as_of=as_of,
                adjusted=adjusted,
                provider=provider,
            )
            snapshot, (direction, reason) = self._evaluate_symbol(
                ticker=ticker,
                bars=bars,
                as_of=as_of,
                entry_window=params.entry_window,
                exit_window=params.exit_window,
                warmup=params.warmup_periods,
            )
            signals.append(
                Signal(
                    strategy_id=self.strategy_id,
                    symbol=ticker,
                    session_date=as_of,
                    direction=direction,
                    reason=reason,
                    indicators=snapshot,
                )
            )
        return SignalBatch(self.strategy_id, as_of, tuple(signals))

    @staticmethod
    def _compute_channels(
        bars: list, entry_window: int, exit_window: int
    ) -> tuple[Decimal | None, Decimal | None]:
        """Compute channels from bars strictly preceding the current bar."""
        preceding = bars[:-1]
        entry_high = (
            max(bar.high for bar in preceding[-entry_window:])
            if len(preceding) >= entry_window
            else None
        )
        exit_low = (
            min(bar.low for bar in preceding[-exit_window:])
            if len(preceding) >= exit_window
            else None
        )
        return entry_high, exit_low

    def _evaluate_symbol(
        self,
        *,
        ticker: str,
        bars: list,
        as_of: date,
        entry_window: int,
        exit_window: int,
        warmup: int,
    ) -> tuple[IndicatorSnapshot, tuple[SignalDirection, SignalReason]]:
        entry_high, exit_low = self._compute_channels(bars, entry_window, exit_window)
        close = bars[-1].close if bars else Decimal(0)
        snapshot = IndicatorSnapshot(
            symbol=ticker,
            session_date=as_of,
            close=close,
            sma_short=None,
            sma_long=None,
            bars_available=len(bars),
            values={"entry_channel_high": entry_high, "exit_channel_low": exit_low},
        )
        if len(bars) < warmup or entry_high is None or exit_low is None:
            return snapshot, (SignalDirection.FLAT, SignalReason.INSUFFICIENT_HISTORY)
        if close > entry_high:
            return snapshot, (SignalDirection.LONG, SignalReason.DONCHIAN_ENTRY_BREAKOUT)
        if close < exit_low:
            return snapshot, (SignalDirection.EXIT, SignalReason.DONCHIAN_EXIT_BREAKDOWN)
        return snapshot, (SignalDirection.FLAT, SignalReason.DONCHIAN_WITHIN_CHANNEL)
