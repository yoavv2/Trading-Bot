"""The shared entry and exit rules for a long signal with a stop and a target
(docs/SIGNALS-PLAN.md, Step 1). Pure-function tests; the engine tests run the same
cases through the backtest engine and the daily engine."""

from __future__ import annotations

from decimal import Decimal as D

import pytest

from trading_platform.strategies.trade_rules import (
    EntryOutcome,
    ExitReason,
    InvalidLevelsError,
    RiskPlan,
    SignalLevels,
    net_r_multiple,
    plan_levels,
    r_multiple,
    resolve_entry,
    resolve_exit,
)

PLAN = RiskPlan(stop_percent=D(5), target_risk_multiple=D(2), min_reward_risk=D(1))
LEVELS = SignalLevels(reference=D(100), stop=D(95), target=D(110))


def test_levels_come_from_the_reference_price() -> None:
    assert plan_levels(D(100), PLAN) == LEVELS
    assert LEVELS.planned_risk == D(5)


def test_levels_are_quantized_to_six_decimals() -> None:
    levels = plan_levels(D("33.333333"), RiskPlan(D("3.3"), D("1.7"), D(0)))
    assert levels.stop == D("32.233333")
    assert levels.target == D("35.203333")


def test_a_non_positive_reference_price_is_refused() -> None:
    with pytest.raises(InvalidLevelsError):
        plan_levels(D(0), PLAN)


# --- entry ------------------------------------------------------------------


def test_open_beyond_the_target_expires_the_signal() -> None:
    decision = resolve_entry(LEVELS, open_price=D(111), min_reward_risk=D(0))
    assert decision.outcome is EntryOutcome.EXPIRED_OPEN_AT_OR_ABOVE_TARGET
    assert not decision.opened and decision.entry_price is None


@pytest.mark.parametrize(
    ("open_price", "outcome"),
    [
        (D(110), EntryOutcome.EXPIRED_OPEN_AT_OR_ABOVE_TARGET),
        (D("109.999999"), EntryOutcome.OPENED),
        (D(95), EntryOutcome.EXPIRED_OPEN_AT_OR_BELOW_STOP),
        (D(94), EntryOutcome.EXPIRED_OPEN_AT_OR_BELOW_STOP),
        (D("95.000001"), EntryOutcome.OPENED),
    ],
)
def test_entry_boundaries_are_inclusive_at_the_levels(open_price: D, outcome: EntryOutcome) -> None:
    assert resolve_entry(LEVELS, open_price=open_price, min_reward_risk=D(0)).outcome is outcome


def test_reward_to_risk_is_measured_at_the_open() -> None:
    """Plan example: reference 100, stop 95, target 110, open 102 gives 8 against 7."""

    opened = resolve_entry(LEVELS, open_price=D(102), min_reward_risk=D("1.14"))
    assert opened.outcome is EntryOutcome.OPENED
    assert opened.entry_price == D(102)
    assert opened.initial_risk == D(7)
    assert opened.reward_risk == D(8) / D(7)

    expired = resolve_entry(LEVELS, open_price=D(102), min_reward_risk=D("1.15"))
    assert expired.outcome is EntryOutcome.EXPIRED_REWARD_RISK_BELOW_MINIMUM
    assert expired.entry_price is None


def test_reward_to_risk_boundary_is_exact() -> None:
    # Open 100: reward 10, risk 5, ratio exactly 2.
    assert resolve_entry(LEVELS, open_price=D(100), min_reward_risk=D(2)).opened
    assert not resolve_entry(LEVELS, open_price=D("100.000001"), min_reward_risk=D(2)).opened


# --- exit -------------------------------------------------------------------


def test_gap_through_the_stop_exits_at_the_open() -> None:
    decision = resolve_exit(LEVELS, open_price=D(90), high=D(93), low=D(88), rule_exit_due=False)
    assert decision is not None
    assert (decision.reason, decision.price, decision.at_open) == (ExitReason.STOP, D(90), True)
    assert decision.intrabar_order_unknown is False


