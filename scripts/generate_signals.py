#!/usr/bin/env python
"""Generate strategy signals for a given session date.

Usage examples:

    # Evaluate the default strategy for the most recent completed session
    PYTHONPATH=src python scripts/generate_signals.py

    # Evaluate for a specific as-of date
    PYTHONPATH=src python scripts/generate_signals.py --as-of 2024-01-15

    # Override the strategy
    PYTHONPATH=src python scripts/generate_signals.py --strategy trend_following_daily --as-of 2024-01-15

    # Print compact JSON (no pretty-print)
    PYTHONPATH=src python scripts/generate_signals.py --compact

Output is a JSON object conforming to SignalBatch.to_dict() schema.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

# Ensure src/ is on the path when invoked directly
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trading_platform.core.logging import configure_logging
from trading_platform.core.settings import load_settings
from trading_platform.db.session import session_scope
from trading_platform.services.market_data_access import latest_completed_session
from trading_platform.strategies.registry import build_default_registry


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="generate_signals",
        description="Evaluate a registered strategy and emit deterministic signals for a session.",
    )
    parser.add_argument(
        "--strategy",
        default="trend_following_daily",
        metavar="STRATEGY_ID",
        help="Registered strategy ID to evaluate (default: trend_following_daily).",
    )
    parser.add_argument(
        "--as-of",
        metavar="YYYY-MM-DD",
        default=None,
        help=(
            "Session date to evaluate as of. "
            "Defaults to the latest completed session with persisted bars. "
            "If no bars are persisted yet, defaults to yesterday."
        ),
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        default=False,
        help="Emit compact JSON (no indentation).",
    )
    return parser


def resolve_as_of(as_of_arg: str | None, db_session, exchange: str) -> date:
    """Return the target session date for signal evaluation."""
    if as_of_arg is not None:
        return date.fromisoformat(as_of_arg)

    latest = latest_completed_session(db_session, exchange=exchange)
    if latest is not None:
        return latest

    # Fall back to yesterday when no bars have been ingested yet
    return date.today() - timedelta(days=1)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    settings = load_settings()
    configure_logging(settings.logging)

    registry = build_default_registry(settings)
    strategy = registry.resolve(args.strategy)

    exchange = settings.market_data.calendar.exchange

    with session_scope(settings) as db_session:
        as_of = resolve_as_of(args.as_of, db_session, exchange)
        batch = strategy.generate_signals(db_session, as_of)

    indent = None if args.compact else 2
    print(json.dumps(batch.to_dict(), indent=indent, default=str))


if __name__ == "__main__":
    main()
