"""The hardened daily signal engine (docs/SIGNALS-PLAN.md, Step 2): recorded skips, the
input data digest and the transition order the database enforces."""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from datetime import date
from decimal import Decimal as D

import pytest
import sqlalchemy as sa
from tests.support.migrated_db import migrated_database
from tests.support.research_fixtures import Bar, seed_research_bars
from tests.test_signals_step1_engines import ASSET, LEVEL_SPEC, SOURCE, approve, scripted_bars

from trading_platform.core.settings import Settings, load_settings
from trading_platform.db.models.research import StrategyVersion
from trading_platform.db.models.signal_log import (
    SessionSkipReason,
    SignalLogEvent,
    SignalSessionRun,
    SignalSessionSkip,
)
from trading_platform.db.session import get_session_factory, session_scope
from trading_platform.services.signals.engine import (
    SessionOutcome,
    SignalEngineError,
    SignalEngineErrorCode,
    record_session_skip,
    run_signal_session,
    run_signal_sessions,
)

# Signal at the close of session 0, entry at the open of session 1, rule exit signalled at
# the close of session 2; session 3 opens below the stop, so the trade closes at the stop rule.
ROWS = [
    ("99", "101", "98", "100"),
    ("100", "101", "99", "99.5"),
    ("96", "97", "95.5", "89.5"),
    ("89", "90", "88", "89"),
    ("89", "90", "88", "89"),
]


@pytest.fixture()
def settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[Settings]:
    with migrated_database(monkeypatch, "signals_step2"):
        yield load_settings()


def _seed(settings: Settings, rows: list[tuple[str, str, str, str]] = ROWS) -> tuple[uuid.UUID, list[Bar]]:
    bars = scripted_bars(rows)
    with session_scope(settings) as session:
        seed_research_bars(session, ASSET, bars)
    return approve(settings, LEVEL_SPEC), bars


def _run(settings: Settings, version_id: uuid.UUID, start: date, end: date) -> list[SessionOutcome]:
    with session_scope(settings) as session:
        outcomes = run_signal_sessions(
            session,
            settings,
            version=session.get(StrategyVersion, version_id),
            asset=ASSET,
            start=start,
            end=end,
            bar_source=SOURCE,
        )
        return [SessionOutcome(o.session_date, o.already_processed, (), o.skipped) for o in outcomes]


def _count(settings: Settings, model: type) -> int:
    with session_scope(settings) as session:
        return session.execute(sa.select(sa.func.count()).select_from(model)).scalar_one()


def _delete_bars(settings: Settings, day: date) -> list[dict[str, object]]:
    with session_scope(settings) as session:
        rows = [dict(r) for r in session.execute(sa.text("SELECT * FROM daily_bars WHERE session_date = :d"), {"d": day}).mappings()]
        session.execute(sa.text("DELETE FROM daily_bars WHERE session_date = :d"), {"d": day})
    return rows


def _restore_bars(settings: Settings, rows: list[dict[str, object]]) -> None:
    table = sa.Table("daily_bars", sa.MetaData(), autoload_with=get_session_factory(settings)().get_bind())
    with session_scope(settings) as session:
        session.execute(sa.insert(table), rows)


# ---------------------------------------------------------------------------
# Skips
# ---------------------------------------------------------------------------


def test_a_missing_bar_is_recorded_as_a_skip_and_the_session_waits_for_its_data(settings: Settings) -> None:
    version_id, bars = _seed(settings)
    days = [bar.session_date for bar in bars]
    removed = _delete_bars(settings, days[2])

    outcomes = _run(settings, version_id, days[0], days[-1])
    assert [(o.session_date, o.skipped) for o in outcomes] == [
        (days[0], None),
        (days[1], None),
        (days[2], SessionSkipReason.MISSING_BAR),
    ]
    assert _count(settings, SignalSessionRun) == 2
    with session_scope(settings) as session:
        skip = session.execute(sa.select(SignalSessionSkip)).scalar_one()
        assert (skip.session_date, skip.reason, skip.detail) == (days[2], "missing_bar", None)
        events_before = session.execute(sa.select(sa.func.count()).select_from(SignalLogEvent)).scalar_one()

    # A second attempt records nothing new, and the session after the skip is refused.
    assert _run(settings, version_id, days[0], days[-1])[-1].skipped is SessionSkipReason.MISSING_BAR
    assert _count(settings, SignalSessionSkip) == 1
    with pytest.raises(SignalEngineError) as info, session_scope(settings) as session:
        run_signal_session(
            session,
            settings,
            version=session.get(StrategyVersion, version_id),
            asset=ASSET,
            session_date=days[3],
            bar_source=SOURCE,
        )
    assert info.value.code is SignalEngineErrorCode.SESSION_SKIPPED
    assert _count(settings, SignalLogEvent) == events_before

    # The data arrives: the skipped session is processed late and the rest follow.
    _restore_bars(settings, removed)
    outcomes = _run(settings, version_id, days[0], days[-1])
    assert [o.skipped for o in outcomes] == [None] * 5
    assert [o.already_processed for o in outcomes] == [True, True, False, False, False]
    with session_scope(settings) as session:
        types = [
            row.event_type
            for row in session.execute(
                sa.select(SignalLogEvent).order_by(SignalLogEvent.event_session, SignalLogEvent.recorded_at)
            ).scalars()
        ]
    assert types == ["issued", "entry_filled", "rule_exit_signalled", "closed_stop"]
    assert _count(settings, SignalSessionSkip) == 1  # the skip stays on record


