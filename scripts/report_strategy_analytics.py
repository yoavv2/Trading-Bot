#!/usr/bin/env python
"""Render a strategy analytics summary plus recent operational inspection data."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence

from trading_platform.core.logging import configure_logging
from trading_platform.core.settings import load_settings
from trading_platform.services.analytics import build_strategy_analytics_report, render_strategy_analytics_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scripts/report_strategy_analytics.py")
    parser.add_argument("--strategy", default="trend_following_daily")
    parser.add_argument("--backtest-run-id", help="Explicit backtest run ID to summarize.")
    parser.add_argument("--paper-run-id", help="Explicit paper execution run ID to summarize.")
    parser.add_argument("--inspection-limit", type=int, default=5)
    parser.add_argument("--summary-format", choices=("markdown", "json"), default="markdown")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings()
    configure_logging(settings.logging)
    logger = logging.getLogger("trading_platform.analytics.report.cli")

    try:
        report = build_strategy_analytics_report(
            strategy_id=args.strategy,
            backtest_run_id=args.backtest_run_id,
            paper_run_id=args.paper_run_id,
            inspection_limit=args.inspection_limit,
            settings=settings,
        )
    except Exception as exc:
        logger.exception(
            "strategy_analytics_report_failed",
            extra={
                "context": {
                    "strategy_id": args.strategy,
                    "backtest_run_id": args.backtest_run_id,
                    "paper_run_id": args.paper_run_id,
                    "error": str(exc),
                }
            },
        )
        print(f"strategy analytics report failed: {exc}", file=sys.stderr)
        return 1

    print(render_strategy_analytics_report(report, summary_format=args.summary_format))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
