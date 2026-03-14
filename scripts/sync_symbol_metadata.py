#!/usr/bin/env python
"""CLI script to refresh symbol metadata from the Polygon ticker overview endpoint.

Usage
-----
    PYTHONPATH=src python scripts/sync_symbol_metadata.py
    PYTHONPATH=src python scripts/sync_symbol_metadata.py --symbols AAPL MSFT
    PYTHONPATH=src python scripts/sync_symbol_metadata.py --dry-run

The script calls the Polygon ticker-overview endpoint for each symbol in the
configured universe and upserts the result into the symbols table.

Environment
-----------
    TRADING_PLATFORM_MARKET_DATA__POLYGON__API_KEY  Required for live calls.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

# Ensure src/ is on the path when run directly.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trading_platform.core.logging import configure_logging
from trading_platform.core.settings import load_settings
from trading_platform.db.models.symbol import Symbol
from trading_platform.db.session import session_scope


# ---------------------------------------------------------------------------
# Polygon ticker-overview fetcher (thin, no separate class needed here)
# ---------------------------------------------------------------------------


def _fetch_ticker_overview(ticker: str, settings: Any) -> dict[str, Any] | None:
    """Fetch Polygon ticker overview for a single symbol.

    Returns the result dict or None on 404/no-data.
    Raises on auth errors or unexpected failures.
    """
    import httpx

    from trading_platform.services.polygon import PolygonAuthError

    base_url = settings.market_data.polygon.base_url
    api_key = settings.market_data.polygon.api_key
    timeout = settings.market_data.polygon.timeout_seconds

    if not api_key:
        raise PolygonAuthError(
            "Polygon API key is not configured. "
            "Set TRADING_PLATFORM_MARKET_DATA__POLYGON__API_KEY in .env or the shell."
        )

    url = f"{base_url}/v3/reference/tickers/{ticker}"
    headers = {"Authorization": f"Bearer {api_key}"}

    try:
        response = httpx.get(url, headers=headers, timeout=timeout)
    except httpx.TransportError as exc:
        raise RuntimeError(f"Network error fetching {ticker}: {exc}") from exc

    if response.status_code == 401:
        raise PolygonAuthError(f"Polygon returned 401 Unauthorized for {ticker}.")
    if response.status_code == 404:
        return None
    response.raise_for_status()

    payload = response.json()
    return payload.get("results")


def _parse_list_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _upsert_symbol_metadata(
    session: Any,
    ticker: str,
    overview: dict[str, Any],
) -> Symbol:
    """Upsert symbol metadata from a Polygon ticker overview result dict."""
    from sqlalchemy import select
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    existing = session.execute(
        select(Symbol).where(Symbol.ticker == ticker)
    ).scalar_one_or_none()

    now = datetime.now(UTC)
    values: dict[str, Any] = {
        "ticker": ticker,
        "name": overview.get("name"),
        "market": overview.get("market"),
        "locale": overview.get("locale"),
        "primary_exchange": overview.get("primary_exchange"),
        "symbol_type": overview.get("type"),
        "active": overview.get("active", True),
        "description": overview.get("description"),
        "list_date": _parse_list_date(overview.get("list_date")),
        "currency_name": overview.get("currency_name"),
        "cik": overview.get("cik"),
        "composite_figi": overview.get("composite_figi"),
        "share_class_figi": overview.get("share_class_figi"),
        "metadata_provider": "polygon",
        "updated_at": now,
    }

    if existing is None:
        values["id"] = uuid.uuid4()
        values["created_at"] = now
        sym = Symbol(**values)
        session.add(sym)
        session.flush()
        return sym
    else:
        for k, v in values.items():
            if k not in ("id", "created_at"):
                setattr(existing, k, v)
        session.flush()
        return existing


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@dataclass
class MetadataSyncResult:
    synced: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    dry_run: bool = False

    @property
    def succeeded(self) -> bool:
        return len(self.failed) == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "synced": self.synced,
            "skipped": self.skipped,
            "failed": self.failed,
            "dry_run": self.dry_run,
            "succeeded": self.succeeded,
        }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sync_symbol_metadata",
        description="Refresh symbol metadata from the Polygon ticker-overview endpoint.",
    )
    parser.add_argument(
        "--symbols",
        nargs="+",
        metavar="TICKER",
        help="Override the configured universe with a custom symbol list.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Fetch metadata and print results without writing to the database.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        default=False,
        help="Suppress JSON output (useful in automation).",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    settings = load_settings()
    configure_logging(settings.logging)
    logger = logging.getLogger("trading_platform.sync_metadata")

    symbols: list[str] = args.symbols or list(settings.market_data.metadata.universe)
    result = MetadataSyncResult(dry_run=args.dry_run)

    logger.info(
        "metadata_sync_started",
        extra={
            "context": {
                "symbols": symbols,
                "dry_run": args.dry_run,
            }
        },
    )

    for ticker in symbols:
        try:
            overview = _fetch_ticker_overview(ticker, settings)
            if overview is None:
                logger.warning(
                    "metadata_not_found",
                    extra={"context": {"ticker": ticker}},
                )
                result.skipped.append(ticker)
                continue

            if args.dry_run:
                logger.info(
                    "metadata_dry_run",
                    extra={"context": {"ticker": ticker, "name": overview.get("name")}},
                )
                result.synced.append(ticker)
                continue

            with session_scope(settings) as db_session:
                _upsert_symbol_metadata(db_session, ticker, overview)

            result.synced.append(ticker)
            logger.info(
                "metadata_synced",
                extra={"context": {"ticker": ticker}},
            )

        except Exception as exc:
            logger.error(
                "metadata_sync_failed",
                extra={"context": {"ticker": ticker, "error": str(exc)}},
            )
            result.failed.append(ticker)

    logger.info(
        "metadata_sync_completed",
        extra={"context": result.to_dict()},
    )

    if not args.quiet:
        print(json.dumps(result.to_dict(), default=str))

    if not result.succeeded:
        sys.exit(1)


if __name__ == "__main__":
    main()
