"""Calendar facts (COR-04, D-22/D-23/D-24; F-4, G-2).

Three SEPARATE closed facts, each with an explicit ``unknown(calendar_data_unavailable)``,
all computed from the PERSISTED ``market_sessions`` open/close (early closes respected):

* ``trading_day``        -> known(date, phase in {pre_open, open, after_close, non_trading_day}) | unknown
* ``evaluation_session`` -> ready(S) | not_ready(S, reason in {missing_bars, insufficient_history,
                            calendar_gap}) | unknown
* ``execution_window``   -> open(until) | closed(next_opens_at) | unknown

Statement bound (review round 2): ``trading_day``, the evaluation-session candidate,
``execution_window`` and the runway count are all pure functions over ONE
``CalendarWindow`` loaded by ``load_calendar_window`` with exactly ONE statement
whatever the date and horizon. Per-symbol bar/history readiness is a pure function
over rows the caller supplies (``readiness_from_rows``), so operator reads can feed it
from their own single universe statements; ``session_readiness`` is the convenience
loader (two statements, independent of the symbol count).

The execution policy ``regular_hours_prev_session_v1`` is a named, versioned SETTING
(not an invariant): the window is regular hours of ``next_session(S)`` from the open until
``execution_window_cutoff_minutes`` before the close.

Read-only: nothing in this module flushes or writes. Services layer: no ``jobs/`` import.
A manifest check for paper execution (20.1-06) is added to eligibility by that plan.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from trading_platform.core.settings import Settings
from trading_platform.db.models.market_session import MarketSession
from trading_platform.services.calendar import (
    CalendarOutOfBoundsError,
    calendar_horizon_end,
    get_calendar,
    is_trading_session,
    next_session_date,
    previous_session_date,
)
from trading_platform.services.market_data_access import (
    PersistedSessionRow,
    bar_counts_through_session,
    missing_bars_for_session,
    persisted_session_row,
)

PAPER_EXECUTION_POLICY = "regular_hours_prev_session_v1"

# Sessions of look-back loaded around today (covers every holiday gap).
WINDOW_LOOKBACK_DAYS = 14
# Sessions ahead always loaded (next open after the window needs N+1).
WINDOW_MIN_SESSIONS_AHEAD = 3


class UnsupportedExecutionPolicyError(ValueError):
    """The configured execution policy name is not implemented."""


class FactStatus(StrEnum):
    KNOWN = "known"
    UNKNOWN = "unknown"


class UnknownReason(StrEnum):
    CALENDAR_DATA_UNAVAILABLE = "calendar_data_unavailable"


class TradingDayPhase(StrEnum):
    PRE_OPEN = "pre_open"
    OPEN = "open"
    AFTER_CLOSE = "after_close"
    NON_TRADING_DAY = "non_trading_day"


class EvaluationStatus(StrEnum):
    READY = "ready"
    NOT_READY = "not_ready"
    UNKNOWN = "unknown"


class EvaluationNotReadyReason(StrEnum):
    MISSING_BARS = "missing_bars"
    INSUFFICIENT_HISTORY = "insufficient_history"
    CALENDAR_GAP = "calendar_gap"


class WindowStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    UNKNOWN = "unknown"


class WindowClosedReason(StrEnum):
    NOT_YET_OPEN = "not_yet_open"
    ELAPSED = "elapsed"


class EligibilityRejection(StrEnum):
    """Closed submit-time rejection set, in precedence order (highest first)."""

    CALENDAR_DATA_UNAVAILABLE = "calendar_data_unavailable"
    HISTORICAL_EXECUTION_REJECTED = "historical_execution_rejected"
    EVALUATION_DATA_NOT_READY = "evaluation_data_not_ready"
    OUTSIDE_EXECUTION_WINDOW = "outside_execution_window"


class _StrategyMetadataLike(Protocol):
    @property
    def universe(self) -> tuple[str, ...]: ...


class StrategyLike(Protocol):
    """The slice of ``BaseStrategy`` the facts read."""

    @property
    def metadata(self) -> _StrategyMetadataLike: ...

    @property
    def warmup_periods(self) -> int: ...


def _require_aware_utc(now: datetime) -> datetime:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be a timezone-aware datetime (UTC)")
    return now.astimezone(UTC)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(UTC).isoformat()


# ---------------------------------------------------------------------------
# The ONE persisted statement
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CalendarWindow:
    """Persisted calendar facts around ``now``, loaded with ONE statement."""

    exchange: str
    now: datetime
    today: date
    rows: Mapping[date, PersistedSessionRow]
    last_persisted_session: date | None
    runway_sessions: int
    horizon_sessions: int
    runway_low_threshold: int
    library_ok: bool


def exchange_local_date(now: datetime, exchange: str) -> date:
    return now.astimezone(get_calendar(exchange).tz).date()


def load_calendar_window(session: Session, *, now: datetime, settings: Settings) -> CalendarWindow:
    """Load every persisted row the facts need with exactly ONE SELECT.

    The statement left-joins the bounded window rows (a few sessions before
    exchange-local today through the coverage horizon) onto a one-row aggregate of
    the whole exchange calendar (last persisted session, runway count), so an empty
    window (a stale calendar) still returns the aggregate.
    """

    now = _require_aware_utc(now)
    calendar_settings = settings.market_data.calendar
    exchange = calendar_settings.exchange
    today = exchange_local_date(now, exchange)

    library_ok = True
    try:
        window_end = calendar_horizon_end(
            settings, today, minimum_sessions=WINDOW_MIN_SESSIONS_AHEAD
        )
    except CalendarOutOfBoundsError:
        library_ok = False
        window_end = today + timedelta(days=WINDOW_LOOKBACK_DAYS)
    window_start = today - timedelta(days=WINDOW_LOOKBACK_DAYS)

    aggregate = (
        select(
            func.max(MarketSession.session_date).label("last_persisted"),
            func.count().filter(MarketSession.session_date > today).label("runway"),
        )
        .where(MarketSession.exchange == exchange)
        .subquery("calendar_aggregate")
    )
    statement = (
        select(
            aggregate.c.last_persisted,
            aggregate.c.runway,
            MarketSession.session_date,
            MarketSession.market_open,
            MarketSession.market_close,
            MarketSession.early_close,
        )
        .select_from(aggregate)
        .outerjoin(
            MarketSession,
            and_(
                MarketSession.exchange == exchange,
                MarketSession.session_date >= window_start,
                MarketSession.session_date <= window_end,
            ),
        )
        .order_by(MarketSession.session_date.asc())
    )
    result = session.execute(statement).all()

    rows: dict[date, PersistedSessionRow] = {}
    last_persisted: date | None = None
    runway = 0
    for record in result:
        last_persisted = record.last_persisted
        runway = int(record.runway or 0)
        if record.session_date is not None:
            rows[record.session_date] = PersistedSessionRow(
                session_date=record.session_date,
                market_open=record.market_open,
                market_close=record.market_close,
                early_close=bool(record.early_close),
            )

    return CalendarWindow(
        exchange=exchange,
        now=now,
        today=today,
        rows=rows,
        last_persisted_session=last_persisted,
        runway_sessions=runway,
        horizon_sessions=calendar_settings.coverage_horizon_sessions,
        runway_low_threshold=calendar_settings.runway_low_sessions,
        library_ok=library_ok,
    )


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TradingDay:
    status: FactStatus
    date: date | None = None
    phase: TradingDayPhase | None = None
    market_open: datetime | None = None
    market_close: datetime | None = None
    reason: UnknownReason | None = None

    def to_dict(self) -> dict[str, Any]:
        if self.status is FactStatus.UNKNOWN:
            return {"status": self.status.value, "reason": self.reason.value if self.reason else None}
        return {
            "status": self.status.value,
            "date": self.date.isoformat() if self.date else None,
            "phase": self.phase.value if self.phase else None,
            "market_open": _iso(self.market_open),
            "market_close": _iso(self.market_close),
        }


_UNKNOWN_TRADING_DAY = TradingDay(
    status=FactStatus.UNKNOWN, reason=UnknownReason.CALENDAR_DATA_UNAVAILABLE
)


@dataclass(frozen=True)
class SymbolBarReadiness:
    """Per-symbol bar/history flags for one evaluation session."""

    symbol: str
    has_bar: bool
    bars_through_session: int
    history_sufficient: bool


@dataclass(frozen=True)
class EvaluationSession:
    status: EvaluationStatus
    session_date: date | None = None
    reason: EvaluationNotReadyReason | UnknownReason | None = None
    symbols: tuple[str, ...] = ()
    symbol_readiness: tuple[SymbolBarReadiness, ...] = field(default=())

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "session_date": self.session_date.isoformat() if self.session_date else None,
            "reason": self.reason.value if self.reason else None,
            "symbols": list(self.symbols),
        }


_UNKNOWN_EVALUATION = EvaluationSession(
    status=EvaluationStatus.UNKNOWN, reason=UnknownReason.CALENDAR_DATA_UNAVAILABLE
)


@dataclass(frozen=True)
class ExecutionWindow:
    status: WindowStatus
    evaluation_session_date: date | None = None
    execution_session_date: date | None = None
    opens_at: datetime | None = None
    until: datetime | None = None
    next_opens_at: datetime | None = None
    closed_reason: WindowClosedReason | None = None
    reason: UnknownReason | None = None

    def to_dict(self) -> dict[str, Any]:
        if self.status is WindowStatus.UNKNOWN:
            return {"status": self.status.value, "reason": self.reason.value if self.reason else None}
        payload: dict[str, Any] = {
            "status": self.status.value,
            "evaluation_session_date": (
                self.evaluation_session_date.isoformat() if self.evaluation_session_date else None
            ),
            "execution_session_date": (
                self.execution_session_date.isoformat() if self.execution_session_date else None
            ),
            "opens_at": _iso(self.opens_at),
            "until": _iso(self.until),
        }
        if self.status is WindowStatus.CLOSED:
            payload["next_opens_at"] = _iso(self.next_opens_at)
            payload["closed_reason"] = self.closed_reason.value if self.closed_reason else None
        return payload


_UNKNOWN_WINDOW = ExecutionWindow(
    status=WindowStatus.UNKNOWN, reason=UnknownReason.CALENDAR_DATA_UNAVAILABLE
)


@dataclass(frozen=True)
class CalendarCoverage:
    last_persisted_session: date | None
    runway_sessions: int
    runway_low: bool
    horizon_sessions: int
    runway_low_threshold: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "last_persisted_session": (
                self.last_persisted_session.isoformat() if self.last_persisted_session else None
            ),
            "runway_sessions": self.runway_sessions,
            "runway_low": self.runway_low,
            "horizon_sessions": self.horizon_sessions,
            "runway_low_threshold": self.runway_low_threshold,
        }


@dataclass(frozen=True)
class EligibilityResult:
    rejection: EligibilityRejection | None
    trading_day: TradingDay
    evaluation_session: EvaluationSession
    window: ExecutionWindow

    @property
    def eligible(self) -> bool:
        return self.rejection is None


# ---------------------------------------------------------------------------
# Pure facts over the window
# ---------------------------------------------------------------------------


def _complete_row(window: CalendarWindow, session_date: date) -> PersistedSessionRow | None:
    row = window.rows.get(session_date)
    if row is None or row.market_open is None or row.market_close is None:
        return None
    return row


def trading_day_from_window(window: CalendarWindow) -> TradingDay:
    """Trading day: the exchange-local date of ``now`` is a session -> its phase;
    otherwise the next session with phase ``non_trading_day``. A needed persisted
    row missing (or a library bound) -> ``unknown(calendar_data_unavailable)``."""

    try:
        is_session = is_trading_session(window.today, window.exchange)
        target = window.today if is_session else next_session_date(window.today, window.exchange)
    except (CalendarOutOfBoundsError, ValueError, OverflowError):
        return _UNKNOWN_TRADING_DAY
    row = _complete_row(window, target)
    if row is None:
        return _UNKNOWN_TRADING_DAY
    assert row.market_open is not None and row.market_close is not None
    market_open = _aware(row.market_open)
    market_close = _aware(row.market_close)
    if not is_session:
        phase = TradingDayPhase.NON_TRADING_DAY
    elif window.now < market_open:
        phase = TradingDayPhase.PRE_OPEN
    elif window.now < market_close:
        phase = TradingDayPhase.OPEN
    else:
        phase = TradingDayPhase.AFTER_CLOSE
    return TradingDay(
        status=FactStatus.KNOWN,
        date=target,
        phase=phase,
        market_open=market_open,
        market_close=market_close,
    )


def evaluation_candidate_from_window(window: CalendarWindow) -> date | UnknownReason:
    """Calendar-only candidate: the latest session whose persisted close is at or
    before ``now`` (never "latest session with bars"). Unknown unless the trading
    day is known and the candidate's persisted row exists."""

    day = trading_day_from_window(window)
    if day.status is FactStatus.UNKNOWN:
        return UnknownReason.CALENDAR_DATA_UNAVAILABLE
    try:
        if day.phase is TradingDayPhase.AFTER_CLOSE:
            candidate = window.today
        else:
            candidate = previous_session_date(window.today, window.exchange)
    except CalendarOutOfBoundsError:
        return UnknownReason.CALENDAR_DATA_UNAVAILABLE
    row = _complete_row(window, candidate)
    if row is None:
        return UnknownReason.CALENDAR_DATA_UNAVAILABLE
    assert row.market_close is not None
    if _aware(row.market_close) > window.now:
        return UnknownReason.CALENDAR_DATA_UNAVAILABLE
    return candidate


