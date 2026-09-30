from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trading_platform.api.app import create_app
from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.strategies.registry import UnknownStrategyError, build_default_registry


def test_registry_lists_and_resolves_default_strategy() -> None:
    clear_settings_cache()
    registry = build_default_registry(load_settings())

    strategies = registry.list_public()

    assert len(strategies) == 4
    by_id = {strategy["strategy_id"]: strategy for strategy in strategies}
    assert set(by_id) == {
        "donchian_breakout_daily",
        "rsi_mean_reversion_daily",
        "time_series_momentum_daily",
        "trend_following_daily",
    }
    trend = by_id["trend_following_daily"]
    assert trend["display_name"] == "TrendFollowingDailyV1"
    assert trend["version"] == "v1"
    assert trend["enabled"] is True
    assert trend["config_reference"] == "config/strategies/trend_following_daily.yaml"

    resolved = registry.resolve("trend_following_daily")
    assert resolved.metadata.display_name == "TrendFollowingDailyV1"
    assert len(resolved.metadata.universe) == 10

    with pytest.raises(UnknownStrategyError):
        registry.resolve("missing_strategy")


def test_strategies_route_uses_registry_metadata() -> None:
    clear_settings_cache()
    app = create_app()

    with TestClient(app) as client:
        response = client.get("/strategies")

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 4
    by_id = {strategy["strategy_id"]: strategy for strategy in body["strategies"]}
    assert by_id["trend_following_daily"]["universe_size"] == 10
