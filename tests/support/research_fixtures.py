"""Synthetic bars and an in-memory bar store for research tests.

``FakeBarStore.bars_for_sessions`` mirrors the access layer's semantics (the last
``n_sessions`` persisted sessions on or before ``as_of``, in ascending order, with a
missing bar simply absent) so the four originals and the interpreter can be run on the
same inputs without a database. ``seed_research_bars`` writes the same series into a
migrated database for the DB-backed checks.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from trading_platform.db.models.daily_bar import DailyBar as DailyBarModel
from trading_platform.db.models.symbol import Symbol
from trading_platform.services.calendar import upsert_market_sessions
from trading_platform.services.market_data_access import SessionBar


@dataclass(frozen=True)
class Bar:
    symbol: str
    session_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    adjusted: bool = True
    provider: str = "polygon"
    vwap: Decimal | None = None
    trade_count: int | None = None
    provider_timestamp: Any = None


def weekday_sessions(start: date, count: int) -> list[date]:
    out: list[date] = []
    current = start
    while len(out) < count:
        if current.weekday() < 5:
            out.append(current)
        current += timedelta(days=1)
    return out


def xnys_sessions(start: date, count: int) -> list[date]:
    """Real XNYS sessions (holidays excluded) for database-backed fixtures."""

    from trading_platform.services.calendar import sessions_in_range

    end = start + timedelta(days=int(count * 1.6) + 10)
    return sessions_in_range(start, end)[:count]


def make_bars(
    symbol: str,
    sessions: list[date],
    *,
    seed: int = 7,
    start_price: Decimal = Decimal("100"),
    drift: float = 0.0,
    volatility: float = 0.02,
) -> list[Bar]:
    rng = random.Random(seed)
    price = start_price
    bars: list[Bar] = []
    for index, session_date in enumerate(sessions):
        move = Decimal(str(round(rng.gauss(drift, volatility), 6)))
        close = (price * (Decimal(1) + move)).quantize(Decimal("0.000001"))
        if close <= 0:
            close = Decimal("0.01")
        open_ = ((price + close) / 2).quantize(Decimal("0.000001"))
        high = max(open_, close) * Decimal("1.005")
        low = min(open_, close) * Decimal("0.995")
        bars.append(
            Bar(
                symbol=symbol,
                session_date=session_date,
                open=open_,
                high=high.quantize(Decimal("0.000001")),
                low=low.quantize(Decimal("0.000001")),
                close=close,
                volume=1_000_000 + index,
            )
        )
        price = close
    return bars


class FakeBarStore:
    """Serves ``bars_for_sessions`` from in-memory series keyed by symbol."""

    def __init__(self, sessions: list[date], series: dict[str, list[Bar]]) -> None:
        self.sessions = sorted(sessions)
        self.series = {symbol: {bar.session_date: bar for bar in bars} for symbol, bars in series.items()}
        self.calls: list[dict[str, Any]] = []

    def bars_for_sessions(
        self,
        session: Any,
        symbol: str,
        n_sessions: int,
        as_of: date | None = None,
        exchange: str = "XNYS",
        adjusted: bool = True,
        provider: str = "polygon",
    ) -> list[SessionBar]:
        self.calls.append({"symbol": symbol, "n_sessions": n_sessions, "as_of": as_of, "adjusted": adjusted, "provider": provider})
        as_of = as_of or date.today()
        window = [s for s in self.sessions if s <= as_of][-n_sessions:]
        by_date = self.series.get(symbol, {})
        return [
            SessionBar(
                symbol=symbol,
                session_date=s,
                open=by_date[s].open,
                high=by_date[s].high,
                low=by_date[s].low,
                close=by_date[s].close,
                volume=by_date[s].volume,
                adjusted=adjusted,
                provider=provider,
                vwap=None,
                trade_count=None,
                provider_timestamp=None,
            )
            for s in window
            if s in by_date
        ]


def seed_research_bars(
    session: Session,
    symbol: str,
    bars: list[Bar],
    *,
    provider: str = "tiingo",
    both_series: bool = True,
    split_factor: Decimal | None = Decimal(1),
    dividend_cash: Decimal | None = Decimal(0),
) -> Symbol:
    if bars:
        upsert_market_sessions(session, bars[0].session_date, bars[-1].session_date)
    symbol_row = Symbol(id=uuid.uuid4(), ticker=symbol, active=True, name=f"{symbol} Inc", market="stocks", symbol_type="CS", metadata_provider=provider)
    session.add(symbol_row)
    session.flush()
    for bar in bars:
        for adjusted in ((False, True) if both_series else (True,)):
            session.add(
                DailyBarModel(
                    id=uuid.uuid4(),
                    symbol_id=symbol_row.id,
                    session_date=bar.session_date,
                    open=bar.open,
                    high=bar.high,
                    low=bar.low,
                    close=bar.close,
                    volume=bar.volume,
                    adjusted=adjusted,
                    provider=provider,
                    split_factor=split_factor,
                    dividend_cash=dividend_cash,
                )
            )
    session.flush()
    return symbol_row
