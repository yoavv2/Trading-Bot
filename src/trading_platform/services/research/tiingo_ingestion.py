"""Tiingo daily-bar ingestion into the research database.

Writes two ``daily_bars`` rows per asset and session, both tagged ``provider = "tiingo"``:
``adjusted = False`` with the raw OHLCV and ``adjusted = True`` with the CRSP-adjusted
OHLC and the adjusted volume. The corporate-action factors (``split_factor``,
``dividend_cash``) are stored on both rows. One ``market_data_ingestion_runs`` row per
call records the outcome exactly as the trading-path ingestion does (one transaction per
asset; zero succeeded assets is FAILED).

This module never touches ``services/ingestion.py`` beyond reusing its symbol upsert.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_platform.core.settings import Settings
from trading_platform.db.models.daily_bar import DailyBar as DailyBarModel
from trading_platform.db.models.market_data_ingestion_run import MarketDataIngestionRun
from trading_platform.db.models.research import AssetCatalogEntry
from trading_platform.db.models.symbol import Symbol
from trading_platform.db.session import session_scope
from trading_platform.services.data import IngestionResult, IngestionRunStatus
from trading_platform.services.ingestion import upsert_symbol
from trading_platform.services.research.budget import DatabaseRequestBudget
from trading_platform.services.tiingo import (
    PROVIDER,
    TiingoAssetMetadata,
    TiingoAuthError,
    TiingoClient,
    TiingoDailyRow,
    TiingoRateLimitError,
    TiingoRequestBudgetExceededError,
)

logger = logging.getLogger(__name__)

_MARKET_BY_ASSET_TYPE = {"Stock": "stocks", "ETF": "stocks"}
_SYMBOL_TYPE_BY_ASSET_TYPE = {"Stock": "CS", "ETF": "ETF"}

PROVIDER_LEVEL_ERRORS: tuple[type[Exception], ...] = (
    TiingoAuthError,
    TiingoRateLimitError,
    TiingoRequestBudgetExceededError,
)
"""Failures that concern the provider session, not one asset: the run aborts on them."""

PROVIDER_AUTH_FAILED = "provider_auth_failed"


def provider_failure_code(exc: BaseException) -> str:
    """Closed code for a run-level failure: the adapter's code when it has one, else the
    exception class name (never its message, which could carry provider text)."""

    code = getattr(exc, "code", None)
    if isinstance(code, str) and code:
        return code
    if isinstance(exc, TiingoAuthError):
        return PROVIDER_AUTH_FAILED
    return type(exc).__name__


def _rows_for_upsert(symbol_id: uuid.UUID, rows: list[TiingoDailyRow]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        common = {
            "symbol_id": symbol_id,
            "session_date": row.session_date,
            "provider": PROVIDER,
            "provider_timestamp": None,
            "vwap": None,
            "trade_count": None,
            "split_factor": row.split_factor,
            "dividend_cash": row.div_cash,
        }
        out.append(
            {
                "id": uuid.uuid4(),
                "open": row.open,
                "high": row.high,
                "low": row.low,
                "close": row.close,
                "volume": row.volume,
                "adjusted": False,
                **common,
            }
        )
        out.append(
            {
                "id": uuid.uuid4(),
                "open": row.adj_open,
                "high": row.adj_high,
                "low": row.adj_low,
                "close": row.adj_close,
                "volume": row.adj_volume,
                "adjusted": True,
                **common,
            }
        )
    return out


def upsert_tiingo_bars(session: Session, symbol_id: uuid.UUID, rows: list[TiingoDailyRow]) -> int:
    """Idempotent upsert of raw and adjusted rows; returns rows written."""

    if not rows:
        return 0
    values = _rows_for_upsert(symbol_id, rows)
    insert_stmt = pg_insert(DailyBarModel).values(values)
    stmt = insert_stmt.on_conflict_do_update(
        constraint="uq_daily_bars_symbol_session_adjusted_provider",
        set_={
            "open": insert_stmt.excluded.open,
            "high": insert_stmt.excluded.high,
            "low": insert_stmt.excluded.low,
            "close": insert_stmt.excluded.close,
            "volume": insert_stmt.excluded.volume,
            "split_factor": insert_stmt.excluded.split_factor,
            "dividend_cash": insert_stmt.excluded.dividend_cash,
            "updated_at": datetime.now(UTC),
        },
    ).returning(DailyBarModel.id)
    returned = session.execute(stmt).fetchall()
    session.flush()
    return len(returned)


def apply_metadata_to_symbol(
    session: Session, symbol: Symbol, metadata: TiingoAssetMetadata
) -> None:
    """Fill the symbol row from Tiingo metadata plus the catalog's asset type if known."""

    catalog = session.execute(
        select(AssetCatalogEntry).where(
            AssetCatalogEntry.provider == PROVIDER, AssetCatalogEntry.ticker == symbol.ticker
        )
    ).scalar_one_or_none()
    asset_type = catalog.asset_type if catalog is not None else None
    symbol.name = metadata.name or symbol.name
    symbol.primary_exchange = metadata.exchange_code or symbol.primary_exchange
    symbol.list_date = metadata.start_date or symbol.list_date
    symbol.market = _MARKET_BY_ASSET_TYPE.get(asset_type or "", symbol.market)
    symbol.symbol_type = _SYMBOL_TYPE_BY_ASSET_TYPE.get(asset_type or "", symbol.symbol_type)
    symbol.currency_name = (catalog.currency.lower() if catalog and catalog.currency else symbol.currency_name)
    symbol.metadata_provider = PROVIDER
    symbol.active = True


