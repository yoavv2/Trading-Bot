"""Daily long-only time-series momentum strategy."""

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


class TimeSeriesMomentumDailyStrategy(BaseStrategy):
    """Enter when the current close exceeds its trailing comparison close."""

    @property
    def strategy_id(self) -> str:
        return "time_series_momentum_daily"

    @property
    def version(self) -> str:
        return "v1"

    @property
    def description(self) -> str:
        return (
            "Long-only time-series momentum: enter when the close is above its "
            "252-session-ago close and exit when it is at or below that close."
        )

    @property
    def warmup_periods(self) -> int:
        return get_strategy_config(
            self.settings, "time_series_momentum_daily"
        ).indicators.warmup_periods

    def build_metadata(self) -> StrategyMetadata:
        config = get_strategy_config(self.settings, "time_series_momentum_daily")
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
        config = get_strategy_config(self.settings, "time_series_momentum_daily")
        params = config.indicators
        signals: list[Signal] = []
        for ticker in config.universe:
            bars = bars_for_sessions(
                db_session,
                symbol=ticker,
                n_sessions=params.warmup_periods,
                as_of=as_of,
            )
            snapshot, (direction, reason) = self._evaluate_symbol(
                ticker=ticker,
                bars=bars,
                as_of=as_of,
                lookback_periods=params.lookback_periods,
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

    def _evaluate_symbol(
        self,
        *,
        ticker: str,
        bars: list,
        as_of: date,
        lookback_periods: int,
        warmup: int,
    ) -> tuple[IndicatorSnapshot, tuple[SignalDirection, SignalReason]]:
        closes = [bar.close for bar in bars]
        comparison_close = (
            closes[-(lookback_periods + 1)] if len(closes) >= lookback_periods + 1 else None
        )
        close = closes[-1] if closes else Decimal(0)
        snapshot = IndicatorSnapshot(
            symbol=ticker,
            session_date=as_of,
            close=close,
            sma_short=None,
            sma_long=None,
            bars_available=len(closes),
            values={"lookback_close": comparison_close},
        )
        if len(closes) < warmup or comparison_close is None:
            return snapshot, (SignalDirection.FLAT, SignalReason.INSUFFICIENT_HISTORY)
        if close > comparison_close:
            return snapshot, (SignalDirection.LONG, SignalReason.TIME_SERIES_MOMENTUM_ENTRY)
        return snapshot, (SignalDirection.EXIT, SignalReason.TIME_SERIES_MOMENTUM_EXIT)
