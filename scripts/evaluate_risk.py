#!/usr/bin/env python
"""Run the persisted signal-to-risk evaluation flow for a registered strategy."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence

from trading_platform.core.logging import configure_logging
from trading_platform.core.settings import load_settings
from trading_platform.services.risk import resolve_evaluation_session, run_risk_evaluation


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scripts/evaluate_risk.py")
    parser.add_argument("--strategy", default="trend_following_daily")
    parser.add_argument(
        "--as-of",
        metavar="YYYY-MM-DD",
        help="Session date to evaluate. Defaults to the latest completed persisted session.",
    )
    parser.add_argument("--compact", action="store_true", default=False)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings()
    configure_logging(settings.logging)
    logger = logging.getLogger("trading_platform.risk.cli")

    try:
        as_of_session = resolve_evaluation_session(
            settings=settings,
            as_of_arg=args.as_of,
        )
        report = run_risk_evaluation(
            args.strategy,
            as_of_session=as_of_session,
            trigger_source="risk_script",
            settings=settings,
        )
    except Exception as exc:
        logger.exception(
            "risk_cli_failed",
            extra={"context": {"strategy_id": args.strategy, "error": str(exc)}},
        )
        print(f"risk evaluation failed for {args.strategy}: {exc}", file=sys.stderr)
        return 1

    indent = None if args.compact else 2
    print(json.dumps(report.to_dict(), indent=indent, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
