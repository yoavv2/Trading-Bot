from __future__ import annotations

import sys
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.jobs.registry import JobRegistry


def _write_config(path: Path, payload: dict) -> None:
    path.write_text(yaml.safe_dump(payload, sort_keys=False))


def test_settings_loader_merges_file_and_environment(monkeypatch, tmp_path: Path) -> None:
    config_file = tmp_path / "app.yaml"
    strategy_dir = tmp_path / "strategies"
    strategy_dir.mkdir()

    _write_config(
        config_file,
        {
            "app": {
                "name": "Trading Strategy Platform",
                "slug": "trading-platform",
                "version": "0.1.0",
                "environment": "local",
                "operator_mode": "single_user",
            },
            "api": {"host": "0.0.0.0", "port": 8000, "base_path": "/api/v1"},
            "logging": {"level": "INFO", "format": "json", "service": "trading-platform-api"},
            "database": {
                "host": "db",
                "port": 5432,
                "name": "trading_platform",
                "user": "trading_platform",
                "password": "trading_platform",
                "echo": False,
                "driver": "psycopg",
            },
            "readiness": {"dependency_checks_enabled": False, "require_database": False},
            "paths": {
                "data_dir": ".data",
                "strategy_config_dir": str(strategy_dir),
            },
        },
    )
    _write_config(
        strategy_dir / "trend_following_daily.yaml",
        {
            "strategy_id": "trend_following_daily",
            "display_name": "TrendFollowingDailyV1",
            "enabled": True,
            "universe": ["SPY", "QQQ"],
            "indicators": {"short_window": 50, "long_window": 200},
            "risk": {"max_positions": 5, "risk_per_trade": 0.01},
            "exits": {"close_below": "sma_50"},
        },
    )

    monkeypatch.setenv("TRADING_PLATFORM_CONFIG_FILE", str(config_file))
    monkeypatch.setenv("TRADING_PLATFORM_STRATEGY_CONFIG_DIR", str(strategy_dir))
    monkeypatch.setenv("TRADING_PLATFORM_API__PORT", "9090")
    monkeypatch.setenv(
        "TRADING_PLATFORM_STRATEGIES__TREND_FOLLOWING_DAILY__RISK__MAX_POSITIONS", "7"
    )

    clear_settings_cache()
    settings = load_settings()

    assert settings.api.port == 9090
    assert settings.strategies.trend_following_daily.universe == ("SPY", "QQQ")
    assert settings.strategies.trend_following_daily.risk.max_positions == 7


def test_app_bootstrap_serves_foundation_endpoints(monkeypatch, tmp_path: Path) -> None:
    config_file = tmp_path / "app.yaml"
    strategy_dir = tmp_path / "strategies"
    strategy_dir.mkdir()

    _write_config(
        config_file,
        {
            "app": {
                "name": "Trading Strategy Platform",
                "slug": "trading-platform",
                "version": "0.1.0",
                "environment": "test",
                "operator_mode": "single_user",
            },
            "api": {"host": "127.0.0.1", "port": 8001, "base_path": "/api/v1"},
            "logging": {"level": "INFO", "format": "json", "service": "trading-platform-api"},
            "database": {
                "host": "db",
                "port": 5432,
                "name": "trading_platform",
                "user": "trading_platform",
                "password": "trading_platform",
                "echo": False,
                "driver": "psycopg",
            },
            "readiness": {"dependency_checks_enabled": False, "require_database": False},
            "paths": {
                "data_dir": ".data",
                "strategy_config_dir": str(strategy_dir),
            },
        },
    )
    _write_config(
        strategy_dir / "trend_following_daily.yaml",
        {
            "strategy_id": "trend_following_daily",
            "display_name": "TrendFollowingDailyV1",
            "enabled": True,
            "universe": ["SPY", "QQQ", "AAPL"],
            "indicators": {"short_window": 50, "long_window": 200},
            "risk": {"max_positions": 10, "risk_per_trade": 0.01},
            "exits": {"close_below": "sma_50"},
        },
    )

    monkeypatch.setenv("TRADING_PLATFORM_CONFIG_FILE", str(config_file))
    monkeypatch.setenv("TRADING_PLATFORM_STRATEGY_CONFIG_DIR", str(strategy_dir))

    clear_settings_cache()
    settings = load_settings()
    startup_calls: list[dict[str, object]] = []

    def _enforce_startup_config(**kwargs: object):
        startup_calls.append(kwargs)
        return settings

    import trading_platform.api.app as api_app

    monkeypatch.setattr(api_app, "enforce_startup_config", _enforce_startup_config)
    injected_registry = JobRegistry()
    app = api_app.create_app(job_registry=injected_registry)
    registered_paths = set(app.openapi()["paths"])

    with TestClient(app) as client:
        health = client.get("/health")
        ready = client.get("/ready")
        system = client.get("/api/v1/system")
        strategies = client.get("/api/v1/strategies")
        strategy_detail = client.get("/api/v1/strategies/trend_following_daily")

    assert health.status_code == 200
    assert startup_calls == [{"mode": api_app.ExecutionMode.BACKTEST, "require_database": True}]
    assert app.state.job_registry is injected_registry
    assert health.json()["status"] == "ok"

    assert ready.status_code == 200
    ready_body = ready.json()
    assert ready_body["ready"] is True
    assert ready_body["checks"]["database"]["status"] == "skipped"

    assert system.status_code == 200
    system_body = system.json()
    assert system_body["application"]["environment"] == "test"
    assert system_body["strategy_catalog"][0]["universe_size"] == 3
    assert system_body["operator_read_api"]["status"] == "available"
    assert (
        system_body["operator_read_api"]["analytics"]["strategy_summary"]
        == "/api/v1/analytics/strategies/{strategy_id}"
    )

    assert strategies.status_code == 200
    assert strategies.json()["count"] == 1

    assert strategy_detail.status_code == 200
    assert (
        strategy_detail.json()["operator_reads"]["runs"]
        == "/api/v1/runs?strategy_id=trend_following_daily"
    )

    assert {
        "/api/v1/analytics/strategies/{strategy_id}",
        "/api/v1/runs",
        "/api/v1/runs/{run_id}",
        "/api/v1/operations/orders",
        "/api/v1/operations/fills",
        "/api/v1/operations/positions",
        "/api/v1/operations/account-snapshots",
        "/api/v1/operations/risk-events",
        "/api/v1/operations/execution-events",
    }.issubset(registered_paths)
