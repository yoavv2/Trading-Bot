"""S0 foundation: research settings, key alias, calendar pin, registry selection,
trading-path invariance of the explicit bar source."""

from __future__ import annotations

from datetime import date

import exchange_calendars as xcals
import pytest
from tests.support.research_fixtures import FakeBarStore, make_bars, weekday_sessions

from trading_platform.core.settings import (
    build_settings_payload,
    clear_settings_cache,
    load_settings,
)
from trading_platform.core.startup import enforce_startup_config
from trading_platform.jobs.registry import (
    RESEARCH_JOB_TYPES,
    build_default_registry,
    build_registry_for,
    build_research_registry,
)
from trading_platform.services import calendar as calendar_module
from trading_platform.services.research.environment import apply_research_environment
from trading_platform.strategies.donchian_breakout_daily import strategy as donchian_module
from trading_platform.strategies.rsi_mean_reversion_daily import strategy as rsi_module
from trading_platform.strategies.time_series_momentum_daily import strategy as tsm_module
from trading_platform.strategies.trend_following_daily import strategy as tf_module


@pytest.fixture(autouse=True)
def _clear_pin():
    calendar_module.pin_calendar_start(None)
    yield
    calendar_module.pin_calendar_start(None)


def test_research_defaults_keep_the_trading_path() -> None:
    clear_settings_cache()
    research = load_settings().research
    assert research.mode is False
    assert (research.bar_provider, research.bar_adjusted) == ("polygon", True)
    assert research.calendar_start is None
    assert research.max_assets_per_study == 10
    assert research.quantity_policy_default == "fractional"
    assert research.ai.enabled is False and research.ai.usable is False
    assert research.ai.model == "claude-sonnet-5-5"
    assert research.tiingo.api_key == ""


def test_ai_stays_unusable_without_key_and_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRADING_PLATFORM_RESEARCH__AI__ENABLED", "true")
    monkeypatch.setenv("TRADING_PLATFORM_RESEARCH__AI__API_KEY", "k")
    clear_settings_cache()
    assert load_settings().research.ai.usable is False  # no limits configured
    monkeypatch.setenv("TRADING_PLATFORM_RESEARCH__AI__MAX_REQUESTS_PER_DAY", "5")
    monkeypatch.setenv("TRADING_PLATFORM_RESEARCH__AI__MAX_OUTPUT_TOKENS", "2000")
    clear_settings_cache()
    assert load_settings().research.ai.usable is True


def test_tiingo_key_alias_and_prefixed_form(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TIINGO_API_KEY", "alias-key")
    clear_settings_cache()
    assert load_settings().research.tiingo.api_key == "alias-key"
    monkeypatch.setenv("TRADING_PLATFORM_RESEARCH__TIINGO__API_KEY", "prefixed-key")
    clear_settings_cache()
    assert load_settings().research.tiingo.api_key == "prefixed-key"


def test_research_mode_env_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRADING_PLATFORM_RESEARCH__MODE", "true")
    monkeypatch.setenv("TRADING_PLATFORM_RESEARCH__BAR_PROVIDER", "tiingo")
    monkeypatch.setenv("TRADING_PLATFORM_RESEARCH__CALENDAR_START", "2000-01-03")
    clear_settings_cache()
    research = load_settings().research
    assert research.mode is True
    assert research.bar_provider == "tiingo"
    assert research.calendar_start == date(2000, 1, 3)


def test_calendar_default_is_the_library_default_and_pin_extends_it() -> None:
    default_first = xcals.get_calendar("XNYS").first_session.date()
    assert calendar_module.get_calendar().first_session.date() == default_first
    calendar_module.pin_calendar_start(date(1995, 1, 2))
    pinned_first = calendar_module.get_calendar().first_session.date()
    assert date(1995, 1, 2) <= pinned_first <= date(1995, 1, 5)
    assert pinned_first < default_first
    calendar_module.pin_calendar_start(None)
    assert calendar_module.get_calendar().first_session.date() == default_first


def test_apply_research_environment_pins_only_in_research_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_settings_cache()
    calendar_module.pin_calendar_start(date(1990, 1, 2))
    apply_research_environment(load_settings())  # mode off -> clears
    assert calendar_module.pinned_calendar_start() is None
    monkeypatch.setenv("TRADING_PLATFORM_RESEARCH__MODE", "true")
    monkeypatch.setenv("TRADING_PLATFORM_RESEARCH__CALENDAR_START", "2001-01-02")
    clear_settings_cache()
    apply_research_environment(load_settings())
    assert calendar_module.pinned_calendar_start() == date(2001, 1, 2)


def test_startup_gate_applies_the_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_settings_cache()
    payload = build_settings_payload()
    payload["research"] = {"mode": True, "calendar_start": "2002-01-02"}
    enforce_startup_config(require_database=False, payload=payload)
    assert calendar_module.pinned_calendar_start() == date(2002, 1, 2)


def test_registries_are_closed_sets_and_selected_by_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_settings_cache()
    settings = load_settings()
    assert build_research_registry(settings).list_job_types() == sorted(RESEARCH_JOB_TYPES)
    assert build_research_registry(settings).list_job_types() == [
        "catalog-sync",
        "ingest-tiingo-bars",
        "research-backtest",
        "research-evaluate",
        "research-freeze",
        "sync-market-sessions",
    ]
    default_types = build_default_registry(settings).list_job_types()
    assert "paper-session" in default_types and "catalog-sync" not in default_types
    assert build_registry_for(settings).list_job_types() == default_types
    monkeypatch.setenv("TRADING_PLATFORM_RESEARCH__MODE", "true")
    clear_settings_cache()
    assert build_registry_for(load_settings()).list_job_types() == sorted(RESEARCH_JOB_TYPES)


@pytest.mark.parametrize(
    ("module", "cls", "key"),
    [
        (tf_module, tf_module.TrendFollowingDailyStrategy, "trend_following_daily"),
        (rsi_module, rsi_module.RsiMeanReversionDailyStrategy, "rsi_mean_reversion_daily"),
        (donchian_module, donchian_module.DonchianBreakoutDailyStrategy, "donchian_breakout_daily"),
        (tsm_module, tsm_module.TimeSeriesMomentumDailyStrategy, "time_series_momentum_daily"),
    ],
)
def test_strategies_pass_the_trading_bar_source_explicitly(monkeypatch: pytest.MonkeyPatch, module, cls, key) -> None:
    clear_settings_cache()
    settings = load_settings()
    sessions = weekday_sessions(date(2020, 1, 2), 300)
    store = FakeBarStore(sessions, {"AAPL": make_bars("AAPL", sessions)})
    monkeypatch.setattr(module, "bars_for_sessions", store.bars_for_sessions)
    monkeypatch.setattr(getattr(settings.strategies, key), "universe", ("AAPL",))
    cls(settings).generate_signals(None, sessions[-1])
    assert store.calls and all(c["adjusted"] is True and c["provider"] == "polygon" for c in store.calls)

    monkeypatch.setenv("TRADING_PLATFORM_RESEARCH__BAR_PROVIDER", "tiingo")
    clear_settings_cache()
    research_settings = load_settings()
    monkeypatch.setattr(getattr(research_settings.strategies, key), "universe", ("AAPL",))
    store.calls.clear()
    cls(research_settings).generate_signals(None, sessions[-1])
    assert store.calls and all(c["provider"] == "tiingo" for c in store.calls)
