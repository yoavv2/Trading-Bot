"""Buy-and-hold benchmark through the same engine (proposal Part I.1).

``LONG`` on the first session of the window (the "buy" decision on the first close,
filled at the next open like every candidate), ``FLAT`` on every later session, never
``EXIT``: the position stays open and is marked at the window's last close. Same
capital, costs and quantity policy as the candidate; its net return exists even though
it has no closed trade.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from trading_platform.core.settings import Settings
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

BENCHMARK_STRATEGY_PREFIX = "research:benchmark:buy_and_hold"


class BuyAndHoldSingleAssetStrategy(BaseStrategy):
    def __init__(
        self, settings: Settings, *, asset: str, window_start: date, bar_source: tuple[str, bool] | None = None
    ) -> None:
        super().__init__(settings)
        self._asset = asset
        self._window_start = window_start
        self._explicit_bar_source = bar_source

    def bar_source(self) -> tuple[str, bool]:
        if self._explicit_bar_source is not None:
            return self._explicit_bar_source
        return super().bar_source()

    @property
    def strategy_id(self) -> str:
        return f"{BENCHMARK_STRATEGY_PREFIX}:{self._asset}"

    @property
    def version(self) -> str:
        return "v1"

    @property
    def description(self) -> str:
        return f"Buy-and-hold benchmark of {self._asset}: buy at the first open, hold, mark at the last close."

    @property
    def warmup_periods(self) -> int:
        return 1

    def build_metadata(self) -> StrategyMetadata:
        return StrategyMetadata(
            strategy_id=self.strategy_id,
            display_name=f"Buy and hold {self._asset}",
            version="v1",
            enabled=True,
            description=self.description,
            config_reference="research:benchmark",
            universe=(self._asset,),
            indicators={"window_start": self._window_start.isoformat()},
            risk={"declared_in_strategy": False},
            exits={"rule": "none; the position is marked at the window's last close"},
        )

    def dry_run(self, services: object) -> StrategyBootstrapResult:
        return StrategyBootstrapResult(status="succeeded", message="Benchmark strategy loaded.", details={})

    def generate_signals(self, db_session: DbSession, as_of: date) -> SignalBatch:
        provider, adjusted = self.bar_source()
        bars = bars_for_sessions(db_session, symbol=self._asset, n_sessions=1, as_of=as_of, adjusted=adjusted, provider=provider)
        close = bars[-1].close if bars else Decimal(0)
        buy = as_of == self._window_start and bool(bars)
        snapshot = IndicatorSnapshot(symbol=self._asset, session_date=as_of, close=close, sma_short=None, sma_long=None, bars_available=len(bars), values={})
        signal = Signal(
            strategy_id=self.strategy_id,
            symbol=self._asset,
            session_date=as_of,
            direction=SignalDirection.LONG if buy else SignalDirection.FLAT,
            reason=SignalReason.RULE_ENTRY if buy else SignalReason.RULE_NO_SIGNAL,
            indicators=snapshot,
            metadata={"benchmark": "buy_and_hold"},
        )
        return SignalBatch(strategy_id=self.strategy_id, as_of_session=as_of, signals=(signal,))
