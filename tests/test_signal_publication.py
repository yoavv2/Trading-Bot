"""Signal books and publication decisions (docs/SIGNALS-PLAN.md, Step 2)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from decimal import Decimal as D
from pathlib import Path

import pytest
import sqlalchemy as sa
from tests.support.migrated_db import migrated_database
from tests.support.research_fixtures import make_bars, seed_research_bars, xnys_sessions
from tests.test_signals_step1_engines import LEVEL_SPEC, SOURCE, START, approve

from trading_platform.core.settings import Settings, load_settings
from trading_platform.db.models.market_session import MarketSession
from trading_platform.db.models.research import StrategyVersion
from trading_platform.db.models.signal_book import (
    PublicationDecision,
    SignalBook,
    SignalPublication,
)
from trading_platform.db.session import session_scope
from trading_platform.services.research.publication_filters import (
    EntrySignal,
    OpenCapRule,
    PublicationOutcome,
    RuleKind,
    apply_rule,
)
from trading_platform.services.research.risk_metrics import RiskTrade
from trading_platform.services.signals.comparison import SignalState, outcomes_from_log
from trading_platform.services.signals.engine import run_signal_session
from trading_platform.services.signals.publication import (
    BookConfig,
    SignalBookError,
    SignalBookErrorCode,
    decide_publications,
    ensure_book,
    load_book_config,
    order_key,
)

ASSETS = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]
SESSIONS = 90
CAP = 2


@pytest.fixture()
def settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[Settings]:
    with migrated_database(monkeypatch, "signal_publication"):
        yield load_settings()


def _seed(settings: Settings) -> tuple[uuid.UUID, list[date]]:
    days = xnys_sessions(START, SESSIONS)
    with session_scope(settings) as session:
        for index, asset in enumerate(ASSETS):
            seed_research_bars(session, asset, make_bars(asset, days, seed=index + 1, volatility=0.03))
    return approve(settings, LEVEL_SPEC), days


def _config(version_spec_sha256: str, *, name: str = "test-book", max_open: int = CAP, first: date = START) -> BookConfig:
    return BookConfig.model_validate(
        {
            "name": name,
            "strategy_spec_sha256": version_spec_sha256,
            "assets": ASSETS,
            "max_open": max_open,
            "first_session": first.isoformat(),
            "bar_source": {"provider": SOURCE[0], "adjusted": SOURCE[1]},
        }
    )


def _book(settings: Settings, version_id: uuid.UUID, **kwargs: object) -> uuid.UUID:
    with session_scope(settings) as session:
        version = session.get(StrategyVersion, version_id)
        return ensure_book(session, _config(version.spec_sha256, **kwargs), "c" * 64).id  # type: ignore[arg-type]


def _close_of(session: sa.orm.Session, day: date) -> datetime:
    return session.execute(sa.select(MarketSession.market_close).where(MarketSession.session_date == day)).scalar_one()


def _run_day(settings: Settings, version_id: uuid.UUID, book_id: uuid.UUID, day: date, *, decide_at: datetime | None = None) -> None:
    """Every asset's session, then the publication decisions, as the daily Job does."""

    with session_scope(settings) as session:
        version = session.get(StrategyVersion, version_id)
        for asset in ASSETS:
            run_signal_session(session, settings, version=version, asset=asset, session_date=day, bar_source=SOURCE)
        decide_publications(session, session.get(SignalBook, book_id), now=decide_at or _close_of(session, day))


def _decisions(settings: Settings) -> dict[tuple[str, date], str]:
    with session_scope(settings) as session:
        return {(p.asset, p.signal_session): p.outcome for p in session.execute(sa.select(SignalPublication)).scalars()}