def execution_window_from_window(
    window: CalendarWindow,
    settings: Settings,
    evaluation_session_date: date | None,
) -> ExecutionWindow:
    """Policy ``regular_hours_prev_session_v1``: regular hours of
    ``next_session(S)`` from its persisted open until (persisted close - cutoff)."""

    if settings.execution.execution_policy != PAPER_EXECUTION_POLICY:
        raise UnsupportedExecutionPolicyError(settings.execution.execution_policy)
    if evaluation_session_date is None:
        return _UNKNOWN_WINDOW
    try:
        execution_date = next_session_date(evaluation_session_date, window.exchange)
    except CalendarOutOfBoundsError:
        return _UNKNOWN_WINDOW
    row = _complete_row(window, execution_date)
    if row is None:
        return _UNKNOWN_WINDOW
    assert row.market_open is not None and row.market_close is not None
    opens_at = _aware(row.market_open)
    until = _aware(row.market_close) - timedelta(
        minutes=settings.execution.execution_window_cutoff_minutes
    )
    if opens_at <= window.now < until:
        return ExecutionWindow(
            status=WindowStatus.OPEN,
            evaluation_session_date=evaluation_session_date,
            execution_session_date=execution_date,
            opens_at=opens_at,
            until=until,
        )
    if window.now < opens_at:
        return ExecutionWindow(
            status=WindowStatus.CLOSED,
            evaluation_session_date=evaluation_session_date,
            execution_session_date=execution_date,
            opens_at=opens_at,
            until=until,
            next_opens_at=opens_at,
            closed_reason=WindowClosedReason.NOT_YET_OPEN,
        )
    # Elapsed: the next regular open strictly after now is the following session's.
    try:
        following = next_session_date(execution_date, window.exchange)
    except CalendarOutOfBoundsError:
        return _UNKNOWN_WINDOW
    following_row = _complete_row(window, following)
    if following_row is None:
        return _UNKNOWN_WINDOW
    assert following_row.market_open is not None
    return ExecutionWindow(
        status=WindowStatus.CLOSED,
        evaluation_session_date=evaluation_session_date,
        execution_session_date=execution_date,
        opens_at=opens_at,
        until=until,
        next_opens_at=_aware(following_row.market_open),
        closed_reason=WindowClosedReason.ELAPSED,
    )


