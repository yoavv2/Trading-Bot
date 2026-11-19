"""Entry and exit rules for a long signal with a stop and a target.

This is the single implementation of the rules in ``docs/SIGNALS-PLAN.md`` (Step 1).
The backtest engine and the daily signal engine both call it and nothing else decides
whether a signal opens, expires or closes, so backtest evidence and the live record are
computed under the same rules.

Everything here is a pure function of ``Decimal`` prices. Costs (slippage, commission)
are the caller's business and are applied to the prices returned here.

Signal levels
    A signal is produced after the close of session D. Its reference price is that
    close. ``stop = reference * (1 - stop_percent / 100)`` and
    ``target = reference + target_risk_multiple * (reference - stop)``. Both are
    computed once and never change.

Entry (the open of session D+1)
    The signal expires without a trade when the open is at or below the stop, at or
    above the target, or leaves ``(target - open) / (open - stop)`` below
    ``min_reward_risk``. Otherwise the trade opens at the open.

Exit (every session while the trade is open, the entry session included)
    1. The open is at or below the stop, or at or above the target: exit at the open.
    2. Otherwise a rule exit signalled at the previous close executes at the open.
    3. Otherwise the session's low and high are checked against the stop and target.
       One touched: exit at that level. Both touched: a stop exit with
       ``intrabar_order_unknown`` set, because a daily bar cannot show which came first.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from enum import StrEnum

PRICE_SCALE = Decimal("0.000001")
_HUNDRED = Decimal(100)


class EntryOutcome(StrEnum):
    OPENED = "opened"
    EXPIRED_OPEN_AT_OR_BELOW_STOP = "expired_open_at_or_below_stop"
    EXPIRED_OPEN_AT_OR_ABOVE_TARGET = "expired_open_at_or_above_target"
    EXPIRED_REWARD_RISK_BELOW_MINIMUM = "expired_reward_risk_below_minimum"


class ExitReason(StrEnum):
    STOP = "stop"
    TARGET = "target"
    RULE = "rule"


class InvalidLevelsError(ValueError):
    """The reference price and risk plan do not give ``stop < reference < target``."""


@dataclass(frozen=True)
class RiskPlan:
    """The ``risk`` block of a specification, as numbers."""

    stop_percent: Decimal  # percent below the reference price
    target_risk_multiple: Decimal  # target distance as a multiple of the planned risk
    min_reward_risk: Decimal  # minimum (target - entry) / (entry - stop) at the open


@dataclass(frozen=True)
class SignalLevels:
    reference: Decimal
    stop: Decimal
    target: Decimal

    @property
    def planned_risk(self) -> Decimal:
        return self.reference - self.stop


@dataclass(frozen=True)
class EntryDecision:
    outcome: EntryOutcome
    entry_price: Decimal | None  # the open, when the trade opened
    initial_risk: Decimal | None  # entry_price - stop, per share
    reward_risk: Decimal | None  # None when the open is at or beyond a level

    @property
    def opened(self) -> bool:
        return self.outcome is EntryOutcome.OPENED


@dataclass(frozen=True)
class ExitDecision:
    reason: ExitReason
    price: Decimal
    at_open: bool
    intrabar_order_unknown: bool = False


def _price(value: Decimal) -> Decimal:
    return value.quantize(PRICE_SCALE, rounding=ROUND_HALF_EVEN)


def plan_levels(reference: Decimal, risk: RiskPlan) -> SignalLevels:
    """Stop and target for a signal whose reference price is ``reference``."""

    reference = _price(reference)
    stop = _price(reference * (Decimal(1) - risk.stop_percent / _HUNDRED))
    target = _price(reference + risk.target_risk_multiple * (reference - stop))
    if not (Decimal(0) < stop < reference < target):
        raise InvalidLevelsError(
            f"levels must satisfy 0 < stop < reference < target; got stop={stop}, "
            f"reference={reference}, target={target}"
        )
    return SignalLevels(reference=reference, stop=stop, target=target)


def resolve_entry(levels: SignalLevels, *, open_price: Decimal, min_reward_risk: Decimal) -> EntryDecision:
    """Decide whether a signal opens at the open of the session after it was produced."""

    if open_price <= levels.stop:
        return EntryDecision(EntryOutcome.EXPIRED_OPEN_AT_OR_BELOW_STOP, None, None, None)
    if open_price >= levels.target:
        return EntryDecision(EntryOutcome.EXPIRED_OPEN_AT_OR_ABOVE_TARGET, None, None, None)
    reward = levels.target - open_price
    risk = open_price - levels.stop
    reward_risk = reward / risk
    # Compared without the division so the boundary is exact.
    if reward < min_reward_risk * risk:
        return EntryDecision(EntryOutcome.EXPIRED_REWARD_RISK_BELOW_MINIMUM, None, None, reward_risk)
    return EntryDecision(EntryOutcome.OPENED, open_price, risk, reward_risk)


def resolve_exit(
    levels: SignalLevels,
    *,
    open_price: Decimal,
    high: Decimal,
    low: Decimal,
    rule_exit_due: bool,
) -> ExitDecision | None:
    """Decide whether an open trade closes in this session, and at what price.

    ``rule_exit_due`` is true when the strategy's exit rule was true at the previous
    session's close. Returns ``None`` when the trade stays open.
    """

    if open_price <= levels.stop:
        return ExitDecision(ExitReason.STOP, open_price, at_open=True)
    if open_price >= levels.target:
        return ExitDecision(ExitReason.TARGET, open_price, at_open=True)
    if rule_exit_due:
        return ExitDecision(ExitReason.RULE, open_price, at_open=True)
    stop_touched = low <= levels.stop
    target_touched = high >= levels.target
    if stop_touched and target_touched:
        return ExitDecision(ExitReason.STOP, levels.stop, at_open=False, intrabar_order_unknown=True)
    if stop_touched:
        return ExitDecision(ExitReason.STOP, levels.stop, at_open=False)
    if target_touched:
        return ExitDecision(ExitReason.TARGET, levels.target, at_open=False)
    return None


def r_multiple(*, entry_price: Decimal, exit_price: Decimal, stop: Decimal) -> Decimal:
    """Gross result in R: the move per share over the actual initial risk.

    The initial risk is the entry price the rules gave (the open) minus the fixed stop.
    Both prices are rule prices, before any cost.
    """

    initial_risk = entry_price - stop
    if initial_risk <= 0:
        raise InvalidLevelsError(f"entry {entry_price} is not above the stop {stop}")
    return (exit_price - entry_price) / initial_risk


def net_r_multiple(*, net_pnl: Decimal, quantity: Decimal, entry_price: Decimal, stop: Decimal) -> Decimal:
    """Net result in R: the trade's profit after the caller's costs over the same risk.

    ``entry_price`` is the rule entry price, not the fill price, so gross and net R share
    one denominator: ``quantity * (entry_price - stop)``.
    """

    initial_risk = entry_price - stop
    if initial_risk <= 0:
        raise InvalidLevelsError(f"entry {entry_price} is not above the stop {stop}")
    if quantity <= 0:
        raise InvalidLevelsError(f"quantity {quantity} is not positive")
    return net_pnl / (quantity * initial_risk)