def _derive_status(succeeded: int, failed: int) -> IngestionRunStatus:
    if succeeded == 0:
        return "failed"
    return "partial" if failed else "succeeded"


def _start_run(
    session: Session,
    *,
    from_date: date,
    to_date: date,
    assets: list[str],
    trigger_source: str,
    job_id: uuid.UUID | None,
) -> MarketDataIngestionRun:
    run = MarketDataIngestionRun(
        id=uuid.uuid4(),
        job_id=job_id,
        provider=PROVIDER,
        from_date=from_date,
        to_date=to_date,
        adjusted=True,
        status="running",
        symbols_requested=list(assets),
        symbols_failed=[],
        bars_upserted=0,
        page_count=0,
        trigger_source=trigger_source,
        started_at=datetime.now(UTC),
        request_metadata={"series": ["raw", "adjusted"], "factors": ["split_factor", "dividend_cash"]},
    )
    session.add(run)
    session.flush()
    return run


def _finalize_run(
    settings: Settings,
    run_id: uuid.UUID,
    *,
    status: IngestionRunStatus,
    bars_upserted: int,
    failed: list[str],
    error_message: str | None,
    request_count: int,
) -> None:
    with session_scope(settings) as session:
        run = session.get(MarketDataIngestionRun, run_id)
        if run is None:
            return
        run.status = status
        run.bars_upserted = bars_upserted
        run.symbols_failed = list(failed)
        run.error_message = error_message
        run.page_count = request_count
        run.completed_at = datetime.now(UTC)


def ingest_tiingo_daily_bars(
    *,
    assets: list[str],
    from_date: date,
    to_date: date,
    settings: Settings,
    trigger_source: str = "job",
    job_id: uuid.UUID | None = None,
    client: TiingoClient | None = None,
) -> IngestionResult:
    """Download only the requested assets for the requested range (plus nothing else).

    Per-asset failures (a bad payload, a DB error for one symbol) mark that asset failed
    and the run continues, as the Polygon path does. Provider-level failures (an invalid
    key, HTTP 429, the in-process hourly budget) are ``PROVIDER_LEVEL_ERRORS``: they abort
    the run at once, finalize it FAILED with the closed code as ``error_message`` and
    propagate, so the Job fails visibly instead of landing as a partial success whose
    remaining assets would each burn another request against the exhausted budget.
    """

    # Construct the client first: a missing key refuses here, before any run row exists.
    owned_client = client is None
    resolved_client = client or TiingoClient(
        settings.research.tiingo,
        budget=DatabaseRequestBudget(settings, provider=PROVIDER, job_id=job_id),
    )

    with session_scope(settings) as session:
        run = _start_run(
            session,
            from_date=from_date,
            to_date=to_date,
            assets=assets,
            trigger_source=trigger_source,
            job_id=job_id,
        )
        run_id = run.id

    total = 0
    failed: list[str] = []
    failures: list[tuple[str, str]] = []
    succeeded = 0
    try:
        for ticker in assets:
            try:
                metadata = resolved_client.fetch_metadata(ticker)
                rows = resolved_client.fetch_daily_prices(ticker, from_date, to_date)
                with session_scope(settings) as symbol_session:
                    symbol = upsert_symbol(symbol_session, ticker)
                    apply_metadata_to_symbol(symbol_session, symbol, metadata)
                    count = upsert_tiingo_bars(symbol_session, symbol.id, rows)
                total += count
                succeeded += 1
                logger.info(
                    "tiingo_asset_ingested",
                    extra={"context": {"ticker": ticker, "rows": count, "run_id": str(run_id)}},
                )
            except PROVIDER_LEVEL_ERRORS as exc:
                logger.error(
                    "tiingo_provider_failure",
                    extra={"context": {"ticker": ticker, "code": provider_failure_code(exc)}},
                )
                failed.append(ticker)
                raise
            except Exception as exc:
                logger.error(
                    "tiingo_asset_ingest_failed",
                    extra={"context": {"ticker": ticker, "error": type(exc).__name__}},
                )
                failed.append(ticker)
                failures.append((ticker, type(exc).__name__))
        status = _derive_status(succeeded, len(failed))
        error_message = (
            "All assets failed: " + ", ".join(f"{t}={e}" for t, e in failures)
            if status == "failed"
            else None
        )
        _finalize_run(
            settings,
            run_id,
            status=status,
            bars_upserted=total,
            failed=failed,
            error_message=error_message,
            request_count=resolved_client.requests_made,
        )
    except Exception as exc:
        try:
            _finalize_run(
                settings,
                run_id,
                status="failed",
                bars_upserted=total,
                failed=failed,
                error_message=provider_failure_code(exc),
                request_count=resolved_client.requests_made,
            )
        except Exception:  # pragma: no cover - bookkeeping must never mask the cause
            logger.exception("tiingo_run_finalize_failed", extra={"context": {"run_id": str(run_id)}})
        raise
    finally:
        if owned_client:
            resolved_client.close()

    return IngestionResult(
        provider=PROVIDER,
        from_date=from_date,
        to_date=to_date,
        symbols_requested=list(assets),
        bars_upserted=total,
        symbols_failed=failed,
        run_id=str(run_id),
        run_status=status,
        run_error_message=error_message,
    )
