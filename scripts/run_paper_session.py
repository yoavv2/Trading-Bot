#!/usr/bin/env python
"""Run the idempotent paper-trading session orchestration for one session."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence

from trading_platform.core.logging import configure_logging
from trading_platform.core.settings import load_settings
from trading_platform.services.paper_execution import resolve_submission_session, run_paper_session


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scripts/run_paper_session.py")
    parser.add_argument("--strategy")
    parser.add_argument(
        "--as-of",
        metavar="YYYY-MM-DD",
        help="Target session date. Defaults to the latest completed persisted session.",
    )
    parser.add_argument("--risk-run-id", help="Explicit succeeded risk_evaluation run ID to consume.")
    parser.add_argument("--compact", action="store_true", default=False)
    parser.add_argument("--trigger-source")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings()
    configure_logging(settings.logging)
    logger = logging.getLogger("trading_platform.paper_execution.runner_cli")

    try:
        as_of_session = resolve_submission_session(
            settings=settings,
            as_of_arg=args.as_of,
        )
        strategy_id = args.strategy or settings.execution.paper_session_runner.default_strategy_id
        trigger_source = args.trigger_source or settings.execution.paper_session_runner.trigger_source
        report = run_paper_session(
            strategy_id,
            as_of_session=as_of_session,
            risk_run_id=args.risk_run_id,
            trigger_source=trigger_source,
            settings=settings,
        )
    except Exception as exc:
        logger.exception(
            "paper_session_runner_failed",
            extra={"context": {"strategy_id": args.strategy, "error": str(exc)}},
        )
        print(f"paper session failed for {args.strategy or 'default strategy'}: {exc}", file=sys.stderr)
        return 1

    indent = None if args.compact else 2
    print(json.dumps(report.to_dict(), indent=indent, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
