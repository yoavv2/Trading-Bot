"""Parity matrix: the four example specifications against their Python originals (contract §9).

Both sides read bars through ``bars_for_sessions`` served by the same in-memory store,
so the only difference under test is the computation. Every evaluation date in the
fixture is compared on ``(session_date, direction)`` and, where the original exposes
them, on indicator values with tolerance 0. Seeding the examples is gated on this file.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
import yaml
from tests.support.research_fixtures import Bar, FakeBarStore, make_bars, weekday_sessions

from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.services.research.examples import EXAMPLES_DIR
from trading_platform.strategies import declarative as declarative_module
from trading_platform.strategies.donchian_breakout_daily import strategy as donchian_module
from trading_platform.strategies.research_registry import strategy_from_compiled
from trading_platform.strategies.rsi_mean_reversion_daily import strategy as rsi_module
from trading_platform.strategies.signals import SignalDirection, SignalReason
from trading_platform.strategies.spec import validate_spec
from trading_platform.strategies.spec.evaluate import compute_rsi, term_value
from trading_platform.strategies.time_series_momentum_daily import strategy as tsm_module
from trading_platform.strategies.trend_following_daily import strategy as tf_module

ORIGINALS = {
    "trend_following_daily": (tf_module, tf_module.TrendFollowingDailyStrategy, ("sma_short", "sma_long")),
    "rsi_mean_reversion_daily": (rsi_module, rsi_module.RsiMeanReversionDailyStrategy, ("rsi",)),
    "donchian_breakout_daily": (donchian_module, donchian_module.DonchianBreakoutDailyStrategy, ("entry_channel_high", "exit_channel_low")),
    "time_series_momentum_daily": (tsm_module, tsm_module.TimeSeriesMomentumDailyStrategy, ("lookback_close",)),
}
VALUE_MAP = {
    "trend_following_daily": {"sma_short": "sma_fast", "sma_long": "sma_slow"},
    "rsi_mean_reversion_daily": {"rsi": "rsi14"},
    "donchian_breakout_daily": {"entry_channel_high": "channel_high", "exit_channel_low": "channel_low"},
    "time_series_momentum_daily": {"lookback_close": "lookback_close"},
}
SYMBOL = "AAPL"
SESSIONS = weekday_sessions(date(2016, 1, 4), 900)


def _settings_with_universe():
    clear_settings_cache()
    settings = load_settings()
    return settings


def _install_store(monkeypatch: pytest.MonkeyPatch, store: FakeBarStore) -> None:
    for module in (tf_module, rsi_module, donchian_module, tsm_module, declarative_module):
        monkeypatch.setattr(module, "bars_for_sessions", store.bars_for_sessions)


def _restrict_universe(monkeypatch: pytest.MonkeyPatch, settings, key: str) -> None:
    config = getattr(settings.strategies, key)
    monkeypatch.setattr(config, "universe", (SYMBOL,))


def _compiled(key: str):
    return validate_spec(yaml.safe_load((EXAMPLES_DIR / f"{key}.yaml").read_text()))


def _snapshot_value(signal, name: str):
    snapshot = signal.indicators
    if name == "sma_short":
        return snapshot.sma_short
    if name == "sma_long":
        return snapshot.sma_long
    return snapshot.values.get(name)


def _compare(key: str, original, declarative, store: FakeBarStore, dates: list[date]) -> None:
    for as_of in dates:
        a = original.generate_signals(None, as_of).signals[0]
        b = declarative.generate_signals(None, as_of).signals[0]
        assert (a.session_date, a.direction) == (b.session_date, b.direction), (key, as_of, a.reason, b.reason)
        if a.reason == SignalReason.INSUFFICIENT_HISTORY:
            # Below warm-up both sides are FLAT; the originals still expose partial
            # indicator values (informational), the interpreter exposes none.
            assert b.reason == SignalReason.RULE_INSUFFICIENT_HISTORY, (key, as_of)
            continue
        for original_name, spec_name in VALUE_MAP[key].items():
            assert _snapshot_value(a, original_name) == b.indicators.values.get(spec_name), (key, as_of, original_name)


@pytest.mark.parametrize("key", list(ORIGINALS))
def test_parity_across_the_full_series_including_early_history(monkeypatch: pytest.MonkeyPatch, key: str) -> None:
    settings = _settings_with_universe()
    _restrict_universe(monkeypatch, settings, key)
    module, cls, _ = ORIGINALS[key]
    bars = make_bars(SYMBOL, SESSIONS, seed=3, volatility=0.025)
    store = FakeBarStore(SESSIONS, {SYMBOL: bars})
    _install_store(monkeypatch, store)
    compiled = _compiled(key)
    original = cls(settings)
    declarative = strategy_from_compiled(settings, compiled, universe=(SYMBOL,))
    assert compiled.history_required == original.warmup_periods
    # Fixtures 1-3: below, exactly at, and far beyond history_required (every date).
    _compare(key, original, declarative, store, SESSIONS)
    # Direction mix sanity: the series must actually produce entries and exits.
    directions = {declarative.generate_signals(None, d).signals[0].direction for d in SESSIONS[300:]}
    assert SignalDirection.LONG in directions and SignalDirection.EXIT in directions


@pytest.mark.parametrize("key", list(ORIGINALS))
def test_parity_with_a_gap_inside_the_window(monkeypatch: pytest.MonkeyPatch, key: str) -> None:
    settings = _settings_with_universe()
    _restrict_universe(monkeypatch, settings, key)
    _, cls, _ = ORIGINALS[key]
    bars = make_bars(SYMBOL, SESSIONS, seed=5)
    missing = SESSIONS[400]
    gapped = [b for b in bars if b.session_date != missing]
    store = FakeBarStore(SESSIONS, {SYMBOL: gapped})
    _install_store(monkeypatch, store)
    compiled = _compiled(key)
    original = cls(settings)
    declarative = strategy_from_compiled(settings, compiled, universe=(SYMBOL,))
    window = SESSIONS[395 : 400 + compiled.history_required + 5]
    _compare(key, original, declarative, store, window)
    # On the day after the gap both sides see fewer than history_required bars -> FLAT.
    after = SESSIONS[401]
    a = original.generate_signals(None, after).signals[0]
    b = declarative.generate_signals(None, after).signals[0]
    assert a.direction == b.direction == SignalDirection.FLAT


def test_rsi_history_dependence_is_real(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fixture 3 for RSI: a different history changes values (and, somewhere, a signal)."""

    bars = make_bars(SYMBOL, SESSIONS, seed=3, volatility=0.025)
    closes_all = [b.close for b in bars]
    differs_values = 0
    flips = 0
    for end in range(400, 900):
        closes = closes_all[:end]
        r100 = compute_rsi(closes[-100:], 14)
        r50 = compute_rsi(closes[-50:], 14)
        r300 = compute_rsi(closes[-300:], 14)
        if r100 != r50 or r100 != r300:
            differs_values += 1
        if (r100 < 30) != (r50 < 30) or (r100 > 70) != (r50 > 70):
            flips += 1
    assert differs_values > 400
    assert flips >= 1, "no threshold flip between history 100 and 50 in this fixture"


