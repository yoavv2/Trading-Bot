#!/usr/bin/env python
"""Execute a deterministic daily-bar backtest for a registered strategy."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence

from trading_platform.core.logging import configure_logging
from trading_platform.core.settings import load_settings
from trading_platform.services.backtesting import resolve_backtest_window, run_backtest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scripts/run_backtest.py")
    parser.add_argument("--strategy", default="trend_following_daily")
    parser.add_argument("--from-date", metavar="YYYY-MM-DD", help="Backtest window start (inclusive).")
    parser.add_argument("--to-date", metavar="YYYY-MM-DD", help="Backtest window end (inclusive).")
    parser.add_argument("--compact", action="store_true", default=False)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings()
    configure_logging(settings.logging)
    logger = logging.getLogger("trading_platform.backtest.cli")

    try:
        from_date, to_date = resolve_backtest_window(
            settings=settings,
            from_date_arg=args.from_date,
            to_date_arg=args.to_date,
        )
        report = run_backtest(
            args.strategy,
            from_date=from_date,
            to_date=to_date,
            trigger_source="backtest_script",
            settings=settings,
        )
    except Exception as exc:
        logger.exception(
            "backtest_cli_failed",
            extra={"context": {"strategy_id": args.strategy, "error": str(exc)}},
        )
        print(f"backtest failed for {args.strategy}: {exc}", file=sys.stderr)
        return 1

    indent = None if args.compact else 2
    print(json.dumps(report.to_dict(), indent=indent, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
