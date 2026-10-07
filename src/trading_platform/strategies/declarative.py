"""``DeclarativeDailyStrategy``: one interpreter for every strategy specification.

Reads exactly ``history_required`` sessions per asset through the shared access layer,
with the bar source passed explicitly (``BaseStrategy.bar_source``), and emits the
existing ``SignalBatch`` with the generic rule reasons. Evaluation is stateless, like
the four original strategies; whether a signal affects the current position is the
engine's business.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from trading_platform.core.settings import Settings
from trading_platform.services.market_data_access import bars_for_sessions
from trading_platform.strategies.base import BaseStrategy, StrategyBootstrapResult, StrategyMetadata
from trading_platform.strategies.signals import IndicatorSnapshot, Signal, SignalBatch
from trading_platform.strategies.spec.evaluate import Evaluation, evaluate
from trading_platform.strategies.spec.validate import CompiledSpec

if TYPE_CHECKING:
    from sqlalchemy.orm import Session as DbSession


class DeclarativeDailyStrategy(BaseStrategy):
    def __init__(
        self,
        settings: Settings,
        compiled: CompiledSpec,
        *,
        strategy_id: str,
        universe: tuple[str, ...],
        version_label: str = "v1",
        config_reference: str = "research:strategy_version",
        bar_source: tuple[str, bool] | None = None,
    ) -> None:
        super().__init__(settings)
        self._compiled = compiled
        # Research runs pin the provider/adjusted pair per study; without an explicit pair
        # the settings-derived source applies (trading default: polygon, adjusted).
        self._explicit_bar_source = bar_source
        self._strategy_id = strategy_id
        self._universe = tuple(universe)
        self._version_label = version_label
        self._config_reference = config_reference

    @property
    def compiled(self) -> CompiledSpec:
        return self._compiled

    def bar_source(self) -> tuple[str, bool]:
        if self._explicit_bar_source is not None:
            return self._explicit_bar_source
        return super().bar_source()

    @property
    def strategy_id(self) -> str:
        return self._strategy_id

    @property
    def version(self) -> str:
        return self._version_label

    @property
    def description(self) -> str:
        return self._compiled.spec.description or self._compiled.spec.name

    @property
    def warmup_periods(self) -> int:
        return self._compiled.history_required

    def build_metadata(self) -> StrategyMetadata:
        spec = self._compiled.spec
        return StrategyMetadata(
            strategy_id=self._strategy_id,
            display_name=spec.name,
            version=self._version_label,
            enabled=True,
            description=self.description,
            config_reference=self._config_reference,
            universe=self._universe,
            indicators={
                name: indicator.model_dump(mode="json") for name, indicator in spec.indicators.items()
            }
            | {
                "history_required": self._compiled.history_required,
                "history_minimum": self._compiled.history_minimum,
                "spec_sha256": self._compiled.spec_sha256,
            },
            risk={"declared_in_strategy": False},
            exits={"rule": "exit condition tree; checked before entry on every close"},
        )

    def dry_run(self, services: object) -> StrategyBootstrapResult:
        return StrategyBootstrapResult(
            status="succeeded",
            message="Declarative strategy loaded; no market-data, risk or broker integration touched.",
            details={
                "strategy_id": self._strategy_id,
                "spec_sha256": self._compiled.spec_sha256,
                "history_required": self._compiled.history_required,
                "universe_size": len(self._universe),
            },
        )

    def evaluate_bars(self, bars: list) -> Evaluation:
        return evaluate(self._compiled, bars)

    def generate_signals(self, db_session: DbSession, as_of: date) -> SignalBatch:
        provider, adjusted = self.bar_source()
        signals: list[Signal] = []
        for ticker in self._universe:
            bars = bars_for_sessions(
                db_session,
                symbol=ticker,
                n_sessions=self._compiled.history_required,
                as_of=as_of,
                adjusted=adjusted,
                provider=provider,
            )
            evaluation = self.evaluate_bars(bars)
            close = bars[-1].close if bars else Decimal(0)
            snapshot = IndicatorSnapshot(
                symbol=ticker,
                session_date=as_of,
                close=close,
                sma_short=None,
                sma_long=None,
                bars_available=evaluation.bars_available,
                values=dict(evaluation.values),
            )
            signals.append(
                Signal(
                    strategy_id=self._strategy_id,
                    symbol=ticker,
                    session_date=as_of,
                    direction=evaluation.direction,
                    reason=evaluation.reason,
                    indicators=snapshot,
                    metadata={"spec_sha256": self._compiled.spec_sha256},
                )
            )
        return SignalBatch(strategy_id=self._strategy_id, as_of_session=as_of, signals=tuple(signals))
