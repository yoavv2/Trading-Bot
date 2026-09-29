"""Idempotent daily-bar ingestion orchestration."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_platform.core.settings import MarketDataSettings
from trading_platform.db.models.daily_bar import DailyBar as DailyBarModel
from trading_platform.db.models.market_data_ingestion_run import MarketDataIngestionRun
from trading_platform.db.models.symbol import Symbol
from trading_platform.db.session import session_scope
from trading_platform.services.data import (
    DailyBar,
    DailyBarRequest,
    IngestionResult,
    IngestionRunStatus,
)
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
    job_id: uuid.UUID | None = None,
) -> MarketDataIngestionRun:
    run = MarketDataIngestionRun(
        id=uuid.uuid4(),
        job_id=job_id,
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


def _derive_run_status(
    *, succeeded_count: int, failed_count: int, run_error: str | None
) -> IngestionRunStatus:
    """Pure D-08a predicate for the terminal ingestion run status.

    ``failed`` when a run-level error occurred or zero symbols succeeded
    (including 0 succeeded and 0 failed); ``partial`` when at least one symbol
    succeeded and at least one failed; ``succeeded`` otherwise.
    """
    if succeeded_count < 0 or failed_count < 0:
        raise ValueError("succeeded_count and failed_count must be >= 0")
    if run_error is not None:
        return "failed"
    if succeeded_count == 0:
        return "failed"
    if failed_count >= 1:
        return "partial"
    return "succeeded"


def _all_symbols_failed_message(
    requested: int, failures: list[tuple[str, str]]
) -> str:
    """Deterministic all-fail message: exception class names only, never
    ``str(exc)`` (provider URLs/query strings must not reach the run row)."""
    listed = (
        ", ".join(f"{ticker} ({class_name})" for ticker, class_name in failures)
        or "none"
    )
    return f"0 of {requested} symbols succeeded; failed: {listed}"


def _finish_run(
    session: Session,
    run: MarketDataIngestionRun,
    *,
    status: IngestionRunStatus,
    bars_upserted: int,
    failed_symbols: list[str],
    error_message: str | None = None,
) -> None:
    run.bars_upserted = bars_upserted
    run.symbols_failed = failed_symbols
    run.completed_at = datetime.now(UTC)
    run.status = status
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


def _finalize_run(
    db_settings: Any,
    run_id: uuid.UUID,
    *,
    status: IngestionRunStatus,
    bars_upserted: int,
    failed_symbols: list[str],
    error_message: str | None = None,
) -> None:
    """Reload the run by id and finalize it in its own committed transaction."""
    with session_scope(db_settings) as session:
        run = session.get(MarketDataIngestionRun, run_id)
        if run is None:  # pragma: no cover - the row was committed at start
            raise LookupError(f"Ingestion run {run_id} not found")
        _finish_run(
            session,
            run,
            status=status,
            bars_upserted=bars_upserted,
            failed_symbols=failed_symbols,
            error_message=error_message,
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
    job_id: uuid.UUID | None = None,
) -> IngestionResult:
    """Orchestrate full daily-bar ingestion for a list of symbols.

    Steps:
    1. Record an ingestion run in its own committed transaction.
    2. For each symbol (one transaction each), upsert the symbol catalog row
       (minimal, ticker-only if not found), fetch bars from Polygon and upsert
       them.
    3. Finalize the ingestion run (succeeded/partial/failed, derived by
       ``_derive_run_status`` per D-08a: zero succeeded symbols is FAILED) in a
       separate committed transaction that also runs when the ingest fails.

    Re-running with the same window is idempotent; existing bars are updated,
    not duplicated.

    ``job_id`` is an opaque originating-Job identifier (D-09): when
    provided, it is written on ``MarketDataIngestionRun.job_id`` in the same
    transaction that creates (and commits) the run. This module imports nothing from
    ``jobs``/ -- the caller (a Job handler) owns that dependency, not this
    service.
    """
    adjusted = settings.polygon.adjusted
    total_bars = 0
    failed_symbols: list[str] = []
    failures: list[tuple[str, str]] = []
    succeeded_count = 0

    # The run row (with its ``job_id``) is committed in its own short
    # transaction BEFORE any work, so it is visible while running and survives
    # a failure or crash (P19 D-13, D-08/D-09).
    with session_scope(db_settings) as session:
        run = _start_run(
            session,
            from_date=from_date,
            to_date=to_date,
            adjusted=adjusted,
            symbols=symbols,
            trigger_source=trigger_source,
            job_id=job_id,
        )
        run_id = run.id

    try:
        with PolygonClient(settings.polygon) as client:
            for ticker in symbols:
                try:
                    # One transaction per symbol: a per-symbol DB error rolls
                    # back only that symbol and cannot poison later symbols
                    # or the final run bookkeeping.
                    with session_scope(db_settings) as symbol_session:
                        symbol = upsert_symbol(symbol_session, ticker)
                        request = DailyBarRequest(
                            symbol=ticker,
                            from_date=from_date,
                            to_date=to_date,
                            adjusted=adjusted,
                            provider=_PROVIDER,
                        )
                        bars = client.fetch_daily_bars(request)
                        count = upsert_daily_bars(symbol_session, bars, symbol.id)
                    total_bars += count
                    succeeded_count += 1
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
                    failures.append((ticker, type(exc).__name__))

        run_status = _derive_run_status(
            succeeded_count=succeeded_count,
            failed_count=len(failed_symbols),
            run_error=None,
        )
        run_error_message = (
            _all_symbols_failed_message(len(symbols), failures)
            if run_status == "failed"
            else None
        )
        _finalize_run(
            db_settings,
            run_id,
            status=run_status,
            bars_upserted=total_bars,
            failed_symbols=failed_symbols,
            error_message=run_error_message,
        )
    except Exception as exc:
        # Finalize FAILED in a separate, committed transaction; never let a
        # bookkeeping error mask the original failure.
        try:
            _finalize_run(
                db_settings,
                run_id,
                status="failed",
                bars_upserted=total_bars,
                failed_symbols=failed_symbols,
                error_message=str(exc),
            )
        except Exception:
            logger.exception(
                "ingestion_run_finalize_failed",
                extra={"context": {"run_id": str(run_id)}},
            )
        raise

    return IngestionResult(
        provider=_PROVIDER,
        from_date=from_date,
        to_date=to_date,
        symbols_requested=symbols,
        bars_upserted=total_bars,
        symbols_failed=failed_symbols,
        run_id=str(run_id),
        run_status=run_status,
        run_error_message=run_error_message,
    )
