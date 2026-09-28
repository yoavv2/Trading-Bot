"""Symbol-metadata sync service (ORCH-02).

Moved from ``scripts/sync_symbol_metadata.py`` so the ``sync-symbol-metadata``
Job handler (a Phase 20 addition) can call a plain ``services.*`` function
instead of a CLI script (``JobHandler`` may only import ``services.*``,
``jobs/contracts.py``). A Job is never a preview-only run (OPS-05 forbids
behavior flags), so ``MetadataSyncResult`` carries no preview-mode field --
there is no code path here that fetches without also persisting.

``fetch_ticker_overview``/``PolygonAuthError``/``httpx`` are imported at
module level (the retired script imported them function-locally); the
``importlib``/``sys.path`` hack ``worker/commands/ingest.py::run_sync_metadata``
used to reach the script no longer has anything to reach into.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_platform.core.settings import Settings
from trading_platform.db.models.symbol import Symbol
from trading_platform.db.session import session_scope
from trading_platform.services.polygon import PolygonAuthError

logger = logging.getLogger(__name__)


def fetch_ticker_overview(ticker: str, settings: Settings) -> dict[str, Any] | None:
    """Fetch the Polygon ticker overview for a single symbol.

    Returns the result dict, or ``None`` on a 404/no-data response. Raises
    ``PolygonAuthError`` on an auth failure or an unexpected/network error.
    """

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


def upsert_symbol_metadata(session: Session, ticker: str, overview: dict[str, Any]) -> Symbol:
    """Upsert symbol metadata from a Polygon ticker-overview result dict."""

    existing = session.execute(select(Symbol).where(Symbol.ticker == ticker)).scalar_one_or_none()

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
        symbol = Symbol(**values)
        session.add(symbol)
        session.flush()
        return symbol

    for key, value in values.items():
        if key not in ("id", "created_at"):
            setattr(existing, key, value)
    session.flush()
    return existing


class SymbolMetadataSyncFailedError(RuntimeError):
    """Raised by ``MetadataSyncResult.raise_for_failures()`` when one or more
    tickers failed to sync (preserves the retired CLI's exit-1-on-any-failure
    semantics as an explicit domain decision)."""

    def __init__(self, failed: tuple[str, ...]) -> None:
        self.failed = failed
        super().__init__(f"Symbol metadata sync failed for: {', '.join(failed)}")


@dataclass
class MetadataSyncResult:
    """Outcome of one ``sync_symbol_metadata`` call. Carries no preview-mode
    field -- a Job is never a preview-only run (OPS-05)."""

    synced: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return len(self.failed) == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "synced": self.synced,
            "skipped": self.skipped,
            "failed": self.failed,
            "synced_count": len(self.synced),
            "skipped_count": len(self.skipped),
            "failed_count": len(self.failed),
            "succeeded": self.succeeded,
        }

    def raise_for_failures(self) -> None:
        if self.failed:
            raise SymbolMetadataSyncFailedError(tuple(self.failed))


def sync_symbol_metadata(symbols: list[str] | tuple[str, ...], *, settings: Settings) -> MetadataSyncResult:
    """Fetch and upsert Polygon ticker-overview metadata for ``symbols``.

    Reproduces the retired ``scripts/sync_symbol_metadata.py::main()`` loop
    exactly (fetch -> ``None`` result is skipped; a successful fetch upserts
    inside its own ``session_scope``; any exception marks the ticker failed)
    but never prints or exits -- callers (a Job handler, a future CLI) decide
    what to do with the returned ``MetadataSyncResult``.
    """

    result = MetadataSyncResult()

    logger.info(
        "metadata_sync_started",
        extra={"context": {"symbols": list(symbols)}},
    )

    for ticker in symbols:
        try:
            overview = fetch_ticker_overview(ticker, settings)
            if overview is None:
                logger.warning(
                    "metadata_not_found",
                    extra={"context": {"ticker": ticker}},
                )
                result.skipped.append(ticker)
                continue

            with session_scope(settings) as db_session:
                upsert_symbol_metadata(db_session, ticker, overview)

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

    return result


__all__ = [
    "MetadataSyncResult",
    "SymbolMetadataSyncFailedError",
    "fetch_ticker_overview",
    "sync_symbol_metadata",
    "upsert_symbol_metadata",
]
