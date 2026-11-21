"""Levels of an active signal across a dividend or a split, and the replay of a book from
its kept inputs (docs/SIGNALS-PLAN.md, Step 2; conditions for starting the forward log)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import date
from decimal import Decimal as D
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from tests.support.migrated_db import migrated_database
from tests.support.research_fixtures import seed_research_bars
from tests.test_signals_daily_job import ASSETS, BOOK, FIRST, FakeContext, World
from tests.test_signals_step1_engines import ASSET, LEVEL_SPEC, SOURCE, approve, scripted_bars

from trading_platform.core.settings import Settings, load_settings
from trading_platform.db.models.research import StrategyVersion
from trading_platform.db.models.signal_log import SignalLogEvent, SignalSessionInput
from trading_platform.db.session import session_scope
from trading_platform.jobs.handlers.signals_daily import SignalsReplayJobHandler
from trading_platform.jobs.registry import build_research_registry
from trading_platform.services.signals.comparison import SignalState, outcomes_from_log
from trading_platform.services.signals.engine import run_signal_session
from trading_platform.services.signals.publication import SignalBookError, SignalBookErrorCode
from trading_platform.services.signals.replay import replay_book_by_name
from trading_platform.services.signals.session_rules import effective_levels
from trading_platform.strategies.trade_rules import SignalLevels

# The exit rule never fires, so only the stop and the target can close the trade.
NO_RULE_EXIT_SPEC = LEVEL_SPEC.replace("right: 90", "right: 1").replace(
    "name: Level entry with stop and target", "name: Level entry, stop and target only"
)


@pytest.fixture()
def settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[Settings]:
    with migrated_database(monkeypatch, "signals_adjust"):
        yield load_settings()


def _rescale_history(settings: Settings, through: date, factor: str, *, asset: str = ASSET) -> None:
    """What a download after an ex-date does: every adjusted bar up to ``through`` is
    rewritten in the new price scale. Raw bars are untouched."""

    with session_scope(settings) as session:
        session.execute(
            sa.text(
                "UPDATE daily_bars SET open = open * :f, high = high * :f, low = low * :f, close = close * :f "
                "WHERE adjusted AND session_date <= :d AND symbol_id = (SELECT id FROM symbols WHERE ticker = :a)"
            ),
            {"f": D(factor), "d": through, "a": asset},
        )


def _process(settings: Settings, version_id: uuid.UUID, day: date) -> list[str]:
    with session_scope(settings) as session:
        outcome = run_signal_session(
            session,
            settings,
            version=session.get(StrategyVersion, version_id),
            asset=ASSET,
            session_date=day,
            bar_source=SOURCE,
        )
        return [event.event_type for event in outcome.events]


def _event(settings: Settings, event_type: str) -> dict[str, Any]:
    with session_scope(settings) as session:
        row = session.execute(sa.select(SignalLogEvent).where(SignalLogEvent.event_type == event_type)).scalar_one()
        return {
            "levels": (row.reference_price, row.stop_price, row.target_price),
            "factor": row.adjustment_factor,
            "effective": (row.effective_stop_price, row.effective_target_price),
            "price": row.price,
            "session": row.event_session,
        }


def test_the_owner_example_a_dividend_from_100_to_99() -> None:
    issued = SignalLevels(reference=D("100"), stop=D("95"), target=D("110"))
    adjusted, factor = effective_levels(issued, D("99"))
    assert (factor, adjusted.stop, adjusted.target) == (D("0.99"), D("94.05"), D("108.90"))
    # Deriving from the issued levels again gives the same result: nothing is applied twice.
    assert effective_levels(issued, D("99")) == (adjusted, factor)
    assert effective_levels(issued, D("100")) == (issued, D(1))


def test_levels_follow_two_dividends_and_each_is_applied_once(settings: Settings) -> None:
    bars = scripted_bars(
        [
            ("99", "101", "98", "100"),  # 0: signal, reference 100, stop 95, target 110
            ("99.5", "100", "98", "99"),  # 1: ex-dividend; entry at the open
            ("99", "99.5", "94.5", "98"),  # 2: low 94.5 is below the issued stop, above the adjusted one
            ("96", "97", "92.5", "96"),  # 3: after a second dividend
            ("96", "96.5", "92", "93"),  # 4: the adjusted stop is touched
        ]
    )
    d = [bar.session_date for bar in bars]
    with session_scope(settings) as session:
        seed_research_bars(session, ASSET, bars)
    version_id = approve(settings, NO_RULE_EXIT_SPEC)

    assert _process(settings, version_id, d[0]) == ["issued"]
    _rescale_history(settings, d[0], "0.99")  # first dividend, ex-date session 1
    assert _process(settings, version_id, d[1]) == ["entry_filled"]
    assert _process(settings, version_id, d[2]) == []  # the issued stop of 95 would have closed it
    _rescale_history(settings, d[2], "0.98")  # second dividend, ex-date session 3
    assert _process(settings, version_id, d[3]) == []
    assert _process(settings, version_id, d[4]) == ["closed_stop"]

    issued, entry, closed = (_event(settings, name) for name in ("issued", "entry_filled", "closed_stop"))
    assert issued["factor"] == 1 and issued["effective"] == (D("95"), D("110"))
    assert entry["factor"] == D("0.99") and entry["effective"] == (D("94.05"), D("108.90"))
    assert entry["price"] == D("99.5")
    # 0.99 x 0.98 = 0.9702, applied once to the issued levels (not 0.98 on top of 94.05 twice).
    assert closed["factor"] == D("0.9702")
    assert closed["effective"] == (D("92.169"), D("106.722")) and closed["price"] == D("92.169")
    # The levels as issued are on every event, unchanged.
    assert issued["levels"] == entry["levels"] == closed["levels"] == (D("100"), D("95"), D("110"))

    # The outcome is measured in one price scale: a stop is -1 R, whatever happened to the scale.
    with session_scope(settings) as session:
        (outcome,) = outcomes_from_log(session, strategy_version_id=version_id, asset=ASSET, through=d[4])
    assert outcome.state is SignalState.CLOSED_STOP
    assert outcome.exit_price == D("95") and outcome.gross_r == D("-1")


def test_a_split_does_not_close_the_trade_at_a_false_stop(settings: Settings) -> None:
    bars = scripted_bars(
        [
            ("99", "101", "98", "100"),  # 0: signal, reference 100, stop 95, target 110
            ("100", "101", "99", "100.5"),  # 1: entry at 100
            ("50", "51", "49", "50.5"),  # 2: after a 2-for-1 split
            ("50.5", "55.5", "50", "54"),  # 3: the adjusted target of 55 is touched
        ]
    )
    d = [bar.session_date for bar in bars]
    with session_scope(settings) as session:
        seed_research_bars(session, ASSET, bars)
    version_id = approve(settings, NO_RULE_EXIT_SPEC)

    assert _process(settings, version_id, d[0]) == ["issued"]
    assert _process(settings, version_id, d[1]) == ["entry_filled"]
    _rescale_history(settings, d[1], "0.5")
    assert _process(settings, version_id, d[2]) == []  # an open of 50 is not a gap through a stop of 95
    assert _process(settings, version_id, d[3]) == ["closed_target"]

    closed = _event(settings, "closed_target")
    assert closed["factor"] == D("0.5") and closed["effective"] == (D("47.5"), D("55"))
    assert closed["price"] == D("55") and closed["levels"] == (D("100"), D("95"), D("110"))
    with session_scope(settings) as session:
        (outcome,) = outcomes_from_log(session, strategy_version_id=version_id, asset=ASSET, through=d[3])
    assert (outcome.entry_price, outcome.exit_price, outcome.gross_r) == (D("100"), D("110"), D("2"))


def test_the_database_refuses_derived_levels_that_contradict_the_factor(settings: Settings) -> None:
    bars = scripted_bars([("99", "101", "98", "100"), ("100", "101", "99", "100.5")])
    with session_scope(settings) as session:
        seed_research_bars(session, ASSET, bars)
    version_id = approve(settings, NO_RULE_EXIT_SPEC)

    def insert(**overrides: object) -> None:
        values: dict[str, object] = {
            "id": uuid.uuid4(),
            "strategy_version_id": version_id,
            "asset": ASSET,
            "signal_session": bars[0].session_date,
            "event_type": "issued",
            "event_session": bars[0].session_date,
            "spec_sha256": "a" * 64,
            "reference_price": D(100),
            "stop_price": D(95),
            "target_price": D(110),
            "adjustment_factor": D(1),
            "effective_stop_price": D(95),
            "effective_target_price": D(110),
            "input_data_digest": "b" * 64,
            "recorded_at": sa.func.now(),
        } | overrides
        with session_scope(settings) as session:
            session.execute(sa.insert(SignalLogEvent).values(**values))

    for overrides in (
        {"adjustment_factor": None},
        {"effective_stop_price": None},
        {"adjustment_factor": D(0)},
        {"effective_stop_price": D("94.05")},  # factor 1 with levels that differ from the issued ones
        {"adjustment_factor": D("0.99"), "effective_stop_price": D("94.05"), "effective_target_price": D("108.9")},  # issued is always factor 1
        {"effective_stop_price": D(120)},  # stop above the target
    ):
        with pytest.raises(sa.exc.IntegrityError, match="ck_signal_log_effective_levels"):
            insert(**overrides)
    insert()


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------


def _run_book_with_a_dividend(world: World) -> str:
    """Run the book day by day. On the first day an asset has a signal active, that asset
    goes ex-dividend (its adjusted history is rewritten). Returns the asset."""

    days = world.days
    paid: str | None = None
    for index in range(FIRST, len(days)):
        if paid is None and index > FIRST:
            with session_scope(world.settings) as session:
                paid = session.execute(
                    sa.text(
                        "SELECT s.asset FROM signal_log s WHERE s.event_type = 'issued' AND NOT EXISTS ("
                        " SELECT 1 FROM signal_log d WHERE d.asset = s.asset AND d.signal_session = s.signal_session"
                        " AND d.event_type IN ('expired', 'closed_stop', 'closed_target', 'closed_rule'))"
                        " ORDER BY s.asset LIMIT 1"
                    )
                ).scalar_one_or_none()
            if paid is not None:
                _rescale_history(world.settings, days[index - 1], "0.97", asset=paid)
        report = world.run(days[index])
        assert report["complete"] is True, report
    assert paid is not None
    return paid


def _replay(world: World) -> dict[str, Any]:
    return replay_book_by_name(world.settings, BOOK, books_directory=world.directory)


def test_a_replay_from_the_kept_inputs_reproduces_every_event_and_publication_decision(
    settings: Settings, tmp_path: Path
) -> None:
    world = World(settings, tmp_path)
    paid = _run_book_with_a_dividend(world)
    counts = world.counts()
    sessions = len(world.days) - FIRST

    report = _replay(world)
    assert report["identical"] is True and report["difference_details"] == []
    assert report["sessions_replayed"] == len(ASSETS) * sessions
    assert report["events_replayed"] == report["events_logged"] == counts["signal_log"] > 0
    assert report["publications_checked"] == counts["signal_publications"] > 0
    assert 1 <= report["max_open_published"] <= 2
    with session_scope(settings) as session:
        assert session.execute(sa.select(sa.func.count()).select_from(SignalSessionInput)).scalar_one() == len(ASSETS) * sessions
        factors = set(
            session.execute(sa.select(SignalLogEvent.adjustment_factor).where(SignalLogEvent.asset == paid)).scalars()
        )
    # The replay wrote nothing.
    assert world.counts() == counts

    # Later dividends rewrite the adjusted history of every asset. The kept inputs are
    # what the sessions read at the time, so the replay is unchanged.
    for asset in ASSETS:
        _rescale_history(settings, world.days[-1], "0.9", asset=asset)
    assert _replay(world)["identical"] is True
    # The scenario did exercise an adjustment: events of the signal that was active across
    # the ex-date carry the dividend's factor.
    # (Stored prices have six decimals, so the factor is 0.97 to within rounding.)
    assert D(1) in factors and any(abs(factor - D("0.97")) < D("0.000001") for factor in factors)


def test_the_replay_reports_a_record_that_differs_from_what_the_inputs_give(settings: Settings, tmp_path: Path) -> None:
    world = World(settings, tmp_path)
    _run_book_with_a_dividend(world)

    def tamper(table: str, statement: str) -> None:
        with session_scope(settings) as session:
            session.execute(sa.text(f"ALTER TABLE {table} DISABLE TRIGGER USER"))
            assert session.execute(sa.text(statement)).rowcount == 1
            session.execute(sa.text(f"ALTER TABLE {table} ENABLE TRIGGER USER"))

    def kinds() -> list[str]:
        return sorted({difference["kind"] for difference in _replay(world)["difference_details"]})

    tamper(
        "signal_log",
        "UPDATE signal_log SET price = price + 1 WHERE id = "
        "(SELECT id FROM signal_log WHERE event_type = 'entry_filled' ORDER BY event_session, asset LIMIT 1)",
    )
    report = _replay(world)
    assert report["identical"] is False and report["differences"] == 1
    (difference,) = report["difference_details"]
    assert difference["kind"] == "event_differs" and difference["fields"] == ["price"]

    tamper(
        "signal_publications",
        "UPDATE signal_publications SET outcome = 'held_back_open_cap' WHERE id = "
        "(SELECT id FROM signal_publications WHERE outcome = 'published' ORDER BY signal_session, asset LIMIT 1)",
    )
    assert "publication_differs" in kinds()

    tamper(
        "signal_session_inputs",
        "UPDATE signal_session_inputs SET inputs = jsonb_set(inputs, '{bar,1}', '\"1\"') WHERE id = "
        "(SELECT id FROM signal_session_inputs WHERE asset = 'CCC' ORDER BY session_date LIMIT 1)",
    )
    assert "inputs_digest_mismatch" in kinds()


def test_the_replay_job_is_registered_and_refuses_a_book_that_never_ran_or_changed(
    settings: Settings, tmp_path: Path
) -> None:
    world = World(settings, tmp_path)
    assert "signals-replay" in build_research_registry(settings)
    with pytest.raises(SignalBookError) as info:
        _replay(world)
    assert info.value.code is SignalBookErrorCode.BOOK_NOT_RECORDED

    world.run(world.days[FIRST])
    handler = SignalsReplayJobHandler(settings, books_directory=world.directory)
    context = FakeContext({"book": BOOK})
    result = handler.run(context)  # type: ignore[arg-type]
    assert result["identical"] is True and result["sessions_replayed"] == len(ASSETS)
    assert context.logs == [("info", "signals_replay_completed")]

    path = world.directory / f"{BOOK}.yaml"
    path.write_text(path.read_text().replace("max_open: 2", "max_open: 3"))
    with pytest.raises(SignalBookError) as info:
        _replay(world)
    assert info.value.code is SignalBookErrorCode.BOOK_CHANGED