def _threshold_bars(values: list[Decimal]) -> list[Bar]:
    sessions = weekday_sessions(date(2020, 1, 6), len(values))
    return [Bar(SYMBOL, s, v, v, v, v, 1000) for s, v in zip(sessions, values)]


def test_threshold_adjacency_momentum_equal_close_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings_with_universe()
    _restrict_universe(monkeypatch, settings, "time_series_momentum_daily")
    values = [Decimal(100)] * 253  # close == lag close -> original emits EXIT (<=)
    bars = _threshold_bars(values)
    sessions = [b.session_date for b in bars]
    store = FakeBarStore(sessions, {SYMBOL: bars})
    _install_store(monkeypatch, store)
    original = tsm_module.TimeSeriesMomentumDailyStrategy(settings)
    declarative = strategy_from_compiled(settings, _compiled("time_series_momentum_daily"), universe=(SYMBOL,))
    a = original.generate_signals(None, sessions[-1]).signals[0]
    b = declarative.generate_signals(None, sessions[-1]).signals[0]
    assert a.direction == b.direction == SignalDirection.EXIT


def test_threshold_adjacency_donchian_tick_above_and_below(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings_with_universe()
    _restrict_universe(monkeypatch, settings, "donchian_breakout_daily")
    base = [Decimal(100)] * 55
    for last, expected in ((Decimal("100.000001"), SignalDirection.LONG), (Decimal(100), SignalDirection.FLAT)):
        bars = _threshold_bars(base + [last])
        sessions = [b.session_date for b in bars]
        store = FakeBarStore(sessions, {SYMBOL: bars})
        _install_store(monkeypatch, store)
        original = donchian_module.DonchianBreakoutDailyStrategy(settings)
        declarative = strategy_from_compiled(settings, _compiled("donchian_breakout_daily"), universe=(SYMBOL,))
        a = original.generate_signals(None, sessions[-1]).signals[0]
        b = declarative.generate_signals(None, sessions[-1]).signals[0]
        assert a.direction == b.direction == expected


def test_threshold_adjacency_trend_following_exit_and_entry_both_true(monkeypatch: pytest.MonkeyPatch) -> None:
    """sma_slow < close < sma_fast with sma_fast > sma_slow: the original checks exit first."""

    settings = _settings_with_universe()
    _restrict_universe(monkeypatch, settings, "trend_following_daily")
    values = [Decimal(50)] * 150 + [Decimal(200)] * 49 + [Decimal(150)]
    bars = _threshold_bars(values)
    sessions = [b.session_date for b in bars]
    store = FakeBarStore(sessions, {SYMBOL: bars})
    _install_store(monkeypatch, store)
    original = tf_module.TrendFollowingDailyStrategy(settings)
    declarative = strategy_from_compiled(settings, _compiled("trend_following_daily"), universe=(SYMBOL,))
    a = original.generate_signals(None, sessions[-1]).signals[0]
    b = declarative.generate_signals(None, sessions[-1]).signals[0]
    assert a.indicators.sma_long < Decimal(150) < a.indicators.sma_short
    assert a.direction == b.direction == SignalDirection.EXIT


def test_threshold_adjacency_rsi_near_30_and_70(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings_with_universe()
    _restrict_universe(monkeypatch, settings, "rsi_mean_reversion_daily")
    bars = make_bars(SYMBOL, SESSIONS, seed=3, volatility=0.025)
    store = FakeBarStore(SESSIONS, {SYMBOL: bars})
    _install_store(monkeypatch, store)
    compiled = _compiled("rsi_mean_reversion_daily")
    original = rsi_module.RsiMeanReversionDailyStrategy(settings)
    declarative = strategy_from_compiled(settings, compiled, universe=(SYMBOL,))
    close_dates = []
    for as_of in SESSIONS[120:]:
        value = term_value(compiled.terms["rsi14"], store.bars_for_sessions(None, SYMBOL, 100, as_of))
        if value is not None and (abs(value - 30) < 2 or abs(value - 70) < 2):
            close_dates.append(as_of)
    assert close_dates, "fixture produces no RSI values near the thresholds"
    _compare("rsi_mean_reversion_daily", original, declarative, store, close_dates)


def test_declarative_is_deterministic_across_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings_with_universe()
    bars = make_bars(SYMBOL, SESSIONS, seed=9)
    store = FakeBarStore(SESSIONS, {SYMBOL: bars})
    _install_store(monkeypatch, store)
    declarative = strategy_from_compiled(settings, _compiled("trend_following_daily"), universe=(SYMBOL,))
    first = [declarative.generate_signals(None, d).to_dict() for d in SESSIONS[250:300]]
    second = [declarative.generate_signals(None, d).to_dict() for d in SESSIONS[250:300]]
    assert first == second


# ---------------------------------------------------------------------------
# Contract §9 item 3 through the real path: YAML -> validator -> interpreter -> bar loader
# ---------------------------------------------------------------------------

RSI_YAML = """spec_version: 1
name: RSI history {history}
timeframe: daily
direction: long_only
indicators:
  rsi14: {{type: rsi, source: close, window: 14, history: {history}}}
entry:
  all_of:
    - {{left: rsi14, op: lt, right: 30}}
exit:
  any_of:
    - {{left: rsi14, op: gt, right: 70}}
"""


def test_rsi_history_dependence_through_the_interpreter(monkeypatch: pytest.MonkeyPatch) -> None:
    """The same synthetic series, two specifications differing only in ``history``
    (50 vs 100), each parsed from YAML, validated, resolved to a ``DeclarativeDailyStrategy``
    and run through ``generate_signals`` with bars served by the access-layer seam. Values
    differ on most sessions and the direction differs on at least one; the ``history: 100``
    run matches the original Python RSI strategy on every session and value."""

    from trading_platform.strategies.spec.validate import validate_yaml

    settings = _settings_with_universe()
    _restrict_universe(monkeypatch, settings, "rsi_mean_reversion_daily")
    bars = make_bars(SYMBOL, SESSIONS, seed=3, volatility=0.025)
    store = FakeBarStore(SESSIONS, {SYMBOL: bars})
    _install_store(monkeypatch, store)

    compiled_50 = validate_yaml(RSI_YAML.format(history=50))
    compiled_100 = validate_yaml(RSI_YAML.format(history=100))
    assert (compiled_50.history_required, compiled_100.history_required) == (50, 100)
    assert compiled_50.spec_sha256 != compiled_100.spec_sha256
    strategy_50 = strategy_from_compiled(settings, compiled_50, universe=(SYMBOL,))
    strategy_100 = strategy_from_compiled(settings, compiled_100, universe=(SYMBOL,))
    original = rsi_module.RsiMeanReversionDailyStrategy(settings)

    dates = SESSIONS[120:]
    value_differs = 0
    direction_differs: list[date] = []
    for as_of in dates:
        s50 = strategy_50.generate_signals(None, as_of).signals[0]
        s100 = strategy_100.generate_signals(None, as_of).signals[0]
        o = original.generate_signals(None, as_of).signals[0]
        v50, v100 = s50.indicators.values["rsi14"], s100.indicators.values["rsi14"]
        assert v50 is not None and v100 is not None
        if v50 != v100:
            value_differs += 1
        if s50.direction != s100.direction:
            direction_differs.append(as_of)
        # history: 100 is the original's 100-bar warm-up: identical value and direction.
        assert (o.direction, o.indicators.values["rsi"]) == (s100.direction, v100), as_of
    # The loader was asked for exactly history_required bars by each interpreter.
    assert {c["n_sessions"] for c in store.calls} == {50, 100}
    assert value_differs > 0.8 * len(dates)
    assert direction_differs, "no session where history 50 and 100 disagree on direction"
    # And the disagreement is not an artefact: at least one date has the 50-bar RSI on the
    # other side of a threshold than the 100-bar RSI.
    flip = direction_differs[0]
    v50 = strategy_50.generate_signals(None, flip).signals[0].indicators.values["rsi14"]
    v100 = strategy_100.generate_signals(None, flip).signals[0].indicators.values["rsi14"]
    assert (v50 < 30) != (v100 < 30) or (v50 > 70) != (v100 > 70)