def coverage_from_window(window: CalendarWindow) -> CalendarCoverage:
    return CalendarCoverage(
        last_persisted_session=window.last_persisted_session,
        runway_sessions=window.runway_sessions,
        runway_low=window.runway_sessions < window.runway_low_threshold,
        horizon_sessions=window.horizon_sessions,
        runway_low_threshold=window.runway_low_threshold,
    )


@dataclass(frozen=True)
class CalendarFacts:
    """Everything derivable from the ONE window statement."""

    window: CalendarWindow
    trading_day: TradingDay
    evaluation_candidate: date | UnknownReason
    execution_window: ExecutionWindow
    coverage: CalendarCoverage


def calendar_facts(session: Session, *, now: datetime, settings: Settings) -> CalendarFacts:
    """Trading day, evaluation candidate, execution window and coverage: ONE statement."""

    window = load_calendar_window(session, now=now, settings=settings)
    candidate = evaluation_candidate_from_window(window)
    return CalendarFacts(
        window=window,
        trading_day=trading_day_from_window(window),
        evaluation_candidate=candidate,
        execution_window=execution_window_from_window(
            window, settings, candidate if isinstance(candidate, date) else None
        ),
        coverage=coverage_from_window(window),
    )


# ---------------------------------------------------------------------------
# Session-taking wrappers (each loads the window once)
# ---------------------------------------------------------------------------


