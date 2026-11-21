"""What one session does to one (strategy version, asset), as a pure function.

``decide_session`` takes the session's inputs (its bar, the history the strategy
evaluates and, when a signal is active, the adjusted close of the signal session as read
today) and the active signal, and returns the events of the session. It reads no
database and no clock. The daily engine calls it with inputs read from ``daily_bars``;
the replay calls it with the inputs the daily engine kept. Both therefore make every
decision with the same code.

Adjusted levels. The engine works on adjusted bars. A dividend or a split after the
signal session rescales the whole adjusted history, so the levels a signal was issued
with are in an older price scale than today's bar. The levels in today's scale are the
issued levels times ``today's adjusted close of the signal session / the issued
reference price``. They are derived from the issued levels on every session, never from
the previous session's derived levels, so an adjustment is applied exactly once. With no
corporate action the two closes are equal and the issued levels are used unchanged.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from trading_platform.db.models.signal_log import SignalEventType
from trading_platform.services.read_recording import sha256_hex
from trading_platform.strategies.signals import SignalDirection
from trading_platform.strategies.trade_rules import (
    PRICE_SCALE,
    ExitReason,
    RiskPlan,
    SignalLevels,
    plan_levels,
    resolve_entry,
    resolve_exit,
)

FACTOR_SCALE = Decimal("0.000000000001")
ONE = Decimal(1)

_CLOSE_EVENT = {
    ExitReason.STOP: SignalEventType.CLOSED_STOP,
    ExitReason.TARGET: SignalEventType.CLOSED_TARGET,
    ExitReason.RULE: SignalEventType.CLOSED_RULE,
}


def _text(value: Decimal) -> str:
    return format(value.normalize(), "f")


@dataclass(frozen=True)
class InputBar:
    """One bar as the session read it. Carries the fields a specification can evaluate."""

    session_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int

    @classmethod
    def of(cls, bar: Any) -> InputBar:
        return cls(bar.session_date, bar.open, bar.high, bar.low, bar.close, int(bar.volume))

    def to_json(self) -> list[Any]:
        return [
            self.session_date.isoformat(),
            _text(self.open),
            _text(self.high),
            _text(self.low),
            _text(self.close),
            self.volume,
        ]

    @classmethod
    def from_json(cls, row: Sequence[Any]) -> InputBar:
        day, open_, high, low, close, volume = row
        return cls(date.fromisoformat(day), Decimal(open_), Decimal(high), Decimal(low), Decimal(close), int(volume))


@dataclass(frozen=True)
class SessionInputs:
    """Everything a session's decisions depend on, besides the active signal."""

    session_date: date
    bar: InputBar
    history: tuple[InputBar, ...]  # ascending, ending at the session; what the strategy evaluates
    signal_session_close: Decimal | None  # set when a signal was active at the start of the session

    def to_json(self) -> dict[str, Any]:
        return {
            "session_date": self.session_date.isoformat(),
            "bar": self.bar.to_json(),
            "history": [bar.to_json() for bar in self.history],
            "signal_session_close": None if self.signal_session_close is None else _text(self.signal_session_close),
        }

    @classmethod
    def from_json(cls, value: dict[str, Any]) -> SessionInputs:
        close = value["signal_session_close"]
        return cls(
            session_date=date.fromisoformat(value["session_date"]),
            bar=InputBar.from_json(value["bar"]),
            history=tuple(InputBar.from_json(row) for row in value["history"]),
            signal_session_close=None if close is None else Decimal(close),
        )

    def sha256(self) -> str:
        return sha256_hex(self.to_json())


@dataclass(frozen=True)
class ActiveSignal:
    """A signal that has neither expired nor closed, with its issued levels."""

    signal_session: date
    levels: SignalLevels
    entered: bool
    rule_exit_due: bool


@dataclass(frozen=True)
class EventSpec:
    """One event of a session, before it is written."""

    signal_session: date
    event_type: SignalEventType
    event_session: date
    levels: SignalLevels  # as issued
    adjustment_factor: Decimal
    effective_stop: Decimal
    effective_target: Decimal
    price: Decimal | None = None
    expiry_reason: str | None = None
    intrabar_order_unknown: bool | None = None


