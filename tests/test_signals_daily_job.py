"""The ``signals-daily`` Job and the daily run of a book (docs/SIGNALS-PLAN.md, Step 2)."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator, Mapping
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from tests.support.migrated_db import migrated_database
from tests.support.research_fixtures import Bar, make_bars, seed_research_bars, xnys_sessions
from tests.test_signals_step1_engines import LEVEL_SPEC, START, approve

from trading_platform.core.settings import Settings, load_settings
from trading_platform.db.models.market_session import MarketSession
from trading_platform.db.models.research import StrategyVersion
from trading_platform.db.models.signal_book import SignalBook, SignalPublication
from trading_platform.db.models.signal_log import (
    SignalLogEvent,
    SignalSessionRun,
    SignalSessionSkip,
)
from trading_platform.db.session import session_scope
from trading_platform.jobs.handlers.signals_daily import SignalsDailyJobHandler
from trading_platform.jobs.handlers.signals_daily_submission import SignalsDailySubmissionSpec
from trading_platform.jobs.registry import InvalidJobPayloadError, build_research_registry
from trading_platform.services.calendar import upsert_market_sessions
from trading_platform.services.signals.daily import (
    DailyRunError,
    DailyRunErrorCode,
    history_start,
    run_daily_book,
)
from trading_platform.services.signals.publication import SignalBookError, SignalBookErrorCode
from trading_platform.services.tiingo import TiingoAssetMetadata, TiingoDailyRow

ASSETS = ["AAA", "BBB", "CCC"]
SESSIONS = 40
FIRST = 20  # index of the book's first session
BOOK = "test-book"


@pytest.fixture()
def settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[Settings]:
    with migrated_database(monkeypatch, "signals_daily"):
        yield load_settings()


class World:
    """Seeded bars for three assets, an approved version and a book configuration file."""

    def __init__(
        self, settings: Settings, directory: Path, *, seeded: int | None = None, max_open: int = 2, sessions: int = SESSIONS
    ) -> None:
        self.settings = settings
        self.directory = directory
        self.days = xnys_sessions(START, sessions)
        seeded = sessions if seeded is None else seeded
        self.bars: dict[str, list[Bar]] = {
            asset: make_bars(asset, self.days, seed=index + 1, volatility=0.03) for index, asset in enumerate(ASSETS)
        }
        with session_scope(settings) as session:
            for asset in ASSETS:
                seed_research_bars(session, asset, self.bars[asset][:seeded])
            upsert_market_sessions(session, self.days[0], self.days[-1] + timedelta(days=10))
        self.version_id = approve(settings, LEVEL_SPEC)
        with session_scope(settings) as session:
            sha = session.get(StrategyVersion, self.version_id).spec_sha256  # type: ignore[union-attr]
        (directory / f"{BOOK}.yaml").write_text(
            f"name: {BOOK}\nstrategy_spec_sha256: {sha}\nassets: [AAA, BBB, CCC]\nmax_open: {max_open}\n"
            f"first_session: {self.days[FIRST].isoformat()}\nbar_source: {{provider: tiingo, adjusted: true}}\n"
        )

    def after_close(self, day: date) -> datetime:
        with session_scope(self.settings) as session:
            close = session.execute(
                sa.select(MarketSession.market_close).where(MarketSession.session_date == day)
            ).scalar_one()
        return close + timedelta(hours=1)

    def run(self, day: date | None, *, at: datetime | None = None, **kwargs: Any) -> dict[str, Any]:
        moment = at or self.after_close(day)  # type: ignore[arg-type]
        return run_daily_book(
            self.settings,
            book_name=BOOK,
            session_date=day,
            ingest=kwargs.pop("ingest", False),
            clock=lambda: moment,
            books_directory=self.directory,
            **kwargs,
        )

    def counts(self) -> dict[str, int]:
        with session_scope(self.settings) as session:
            return {
                model.__tablename__: session.execute(sa.select(sa.func.count()).select_from(model)).scalar_one()
                for model in (SignalLogEvent, SignalSessionRun, SignalSessionSkip, SignalPublication, SignalBook)
            }


@pytest.fixture()
def world(settings: Settings, tmp_path: Path) -> World:
    return World(settings, tmp_path)


def test_a_daily_run_processes_every_asset_decides_publications_and_a_rerun_writes_nothing(world: World) -> None:
    d = world.days
    report = world.run(d[FIRST])
    assert report["session_date"] == d[FIRST].isoformat()
    assert report["sessions_processed"] == 3 and report["complete"] is True
    assert report["assets_skipped"] == {} and report["assets_refused"] == {} and report["integrity_errors"] == {}
    assert sum(report["publications"].values()) == report["events"].get("issued", 0)
    before = world.counts()
    assert before["signal_session_runs"] == 3 and before["signal_books"] == 1

    again = world.run(d[FIRST])
    assert again["sessions_processed"] == 0 and again["publications"] == {} and again["events"] == {}
    assert world.counts() == before

    # Day by day, the cap of 2 is never exceeded and every signal gets exactly one decision.
    for day in d[FIRST + 1 :]:
        report = world.run(day)
        assert report["complete"] is True and report["open_published"] <= 2
    counts = world.counts()
    assert counts["signal_session_runs"] == 3 * (SESSIONS - FIRST)
    with session_scope(world.settings) as session:
        issued = session.execute(
            sa.select(sa.func.count()).select_from(SignalLogEvent).where(SignalLogEvent.event_type == "issued")
        ).scalar_one()
        outcomes = set(session.execute(sa.select(SignalPublication.outcome)).scalars())
        assert session.execute(sa.select(sa.func.min(SignalLogEvent.signal_session))).scalar_one() >= d[FIRST]
    assert counts["signal_publications"] == issued >= 2
    assert outcomes <= {"published", "held_back_open_cap"} and "published" in outcomes


def test_a_run_after_several_missed_days_catches_up_and_holds_back_the_old_signals_as_late(world: World) -> None:
    d = world.days
    report = world.run(d[FIRST + 6])
    assert report["sessions_processed"] == 3 * 7 and report["complete"] is True
    with session_scope(world.settings) as session:
        rows = list(session.execute(sa.select(SignalPublication)).scalars())
    assert rows
    for row in rows:
        if row.signal_session < d[FIRST + 6]:
            assert row.outcome == "held_back_late"
        else:
            assert row.outcome in {"published", "held_back_open_cap"}
    assert any(row.outcome == "held_back_late" for row in rows)


def test_the_latest_closed_session_is_the_default_target(world: World) -> None:
    d = world.days
    report = world.run(None, at=world.after_close(d[FIRST + 2]))
    assert report["session_date"] == d[FIRST + 2].isoformat()
    # Before that day's close the latest closed session is the one before it.
    with session_scope(world.settings) as session:
        close = session.execute(
            sa.select(MarketSession.market_close).where(MarketSession.session_date == d[FIRST + 4])
        ).scalar_one()
    assert world.run(None, at=close - timedelta(minutes=1))["session_date"] == d[FIRST + 3].isoformat()


def test_a_run_is_refused_for_a_date_that_cannot_be_run(world: World) -> None:
    d = world.days

    def refused(day: date, *, at: datetime) -> DailyRunErrorCode:
        with pytest.raises(DailyRunError) as info:
            world.run(day, at=at)
        return info.value.code

    late = world.after_close(d[-1])
    assert refused(date(2024, 1, 6), at=late) is DailyRunErrorCode.NOT_A_SESSION  # a Saturday
    assert refused(d[FIRST - 1], at=late) is DailyRunErrorCode.BEFORE_FIRST_SESSION
    assert refused(d[FIRST + 1], at=world.after_close(d[FIRST])) is DailyRunErrorCode.SESSION_NOT_CLOSED
    counts = world.counts()
    assert counts["signal_log"] == counts["signal_session_runs"] == counts["signal_session_skips"] == 0


def test_a_missing_bar_skips_one_asset_and_the_others_run(world: World) -> None:
    d = world.days
    with session_scope(world.settings) as session:
        symbol_id = session.execute(sa.text("SELECT id FROM symbols WHERE ticker = 'BBB'")).scalar_one()
        saved = [
            dict(row)
            for row in session.execute(
                sa.text("SELECT * FROM daily_bars WHERE symbol_id = :s AND session_date = :d"),
                {"s": symbol_id, "d": d[FIRST + 1]},
            ).mappings()
        ]
        session.execute(
            sa.text("DELETE FROM daily_bars WHERE symbol_id = :s AND session_date = :d"),
            {"s": symbol_id, "d": d[FIRST + 1]},
        )
    world.run(d[FIRST])
    report = world.run(d[FIRST + 1])
    assert report["complete"] is False and report["sessions_processed"] == 2
    assert list(report["assets_skipped"]) == ["BBB"]
    assert report["assets_skipped"]["BBB"]["session"] == d[FIRST + 1].isoformat()
    # The missing session is an integrity error of the checked range (a bar is absent).
    assert report["assets_skipped"]["BBB"]["reason"] == "integrity_failed"
    assert report["integrity_errors"] == {"BBB": report["assets_skipped"]["BBB"]["detail"]}

    # The next day BBB is still waiting; the skip is not recorded twice for the same session.
    report = world.run(d[FIRST + 2])
    assert report["assets_skipped"]["BBB"]["session"] == d[FIRST + 1].isoformat()
    assert world.counts()["signal_session_skips"] == 1

    # The bar arrives: BBB catches up with both sessions.
    with session_scope(world.settings) as session:
        table = sa.Table("daily_bars", sa.MetaData(), autoload_with=session.get_bind())
        session.execute(sa.insert(table), saved)
    report = world.run(d[FIRST + 2])
    assert report["complete"] is True and report["sessions_processed"] == 2
    with session_scope(world.settings) as session:
        last = dict(
            session.execute(
                sa.select(SignalSessionRun.asset, sa.func.max(SignalSessionRun.session_date)).group_by(
                    SignalSessionRun.asset
                )
            ).all()
        )
    assert last == {asset: d[FIRST + 2] for asset in ASSETS}


def test_a_book_whose_configuration_changed_is_refused(world: World) -> None:
    world.run(world.days[FIRST])
    path = world.directory / f"{BOOK}.yaml"
    path.write_text(path.read_text().replace("max_open: 2", "max_open: 3"))
    before = world.counts()
    with pytest.raises(SignalBookError) as info:
        world.run(world.days[FIRST + 1])
    assert info.value.code is SignalBookErrorCode.BOOK_CHANGED
    assert world.counts() == before


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------


class StubTiingo:
    def __init__(self, bars: dict[str, list[Bar]]) -> None:
        self.bars = bars
        self.requests_made = 0
        self.metadata_requests = 0
        self.ranges: list[tuple[str, date, date]] = []

    def fetch_metadata(self, ticker: str) -> TiingoAssetMetadata:
        self.requests_made += 1
        self.metadata_requests += 1
        return TiingoAssetMetadata(
            ticker=ticker, name=f"{ticker} Corp", exchange_code="NYSE", start_date=date(1990, 1, 2), end_date=None
        )

    def fetch_daily_prices(self, ticker: str, from_date: date, to_date: date) -> list[TiingoDailyRow]:
        self.requests_made += 1
        self.ranges.append((ticker, from_date, to_date))
        return [
            TiingoDailyRow(
                session_date=bar.session_date,
                open=bar.open, high=bar.high, low=bar.low, close=bar.close, volume=bar.volume,
                adj_open=bar.open, adj_high=bar.high, adj_low=bar.low, adj_close=bar.close, adj_volume=bar.volume,
                div_cash=bar.close * 0, split_factor=bar.close * 0 + 1,
            )
            for bar in self.bars[ticker]
            if from_date <= bar.session_date <= to_date
        ]

    def close(self) -> None:
        pass


def test_the_run_downloads_the_history_range_with_one_request_an_asset(settings: Settings, tmp_path: Path) -> None:
    world = World(settings, tmp_path, seeded=FIRST + 1)  # bars exist up to the first session only
    d = world.days
    stub = StubTiingo(world.bars)
    world.run(d[FIRST])

    report = world.run(d[FIRST + 1], ingest=True, client=stub)
    assert report["complete"] is True and report["sessions_processed"] == 3
    assert report["ingestion"]["run_status"] == "succeeded" and report["ingestion"]["assets_failed"] == []
    assert (stub.requests_made, stub.metadata_requests) == (3, 0)
    assert {(start, end) for _, start, end in stub.ranges} == {(date.fromisoformat(report["history_from"]), d[FIRST + 1])}
    assert date.fromisoformat(report["history_from"]) < d[FIRST + 1] - timedelta(days=30)


def test_a_run_after_a_long_interruption_downloads_from_the_first_pending_session_and_catches_up(
    settings: Settings, tmp_path: Path
) -> None:
    # The book ran its first session, then nothing ran for 59 sessions (about 85 calendar
    # days). That is longer than the history range of one target session (31 days for this
    # one-bar strategy), so the bars of the first missed sessions lie before that range.
    world = World(settings, tmp_path, seeded=FIRST + 1, sessions=80)
    d = world.days
    world.run(d[FIRST])
    stub = StubTiingo(world.bars)
    assert history_start(d[-1], 1) > d[FIRST + 1]  # the gap the old range left

    report = world.run(d[-1], ingest=True, client=stub)
    assert report["complete"] is True and report["assets_skipped"] == {} and report["integrity_errors"] == {}
    assert report["sessions_processed"] == 3 * (80 - FIRST - 1)
    # The range starts from the history of the first pending session, not of the target.
    assert report["first_pending_session"] == d[FIRST + 1].isoformat()
    assert date.fromisoformat(report["history_from"]) == history_start(d[FIRST + 1], 1)
    assert {(start, end) for _, start, end in stub.ranges} == {(history_start(d[FIRST + 1], 1), d[-1])}
    with session_scope(settings) as session:
        last = dict(
            session.execute(
                sa.select(SignalSessionRun.asset, sa.func.max(SignalSessionRun.session_date)).group_by(
                    SignalSessionRun.asset
                )
            ).all()
        )
    assert last == {asset: d[-1] for asset in ASSETS}

    # A repeated run, with or without the download, writes nothing.
    before = world.counts()
    for ingest in (True, False):
        again = world.run(d[-1], ingest=ingest, client=stub if ingest else None)
        assert again["sessions_processed"] == 0 and again["events"] == {} and again["publications"] == {}
        assert again["complete"] is True
    assert world.counts() == before
    # With nothing pending the range is the target's own history again.
    assert date.fromisoformat(again["history_from"]) == history_start(d[-1], 1)


# ---------------------------------------------------------------------------
# The Job type
# ---------------------------------------------------------------------------


class FakeContext:
    def __init__(self, payload: Mapping[str, Any]) -> None:
        self.job_id = uuid.uuid4()
        self.job_type = "signals-daily"
        self.payload = payload
        self.steps: list[str] = []
        self.logs: list[tuple[str, str]] = []

    def report_progress(self, *, percent: int | None = None, step: str | None = None, current: int | None = None, total: int | None = None) -> None:
        self.steps.append(step or "")

    def log(self, *, level: str, event_code: str, message: str, context: Mapping[str, Any] | None = None) -> None:
        self.logs.append((level, event_code))

    def is_cancellation_requested(self) -> bool:
        return False

    def raise_if_cancelled(self) -> None:
        return None


def test_the_job_type_is_registered_validates_its_payload_and_runs_the_book(world: World) -> None:
    registry = build_research_registry(world.settings)
    assert "signals-daily" in registry
    spec = registry.resolve_submission_spec("signals-daily")
    assert isinstance(spec, SignalsDailySubmissionSpec)
    assert spec.validate_payload({"book": BOOK}) == {"book": BOOK, "session_date": None, "ingest": True}
    assert spec.validate_payload({"book": BOOK, "session_date": "2024-02-01", "ingest": False}) == {
        "book": BOOK,
        "session_date": "2024-02-01",
        "ingest": False,
    }

    def reason(payload: dict[str, Any]) -> str:
        with pytest.raises(InvalidJobPayloadError) as info:
            spec.validate_payload(payload)
        return info.value.reason

    assert reason({}) == "missing_required_field"
    assert reason({"book": BOOK, "assets": ["AAA"]}) == "unknown_payload_keys"
    assert reason({"book": 7}) == "invalid_field_type"
    assert reason({"book": BOOK, "ingest": "yes"}) == "invalid_field_type"
    assert reason({"book": BOOK, "session_date": "01/02/2024"}) == "invalid_date"
    assert reason({"book": "../secrets"}) == "invalid_book_name"

    day = world.days[FIRST]
    clock: Callable[[], datetime] = lambda: world.after_close(day)  # noqa: E731
    handler = SignalsDailyJobHandler(world.settings, clock=clock, books_directory=world.directory)
    context = FakeContext({"book": BOOK, "session_date": day.isoformat(), "ingest": False})
    result = handler.run(context)  # type: ignore[arg-type]
    assert result["sessions_processed"] == 3 and result["produced_run_ids"] == []
    assert context.steps == ["checking integrity", "processing sessions", "deciding publications"]
    assert context.logs == [("info", "signals_daily_completed")]