def trading_day(session: Session, *, now: datetime, settings: Settings) -> TradingDay:
    return trading_day_from_window(load_calendar_window(session, now=now, settings=settings))


def evaluation_candidate_session(
    session: Session, *, now: datetime, settings: Settings
) -> date | UnknownReason:
    return evaluation_candidate_from_window(
        load_calendar_window(session, now=now, settings=settings)
    )


def execution_window(
    session: Session,
    *,
    now: datetime,
    settings: Settings,
    evaluation_session_date: date | None,
) -> ExecutionWindow:
    return execution_window_from_window(
        load_calendar_window(session, now=now, settings=settings),
        settings,
        evaluation_session_date,
    )


def calendar_coverage(session: Session, *, now: datetime, settings: Settings) -> CalendarCoverage:
    return coverage_from_window(load_calendar_window(session, now=now, settings=settings))


# ---------------------------------------------------------------------------
# Evaluation-session readiness (bar / history rule; never clock-dependent)
# ---------------------------------------------------------------------------


def readiness_from_rows(
    *,
    session_date: date,
    row_present: bool,
    universe: Iterable[str],
    missing_symbols: Iterable[str],
    bar_counts: Mapping[str, int],
    warmup_periods: int,
) -> EvaluationSession:
    """Pure decision. Precedence: calendar_gap > missing_bars > insufficient_history > ready.

    ``missing_symbols`` are universe symbols with no bar ON ``session_date``;
    ``bar_counts`` maps symbol -> bars on or before it. Callers with their own
    single universe statement supply these rows directly.
    """

    tickers = tuple(sorted(dict.fromkeys(universe)))
    missing = frozenset(missing_symbols)
    per_symbol = tuple(
        SymbolBarReadiness(
            symbol=ticker,
            has_bar=ticker not in missing,
            bars_through_session=int(bar_counts.get(ticker, 0)),
            history_sufficient=int(bar_counts.get(ticker, 0)) >= warmup_periods,
        )
        for ticker in tickers
    )
    if not row_present:
        return EvaluationSession(
            status=EvaluationStatus.NOT_READY,
            session_date=session_date,
            reason=EvaluationNotReadyReason.CALENDAR_GAP,
            symbols=(),
            symbol_readiness=per_symbol,
        )
    missing_bars = tuple(item.symbol for item in per_symbol if not item.has_bar)
    if missing_bars:
        return EvaluationSession(
            status=EvaluationStatus.NOT_READY,
            session_date=session_date,
            reason=EvaluationNotReadyReason.MISSING_BARS,
            symbols=missing_bars,
            symbol_readiness=per_symbol,
        )
    short = tuple(item.symbol for item in per_symbol if not item.history_sufficient)
    if short:
        return EvaluationSession(
            status=EvaluationStatus.NOT_READY,
            session_date=session_date,
            reason=EvaluationNotReadyReason.INSUFFICIENT_HISTORY,
            symbols=short,
            symbol_readiness=per_symbol,
        )
    return EvaluationSession(
        status=EvaluationStatus.READY,
        session_date=session_date,
        reason=None,
        symbols=(),
        symbol_readiness=per_symbol,
    )


