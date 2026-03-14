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

    sync_meta_parser = subparsers.add_parser(
        "sync-metadata", help="Refresh symbol metadata from Polygon ticker overview."
    )
    sync_meta_parser.add_argument("--symbols", nargs="+", metavar="TICKER", help="Symbol override list.")
    sync_meta_parser.add_argument(
        "--dry-run", action="store_true", default=False, help="Print metadata without persisting."
    )

    sync_sessions_parser = subparsers.add_parser(
        "sync-sessions", help="Persist XNYS market sessions for a date range."
    )
    sync_sessions_parser.add_argument(
        "--from-date", metavar="YYYY-MM-DD", help="Session sync start (inclusive)."
    )
    sync_sessions_parser.add_argument(
        "--to-date", metavar="YYYY-MM-DD", help="Session sync end (inclusive)."
    )

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


def run_sync_metadata(args: argparse.Namespace) -> None:
    from trading_platform.db.models.symbol import Symbol as SymbolModel
    from trading_platform.db.session import session_scope

    import uuid
    from datetime import UTC, datetime

    settings = load_settings()
    configure_logging(settings.logging)
    logger = logging.getLogger("trading_platform.worker")

    # Import the standalone sync logic from the scripts module
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "scripts"))

    import importlib

    sync_mod = importlib.import_module("sync_symbol_metadata")

    symbols: list[str] = args.symbols or list(settings.market_data.metadata.universe)
    result = sync_mod.MetadataSyncResult(dry_run=args.dry_run)

    for ticker in symbols:
        try:
            overview = sync_mod._fetch_ticker_overview(ticker, settings)
            if overview is None:
                result.skipped.append(ticker)
                continue

            if args.dry_run:
                result.synced.append(ticker)
                continue

            with session_scope(settings) as db_session:
                sync_mod._upsert_symbol_metadata(db_session, ticker, overview)
            result.synced.append(ticker)
        except Exception as exc:
            logger.error(
                "metadata_sync_failed",
                extra={"context": {"ticker": ticker, "error": str(exc)}},
            )
            result.failed.append(ticker)

    print(json.dumps(result.to_dict(), default=str))


def run_sync_sessions(args: argparse.Namespace) -> None:
    from datetime import timedelta

    from trading_platform.db.session import session_scope
    from trading_platform.services.calendar import upsert_market_sessions

    settings = load_settings()
    configure_logging(settings.logging)
    logger = logging.getLogger("trading_platform.worker")

    exchange = settings.market_data.calendar.exchange
    yesterday = date.today() - timedelta(days=1)
    to_date = date.fromisoformat(args.to_date) if args.to_date else yesterday
    from_date = (
        date.fromisoformat(args.from_date)
        if args.from_date
        else to_date - timedelta(days=settings.market_data.ingest.default_lookback_days)
    )

    with session_scope(settings) as db_session:
        count = upsert_market_sessions(db_session, from_date, to_date, exchange)

    summary = {
        "exchange": exchange,
        "from_date": from_date.isoformat(),
        "to_date": to_date.isoformat(),
        "sessions_upserted": count,
    }
    logger.info("sync_sessions_completed", extra={"context": summary})
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
    if args.command == "sync-metadata":
        run_sync_metadata(args)
        return
    if args.command == "sync-sessions":
        run_sync_sessions(args)
        return

    parser.error(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