def test_a_skip_for_another_reason_is_recorded_once_with_its_detail(settings: Settings) -> None:
    version_id, bars = _seed(settings)

    def skip(detail: str | None, reason: SessionSkipReason = SessionSkipReason.INTEGRITY_FAILED) -> bool:
        with session_scope(settings) as session:
            return record_session_skip(
                session,
                version=session.get(StrategyVersion, version_id),
                asset=ASSET,
                session_date=bars[0].session_date,
                reason=reason,
                detail=detail,
            )

    assert skip("ohlc_relation") is True
    assert skip("ohlc_relation") is False
    assert _count(settings, SignalSessionSkip) == 1
    with pytest.raises(sa.exc.IntegrityError, match="ck_signal_session_skips_detail_on_integrity"):
        skip("unexpected detail", SessionSkipReason.MISSING_BAR)


@pytest.mark.parametrize("statement", ["UPDATE signal_session_skips SET asset = 'X'", "DELETE FROM signal_session_skips"])
def test_the_skip_record_refuses_update_delete_and_unknown_reasons(settings: Settings, statement: str) -> None:
    version_id, bars = _seed(settings)
    _delete_bars(settings, bars[0].session_date)
    _run(settings, version_id, bars[0].session_date, bars[0].session_date)
    with pytest.raises(sa.exc.IntegrityError, match="append-only"), session_scope(settings) as session:
        session.execute(sa.text(statement))
    with pytest.raises(sa.exc.IntegrityError, match="ck_signal_session_skips_reason"), session_scope(settings) as session:
        session.add(
            SignalSessionSkip(
                strategy_version_id=version_id,
                asset=ASSET,
                session_date=bars[1].session_date,
                reason="holiday",
                recorded_at=sa.func.now(),
            )
        )
    assert _count(settings, SignalSessionSkip) == 1


# ---------------------------------------------------------------------------
# Input data digest
# ---------------------------------------------------------------------------


def _session_digests(settings: Settings, version_id: uuid.UUID, bars: list[Bar]) -> dict[date, str]:
    """Run every session in a transaction that is rolled back; return each session's digest."""

    session = get_session_factory(settings)()
    try:
        run_signal_sessions(
            session,
            settings,
            version=session.get(StrategyVersion, version_id),
            asset=ASSET,
            start=bars[0].session_date,
            end=bars[-1].session_date,
            bar_source=SOURCE,
        )
        runs = {r.session_date: r.input_data_digest for r in session.execute(sa.select(SignalSessionRun)).scalars()}
        for event in session.execute(sa.select(SignalLogEvent)).scalars():
            assert event.input_data_digest == runs[event.event_session]
        return runs
    finally:
        session.rollback()
        session.close()


def test_every_row_carries_the_digest_of_its_session_inputs_and_a_replay_reproduces_it(settings: Settings) -> None:
    version_id, bars = _seed(settings)
    first = _session_digests(settings, version_id, bars)
    assert len(first) == 5 and all(re.fullmatch(r"[0-9a-f]{64}", digest) for digest in first.values())
    assert len(set(first.values())) == 5
    assert _session_digests(settings, version_id, bars) == first

    # Changing one bar changes the digest of the session that read it and of no other
    # (this strategy reads one bar of history).
    changed = bars[3].session_date
    with session_scope(settings) as session:
        session.execute(
            sa.text("UPDATE daily_bars SET volume = volume + 1 WHERE session_date = :d AND adjusted"), {"d": changed}
        )
    second = _session_digests(settings, version_id, bars)
    assert {day for day in first if first[day] != second[day]} == {changed}


# ---------------------------------------------------------------------------
# Transition order, enforced by the database
# ---------------------------------------------------------------------------


