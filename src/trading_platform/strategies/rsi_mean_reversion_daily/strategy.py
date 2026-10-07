"""Daily long-only RSI mean-reversion strategy."""

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


class RsiMeanReversionDailyStrategy(BaseStrategy):
    """Enter oversold symbols and exit them when RSI becomes overbought."""

    @property
    def strategy_id(self) -> str:
        return "rsi_mean_reversion_daily"

    @property
    def version(self) -> str:
        return "v1"

    @property
    def description(self) -> str:
        return (
            "Long-only daily mean reversion using RSI(14): enter below the oversold "
            "threshold and exit above the overbought threshold."
        )

    @property
    def warmup_periods(self) -> int:
        return get_strategy_config(
            self.settings, "rsi_mean_reversion_daily"
        ).indicators.warmup_periods

    def build_metadata(self) -> StrategyMetadata:
        config = get_strategy_config(self.settings, "rsi_mean_reversion_daily")
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
        config = get_strategy_config(self.settings, "rsi_mean_reversion_daily")
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
                rsi_window=params.rsi_window,
                oversold=Decimal(str(params.oversold)),
                overbought=Decimal(str(params.overbought)),
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
    def _compute_rsi(closes: list[Decimal], window: int) -> Decimal | None:
        """Return Wilder RSI with deterministic handling for zero-move windows."""
        if len(closes) < window + 1:
            return None
        changes = [current - previous for previous, current in zip(closes, closes[1:])]
        initial = changes[:window]
        average_gain = sum((max(change, Decimal(0)) for change in initial), Decimal(0)) / Decimal(
            window
        )
        average_loss = sum((max(-change, Decimal(0)) for change in initial), Decimal(0)) / Decimal(
            window
        )

        for change in changes[window:]:
            gain = max(change, Decimal(0))
            loss = max(-change, Decimal(0))
            average_gain = ((average_gain * Decimal(window - 1)) + gain) / Decimal(window)
            average_loss = ((average_loss * Decimal(window - 1)) + loss) / Decimal(window)

        if average_gain == 0 and average_loss == 0:
            return Decimal(50)
        if average_loss == 0:
            return Decimal(100)
        if average_gain == 0:
            return Decimal(0)
        relative_strength = average_gain / average_loss
        return Decimal(100) - (Decimal(100) / (Decimal(1) + relative_strength))

    def _evaluate_symbol(
        self,
        *,
        ticker: str,
        bars: list,
        as_of: date,
        rsi_window: int,
        oversold: Decimal,
        overbought: Decimal,
        warmup: int,
    ) -> tuple[IndicatorSnapshot, tuple[SignalDirection, SignalReason]]:
        closes = [bar.close for bar in bars]
        rsi = self._compute_rsi(closes, rsi_window)
        snapshot = IndicatorSnapshot(
            symbol=ticker,
            session_date=as_of,
            close=closes[-1] if closes else Decimal(0),
            sma_short=None,
            sma_long=None,
            bars_available=len(closes),
            values={"rsi": rsi},
        )
        if len(closes) < warmup or rsi is None:
            return snapshot, (SignalDirection.FLAT, SignalReason.INSUFFICIENT_HISTORY)
        if rsi < oversold:
            return snapshot, (SignalDirection.LONG, SignalReason.RSI_OVERSOLD_ENTRY)
        if rsi > overbought:
            return snapshot, (SignalDirection.EXIT, SignalReason.RSI_OVERBOUGHT_EXIT)
        return snapshot, (SignalDirection.FLAT, SignalReason.RSI_NEUTRAL)
