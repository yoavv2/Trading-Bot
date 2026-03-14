#!/usr/bin/env python
"""Render and export a persisted backtest report."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence

from trading_platform.core.logging import configure_logging
from trading_platform.core.settings import load_settings
from trading_platform.services.backtest_reporting import export_backtest_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scripts/export_backtest_report.py")
    parser.add_argument("--run-id", help="Explicit backtest run ID to export.")
    parser.add_argument("--strategy", default="trend_following_daily")
    parser.add_argument("--summary-format", choices=("markdown", "json"), default="markdown")
    parser.add_argument("--output-dir", help="Directory for summary and CSV exports.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings()
    configure_logging(settings.logging)
    logger = logging.getLogger("trading_platform.backtest.report.cli")

    try:
        manifest = export_backtest_report(
            run_id=args.run_id,
            strategy_id=args.strategy,
            output_dir=args.output_dir,
            summary_format=args.summary_format,
            settings=settings,
        )
    except Exception as exc:
        logger.exception(
            "backtest_report_cli_failed",
            extra={"context": {"run_id": args.run_id, "strategy_id": args.strategy, "error": str(exc)}},
        )
        print(f"backtest report failed: {exc}", file=sys.stderr)
        return 1

    print(manifest.rendered_summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
