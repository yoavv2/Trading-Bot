#!/usr/bin/env python
"""Render the persisted operator control and health summary."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence

from trading_platform.core.logging import configure_logging
from trading_platform.core.settings import load_settings
from trading_platform.services.operator_status import (
    build_operator_status_report,
    render_operator_status_report,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scripts/operator_status.py")
    parser.add_argument("--strategy", default="trend_following_daily")
    parser.add_argument("--inspection-limit", type=int, default=5)
    parser.add_argument("--summary-format", choices=("markdown", "json"), default="markdown")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings()
    configure_logging(settings.logging)
    logger = logging.getLogger("trading_platform.operator_status.cli")

    try:
        report = build_operator_status_report(
            strategy_id=args.strategy,
            inspection_limit=args.inspection_limit,
            settings=settings,
        )
    except Exception as exc:
        logger.exception(
            "operator_status_failed",
            extra={
                "context": {
                    "strategy_id": args.strategy,
                    "inspection_limit": args.inspection_limit,
                    "error": str(exc),
                }
            },
        )
        print(f"operator status failed: {exc}", file=sys.stderr)
        return 1

    print(render_operator_status_report(report, summary_format=args.summary_format))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
