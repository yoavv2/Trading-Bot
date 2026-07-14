"""Worker CLI handler: `reconcile-paper-execution` (STRUCT-03: extracted from __main__.py)."""

from __future__ import annotations

import argparse
import json

from trading_platform.core.logging import configure_logging, get_logger
from trading_platform.core.startup import enforce_startup_config
from trading_platform.services.config.validation import ExecutionMode
from trading_platform.services.execution import resolve_submission_session
from trading_platform.services.reconciliation import reconcile_paper_execution


def run_reconcile_paper_execution_command(args: argparse.Namespace) -> None:
    settings = enforce_startup_config(mode=ExecutionMode.PAPER)
    configure_logging(settings.logging)
    logger = get_logger("trading_platform.worker")
    as_of_session = resolve_submission_session(
        settings=settings,
        as_of_arg=args.as_of,
    )
    strategy_id = args.strategy or settings.execution.paper_session_runner.default_strategy_id
    report = reconcile_paper_execution(
        strategy_id,
        as_of_session=as_of_session,
        settings=settings,
        trigger_source=args.trigger_source,
    )
    logger.info(
        "worker_paper_reconciliation_completed",
        extra={
            "context": {
                "strategy_id": strategy_id,
                "as_of_session": as_of_session.isoformat(),
                "finding_count": report.finding_count,
                "blocks_execution": report.blocks_execution,
            }
        },
    )
    indent = None if args.compact else 2
    print(json.dumps(report.to_dict(), indent=indent, default=str))