def test_daily_decisions_match_the_cap_rule_of_the_publication_filter_study(settings: Settings) -> None:
    version_id, days = _seed(settings)
    book_id = _book(settings, version_id)
    for day in days:
        _run_day(settings, version_id, book_id, day)

    # The study's rule, applied to the finished log in one pass.
    signals: list[EntrySignal] = []
    with session_scope(settings) as session:
        for asset in ASSETS:
            for outcome in outcomes_from_log(session, strategy_version_id=version_id, asset=asset, through=days[-1]):
                if outcome.signal_session == days[-1]:
                    continue  # issued at the last close: its entry is not resolved yet
                trade = None
                if outcome.state is not SignalState.EXPIRED:
                    trade = RiskTrade(
                        asset=asset,
                        signal_session=outcome.signal_session,
                        exit_session=outcome.exit_session,
                        quantity=D(1),
                        rule_entry_price=outcome.entry_price or D(0),
                        rule_exit_price=outcome.exit_price,
                        stop_price=outcome.stop,
                        target_price=outcome.target,
                        net_pnl=None,
                        exit_reason=outcome.exit_reason,
                        intrabar_order_unknown=bool(outcome.intrabar_order_unknown),
                    )
                signals.append(EntrySignal(asset, outcome.signal_session, trade, None if trade else outcome.entry_outcome))
    expected = {
        (signal.asset, signal.signal_session): (
            "held_back_open_cap" if outcome is PublicationOutcome.HELD_BACK_OPEN_CAP else "published"
        )
        for signal, outcome in apply_rule(signals, OpenCapRule(id="cap", kind=RuleKind.OPEN_CAP, max_open=CAP))
    }
    recorded = {key: value for key, value in _decisions(settings).items() if key[1] != days[-1]}
    assert recorded == expected
    assert len(expected) > 40 and set(expected.values()) == {"published", "held_back_open_cap"}

    # The cap held at every decision, and a second pass decides nothing again.
    with session_scope(settings) as session:
        rows = list(session.execute(sa.select(SignalPublication)).scalars())
        assert all(row.open_before < CAP for row in rows if row.outcome == "published")
        assert all(row.open_before == CAP for row in rows if row.outcome == "held_back_open_cap")
        assert all(row.order_key == order_key(row.signal_session, row.asset) for row in rows)
        assert decide_publications(session, session.get(SignalBook, book_id), now=_close_of(session, days[-1])) == []


def test_a_decision_at_or_after_the_next_open_is_held_back_as_late(settings: Settings) -> None:
    version_id, days = _seed(settings)
    book_id = _book(settings, version_id, max_open=50)
    with session_scope(settings) as session:
        next_open = session.execute(
            sa.select(MarketSession.market_open).where(MarketSession.session_date == days[1])
        ).scalar_one()
    _run_day(settings, version_id, book_id, days[0], decide_at=next_open)
    first = _decisions(settings)
    assert first and set(first.values()) == {"held_back_late"}

    # One second before the open is still in time.
    for index in range(1, 30):
        before_open = _next_open(settings, days[index + 1]) - timedelta(seconds=1)
        _run_day(settings, version_id, book_id, days[index], decide_at=before_open)
    later = {key: value for key, value in _decisions(settings).items() if key[1] != days[0]}
    assert later and set(later.values()) == {"published"}


def _next_open(settings: Settings, day: date) -> datetime:
    with session_scope(settings) as session:
        return session.execute(sa.select(MarketSession.market_open).where(MarketSession.session_date == day)).scalar_one()


def test_the_database_refuses_a_decision_the_rules_do_not_require(settings: Settings) -> None:
    version_id, days = _seed(settings)
    book_id = _book(settings, version_id, max_open=1)
    with session_scope(settings) as session:
        version = session.get(StrategyVersion, version_id)
        for asset in ASSETS:
            run_signal_session(session, settings, version=version, asset=asset, session_date=days[0], bar_source=SOURCE)
        issued = sorted(
            session.execute(sa.text("SELECT asset FROM signal_log WHERE event_type = 'issued'")).scalars(),
            key=lambda asset: order_key(days[0], asset),
        )
        in_time = _close_of(session, days[0])
    assert len(issued) >= 2
    late = _next_open(settings, days[1])

    def insert(asset: str, outcome: str, open_before: int, *, at: datetime = in_time, signal: date = days[0]) -> None:
        with session_scope(settings) as session:
            session.add(
                SignalPublication(
                    book_id=book_id,
                    asset=asset,
                    signal_session=signal,
                    outcome=outcome,
                    order_key=order_key(signal, asset),
                    open_before=open_before,
                    recorded_at=at,
                )
            )

    def refused(match: str, *args: object, **kwargs: object) -> None:
        with pytest.raises(sa.exc.IntegrityError, match=match):
            insert(*args, **kwargs)  # type: ignore[arg-type]

    refused("the rules require published", issued[0], "held_back_open_cap", 0)
    refused("the rules require published", issued[0], "held_back_late", 0)
    refused("the rules require published with 0 open", issued[0], "published", 1)
    refused("the rules require held_back_late", issued[0], "published", 0, at=late)
    refused("the asset is not in the book", "ZZZ", "published", 0)
    refused("no issued signal", issued[0], "published", 0, signal=days[1])
    refused("the rules require published", issued[0], "withdrawn", 0)  # outside the closed vocabulary

    insert(issued[0], "published", 0)
    refused("the signal already has a decision", issued[0], "published", 0)
    refused("the rules require held_back_open_cap", issued[1], "published", 1)
    refused("the rules require held_back_open_cap with 1 open", issued[1], "held_back_open_cap", 0)
    insert(issued[1], "held_back_open_cap", 1)

    for statement in ("UPDATE signal_publications SET outcome = 'published'", "DELETE FROM signal_publications",
                      "UPDATE signal_books SET max_open = 99", "DELETE FROM signal_books"):
        with pytest.raises(sa.exc.IntegrityError, match="append-only"), session_scope(settings) as session:
            session.execute(sa.text(statement))
    assert _decisions(settings) == {(issued[0], days[0]): "published", (issued[1], days[0]): "held_back_open_cap"}


