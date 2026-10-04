"""Read-request recording and canonical result digests (PROV-01, D-25).

During a risk evaluation every read made through the shared market-data
accessors is reported to the active ``ReadRecorder`` as ``(kind, parameters,
result digest, result count)`` -- empty results included, because a symbol that
was entirely absent at evaluation time and is ingested later must change a
digest (the 29 Sep shape).

This module is deliberately pure: it imports nothing from ``db/`` or from the
accessor module (which imports *it*), so there is no cycle. Digests are SHA-256
over canonical JSON. Bar digests cover exactly ``(session_date, open, high, low,
close, volume, vwap, trade_count, adjusted, provider)``: ``updated_at`` and
``provider_timestamp`` are excluded so a no-op re-ingest is never stale, and
Decimals are rendered with ``format(d.normalize(), 'f')`` so ``1.50 == 1.5``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date
from decimal import Decimal
from typing import Any, Protocol

# Accessor kinds (the closed ``ManifestRequestKind`` values are these strings).
KIND_BARS_FOR_SESSIONS = "bars_for_sessions"
KIND_MISSING_BARS_FOR_SESSION = "missing_bars_for_session"
KIND_BARS_FOR_SESSION_DATE = "bars_for_session_date"
KIND_LATEST_COMPLETED_SESSION = "latest_completed_session"
KIND_PERSISTED_SESSION_DATES = "persisted_session_dates"


class ReadRecorder(Protocol):
    """Receives one call per recorded accessor read."""

    def record(self, kind: str, params: Mapping[str, Any], digest: str, count: int) -> None: ...


class BarLike(Protocol):
    session_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    vwap: Decimal | None
    trade_count: int | None
    adjusted: bool
    provider: str


_ACTIVE: ContextVar[ReadRecorder | None] = ContextVar("evaluation_read_recorder", default=None)


def active_recorder() -> ReadRecorder | None:
    return _ACTIVE.get()


@contextmanager
def recording(recorder: ReadRecorder) -> Iterator[ReadRecorder]:
    """Make *recorder* the active recorder for the enclosed reads."""

    token = _ACTIVE.set(recorder)
    try:
        yield recorder
    finally:
        _ACTIVE.reset(token)


@contextmanager
def suspended() -> Iterator[None]:
    """Hide the recorder from nested accessor calls (the outer request is the record)."""

    token = _ACTIVE.set(None)
    try:
        yield
    finally:
        _ACTIVE.reset(token)


# ---------------------------------------------------------------------------
# Canonical digests (pure)
# ---------------------------------------------------------------------------


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def sha256_hex(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


EMPTY_DIGEST = sha256_hex([])


def _decimal(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value.normalize(), "f")


def _bar_row(bar: BarLike) -> list[Any]:
    return [
        bar.session_date.isoformat(),
        _decimal(bar.open),
        _decimal(bar.high),
        _decimal(bar.low),
        _decimal(bar.close),
        int(bar.volume),
        _decimal(bar.vwap),
        None if bar.trade_count is None else int(bar.trade_count),
        bool(bar.adjusted),
        bar.provider,
    ]


def digest_bars(bars: Iterable[BarLike]) -> str:
    """Digest of a bar series, ascending by session date (accessor order never matters)."""

    ordered = sorted(bars, key=lambda bar: bar.session_date)
    return sha256_hex([_bar_row(bar) for bar in ordered])


def digest_bar_map(bars: Mapping[str, BarLike]) -> str:
    """Digest of ``{symbol: bar}`` (valuation reads), keyed by symbol."""

    return sha256_hex([[symbol, *_bar_row(bars[symbol])] for symbol in sorted(bars)])


def digest_dates(dates: Iterable[date]) -> str:
    return sha256_hex([item.isoformat() for item in sorted(dates)])


def digest_symbols(symbols: Iterable[str]) -> str:
    return sha256_hex(sorted(symbols))


def result_digest(kind: str, result: Any) -> str:
    """The digest of an accessor result, shared by recording and verification."""

    if kind == KIND_BARS_FOR_SESSIONS:
        return digest_bars(result)
    if kind == KIND_BARS_FOR_SESSION_DATE:
        return digest_bar_map(result)
    if kind == KIND_MISSING_BARS_FOR_SESSION:
        return digest_symbols(result)
    if kind == KIND_PERSISTED_SESSION_DATES:
        return digest_dates(result)
    if kind == KIND_LATEST_COMPLETED_SESSION:
        return digest_dates([] if result is None else [result])
    raise ValueError(f"Unknown read kind '{kind}'.")


def result_count(kind: str, result: Any) -> int:
    if kind == KIND_LATEST_COMPLETED_SESSION:
        return 0 if result is None else 1
    return len(result)
