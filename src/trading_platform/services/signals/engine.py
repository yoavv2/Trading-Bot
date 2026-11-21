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
functions the backtest engine calls, in the same order within a session. The decisions
of a session are the pure function ``session_rules.decide_session``; this module reads
its inputs and writes its events. After a dividend or a split the levels are rescaled
to the session's price scale (see ``session_rules.py``); the issued levels are kept. Prices in the
log are the rule prices (the open, the stop or the target); the log simulates no costs.

Sessions are processed in calendar order with none skipped: a stop can only be checked
on a session the engine actually processed. ``signal_session_runs`` records each
processed session, so a re-run writes nothing and a gap is refused with a typed error.
Nothing is ever updated or deleted (migration 0034 installs the triggers).

A session whose bar is missing, or whose data failed the integrity check, is not
processed: ``signal_session_skips`` records it with a closed reason and the session
stays the next one to process. The sessions after it wait until its data arrives, so no
stop check is ever lost; the skip is recorded once per reason, never silently ignored.

Every row written for a session carries ``input_data_digest``: the SHA-256 of the
market-data reads the session made (its own bar and the history the strategy
evaluated), in order. A replay over the same data produces the same digest.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trading_platform.core.settings import Settings
from trading_platform.db.models.research import StrategyVersion
from trading_platform.db.models.signal_log import (
    TERMINAL_EVENT_TYPES,
    SessionSkipReason,
    SignalEventType,
    SignalLogEvent,
    SignalSessionInput,
    SignalSessionRun,
    SignalSessionSkip,
)
from trading_platform.services.market_data_access import (
    bars_for_session_date,
    bars_for_sessions,
    persisted_session_dates,
)
from trading_platform.services.read_recording import recording, sha256_hex
from trading_platform.services.signals.session_rules import (
    ActiveSignal,
    InputBar,
    SessionInputs,
    decide_session,
)
from trading_platform.strategies.research_registry import strategy_from_version
from trading_platform.strategies.trade_rules import SignalLevels


class SignalEngineErrorCode(StrEnum):
    VERSION_HAS_NO_RISK_PLAN = "version_has_no_risk_plan"
    NOT_A_SESSION = "not_a_session"
    SESSION_OUT_OF_ORDER = "session_out_of_order"
    SESSION_SKIPPED = "session_skipped"


class SignalEngineError(Exception):
    """The session was refused; nothing was written. ``code`` is from a closed list."""

    def __init__(self, code: SignalEngineErrorCode, message: str) -> None:
        self.code = code
        super().__init__(f"{code.value}: {message}")


@dataclass(frozen=True)
class SessionOutcome:
    """``skipped`` is set when the session was not processed; it has no events then."""

    session_date: date
    already_processed: bool
    events: tuple[SignalLogEvent, ...]
    skipped: SessionSkipReason | None = None


class _InputReads:
    """Collects the recorded market-data reads of one session."""

    def __init__(self) -> None:
        self._reads: list[list[Any]] = []

    def record(self, kind: str, params: Mapping[str, Any], digest: str, count: int) -> None:
        self._reads.append([kind, dict(params), digest, count])

    def digest(self) -> str:
        return sha256_hex(self._reads)


def record_session_skip(
    session: Session,
    *,
    version: StrategyVersion,
    asset: str,
    session_date: date,
    reason: SessionSkipReason,
    detail: str | None = None,
    now: datetime | None = None,
) -> bool:
    """Record that a session could not be processed. Returns False when the same skip
    (version, asset, session, reason) is already recorded, and writes nothing then."""

    known = session.execute(
        select(SignalSessionSkip.id).where(
            SignalSessionSkip.strategy_version_id == version.id,
            SignalSessionSkip.asset == asset,
            SignalSessionSkip.session_date == session_date,
            SignalSessionSkip.reason == reason.value,
        )
    ).first()
    if known is not None:
        return False
    session.add(
        SignalSessionSkip(
            strategy_version_id=version.id,
            asset=asset,
            session_date=session_date,
            reason=reason.value,
            detail=detail,
            recorded_at=now or datetime.now(UTC),
        )
    )
    session.flush()
    return True


