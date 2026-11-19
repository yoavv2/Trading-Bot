"""Signals Step 1: the backtest engine and the daily signal engine apply the same entry
and exit rules (docs/SIGNALS-PLAN.md).

Each case approves a v2 specification through the strategy service, seeds scripted bars
into a migrated database, runs the backtest engine and the daily engine over the same
sessions, and asserts both recorded the same outcome. The last tests cover the signal
log itself: append-only, idempotent re-runs, no skipped sessions.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import date
from decimal import Decimal as D

import pytest
import sqlalchemy as sa
from tests.support.migrated_db import migrated_database
from tests.support.research_fixtures import Bar, make_bars, seed_research_bars, xnys_sessions

from trading_platform.core.settings import Settings, load_settings
from trading_platform.db.models import (
    BacktestTrade,
    StrategyRun,
    StrategyRunStatus,
    StrategyRunType,
)
from trading_platform.db.models.research import StrategyVersion
from trading_platform.db.models.signal_log import SignalEventType, SignalLogEvent, SignalSessionRun
from trading_platform.db.session import session_scope
from trading_platform.services.backtesting import EngineOptions, _execute_backtest_run
from trading_platform.services.bootstrap import ensure_strategy_record
from trading_platform.services.research.strategies import ResearchStrategyService
from trading_platform.services.signals.comparison import (
    SignalOutcome,
    SignalState,
    differences,
    net_r_from_backtest,
    outcomes_from_backtest,
    outcomes_from_log,
)
from trading_platform.services.signals.engine import (
    SignalEngineError,
    SignalEngineErrorCode,
    run_signal_session,
    run_signal_sessions,
)
from trading_platform.strategies.research_registry import strategy_from_version

ASSET = "TEST"
SOURCE = ("tiingo", True)
START = date(2024, 1, 2)

# Enters on a close at or above 100, exits on a close below 90. One bar of history, so a
# handful of scripted bars drives it. Stop 5% below the reference, target 2R above it.
LEVEL_SPEC = """\
spec_version: 2
name: Level entry with stop and target
timeframe: daily
direction: long_only
entry:
  all_of:
    - {left: close, op: ge, right: 100}
exit:
  any_of:
    - {left: close, op: lt, right: 90}
risk:
  stop: {type: percent, value: 5}
  target: {type: risk_multiple, value: 2}
  min_reward_risk: 1
"""

# A moving-average strategy for the long comparison run.
SMA_SPEC = """\
spec_version: 2
name: Close above the 10-session average
timeframe: daily
direction: long_only
indicators:
  sma10: {type: sma, source: close, window: 10}
  sma30: {type: sma, source: close, window: 30}
entry:
  all_of:
    - {left: close, op: gt, right: sma10}
exit:
  any_of:
    - {left: close, op: lt, right: sma30}
risk:
  stop: {type: percent, value: 3}
  target: {type: risk_multiple, value: 1.5}
  min_reward_risk: 1.2
