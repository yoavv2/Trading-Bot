"""Per-symbol metadata readiness (COR-03, D-29, R-29).

A symbol is trading-ready only when the reference data that gates trading is
actually present: ``metadata_provider`` set (a sync populated the row),
``active``, ``market == 'stocks'``, ``symbol_type`` in {CS, ETF} and a
non-empty ``primary_exchange``. A symbol without it is
``not_ready(missing_metadata)`` and is never reported ready for trading.

Scope: this is METADATA readiness, per symbol. The evaluation session's data
readiness (bars, warm-up, calendar) is a separate bar/history rule (20.1-05).

Read-only, no ``jobs/`` import. ``symbols_readiness`` issues ONE statement for
any number of tickers.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_platform.db.models.symbol import Symbol

REQUIRED_MARKET = "stocks"
REQUIRED_SYMBOL_TYPES: frozenset[str] = frozenset({"CS", "ETF"})
REQUIRED_REQUIREMENTS: tuple[str, ...] = (
    "metadata_provider",
    "active",
    "market",
    "symbol_type",
    "primary_exchange",
)


class NotReadyReason(StrEnum):
    """Closed reason a symbol is not ready."""

    MISSING_METADATA = "missing_metadata"


@dataclass(frozen=True)
class SymbolReadiness:
    ticker: str
    ready: bool
    reason: NotReadyReason | None
    missing_requirements: tuple[str, ...]


def readiness_from_symbol(ticker: str, symbol: Symbol | None) -> SymbolReadiness:
    """Pure readiness decision for one ticker and its (optional) Symbol row."""

    if symbol is None:
        missing = REQUIRED_REQUIREMENTS
    else:
        failed = {
            "metadata_provider": symbol.metadata_provider is None,
            "active": symbol.active is not True,
            "market": symbol.market != REQUIRED_MARKET,
            "symbol_type": symbol.symbol_type not in REQUIRED_SYMBOL_TYPES,
            "primary_exchange": not (symbol.primary_exchange or "").strip(),
        }
        missing = tuple(name for name in REQUIRED_REQUIREMENTS if failed[name])

    if missing:
        return SymbolReadiness(
            ticker=ticker,
            ready=False,
            reason=NotReadyReason.MISSING_METADATA,
            missing_requirements=missing,
        )
    return SymbolReadiness(ticker=ticker, ready=True, reason=None, missing_requirements=())


def symbols_readiness(session: Session, tickers: Iterable[str]) -> dict[str, SymbolReadiness]:
    """Readiness for every requested ticker with ONE SELECT."""

    requested = list(dict.fromkeys(tickers))
    if not requested:
        return {}
    rows = session.execute(select(Symbol).where(Symbol.ticker.in_(requested))).scalars().all()
    by_ticker = {row.ticker: row for row in rows}
    return {ticker: readiness_from_symbol(ticker, by_ticker.get(ticker)) for ticker in requested}


__all__ = [
    "REQUIRED_MARKET",
    "REQUIRED_REQUIREMENTS",
    "REQUIRED_SYMBOL_TYPES",
    "NotReadyReason",
    "SymbolReadiness",
    "readiness_from_symbol",
    "symbols_readiness",
]
