"""Typed runtime settings assembled from YAML files and environment overrides."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_APP_CONFIG_FILE = PROJECT_ROOT / "config" / "app.yaml"
DEFAULT_STRATEGY_CONFIG_DIR = PROJECT_ROOT / "config" / "strategies"


class AppMetadata(BaseModel):
    name: str = "Trading Strategy Platform"
    slug: str = "trading-platform"
    version: str = "0.1.0"
    environment: Literal["local", "test", "development", "staging", "production"] = "local"
    operator_mode: Literal["single_user"] = "single_user"


class ApiSettings(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    base_path: str = "/api/v1"


class LoggingSettings(BaseModel):
    level: str = "INFO"
    format: Literal["json"] = "json"
    service: str = "trading-platform-api"


class DatabaseSettings(BaseModel):
    host: str = "db"
    port: int = 5432
    name: str = "trading_platform"
    user: str = "trading_platform"
    password: str = "trading_platform"
    echo: bool = False
    driver: Literal["psycopg"] = "psycopg"

    @property
    def url(self) -> str:
        return (
            f"postgresql+{self.driver}://{self.user}:{self.password}"
            f"@{self.host}:{self.port}/{self.name}"
        )


class ReadinessSettings(BaseModel):
    dependency_checks_enabled: bool = False
    require_database: bool = False


class PathSettings(BaseModel):
    data_dir: Path = PROJECT_ROOT / ".data"
    strategy_config_dir: Path = DEFAULT_STRATEGY_CONFIG_DIR


class TrendFollowingIndicatorSettings(BaseModel):
    short_window: int = 50
    long_window: int = 200
    warmup_periods: int = 200  # minimum bars required before signals are emitted


class TrendFollowingExitSettings(BaseModel):
    close_below: str = "sma_50"  # exit when close < this moving average
    exit_window: int = 50  # window length matching the close_below MA


class TrendFollowingRiskSettings(BaseModel):
    max_positions: int = 10
    risk_per_trade: float = Field(default=0.01, ge=0.0, le=1.0)


class TrendFollowingDailySettings(BaseModel):
    strategy_id: str = "trend_following_daily"
    display_name: str = "TrendFollowingDailyV1"
    enabled: bool = True
    universe: tuple[str, ...] = (
        "SPY",
        "QQQ",
        "AAPL",
        "MSFT",
        "NVDA",
        "AMD",
        "META",
        "AMZN",
        "GOOGL",
        "TSLA",
    )
    indicators: TrendFollowingIndicatorSettings = TrendFollowingIndicatorSettings()
    risk: TrendFollowingRiskSettings = TrendFollowingRiskSettings()
    exits: TrendFollowingExitSettings = TrendFollowingExitSettings()


class StrategyBundle(BaseModel):
    trend_following_daily: TrendFollowingDailySettings = TrendFollowingDailySettings()


class PolygonProviderSettings(BaseModel):
    """Typed settings for the Polygon.io REST provider."""

    base_url: str = "https://api.polygon.io"
    api_key: str = ""
    adjusted: bool = True
    max_retries: int = 3
    retry_backoff_factor: float = 0.5
    timeout_seconds: float = 30.0


class IngestSettings(BaseModel):
    """Defaults controlling the daily-bar ingest window and symbol universe."""

    default_lookback_days: int = 365
    universe: tuple[str, ...] = (
        "SPY",
        "QQQ",
        "AAPL",
        "MSFT",
        "NVDA",
        "AMD",
        "META",
        "AMZN",
        "GOOGL",
        "TSLA",
    )


class CalendarSettings(BaseModel):
    """Settings for the exchange calendar service."""

    exchange: str = "XNYS"


class MetadataRefreshSettings(BaseModel):
    """Settings controlling symbol metadata refresh behavior."""

    provider: str = "polygon"
    universe: tuple[str, ...] = (
        "SPY",
        "QQQ",
        "AAPL",
        "MSFT",
        "NVDA",
        "AMD",
        "META",
        "AMZN",
        "GOOGL",
        "TSLA",
    )


class MarketDataSettings(BaseModel):
    """Root market-data settings block bundling provider and ingest defaults."""

    polygon: PolygonProviderSettings = PolygonProviderSettings()
    ingest: IngestSettings = IngestSettings()
    calendar: CalendarSettings = CalendarSettings()
    metadata: MetadataRefreshSettings = MetadataRefreshSettings()


class BacktestFeeSettings(BaseModel):
    """Explicit fee assumptions for deterministic backtests."""

    commission_per_order: float = Field(default=0.0, ge=0.0)


class BacktestSlippageSettings(BaseModel):
    """Explicit slippage assumptions for deterministic backtests."""

    model: Literal["bps"] = "bps"
    bps: float = Field(default=5.0, ge=0.0)


class BacktestSettings(BaseModel):
    """Typed settings for the daily-bar backtest runner."""

    initial_capital: float = Field(default=100_000.0, gt=0.0)
    fill_strategy: Literal["next_session_open"] = "next_session_open"
    allocation_model: Literal["equal_weight_slots"] = "equal_weight_slots"
    max_concurrent_positions: int = Field(default=10, ge=1)
    fees: BacktestFeeSettings = BacktestFeeSettings()
    slippage: BacktestSlippageSettings = BacktestSlippageSettings()


class Settings(BaseModel):
    app: AppMetadata = AppMetadata()
    api: ApiSettings = ApiSettings()
    logging: LoggingSettings = LoggingSettings()
    database: DatabaseSettings = DatabaseSettings()
    readiness: ReadinessSettings = ReadinessSettings()
    paths: PathSettings = PathSettings()
    strategies: StrategyBundle = StrategyBundle()
    market_data: MarketDataSettings = MarketDataSettings()
    backtest: BacktestSettings = BacktestSettings()


class EnvironmentOverrides(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TRADING_PLATFORM_",
        env_nested_delimiter="__",
        env_file=".env",
        extra="ignore",
    )

    app: AppMetadata = AppMetadata()
    api: ApiSettings = ApiSettings()
    logging: LoggingSettings = LoggingSettings()
    database: DatabaseSettings = DatabaseSettings()
    readiness: ReadinessSettings = ReadinessSettings()
    paths: PathSettings = PathSettings()
    strategies: StrategyBundle = StrategyBundle()
    market_data: MarketDataSettings = MarketDataSettings()
    backtest: BacktestSettings = BacktestSettings()


def _resolve_path(raw_path: str | Path) -> Path:
    path = Path(raw_path)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _load_yaml_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")

    raw = yaml.safe_load(path.read_text()) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"Expected mapping at top level of {path}")
    return raw


def _load_strategy_bundle(strategy_dir: Path) -> dict[str, Any]:
    if not strategy_dir.exists():
        raise FileNotFoundError(f"Strategy config directory not found: {strategy_dir}")

    strategies: dict[str, Any] = {}
    for path in sorted(strategy_dir.glob("*.yaml")):
        strategies[path.stem] = _load_yaml_file(path)
    return {"strategies": strategies}


def _deep_merge(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _resolve_config_locations(
    *,
    config_file: Path | None = None,
    strategy_dir: Path | None = None,
) -> tuple[Path, Path]:
    resolved_config_file = config_file or _resolve_path(
        os.getenv("TRADING_PLATFORM_CONFIG_FILE", DEFAULT_APP_CONFIG_FILE)
    )

    if strategy_dir is not None:
        return resolved_config_file, strategy_dir

    env_strategy_dir = os.getenv("TRADING_PLATFORM_STRATEGY_CONFIG_DIR")
    if env_strategy_dir:
        return resolved_config_file, _resolve_path(env_strategy_dir)

    app_config = _load_yaml_file(resolved_config_file)
    configured_dir = app_config.get("paths", {}).get("strategy_config_dir", DEFAULT_STRATEGY_CONFIG_DIR)
    return resolved_config_file, _resolve_path(configured_dir)


def build_settings_payload(
    *,
    config_file: Path | None = None,
    strategy_dir: Path | None = None,
) -> dict[str, Any]:
    resolved_config_file, resolved_strategy_dir = _resolve_config_locations(
        config_file=config_file,
        strategy_dir=strategy_dir,
    )
    defaults = Settings().model_dump(mode="python")
    file_config = _load_yaml_file(resolved_config_file)
    strategy_config = _load_strategy_bundle(resolved_strategy_dir)
    env_overrides = EnvironmentOverrides().model_dump(exclude_unset=True, mode="python")

    return _deep_merge(
        _deep_merge(_deep_merge(defaults, file_config), strategy_config),
        env_overrides,
    )


@lru_cache(maxsize=1)
def load_settings(
    *,
    config_file: Path | None = None,
    strategy_dir: Path | None = None,
) -> Settings:
    payload = build_settings_payload(config_file=config_file, strategy_dir=strategy_dir)
    return Settings.model_validate(payload)


def clear_settings_cache() -> None:
    load_settings.cache_clear()


def get_strategy_config(settings: Settings, strategy_id: str) -> TrendFollowingDailySettings:  # noqa: E501
    try:
        return getattr(settings.strategies, strategy_id)
    except AttributeError as exc:
        available = ", ".join(sorted(settings.strategies.__class__.model_fields.keys()))
        raise KeyError(f"Unknown strategy '{strategy_id}'. Available: {available}") from exc