def test_signals_before_the_first_session_of_the_book_are_not_decided(settings: Settings) -> None:
    version_id, days = _seed(settings)
    book_id = _book(settings, version_id, first=days[1], max_open=50)
    _run_day(settings, version_id, book_id, days[0])
    assert _decisions(settings) == {}
    _run_day(settings, version_id, book_id, days[1])
    assert {key[1] for key in _decisions(settings)} <= {days[1]}


# ---------------------------------------------------------------------------
# The book configuration
# ---------------------------------------------------------------------------

BOOK_YAML = """\
name: {name}
strategy_spec_sha256: {sha}
assets: [BBB, AAA]
max_open: 10
first_session: 2024-01-02
bar_source: {{provider: tiingo, adjusted: true}}
"""


def test_a_book_is_recorded_once_and_a_changed_configuration_is_refused(settings: Settings, tmp_path: Path) -> None:
    version_id, _ = _seed(settings)
    with session_scope(settings) as session:
        sha = session.get(StrategyVersion, version_id).spec_sha256  # type: ignore[union-attr]
    (tmp_path / "pilot.yaml").write_text(BOOK_YAML.format(name="pilot", sha=sha))
    config, digest = load_book_config("pilot", directory=tmp_path)
    assert (config.assets, config.max_open, config.exchange, len(digest)) == (["AAA", "BBB"], 10, "XNYS", 64)

    with session_scope(settings) as session:
        first = ensure_book(session, config, digest).id
    with session_scope(settings) as session:
        assert ensure_book(session, config, digest).id == first
        assert session.execute(sa.select(sa.func.count()).select_from(SignalBook)).scalar_one() == 1

    (tmp_path / "pilot.yaml").write_text(BOOK_YAML.format(name="pilot", sha=sha).replace("max_open: 10", "max_open: 12"))
    changed, changed_digest = load_book_config("pilot", directory=tmp_path)
    with pytest.raises(SignalBookError) as info, session_scope(settings) as session:
        ensure_book(session, changed, changed_digest)
    assert info.value.code is SignalBookErrorCode.BOOK_CHANGED


def test_book_configuration_errors_have_closed_codes(settings: Settings, tmp_path: Path) -> None:
    def code(name: str) -> SignalBookErrorCode:
        with pytest.raises(SignalBookError) as info:
            load_book_config(name, directory=tmp_path)
        return info.value.code

    assert code("../etc/passwd") is SignalBookErrorCode.BOOK_NAME_INVALID
    assert code("Pilot") is SignalBookErrorCode.BOOK_NAME_INVALID
    assert code("absent") is SignalBookErrorCode.BOOK_CONFIG_NOT_FOUND
    good = BOOK_YAML.format(name="pilot", sha="a" * 64)
    for label, text in {
        "other-name": good,
        "unknown-key": good.replace("name: pilot", "name: unknown-key") + "extra: 1\n",
        "zero-cap": good.replace("name: pilot", "name: zero-cap").replace("max_open: 10", "max_open: 0"),
        "repeated": good.replace("name: pilot", "name: repeated").replace("[BBB, AAA]", "[AAA, AAA]"),
        "no-assets": good.replace("name: pilot", "name: no-assets").replace("[BBB, AAA]", "[]"),
        "bad-sha": good.replace("name: pilot", "name: bad-sha").replace("a" * 64, "xyz"),
        "not-yaml": "name: [unclosed",
    }.items():
        (tmp_path / f"{label}.yaml").write_text(text)
        assert code(label) is SignalBookErrorCode.BOOK_CONFIG_INVALID, label

    (tmp_path / "pilot.yaml").write_text(good)
    config, digest = load_book_config("pilot", directory=tmp_path)
    with pytest.raises(SignalBookError) as info, session_scope(settings) as session:
        ensure_book(session, config, digest)
    assert info.value.code is SignalBookErrorCode.VERSION_NOT_FOUND
    assert PublicationDecision.HELD_BACK_LATE.value == "held_back_late"
