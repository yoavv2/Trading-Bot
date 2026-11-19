"""The daily signal engine (docs/SIGNALS-PLAN.md, Step 1).

``run_signal_session`` processes one session for one (approved strategy version, asset)
and appends what happened to ``signal_log``:

1. A signal issued at the previous close is resolved at today's open: it opens
   (``entry_filled``) or expires (``expired`` with a closed reason).
2. An open trade is checked against its stop and target and against a rule exit
   signalled at the previous close (``closed_stop`` / ``closed_target`` / ``closed_rule``).
3. The strategy is evaluated at today's close: an exit while a trade is open is recorded
   (``rule_exit_signalled``) for tomorrow's open; a long signal while flat is issued
   (``issued``) with its reference price, stop and target.

Every entry and exit decision is made by ``strategies/trade_rules.py``, the same
functions the backtest engine calls, in the same order within a session. Prices in the
log are the rule prices (the open, the stop or the target); the log simulates no costs.

Sessions are processed in calendar order with none skipped: a stop can only be checked
on a session the engine actually processed. ``signal_session_runs`` records each
processed session, so a re-run writes nothing and a gap is refused with a typed error.
Nothing is ever updated or deleted (migration 0034 installs the triggers).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trading_platform.core.settings import Settings
from trading_platform.db.models.research import StrategyVersion
from trading_platform.db.models.signal_log import (
    TERMINAL_EVENT_TYPES,
    SignalEventType,
    SignalLogEvent,
    SignalSessionRun,
)
from trading_platform.services.market_data_access import (
    bars_for_session_date,
    persisted_session_dates,
)
from trading_platform.strategies.research_registry import strategy_from_version
from trading_platform.strategies.signals import SignalDirection
from trading_platform.strategies.trade_rules import (
    ExitReason,
    SignalLevels,
    plan_levels,
    resolve_entry,
    resolve_exit,
)

_CLOSE_EVENT = {
    ExitReason.STOP: SignalEventType.CLOSED_STOP,
    ExitReason.TARGET: SignalEventType.CLOSED_TARGET,
    ExitReason.RULE: SignalEventType.CLOSED_RULE,
}


class SignalEngineErrorCode(StrEnum):
    VERSION_HAS_NO_RISK_PLAN = "version_has_no_risk_plan"
    NOT_A_SESSION = "not_a_session"
    SESSION_OUT_OF_ORDER = "session_out_of_order"
    SESSION_SKIPPED = "session_skipped"
    MISSING_BAR = "missing_bar"


class SignalEngineError(Exception):
    """The session was refused; nothing was written. ``code`` is from a closed list."""

    def __init__(self, code: SignalEngineErrorCode, message: str) -> None:
        self.code = code
        super().__init__(f"{code.value}: {message}")


@dataclass(frozen=True)
class SessionOutcome:
    session_date: date
    already_processed: bool
    events: tuple[SignalLogEvent, ...]


@dataclass(frozen=True)
class _ActiveSignal:
    signal_session: date
    levels: SignalLevels
    event_types: frozenset[str]

    @property
    def is_open(self) -> bool:
        return SignalEventType.ENTRY_FILLED in self.event_types

    @property
    def rule_exit_due(self) -> bool:
        return SignalEventType.RULE_EXIT_SIGNALLED in self.event_types


def _active_signal(session: Session, version_id, asset: str) -> _ActiveSignal | None:
    """The newest signal of (version, asset) if it has not expired or closed."""

    latest = session.execute(
        select(func.max(SignalLogEvent.signal_session)).where(
            SignalLogEvent.strategy_version_id == version_id, SignalLogEvent.asset == asset
        )
    ).scalar_one()
    if latest is None:
        return None
    rows = list(
        session.execute(
            select(SignalLogEvent).where(
                SignalLogEvent.strategy_version_id == version_id,
                SignalLogEvent.asset == asset,
                SignalLogEvent.signal_session == latest,
            )
        ).scalars()
    )
    types = frozenset(row.event_type for row in rows)
    if types & TERMINAL_EVENT_TYPES:
        return None
    first = rows[0]
    return _ActiveSignal(
        signal_session=latest,
        levels=SignalLevels(reference=first.reference_price, stop=first.stop_price, target=first.target_price),
        event_types=types,
    )


def _check_order(session: Session, settings: Settings, version_id, asset: str, session_date: date) -> None:
    exchange = settings.market_data.calendar.exchange
    if not persisted_session_dates(session, start=session_date, end=session_date, exchange=exchange):
        raise SignalEngineError(SignalEngineErrorCode.NOT_A_SESSION, f"{session_date} is not a market session")
    last = session.execute(
        select(func.max(SignalSessionRun.session_date)).where(
            SignalSessionRun.strategy_version_id == version_id, SignalSessionRun.asset == asset
        )
    ).scalar_one()
    if last is None:
        return
    if session_date < last:
        raise SignalEngineError(
            SignalEngineErrorCode.SESSION_OUT_OF_ORDER,
            f"{session_date} is before the last processed session {last}",
        )
    between = persisted_session_dates(session, start=last, end=session_date, exchange=exchange)
    if between != [last, session_date]:
        raise SignalEngineError(
            SignalEngineErrorCode.SESSION_SKIPPED,
            f"{len(between) - 2} session(s) between {last} and {session_date} were not processed",
        )


def run_signal_session(
    session: Session,
    settings: Settings,
    *,
    version: StrategyVersion,
    asset: str,
    session_date: date,
    bar_source: tuple[str, bool],
    now: datetime | None = None,
) -> SessionOutcome:
    """Process one session. Raises ``SignalEngineError`` and writes nothing when refused."""

    strategy = strategy_from_version(settings, version, universe=(asset,), bar_source=bar_source)
    risk = strategy.risk_plan
    if risk is None:
        raise SignalEngineError(
            SignalEngineErrorCode.VERSION_HAS_NO_RISK_PLAN,
            f"strategy version {version.id} is a v1 specification without a stop and a target",
        )

    already = session.execute(
        select(SignalSessionRun.id).where(
            SignalSessionRun.strategy_version_id == version.id,
            SignalSessionRun.asset == asset,
            SignalSessionRun.session_date == session_date,
        )
    ).first()
    if already is not None:
        return SessionOutcome(session_date=session_date, already_processed=True, events=())

    _check_order(session, settings, version.id, asset, session_date)
    provider, adjusted = bar_source
    bar = bars_for_session_date(session, session_date, symbols=[asset], adjusted=adjusted, provider=provider).get(asset)
    if bar is None:
        raise SignalEngineError(SignalEngineErrorCode.MISSING_BAR, f"no {provider} bar for {asset} on {session_date}")

    recorded_at = now or datetime.now(UTC)
    events: list[SignalLogEvent] = []

    def record(
        signal_session: date,
        levels: SignalLevels,
        event_type: SignalEventType,
        *,
        price: Decimal | None = None,
        expiry_reason: str | None = None,
        intrabar_order_unknown: bool | None = None,
    ) -> None:
        events.append(
            SignalLogEvent(
                strategy_version_id=version.id,
                asset=asset,
                signal_session=signal_session,
                event_type=event_type.value,
                event_session=session_date,
                spec_sha256=version.spec_sha256,
                reference_price=levels.reference,
                stop_price=levels.stop,
                target_price=levels.target,
                price=price,
                expiry_reason=expiry_reason,
                intrabar_order_unknown=intrabar_order_unknown,
                recorded_at=recorded_at,
            )
        )

    active = _active_signal(session, version.id, asset)
    in_trade = False

    if active is not None and not active.is_open:
        # Issued at the previous close: resolve the entry at today's open.
        entry = resolve_entry(active.levels, open_price=bar.open, min_reward_risk=risk.min_reward_risk)
        if entry.opened:
            record(active.signal_session, active.levels, SignalEventType.ENTRY_FILLED, price=entry.entry_price)
            in_trade = True
        else:
            record(active.signal_session, active.levels, SignalEventType.EXPIRED, expiry_reason=entry.outcome.value)
        rule_exit_due = False
    elif active is not None:
        in_trade = True
        rule_exit_due = active.rule_exit_due
    else:
        rule_exit_due = False

    if in_trade:
        assert active is not None
        decision = resolve_exit(
            active.levels, open_price=bar.open, high=bar.high, low=bar.low, rule_exit_due=rule_exit_due
        )
        if decision is not None:
            record(
                active.signal_session,
                active.levels,
                _CLOSE_EVENT[decision.reason],
                price=decision.price,
                intrabar_order_unknown=decision.intrabar_order_unknown,
            )
            in_trade = False

    # The close: evaluate the strategy on bars up to and including today.
    signal = strategy.generate_signals(session, session_date).signals[0]
    if in_trade and signal.direction is SignalDirection.EXIT:
        assert active is not None
        if not active.rule_exit_due:
            record(active.signal_session, active.levels, SignalEventType.RULE_EXIT_SIGNALLED)
    elif not in_trade and signal.direction is SignalDirection.LONG:
        levels = plan_levels(signal.indicators.close, risk)
        record(session_date, levels, SignalEventType.ISSUED)

    session.add_all(events)
    session.add(
        SignalSessionRun(
            strategy_version_id=version.id,
            asset=asset,
            session_date=session_date,
            spec_sha256=version.spec_sha256,
            recorded_at=recorded_at,
        )
    )
    session.flush()
    return SessionOutcome(session_date=session_date, already_processed=False, events=tuple(events))


def run_signal_sessions(
    session: Session,
    settings: Settings,
    *,
    version: StrategyVersion,
    asset: str,
    start: date,
    end: date,
    bar_source: tuple[str, bool],
) -> list[SessionOutcome]:
    """Process every market session in ``[start, end]`` in order."""

    exchange = settings.market_data.calendar.exchange
    return [
        run_signal_session(
            session, settings, version=version, asset=asset, session_date=session_date, bar_source=bar_source
        )
        for session_date in persisted_session_dates(session, start=start, end=end, exchange=exchange)
    ]
