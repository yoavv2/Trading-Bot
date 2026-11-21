"""Publication rules over a version's recorded signals (signals plan, Step 1 status)."""

from __future__ import annotations

import random
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from trading_platform.services.research.publication_filters import (
    EntrySignal,
    MinInitialRiskRule,
    NoRule,
    OpenCapRule,
    PublicationOutcome,
    PublicationProtocol,
    RuleKind,
    SourceDataError,
    TargetRate,
    apply_rule,
    initial_risk_fraction,
    load_protocol,
    max_open_published,
    measure,
    read_verdict,
    session_order_key,
)
from trading_platform.services.research.risk_metrics import RiskTrade

DAY0 = date(2024, 1, 1)
PROTOCOL = (
    Path(__file__).resolve().parents[1]
    / "config"
    / "research"
    / "signals_v2_publication_filters.yaml"
)


def _day(offset: int) -> date:
    return DAY0 + timedelta(days=offset)


def _trade(
    asset: str,
    signal: int,
    exit_: int | None,
    *,
    entry: str = "100",
    stop: str = "95",
    exit_price: str = "110",
) -> RiskTrade:
    closed = exit_ is not None
    quantity = Decimal(10)
    return RiskTrade(
        asset=asset,
        signal_session=_day(signal),
        exit_session=_day(exit_) if closed else None,
        quantity=quantity,
        rule_entry_price=Decimal(entry),
        rule_exit_price=Decimal(exit_price) if closed else None,
        stop_price=Decimal(stop),
        target_price=Decimal("110"),
        net_pnl=(Decimal(exit_price) - Decimal(entry)) * quantity if closed else None,
        exit_reason="target" if closed else None,
        intrabar_order_unknown=False,
    )


def _signal(asset: str, signal: int, exit_: int | None = None, **kw: str) -> EntrySignal:
    return EntrySignal(asset, _day(signal), _trade(asset, signal, exit_, **kw))


def _expired(asset: str, signal: int) -> EntrySignal:
    return EntrySignal(asset, _day(signal), None, "expired_reward_risk_below_minimum")


def _outcomes(
    decided: list[tuple[EntrySignal, PublicationOutcome]],
) -> dict[tuple[str, date], PublicationOutcome]:
    return {(s.asset, s.signal_session): o for s, o in decided}


def test_no_rule_publishes_every_signal() -> None:
    signals = [_signal("A", 0, 5), _expired("B", 0), _signal("C", 1)]
    decided = apply_rule(signals, NoRule(id="all", kind=RuleKind.NONE))
    assert sorted(o.value for _, o in decided) == [
        "published_expired",
        "published_trade",
        "published_trade",
    ]


def test_min_initial_risk_withdraws_only_trades_below_the_value() -> None:
    # Risk fractions: 0.05 (entry 100), exactly 0.02 (entry 96.938775...) is hard to hit
    # with decimals, so use stop 98 against entry 100: 0.02.
    at_value = _signal("A", 0, 3, entry="100", stop="98")
    below = _signal("B", 0, 3, entry="100", stop="98.01")
    above = _signal("C", 0, 3, entry="100", stop="95")
    assert initial_risk_fraction(at_value.trade) == Decimal("0.02")  # type: ignore[arg-type]
    rule = MinInitialRiskRule(
        id="r", kind=RuleKind.MIN_INITIAL_RISK, min_risk_fraction=Decimal("0.02")
    )
    outcomes = _outcomes(apply_rule([at_value, below, above, _expired("D", 0)], rule))
    assert outcomes[("A", _day(0))] is PublicationOutcome.PUBLISHED_TRADE
    assert outcomes[("B", _day(0))] is PublicationOutcome.WITHDRAWN_LOW_INITIAL_RISK
    assert outcomes[("C", _day(0))] is PublicationOutcome.PUBLISHED_TRADE
    assert outcomes[("D", _day(0))] is PublicationOutcome.PUBLISHED_EXPIRED


