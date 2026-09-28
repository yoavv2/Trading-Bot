"""Worker CLI handlers: `report-backtest`, `report-strategy-analytics` (STRUCT-03)."""

from __future__ import annotations

import argparse

from trading_platform.core.logging import configure_logging, get_logger
from trading_platform.core.startup import enforce_startup_config
from trading_platform.services.analytics import (
    build_strategy_analytics_report,
    render_strategy_analytics_report,
)
from trading_platform.services.backtest_reporting import export_backtest_report
from trading_platform.services.config.validation import ExecutionMode


def run_report_backtest_command(args: argparse.Namespace) -> None:
    settings = enforce_startup_config(mode=ExecutionMode.BACKTEST)
    configure_logging(settings.logging)
    manifest = export_backtest_report(
        run_id=args.run_id,
        strategy_id=args.strategy,
        output_dir=args.output_dir,
        summary_format=args.summary_format,
        settings=settings,
    )
    print(manifest.rendered_summary)


def run_report_strategy_analytics_command(args: argparse.Namespace) -> None:
    settings = enforce_startup_config(mode=ExecutionMode.BACKTEST)
    configure_logging(settings.logging)
    logger = get_logger("trading_platform.analytics.report.worker")
    report = build_strategy_analytics_report(
        strategy_id=args.strategy,
        backtest_run_id=args.backtest_run_id,
        paper_run_id=args.paper_run_id,
        inspection_limit=args.inspection_limit,
        settings=settings,
    )
    logger.info(
        "worker_strategy_analytics_report_completed",
        extra={
            "context": {
                "strategy_id": args.strategy,
                "backtest_run_id": args.backtest_run_id,
                "paper_run_id": args.paper_run_id,
                "inspection_limit": args.inspection_limit,
                "summary_format": args.summary_format,
            }
        },
    )
    print(render_strategy_analytics_report(report, summary_format=args.summary_format))
