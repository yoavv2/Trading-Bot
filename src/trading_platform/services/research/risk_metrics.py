"""Metrics for runs of a specification with a risk block (signals plan, Step 1), pure.

``metrics.py`` measures a run as an equity curve. A signals record is measured per
signal, in R, so this module reports what that record needs: how many signals and trades
a month, why signals expired, the result in R net of the run's cost model, the drawdown
of the cumulative R curve, and how much of the result rests on the stop-first assumption
for bars that touched both levels (``intrabar_order_unknown``).

The same function serves one run and a pool of runs (several assets of one version, one
window): pass every trade and the summed expiry counts.

Definitions
    gross R     (rule exit - rule entry) / (rule entry - stop), on rule prices.
    net R       net profit after slippage and commissions / (quantity * (rule entry - stop)).
    R drawdown  the largest peak-to-trough fall of cumulative net R, trades ordered by
                exit session; the curve starts at 0.
    month       365.25 / 12 calendar days of the window.
    target-first bound
                each flagged trade re-priced as if the target had come first: its exit
                moves from the stop to the target, slippage unchanged. The recorded
                result is the stop-first bound; the truth lies between the two.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from trading_platform.strategies.trade_rules import (
    EntryOutcome,
    ExitReason,
    net_r_multiple,
    r_multiple,
)

DAYS_PER_MONTH = Decimal("365.25") / Decimal(12)
_BPS = Decimal(10000)


@dataclass(frozen=True)
class RiskTrade:
    """A trade of a risk-block run: the levels, the rule prices and the net profit."""

    asset: str
    signal_session: date
    exit_session: date | None  # None while the trade is open
    quantity: Decimal
    rule_entry_price: Decimal
    rule_exit_price: Decimal | None
    stop_price: Decimal
    target_price: Decimal
    net_pnl: Decimal | None
    exit_reason: str | None
    intrabar_order_unknown: bool

    @property
    def closed(self) -> bool:
        return self.exit_session is not None and self.rule_exit_price is not None and self.net_pnl is not None


def _f(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def window_months(window_start: date, window_end: date) -> Decimal:
    return Decimal((window_end - window_start).days + 1) / DAYS_PER_MONTH


def gross_r(trade: RiskTrade) -> Decimal:
    assert trade.rule_exit_price is not None
    return r_multiple(entry_price=trade.rule_entry_price, exit_price=trade.rule_exit_price, stop=trade.stop_price)


def net_r(trade: RiskTrade) -> Decimal:
    assert trade.net_pnl is not None
    return net_r_multiple(
        net_pnl=trade.net_pnl, quantity=trade.quantity, entry_price=trade.rule_entry_price, stop=trade.stop_price
    )


def net_r_if_target_first(trade: RiskTrade, *, slippage_bps: Decimal) -> Decimal:
    """Net R of a flagged trade had the target been reached before the stop."""

    risk = trade.rule_entry_price - trade.stop_price
    exit_fill_factor = Decimal(1) - slippage_bps / _BPS
    return net_r(trade) + (trade.target_price - trade.stop_price) * exit_fill_factor / risk


def max_r_drawdown(results_in_exit_order: list[Decimal]) -> Decimal:
    peak = Decimal(0)
    cumulative = Decimal(0)
    worst = Decimal(0)
    for value in results_in_exit_order:
        cumulative += value
        peak = max(peak, cumulative)
        worst = max(worst, peak - cumulative)
    return worst


def compute_risk_metrics(
    *,
    trades: list[RiskTrade],
    expired_by_reason: dict[str, int],
    window_start: date,
    window_end: date,
    slippage_bps: Decimal,
) -> dict[str, Any]:
    """The per-signal record of one window. Undefined values are ``None``, never zero."""

    months = window_months(window_start, window_end)
    expired = {
        outcome.value: int(expired_by_reason.get(outcome.value, 0))
        for outcome in EntryOutcome
        if outcome is not EntryOutcome.OPENED
    }
    expired_total = sum(expired.values())
    closed = sorted(
        (t for t in trades if t.closed),
        key=lambda t: (t.exit_session, t.asset, t.signal_session),
    )
    entry_signals = len(trades) + expired_total

    gross = [gross_r(t) for t in closed]
    net = [net_r(t) for t in closed]
    flagged = [t for t in closed if t.intrabar_order_unknown]
    net_target_first = [
        net_r_if_target_first(t, slippage_bps=slippage_bps) if t.intrabar_order_unknown else net_r(t) for t in closed
    ]
    count = Decimal(len(closed))
    total_net = sum(net, Decimal(0))
    total_gross = sum(gross, Decimal(0))
    total_net_target_first = sum(net_target_first, Decimal(0))
    drawdown = max_r_drawdown(net)

    exits = {reason.value: 0 for reason in ExitReason}
    for trade in closed:
        if trade.exit_reason in exits:
            exits[trade.exit_reason] += 1

    return {
        "window": {"start": window_start.isoformat(), "end": window_end.isoformat(), "months": _f(months)},
        "entry_signals": entry_signals,
        "entry_signals_per_month": _f(Decimal(entry_signals) / months),
        "trades_opened": len(trades),
        "trades_per_month": _f(Decimal(len(trades)) / months),
        "closed_trades": len(closed),
        "open_at_end": len(trades) - len(closed),
        "expired_signals": expired_total,
        "expired_by_reason": expired,
        "exits_by_reason": exits,
        "gross_r": {
            "total": _f(total_gross),
            "mean": _f(total_gross / count) if closed else None,
        },
        "net_r": {
            "total": _f(total_net),
            "mean": _f(total_net / count) if closed else None,
            "win_rate": _f(Decimal(sum(1 for value in net if value > 0)) / count) if closed else None,
            "best": _f(max(net)) if closed else None,
            "worst": _f(min(net)) if closed else None,
        },
        "max_r_drawdown": _f(drawdown),
        "net_r_over_max_drawdown": _f(total_net / drawdown) if drawdown > 0 else None,
        "intrabar_order_unknown": {
            "trades": len(flagged),
            "share_of_closed_trades": _f(Decimal(len(flagged)) / count) if closed else None,
            # Recorded result: the stop came first on every flagged bar.
            "net_r_total_stop_first": _f(total_net),
            "net_r_mean_stop_first": _f(total_net / count) if closed else None,
            # Upper bound: the target came first on every flagged bar.
            "net_r_total_target_first": _f(total_net_target_first),
            "net_r_mean_target_first": _f(total_net_target_first / count) if closed else None,
            "net_r_total_range": _f(total_net_target_first - total_net),
        },
    }
