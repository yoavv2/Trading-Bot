"""Asset catalog: Tiingo's public ticker list plus public name directories.

Sources (all public, keyless; observed 2026-10-07):
* Tiingo ``supported_tickers.zip`` (CSV columns ``ticker, exchange, assetType,
  priceCurrency, startDate, endDate``; no names);
* Nasdaq Trader ``nasdaqtraded.txt`` (pipe-delimited symbol directory with
  ``Security Name`` for every exchange-listed security; class shares as ``BRK.B``);
* SEC EDGAR ``company_tickers.json`` (SEC registrants: ``ticker`` + ``title``).

Catalog scope: US ``Stock`` and ``ETF`` in USD on NYSE, NASDAQ, NYSE ARCA, BATS and
AMEX. Names are joined at sync time and recorded with their ``name_source``; Tiingo
metadata fills gaps on view (``enrich_asset_name``). Name search covers populated names
only and callers must say so (``name_coverage`` gives the numbers). Catalog presence is
never proof of usable coverage: readiness checks the actual bars.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

import httpx
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_platform.db.models.research import AssetCatalogEntry
from trading_platform.services.tiingo import PROVIDER, TiingoClient

logger = logging.getLogger(__name__)

TIINGO_SUPPORTED_TICKERS_URL = "https://apimedia.tiingo.com/docs/tiingo/daily/supported_tickers.zip"
NASDAQ_TRADED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqtraded.txt"
SEC_COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"

MAJOR_EXCHANGES = frozenset({"NYSE", "NASDAQ", "NYSE ARCA", "BATS", "AMEX"})
ASSET_TYPES = frozenset({"Stock", "ETF"})
CURRENCY = "USD"

NAME_SOURCE_NASDAQ = "nasdaq_trader"
NAME_SOURCE_SEC = "sec_edgar"
NAME_SOURCE_TIINGO = "tiingo_metadata"


def normalize_ticker(raw: str) -> str:
    """Join key across sources: upper-case, class separator ``.`` becomes ``-``."""

    return raw.strip().upper().replace(".", "-")


@dataclass(frozen=True)
class CatalogSources:
    tiingo_csv_text: str
    nasdaq_text: str | None = None
    sec_json: dict[str, Any] | None = None


@dataclass
class CatalogSyncReport:
    provider: str
    rows_total: int = 0
    rows_named: int = 0
    named_by_source: dict[str, int] = field(default_factory=dict)
    synced_at: datetime | None = None
    #: Source rows that shared a ticker with an earlier row after normalisation (``BRK.B``
    #: and ``BRK-B``, or one ticker on two exchanges); one row per ticker is kept, the one
    #: with the latest ``catalog_end`` (then the earliest ``catalog_start``).
    duplicate_tickers: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "rows_total": self.rows_total,
            "rows_named": self.rows_named,
            "duplicate_tickers": self.duplicate_tickers,
            "named_by_source": dict(self.named_by_source),
            "synced_at": self.synced_at.isoformat() if self.synced_at else None,
            "name_search_coverage": "populated names only",
        }


# ---------------------------------------------------------------------------
# Fetch (public downloads; no key, no Tiingo API budget)
# ---------------------------------------------------------------------------


def fetch_catalog_sources(
    client: httpx.Client, *, user_agent: str, include_names: bool = True
) -> CatalogSources:
    tiingo_zip = client.get(TIINGO_SUPPORTED_TICKERS_URL, headers={"User-Agent": user_agent})
    tiingo_zip.raise_for_status()
    archive = zipfile.ZipFile(io.BytesIO(tiingo_zip.content))
    member = archive.namelist()[0]
    tiingo_csv_text = archive.read(member).decode("utf-8")
    nasdaq_text: str | None = None
    sec_json: dict[str, Any] | None = None
    if include_names:
        try:
            response = client.get(NASDAQ_TRADED_URL, headers={"User-Agent": user_agent})
            response.raise_for_status()
            nasdaq_text = response.text
        except httpx.HTTPError as exc:
            logger.warning("catalog_nasdaq_names_unavailable", extra={"context": {"error": str(exc)}})
        try:
            response = client.get(SEC_COMPANY_TICKERS_URL, headers={"User-Agent": user_agent})
            response.raise_for_status()
            sec_json = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("catalog_sec_names_unavailable", extra={"context": {"error": str(exc)}})
    return CatalogSources(tiingo_csv_text=tiingo_csv_text, nasdaq_text=nasdaq_text, sec_json=sec_json)


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------


def _optional_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value[:10])


def parse_tiingo_supported(csv_text: str) -> list[dict[str, Any]]:
    """Rows in scope: US Stock/ETF in USD on the major exchanges."""

    rows: list[dict[str, Any]] = []
    for raw in csv.DictReader(io.StringIO(csv_text)):
        exchange = (raw.get("exchange") or "").strip()
        asset_type = (raw.get("assetType") or "").strip()
        currency = (raw.get("priceCurrency") or "").strip()
        ticker = (raw.get("ticker") or "").strip()
        if not ticker or exchange not in MAJOR_EXCHANGES or asset_type not in ASSET_TYPES:
            continue
        if currency != CURRENCY:
            continue
        rows.append(
            {
                "ticker": normalize_ticker(ticker),
                "exchange": exchange,
                "asset_type": asset_type,
                "currency": currency,
                "catalog_start": _optional_date(raw.get("startDate")),
                "catalog_end": _optional_date(raw.get("endDate")),
            }
        )
    return rows


def parse_nasdaq_names(text: str) -> dict[str, str]:
    lines = [line for line in text.splitlines() if line and not line.startswith("File Creation Time")]
    names: dict[str, str] = {}
    for raw in csv.DictReader(io.StringIO("\n".join(lines)), delimiter="|"):
        symbol = (raw.get("Symbol") or "").strip()
        name = (raw.get("Security Name") or "").strip()
        if not symbol or not name or (raw.get("Test Issue") or "").strip() == "Y":
            continue
        names[normalize_ticker(symbol)] = name
    return names


def parse_sec_names(payload: dict[str, Any]) -> dict[str, str]:
    names: dict[str, str] = {}
    for entry in payload.values():
        if not isinstance(entry, dict):
            continue
        ticker = str(entry.get("ticker") or "").strip()
        title = str(entry.get("title") or "").strip()
        if ticker and title:
            names[normalize_ticker(ticker)] = title
    return names


# ---------------------------------------------------------------------------
# Sync and queries
# ---------------------------------------------------------------------------


def _dedupe_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """One row per normalised ticker. The public list repeats a ticker (one listing per
    exchange, or ``BRK.B`` next to ``BRK-B``); a single INSERT ... ON CONFLICT cannot touch
    the same row twice, so the sync keeps the row with the latest ``catalog_end`` and, on a
    tie, the earliest ``catalog_start``. Returns the kept rows and the dropped count."""

    chosen: dict[str, dict[str, Any]] = {}
    dropped = 0
    for row in rows:
        ticker = row["ticker"]
        current = chosen.get(ticker)
        if current is None:
            chosen[ticker] = row
            continue
        dropped += 1
        key_new = (row.get("catalog_end") or date.min, -(row.get("catalog_start") or date.max).toordinal())
        key_old = (current.get("catalog_end") or date.min, -(current.get("catalog_start") or date.max).toordinal())
        if key_new > key_old:
            chosen[ticker] = row
    return list(chosen.values()), dropped


def sync_asset_catalog(
    session: Session, sources: CatalogSources, *, now: datetime | None = None
) -> CatalogSyncReport:
    synced_at = now or datetime.now(UTC)
    rows, duplicates = _dedupe_rows(parse_tiingo_supported(sources.tiingo_csv_text))
    nasdaq = parse_nasdaq_names(sources.nasdaq_text) if sources.nasdaq_text else {}
    sec = parse_sec_names(sources.sec_json) if sources.sec_json else {}
    report = CatalogSyncReport(provider=PROVIDER, synced_at=synced_at, duplicate_tickers=duplicates)

    existing_names = {
        ticker: (name, source)
        for ticker, name, source in session.execute(
            select(AssetCatalogEntry.ticker, AssetCatalogEntry.name, AssetCatalogEntry.name_source).where(
                AssetCatalogEntry.provider == PROVIDER
            )
        ).all()
    }

    values: list[dict[str, Any]] = []
    for row in rows:
        ticker = row["ticker"]
        name: str | None
        source: str | None
        if ticker in nasdaq:
            name, source = nasdaq[ticker], NAME_SOURCE_NASDAQ
        elif ticker in sec:
            name, source = sec[ticker], NAME_SOURCE_SEC
        else:
            prior = existing_names.get(ticker)
            name, source = (prior[0], prior[1]) if prior and prior[0] else (None, None)
        if name:
            report.rows_named += 1
            report.named_by_source[source or "unknown"] = report.named_by_source.get(source or "unknown", 0) + 1
        values.append(
            {
                "id": uuid.uuid4(),
                "provider": PROVIDER,
                "name": name,
                "name_source": source,
                "synced_at": synced_at,
                **row,
            }
        )
    report.rows_total = len(values)
    if not values:
        return report

    for start in range(0, len(values), 2000):
        chunk = values[start : start + 2000]
        stmt = pg_insert(AssetCatalogEntry).values(chunk)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_asset_catalog_provider_ticker",
            set_={
                "exchange": stmt.excluded.exchange,
                "asset_type": stmt.excluded.asset_type,
                "currency": stmt.excluded.currency,
                "catalog_start": stmt.excluded.catalog_start,
                "catalog_end": stmt.excluded.catalog_end,
                "name": stmt.excluded.name,
                "name_source": stmt.excluded.name_source,
                "synced_at": stmt.excluded.synced_at,
                "updated_at": synced_at,
            },
        )
        session.execute(stmt)
    session.flush()
    return report


def name_coverage(session: Session, *, provider: str = PROVIDER) -> tuple[int, int]:
    """``(named, total)`` catalog rows: the number name search can see versus all rows."""

    total = session.execute(
        select(func.count()).select_from(AssetCatalogEntry).where(AssetCatalogEntry.provider == provider)
    ).scalar_one()
    named = session.execute(
        select(func.count())
        .select_from(AssetCatalogEntry)
        .where(AssetCatalogEntry.provider == provider, AssetCatalogEntry.name.is_not(None))
    ).scalar_one()
    return int(named), int(total)


def search_assets(
    session: Session, query: str, *, limit: int = 20, provider: str = PROVIDER
) -> list[AssetCatalogEntry]:
    """Ticker prefix or name substring over the local catalog (populated names only)."""

    needle = query.strip()
    if not needle:
        return []
    ticker_prefix = normalize_ticker(needle) + "%"
    name_like = f"%{needle}%"
    stmt = (
        select(AssetCatalogEntry)
        .where(AssetCatalogEntry.provider == provider)
        .where(
            or_(
                AssetCatalogEntry.ticker.like(ticker_prefix),
                AssetCatalogEntry.name.ilike(name_like),
            )
        )
        .order_by(AssetCatalogEntry.ticker.asc())
        .limit(limit)
    )
    return list(session.execute(stmt).scalars().all())


def get_asset(session: Session, ticker: str, *, provider: str = PROVIDER) -> AssetCatalogEntry | None:
    return session.execute(
        select(AssetCatalogEntry).where(
            AssetCatalogEntry.provider == provider,
            AssetCatalogEntry.ticker == normalize_ticker(ticker),
        )
    ).scalar_one_or_none()


def enrich_asset_name(
    session: Session, client: TiingoClient, ticker: str, *, now: datetime | None = None
) -> AssetCatalogEntry | None:
    """Fill a missing name from Tiingo metadata (one budgeted request).

    No-op when the row is already named or when a fetch was already attempted
    (``names_fetched_at`` set): a provider that returns no name is asked once, never
    on every view.
    """

    entry = get_asset(session, ticker)
    if entry is None or entry.name or entry.names_fetched_at is not None:
        return entry
    metadata = client.fetch_metadata(entry.ticker)
    entry.names_fetched_at = now or datetime.now(UTC)
    if metadata.name:
        entry.name = metadata.name
        entry.name_source = NAME_SOURCE_TIINGO
    session.flush()
    return entry


def catalog_summary_json(report: CatalogSyncReport) -> str:
    return json.dumps(report.to_dict(), sort_keys=True)