def test_gap_through_the_target_exits_at_the_open() -> None:
    decision = resolve_exit(LEVELS, open_price=D(113), high=D(115), low=D(112), rule_exit_due=False)
    assert decision is not None
    assert (decision.reason, decision.price, decision.at_open) == (ExitReason.TARGET, D(113), True)


def test_one_bar_touching_both_levels_is_a_flagged_stop_exit() -> None:
    decision = resolve_exit(LEVELS, open_price=D(100), high=D(111), low=D(94), rule_exit_due=False)
    assert decision is not None
    assert (decision.reason, decision.price, decision.at_open) == (ExitReason.STOP, D(95), False)
    assert decision.intrabar_order_unknown is True


def test_a_single_touch_exits_at_that_level_without_the_flag() -> None:
    stop = resolve_exit(LEVELS, open_price=D(100), high=D(101), low=D(95), rule_exit_due=False)
    target = resolve_exit(LEVELS, open_price=D(100), high=D(110), low=D(99), rule_exit_due=False)
    assert stop is not None and (stop.reason, stop.price, stop.intrabar_order_unknown) == (ExitReason.STOP, D(95), False)
    assert target is not None and (target.reason, target.price, target.intrabar_order_unknown) == (ExitReason.TARGET, D(110), False)


def test_no_touch_keeps_the_trade_open() -> None:
    assert resolve_exit(LEVELS, open_price=D(100), high=D("109.99"), low=D("95.01"), rule_exit_due=False) is None


def test_rule_exit_executes_at_the_open_before_the_range_is_checked() -> None:
    decision = resolve_exit(LEVELS, open_price=D(100), high=D(111), low=D(94), rule_exit_due=True)
    assert decision is not None
    assert (decision.reason, decision.price, decision.at_open) == (ExitReason.RULE, D(100), True)
    assert decision.intrabar_order_unknown is False


def test_a_gap_beyond_a_level_takes_precedence_over_a_due_rule_exit() -> None:
    decision = resolve_exit(LEVELS, open_price=D(94), high=D(96), low=D(93), rule_exit_due=True)
    assert decision is not None and decision.reason is ExitReason.STOP and decision.price == D(94)


def test_r_is_measured_against_the_actual_initial_risk() -> None:
    # Entry 102 against stop 95: risk 7 per share.
    assert r_multiple(entry_price=D(102), exit_price=D(110), stop=D(95)) == D(8) / D(7)
    assert r_multiple(entry_price=D(102), exit_price=D(95), stop=D(95)) == D(-1)
    assert r_multiple(entry_price=D(102), exit_price=D(90), stop=D(95)) == D(-12) / D(7)
    with pytest.raises(InvalidLevelsError):
        r_multiple(entry_price=D(95), exit_price=D(100), stop=D(95))


@pytest.mark.parametrize(
    ("open_price", "min_reward_risk", "opens"),
    [
        (D(101), D("1.5"), True),  # 9 / 6 is exactly the threshold
        (D("101.000001"), D("1.5"), False),
        (D(102), D("1.14"), True),  # 8 / 7 = 1.142857...
        (D(102), D("1.15"), False),
        (D(102), D("1.142857"), True),
        (D(102), D("1.142858"), False),
    ],
)
def test_min_reward_risk_threshold_uses_the_exact_ratio(open_price: D, min_reward_risk: D, opens: bool) -> None:
    """Stop 95, target 110. The decision is ``reward >= min * risk``; no rounded ratio."""

    assert resolve_entry(LEVELS, open_price=open_price, min_reward_risk=min_reward_risk).opened is opens


def test_net_r_shares_the_gross_denominator() -> None:
    # 10 shares, rule entry 102, stop 95: the risk is 70. A net profit of 56 is 0.8R.
    assert net_r_multiple(net_pnl=D(56), quantity=D(10), entry_price=D(102), stop=D(95)) == D("0.8")
    with pytest.raises(InvalidLevelsError):
        net_r_multiple(net_pnl=D(1), quantity=D(10), entry_price=D(95), stop=D(95))
    with pytest.raises(InvalidLevelsError):
        net_r_multiple(net_pnl=D(1), quantity=D(0), entry_price=D(102), stop=D(95))
