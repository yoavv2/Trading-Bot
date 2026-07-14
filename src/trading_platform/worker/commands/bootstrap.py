"""Worker CLI handlers: `serve` + `dry-run` (STRUCT-03: extracted from __main__.py)."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime

from trading_platform.core.logging import configure_logging, get_logger
from trading_platform.core.settings import get_strategy_config
from trading_platform.core.startup import enforce_startup_config
from trading_platform.services.bootstrap import run_dry_bootstrap as run_persisted_dry_bootstrap
from trading_platform.services.config.validation import ExecutionMode


def run_placeholder_worker(interval_seconds: int) -> None:
    settings = enforce_startup_config(mode=ExecutionMode.BACKTEST)
    configure_logging(settings.logging)
    logger = get_logger("trading_platform.worker")
    logger.info(
        "worker_started",
        extra={
            "context": {
                "interval_seconds": interval_seconds,
                "environment": settings.app.environment,
            }
        },
    )

    try:
        while True:
            logger.info(
                "worker_heartbeat",
                extra={"context": {"timestamp": datetime.now(UTC).isoformat()}},
            )
            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        logger.info("worker_stopped")


def run_dry_bootstrap(strategy_id: str) -> None:
    settings = enforce_startup_config(mode=ExecutionMode.BACKTEST)
    configure_logging(settings.logging)
    logger = get_logger("trading_platform.worker")
    strategy = get_strategy_config(settings, strategy_id)
    report = run_persisted_dry_bootstrap(
        strategy.strategy_id,
        trigger_source="worker_cli",
        settings=settings,
    )
    logger.info(
        "worker_dry_run_completed",
        extra={"context": {"run_id": report.run_id, "strategy_id": report.strategy_id}},
    )
    print(json.dumps(report.to_dict(), default=str))
