"""Exchange calendar service backed by exchange_calendars (XNYS)."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

import exchange_calendars as xcals
import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_platform.db.models.market_session import MarketSession
from trading_platform.db.session import session_scope

if TYPE_CHECKING:
    from trading_platform.core.settings import Settings

logger = logging.getLogger(__name__)

_DEFAULT_EXCHANGE = "XNYS"


def get_calendar(exchange: str = _DEFAULT_EXCHANGE) -> Any:
    """Return an exchange_calendars calendar object for the given exchange."""
    return xcals.get_calendar(exchange)


def sessions_in_range(
    start: date,
    end: date,
    exchange: str = _DEFAULT_EXCHANGE,
) -> list[date]:
    """Return all trading session dates for the exchange in [start, end] inclusive.

    Uses XNYS (NYSE) as the authoritative calendar — this covers all ten symbols
    in the configured universe which are all US equities.
    """
    cal = get_calendar(exchange)
    sessions = cal.sessions_in_range(
        pd.Timestamp(start),
        pd.Timestamp(end),
    )
    return [ts.date() for ts in sessions]


def latest_session_before(
    as_of: date,
    exchange: str = _DEFAULT_EXCHANGE,
) -> date:
    """Return the most recent completed session on or before as_of.

    Returns the last session on or before as_of. Useful for determining the
    "current completed session" relative to today.
    """
    cal = get_calendar(exchange)
    ts = pd.Timestamp(as_of)
    # If as_of is itself a session, return it.
    if cal.is_session(ts):
        return ts.date()
    # Walk back to find the last session before as_of using date_to_session
    # with direction="previous", which handles non-session dates gracefully.
    prev = cal.date_to_session(ts, direction="previous")
    return prev.date()


def is_trading_session(session_date: date, exchange: str = _DEFAULT_EXCHANGE) -> bool:
    """Return True if session_date is a trading session for the exchange."""
    cal = get_calendar(exchange)
    return bool(cal.is_session(pd.Timestamp(session_date)))


# ---------------------------------------------------------------------------
# Library-bound navigation (COR-04): read surface for the calendar facts
# ---------------------------------------------------------------------------


class CalendarOutOfBoundsError(Exception):
    """A calendar lookup fell outside the exchange_calendars library window.

    The library only knows a rolling window of sessions; a date beyond it
    raises ``DateOutOfBounds`` (a ``ValueError``) or ``OverflowError``. This
    typed error lets callers map that to an explicit ``unknown`` instead of an
    untyped crash.
    """


def previous_session_date(as_of: date, exchange: str = _DEFAULT_EXCHANGE) -> date:
    """Return the latest session strictly before *as_of* (calendar read surface).

    Works for session and non-session dates. Raises ``CalendarOutOfBoundsError``
    when the library cannot answer.
    """

    cal = get_calendar(exchange)
    ts = pd.Timestamp(as_of)
    try:
        if cal.is_session(ts):
            return cal.previous_session(ts).date()
        return cal.date_to_session(ts, direction="previous").date()
    except (ValueError, OverflowError) as exc:
        raise CalendarOutOfBoundsError(str(exc)) from exc


def next_session_date(as_of: date, exchange: str = _DEFAULT_EXCHANGE) -> date:
    """Return the earliest session strictly after *as_of* (calendar read surface)."""

    cal = get_calendar(exchange)
    ts = pd.Timestamp(as_of)
    try:
        if cal.is_session(ts):
            return cal.next_session(ts).date()
        return cal.date_to_session(ts, direction="next").date()
    except (ValueError, OverflowError) as exc:
        raise CalendarOutOfBoundsError(str(exc)) from exc


def calendar_horizon_end(
    settings: Settings,
    today: date,
    *,
    minimum_sessions: int = 0,
) -> date:
    """Date of the N-th session strictly after *today*, N = the configured
    coverage horizon (``max(N, minimum_sessions)``), clipped to the library's
    last session. Raises ``CalendarOutOfBoundsError`` when *today* itself is
    outside the library window (read surface for the calendar facts, D-24)."""

    exchange = settings.market_data.calendar.exchange
    count = max(settings.market_data.calendar.coverage_horizon_sessions, minimum_sessions)
    cal = get_calendar(exchange)
    try:
        last_session: date = cal.last_session.date()
        current = next_session_date(today, exchange)
        if current > last_session:
            raise CalendarOutOfBoundsError(f"no session after {today.isoformat()} in the library window")
        ts = pd.Timestamp(current)
        try:
            return cal.session_offset(ts, count - 1).date()
        except (ValueError, OverflowError):
            return last_session
    except (ValueError, OverflowError) as exc:
        raise CalendarOutOfBoundsError(str(exc)) from exc


# ---------------------------------------------------------------------------
# Session persistence helpers
# ---------------------------------------------------------------------------


def _build_session_rows(
    sessions: list[date],
    exchange: str,
    calendar: Any,
) -> list[dict[str, Any]]:
    """Build upsert rows for a list of session dates."""
    rows: list[dict[str, Any]] = []
    schedule = calendar.schedule

    for session_date in sessions:
        ts = pd.Timestamp(session_date)
        try:
            row_data = schedule.loc[ts]
            market_open: datetime | None = row_data["open"].to_pydatetime().replace(tzinfo=UTC)
            market_close: datetime | None = row_data["close"].to_pydatetime().replace(tzinfo=UTC)
        except KeyError:
            market_open = None
            market_close = None

        # Detect early close: standard NYSE close is 16:00 ET = 21:00 UTC.
        # If close is before 20:30 UTC treat it as early close.
        early_close = False
        if market_close is not None:
            # 20:30 UTC = 16:30 ET; close before that is early
            early_close = market_close.hour < 20 or (market_close.hour == 20 and market_close.minute < 30)

        rows.append(
            {
                "id": uuid.uuid4(),
                "exchange": exchange,
                "session_date": session_date,
                "market_open": market_open,
                "market_close": market_close,
                "early_close": early_close,
            }
        )
    return rows


def upsert_market_sessions(
    session: Session,
    start: date,
    end: date,
    exchange: str = _DEFAULT_EXCHANGE,
) -> int:
    """Upsert XNYS session rows for [start, end] into the database.

    Returns the number of rows affected. Safe to call repeatedly — existing
    rows are updated, not duplicated.
    """
    session_dates = sessions_in_range(start, end, exchange)
    if not session_dates:
        return 0

    cal = get_calendar(exchange)
    rows = _build_session_rows(session_dates, exchange, cal)

    stmt = pg_insert(MarketSession).values(rows)
    update_cols = {
        "market_open": stmt.excluded.market_open,
        "market_close": stmt.excluded.market_close,
        "early_close": stmt.excluded.early_close,
        "updated_at": datetime.now(UTC),
    }
    stmt = stmt.on_conflict_do_update(
        constraint="uq_market_sessions_exchange_date",
        set_=update_cols,
    ).returning(MarketSession.id)

    returned = session.execute(stmt).fetchall()
    session.flush()

    logger.info(
        "market_sessions_upserted",
        extra={
            "context": {
                "exchange": exchange,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "count": len(returned),
            }
        },
    )
    return len(returned)


def get_persisted_sessions(
    session: Session,
    start: date,
    end: date,
    exchange: str = _DEFAULT_EXCHANGE,
) -> list[MarketSession]:
    """Return persisted MarketSession rows for the given exchange and date range."""
    rows = session.execute(
        select(MarketSession)
        .where(MarketSession.exchange == exchange)
        .where(MarketSession.session_date >= start)
        .where(MarketSession.session_date <= end)
        .order_by(MarketSession.session_date)
    ).scalars().all()
    return list(rows)


# ---------------------------------------------------------------------------
# Job-callable session-opening wrapper (OPS-05)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MarketSessionSyncResult:
    """Outcome of one ``sync_market_sessions`` call."""

    exchange: str
    from_date: date
    to_date: date
    sessions_upserted: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "exchange": self.exchange,
            "from_date": self.from_date.isoformat(),
            "to_date": self.to_date.isoformat(),
            "sessions_upserted": self.sessions_upserted,
        }


def sync_market_sessions(
    *,
    from_date: date,
    to_date: date,
    settings: Settings,
) -> MarketSessionSyncResult:
    """Open a session and upsert XNYS sessions for [from_date, to_date].

    ``JobHandler`` implementations may only import ``services.*``
    (``jobs/contracts.py``), never ``db.session`` directly -- this wraps
    ``upsert_market_sessions`` (which takes a caller-owned ``Session``) the
    same way ``worker/commands/ingest.py::run_sync_sessions`` did, moved one
    layer down so the ``sync-market-sessions`` Job handler stays inside that
    contract. ``session_scope`` is imported at module level (no import cycle:
    ``db/session.py`` only imports ``core.settings``).
    """

    exchange = settings.market_data.calendar.exchange
    with session_scope(settings) as db_session:
        count = upsert_market_sessions(db_session, from_date, to_date, exchange)

    return MarketSessionSyncResult(
        exchange=exchange,
        from_date=from_date,
        to_date=to_date,
        sessions_upserted=count,
    )
