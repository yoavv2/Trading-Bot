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
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trading_platform.db.models.daily_bar import DailyBar as DailyBarModel
from trading_platform.db.models.market_session import MarketSession
from trading_platform.db.models.symbol import Symbol
from trading_platform.services.calendar import (
    _DEFAULT_EXCHANGE,
    get_persisted_sessions,
    latest_session_before,
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