def _insert(settings: Settings, version_id: uuid.UUID, event_type: str, signal: date, event: date, **overrides: object) -> None:
    values: dict[str, object] = {
        "id": uuid.uuid4(),
        "strategy_version_id": version_id,
        "asset": ASSET,
        "signal_session": signal,
        "event_type": event_type,
        "event_session": event,
        "spec_sha256": "a" * 64,
        "reference_price": D(100),
        "stop_price": D(95),
        "target_price": D(110),
        "price": D(100) if event_type in {"entry_filled", "closed_stop", "closed_target", "closed_rule"} else None,
        "expiry_reason": "expired_open_at_or_below_stop" if event_type == "expired" else None,
        "intrabar_order_unknown": False if event_type.startswith("closed_") else None,
        "input_data_digest": "b" * 64,
        "adjustment_factor": D(1),
        "effective_stop_price": D(95),
        "effective_target_price": D(110),
        "recorded_at": sa.func.now(),
    } | overrides
    with session_scope(settings) as session:
        session.execute(sa.insert(SignalLogEvent).values(**values))


def test_the_database_refuses_events_out_of_order(settings: Settings) -> None:
    version_id, bars = _seed(settings)
    d = [bar.session_date for bar in bars]

    def refused(event_type: str, signal: date, event: date, problem: str, **overrides: object) -> None:
        before = _count(settings, SignalLogEvent)
        with pytest.raises(sa.exc.IntegrityError, match=f"illegal transition to {event_type}: {problem}"):
            _insert(settings, version_id, event_type, signal, event, **overrides)
        assert _count(settings, SignalLogEvent) == before

    refused("entry_filled", d[0], d[1], "the signal was never issued")
    refused("closed_stop", d[0], d[1], "the signal was never issued")

    _insert(settings, version_id, "issued", d[0], d[0])
    refused("issued", d[0], d[0], "the signal is already issued")
    refused("issued", d[1], d[1], "another signal of this version and asset is still active")
    refused("closed_target", d[0], d[1], "the signal has not been entered")
    refused("rule_exit_signalled", d[0], d[1], "the signal has not been entered")
    refused("entry_filled", d[0], d[1], "the levels or the specification differ", stop_price=D(94))
    refused("entry_filled", d[0], d[1], "the levels or the specification differ", spec_sha256="c" * 64)

    _insert(settings, version_id, "entry_filled", d[0], d[1])
    refused("entry_filled", d[0], d[2], "the signal has already been entered")
    refused("expired", d[0], d[2], "the signal has already been entered")
    _insert(settings, version_id, "rule_exit_signalled", d[0], d[2])
    refused("closed_rule", d[0], d[1], "the event is before an earlier event of the signal")

    _insert(settings, version_id, "closed_rule", d[0], d[3])
    refused("closed_stop", d[0], d[4], "the signal has already expired or closed")
    refused("rule_exit_signalled", d[0], d[4], "the signal has already expired or closed")
    refused("issued", d[0], d[0], "the signal is already issued")

    # After the close a new signal may be issued, on the same session or later, never earlier.
    _insert(settings, version_id, "issued", d[3], d[3])
    _insert(settings, version_id, "expired", d[3], d[4])
    refused("entry_filled", d[3], d[4], "the signal has already expired or closed")
    refused("issued", d[2], d[2], "a later or equal signal exists")


def test_the_database_refuses_a_row_without_an_input_digest_and_a_session_out_of_order(settings: Settings) -> None:
    version_id, bars = _seed(settings)
    d = [bar.session_date for bar in bars]
    for digest in (None, "b" * 63, "B" * 64):
        with pytest.raises(sa.exc.IntegrityError, match="ck_signal_log_input_data_digest"):
            _insert(settings, version_id, "issued", d[0], d[0], input_data_digest=digest)

    def add_run(day: date, digest: str | None = "b" * 64) -> None:
        with session_scope(settings) as session:
            session.add(
                SignalSessionRun(
                    strategy_version_id=version_id,
                    asset=ASSET,
                    session_date=day,
                    spec_sha256="a" * 64,
                    input_data_digest=digest,
                    recorded_at=sa.func.now(),
                )
            )

    add_run(d[1])
    for day in (d[0], d[1]):
        with pytest.raises(sa.exc.IntegrityError, match="not after the last processed session"):
            add_run(day)
    with pytest.raises(sa.exc.IntegrityError, match="ck_signal_session_runs_input_data_digest"):
        add_run(d[2], None)
    add_run(d[2])
    assert _count(settings, SignalSessionRun) == 2
