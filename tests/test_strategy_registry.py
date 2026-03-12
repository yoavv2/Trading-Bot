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

    assert len(strategies) == 1
    assert strategies[0]["strategy_id"] == "trend_following_daily"
    assert strategies[0]["display_name"] == "TrendFollowingDailyV1"
    assert strategies[0]["version"] == "v1"
    assert strategies[0]["enabled"] is True
    assert strategies[0]["config_reference"] == "config/strategies/trend_following_daily.yaml"

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
    assert body["count"] == 1
    assert body["strategies"][0]["strategy_id"] == "trend_following_daily"
    assert body["strategies"][0]["universe_size"] == 10
