"""Session-aware market-data read layer.

This module provides the reusable access patterns that strategies and backtests
use to read persisted daily bars.  It hides provider-specific logic from
callers and operates entirely on the database.

Key queries:
- latest_completed_session: the most recent session for which bars exist
- bars_for_sessions: bars for a symbol over the last N sessions
- missing_sessions: sessions in a range that lack bars for a symbol
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trading_platform.db.models.daily_bar import DailyBar as DailyBarModel
from trading_platform.db.models.market_session import MarketSession
from trading_platform.db.models.symbol import Symbol
from trading_platform.services.calendar import (
    _DEFAULT_EXCHANGE,
    get_persisted_sessions,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Value objects returned by the access layer
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionBar:
    """Normalized price bar aligned to a trading session."""

    symbol: str
    session_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    adjusted: bool
    provider: str
    vwap: Decimal | None = None
    trade_count: int | None = None
    provider_timestamp: datetime | None = None


@dataclass(frozen=True)
class PersistedSessionRow:
    """Persisted open/close facts of one session (COR-04 calendar-facts read surface)."""

    session_date: date
    market_open: datetime | None
    market_close: datetime | None
    early_close: bool


@dataclass(frozen=True)
class PersistedSessionsAfter:
    """Count and last date of persisted sessions strictly after a date."""

    count: int
    last_session: date | None


@dataclass(frozen=True)
class MissingSessionInfo:
    """A session for which expected bar data is absent."""

    exchange: str
    session_date: date
    symbol: str | None = None  # None means the session itself is not persisted


# ---------------------------------------------------------------------------
# Access helpers
# ---------------------------------------------------------------------------


def latest_completed_session(
    session: Session,
    exchange: str = _DEFAULT_EXCHANGE,
    as_of: date | None = None,
) -> date | None:
    """Return the latest session date for which at least one bar exists.

    If as_of is provided, restrict to sessions on or before that date.
    Returns None if no sessions or bars are persisted yet.
    """
    query = (
        select(func.max(MarketSession.session_date))
        .where(MarketSession.exchange == exchange)
        .join(
            DailyBarModel,
            DailyBarModel.session_date == MarketSession.session_date,
        )
    )
    if as_of is not None:
        query = query.where(MarketSession.session_date <= as_of)

    result = session.execute(query).scalar_one_or_none()
    return result


def latest_persisted_session(
    session: Session,
    exchange: str = _DEFAULT_EXCHANGE,
    as_of: date | None = None,
) -> date | None:
    """Return the latest session date that is persisted (regardless of bar coverage).

    Useful for determining the horizon of the calendar sync.
    """
    query = select(func.max(MarketSession.session_date)).where(
        MarketSession.exchange == exchange
    )
    if as_of is not None:
        query = query.where(MarketSession.session_date <= as_of)
    return session.execute(query).scalar_one_or_none()


def persisted_session_dates(
    session: Session,
    start: date,
    end: date,
    exchange: str = _DEFAULT_EXCHANGE,
) -> list[date]:
    """Return persisted session dates in ascending order for the requested range."""
    return session.execute(
        select(MarketSession.session_date)
        .where(MarketSession.exchange == exchange)
        .where(MarketSession.session_date >= start)
        .where(MarketSession.session_date <= end)
        .order_by(MarketSession.session_date.asc())
    ).scalars().all()


def next_persisted_session(
    session: Session,
    session_date: date,
    exchange: str = _DEFAULT_EXCHANGE,
) -> date | None:
    """Return the next persisted session after *session_date*."""
    return session.execute(
        select(MarketSession.session_date)
        .where(MarketSession.exchange == exchange)
        .where(MarketSession.session_date > session_date)
        .order_by(MarketSession.session_date.asc())
        .limit(1)
    ).scalar_one_or_none()


def bars_for_sessions(
    session: Session,
    symbol: str,
    n_sessions: int,
    as_of: date | None = None,
    exchange: str = _DEFAULT_EXCHANGE,
    adjusted: bool = True,
    provider: str = "polygon",
) -> list[SessionBar]:
    """Return the last n_sessions bars for a symbol in ascending session order.

    Args:
        session: SQLAlchemy session.
        symbol: Ticker string (e.g. "AAPL").
        n_sessions: Number of sessions to return.
        as_of: Restrict to sessions on or before this date.  Defaults to today.
        exchange: Exchange code used to filter persisted sessions.
        adjusted: Whether to return adjusted bars.
        provider: Provider tag to filter bars.

    Returns an empty list if the symbol has no bars.
    """
    as_of = as_of or date.today()

    # Resolve the symbol row
    sym = session.execute(
        select(Symbol).where(Symbol.ticker == symbol)
    ).scalar_one_or_none()
    if sym is None:
        return []

    # Get n_sessions most recent session dates from persisted sessions
    session_subq = (
        select(MarketSession.session_date)
        .where(MarketSession.exchange == exchange)
        .where(MarketSession.session_date <= as_of)
        .order_by(MarketSession.session_date.desc())
        .limit(n_sessions)
        .subquery()
    )

    bars = session.execute(
        select(DailyBarModel)
        .where(DailyBarModel.symbol_id == sym.id)
        .where(DailyBarModel.adjusted == adjusted)
        .where(DailyBarModel.provider == provider)
        .where(DailyBarModel.session_date.in_(select(session_subq)))
        .order_by(DailyBarModel.session_date.asc())
    ).scalars().all()

    return [
        SessionBar(
            symbol=symbol,
            session_date=bar.session_date,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume,
            adjusted=bar.adjusted,
            provider=bar.provider,
            vwap=bar.vwap,
            trade_count=bar.trade_count,
            provider_timestamp=bar.provider_timestamp,
        )
        for bar in bars
    ]


def bars_for_session_date(
    session: Session,
    session_date: date,
    *,
    symbols: list[str] | tuple[str, ...] | None = None,
    adjusted: bool = True,
    provider: str = "polygon",
) -> dict[str, SessionBar]:
    """Return session bars keyed by symbol for one persisted session date."""
    query = (
        select(Symbol.ticker, DailyBarModel)
        .join(Symbol, Symbol.id == DailyBarModel.symbol_id)
        .where(DailyBarModel.session_date == session_date)
        .where(DailyBarModel.adjusted == adjusted)
        .where(DailyBarModel.provider == provider)
        .order_by(Symbol.ticker.asc())
    )

    if symbols:
        query = query.where(Symbol.ticker.in_(symbols))

    rows = session.execute(query).all()
    return {
        ticker: SessionBar(
            symbol=ticker,
            session_date=bar.session_date,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume,
            adjusted=bar.adjusted,
            provider=bar.provider,
            vwap=bar.vwap,
            trade_count=bar.trade_count,
            provider_timestamp=bar.provider_timestamp,
        )
        for ticker, bar in rows
    }


def missing_bars_for_session(
    session: Session,
    session_date: date,
    *,
    symbols: list[str] | tuple[str, ...],
    adjusted: bool = True,
    provider: str = "polygon",
) -> list[str]:
    """Return requested symbols missing a persisted bar for *session_date*."""

    if not symbols:
        return []
    available = bars_for_session_date(
        session,
        session_date,
        symbols=list(symbols),
        adjusted=adjusted,
        provider=provider,
    )
    return sorted(set(symbols) - set(available))


def missing_sessions_for_symbol(
    session: Session,
    symbol: str,
    start: date,
    end: date,
    exchange: str = _DEFAULT_EXCHANGE,
    adjusted: bool = True,
    provider: str = "polygon",
) -> list[MissingSessionInfo]:
    """Return sessions in [start, end] that lack bars for the given symbol.

    A session is "missing" if:
    1. It is a persisted trading session (exists in market_sessions), AND
    2. The symbol has no bar row for that session_date + adjusted + provider.

    If market_sessions has not been seeded for the range, those dates are also
    flagged (session_date present but no symbol data recorded).
    """
    # Resolve symbol
    sym = session.execute(
        select(Symbol).where(Symbol.ticker == symbol)
    ).scalar_one_or_none()

    persisted = get_persisted_sessions(session, start, end, exchange)
    if not persisted:
        return []

    # Get the set of dates with bars for this symbol
    covered: set[date] = set()
    if sym is not None:
        bar_dates = session.execute(
            select(DailyBarModel.session_date)
            .where(DailyBarModel.symbol_id == sym.id)
            .where(DailyBarModel.adjusted == adjusted)
            .where(DailyBarModel.provider == provider)
            .where(DailyBarModel.session_date >= start)
            .where(DailyBarModel.session_date <= end)
        ).scalars().all()
        covered = set(bar_dates)

    return [
        MissingSessionInfo(
            exchange=exchange,
            session_date=ms.session_date,
            symbol=symbol,
        )
        for ms in persisted
        if ms.session_date not in covered
    ]


# ---------------------------------------------------------------------------
# Read surface for the calendar facts (COR-04, services/calendar_facts.py).
# Evaluation-input recording (20.1-06) records reads made through these.
# ---------------------------------------------------------------------------


def persisted_session_row(
    session: Session,
    session_date: date,
    exchange: str = _DEFAULT_EXCHANGE,
) -> PersistedSessionRow | None:
    """Return the persisted open/close/early-close of one session, or ``None``.

    Read surface for the calendar facts: one statement.
    """

    row = session.execute(
        select(
            MarketSession.session_date,
            MarketSession.market_open,
            MarketSession.market_close,
            MarketSession.early_close,
        )
        .where(MarketSession.exchange == exchange)
        .where(MarketSession.session_date == session_date)
    ).one_or_none()
    if row is None:
        return None
    return PersistedSessionRow(
        session_date=row.session_date,
        market_open=row.market_open,
        market_close=row.market_close,
        early_close=row.early_close,
    )


def persisted_sessions_after(
    session: Session,
    session_date: date,
    exchange: str = _DEFAULT_EXCHANGE,
    limit: int | None = None,
) -> PersistedSessionsAfter:
    """Count (optionally capped at *limit*) and last persisted session strictly
    after *session_date*. Read surface for the calendar runway: one statement."""

    base = (
        select(MarketSession.session_date)
        .where(MarketSession.exchange == exchange)
        .where(MarketSession.session_date > session_date)
    )
    if limit is not None:
        base = base.order_by(MarketSession.session_date.asc()).limit(limit)
    sub = base.subquery()
    row = session.execute(
        select(func.count(sub.c.session_date), func.max(sub.c.session_date))
    ).one()
    return PersistedSessionsAfter(count=int(row[0]), last_session=row[1])


def bar_counts_through_session(
    session: Session,
    symbols: list[str] | tuple[str, ...],
    session_date: date,
    adjusted: bool = True,
    provider: str = "polygon",
) -> dict[str, int]:
    """Bars on or before *session_date* per requested symbol, ONE GROUP BY
    statement for any number of symbols. Symbols with no bars map to ``0``.

    Read surface for the evaluation-session history rule (warm-up).
    """

    if not symbols:
        return {}
    rows = session.execute(
        select(Symbol.ticker, func.count(DailyBarModel.id))
        .join(DailyBarModel, DailyBarModel.symbol_id == Symbol.id)
        .where(Symbol.ticker.in_(list(symbols)))
        .where(DailyBarModel.session_date <= session_date)
        .where(DailyBarModel.adjusted == adjusted)
        .where(DailyBarModel.provider == provider)
        .group_by(Symbol.ticker)
    ).all()
    counts = {ticker: int(count) for ticker, count in rows}
    return {symbol: counts.get(symbol, 0) for symbol in symbols}
