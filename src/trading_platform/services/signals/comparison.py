"""Compare the signal log with a backtest of the same strategy version.

Both engines apply ``strategies/trade_rules.py``. This module reduces what each one
recorded to the same shape, one ``SignalOutcome`` per signal session, so the two can be
compared row by row: the levels, the final state, the entry and exit sessions, the entry
and exit prices the rules gave, the exit reason, the ``intrabar_order_unknown`` flag and
the gross result in R.

Prices here are rule prices (the open, the stop or the target), before costs. The log
simulates no costs, so net R exists on the backtest side only and is reported separately
by ``net_r_from_backtest``; it is not part of the comparison.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_platform.db.models import BacktestSignal, BacktestTrade, Symbol
from trading_platform.db.models.signal_log import (
    CLOSING_EVENT_TYPES,
    SignalEventType,
    SignalLogEvent,
)
from trading_platform.strategies.trade_rules import (
    PRICE_SCALE,
    EntryOutcome,
    net_r_multiple,
    r_multiple,
)

OUTCOME_OPENED = EntryOutcome.OPENED.value


class SignalState(StrEnum):
    """Where a resolved signal stands at the end of the compared range."""

    EXPIRED = "expired"
    OPEN = "open"
    CLOSED_STOP = "closed_stop"
    CLOSED_TARGET = "closed_target"
    CLOSED_RULE = "closed_rule"


@dataclass(frozen=True)
class SignalOutcome:
    signal_session: date
    reference: Decimal
    stop: Decimal
    target: Decimal
    state: SignalState
    entry_outcome: str  # an ``EntryOutcome`` value
    entry_session: date | None
    entry_price: Decimal | None  # rule price: the open of the entry session
    exit_session: date | None
    exit_price: Decimal | None  # rule price: the open, the stop or the target
    exit_reason: str | None  # stop | target | rule
    intrabar_order_unknown: bool | None
    gross_r: Decimal | None  # (exit - entry) / (entry - stop) on rule prices; closed trades only


def _state(entry_outcome: str, exit_reason: str | None) -> SignalState:
    if entry_outcome != OUTCOME_OPENED:
        return SignalState.EXPIRED
    if exit_reason is None:
        return SignalState.OPEN
    return SignalState(f"closed_{exit_reason}")


def _outcome(
    *,
    signal_session: date,
    reference: Decimal,
    stop: Decimal,
    target: Decimal,
    entry_outcome: str,
    entry_session: date | None,
    entry_price: Decimal | None,
    exit_session: date | None,
    exit_price: Decimal | None,
    exit_reason: str | None,
    intrabar_order_unknown: bool | None,
) -> SignalOutcome:
    gross_r = (
        r_multiple(entry_price=entry_price, exit_price=exit_price, stop=stop)
        if entry_price is not None and exit_price is not None
        else None
    )
    return SignalOutcome(
        signal_session=signal_session,
        reference=reference,
        stop=stop,
        target=target,
        state=_state(entry_outcome, exit_reason),
        entry_outcome=entry_outcome,
        entry_session=entry_session,
        entry_price=entry_price,
        exit_session=exit_session,
        exit_price=exit_price,
        exit_reason=exit_reason,
        intrabar_order_unknown=intrabar_order_unknown,
        gross_r=gross_r,
    )


def _issue_scale(event: SignalLogEvent | None) -> Decimal | None:
    """The event's price in the price scale of the signal session. After a dividend or a
    split the engine works in the scale of the event session and records the factor; the
    outcome, and its R, are measured in one scale."""

    if event is None or event.price is None:
        return None
    if event.adjustment_factor is None or event.adjustment_factor == 1:
        return event.price
    return (event.price / event.adjustment_factor).quantize(PRICE_SCALE, rounding=ROUND_HALF_EVEN)


def outcomes_from_log(
    session: Session, *, strategy_version_id: uuid.UUID, asset: str, through: date
) -> list[SignalOutcome]:
    """Resolved signals in the log, using only events on or before ``through``."""

    rows = session.execute(
        select(SignalLogEvent)
        .where(
            SignalLogEvent.strategy_version_id == strategy_version_id,
            SignalLogEvent.asset == asset,
            SignalLogEvent.event_session <= through,
        )
        .order_by(SignalLogEvent.signal_session, SignalLogEvent.event_session)
    ).scalars()
    by_signal: dict[date, dict[str, SignalLogEvent]] = {}
    for row in rows:
        by_signal.setdefault(row.signal_session, {})[row.event_type] = row
    out: list[SignalOutcome] = []
    for signal_session, events in sorted(by_signal.items()):
        issued = events[SignalEventType.ISSUED]
        expired = events.get(SignalEventType.EXPIRED)
        filled = events.get(SignalEventType.ENTRY_FILLED)
        if expired is None and filled is None:
            continue  # issued on the last session: not resolved yet
        closed = next((events[t] for t in CLOSING_EVENT_TYPES if t in events), None)
        out.append(
            _outcome(
                signal_session=signal_session,
                reference=issued.reference_price,
                stop=issued.stop_price,
                target=issued.target_price,
                # ``expiry_reason`` is NOT NULL on an ``expired`` row (CHECK constraint, 0034).
                entry_outcome=(expired.expiry_reason or "") if expired is not None else OUTCOME_OPENED,
                entry_session=filled.event_session if filled is not None else None,
                entry_price=_issue_scale(filled),
                exit_session=closed.event_session if closed is not None else None,
                exit_price=_issue_scale(closed),
                exit_reason=closed.event_type.removeprefix("closed_") if closed is not None else None,
                intrabar_order_unknown=closed.intrabar_order_unknown if closed is not None else None,
            )
        )
    return out


def _run_trades(session: Session, run_id: uuid.UUID, asset: str) -> tuple[uuid.UUID, dict[date, BacktestTrade]]:
    symbol_id = session.execute(select(Symbol.id).where(Symbol.ticker == asset)).scalar_one()
    trades = {
        trade.entry_signal_session: trade
        for trade in session.execute(
            select(BacktestTrade).where(
                BacktestTrade.strategy_run_id == run_id, BacktestTrade.symbol_id == symbol_id
            )
        ).scalars()
    }
    return symbol_id, trades


def outcomes_from_backtest(session: Session, *, run_id: uuid.UUID, asset: str) -> list[SignalOutcome]:
    """Resolved signals of one backtest run of a specification with a risk block."""

    symbol_id, trades = _run_trades(session, run_id, asset)
    signals = session.execute(
        select(BacktestSignal)
        .where(BacktestSignal.strategy_run_id == run_id, BacktestSignal.symbol_id == symbol_id)
        .order_by(BacktestSignal.session_date)
    ).scalars()
    out: list[SignalOutcome] = []
    for signal in signals:
        metadata = signal.signal_metadata or {}
        levels = metadata.get("levels")
        entry_outcome = metadata.get("entry_outcome")
        if levels is None or entry_outcome is None:
            continue  # not an entry signal, or its fill session never came
        trade = trades.get(signal.session_date)
        out.append(
            _outcome(
                signal_session=signal.session_date,
                reference=Decimal(levels["reference"]),
                stop=Decimal(levels["stop"]),
                target=Decimal(levels["target"]),
                entry_outcome=entry_outcome,
                entry_session=trade.entry_fill_session if trade is not None else None,
                entry_price=trade.rule_entry_price if trade is not None else None,
                exit_session=trade.exit_fill_session if trade is not None else None,
                exit_price=trade.rule_exit_price if trade is not None else None,
                exit_reason=trade.exit_reason if trade is not None else None,
                intrabar_order_unknown=trade.intrabar_order_unknown if trade is not None else None,
            )
        )
    return out


def net_r_from_backtest(session: Session, *, run_id: uuid.UUID, asset: str) -> dict[date, Decimal]:
    """Net R of each closed trade of a backtest run, keyed by signal session.

    Net of the run's own cost model (slippage on both fills and both commissions), over
    the same initial risk as gross R: quantity times (rule entry price minus stop).
    """

    _, trades = _run_trades(session, run_id, asset)
    return {
        signal_session: net_r_multiple(
            net_pnl=trade.net_pnl,
            quantity=trade.quantity,
            entry_price=trade.rule_entry_price,
            stop=trade.stop_price,
        )
        for signal_session, trade in sorted(trades.items())
        if trade.net_pnl is not None and trade.rule_entry_price is not None and trade.stop_price is not None
    }


_COMPARED_FIELDS = (
    "reference",
    "stop",
    "target",
    "state",
    "entry_outcome",
    "entry_session",
    "entry_price",
    "exit_session",
    "exit_price",
    "exit_reason",
    "intrabar_order_unknown",
    "gross_r",
)


def differences(log: list[SignalOutcome], backtest: list[SignalOutcome]) -> list[str]:
    """Human-readable differences, one line per differing field; empty when the two agree."""

    log_by = {o.signal_session: o for o in log}
    backtest_by = {o.signal_session: o for o in backtest}
    out: list[str] = []
    for signal_session in sorted(log_by.keys() | backtest_by.keys()):
        left, right = log_by.get(signal_session), backtest_by.get(signal_session)
        if left is None:
            out.append(f"{signal_session}: in the backtest only ({right})")
        elif right is None:
            out.append(f"{signal_session}: in the signal log only ({left})")
        else:
            for name in _COMPARED_FIELDS:
                a, b = getattr(left, name), getattr(right, name)
                if a != b:
                    out.append(f"{signal_session}: {name}: log {a} != backtest {b}")
    return out
