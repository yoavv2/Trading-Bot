"""Worker CLI for placeholder service, dry-run scaffolding, and market-data ingestion."""

from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import UTC, date, datetime, timedelta

from trading_platform.core.logging import configure_logging
from trading_platform.core.settings import get_strategy_config, load_settings
from trading_platform.services.bootstrap import run_dry_bootstrap as run_persisted_dry_bootstrap


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trading-platform-worker")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve", help="Run the placeholder worker loop.")
    serve_parser.add_argument("--interval-seconds", type=int, default=30)

    dry_run_parser = subparsers.add_parser("dry-run", help="Exercise config and strategy bootstrap.")
    dry_run_parser.add_argument("--strategy", default="trend_following_daily")

    ingest_parser = subparsers.add_parser("ingest-bars", help="Ingest historical Polygon daily bars.")
    ingest_parser.add_argument("--from-date", metavar="YYYY-MM-DD", help="Ingest window start (inclusive).")
    ingest_parser.add_argument("--to-date", metavar="YYYY-MM-DD", help="Ingest window end (inclusive).")
    ingest_parser.add_argument("--symbols", nargs="+", metavar="TICKER", help="Symbol override list.")
    ingest_parser.add_argument("--trigger-source", default="worker_cli", help="Trigger label for the run record.")

    return parser


def run_placeholder_worker(interval_seconds: int) -> None:
    settings = load_settings()
    configure_logging(settings.logging)
    logger = logging.getLogger("trading_platform.worker")
    logger.info(
        "worker_started",
        extra={
            "context": {
                "interval_seconds": interval_seconds,
                "environment": settings.app.environment,
            }
        },
    )

    try:
        while True:
            logger.info(
                "worker_heartbeat",
                extra={"context": {"timestamp": datetime.now(UTC).isoformat()}},
            )
            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        logger.info("worker_stopped")


def run_dry_bootstrap(strategy_id: str) -> None:
    settings = load_settings()
    configure_logging(settings.logging)
    logger = logging.getLogger("trading_platform.worker")
    strategy = get_strategy_config(settings, strategy_id)
    report = run_persisted_dry_bootstrap(
        strategy.strategy_id,
        trigger_source="worker_cli",
        settings=settings,
    )
    logger.info(
        "worker_dry_run_completed",
        extra={"context": {"run_id": report.run_id, "strategy_id": report.strategy_id}},
    )
    print(json.dumps(report.to_dict(), default=str))


def run_ingest_bars(args: argparse.Namespace) -> None:
    from trading_platform.services.ingestion import ingest_daily_bars

    settings = load_settings()
    configure_logging(settings.logging)
    logger = logging.getLogger("trading_platform.worker")

    yesterday = date.today() - timedelta(days=1)
    to_date = date.fromisoformat(args.to_date) if args.to_date else yesterday
    from_date = (
        date.fromisoformat(args.from_date)
        if args.from_date
        else to_date - timedelta(days=settings.market_data.ingest.default_lookback_days)
    )
    symbols: list[str] = args.symbols or list(settings.market_data.ingest.universe)

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
    logger.info("ingest_bars_completed", extra={"context": summary})
    print(json.dumps(summary, default=str))


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "serve":
        run_placeholder_worker(args.interval_seconds)
        return
    if args.command == "dry-run":
        run_dry_bootstrap(args.strategy)
        return
    if args.command == "ingest-bars":
        run_ingest_bars(args)
        return

    parser.error(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
