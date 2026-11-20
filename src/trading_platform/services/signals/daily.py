"""The daily run of a signal book (docs/SIGNALS-PLAN.md, Step 2).

``run_daily_book`` is what the ``signals-daily`` Job executes after the U.S. close:

1. read the book configuration and record the book on first use;
2. resolve the target session: the requested date, or the latest session that has
   closed. A date that is not a session, has not closed yet or is before the book's first
   session is refused with a closed code and nothing is written;
3. download the bars of the book's assets for the history the strategy reads, ending at
   the target session (skipped when ``ingest`` is false);
4. check the integrity of that range. An asset with an integrity error is not run: its
   next unprocessed session gets a recorded skip (``integrity_failed`` with the code);
5. for every other asset, process each unprocessed session up to the target, in order.
   A session without a bar is a recorded skip (``missing_bar``) and stops that asset;
6. give every issued signal of the book its publication decision.

Each asset runs in its own transaction, so one asset's failure never undoes another's
sessions. A second run for the same session writes nothing new.
"""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from trading_platform.core.settings import Settings
from trading_platform.db.models.market_session import MarketSession
from trading_platform.db.models.research import StrategyVersion
from trading_platform.db.models.signal_book import PublicationDecision, SignalBook
from trading_platform.db.models.signal_log import SessionSkipReason, SignalSessionRun
from trading_platform.db.session import session_scope
from trading_platform.services.market_data_access import persisted_session_dates
from trading_platform.services.research.integrity import check_research_inputs
from trading_platform.services.research.tiingo_ingestion import ingest_tiingo_daily_bars
from trading_platform.services.signals.engine import (
    SignalEngineError,
    record_session_skip,
    run_signal_sessions,
)
from trading_platform.services.signals.publication import (
    BOOKS_DIRECTORY,
    decide_publications,
    ensure_book,
    load_book_config,
    open_published_count,
)
from trading_platform.services.tiingo import TiingoClient

# Calendar days downloaded for N sessions of history: 252 sessions are about 365 days.
_CALENDAR_DAYS_PER_SESSION_NUM, _CALENDAR_DAYS_PER_SESSION_DEN = 8, 5
_HISTORY_MARGIN_DAYS = 30


class DailyRunErrorCode(StrEnum):
    NOT_A_SESSION = "not_a_session"
    SESSION_NOT_CLOSED = "session_not_closed"
    BEFORE_FIRST_SESSION = "before_first_session"
    NO_CLOSED_SESSION = "no_closed_session"


class DailyRunError(Exception):
    """The run was refused before any session was processed. ``code`` is from a closed list."""

    def __init__(self, code: DailyRunErrorCode, message: str) -> None:
        self.code = code
        super().__init__(f"{code.value}: {message}")


@dataclass(frozen=True)
class _Book:
    id: uuid.UUID
    version_id: uuid.UUID
    assets: tuple[str, ...]
    first_session: date
    exchange: str
    bar_source: tuple[str, bool]
    history_required: int


def history_start(target: date, history_required: int) -> date:
    """First calendar day of the range downloaded and checked for a target session."""

    days = history_required * _CALENDAR_DAYS_PER_SESSION_NUM // _CALENDAR_DAYS_PER_SESSION_DEN + _HISTORY_MARGIN_DAYS
    return target - timedelta(days=days)


def _resolve_target(settings: Settings, book: _Book, requested: date | None, now: datetime) -> date:
    with session_scope(settings) as session:
        if requested is None:
            target = session.execute(
                select(func.max(MarketSession.session_date)).where(
                    MarketSession.exchange == book.exchange, MarketSession.market_close <= now
                )
            ).scalar_one()
            if target is None:
                raise DailyRunError(DailyRunErrorCode.NO_CLOSED_SESSION, "the calendar holds no closed session")
        else:
            target = requested
            close = session.execute(
                select(MarketSession.market_close).where(
                    MarketSession.exchange == book.exchange, MarketSession.session_date == target
                )
            ).scalar_one_or_none()
            if close is None:
                raise DailyRunError(DailyRunErrorCode.NOT_A_SESSION, f"{target} is not a market session")
            if close > now:
                raise DailyRunError(DailyRunErrorCode.SESSION_NOT_CLOSED, f"the session of {target} has not closed")
    if target < book.first_session:
        raise DailyRunError(
            DailyRunErrorCode.BEFORE_FIRST_SESSION, f"{target} is before the book's first session {book.first_session}"
        )
    return target