def test_open_cap_takes_same_session_signals_in_the_fixed_order() -> None:
    signals = [_signal(asset, 0, 9) for asset in ("A", "B", "C", "D")]
    order = sorted(signals, key=session_order_key)
    outcomes = _outcomes(
        apply_rule(signals, OpenCapRule(id="c", kind=RuleKind.OPEN_CAP, max_open=2))
    )
    assert [outcomes[(s.asset, s.signal_session)] for s in order] == [
        PublicationOutcome.PUBLISHED_TRADE,
        PublicationOutcome.PUBLISHED_TRADE,
        PublicationOutcome.HELD_BACK_OPEN_CAP,
        PublicationOutcome.HELD_BACK_OPEN_CAP,
    ]
    # The order depends on the session, not on the input order.
    assert (
        _outcomes(
            apply_rule(
                list(reversed(signals)), OpenCapRule(id="c", kind=RuleKind.OPEN_CAP, max_open=2)
            )
        )
        == outcomes
    )


def test_open_cap_frees_a_place_at_the_close_of_the_exit_session() -> None:
    rule = OpenCapRule(id="c", kind=RuleKind.OPEN_CAP, max_open=1)
    first = _signal("A", 0, 5)  # exits on session 5
    blocked = _signal("B", 4, 9)  # A still open after session 4
    freed = _signal("C", 5, 9)  # A exited on session 5: its place is free at that close
    outcomes = _outcomes(apply_rule([first, blocked, freed], rule))
    assert outcomes[("A", _day(0))] is PublicationOutcome.PUBLISHED_TRADE
    assert outcomes[("B", _day(4))] is PublicationOutcome.HELD_BACK_OPEN_CAP
    assert outcomes[("C", _day(5))] is PublicationOutcome.PUBLISHED_TRADE


def test_open_cap_expired_signal_uses_its_place_for_that_session_only() -> None:
    rule = OpenCapRule(id="c", kind=RuleKind.OPEN_CAP, max_open=1)
    # The expired signal is the one the fixed order takes first on session 0.
    first_asset, second_asset = (
        s.asset for s in sorted([_expired("A", 0), _expired("B", 0)], key=session_order_key)
    )
    expired, same_day = _expired(first_asset, 0), _signal(second_asset, 0, 9)
    next_day = _signal("C", 1, 9)
    outcomes = _outcomes(apply_rule([expired, same_day, next_day], rule))
    assert outcomes[(expired.asset, _day(0))] is PublicationOutcome.PUBLISHED_EXPIRED
    assert outcomes[(same_day.asset, _day(0))] is PublicationOutcome.HELD_BACK_OPEN_CAP
    assert outcomes[("C", _day(1))] is PublicationOutcome.PUBLISHED_TRADE


def test_open_cap_never_publishes_a_held_back_signal_later_and_never_exceeds_the_cap() -> None:
    rng = random.Random(7)
    assets = [f"S{i:02d}" for i in range(12)]
    signals: list[EntrySignal] = []
    for asset in assets:
        day = rng.randint(0, 5)
        while day < 300:
            if rng.random() < 0.15:
                signals.append(_expired(asset, day))
                day += 1
                continue
            hold = rng.randint(1, 40)
            signals.append(_signal(asset, day, day + hold if day + hold < 300 else None))
            day += hold + rng.randint(0, 3)
    for cap in (1, 3, 5):
        decided = apply_rule(signals, OpenCapRule(id="c", kind=RuleKind.OPEN_CAP, max_open=cap))
        assert len(decided) == len(signals) and len(_outcomes(decided)) == len(signals)
        assert max_open_published(decided) <= cap
        assert any(o is PublicationOutcome.HELD_BACK_OPEN_CAP for _, o in decided)
    unlimited = apply_rule(
        signals, OpenCapRule(id="c", kind=RuleKind.OPEN_CAP, max_open=len(assets))
    )
    assert all(o is not PublicationOutcome.HELD_BACK_OPEN_CAP for _, o in unlimited)


