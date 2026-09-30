"""Deterministic unit coverage for the additional daily long-only strategies."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import pytest

from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.strategies.donchian_breakout_daily import DonchianBreakoutDailyStrategy
from trading_platform.strategies.rsi_mean_reversion_daily import RsiMeanReversionDailyStrategy
from trading_platform.strategies.signals import SignalDirection, SignalReason
from trading_platform.strategies.time_series_momentum_daily import (
    TimeSeriesMomentumDailyStrategy,
)

AS_OF = date(2026, 9, 29)


@dataclass(frozen=True)
class FakeBar:
    close: Decimal
    high: Decimal
    low: Decimal


def _bar(
    close: int | str, *, high: int | str | None = None, low: int | str | None = None
) -> FakeBar:
    close_value = Decimal(str(close))
    return FakeBar(
        close=close_value,
        high=Decimal(str(high)) if high is not None else close_value,
        low=Decimal(str(low)) if low is not None else close_value,
    )


@pytest.fixture()
def settings():
    clear_settings_cache()
    return load_settings()


class TestRsiMeanReversionDaily:
    def test_rsi_edge_cases_and_mixed_changes(self) -> None:
        compute = RsiMeanReversionDailyStrategy._compute_rsi
        assert compute([Decimal(1)] * 14, 14) is None
        assert compute([Decimal(n) for n in range(1, 16)], 14) == Decimal(100)
        assert compute([Decimal(n) for n in range(15, 0, -1)], 14) == Decimal(0)
        assert compute([Decimal(10)] * 15, 14) == Decimal(50)
        assert compute([Decimal(10), Decimal(11), Decimal(10)], 2) == Decimal(50)

    @pytest.mark.parametrize(
        ("closes", "direction", "reason"),
        [
            (
                list(range(15, 0, -1)),
                SignalDirection.LONG,
                SignalReason.RSI_OVERSOLD_ENTRY,
            ),
            (
                list(range(1, 16)),
                SignalDirection.EXIT,
                SignalReason.RSI_OVERBOUGHT_EXIT,
            ),
            ([10] * 15, SignalDirection.FLAT, SignalReason.RSI_NEUTRAL),
        ],
    )
    def test_long_exit_and_flat(self, settings, closes, direction, reason) -> None:
        strategy = RsiMeanReversionDailyStrategy(settings)
        snapshot, actual = strategy._evaluate_symbol(
            ticker="SPY",
            bars=[_bar(close) for close in closes],
            as_of=AS_OF,
            rsi_window=14,
            oversold=Decimal(30),
            overbought=Decimal(70),
            warmup=15,
        )
        assert actual == (direction, reason)
        assert snapshot.values["rsi"] is not None

    def test_insufficient_history(self, settings) -> None:
        strategy = RsiMeanReversionDailyStrategy(settings)
        snapshot, actual = strategy._evaluate_symbol(
            ticker="SPY",
            bars=[_bar(close) for close in range(1, 15)],
            as_of=AS_OF,
            rsi_window=14,
            oversold=Decimal(30),
            overbought=Decimal(70),
            warmup=15,
        )
        assert actual == (SignalDirection.FLAT, SignalReason.INSUFFICIENT_HISTORY)
        assert snapshot.values["rsi"] is None


class TestDonchianBreakoutDaily:
    def _evaluate(self, strategy, bars):
        return strategy._evaluate_symbol(
            ticker="SPY",
            bars=bars,
            as_of=AS_OF,
            entry_window=3,
            exit_window=2,
            warmup=4,
        )

    def test_channels_exclude_current_bar(self) -> None:
        bars = [
            _bar(10, high=11, low=9),
            _bar(11, high=12, low=10),
            _bar(12, high=13, low=11),
            _bar(14, high=999, low=0),
        ]
        entry_high, exit_low = DonchianBreakoutDailyStrategy._compute_channels(bars, 3, 2)
        assert entry_high == Decimal(13)
        assert exit_low == Decimal(10)

    @pytest.mark.parametrize(
        ("current", "direction", "reason"),
        [
            (14, SignalDirection.LONG, SignalReason.DONCHIAN_ENTRY_BREAKOUT),
            (8, SignalDirection.EXIT, SignalReason.DONCHIAN_EXIT_BREAKDOWN),
            (11, SignalDirection.FLAT, SignalReason.DONCHIAN_WITHIN_CHANNEL),
        ],
    )
    def test_long_exit_and_flat(self, settings, current, direction, reason) -> None:
        strategy = DonchianBreakoutDailyStrategy(settings)
        prior = [_bar(10, high=11, low=9), _bar(11, high=12, low=10), _bar(12, high=13, low=11)]
        snapshot, actual = self._evaluate(strategy, [*prior, _bar(current)])
        assert actual == (direction, reason)
        assert snapshot.values == {
            "entry_channel_high": Decimal(13),
            "exit_channel_low": Decimal(10),
        }

    def test_insufficient_history(self, settings) -> None:
        strategy = DonchianBreakoutDailyStrategy(settings)
        _, actual = self._evaluate(strategy, [_bar(10), _bar(11), _bar(12)])
        assert actual == (SignalDirection.FLAT, SignalReason.INSUFFICIENT_HISTORY)


class TestTimeSeriesMomentumDaily:
    def _evaluate(self, strategy, closes):
        return strategy._evaluate_symbol(
            ticker="SPY",
            bars=[_bar(close) for close in closes],
            as_of=AS_OF,
            lookback_periods=3,
            warmup=4,
        )

    @pytest.mark.parametrize(
        ("closes", "direction", "reason"),
        [
            ([10, 11, 12, 13], SignalDirection.LONG, SignalReason.TIME_SERIES_MOMENTUM_ENTRY),
            ([10, 11, 12, 10], SignalDirection.EXIT, SignalReason.TIME_SERIES_MOMENTUM_EXIT),
            ([10, 11, 12, 9], SignalDirection.EXIT, SignalReason.TIME_SERIES_MOMENTUM_EXIT),
        ],
    )
    def test_long_and_exit_including_equal_close(self, settings, closes, direction, reason) -> None:
        strategy = TimeSeriesMomentumDailyStrategy(settings)
        snapshot, actual = self._evaluate(strategy, closes)
        assert actual == (direction, reason)
        assert snapshot.values["lookback_close"] == Decimal(10)

    def test_insufficient_history(self, settings) -> None:
        strategy = TimeSeriesMomentumDailyStrategy(settings)
        snapshot, actual = self._evaluate(strategy, [10, 11, 12])
        assert actual == (SignalDirection.FLAT, SignalReason.INSUFFICIENT_HISTORY)
        assert snapshot.values["lookback_close"] is None


def test_strategy_config_metadata(settings) -> None:
    strategies = [
        RsiMeanReversionDailyStrategy(settings),
        DonchianBreakoutDailyStrategy(settings),
        TimeSeriesMomentumDailyStrategy(settings),
    ]
    assert [strategy.metadata.display_name for strategy in strategies] == [
        "RsiMeanReversionDailyV1",
        "DonchianBreakoutDailyV1",
        "TimeSeriesMomentumDailyV1",
    ]
    assert all(len(strategy.metadata.universe) == 10 for strategy in strategies)
    assert all(strategy.metadata.risk["risk_per_trade"] == 0.01 for strategy in strategies)
    assert strategies[0].metadata.indicators["rsi_window"] == 14
    assert strategies[1].metadata.indicators["entry_window"] == 55
    assert strategies[2].metadata.indicators["lookback_periods"] == 252


def test_indicator_values_are_json_safe(settings) -> None:
    strategy = TimeSeriesMomentumDailyStrategy(settings)
    snapshot, _ = TestTimeSeriesMomentumDaily()._evaluate(strategy, [10, 11, 12, 13])
    assert snapshot.to_dict()["values"] == {"lookback_close": 10.0}