def session_readiness(
    session: Session,
    *,
    settings: Settings,
    strategy: StrategyLike,
    session_date: date,
    row_present: bool | None = None,
) -> EvaluationSession:
    """Load the rows (persisted row unless supplied, missing bars, bar counts:
    at most three statements, independent of the symbol count) and decide."""

    exchange = settings.market_data.calendar.exchange
    if row_present is None:
        row_present = persisted_session_row(session, session_date, exchange) is not None
    universe = tuple(strategy.metadata.universe)
    missing = missing_bars_for_session(session, session_date, symbols=universe)
    counts = bar_counts_through_session(session, universe, session_date)
    return readiness_from_rows(
        session_date=session_date,
        row_present=row_present,
        universe=universe,
        missing_symbols=missing,
        bar_counts=counts,
        warmup_periods=int(strategy.warmup_periods),
    )


def evaluation_session(
    session: Session,
    *,
    now: datetime,
    settings: Settings,
    strategy: StrategyLike,
    window: CalendarWindow | None = None,
) -> EvaluationSession:
    """ready(S) | not_ready(S, reason) | unknown, for the evaluation candidate S."""

    resolved = window or load_calendar_window(session, now=now, settings=settings)
    candidate = evaluation_candidate_from_window(resolved)
    if not isinstance(candidate, date):
        return _UNKNOWN_EVALUATION
    return session_readiness(
        session,
        settings=settings,
        strategy=strategy,
        session_date=candidate,
        row_present=candidate in resolved.rows,
    )


