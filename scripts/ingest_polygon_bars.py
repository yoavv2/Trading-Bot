#!/usr/bin/env python
"""CLI for historical Polygon daily-bar ingestion.

Usage
-----
    PYTHONPATH=src python scripts/ingest_polygon_bars.py \\
        --from-date 2024-01-01 --to-date 2024-12-31

The script uses the platform settings for provider config and the default
ingest universe unless overridden with --symbols.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

# Ensure src/ is on the path when run directly.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trading_platform.core.logging import configure_logging
from trading_platform.core.settings import load_settings
from trading_platform.services.ingestion import ingest_daily_bars


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ingest_polygon_bars",
        description="Ingest historical Polygon daily bars into PostgreSQL.",
    )
    parser.add_argument(
        "--from-date",
        metavar="YYYY-MM-DD",
        help="Start of the ingest window (inclusive). Defaults to lookback from today.",
    )
    parser.add_argument(
        "--to-date",
        metavar="YYYY-MM-DD",
        help="End of the ingest window (inclusive). Defaults to yesterday.",
    )
    parser.add_argument(
        "--symbols",
        nargs="+",
        metavar="TICKER",
        help="Override the configured universe with a custom symbol list.",
    )
    parser.add_argument(
        "--trigger-source",
        default="cli",
        help="Label recorded in the ingestion run (default: cli).",
    )
    return parser


def _parse_date(raw: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise SystemExit(f"Invalid date '{raw}': expected YYYY-MM-DD") from exc


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    settings = load_settings()
    configure_logging(settings.logging)
    logger = logging.getLogger("trading_platform.ingest")

    yesterday = date.today() - timedelta(days=1)
    to_date = _parse_date(args.to_date) if args.to_date else yesterday
    if args.from_date:
        from_date = _parse_date(args.from_date)
    else:
        from_date = to_date - timedelta(days=settings.market_data.ingest.default_lookback_days)

    symbols: list[str] = args.symbols or list(settings.market_data.ingest.universe)

    logger.info(
        "ingest_started",
        extra={
            "context": {
                "from_date": from_date.isoformat(),
                "to_date": to_date.isoformat(),
                "symbols": symbols,
                "trigger_source": args.trigger_source,
            }
        },
    )

    result = ingest_daily_bars(
        from_date=from_date,
        to_date=to_date,
        symbols=symbols,
        settings=settings.market_data,
        trigger_source=args.trigger_source,
    )

    summary = {
        "provider": result.provider,
        "from_date": result.from_date.isoformat(),
        "to_date": result.to_date.isoformat(),
        "symbols_requested": result.symbol_count,
        "bars_upserted": result.bars_upserted,
        "failed_symbols": result.symbols_failed,
        "succeeded": result.succeeded,
    }
    print(json.dumps(summary, default=str))

    if not result.succeeded:
        sys.exit(1)


if __name__ == "__main__":
    main()
