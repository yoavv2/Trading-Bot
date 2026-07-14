"""Worker CLI handlers: `ingest-bars`, `sync-metadata`, `sync-sessions` (STRUCT-03)."""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta

from trading_platform.core.logging import configure_logging, get_logger
from trading_platform.core.startup import enforce_startup_config
from trading_platform.services.config.validation import ExecutionMode


def run_ingest_bars(args: argparse.Namespace) -> None:
    from trading_platform.services.ingestion import ingest_daily_bars

    settings = enforce_startup_config(mode=ExecutionMode.BACKTEST)
    configure_logging(settings.logging)
    logger = get_logger("trading_platform.worker")

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

    # sync-metadata's --dry-run flag deliberately never writes to the DB
    # (see the loop below), so the startup gate doesn't require reachability
    # for a dry-run invocation.
    settings = enforce_startup_config(mode=ExecutionMode.BACKTEST, require_database=not args.dry_run)
    configure_logging(settings.logging)
    logger = get_logger("trading_platform.worker")

    # Import the standalone sync logic from the scripts module
    import sys
    from pathlib import Path

    # NOTE: parents[5] here reproduces the exact (pre-existing, buggy) resolved
    # path from the pre-split worker/__main__.py's parents[4] — that literal
    # resolved one level ABOVE the project root (no `scripts/` dir there), so
    # a real (non---dry-run) sync-metadata invocation's scripts import was
    # already broken before this move. Preserved verbatim (not fixed) per the
    # zero-behavior-change contract; logged as a pre-existing bug in the plan
    # SUMMARY / deferred-items.md rather than silently repaired here.
    sys.path.insert(0, str(Path(__file__).resolve().parents[5] / "scripts"))

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

    settings = enforce_startup_config(mode=ExecutionMode.BACKTEST)
    configure_logging(settings.logging)
    logger = get_logger("trading_platform.worker")

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