# ---------------------------------------------------------------------------
# Paper execution eligibility (D-23)
# ---------------------------------------------------------------------------


def paper_execution_eligibility(
    session: Session,
    *,
    now: datetime,
    settings: Settings,
    strategy: StrategyLike,
    as_of_session: date,
) -> EligibilityResult:
    """Pure-read eligibility of ``paper-session`` for ``as_of_session`` at ``now``.

    Fixed precedence: calendar_data_unavailable > historical_execution_rejected >
    evaluation_data_not_ready > outside_execution_window. The evaluation session
    must equal ``previous_session(trading day)``, be ready, and ``now`` must be
    inside its window. (The manifest check is added by 20.1-06.)
    """

    window = load_calendar_window(session, now=now, settings=settings)
    day = trading_day_from_window(window)
    if day.status is FactStatus.UNKNOWN or day.date is None:
        return EligibilityResult(
            EligibilityRejection.CALENDAR_DATA_UNAVAILABLE,
            day,
            _UNKNOWN_EVALUATION,
            _UNKNOWN_WINDOW,
        )
    try:
        expected = previous_session_date(day.date, window.exchange)
    except CalendarOutOfBoundsError:
        return EligibilityResult(
            EligibilityRejection.CALENDAR_DATA_UNAVAILABLE,
            day,
            _UNKNOWN_EVALUATION,
            _UNKNOWN_WINDOW,
        )
    if as_of_session != expected:
        return EligibilityResult(
            EligibilityRejection.HISTORICAL_EXECUTION_REJECTED,
            day,
            _UNKNOWN_EVALUATION,
            _UNKNOWN_WINDOW,
        )
    evaluation = session_readiness(
        session,
        settings=settings,
        strategy=strategy,
        session_date=as_of_session,
        row_present=as_of_session in window.rows,
    )
    exec_window = execution_window_from_window(window, settings, as_of_session)
    if evaluation.status is not EvaluationStatus.READY:
        return EligibilityResult(
            EligibilityRejection.EVALUATION_DATA_NOT_READY, day, evaluation, exec_window
        )
    if exec_window.status is WindowStatus.UNKNOWN:
        return EligibilityResult(
            EligibilityRejection.CALENDAR_DATA_UNAVAILABLE, day, evaluation, exec_window
        )
    if day.phase is not TradingDayPhase.OPEN or exec_window.status is not WindowStatus.OPEN:
        return EligibilityResult(
            EligibilityRejection.OUTSIDE_EXECUTION_WINDOW, day, evaluation, exec_window
        )
    return EligibilityResult(None, day, evaluation, exec_window)


__all__ = [
    "PAPER_EXECUTION_POLICY",
    "CalendarCoverage",
    "CalendarFacts",
    "CalendarWindow",
    "EligibilityRejection",
    "EligibilityResult",
    "EvaluationNotReadyReason",
    "EvaluationSession",
    "EvaluationStatus",
    "ExecutionWindow",
    "FactStatus",
    "StrategyLike",
    "SymbolBarReadiness",
    "TradingDay",
    "TradingDayPhase",
    "UnknownReason",
    "UnsupportedExecutionPolicyError",
    "WindowClosedReason",
    "WindowStatus",
    "calendar_coverage",
    "calendar_facts",
    "coverage_from_window",
    "evaluation_candidate_from_window",
    "evaluation_candidate_session",
    "evaluation_session",
    "exchange_local_date",
    "execution_window",
    "execution_window_from_window",
    "load_calendar_window",
    "paper_execution_eligibility",
    "readiness_from_rows",
    "session_readiness",
    "trading_day",
    "trading_day_from_window",
]
