#!/usr/bin/env python
"""Enable or disable persisted strategy execution with audit output."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence

from trading_platform.core.logging import configure_logging
from trading_platform.core.settings import load_settings
from trading_platform.services.operator_controls import (
    OperatorControlService,
    render_operator_control_report,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scripts/operator_control.py")
    parser.add_argument("action", choices=("enable", "disable"))
    parser.add_argument("--strategy", default="trend_following_daily")
    parser.add_argument("--reason")
    parser.add_argument("--actor", default="operator_cli")
    parser.add_argument("--trigger-source", default="operator_control_script")
    parser.add_argument("--summary-format", choices=("markdown", "json"), default="json")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings()
    configure_logging(settings.logging)
    logger = logging.getLogger("trading_platform.operator_controls.cli")
    service = OperatorControlService(settings=settings)

    try:
        if args.action == "disable":
            report = service.disable_strategy(
                args.strategy,
                reason=args.reason,
                actor=args.actor,
                trigger_source=args.trigger_source,
            )
        else:
            report = service.enable_strategy(
                args.strategy,
                reason=args.reason,
                actor=args.actor,
                trigger_source=args.trigger_source,
            )
    except Exception as exc:
        logger.exception(
            "operator_control_failed",
            extra={
                "context": {
                    "strategy_id": args.strategy,
                    "action": args.action,
                    "trigger_source": args.trigger_source,
                    "error": str(exc),
                }
            },
        )
        print(f"operator control failed: {exc}", file=sys.stderr)
        return 1

    print(render_operator_control_report(report, summary_format=args.summary_format))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
