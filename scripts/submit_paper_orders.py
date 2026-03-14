#!/usr/bin/env python
"""Submit approved paper orders for a registered strategy."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence

from trading_platform.core.logging import configure_logging
from trading_platform.core.settings import load_settings
from trading_platform.services.paper_execution import resolve_submission_session, run_paper_order_submission


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scripts/submit_paper_orders.py")
    parser.add_argument("--strategy", default="trend_following_daily")
    parser.add_argument(
        "--as-of",
        metavar="YYYY-MM-DD",
        help="Session date whose approved risk decisions should be submitted.",
    )
    parser.add_argument("--risk-run-id", help="Explicit succeeded risk_evaluation run ID to consume.")
    parser.add_argument("--compact", action="store_true", default=False)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings()
    configure_logging(settings.logging)
    logger = logging.getLogger("trading_platform.paper_execution.cli")

    try:
        as_of_session = resolve_submission_session(
            settings=settings,
            as_of_arg=args.as_of,
        )
        report = run_paper_order_submission(
            args.strategy,
            as_of_session=as_of_session,
            risk_run_id=args.risk_run_id,
            trigger_source="paper_orders_script",
            settings=settings,
        )
    except Exception as exc:
        logger.exception(
            "paper_execution_cli_failed",
            extra={"context": {"strategy_id": args.strategy, "error": str(exc)}},
        )
        print(f"paper order submission failed for {args.strategy}: {exc}", file=sys.stderr)
        return 1

    indent = None if args.compact else 2
    print(json.dumps(report.to_dict(), indent=indent, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
