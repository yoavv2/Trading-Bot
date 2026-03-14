"""Deterministic tests for TrendFollowingDailyV1 indicator computation and signals.

All tests use synthetic fixture data — no database or network access required.
The strategy's _evaluate_symbol and _compute_sma helpers are tested directly
to verify boundary conditions, and generate_signals is tested through a
lightweight mock market-data layer.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.strategies.signals import SignalDirection, SignalReason
from trading_platform.strategies.trend_following_daily.strategy import TrendFollowingDailyStrategy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_strategy() -> TrendFollowingDailyStrategy:
    clear_settings_cache()
    return TrendFollowingDailyStrategy(load_settings())


@dataclass
class FakeBar:
    """Minimal bar fixture compatible with the SessionBar interface."""

    close: Decimal
    session_date: date = field(default_factory=lambda: date(2024, 1, 15))
    symbol: str = "AAPL"


def _bars(closes: list[float], base_date: date = date(2024, 1, 15)) -> list[FakeBar]:
    """Build a list of FakeBars from a list of close prices in ascending order."""
    return [
        FakeBar(
            close=Decimal(str(c)),
            session_date=date(base_date.year, base_date.month, base_date.day),
        )
        for c in closes
    ]


# ---------------------------------------------------------------------------
# _compute_sma tests
# ---------------------------------------------------------------------------


class TestComputeSma:
    def test_returns_none_when_fewer_values_than_window(self) -> None:
        result = TrendFollowingDailyStrategy._compute_sma(
            [Decimal("100"), Decimal("110")], window=5
        )
        assert result is None

    def test_returns_correct_sma_for_exact_window(self) -> None:
        closes = [Decimal(str(x)) for x in [100, 110, 120, 130, 140]]
        result = TrendFollowingDailyStrategy._compute_sma(closes, window=5)
        assert result == Decimal("120")

    def test_uses_last_n_values_when_more_available(self) -> None:
        # Last 3 values: 130, 140, 150 → SMA = 140
        closes = [Decimal(str(x)) for x in [100, 110, 120, 130, 140, 150]]
        result = TrendFollowingDailyStrategy._compute_sma(closes, window=3)
        assert result == Decimal("140")

    def test_single_value_window(self) -> None:
        closes = [Decimal("250")]
        result = TrendFollowingDailyStrategy._compute_sma(closes, window=1)
        assert result == Decimal("250")

    def test_empty_list_returns_none(self) -> None:
        result = TrendFollowingDailyStrategy._compute_sma([], window=1)
        assert result is None


# ---------------------------------------------------------------------------
# _evaluate_symbol tests
# ---------------------------------------------------------------------------


class TestEvaluateSymbol:
    """Tests for the per-symbol evaluation logic without database access."""

    def setup_method(self) -> None:
        self.strategy = _make_strategy()
        self.base_params: dict[str, Any] = {
            "ticker": "AAPL",
            "as_of": date(2024, 1, 15),
            "short_window": 50,
            "long_window": 200,
            "warmup": 200,
            "exit_window": 50,
        }

    def test_insufficient_history_when_bars_below_warmup(self) -> None:
        """Fewer bars than warmup_periods → FLAT / INSUFFICIENT_HISTORY."""
        bars = _bars([100.0] * 199)  # one short of warmup=200
        snapshot, (direction, reason) = self.strategy._evaluate_symbol(
            bars=bars, **self.base_params
        )

        assert direction == SignalDirection.FLAT
        assert reason == SignalReason.INSUFFICIENT_HISTORY
        assert snapshot.bars_available == 199

    def test_insufficient_history_when_no_bars(self) -> None:
        """Zero bars → FLAT / INSUFFICIENT_HISTORY."""
        snapshot, (direction, reason) = self.strategy._evaluate_symbol(
            bars=[], **self.base_params
        )

        assert direction == SignalDirection.FLAT
        assert reason == SignalReason.INSUFFICIENT_HISTORY
        assert snapshot.bars_available == 0
        assert snapshot.sma_short is None
        assert snapshot.sma_long is None

    def test_entry_signal_when_trend_filters_pass(self) -> None:
        """Entry: close > SMA200 AND SMA50 > SMA200.

        We construct 200 bars where early bars have low prices (pulling SMA200
        down) and recent bars have high prices (pulling SMA50 up and the
        close above SMA200).
        """
        # First 150 bars at 100.0 (low base)
        # Last 50 bars at 200.0 (recent surge)
        closes = [100.0] * 150 + [200.0] * 50

        # SMA200 = (150*100 + 50*200) / 200 = 25000/200 = 125.0
        # SMA50  = 200.0  (last 50 bars all at 200)
        # close  = 200.0
        # Entry condition: 200 > 125 AND 200 > 125 → TRUE

        bars = _bars(closes)
        snapshot, (direction, reason) = self.strategy._evaluate_symbol(
            bars=bars, **self.base_params
        )

        assert direction == SignalDirection.LONG
        assert reason == SignalReason.TREND_ENTRY
        assert snapshot.close == Decimal("200.0")
        assert snapshot.sma_long is not None
        assert snapshot.sma_short is not None
        assert snapshot.sma_short > snapshot.sma_long

    def test_exit_signal_when_close_below_exit_ma(self) -> None:
        """Exit: close < SMA(exit_window=50).

        Construct bars where earlier prices are high and the final bar is
        significantly lower than the preceding 50-bar average.
        """
        # 150 bars at 200.0 then 49 bars at 200.0 then 1 bar at 50.0
        # SMA50 over last 50 bars = (49*200 + 50) / 50 = (9800 + 50)/50 = 197.0
        # close = 50.0 → 50 < 197 → EXIT
        # SMA200: (150*200 + 49*200 + 50) / 200 = (39850)/200 = 199.25
        # SMA50 = 197.0 > 199.25 is FALSE, but exit rule fires first

        closes = [200.0] * 199 + [50.0]
        bars = _bars(closes)
        snapshot, (direction, reason) = self.strategy._evaluate_symbol(
            bars=bars, **self.base_params
        )

        assert direction == SignalDirection.EXIT
        assert reason == SignalReason.CLOSE_BELOW_EXIT_MA

    def test_flat_when_trend_not_confirmed(self) -> None:
        """FLAT when warmup satisfied but neither entry nor exit rules fire.

        Use uniform prices so all SMAs equal close → no crossover.
        """
        closes = [150.0] * 200
        # SMA200 = SMA50 = close = 150.0
        # Entry: 150 > 150 → FALSE  (close not > SMA_long)
        # Exit:  150 < 150 → FALSE
        bars = _bars(closes)
        snapshot, (direction, reason) = self.strategy._evaluate_symbol(
            bars=bars, **self.base_params
        )

        assert direction == SignalDirection.FLAT
        assert reason == SignalReason.TREND_NOT_CONFIRMED

    def test_indicators_snapshot_populated_correctly(self) -> None:
        """IndicatorSnapshot fields are correct after a full-warmup evaluation."""
        closes = [100.0] * 150 + [200.0] * 50
        bars = _bars(closes)
        snapshot, _ = self.strategy._evaluate_symbol(
            bars=bars, **self.base_params
        )

        assert snapshot.symbol == "AAPL"
        assert snapshot.session_date == date(2024, 1, 15)
        assert snapshot.close == Decimal("200.0")
        assert snapshot.bars_available == 200
        assert snapshot.sma_short is not None
        assert snapshot.sma_long is not None


# ---------------------------------------------------------------------------
# generate_signals integration tests (with mocked market-data layer)
# ---------------------------------------------------------------------------


class TestGenerateSignals:
    """Tests for the full generate_signals path using a mocked DB layer."""

    def setup_method(self) -> None:
        self.strategy = _make_strategy()
        self.as_of = date(2024, 1, 15)

    def _make_mock_db_session(self) -> MagicMock:
        return MagicMock(name="db_session")

    def _trending_bars(self, n: int = 200) -> list[FakeBar]:
        """Return bars that trigger an entry signal (strong uptrend)."""
        closes = [100.0] * (n - 50) + [200.0] * 50
        return _bars(closes)

    def _insufficient_bars(self, n: int = 50) -> list[FakeBar]:
        return _bars([100.0] * n)

    def test_returns_signal_batch_with_one_signal_per_universe_symbol(self) -> None:
        """generate_signals emits exactly one signal per universe symbol."""
        config = self.strategy.settings.strategies.trend_following_daily
        universe_size = len(config.universe)

        db_session = self._make_mock_db_session()

        with patch(
            "trading_platform.strategies.trend_following_daily.strategy.bars_for_sessions",
            return_value=self._insufficient_bars(),
        ):
            batch = self.strategy.generate_signals(db_session, self.as_of)

        assert batch.strategy_id == "trend_following_daily"
        assert batch.as_of_session == self.as_of
        assert len(batch.signals) == universe_size

    def test_all_signals_are_flat_when_insufficient_history(self) -> None:
        """When all symbols have fewer bars than warmup, all signals are FLAT."""
        db_session = self._make_mock_db_session()

        with patch(
            "trading_platform.strategies.trend_following_daily.strategy.bars_for_sessions",
            return_value=self._insufficient_bars(50),
        ):
            batch = self.strategy.generate_signals(db_session, self.as_of)

        assert len(batch.entry_signals) == 0
        assert len(batch.exit_signals) == 0
        assert all(
            s.reason == SignalReason.INSUFFICIENT_HISTORY for s in batch.flat_signals
        )

    def test_entry_signals_when_trend_filters_pass(self) -> None:
        """When trending bars are provided, all symbols emit LONG signals."""
        db_session = self._make_mock_db_session()

        with patch(
            "trading_platform.strategies.trend_following_daily.strategy.bars_for_sessions",
            return_value=self._trending_bars(200),
        ):
            batch = self.strategy.generate_signals(db_session, self.as_of)

        config = self.strategy.settings.strategies.trend_following_daily
        expected_count = len(config.universe)

        assert len(batch.entry_signals) == expected_count
        assert all(s.reason == SignalReason.TREND_ENTRY for s in batch.entry_signals)

    def test_exit_signals_when_close_below_exit_ma(self) -> None:
        """When close drops below exit MA, EXIT signals are emitted."""
        exit_bars = _bars([200.0] * 199 + [50.0])
        db_session = self._make_mock_db_session()

        with patch(
            "trading_platform.strategies.trend_following_daily.strategy.bars_for_sessions",
            return_value=exit_bars,
        ):
            batch = self.strategy.generate_signals(db_session, self.as_of)

        config = self.strategy.settings.strategies.trend_following_daily
        expected_count = len(config.universe)
        assert len(batch.exit_signals) == expected_count

    def test_determinism_same_input_produces_same_output(self) -> None:
        """Evaluating the same bar window twice produces identical signal output."""
        trending = self._trending_bars(200)
        db_session = self._make_mock_db_session()

        with patch(
            "trading_platform.strategies.trend_following_daily.strategy.bars_for_sessions",
            return_value=trending,
        ):
            batch_1 = self.strategy.generate_signals(db_session, self.as_of)

        with patch(
            "trading_platform.strategies.trend_following_daily.strategy.bars_for_sessions",
            return_value=trending,
        ):
            batch_2 = self.strategy.generate_signals(db_session, self.as_of)

        assert batch_1.to_dict() == batch_2.to_dict()

    def test_signal_to_dict_is_serializable(self) -> None:
        """Signal.to_dict returns JSON-serializable structure."""
        import json

        db_session = self._make_mock_db_session()

        with patch(
            "trading_platform.strategies.trend_following_daily.strategy.bars_for_sessions",
            return_value=self._insufficient_bars(),
        ):
            batch = self.strategy.generate_signals(db_session, self.as_of)

        serialized = json.dumps(batch.to_dict(), default=str)
        deserialized = json.loads(serialized)

        assert deserialized["strategy_id"] == "trend_following_daily"
        assert "signals" in deserialized


# ---------------------------------------------------------------------------
# Signal and SignalBatch type tests
# ---------------------------------------------------------------------------


class TestSignalTypes:
    """Structural tests for the signal value objects."""

    def test_signal_batch_entry_exit_flat_partitioning(self) -> None:
        """entry_signals, exit_signals, flat_signals partition the full set."""
        from trading_platform.strategies.signals import (
            IndicatorSnapshot,
            Signal,
            SignalBatch,
            SignalDirection,
            SignalReason,
        )

        as_of = date(2024, 1, 15)
        snapshot = IndicatorSnapshot(
            symbol="AAPL",
            session_date=as_of,
            close=Decimal("150"),
            sma_short=None,
            sma_long=None,
            bars_available=10,
        )

        def _signal(direction: SignalDirection, reason: SignalReason, sym: str) -> Signal:
            return Signal(
                strategy_id="test",
                symbol=sym,
                session_date=as_of,
                direction=direction,
                reason=reason,
                indicators=snapshot,
            )

        batch = SignalBatch(
            strategy_id="test",
            as_of_session=as_of,
            signals=(
                _signal(SignalDirection.LONG, SignalReason.TREND_ENTRY, "A"),
                _signal(SignalDirection.EXIT, SignalReason.CLOSE_BELOW_EXIT_MA, "B"),
                _signal(SignalDirection.FLAT, SignalReason.TREND_NOT_CONFIRMED, "C"),
                _signal(SignalDirection.FLAT, SignalReason.INSUFFICIENT_HISTORY, "D"),
            ),
        )

        assert len(batch.entry_signals) == 1
        assert len(batch.exit_signals) == 1
        assert len(batch.flat_signals) == 2
        assert len(batch.signals) == 4

    def test_indicator_snapshot_to_dict_with_none_smas(self) -> None:
        from trading_platform.strategies.signals import IndicatorSnapshot

        snap = IndicatorSnapshot(
            symbol="SPY",
            session_date=date(2024, 1, 15),
            close=Decimal("450.25"),
            sma_short=None,
            sma_long=None,
            bars_available=10,
        )
        d = snap.to_dict()
        assert d["sma_short"] is None
        assert d["sma_long"] is None
        assert d["close"] == pytest.approx(450.25)