"""


@pytest.fixture()
def settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[Settings]:
    with migrated_database(monkeypatch, "signals_step1"):
        yield load_settings()


def scripted_bars(rows: list[tuple[str, str, str, str]]) -> list[Bar]:
    """``(open, high, low, close)`` per consecutive XNYS session from ``START``."""

    sessions = xnys_sessions(START, len(rows))
    return [
        Bar(symbol=ASSET, session_date=day, open=D(open_), high=D(high), low=D(low), close=D(close), volume=1_000_000)
        for day, (open_, high, low, close) in zip(sessions, rows, strict=True)
    ]


def approve(settings: Settings, yaml_text: str) -> uuid.UUID:
    service = ResearchStrategyService(settings)
    draft = service.create_draft(title="signals step 1", yaml_text=yaml_text)
    return uuid.UUID(service.approve_draft(uuid.UUID(draft["draft_id"]))["version_id"])


def run_backtest(
    settings: Settings, version_id: uuid.UUID, bars: list[Bar], *, slippage_bps: str = "0", commission: str = "0"
) -> uuid.UUID:
    with session_scope(settings) as session:
        version = session.get(StrategyVersion, version_id)
        strategy = strategy_from_version(settings, version, universe=(ASSET,), bar_source=SOURCE)
        record = ensure_strategy_record(session, strategy.metadata)
        run = StrategyRun(
            strategy_id=record.id,
            run_type=StrategyRunType.BACKTEST,
            status=StrategyRunStatus.RUNNING,
            trigger_source="test",
            parameters_snapshot={},
            result_summary={},
        )
        session.add(run)
        session.flush()
        run_id = run.id
    _execute_backtest_run(
        settings,
        run_id=run_id,
        strategy=strategy,
        from_date=bars[0].session_date,
        to_date=bars[-1].session_date,
        options=EngineOptions(
            initial_capital=D(100000),
            commission_per_order=D(commission),
            slippage_bps=D(slippage_bps),
            max_concurrent_positions=1,
            quantity_policy="fractional",
            bar_provider=SOURCE[0],
            bar_adjusted=SOURCE[1],
            research=True,
        ),
    )
    return run_id


def run_daily(settings: Settings, version_id: uuid.UUID, bars: list[Bar]) -> None:
    with session_scope(settings) as session:
        run_signal_sessions(
            session,
            settings,
            version=session.get(StrategyVersion, version_id),
            asset=ASSET,
            start=bars[0].session_date,
            end=bars[-1].session_date,
            bar_source=SOURCE,
        )


def both_engines(
    settings: Settings, yaml_text: str, bars: list[Bar], *, slippage_bps: str = "0", commission: str = "0"
) -> tuple[list[SignalOutcome], list[SignalOutcome], uuid.UUID, uuid.UUID]:
    with session_scope(settings) as session:
        seed_research_bars(session, ASSET, bars)
    version_id = approve(settings, yaml_text)
    run_id = run_backtest(settings, version_id, bars, slippage_bps=slippage_bps, commission=commission)
    run_daily(settings, version_id, bars)
    with session_scope(settings) as session:
        backtest = outcomes_from_backtest(session, run_id=run_id, asset=ASSET)
        log = outcomes_from_log(session, strategy_version_id=version_id, asset=ASSET, through=bars[-1].session_date)
    return backtest, log, version_id, run_id


def log_events(settings: Settings, version_id: uuid.UUID) -> list[tuple[str, date, D | None]]:
    with session_scope(settings) as session:
        rows = session.execute(
            sa.select(SignalLogEvent)
            .where(SignalLogEvent.strategy_version_id == version_id)
            .order_by(SignalLogEvent.event_session, SignalLogEvent.recorded_at, SignalLogEvent.event_type)
        ).scalars()
        return [(row.event_type, row.event_session, row.price) for row in rows]


def trades(settings: Settings, run_id: uuid.UUID) -> list[BacktestTrade]:
    with session_scope(settings) as session:
        rows = list(session.execute(sa.select(BacktestTrade).where(BacktestTrade.strategy_run_id == run_id)).scalars())
        session.expunge_all()
        return rows


# ---------------------------------------------------------------------------
# The three shared cases
# ---------------------------------------------------------------------------


def test_open_beyond_the_target_expires_the_signal_in_both_engines(settings: Settings) -> None:
    bars = scripted_bars(
        [
            ("99", "101", "98", "100"),  # close 100: signal, stop 95, target 110
            ("111", "112", "108", "80"),  # open 111 is beyond the target
            ("80", "81", "79", "80"),
        ]
    )
    backtest, log, version_id, run_id = both_engines(settings, LEVEL_SPEC, bars)
    expected = [
        SignalOutcome(
            signal_session=bars[0].session_date,
            reference=D(100),
            stop=D(95),
            target=D(110),
            state=SignalState.EXPIRED,
            entry_outcome="expired_open_at_or_above_target",
            entry_session=None,
            entry_price=None,
            exit_session=None,
            exit_price=None,
            exit_reason=None,
            intrabar_order_unknown=None,
            gross_r=None,
        )
    ]
    assert backtest == expected and log == expected
    assert trades(settings, run_id) == []
    assert log_events(settings, version_id) == [
        ("issued", bars[0].session_date, None),
        ("expired", bars[1].session_date, None),
    ]


def test_gap_through_the_stop_exits_at_the_open_in_both_engines(settings: Settings) -> None:
    bars = scripted_bars(
        [
            ("99", "101", "98", "100"),  # signal
            ("100", "101", "99", "99.5"),  # entry at 100; range stays inside the levels
            ("90", "91", "88", "89.5"),  # open 90 is through the stop at 95
            ("89", "90", "88", "89"),
        ]
    )
    backtest, log, version_id, run_id = both_engines(settings, LEVEL_SPEC, bars)
    expected = [
        SignalOutcome(
            signal_session=bars[0].session_date,
            reference=D(100),
            stop=D(95),
            target=D(110),
            state=SignalState.CLOSED_STOP,
            entry_outcome="opened",
            entry_session=bars[1].session_date,
            entry_price=D(100),
            exit_session=bars[2].session_date,
            exit_price=D(90),  # the open, below the stop
            exit_reason="stop",
            intrabar_order_unknown=False,
            gross_r=D(-2),  # lost 10 against an initial risk of 5
        )
    ]
    assert backtest == expected and log == expected
    (trade,) = trades(settings, run_id)
    assert (trade.entry_price, trade.exit_price, trade.status) == (D(100), D(90), "closed")
    assert (trade.reference_price, trade.stop_price, trade.target_price) == (D(100), D(95), D(110))
    assert trade.exit_signal_session is None
    assert log_events(settings, version_id) == [
        ("issued", bars[0].session_date, None),
        ("entry_filled", bars[1].session_date, D(100)),
        ("closed_stop", bars[2].session_date, D(90)),
    ]


def test_one_bar_touching_both_levels_is_a_flagged_stop_exit_in_both_engines(settings: Settings) -> None:
    bars = scripted_bars(
        [
            ("99", "101", "98", "100"),  # signal
            ("100", "101", "99", "99.5"),  # entry at 100
            ("100", "111", "94", "99"),  # high 111 and low 94: both levels touched
            ("99", "99.5", "98", "99"),
        ]
    )
    backtest, log, version_id, run_id = both_engines(settings, LEVEL_SPEC, bars)
    expected = [
        SignalOutcome(
            signal_session=bars[0].session_date,
            reference=D(100),
            stop=D(95),
            target=D(110),
            state=SignalState.CLOSED_STOP,
            entry_outcome="opened",
            entry_session=bars[1].session_date,
            entry_price=D(100),
            exit_session=bars[2].session_date,
            exit_price=D(95),
            exit_reason="stop",
            intrabar_order_unknown=True,
            gross_r=D(-1),
        )
    ]
    assert backtest == expected and log == expected
    (trade,) = trades(settings, run_id)
    assert (trade.exit_price, trade.exit_reason, trade.intrabar_order_unknown) == (D(95), "stop", True)
    assert log_events(settings, version_id)[-1] == ("closed_stop", bars[2].session_date, D(95))
    with session_scope(settings) as session:
        summary = session.get(StrategyRun, run_id).result_summary
    assert summary["risk"]["intrabar_order_unknown_exits"] == 1
    assert summary["risk"]["exits_by_reason"] == {"stop": 1, "target": 0, "rule": 0}


# ---------------------------------------------------------------------------
# Further rule paths through both engines
# ---------------------------------------------------------------------------


def test_reward_to_risk_below_the_minimum_expires_and_the_entry_bar_can_hit_the_target(settings: Settings) -> None:
    bars = scripted_bars(
        [
            ("99", "101", "98", "100"),  # signal A: stop 95, target 110
            ("103", "104", "101", "101"),  # open 103: reward 7 over risk 8 is below 1; A expires. Close 101: signal B
            ("101", "107.5", "100", "99"),  # B (stop 95.95, target 111.10) opens at 101
            ("99", "112", "98", "99"),  # high 112 touches B's target
        ]
    )
    backtest, log, version_id, run_id = both_engines(settings, LEVEL_SPEC, bars)
    assert backtest == log
    first, second = log
    assert (first.entry_outcome, first.entry_session) == ("expired_reward_risk_below_minimum", None)
    assert (second.reference, second.stop, second.target) == (D(101), D("95.95"), D("111.10"))
    assert (second.entry_outcome, second.exit_reason, second.exit_session) == ("opened", "target", bars[3].session_date)
    (trade,) = trades(settings, run_id)
    assert (trade.entry_price, trade.exit_price) == (D(101), D("111.10"))


def test_rule_exit_executes_at_the_next_open_and_a_gap_beyond_the_stop_takes_precedence(settings: Settings) -> None:
    bars = scripted_bars(
        [
            ("99", "101", "98", "100"),  # signal A
            ("100", "101", "96", "100"),  # A opens at 100
            ("99", "100", "95.5", "96"),  # close 96: the exit rule is true (stop 95 not touched)
            ("96", "104", "94", "104"),  # rule exit at 96 before the low touches the stop; close 104: signal B
            ("104", "105", "99", "99"),  # B (stop 98.8, target 114.4) opens at 104; close 99: exit rule true
            ("97", "98", "96", "97"),  # open 97 is below B's stop: a stop exit, not a rule exit
        ]
    )
    backtest, log, version_id, run_id = both_engines(settings, LEVEL_SPEC.replace("right: 90", "right: 100"), bars)
    assert backtest == log
    first, second = log
    assert (first.exit_reason, first.exit_session, first.intrabar_order_unknown) == ("rule", bars[3].session_date, False)
    assert (second.exit_reason, second.exit_session) == ("stop", bars[5].session_date)
    by_entry = {t.entry_fill_session: t for t in trades(settings, run_id)}
    assert by_entry[bars[1].session_date].exit_price == D(96)
    assert by_entry[bars[1].session_date].exit_signal_session == bars[2].session_date
    assert by_entry[bars[4].session_date].exit_price == D(97)
    assert by_entry[bars[4].session_date].exit_signal_session is None
    assert [e[0] for e in log_events(settings, version_id) if e[0].startswith(("rule", "closed"))] == [
        "rule_exit_signalled",
        "closed_rule",
        "rule_exit_signalled",
        "closed_stop",
    ]


def test_the_log_matches_the_backtest_of_the_same_version_over_a_long_run(settings: Settings) -> None:
    sessions = xnys_sessions(date(2020, 1, 2), 600)
    bars = make_bars(ASSET, sessions, seed=11, volatility=0.02)
    # Slippage changes the backtest's fill prices, never what the rules decide.
    backtest, log, version_id, run_id = both_engines(settings, SMA_SPEC, bars, slippage_bps="5")
    assert differences(log, backtest) == []
    assert len(log) > 40
    outcomes = {o.entry_outcome for o in log}
    assert {"opened", "expired_reward_risk_below_minimum"} <= outcomes
    assert {"stop", "target", "rule"} <= {o.exit_reason for o in log if o.exit_reason}
    assert any(o.intrabar_order_unknown is False for o in log)
    # Rule prices and gross R agree although the backtest's fills carry slippage.
    closed = [o for o in log if o.gross_r is not None]
    assert len(closed) > 20
    assert all(o.entry_price is not None and o.entry_price > o.stop for o in closed)
    by_signal = {t.entry_signal_session: t for t in trades(settings, run_id)}
    assert all(by_signal[o.signal_session].entry_price > o.entry_price for o in closed)  # fill above the open
    with session_scope(settings) as session:
        net = net_r_from_backtest(session, run_id=run_id, asset=ASSET)
    assert set(net) == {o.signal_session for o in closed}
    assert all(net[o.signal_session] < o.gross_r for o in closed)  # costs only ever reduce R


# ---------------------------------------------------------------------------
# The final state, rule prices and R of each signal
# ---------------------------------------------------------------------------


def test_every_final_state_and_its_gross_r_agree_between_the_engines(settings: Settings) -> None:
    bars = scripted_bars(
        [
            ("99", "101", "98", "100"),  # signal A: stop 95, target 110
            ("101", "110", "100", "99"),  # A opens at 101 (risk 6); high 110 touches the target
            ("99", "99.5", "96", "89"),  # flat; close 89: no entry
            ("96", "101", "95.5", "100"),  # close 100: signal B, stop 95, target 110
            ("100", "101", "96", "89"),  # B opens at 100; close 89: the exit rule is true
            ("98", "101", "97", "100"),  # B rule exit at the open 98. Close 100: signal C
            ("111", "112", "108", "100"),  # C: open 111 is beyond the target; expires. Close 100: signal D
            ("100", "101", "99", "99.5"),  # D opens at 100 and stays open
        ]
    )
    backtest, log, _version_id, _run_id = both_engines(settings, LEVEL_SPEC, bars)
    assert differences(log, backtest) == []
    assert [(o.state, o.entry_price, o.exit_price, o.gross_r) for o in log] == [
        (SignalState.CLOSED_TARGET, D(101), D(110), D(9) / D(6)),  # risk is 101 - 95, not 100 - 95
        (SignalState.CLOSED_RULE, D(100), D(98), D(-2) / D(5)),
        (SignalState.EXPIRED, None, None, None),
        (SignalState.OPEN, D(100), None, None),
    ]


def test_net_r_uses_the_backtest_cost_model_over_the_same_initial_risk(settings: Settings) -> None:
    bars = scripted_bars(
        [
            ("99", "101", "98", "100"),  # signal: stop 95, target 110
            ("100", "101", "99", "99.5"),  # entry at 100
            ("100", "111", "96", "99"),  # target touched: exit at 110
            ("99", "100", "98", "99"),
        ]
    )
    # 100 bps a side and 10 per order on 100000: entry fill 101, exit fill 108.9.
    backtest, log, _version_id, run_id = both_engines(settings, LEVEL_SPEC, bars, slippage_bps="100", commission="10")
    assert differences(log, backtest) == []
    (outcome,) = log
    assert (outcome.entry_price, outcome.exit_price, outcome.gross_r) == (D(100), D(110), D(2))
    (trade,) = trades(settings, run_id)
    assert (trade.rule_entry_price, trade.rule_exit_price) == (D(100), D(110))
    assert (trade.entry_price, trade.exit_price) == (D(101), D("108.9"))
    with session_scope(settings) as session:
        net = net_r_from_backtest(session, run_id=run_id, asset=ASSET)
    # Net profit over quantity * (rule entry 100 - stop 95); the fill price is not the denominator.
    assert net == {bars[0].session_date: trade.net_pnl / (trade.quantity * D(5))}
    assert D("1.5") < net[bars[0].session_date] < D("1.58")  # (108.9 - 101) / 5 = 1.58 before commissions


# ---------------------------------------------------------------------------
# min_reward_risk boundaries (plan example: stop 95, target 110)
# ---------------------------------------------------------------------------


def _level_spec(min_reward_risk: str) -> str:
    return LEVEL_SPEC.replace("min_reward_risk: 1\n", f"min_reward_risk: {min_reward_risk}\n")


@pytest.mark.parametrize(
    ("open_price", "min_reward_risk", "opens"),
    [
        ("101", "1.5", True),  # reward 9 over risk 6 is exactly 1.5: at the threshold opens
        ("101.000001", "1.5", False),  # one price tick above: just below 1.5
        ("102", "1.14", True),  # reward 8 over risk 7 = 1.142857...
        ("102", "1.15", False),
        ("102", "1.142857", True),  # the exact ratio 8/7 decides, not a rounded one
        ("102", "1.142858", False),
    ],
)
def test_min_reward_risk_boundary_in_both_engines(
    settings: Settings, open_price: str, min_reward_risk: str, opens: bool
) -> None:
    bars = scripted_bars(
        [
            ("99", "101", "98", "100"),  # signal: stop 95, target 110
            (open_price, "103", "100.5", "99"),  # close 99: no new signal
            ("99", "100", "98", "99"),
        ]
    )
    backtest, log, _version_id, run_id = both_engines(settings, _level_spec(min_reward_risk), bars)
    assert differences(log, backtest) == []
    (outcome,) = log
    if opens:
        assert (outcome.state, outcome.entry_price) == (SignalState.OPEN, D(open_price))
        assert len(trades(settings, run_id)) == 1
    else:
        assert (outcome.state, outcome.entry_outcome) == (SignalState.EXPIRED, "expired_reward_risk_below_minimum")
        assert trades(settings, run_id) == []


# ---------------------------------------------------------------------------
# The signal log
# ---------------------------------------------------------------------------


def _one_trade(settings: Settings) -> tuple[uuid.UUID, list[Bar]]:
    bars = scripted_bars(
        [
            ("99", "101", "98", "100"),
            ("100", "101", "99", "99.5"),
            ("90", "91", "88", "89.5"),
            ("89", "90", "88", "89"),
        ]
    )
    with session_scope(settings) as session:
        seed_research_bars(session, ASSET, bars)
    return approve(settings, LEVEL_SPEC), bars


def _counts(settings: Settings) -> tuple[int, int]:
    with session_scope(settings) as session:
        return (
            session.execute(sa.select(sa.func.count()).select_from(SignalLogEvent)).scalar_one(),
            session.execute(sa.select(sa.func.count()).select_from(SignalSessionRun)).scalar_one(),
        )


def test_a_rerun_of_processed_sessions_writes_nothing(settings: Settings) -> None:
    version_id, bars = _one_trade(settings)
    run_daily(settings, version_id, bars)
    before = _counts(settings)
    assert before == (3, 4)
    with session_scope(settings) as session:
        outcomes = run_signal_sessions(
            session,
            settings,
            version=session.get(StrategyVersion, version_id),
            asset=ASSET,
            start=bars[0].session_date,
            end=bars[-1].session_date,
            bar_source=SOURCE,
        )
    assert all(o.already_processed and o.events == () for o in outcomes)
    assert _counts(settings) == before


@pytest.mark.parametrize("statement", ["UPDATE {table} SET asset = 'X'", "DELETE FROM {table}"])
@pytest.mark.parametrize("table", ["signal_log", "signal_session_runs"])
def test_the_log_refuses_update_and_delete(settings: Settings, table: str, statement: str) -> None:
    version_id, bars = _one_trade(settings)
    run_daily(settings, version_id, bars)
    before = _counts(settings)
    with pytest.raises(sa.exc.IntegrityError, match="append-only"):
        with session_scope(settings) as session:
            session.execute(sa.text(statement.format(table=table)))
    assert _counts(settings) == before


def test_the_database_refuses_a_second_event_of_the_same_type_and_unknown_vocabulary(settings: Settings) -> None:
    version_id, bars = _one_trade(settings)
    run_daily(settings, version_id, bars)

    def insert(**overrides: object) -> None:
        values: dict[str, object] = {
            "id": uuid.uuid4(),
            "strategy_version_id": version_id,
            "asset": ASSET,
            "signal_session": bars[3].session_date,
            "event_type": "issued",
            "event_session": bars[3].session_date,
            "spec_sha256": "0" * 64,
            "reference_price": D(100),
            "stop_price": D(95),
            "target_price": D(110),
            "price": None,
            "expiry_reason": None,
            "intrabar_order_unknown": None,
            "recorded_at": sa.func.now(),
        } | overrides
        with session_scope(settings) as session:
            session.execute(sa.insert(SignalLogEvent).values(**values))

    refused: list[dict[str, object]] = [
        {"signal_session": bars[0].session_date, "event_session": bars[0].session_date},  # duplicate issued
        {"event_type": "cancelled"},
        {"event_type": "expired", "event_session": bars[3].session_date},  # an expiry on the signal session
        {"event_type": "expired", "signal_session": bars[2].session_date, "expiry_reason": "changed_my_mind"},
        {"event_type": "expired", "signal_session": bars[2].session_date},  # expiry without a reason
        {"stop_price": D(101)},  # stop above the reference
        {"price": D(100)},  # a price on an event that is not a fill
        {"intrabar_order_unknown": True},  # the flag on an event that is not a stop exit
        {"event_type": "closed_target", "signal_session": bars[2].session_date, "price": D(110), "intrabar_order_unknown": True},
    ]
    for overrides in refused:
        with pytest.raises(sa.exc.IntegrityError):
            insert(**overrides)
    insert()  # the valid row is accepted, so the refusals above are the constraints'


def test_sessions_cannot_be_skipped_or_run_out_of_order_and_refusals_write_nothing(settings: Settings) -> None:
    version_id, bars = _one_trade(settings)

    def run(day: date) -> None:
        with session_scope(settings) as session:
            run_signal_session(
                session,
                settings,
                version=session.get(StrategyVersion, version_id),
                asset=ASSET,
                session_date=day,
                bar_source=SOURCE,
            )

    def refused(day: date) -> SignalEngineErrorCode:
        before = _counts(settings)
        with pytest.raises(SignalEngineError) as info:
            run(day)
        assert _counts(settings) == before
        return info.value.code

    run(bars[1].session_date)
    assert refused(bars[3].session_date) is SignalEngineErrorCode.SESSION_SKIPPED
    assert refused(bars[0].session_date) is SignalEngineErrorCode.SESSION_OUT_OF_ORDER
    assert refused(date(2024, 1, 6)) is SignalEngineErrorCode.NOT_A_SESSION  # a Saturday
    with session_scope(settings) as session:
        session.execute(
            sa.text("DELETE FROM daily_bars WHERE session_date = :day"), {"day": bars[2].session_date}
        )
    assert refused(bars[2].session_date) is SignalEngineErrorCode.MISSING_BAR


def test_a_v1_version_is_refused_by_the_daily_engine(settings: Settings) -> None:
    _, bars = _one_trade(settings)
    v1_text = LEVEL_SPEC.replace("spec_version: 2", "spec_version: 1").split("risk:")[0]
    version_id = approve(settings, v1_text)
    with pytest.raises(SignalEngineError) as info:
        run_daily(settings, version_id, bars)
    assert info.value.code is SignalEngineErrorCode.VERSION_HAS_NO_RISK_PLAN
    assert _counts(settings) == (0, 0)


def test_every_event_carries_the_version_and_its_specification_digest(settings: Settings) -> None:
    version_id, bars = _one_trade(settings)
    run_daily(settings, version_id, bars)
    with session_scope(settings) as session:
        digest = session.get(StrategyVersion, version_id).spec_sha256
        rows = list(session.execute(sa.select(SignalLogEvent)).scalars())
        assert rows and all((r.strategy_version_id, r.spec_sha256) == (version_id, digest) for r in rows)
        assert {r.event_type for r in rows} == {
            SignalEventType.ISSUED,
            SignalEventType.ENTRY_FILLED,
            SignalEventType.CLOSED_STOP,
        }
