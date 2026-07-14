"""Worker CLI handler: `evaluate-risk` (STRUCT-03: extracted from __main__.py)."""

from __future__ import annotations

import argparse
import json

from trading_platform.core.logging import configure_logging, get_logger
from trading_platform.core.startup import enforce_startup_config
from trading_platform.services.config.validation import ExecutionMode
from trading_platform.services.risk import resolve_evaluation_session, run_risk_evaluation


def run_evaluate_risk_command(args: argparse.Namespace) -> None:
    settings = enforce_startup_config(mode=ExecutionMode.BACKTEST)
    configure_logging(settings.logging)
    logger = get_logger("trading_platform.worker")
    as_of_session = resolve_evaluation_session(
        settings=settings,
        as_of_arg=args.as_of,
    )
    report = run_risk_evaluation(
        args.strategy,
        as_of_session=as_of_session,
        trigger_source=args.trigger_source,
        settings=settings,
    )
    logger.info(
        "worker_risk_evaluation_completed",
        extra={
            "context": {
                "run_id": report.run_id,
                "strategy_id": report.strategy_id,
                "status": report.status,
                "as_of_session": as_of_session.isoformat(),
            }
        },
    )
    indent = None if args.compact else 2
    print(json.dumps(report.to_dict(), indent=indent, default=str))
