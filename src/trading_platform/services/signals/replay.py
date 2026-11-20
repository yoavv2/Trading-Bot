"""Replay of a signal book from the inputs the daily run kept (docs/SIGNALS-PLAN.md,
Step 2; success condition 3 of the forward log).

``replay_book`` recomputes, without reading ``daily_bars``:

* every event of every asset, by feeding the kept inputs of each processed session, in
  order, to ``session_rules.decide_session`` (the function the daily engine called) and
  carrying the active signal from one session to the next in memory;
* every publication decision, by applying the book's rule to the decisions in the order
  they were recorded: late when recorded at or after the next session's open, otherwise
  held back when ``max_open`` published signals were still open at that moment,
  otherwise published.

It then compares both with what the daily run recorded and returns every difference with
a closed ``kind``. It writes nothing. The inputs are the ones kept at the time, so a
later dividend or split, which rewrites the adjusted history in ``daily_bars``, does not
change the replay.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_platform.core.settings import Settings
from trading_platform.db.models.market_session import MarketSession
from trading_platform.db.models.research import StrategyVersion
from trading_platform.db.models.signal_book import (
    PublicationDecision,
    SignalBook,
    SignalPublication,
)
from trading_platform.db.models.signal_log import (
    TERMINAL_EVENT_TYPES,
    SignalEventType,
    SignalLogEvent,
    SignalSessionInput,
    SignalSessionRun,
)
from trading_platform.db.session import session_scope
from trading_platform.services.signals.publication import (
    BOOKS_DIRECTORY,
    load_book_config,
    order_key,
    recorded_book,
)
from trading_platform.services.signals.session_rules import (
    ActiveSignal,
    EventSpec,
    SessionInputs,
    decide_session,
)
from trading_platform.strategies.research_registry import strategy_from_version


class ReplayDifference(StrEnum):
    INPUTS_MISSING = "inputs_missing"  # a processed session has no kept inputs
    INPUTS_DIGEST_MISMATCH = "inputs_digest_mismatch"  # the kept inputs do not match their SHA-256
    INPUTS_WITHOUT_SESSION = "inputs_without_session"  # kept inputs of a session that was not processed
    EVENT_NOT_IN_LOG = "event_not_in_log"
    EVENT_NOT_IN_REPLAY = "event_not_in_replay"
    EVENT_DIFFERS = "event_differs"
    PUBLICATION_MISSING = "publication_missing"  # a replayed signal of the book has no decision
    PUBLICATION_WITHOUT_SIGNAL = "publication_without_signal"
    PUBLICATION_DIFFERS = "publication_differs"
    CAP_EXCEEDED = "cap_exceeded"


EventKey = tuple[str, date, str]  # asset, signal session, event type


def _fields(
    *,
    event_session: date,
    reference: Decimal,
    stop: Decimal,
    target: Decimal,
    factor: Decimal | None,
    effective_stop: Decimal | None,
    effective_target: Decimal | None,
    price: Decimal | None,
    expiry_reason: str | None,
    flag: bool | None,
) -> dict[str, Any]:
    def text(value: Decimal | None) -> str | None:
        return None if value is None else format(value.normalize(), "f")

    return {
        "event_session": event_session.isoformat(),
        "reference_price": text(reference),
        "stop_price": text(stop),
        "target_price": text(target),
        "adjustment_factor": text(factor),
        "effective_stop_price": text(effective_stop),
        "effective_target_price": text(effective_target),
        "price": text(price),
        "expiry_reason": expiry_reason,
        "intrabar_order_unknown": flag,
    }


def _spec_fields(spec: EventSpec) -> dict[str, Any]:
    return _fields(
        event_session=spec.event_session,
        reference=spec.levels.reference,
        stop=spec.levels.stop,
        target=spec.levels.target,
        factor=spec.adjustment_factor,
        effective_stop=spec.effective_stop,
        effective_target=spec.effective_target,
        price=spec.price,
        expiry_reason=spec.expiry_reason,
        flag=spec.intrabar_order_unknown,
    )


def _row_fields(row: SignalLogEvent) -> dict[str, Any]:
    return _fields(
        event_session=row.event_session,
        reference=row.reference_price,
        stop=row.stop_price,
        target=row.target_price,
        factor=row.adjustment_factor,
        effective_stop=row.effective_stop_price,
        effective_target=row.effective_target_price,
        price=row.price,
        expiry_reason=row.expiry_reason,
        flag=row.intrabar_order_unknown,
    )


def replay_book(session: Session, settings: Settings, book: SignalBook) -> dict[str, Any]:
    """Replay the book and compare with the record. Read-only."""

    version = session.get(StrategyVersion, book.strategy_version_id)
    assert version is not None
    differences: list[dict[str, Any]] = []

    def differ(kind: ReplayDifference, **detail: Any) -> None:
        differences.append({"kind": kind.value, **detail})

    replayed: dict[EventKey, dict[str, Any]] = {}
    sessions_replayed = 0
    for asset in book.assets:
        strategy = strategy_from_version(
            settings, version, universe=(asset,), bar_source=(book.bar_provider, book.bar_adjusted)
        )
        risk = strategy.risk_plan
        assert risk is not None
        processed = set(
            session.execute(
                select(SignalSessionRun.session_date).where(
                    SignalSessionRun.strategy_version_id == version.id, SignalSessionRun.asset == asset
                )
            ).scalars()
        )
        kept = {
            row.session_date: row
            for row in session.execute(
                select(SignalSessionInput).where(
                    SignalSessionInput.strategy_version_id == version.id, SignalSessionInput.asset == asset
                )
            ).scalars()
        }
        for day in sorted(set(kept) - processed):
            differ(ReplayDifference.INPUTS_WITHOUT_SESSION, asset=asset, session=day.isoformat())
        active: ActiveSignal | None = None
        for day in sorted(processed):
            row = kept.get(day)
            if row is None:
                differ(ReplayDifference.INPUTS_MISSING, asset=asset, session=day.isoformat())
                break  # the state after this session is unknown: the asset's replay stops
            inputs = SessionInputs.from_json(row.inputs)
            if inputs.sha256() != row.inputs_sha256 or inputs.session_date != day:
                differ(ReplayDifference.INPUTS_DIGEST_MISMATCH, asset=asset, session=day.isoformat())
                break
            specs, active = decide_session(inputs, active, risk=risk, evaluate=strategy.evaluate_bars)
            sessions_replayed += 1
            for spec in specs:
                replayed[(asset, spec.signal_session, spec.event_type.value)] = _spec_fields(spec)

    logged_rows = list(
        session.execute(
            select(SignalLogEvent).where(
                SignalLogEvent.strategy_version_id == version.id, SignalLogEvent.asset.in_(list(book.assets))
            )
        ).scalars()
    )
    logged = {(row.asset, row.signal_session, row.event_type): _row_fields(row) for row in logged_rows}
    for key in sorted(set(replayed) | set(logged)):
        where = {"asset": key[0], "signal_session": key[1].isoformat(), "event_type": key[2]}
        if key not in logged:
            differ(ReplayDifference.EVENT_NOT_IN_LOG, **where, replayed=replayed[key])
        elif key not in replayed:
            differ(ReplayDifference.EVENT_NOT_IN_REPLAY, **where, logged=logged[key])
        elif replayed[key] != logged[key]:
            changed = sorted(name for name in logged[key] if logged[key][name] != replayed[key][name])
            differ(
                ReplayDifference.EVENT_DIFFERS,
                **where,
                fields=changed,
                logged={name: logged[key][name] for name in changed},
                replayed={name: replayed[key][name] for name in changed},
            )

    # Publication decisions, in the order they were recorded.
    terminal = {event.value for event in TERMINAL_EVENT_TYPES}
    closed_at: dict[tuple[str, date], datetime] = {
        (row.asset, row.signal_session): row.recorded_at for row in logged_rows if row.event_type in terminal
    }
    opens = dict(
        session.execute(
            select(MarketSession.session_date, MarketSession.market_open)
            .where(MarketSession.exchange == book.exchange, MarketSession.session_date > book.first_session)
            .order_by(MarketSession.session_date)
        ).all()
    )
    open_days = sorted(opens)

    def next_open(day: date) -> datetime | None:
        return next((opens[d] for d in open_days if d > day and opens[d] is not None), None)

    decisions = sorted(
        session.execute(select(SignalPublication).where(SignalPublication.book_id == book.id)).scalars(),
        key=lambda p: (p.recorded_at, p.signal_session, p.order_key),
    )
    published: list[SignalPublication] = []
    max_open_seen = 0
    for decision in decisions:
        where = {"asset": decision.asset, "signal_session": decision.signal_session.isoformat()}
        open_before = sum(
            1
            for earlier in published
            if (earlier.asset, earlier.signal_session) not in closed_at
            or closed_at[(earlier.asset, earlier.signal_session)] > decision.recorded_at
        )
        opening = next_open(decision.signal_session)
        if opening is None or decision.recorded_at >= opening:
            expected = PublicationDecision.HELD_BACK_LATE
        elif open_before >= book.max_open:
            expected = PublicationDecision.HELD_BACK_OPEN_CAP
        else:
            expected = PublicationDecision.PUBLISHED
        if (
            decision.outcome != expected.value
            or decision.open_before != open_before
            or decision.order_key != order_key(decision.signal_session, decision.asset)
        ):
            differ(
                ReplayDifference.PUBLICATION_DIFFERS,
                **where,
                logged={"outcome": decision.outcome, "open_before": decision.open_before},
                replayed={"outcome": expected.value, "open_before": open_before},
            )
        if decision.outcome == PublicationDecision.PUBLISHED.value:
            published.append(decision)
            max_open_seen = max(max_open_seen, open_before + 1)
            if open_before + 1 > book.max_open:
                differ(ReplayDifference.CAP_EXCEEDED, **where, open_published=open_before + 1)

    issued = {
        (asset, signal_session)
        for asset, signal_session, event_type in replayed
        if event_type == SignalEventType.ISSUED.value and signal_session >= book.first_session
    }
    decided = {(decision.asset, decision.signal_session) for decision in decisions}
    for asset, signal_session in sorted(issued - decided):
        differ(ReplayDifference.PUBLICATION_MISSING, asset=asset, signal_session=signal_session.isoformat())
    for asset, signal_session in sorted(decided - issued):
        differ(ReplayDifference.PUBLICATION_WITHOUT_SIGNAL, asset=asset, signal_session=signal_session.isoformat())

    return {
        "book": book.name,
        "book_id": str(book.id),
        "sessions_replayed": sessions_replayed,
        "events_replayed": len(replayed),
        "events_logged": len(logged),
        "publications_checked": len(decisions),
        "max_open_published": max_open_seen,
        "differences": len(differences),
        "difference_details": differences,
        "identical": not differences,
    }


def replay_book_by_name(settings: Settings, book_name: str, *, books_directory: Path = BOOKS_DIRECTORY) -> dict[str, Any]:
    """Replay the recorded book of a configuration file. Refused with a closed code when
    the book has never run or its file has changed."""

    config, config_sha256 = load_book_config(book_name, directory=books_directory)
    with session_scope(settings) as session:
        return replay_book(session, settings, recorded_book(session, config, config_sha256))