class MissingSignalSessionCloseError(ValueError):
    """A signal is active but the inputs carry no close of its signal session."""


def effective_levels(levels: SignalLevels, signal_session_close: Decimal) -> tuple[SignalLevels, Decimal]:
    """The issued levels in the price scale of ``signal_session_close``, and the factor."""

    if signal_session_close == levels.reference:
        return levels, ONE

    def scaled(value: Decimal) -> Decimal:
        return (value * signal_session_close / levels.reference).quantize(PRICE_SCALE, rounding=ROUND_HALF_EVEN)

    factor = (signal_session_close / levels.reference).quantize(FACTOR_SCALE, rounding=ROUND_HALF_EVEN)
    adjusted = SignalLevels(
        reference=signal_session_close.quantize(PRICE_SCALE, rounding=ROUND_HALF_EVEN),
        stop=scaled(levels.stop),
        target=scaled(levels.target),
    )
    return adjusted, factor


def decide_session(
    inputs: SessionInputs,
    active: ActiveSignal | None,
    *,
    risk: RiskPlan,
    evaluate: Callable[[list[Any]], Any],
) -> tuple[list[EventSpec], ActiveSignal | None]:
    """The events of the session, in order, and the signal active after it."""

    bar = inputs.bar
    events: list[EventSpec] = []
    current: SignalLevels | None = None
    factor = ONE
    if active is not None:
        if inputs.signal_session_close is None:
            raise MissingSignalSessionCloseError(f"no close of {active.signal_session} in the inputs of {bar.session_date}")
        current, factor = effective_levels(active.levels, inputs.signal_session_close)

    def record(signal: ActiveSignal, event_type: SignalEventType, **fields: Any) -> None:
        assert current is not None
        events.append(
            EventSpec(
                signal_session=signal.signal_session,
                event_type=event_type,
                event_session=inputs.session_date,
                levels=signal.levels,
                adjustment_factor=factor,
                effective_stop=current.stop,
                effective_target=current.target,
                **fields,
            )
        )

    if active is not None and not active.entered:
        # Issued at the previous close: resolve the entry at today's open.
        assert current is not None
        entry = resolve_entry(current, open_price=bar.open, min_reward_risk=risk.min_reward_risk)
        if entry.opened:
            record(active, SignalEventType.ENTRY_FILLED, price=entry.entry_price)
            active = ActiveSignal(active.signal_session, active.levels, entered=True, rule_exit_due=False)
        else:
            record(active, SignalEventType.EXPIRED, expiry_reason=entry.outcome.value)
            active = None

    if active is not None:
        assert current is not None
        decision = resolve_exit(
            current, open_price=bar.open, high=bar.high, low=bar.low, rule_exit_due=active.rule_exit_due
        )
        if decision is not None:
            record(
                active,
                _CLOSE_EVENT[decision.reason],
                price=decision.price,
                intrabar_order_unknown=decision.intrabar_order_unknown,
            )
            active = None

    # The close: evaluate the strategy on bars up to and including today.
    direction = evaluate(list(inputs.history)).direction
    if active is not None and direction is SignalDirection.EXIT:
        if not active.rule_exit_due:
            record(active, SignalEventType.RULE_EXIT_SIGNALLED)
            active = ActiveSignal(active.signal_session, active.levels, entered=True, rule_exit_due=True)
    elif active is None and direction is SignalDirection.LONG:
        levels = plan_levels(inputs.history[-1].close if inputs.history else Decimal(0), risk)
        events.append(
            EventSpec(
                signal_session=inputs.session_date,
                event_type=SignalEventType.ISSUED,
                event_session=inputs.session_date,
                levels=levels,
                adjustment_factor=ONE,
                effective_stop=levels.stop,
                effective_target=levels.target,
            )
        )
        active = ActiveSignal(inputs.session_date, levels, entered=False, rule_exit_due=False)
    return events, active
