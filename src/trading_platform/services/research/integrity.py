"""Data-integrity checks over research inputs (proposal Part J.2).

Run before a data freeze over the persisted rows for the requested assets and
range. Every ``error`` blocks the freeze; ``warning`` findings are listed. Codes are
a closed set (``IntegrityCode``). ``date_invalid`` is enforced at ingestion by the
adapter (ten-character ISO date, no timezone conversion) and therefore never appears
on persisted rows; it is kept in the enum so reports can state that.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_platform.db.models.daily_bar import DailyBar as DailyBarModel
from trading_platform.db.models.market_session import MarketSession
from trading_platform.db.models.symbol import Symbol

BIGINT_MAX = 9_223_372_036_854_775_807
EXTREME_MOVE = Decimal("0.5")
RATIO_TOLERANCE = Decimal("1e-9")


class IntegrityCode(StrEnum):
    DATE_INVALID = "date_invalid"
    DATE_NOT_SESSION = "date_not_session"
    ORDERING = "ordering"
    DUPLICATE_KEY = "duplicate_key"
    PRICE_NONFINITE = "price_nonfinite"
    PRICE_NONPOSITIVE = "price_nonpositive"
    OHLC_RELATION = "ohlc_relation"
    VOLUME_INVALID = "volume_invalid"
    MISSING_SESSION = "missing_session"
    PAIR_MISSING = "pair_missing"
    ADJUSTMENT_RATIO_INCONSISTENT = "adjustment_ratio_inconsistent"
    SOURCE_MIXED = "source_mixed"
    FACTOR_MISSING = "factor_missing"
    ASSET_ABSENT = "asset_absent"
    EXTREME_MOVE_ADJUSTED = "extreme_move_adjusted"
    EXTREME_MOVE_RAW_UNEXPLAINED = "extreme_move_raw_unexplained"
    ZERO_VOLUME_SESSION = "zero_volume_session"


WARNINGS = frozenset(
    {
        IntegrityCode.EXTREME_MOVE_ADJUSTED,
        IntegrityCode.EXTREME_MOVE_RAW_UNEXPLAINED,
        IntegrityCode.ZERO_VOLUME_SESSION,
    }
)


@dataclass(frozen=True)
class IntegrityFinding:
    code: IntegrityCode
    asset: str
    session_date: date | None = None
    detail: str = ""

    @property
    def severity(self) -> str:
        return "warning" if self.code in WARNINGS else "error"

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "severity": self.severity,
            "asset": self.asset,
            "session_date": self.session_date.isoformat() if self.session_date else None,
            "detail": self.detail,
        }


@dataclass
class IntegrityReport:
    provider: str
    assets: list[str]
    range_start: date
    range_end: date
    sessions_checked: int = 0
    findings: list[IntegrityFinding] = field(default_factory=list)

    @property
    def errors(self) -> list[IntegrityFinding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warnings(self) -> list[IntegrityFinding]:
        return [f for f in self.findings if f.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for finding in self.findings:
            counts[finding.code.value] = counts.get(finding.code.value, 0) + 1
        return dict(sorted(counts.items()))

    def summary(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "assets": list(self.assets),
            "range_start": self.range_start.isoformat(),
            "range_end": self.range_end.isoformat(),
            "sessions_checked": self.sessions_checked,
            "ok": self.ok,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "counts": self.counts(),
            "date_invalid": "enforced at ingestion; cannot occur on persisted rows",
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.summary(), "findings": [f.to_dict() for f in self.findings]}


def _finite_positive(value: Decimal | None) -> tuple[bool, bool]:
    """``(finite, positive)``."""

    if value is None or not value.is_finite():
        return False, False
    return True, value > 0


def _check_row(asset: str, bar: DailyBarModel, findings: list[IntegrityFinding]) -> bool:
    """Row-level price/volume checks; returns True when the row is numerically usable."""

    usable = True
    label = "adjusted" if bar.adjusted else "raw"
    for name in ("open", "high", "low", "close"):
        finite, positive = _finite_positive(getattr(bar, name))
        if not finite:
            findings.append(IntegrityFinding(IntegrityCode.PRICE_NONFINITE, asset, bar.session_date, f"{label} {name}"))
            usable = False
        elif not positive:
            findings.append(IntegrityFinding(IntegrityCode.PRICE_NONPOSITIVE, asset, bar.session_date, f"{label} {name}"))
            usable = False
    if usable:
        low, high = bar.low, bar.high
        if not (low <= min(bar.open, bar.close) and max(bar.open, bar.close) <= high):
            findings.append(IntegrityFinding(IntegrityCode.OHLC_RELATION, asset, bar.session_date, label))
    volume = bar.volume
    if volume is None or volume < 0 or volume > BIGINT_MAX:
        findings.append(IntegrityFinding(IntegrityCode.VOLUME_INVALID, asset, bar.session_date, label))
    elif volume == 0:
        findings.append(IntegrityFinding(IntegrityCode.ZERO_VOLUME_SESSION, asset, bar.session_date, label))
    return usable


class BarRowLike(Protocol):
    session_date: date
    adjusted: bool
    provider: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    split_factor: Decimal | None
    dividend_cash: Decimal | None


def check_asset_rows(
    asset: str,
    session_dates: list[date],
    rows: list[Any],
    *,
    provider: str,
    today: date | None = None,
) -> list[IntegrityFinding]:
    """Pure row-level checks for one asset (rows may carry any provider)."""

    findings: list[IntegrityFinding] = []
    session_set = set(session_dates)
    today = today or date.today()
    other_providers = sorted({b.provider for b in rows if b.provider != provider})
    if other_providers:
        findings.append(IntegrityFinding(IntegrityCode.SOURCE_MIXED, asset, None, ",".join(other_providers)))
    bars = [b for b in rows if b.provider == provider]
    if not bars:
        findings.append(IntegrityFinding(IntegrityCode.ASSET_ABSENT, asset, None, "no bars in range"))
        return findings

    raw: dict[date, Any] = {}
    adjusted: dict[date, Any] = {}
    seen: set[tuple[date, bool]] = set()
    for bar in bars:
        key = (bar.session_date, bar.adjusted)
        if key in seen:
            findings.append(IntegrityFinding(IntegrityCode.DUPLICATE_KEY, asset, bar.session_date))
            continue
        seen.add(key)
        (adjusted if bar.adjusted else raw)[bar.session_date] = bar
        if bar.session_date not in session_set:
            findings.append(IntegrityFinding(IntegrityCode.DATE_NOT_SESSION, asset, bar.session_date))
        if bar.session_date > today:
            findings.append(IntegrityFinding(IntegrityCode.ORDERING, asset, bar.session_date, "future date"))

    for session_date in session_dates:
        r, a = raw.get(session_date), adjusted.get(session_date)
        if r is None and a is None:
            findings.append(IntegrityFinding(IntegrityCode.MISSING_SESSION, asset, session_date))
            continue
        if r is None or a is None:
            findings.append(
                IntegrityFinding(IntegrityCode.PAIR_MISSING, asset, session_date, "raw" if r is None else "adjusted")
            )
        r_ok = _check_row(asset, r, findings) if r is not None else False
        a_ok = _check_row(asset, a, findings) if a is not None else False
        if r is not None and (r.split_factor is None or r.dividend_cash is None):
            findings.append(IntegrityFinding(IntegrityCode.FACTOR_MISSING, asset, session_date))
        if r_ok and a_ok and r is not None and a is not None:
            ratios = [a.open / r.open, a.high / r.high, a.low / r.low, a.close / r.close]
            base = ratios[-1]
            if any(abs(ratio / base - 1) > RATIO_TOLERANCE for ratio in ratios):
                findings.append(IntegrityFinding(IntegrityCode.ADJUSTMENT_RATIO_INCONSISTENT, asset, session_date))

    def _moves(series: dict[date, Any], code: IntegrityCode, explained_by_split: bool) -> None:
        prev: Any | None = None
        for session_date in sorted(series):
            bar = series[session_date]
            if (
                prev is not None
                and prev.close.is_finite()
                and bar.close.is_finite()
                and prev.close > 0
                and bar.close > 0
            ):
                move = abs(bar.close / prev.close - 1)
                if move > EXTREME_MOVE:
                    split = bar.split_factor if bar.split_factor is not None else Decimal(1)
                    if not (explained_by_split and split != 1):
                        findings.append(IntegrityFinding(code, asset, session_date, f"move={move:.4f}"))
            prev = bar

    _moves(adjusted, IntegrityCode.EXTREME_MOVE_ADJUSTED, explained_by_split=False)
    _moves(raw, IntegrityCode.EXTREME_MOVE_RAW_UNEXPLAINED, explained_by_split=True)
    return findings


def check_research_inputs(
    session: Session,
    *,
    assets: list[str],
    range_start: date,
    range_end: date,
    provider: str,
    exchange: str = "XNYS",
    today: date | None = None,
) -> IntegrityReport:
    report = IntegrityReport(provider=provider, assets=list(assets), range_start=range_start, range_end=range_end)
    session_dates = list(
        session.execute(
            select(MarketSession.session_date)
            .where(MarketSession.exchange == exchange)
            .where(MarketSession.session_date >= range_start)
            .where(MarketSession.session_date <= range_end)
            .order_by(MarketSession.session_date.asc())
        ).scalars()
    )
    report.sessions_checked = len(session_dates)
    for asset in assets:
        symbol = session.execute(select(Symbol).where(Symbol.ticker == asset)).scalar_one_or_none()
        if symbol is None:
            report.findings.append(IntegrityFinding(IntegrityCode.ASSET_ABSENT, asset, None, "no symbol row"))
            continue
        rows = list(
            session.execute(
                select(DailyBarModel)
                .where(DailyBarModel.symbol_id == symbol.id)
                .where(DailyBarModel.session_date >= range_start)
                .where(DailyBarModel.session_date <= range_end)
                .order_by(DailyBarModel.session_date.asc(), DailyBarModel.adjusted.asc())
            ).scalars()
        )
        report.findings.extend(check_asset_rows(asset, session_dates, rows, provider=provider, today=today))
    return report
