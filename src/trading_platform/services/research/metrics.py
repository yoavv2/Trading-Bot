"""Pinned research metrics (proposal Part I.2), pure.

Per window, net of the stated costs, on the series the run used. Every metric follows
the table in the proposal, including its undefined-value rule: an undefined metric is
``None`` and the reason is recorded in ``flags`` or ``notes`` so a reader never mistakes
a missing value for zero. Closed trades (an exit fill inside the window) and the
position still open at the last session are kept apart; open positions enter only the
equity-based metrics and ``open_at_end``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from statistics import median
from typing import Any

SESSIONS_PER_YEAR = 252
DAYS_PER_YEAR = 365.25
SHORT_WINDOW_DAYS = 365

FLAG_ANNUALISED_FROM_SHORT_WINDOW = "annualised_from_short_window"
FLAG_DATA_GAP_AFFECTED = "data_gap_affected"
FLAG_ZERO_QUANTITY_FILL = "zero_quantity_fill"


@dataclass(frozen=True)
class TradeRecord:
    status: str  # "closed" | "open"
    quantity: Decimal
    entry_fill_session: date
    entry_price: Decimal
    entry_commission: Decimal
    entry_slippage: Decimal
    exit_fill_session: date | None
    exit_price: Decimal | None
    exit_commission: Decimal
    exit_slippage: Decimal
    net_pnl: Decimal | None
    holding_period_sessions: int | None
    rounding_slack: Decimal | None


@dataclass(frozen=True)
class EquityPoint:
    session_date: date
    total_equity: Decimal
    gross_exposure: Decimal
    unrealized_pnl: Decimal


def _f(value: Decimal | float | None) -> float | None:
    return None if value is None else float(value)


def daily_returns(equity: list[EquityPoint]) -> list[float]:
    out: list[float] = []
    for previous, current in zip(equity, equity[1:]):
        if previous.total_equity > 0:
            out.append(float(current.total_equity / previous.total_equity - Decimal(1)))
    return out


def compute_metrics(
    *,
    initial_capital: Decimal,
    equity: list[EquityPoint],
    trades: list[TradeRecord],
    skipped_fills: int = 0,
    zero_quantity_fills: int = 0,
) -> dict[str, Any]:
    """The I.2 table for one run of one window."""

    flags: list[str] = []
    notes: dict[str, str] = {}
    closed = [t for t in trades if t.status == "closed" and t.net_pnl is not None]
    open_at_end = [t for t in trades if t.status != "closed"]

    final_equity = equity[-1].total_equity if equity else initial_capital
    net_total_return = (final_equity - initial_capital) / initial_capital if initial_capital > 0 else None

    cagr: float | None = None
    if len(equity) >= 2 and initial_capital > 0 and final_equity > 0:
        days = (equity[-1].session_date - equity[0].session_date).days
        if days > 0:
            years = days / DAYS_PER_YEAR
            cagr = math.pow(float(final_equity / initial_capital), 1 / years) - 1
            if days < SHORT_WINDOW_DAYS:
                flags.append(FLAG_ANNUALISED_FROM_SHORT_WINDOW)
    if cagr is None:
        notes["cagr"] = "undefined: fewer than two sessions or non-positive equity"

    max_drawdown = Decimal(0)
    drawdown_duration = 0
    peak: Decimal | None = None
    run = 0
    for point in equity:
        if peak is None or point.total_equity > peak:
            peak = point.total_equity
            run = 0
        else:
            if point.total_equity < peak:
                run += 1
                drawdown_duration = max(drawdown_duration, run)
            else:
                run = 0
        if peak and peak > 0:
            drawdown = point.total_equity / peak - Decimal(1)
            if drawdown < max_drawdown:
                max_drawdown = drawdown

    holding = [t.holding_period_sessions for t in closed if t.holding_period_sessions is not None]
    holding_median = float(median(holding)) if holding else None
    holding_mean = (sum(holding) / len(holding)) if holding else None
    if not closed:
        notes["holding_period"] = "undefined: no closed trades"

    wins = [t.net_pnl for t in closed if t.net_pnl is not None and t.net_pnl > 0]
    losses = [t.net_pnl for t in closed if t.net_pnl is not None and t.net_pnl < 0]
    win_rate = (Decimal(len(wins)) / Decimal(len(closed))) if closed else None
    gross_profit = sum(wins, Decimal(0))
    gross_loss = sum((abs(x) for x in losses), Decimal(0))
    profit_factor: Decimal | None
    if not closed:
        profit_factor = None
        notes["profit_factor"] = "undefined: no closed trades"
    elif gross_loss == 0:
        profit_factor = None
        notes["profit_factor"] = "undefined: no losing trades"
    else:
        profit_factor = gross_profit / gross_loss
    expectancy = (sum((t.net_pnl for t in closed if t.net_pnl is not None), Decimal(0)) / Decimal(len(closed))) if closed else None
    if not closed:
        notes["win_rate"] = notes["expectancy"] = "undefined: no closed trades"

    returns = daily_returns(equity)
    sharpe: float | None = None
    sortino: float | None = None
    if len(returns) >= 2:
        mean = sum(returns) / len(returns)
        variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
        std = math.sqrt(variance)
        if std > 0:
            sharpe = mean / std * math.sqrt(SESSIONS_PER_YEAR)
        downside = math.sqrt(sum(min(r, 0.0) ** 2 for r in returns) / len(returns))
        if any(r < 0 for r in returns) and downside > 0:
            sortino = mean / downside * math.sqrt(SESSIONS_PER_YEAR)
        else:
            notes["sortino_daily_ann"] = "undefined: no negative day"
        if sharpe is None:
            notes["sharpe_daily_ann"] = "undefined: zero deviation"
    else:
        notes["sharpe_daily_ann"] = notes["sortino_daily_ann"] = "undefined: fewer than two returns"

    exposure = (
        sum((p.gross_exposure / p.total_equity for p in equity if p.total_equity > 0), Decimal(0)) / Decimal(len(equity))
        if equity
        else None
    )
    mean_equity = (sum((p.total_equity for p in equity), Decimal(0)) / Decimal(len(equity))) if equity else None
    notional = Decimal(0)
    for t in trades:
        notional += t.entry_price * t.quantity
        if t.exit_price is not None:
            notional += t.exit_price * t.quantity
    turnover = (notional / mean_equity) if mean_equity and mean_equity > 0 else None

    slippage = sum((t.entry_slippage + t.exit_slippage for t in trades), Decimal(0))
    commission = sum((t.entry_commission + t.exit_commission for t in trades), Decimal(0))
    total_costs = slippage + commission
    rounding_slack = sum((t.rounding_slack for t in trades if t.rounding_slack is not None), Decimal(0))
    if skipped_fills > 0:
        flags.append(FLAG_DATA_GAP_AFFECTED)
    if zero_quantity_fills > 0:
        flags.append(FLAG_ZERO_QUANTITY_FILL)

    return {
        "net_total_return": _f(net_total_return),
        "cagr": cagr,
        "max_drawdown": _f(max_drawdown),
        "drawdown_duration": drawdown_duration,
        "closed_trades": len(closed),
        "open_at_end": {
            "count": len(open_at_end),
            "unrealized_pnl": _f(equity[-1].unrealized_pnl) if equity else 0.0,
        },
        "holding_period": {"median_sessions": holding_median, "mean_sessions": holding_mean},
        "win_rate": _f(win_rate),
        "profit_factor": _f(profit_factor),
        "expectancy": _f(expectancy),
        "sharpe_daily_ann": sharpe,
        "sortino_daily_ann": sortino,
        "exposure": _f(exposure),
        "turnover": _f(turnover),
        "total_costs": {
            "currency": _f(total_costs),
            "slippage": _f(slippage),
            "commission": _f(commission),
            "pct_of_initial_capital": _f(total_costs / initial_capital) if initial_capital > 0 else None,
        },
        "rounding_slack": _f(rounding_slack),
        "skipped_fills": skipped_fills,
        "zero_quantity_fills": zero_quantity_fills,
        "measured_sessions": len(equity),
        "final_equity": _f(final_equity),
        "initial_capital": _f(initial_capital),
        "flags": flags,
        "notes": notes,
    }
