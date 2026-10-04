"""Calendar facts (COR-04, D-22/D-23/D-24): trading day, evaluation session and
execution window as three closed facts computed from the persisted calendar.

Clock-injected (``et(...)`` builds exchange-local instants) over real XNYS sessions:
normal Tuesday 2025-12-02, early close 2025-11-28 (13:00 ET), holiday 2025-11-27,
weekend 2025-11-29.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from tests.support.calendar_facts import (
    FakeStrategy,
    et,
    seed_bars,
    seed_calendar,
    sessions_between,
)
from tests.support.migrated_db import migrated_database
from tests.support.query_counter import count_queries

from trading_platform.core.settings import Settings, load_settings
from trading_platform.db.session import session_scope
from trading_platform.services import calendar_facts as facts
from trading_platform.services.calendar import (
    CalendarOutOfBoundsError,
    calendar_horizon_end,
    get_calendar,
    next_session_date,
    previous_session_date,
)
from trading_platform.services.market_data_access import (
    bar_counts_through_session,
    persisted_session_row,
    persisted_sessions_after,
)


@pytest.fixture()
def facts_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    with migrated_database(monkeypatch, "calfacts") as name:
        yield name


@pytest.fixture()
def seeded(facts_db: str) -> Settings:
    seed_calendar(date(2025, 11, 1), date(2026, 3, 31))
    return load_settings()


def _with_calendar(settings: Settings, **updates: Any) -> Settings:
    calendar = settings.market_data.calendar.model_copy(update=updates)
    market_data = settings.market_data.model_copy(update={"calendar": calendar})
    return settings.model_copy(update={"market_data": market_data})


def _with_execution(settings: Settings, **updates: Any) -> Settings:
    return settings.model_copy(update={"execution": settings.execution.model_copy(update=updates)})


def _facts_at(settings: Settings, now: datetime) -> facts.CalendarFacts:
    with session_scope(settings) as session:
        return facts.calendar_facts(session, now=now, settings=settings)


# ---------------------------------------------------------------------------
# Closed enums
# ---------------------------------------------------------------------------


def test_closed_enums_have_exact_member_sets() -> None:
    assert {m.value for m in facts.FactStatus} == {"known", "unknown"}
    assert {m.value for m in facts.UnknownReason} == {"calendar_data_unavailable"}
    assert {m.value for m in facts.TradingDayPhase} == {
        "pre_open",
        "open",
        "after_close",
        "non_trading_day",
    }
    assert {m.value for m in facts.EvaluationStatus} == {"ready", "not_ready", "unknown"}
    assert {m.value for m in facts.EvaluationNotReadyReason} == {
        "missing_bars",
        "insufficient_history",
        "calendar_gap",
    }
    assert {m.value for m in facts.WindowStatus} == {"open", "closed", "unknown"}
    assert {m.value for m in facts.WindowClosedReason} == {"not_yet_open", "elapsed"}
    assert {m.value for m in facts.EligibilityRejection} == {
        "calendar_data_unavailable",
        "historical_execution_rejected",
        "evaluation_data_not_ready",
        "outside_execution_window",
    }


# ---------------------------------------------------------------------------
# Trading day / candidate / window across pre-open, open, cutoff, after close,
# weekend, holiday and early close
# ---------------------------------------------------------------------------

_D = date

CASES: list[tuple[str, datetime, date, facts.TradingDayPhase, date, facts.WindowStatus, Any, Any]] = [
    # id, now, trading-day date, phase, candidate S, window status, closed reason, until (ET hh:mm)
    (
        "pre_open",
        et(2025, 12, 2, 8, 0),
        _D(2025, 12, 2),
        facts.TradingDayPhase.PRE_OPEN,
        _D(2025, 12, 1),
        facts.WindowStatus.CLOSED,
        facts.WindowClosedReason.NOT_YET_OPEN,
        (15, 45),
    ),
    (
        "open",
        et(2025, 12, 2, 10, 0),
        _D(2025, 12, 2),
        facts.TradingDayPhase.OPEN,
        _D(2025, 12, 1),
        facts.WindowStatus.OPEN,
        None,
        (15, 45),
    ),
    (
        "one_second_before_cutoff",
        et(2025, 12, 2, 15, 44, 59),
        _D(2025, 12, 2),
        facts.TradingDayPhase.OPEN,
        _D(2025, 12, 1),
        facts.WindowStatus.OPEN,
        None,
        (15, 45),
    ),
    (
        "at_cutoff",
        et(2025, 12, 2, 15, 45, 0),
        _D(2025, 12, 2),
        facts.TradingDayPhase.OPEN,
        _D(2025, 12, 1),
        facts.WindowStatus.CLOSED,
        facts.WindowClosedReason.ELAPSED,
        (15, 45),
    ),
    (
        "after_close",
        et(2025, 12, 2, 16, 30),
        _D(2025, 12, 2),
        facts.TradingDayPhase.AFTER_CLOSE,
        _D(2025, 12, 2),
        facts.WindowStatus.CLOSED,
        facts.WindowClosedReason.NOT_YET_OPEN,
        (15, 45),
    ),
    (
        "weekend",
        et(2025, 11, 29, 12, 0),
        _D(2025, 12, 1),
        facts.TradingDayPhase.NON_TRADING_DAY,
        _D(2025, 11, 28),
        facts.WindowStatus.CLOSED,
        facts.WindowClosedReason.NOT_YET_OPEN,
        (15, 45),
    ),
    (
        "holiday",
        et(2025, 11, 27, 10, 0),
        _D(2025, 11, 28),
        facts.TradingDayPhase.NON_TRADING_DAY,
        _D(2025, 11, 26),
        facts.WindowStatus.CLOSED,
        facts.WindowClosedReason.NOT_YET_OPEN,
        (12, 45),
    ),
    (
        "early_close_open",
        et(2025, 11, 28, 10, 0),
        _D(2025, 11, 28),
        facts.TradingDayPhase.OPEN,
        _D(2025, 11, 26),
        facts.WindowStatus.OPEN,
        None,
        (12, 45),
    ),
    (
        "early_close_one_second_before_cutoff",
        et(2025, 11, 28, 12, 44, 59),
        _D(2025, 11, 28),
        facts.TradingDayPhase.OPEN,
        _D(2025, 11, 26),
        facts.WindowStatus.OPEN,
        None,
        (12, 45),
    ),
    (
        "early_close_after_cutoff",
        et(2025, 11, 28, 12, 46),
        _D(2025, 11, 28),
        facts.TradingDayPhase.OPEN,
        _D(2025, 11, 26),
        facts.WindowStatus.CLOSED,
        facts.WindowClosedReason.ELAPSED,
        (12, 45),
    ),
    (
        "early_close_after_close",
        et(2025, 11, 28, 13, 0),
        _D(2025, 11, 28),
        facts.TradingDayPhase.AFTER_CLOSE,
        _D(2025, 11, 28),
        facts.WindowStatus.CLOSED,
        facts.WindowClosedReason.NOT_YET_OPEN,
        (15, 45),
    ),
]


@pytest.mark.parametrize(
    ("label", "now", "day_date", "phase", "candidate", "window_status", "closed_reason", "until_et"),
    CASES,
    ids=[case[0] for case in CASES],
)
def test_three_facts_across_clock_table(
    seeded: Settings,
    label: str,
    now: datetime,
    day_date: date,
    phase: facts.TradingDayPhase,
    candidate: date,
    window_status: facts.WindowStatus,
    closed_reason: facts.WindowClosedReason | None,
    until_et: tuple[int, int],
) -> None:
    result = _facts_at(seeded, now)

    assert result.trading_day.status is facts.FactStatus.KNOWN
    assert result.trading_day.date == day_date
    assert result.trading_day.phase is phase
    assert result.evaluation_candidate == candidate
    window = result.execution_window
    assert window.status is window_status
    assert window.closed_reason == closed_reason
    assert window.until is not None
    local_until = window.until.astimezone(ZoneInfo("America/New_York"))
    assert (local_until.hour, local_until.minute) == until_et
    if window_status is facts.WindowStatus.CLOSED:
        assert window.next_opens_at is not None
        assert window.next_opens_at > now
    else:
        assert window.next_opens_at is None
        assert window.until is not None and now < window.until


def test_elapsed_window_reports_the_next_regular_open_strictly_after_now(seeded: Settings) -> None:
    result = _facts_at(seeded, et(2025, 12, 2, 15, 45))
    assert result.execution_window.next_opens_at == et(2025, 12, 3, 9, 30)


def test_not_yet_open_window_reports_its_own_open(seeded: Settings) -> None:
    result = _facts_at(seeded, et(2025, 12, 2, 8, 0))
    assert result.execution_window.next_opens_at == et(2025, 12, 2, 9, 30)
    assert result.execution_window.opens_at == et(2025, 12, 2, 9, 30)


def test_configurable_cutoff_changes_until(seeded: Settings) -> None:
    now = et(2025, 12, 2, 10, 0)
    zero = _facts_at(_with_execution(seeded, execution_window_cutoff_minutes=0), now)
    thirty = _facts_at(_with_execution(seeded, execution_window_cutoff_minutes=30), now)
    assert zero.execution_window.until == et(2025, 12, 2, 16, 0)
    assert thirty.execution_window.until == et(2025, 12, 2, 15, 30)
    assert _facts_at(_with_execution(seeded, execution_window_cutoff_minutes=30), et(2025, 12, 2, 15, 30)).execution_window.status is facts.WindowStatus.CLOSED
    assert _facts_at(_with_execution(seeded, execution_window_cutoff_minutes=0), et(2025, 12, 2, 15, 59, 59)).execution_window.status is facts.WindowStatus.OPEN


def test_naive_now_is_rejected(seeded: Settings) -> None:
    with session_scope(seeded) as session:
        with pytest.raises(ValueError, match="timezone-aware"):
            facts.load_calendar_window(session, now=datetime(2025, 12, 2, 10, 0), settings=seeded)


# ---------------------------------------------------------------------------
# Explicit unknowns
# ---------------------------------------------------------------------------


def test_calendar_ending_2026_03_13_makes_all_three_facts_unknown_on_2026_09_29(facts_db: str) -> None:
    seed_calendar(date(2026, 1, 2), date(2026, 3, 13))
    settings = load_settings()
    now = et(2026, 9, 29, 10, 0)

    result = _facts_at(settings, now)

    assert result.trading_day.status is facts.FactStatus.UNKNOWN
    assert result.trading_day.reason is facts.UnknownReason.CALENDAR_DATA_UNAVAILABLE
    assert result.evaluation_candidate is facts.UnknownReason.CALENDAR_DATA_UNAVAILABLE
    assert result.execution_window.status is facts.WindowStatus.UNKNOWN
    assert result.execution_window.reason is facts.UnknownReason.CALENDAR_DATA_UNAVAILABLE
    with session_scope(settings) as session:
        evaluation = facts.evaluation_session(
            session, now=now, settings=settings, strategy=FakeStrategy(["AAA"])
        )
    assert evaluation.status is facts.EvaluationStatus.UNKNOWN
    assert evaluation.reason is facts.UnknownReason.CALENDAR_DATA_UNAVAILABLE
    # the stale calendar is still visible in coverage, never as a current date
    assert result.coverage.last_persisted_session == date(2026, 3, 13)
    assert result.coverage.runway_sessions == 0
    assert result.coverage.runway_low is True


def test_empty_calendar_is_unknown_safe(facts_db: str) -> None:
    settings = load_settings()
    result = _facts_at(settings, et(2025, 12, 2, 10, 0))
    assert result.trading_day.status is facts.FactStatus.UNKNOWN
    assert result.coverage.last_persisted_session is None
    assert result.coverage.runway_sessions == 0
    assert result.coverage.runway_low is True


def test_missing_persisted_row_for_needed_session_is_unknown(facts_db: str) -> None:
    # today's row persisted, previous session's row missing -> candidate unknown
    seed_calendar(date(2025, 12, 2), date(2025, 12, 31))
    settings = load_settings()
    result = _facts_at(settings, et(2025, 12, 2, 10, 0))
    assert result.trading_day.status is facts.FactStatus.KNOWN
    assert result.evaluation_candidate is facts.UnknownReason.CALENDAR_DATA_UNAVAILABLE
    assert result.execution_window.status is facts.WindowStatus.UNKNOWN


def test_library_out_of_bounds_is_unknown(facts_db: str) -> None:
    settings = load_settings()
    result = _facts_at(settings, datetime(2040, 1, 3, 15, 0, tzinfo=UTC))
    assert result.trading_day.status is facts.FactStatus.UNKNOWN
    assert result.evaluation_candidate is facts.UnknownReason.CALENDAR_DATA_UNAVAILABLE
    assert result.execution_window.status is facts.WindowStatus.UNKNOWN
    with pytest.raises(CalendarOutOfBoundsError):
        calendar_horizon_end(settings, date(2040, 1, 3))


# ---------------------------------------------------------------------------
# Statement bound
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("horizon", [1, 5, 60, 200])
@pytest.mark.parametrize(
    "now",
    [et(2025, 12, 2, 10, 0), et(2025, 11, 29, 12, 0), et(2026, 9, 29, 10, 0)],
    ids=["tuesday", "weekend", "stale"],
)
def test_calendar_facts_issue_exactly_one_statement(seeded: Settings, horizon: int, now: datetime) -> None:
    settings = _with_calendar(seeded, coverage_horizon_sessions=horizon)
    with session_scope(settings) as session:
        with count_queries(session) as counter:
            facts.calendar_facts(session, now=now, settings=settings)
    assert counter.count == 1


def test_each_single_fact_loads_the_window_with_one_statement(seeded: Settings) -> None:
    now = et(2025, 12, 2, 10, 0)
    with session_scope(seeded) as session:
        for call in (
            lambda: facts.trading_day(session, now=now, settings=seeded),
            lambda: facts.evaluation_candidate_session(session, now=now, settings=seeded),
            lambda: facts.execution_window(
                session, now=now, settings=seeded, evaluation_session_date=date(2025, 12, 1)
            ),
            lambda: facts.calendar_coverage(session, now=now, settings=seeded),
        ):
            with count_queries(session) as counter:
                call()
            assert counter.count == 1


def test_bar_counts_through_session_is_one_statement_for_1_and_50_symbols(facts_db: str) -> None:
    seed_calendar(date(2025, 12, 1), date(2025, 12, 5))
    tickers = [f"T{i:02d}" for i in range(50)]
    seed_bars(tickers, [date(2025, 12, 1), date(2025, 12, 2)])
    settings = load_settings()
    with session_scope(settings) as session:
        with count_queries(session) as one:
            single = bar_counts_through_session(session, tickers[:1], date(2025, 12, 2))
        with count_queries(session) as many:
            counts = bar_counts_through_session(session, tickers + ["ZZZZ"], date(2025, 12, 1))
    assert one.count == 1 and many.count == 1
    assert single == {"T00": 2}
    assert counts["T10"] == 1 and counts["ZZZZ"] == 0 and len(counts) == 51


# ---------------------------------------------------------------------------
# Readiness: calendar_gap > missing_bars > insufficient_history > ready
# ---------------------------------------------------------------------------


def test_readiness_precedence_and_symbols(seeded: Settings) -> None:
    strategy = FakeStrategy(["AAA", "BBB"], warmup_periods=3)
    s = date(2025, 12, 2)
    days = sessions_between(date(2025, 11, 26), s)
    assert len(days) >= 3
    with session_scope(seeded) as session:
        # no bars at all -> missing_bars lists both symbols
        result = facts.session_readiness(session, settings=seeded, strategy=strategy, session_date=s)
        assert result.status is facts.EvaluationStatus.NOT_READY
        assert result.reason is facts.EvaluationNotReadyReason.MISSING_BARS
        assert result.symbols == ("AAA", "BBB")
        # calendar_gap beats missing_bars
        gap = facts.session_readiness(
            session, settings=seeded, strategy=strategy, session_date=date(2025, 10, 1)
        )
        assert gap.reason is facts.EvaluationNotReadyReason.CALENDAR_GAP
    seed_bars(["AAA"], days)
    with session_scope(seeded) as session:
        partial = facts.session_readiness(session, settings=seeded, strategy=strategy, session_date=s)
        assert partial.reason is facts.EvaluationNotReadyReason.MISSING_BARS
        assert partial.symbols == ("BBB",)
    # BBB has the bar on S only: history is insufficient (1 < 3)
    seed_bars(["BBB"], [s])
    with session_scope(seeded) as session:
        short = facts.session_readiness(session, settings=seeded, strategy=strategy, session_date=s)
        assert short.reason is facts.EvaluationNotReadyReason.INSUFFICIENT_HISTORY
        assert short.symbols == ("BBB",)
        flags = {item.symbol: item for item in short.symbol_readiness}
        assert flags["AAA"].history_sufficient and not flags["BBB"].history_sufficient
    seed_bars(["BBB"], [d for d in days if d != s])
    with session_scope(seeded) as session:
        ready = facts.session_readiness(session, settings=seeded, strategy=strategy, session_date=s)
        assert ready.status is facts.EvaluationStatus.READY
        assert ready.session_date == s
        assert ready.reason is None


def test_evaluation_session_uses_the_candidate_and_ignores_the_clock_beyond_it(seeded: Settings) -> None:
    strategy = FakeStrategy(["AAA"], warmup_periods=1)
    seed_bars(["AAA"], [date(2025, 12, 1)])
    with session_scope(seeded) as session:
        for now in (et(2025, 12, 2, 8, 0), et(2025, 12, 2, 10, 0), et(2025, 12, 2, 15, 50)):
            result = facts.evaluation_session(session, now=now, settings=seeded, strategy=strategy)
            assert result.status is facts.EvaluationStatus.READY
            assert result.session_date == date(2025, 12, 1)
        after_close = facts.evaluation_session(
            session, now=et(2025, 12, 2, 17, 0), settings=seeded, strategy=strategy
        )
        assert after_close.session_date == date(2025, 12, 2)
        assert after_close.reason is facts.EvaluationNotReadyReason.MISSING_BARS


def test_readiness_from_rows_is_pure() -> None:
    result = facts.readiness_from_rows(
        session_date=date(2025, 12, 2),
        row_present=True,
        universe=["B", "A"],
        missing_symbols=[],
        bar_counts={"A": 5, "B": 5},
        warmup_periods=5,
    )
    assert result.status is facts.EvaluationStatus.READY


# ---------------------------------------------------------------------------
# Coverage and calendar navigation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("extra", "low"), [(10, False), (9, True)])
def test_runway_low_boundary_at_exactly_the_threshold(facts_db: str, extra: int, low: bool) -> None:
    today = date(2025, 12, 2)
    sessions = sessions_between(date(2025, 11, 20), date(2026, 2, 28))
    end = sessions[sessions.index(today) + extra]
    seed_calendar(date(2025, 11, 20), end)
    settings = load_settings()
    with session_scope(settings) as session:
        coverage = facts.calendar_coverage(session, now=et(2025, 12, 2, 10, 0), settings=settings)
    assert coverage.runway_sessions == extra
    assert coverage.runway_low is low
    assert coverage.last_persisted_session == end
    assert coverage.horizon_sessions == 60
    assert coverage.runway_low_threshold == 10


def test_runway_threshold_is_configurable(seeded: Settings) -> None:
    tight = _with_calendar(seeded, runway_low_sessions=200)
    with session_scope(tight) as session:
        coverage = facts.calendar_coverage(session, now=et(2025, 12, 2, 10, 0), settings=tight)
    assert coverage.runway_low is True


def test_calendar_navigation_helpers() -> None:
    assert previous_session_date(date(2025, 11, 29)) == date(2025, 11, 28)
    assert previous_session_date(date(2025, 12, 2)) == date(2025, 12, 1)
    assert next_session_date(date(2025, 11, 27)) == date(2025, 11, 28)
    assert next_session_date(date(2025, 11, 28)) == date(2025, 12, 1)
    with pytest.raises(CalendarOutOfBoundsError):
        next_session_date(date(2090, 1, 1))


def test_horizon_end_is_the_nth_session_after_today_and_clips_to_the_library() -> None:
    settings = load_settings()
    five = _with_calendar(settings, coverage_horizon_sessions=5)
    assert calendar_horizon_end(five, date(2025, 12, 2)) == date(2025, 12, 9)
    assert calendar_horizon_end(five, date(2025, 11, 29)) == date(2025, 12, 5)
    near_end = _with_calendar(settings, coverage_horizon_sessions=500)
    last = get_calendar(settings.market_data.calendar.exchange).last_session.date()
    # the library window rolls with the real date: derive the edge, never hardcode it
    assert calendar_horizon_end(near_end, last - timedelta(days=30)) == last


def test_persisted_accessors(seeded: Settings) -> None:
    with session_scope(seeded) as session:
        row = persisted_session_row(session, date(2025, 11, 28), "XNYS")
        assert row is not None and row.early_close is True
        assert row.market_close is not None and row.market_close == et(2025, 11, 28, 13, 0)
        assert persisted_session_row(session, date(2025, 11, 27), "XNYS") is None
        after = persisted_sessions_after(session, date(2025, 12, 2), "XNYS", limit=3)
        assert after.count == 3
        total = persisted_sessions_after(session, date(2026, 3, 30), "XNYS")
        assert total.count == 1 and total.last_session == date(2026, 3, 31)


def test_unsupported_policy_raises_typed_error(seeded: Settings) -> None:
    bogus = seeded.model_copy(
        update={"execution": seeded.execution.model_copy(update={"execution_policy": "other_v9"})}
    )
    window = None
    with session_scope(seeded) as session:
        window = facts.load_calendar_window(session, now=et(2025, 12, 2, 10, 0), settings=seeded)
    with pytest.raises(facts.UnsupportedExecutionPolicyError):
        facts.execution_window_from_window(window, bogus, date(2025, 12, 1))


def test_facts_are_read_only(seeded: Settings) -> None:
    with session_scope(seeded) as session:
        facts.calendar_facts(session, now=et(2025, 12, 2, 10, 0), settings=seeded)
        facts.session_readiness(
            session, settings=seeded, strategy=FakeStrategy(["AAA"], 1), session_date=date(2025, 12, 1)
        )
        assert not session.new and not session.dirty and not session.deleted


def test_to_dict_shapes(seeded: Settings) -> None:
    result = _facts_at(seeded, et(2025, 12, 2, 10, 0))
    assert result.trading_day.to_dict()["phase"] == "open"
    assert result.execution_window.to_dict()["status"] == "open"
    assert result.coverage.to_dict()["runway_low"] is False
    unknown = _facts_at(seeded, datetime(2040, 1, 3, 15, 0, tzinfo=UTC))
    assert unknown.trading_day.to_dict() == {"status": "unknown", "reason": "calendar_data_unavailable"}
    assert unknown.execution_window.to_dict() == {
        "status": "unknown",
        "reason": "calendar_data_unavailable",
    }
