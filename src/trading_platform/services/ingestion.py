"""Idempotent daily-bar ingestion orchestration."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_platform.core.settings import MarketDataSettings
from trading_platform.db.models.daily_bar import DailyBar as DailyBarModel
from trading_platform.db.models.market_data_ingestion_run import MarketDataIngestionRun
from trading_platform.db.models.symbol import Symbol
from trading_platform.db.session import session_scope
from trading_platform.services.data import DailyBar, DailyBarRequest, IngestionResult
from trading_platform.services.polygon import PolygonClient

logger = logging.getLogger(__name__)

_PROVIDER = "polygon"


# ---------------------------------------------------------------------------
# Symbol upsert helpers
# ---------------------------------------------------------------------------


def upsert_symbol(session: Session, ticker: str) -> Symbol:
    """Return an existing Symbol row or create a minimal one for the given ticker.

    This ensures daily-bar rows always have a valid symbol_id FK without
    requiring a full provider metadata sync before bar ingestion.
    """
    existing = session.execute(
        select(Symbol).where(Symbol.ticker == ticker)
    ).scalar_one_or_none()

    if existing is not None:
        return existing

    symbol = Symbol(id=uuid.uuid4(), ticker=ticker, active=True)
    session.add(symbol)
    session.flush()  # assign PK without committing
    logger.debug("symbol_created", extra={"context": {"ticker": ticker, "id": str(symbol.id)}})
    return symbol


# ---------------------------------------------------------------------------
# Bar upsert helpers
# ---------------------------------------------------------------------------


def _bar_to_row(bar: DailyBar, symbol_id: uuid.UUID) -> dict[str, Any]:
    """Convert a normalized DailyBar value object into a dict for upsert."""
    return {
        "id": uuid.uuid4(),
        "symbol_id": symbol_id,
        "session_date": bar.session_date,
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "volume": bar.volume,
        "vwap": bar.vwap,
        "trade_count": bar.trade_count,
        "adjusted": bar.adjusted,
        "provider": bar.provider,
        "provider_timestamp": bar.provider_timestamp,
    }


def upsert_daily_bars(session: Session, bars: list[DailyBar], symbol_id: uuid.UUID) -> int:
    """Upsert a batch of normalized bars; return the number of rows affected.

    Uses a PostgreSQL INSERT ... ON CONFLICT DO UPDATE so re-running the same
    ingest window refreshes OHLCV values without creating duplicates.
    """
    if not bars:
        return 0

    rows = [_bar_to_row(bar, symbol_id) for bar in bars]

    stmt = pg_insert(DailyBarModel).values(rows)
    update_cols = {
        "open": stmt.excluded.open,
        "high": stmt.excluded.high,
        "low": stmt.excluded.low,
        "close": stmt.excluded.close,
        "volume": stmt.excluded.volume,
        "vwap": stmt.excluded.vwap,
        "trade_count": stmt.excluded.trade_count,
        "provider_timestamp": stmt.excluded.provider_timestamp,
        "updated_at": datetime.now(UTC),
    }
    stmt = stmt.on_conflict_do_update(
        constraint="uq_daily_bars_symbol_session_adjusted_provider",
        set_=update_cols,
    ).returning(DailyBarModel.id)
    returned = session.execute(stmt).fetchall()
    session.flush()
    return len(returned)


# ---------------------------------------------------------------------------
# Ingestion run lifecycle
# ---------------------------------------------------------------------------


def _start_run(
    session: Session,
    *,
    from_date: date,
    to_date: date,
    adjusted: bool,
    symbols: list[str],
    trigger_source: str,
) -> MarketDataIngestionRun:
    run = MarketDataIngestionRun(
        id=uuid.uuid4(),
        provider=_PROVIDER,
        from_date=from_date,
        to_date=to_date,
        adjusted=adjusted,
        status="running",
        symbols_requested=symbols,
        symbols_failed=[],
        bars_upserted=0,
        page_count=0,
        trigger_source=trigger_source,
        started_at=datetime.now(UTC),
        request_metadata={"adjusted": adjusted, "symbols": symbols},
    )
    session.add(run)
    session.flush()
    logger.info(
        "ingestion_run_started",
        extra={"context": {"run_id": str(run.id), "symbols": symbols}},
    )
    return run


def _finish_run(
    session: Session,
    run: MarketDataIngestionRun,
    *,
    bars_upserted: int,
    failed_symbols: list[str],
    error_message: str | None = None,
) -> None:
    run.bars_upserted = bars_upserted
    run.symbols_failed = failed_symbols
    run.completed_at = datetime.now(UTC)
    run.status = (
        "failed"
        if error_message
        else ("partial" if failed_symbols else "succeeded")
    )
    if error_message:
        run.error_message = error_message
    session.flush()
    logger.info(
        "ingestion_run_finished",
        extra={
            "context": {
                "run_id": str(run.id),
                "status": run.status,
                "bars_upserted": bars_upserted,
                "failed_symbols": failed_symbols,
            }
        },
    )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def ingest_daily_bars(
    *,
    from_date: date,
    to_date: date,
    symbols: list[str],
    settings: MarketDataSettings,
    trigger_source: str = "cli",
    db_settings: Any = None,
) -> IngestionResult:
    """Orchestrate full daily-bar ingestion for a list of symbols.

    Steps:
    1. Open a DB session and record an ingestion run.
    2. Upsert symbol catalog rows (minimal, ticker-only if not found).
    3. For each symbol, fetch bars from Polygon and upsert them.
    4. Update the ingestion run with outcome metadata and close.

    Re-running with the same window is idempotent; existing bars are updated,
    not duplicated.
    """
    adjusted = settings.polygon.adjusted
    total_bars = 0
    failed_symbols: list[str] = []

    with session_scope(db_settings) as session:
        run = _start_run(
            session,
            from_date=from_date,
            to_date=to_date,
            adjusted=adjusted,
            symbols=symbols,
            trigger_source=trigger_source,
        )
        run_id = run.id

        try:
            with PolygonClient(settings.polygon) as client:
                for ticker in symbols:
                    try:
                        symbol = upsert_symbol(session, ticker)
                        request = DailyBarRequest(
                            symbol=ticker,
                            from_date=from_date,
                            to_date=to_date,
                            adjusted=adjusted,
                            provider=_PROVIDER,
                        )
                        bars = client.fetch_daily_bars(request)
                        count = upsert_daily_bars(session, bars, symbol.id)
                        total_bars += count
                        logger.info(
                            "symbol_bars_ingested",
                            extra={
                                "context": {
                                    "ticker": ticker,
                                    "bars": count,
                                    "run_id": str(run_id),
                                }
                            },
                        )
                    except Exception as exc:
                        logger.error(
                            "symbol_ingest_failed",
                            extra={"context": {"ticker": ticker, "error": str(exc)}},
                        )
                        failed_symbols.append(ticker)

            _finish_run(
                session,
                run,
                bars_upserted=total_bars,
                failed_symbols=failed_symbols,
            )

        except Exception as exc:
            _finish_run(
                session,
                run,
                bars_upserted=total_bars,
                failed_symbols=failed_symbols,
                error_message=str(exc),
            )
            raise

    return IngestionResult(
        provider=_PROVIDER,
        from_date=from_date,
        to_date=to_date,
        symbols_requested=symbols,
        bars_upserted=total_bars,
        symbols_failed=failed_symbols,
    )
