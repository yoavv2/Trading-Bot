"""Seed helpers and a clock for the calendar-fact tests (COR-04, 20.1-05).

Calendar facts used by tests (XNYS): normal Tuesday 2025-12-02, early close
2025-11-28 (13:00 ET), holidays 2025-11-27 (Thanksgiving) and 2025-12-25,
weekend 2025-11-29/30.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import insert, select
from tests.support.symbol_metadata import ready_symbol_fields

from trading_platform.core.settings import Settings, load_settings
from trading_platform.db.models.daily_bar import DailyBar
from trading_platform.db.models.symbol import Symbol
from trading_platform.db.session import session_scope
from trading_platform.services.calendar import sessions_in_range, upsert_market_sessions

ET = ZoneInfo("America/New_York")


def et(year: int, month: int, day: int, hour: int = 0, minute: int = 0, second: int = 0) -> datetime:
    """An exchange-local (America/New_York) instant as an aware UTC datetime."""

    return datetime(year, month, day, hour, minute, second, tzinfo=ET).astimezone(UTC)


def clock_at(now: datetime) -> Callable[[], datetime]:
    return lambda: now


def seed_calendar(start: date, end: date, *, settings: Settings | None = None) -> int:
    resolved = settings or load_settings()
    with session_scope(resolved) as session:
        return upsert_market_sessions(session, start, end, resolved.market_data.calendar.exchange)


@dataclass(frozen=True)
class _Meta:
    strategy_id: str
    universe: tuple[str, ...]


class FakeStrategy:
    """Stand-in for ``BaseStrategy``: the facts read only ``metadata.universe`` and
    ``warmup_periods``."""

    def __init__(
        self,
        universe: Iterable[str],
        warmup_periods: int = 0,
        strategy_id: str = "fake_strategy",
    ) -> None:
        self.strategy_id = strategy_id
        self.metadata = _Meta(strategy_id=strategy_id, universe=tuple(universe))
        self.warmup_periods = warmup_periods


def seed_bars(
    symbols: Iterable[str],
    session_dates: Iterable[date],
    *,
    ready_metadata: bool = True,
    settings: Settings | None = None,
) -> None:
    """Persist one adjusted polygon bar per (symbol, session date). Symbols that
    already exist are reused."""

    resolved = settings or load_settings()
    dates = list(session_dates)
    with session_scope(resolved) as session:
        for ticker in symbols:
            symbol = session.execute(select(Symbol).where(Symbol.ticker == ticker)).scalar_one_or_none()
            if symbol is None:
                fields = ready_symbol_fields() if ready_metadata else {"active": True}
                symbol = Symbol(ticker=ticker, **fields)
                session.add(symbol)
                session.flush()
            existing = set(
                session.execute(
                    select(DailyBar.session_date).where(
                        DailyBar.symbol_id == symbol.id,
                        DailyBar.adjusted.is_(True),
                        DailyBar.provider == "polygon",
                    )
                ).scalars()
            )
            rows = [
                {
                    "id": uuid.uuid4(),
                    "symbol_id": symbol.id,
                    "session_date": session_date,
                    "open": Decimal("100"),
                    "high": Decimal("101"),
                    "low": Decimal("99"),
                    "close": Decimal("100.5"),
                    "volume": 1000,
                    "adjusted": True,
                    "provider": "polygon",
                }
                for session_date in dates
                if session_date not in existing
            ]
            if rows:
                session.execute(insert(DailyBar), rows)


def sessions_between(start: date, end: date) -> list[date]:
    return sessions_in_range(start, end)
