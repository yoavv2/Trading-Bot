"""Execute the Phase 1 dry bootstrap flow for a registered strategy."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence

from trading_platform.core.logging import configure_logging
from trading_platform.core.settings import load_settings
from trading_platform.services.bootstrap import run_dry_bootstrap


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scripts/dry_run.py")
    parser.add_argument("--strategy", default="trend_following_daily")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings()
    configure_logging(settings.logging)
    logger = logging.getLogger("trading_platform.dry_run.cli")

    try:
        report = run_dry_bootstrap(
            args.strategy,
            trigger_source="dry_run_script",
            settings=settings,
        )
    except Exception as exc:
        logger.exception(
            "dry_run_cli_failed",
            extra={"context": {"strategy_id": args.strategy, "error": str(exc)}},
        )
        print(f"dry-run failed for {args.strategy}: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report.to_dict(), default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