def _active_signal(session: Session, version_id: Any, asset: str) -> ActiveSignal | None:
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
    return ActiveSignal(
        signal_session=latest,
        levels=SignalLevels(reference=first.reference_price, stop=first.stop_price, target=first.target_price),
        entered=SignalEventType.ENTRY_FILLED in types,
        rule_exit_due=SignalEventType.RULE_EXIT_SIGNALLED in types,
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
    keep_inputs: bool = False,
) -> SessionOutcome:
    """Process one session. Raises ``SignalEngineError`` and writes nothing when refused.

    A session without a bar is not processed: the skip is recorded and returned in
    ``SessionOutcome.skipped``. With ``keep_inputs`` the bars the session was computed
    from are stored in ``signal_session_inputs`` for a later replay."""

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
    recorded_at = now or datetime.now(UTC)
    active = _active_signal(session, version.id, asset)

    def skip() -> SessionOutcome:
        record_session_skip(
            session,
            version=version,
            asset=asset,
            session_date=session_date,
            reason=SessionSkipReason.MISSING_BAR,
            now=recorded_at,
        )
        return SessionOutcome(
            session_date=session_date, already_processed=False, events=(), skipped=SessionSkipReason.MISSING_BAR
        )

    reads = _InputReads()
    with recording(reads):
        bar = bars_for_session_date(
            session, session_date, symbols=[asset], adjusted=adjusted, provider=provider
        ).get(asset)
        if bar is None:
            return skip()
        signal_session_close: Decimal | None = None
        if active is not None:
            # The signal session's adjusted close as it reads today: a dividend or a split
            # since then has rescaled it, and the levels follow (session_rules.py).
            signal_bar = bars_for_session_date(
                session, active.signal_session, symbols=[asset], adjusted=adjusted, provider=provider
            ).get(asset)
            if signal_bar is None:
                return skip()
            signal_session_close = signal_bar.close
        history = bars_for_sessions(
            session,
            symbol=asset,
            n_sessions=strategy.warmup_periods,
            as_of=session_date,
            adjusted=adjusted,
            provider=provider,
        )

    inputs = SessionInputs(
        session_date=session_date,
        bar=InputBar.of(bar),
        history=tuple(InputBar.of(row) for row in history),
        signal_session_close=signal_session_close,
    )
    specs, _ = decide_session(inputs, active, risk=risk, evaluate=strategy.evaluate_bars)

    input_data_digest = reads.digest()
    events: list[SignalLogEvent] = []
    for spec in specs:
        event = SignalLogEvent(
            strategy_version_id=version.id,
            asset=asset,
            signal_session=spec.signal_session,
            event_type=spec.event_type.value,
            event_session=spec.event_session,
            spec_sha256=version.spec_sha256,
            reference_price=spec.levels.reference,
            stop_price=spec.levels.stop,
            target_price=spec.levels.target,
            adjustment_factor=spec.adjustment_factor,
            effective_stop_price=spec.effective_stop,
            effective_target_price=spec.effective_target,
            price=spec.price,
            expiry_reason=spec.expiry_reason,
            intrabar_order_unknown=spec.intrabar_order_unknown,
            input_data_digest=input_data_digest,
            recorded_at=recorded_at,
        )
        # One statement per event, in order: the database checks each transition against
        # the events already written.
        session.add(event)
        session.flush()
        events.append(event)
    session.add(
        SignalSessionRun(
            strategy_version_id=version.id,
            asset=asset,
            session_date=session_date,
            spec_sha256=version.spec_sha256,
            input_data_digest=input_data_digest,
            recorded_at=recorded_at,
        )
    )
    if keep_inputs:
        session.add(
            SignalSessionInput(
                strategy_version_id=version.id,
                asset=asset,
                session_date=session_date,
                inputs=inputs.to_json(),
                inputs_sha256=inputs.sha256(),
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
    now: datetime | None = None,
    keep_inputs: bool = False,
) -> list[SessionOutcome]:
    """Process every market session in ``[start, end]`` in order, stopping at the first
    session that had to be skipped (the last outcome then has ``skipped`` set)."""

    exchange = settings.market_data.calendar.exchange
    outcomes: list[SessionOutcome] = []
    for session_date in persisted_session_dates(session, start=start, end=end, exchange=exchange):
        outcome = run_signal_session(
            session,
            settings,
            version=version,
            asset=asset,
            session_date=session_date,
            bar_source=bar_source,
            now=now,
            keep_inputs=keep_inputs,
        )
        outcomes.append(outcome)
        if outcome.skipped is not None:
            break
    return outcomes