def test_duplicate_signal_for_an_asset_and_session_is_refused() -> None:
    with pytest.raises(SourceDataError):
        apply_rule([_signal("A", 0, 3), _expired("A", 0)], NoRule(id="all", kind=RuleKind.NONE))


def test_measure_counts_only_published_trades_and_read_verdict_applies_the_protocol_reading() -> (
    None
):
    winners = [_signal(f"W{i}", i, i + 2, exit_price="110") for i in range(6)]  # +2 R each
    loser = _signal("L", 1, 4, exit_price="95")  # -1 R
    tiny = _signal("T", 2, 4, entry="100", stop="99.5", exit_price="99.5")  # withdrawn below 2%
    rule = MinInitialRiskRule(
        id="r", kind=RuleKind.MIN_INITIAL_RISK, min_risk_fraction=Decimal("0.02")
    )
    window = {"window_start": _day(0), "window_end": _day(60), "slippage_bps": Decimal(0)}
    measured = measure(apply_rule([*winners, loser, tiny], rule), **window)  # type: ignore[arg-type]
    assert measured["outcomes"]["withdrawn_low_initial_risk"] == 1
    assert measured["risk_metrics"]["closed_trades"] == 7
    assert measured["risk_metrics"]["net_r"]["total"] == pytest.approx(11.0)
    assert measured["net_r_median"] == pytest.approx(2.0)
    assert (measured["profitable_assets"], measured["assets_with_closed_trades"]) == (6, 7)

    rate = measured["risk_metrics"]["trades_per_month"]
    inside = TargetRate(min=Decimal(str(rate - 1)), max=Decimal(str(rate + 1)))
    outside = TargetRate(min=Decimal(str(rate + 1)), max=Decimal(str(rate + 2)))
    both = {"development": measured, "validation": measured}
    assert read_verdict(both, inside) == {
        "keeps_edge": True,
        "meets_rate": True,
        "ranking_value": pytest.approx(11 / 7),
    }
    assert read_verdict(both, outside)["meets_rate"] is False
    losing = measure(apply_rule([loser], rule), **window)  # type: ignore[arg-type]
    assert (
        read_verdict({"development": measured, "validation": losing}, inside)["keeps_edge"] is False
    )


def test_protocol_is_closed() -> None:
    base = {
        "name": "p",
        "source_results": "x.json",
        "version": "v",
        "windows": ["development"],
        "target_trades_per_month": {"min": 7, "max": 12},
        "rules": [{"id": "a", "kind": "none"}],
    }
    assert PublicationProtocol.model_validate(base).rules[0].kind is RuleKind.NONE
    for change in (
        {"rules": [{"id": "a", "kind": "trailing"}]},  # unknown rule kind
        {
            "rules": [{"id": "a", "kind": "none"}, {"id": "a", "kind": "open_cap", "max_open": 3}]
        },  # duplicate id
        {"rules": [{"id": "a", "kind": "open_cap", "max_open": 0}]},
        {"rules": [{"id": "a", "kind": "min_initial_risk", "min_risk_fraction": 0}]},
        {"rules": [{"id": "a", "kind": "none", "max_open": 3}]},  # field of another kind
        {"windows": ["holdout"]},  # the holdout cannot be named
        {"target_trades_per_month": {"min": 12, "max": 7}},
        {"extra": 1},
    ):
        with pytest.raises(ValidationError):
            PublicationProtocol.model_validate({**base, **change})


def test_committed_protocol_loads_and_reads_selection_windows_only() -> None:
    protocol, digest = load_protocol(PROTOCOL)
    assert len(digest) == 64
    assert protocol.windows == ["development", "validation"]
    assert [rule.id for rule in protocol.rules][0] == "all_signals"
    assert {rule.kind for rule in protocol.rules} == set(RuleKind)