def run_daily_book(
    settings: Settings,
    *,
    book_name: str,
    session_date: date | None = None,
    ingest: bool = True,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    client: TiingoClient | None = None,
    job_id: uuid.UUID | None = None,
    books_directory: Path = BOOKS_DIRECTORY,
    progress: Callable[[str], None] = lambda _step: None,
    checkpoint: Callable[[], None] = lambda: None,
) -> dict[str, Any]:
    """Run one book up to one session. Returns the report the Job stores."""

    started = clock()
    config, config_sha256 = load_book_config(book_name, directory=books_directory)
    with session_scope(settings) as session:
        row = ensure_book(session, config, config_sha256, now=started)
        version = session.get(StrategyVersion, row.strategy_version_id)
        assert version is not None
        book = _Book(
            id=row.id,
            version_id=row.strategy_version_id,
            assets=tuple(row.assets),
            first_session=row.first_session,
            exchange=row.exchange,
            bar_source=(row.bar_provider, row.bar_adjusted),
            history_required=version.history_required,
        )
    target = _resolve_target(settings, book, session_date, started)
    range_start = history_start(target, book.history_required)

    ingestion: dict[str, Any] | None = None
    if ingest:
        checkpoint()
        progress("downloading bars")
        result = ingest_tiingo_daily_bars(
            assets=list(book.assets),
            from_date=range_start,
            to_date=target,
            settings=settings,
            trigger_source="job",
            job_id=job_id,
            client=client,
            refresh_metadata=False,
        )
        ingestion = {
            "run_id": result.run_id,
            "run_status": result.run_status,
            "bars_upserted": result.bars_upserted,
            "assets_failed": list(result.symbols_failed),
        }

    checkpoint()
    progress("checking integrity")
    with session_scope(settings) as session:
        report = check_research_inputs(
            session,
            assets=list(book.assets),
            range_start=range_start,
            range_end=target,
            provider=book.bar_source[0],
            exchange=book.exchange,
            today=started.date(),
        )
    integrity_errors: dict[str, str] = {}
    for finding in report.errors:
        integrity_errors.setdefault(finding.asset, finding.code.value)

    progress("processing sessions")
    sessions_processed = 0
    events: Counter[str] = Counter()
    skipped: dict[str, dict[str, str]] = {}
    refused: dict[str, str] = {}
    for asset in book.assets:
        checkpoint()
        try:
            with session_scope(settings) as session:
                version = session.get(StrategyVersion, book.version_id)
                assert version is not None
                last = session.execute(
                    select(func.max(SignalSessionRun.session_date)).where(
                        SignalSessionRun.strategy_version_id == book.version_id, SignalSessionRun.asset == asset
                    )
                ).scalar_one()
                start = book.first_session if last is None else last + timedelta(days=1)
                pending = persisted_session_dates(session, start=start, end=target, exchange=book.exchange)
                if not pending:
                    continue
                if asset in integrity_errors:
                    record_session_skip(
                        session,
                        version=version,
                        asset=asset,
                        session_date=pending[0],
                        reason=SessionSkipReason.INTEGRITY_FAILED,
                        detail=integrity_errors[asset],
                        now=started,
                    )
                    skipped[asset] = {
                        "session": pending[0].isoformat(),
                        "reason": SessionSkipReason.INTEGRITY_FAILED.value,
                        "detail": integrity_errors[asset],
                    }
                    continue
                outcomes = run_signal_sessions(
                    session,
                    settings,
                    version=version,
                    asset=asset,
                    start=pending[0],
                    end=target,
                    bar_source=book.bar_source,
                )
                for outcome in outcomes:
                    if outcome.skipped is not None:
                        skipped[asset] = {"session": outcome.session_date.isoformat(), "reason": outcome.skipped.value}
                    elif not outcome.already_processed:
                        sessions_processed += 1
                        events.update(event.event_type for event in outcome.events)
        except SignalEngineError as exc:
            refused[asset] = exc.code.value

    checkpoint()
    progress("deciding publications")
    with session_scope(settings) as session:
        book_row = session.get(SignalBook, book.id)
        assert book_row is not None
        # The clock is read again: the download may have taken long enough to pass an open.
        decisions = decide_publications(session, book_row, now=clock())
        open_published = open_published_count(session, book_row)

    return {
        "book": book_name,
        "book_id": str(book.id),
        "strategy_version_id": str(book.version_id),
        "session_date": target.isoformat(),
        "history_from": range_start.isoformat(),
        "ingestion": ingestion,
        "integrity_errors": integrity_errors,
        "sessions_processed": sessions_processed,
        "events": dict(sorted(events.items())),
        "assets_skipped": skipped,
        "assets_refused": refused,
        "publications": dict(sorted(Counter(d.outcome.value for d in decisions).items())),
        "published": sorted(
            (
                {"asset": d.asset, "signal_session": d.signal_session.isoformat()}
                for d in decisions
                if d.outcome is PublicationDecision.PUBLISHED
            ),
            key=lambda item: (item["signal_session"], item["asset"]),
        ),
        "open_published": open_published,
        "complete": not skipped and not refused,
    }
