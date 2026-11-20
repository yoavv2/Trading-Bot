"""Signal books and the publication decision (docs/SIGNALS-PLAN.md, Step 2).

A book is a fixed set of (strategy version, assets) read from a configuration file in
``config/signals/``. The first run records it in ``signal_books`` with the SHA-256 of
the file; a later run with a changed file is refused (``book_changed``), because a
changed book is a new test, not a continuation.

``decide_publications`` gives every issued signal of the book its one decision, in the
order of ``(signal session, sha256("<signal session>:<asset>"))``:

* ``held_back_late`` when the decision is made at or after the open of the next session;
* otherwise ``held_back_open_cap`` when the book already has ``max_open`` published
  signals that have neither expired nor closed;
* otherwise ``published``.

This is the cap rule of the publication-filter study
(``services/research/publication_filters.py``) applied one session at a time. The
database guard of migration 0036 computes the same outcome and refuses any other. A
signal that is held back keeps running in the engine's log and is never published later.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import and_, exists, func, select
from sqlalchemy.orm import Session, aliased

from trading_platform.core.settings import PROJECT_ROOT
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
)

BOOKS_DIRECTORY = PROJECT_ROOT / "config" / "signals"
BOOK_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_ASSET_PATTERN = re.compile(r"^[A-Z][A-Z0-9.\-]{0,14}$")


class SignalBookErrorCode(StrEnum):
    BOOK_NAME_INVALID = "book_name_invalid"
    BOOK_CONFIG_NOT_FOUND = "book_config_not_found"
    BOOK_CONFIG_INVALID = "book_config_invalid"
    BOOK_CHANGED = "book_changed"
    VERSION_NOT_FOUND = "version_not_found"
    VERSION_AMBIGUOUS = "version_ambiguous"


class SignalBookError(Exception):
    """The book cannot be used; nothing was written. ``code`` is from a closed list."""

    def __init__(self, code: SignalBookErrorCode, message: str) -> None:
        self.code = code
        super().__init__(f"{code.value}: {message}")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BookBarSource(_Strict):
    provider: str = Field(min_length=1, max_length=32)
    adjusted: bool


class BookConfig(_Strict):
    name: str
    strategy_spec_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    assets: list[str] = Field(min_length=1)
    max_open: int = Field(gt=0)
    first_session: date
    exchange: str = Field(default="XNYS", min_length=1, max_length=16)
    bar_source: BookBarSource

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not BOOK_NAME_PATTERN.fullmatch(value):
            raise ValueError("name must be lower-case letters, digits and hyphens")
        return value

    @field_validator("assets")
    @classmethod
    def _assets(cls, value: list[str]) -> list[str]:
        if any(not _ASSET_PATTERN.fullmatch(asset) for asset in value):
            raise ValueError("assets must be upper-case tickers")
        if len(set(value)) != len(value):
            raise ValueError("assets must not repeat")
        return sorted(value)


def load_book_config(name: str, *, directory: Path = BOOKS_DIRECTORY) -> tuple[BookConfig, str]:
    """The book configuration ``<directory>/<name>.yaml`` and the SHA-256 of the file."""

    if not BOOK_NAME_PATTERN.fullmatch(name):
        raise SignalBookError(SignalBookErrorCode.BOOK_NAME_INVALID, repr(name))
    path = directory / f"{name}.yaml"
    if not path.is_file():
        raise SignalBookError(SignalBookErrorCode.BOOK_CONFIG_NOT_FOUND, f"no book configuration named {name}")
    raw = path.read_bytes()
    try:
        config = BookConfig.model_validate(yaml.safe_load(raw))
    except (ValidationError, yaml.YAMLError) as exc:
        raise SignalBookError(SignalBookErrorCode.BOOK_CONFIG_INVALID, f"{name}: {type(exc).__name__}") from exc
    if config.name != name:
        raise SignalBookError(SignalBookErrorCode.BOOK_CONFIG_INVALID, f"{name}: the file names book {config.name}")
    return config, hashlib.sha256(raw).hexdigest()


def ensure_book(session: Session, config: BookConfig, config_sha256: str, *, now: datetime | None = None) -> SignalBook:
    """The recorded book of this configuration, recording it on first use."""

    book = session.execute(select(SignalBook).where(SignalBook.name == config.name)).scalar_one_or_none()
    if book is not None:
        if book.config_sha256 != config_sha256:
            raise SignalBookError(
                SignalBookErrorCode.BOOK_CHANGED,
                f"book {config.name} was recorded from a different configuration file",
            )
        return book
    versions = list(
        session.execute(
            select(StrategyVersion.id).where(StrategyVersion.spec_sha256 == config.strategy_spec_sha256)
        ).scalars()
    )
    if not versions:
        raise SignalBookError(
            SignalBookErrorCode.VERSION_NOT_FOUND, f"no approved version with specification {config.strategy_spec_sha256}"
        )
    if len(versions) > 1:
        raise SignalBookError(
            SignalBookErrorCode.VERSION_AMBIGUOUS,
            f"{len(versions)} approved versions share specification {config.strategy_spec_sha256}",
        )
    book = SignalBook(
        name=config.name,
        strategy_version_id=versions[0],
        assets=list(config.assets),
        max_open=config.max_open,
        first_session=config.first_session,
        exchange=config.exchange,
        bar_provider=config.bar_source.provider,
        bar_adjusted=config.bar_source.adjusted,
        config_sha256=config_sha256,
        created_at=now or datetime.now(UTC),
    )
    session.add(book)
    session.flush()
    return book


def order_key(signal_session: date, asset: str) -> str:
    """The fixed order of same-session signals: lowest key first."""

    return hashlib.sha256(f"{signal_session.isoformat()}:{asset}".encode()).hexdigest()


def open_published_count(session: Session, book: SignalBook) -> int:
    """Published signals of the book that have neither expired nor closed."""

    done = aliased(SignalLogEvent)
    return session.execute(
        select(func.count())
        .select_from(SignalPublication)
        .where(
            SignalPublication.book_id == book.id,
            SignalPublication.outcome == PublicationDecision.PUBLISHED.value,
            ~exists().where(
                done.strategy_version_id == book.strategy_version_id,
                done.asset == SignalPublication.asset,
                done.signal_session == SignalPublication.signal_session,
                done.event_type.in_([event.value for event in TERMINAL_EVENT_TYPES]),
            ),
        )
    ).scalar_one()


@dataclass(frozen=True)
class DecidedPublication:
    asset: str
    signal_session: date
    outcome: PublicationDecision
    open_before: int


def decide_publications(session: Session, book: SignalBook, *, now: datetime | None = None) -> list[DecidedPublication]:
    """Decide every issued signal of the book that has no decision yet. Idempotent: a
    signal is decided once, and a second call writes nothing for it."""

    recorded_at = now or datetime.now(UTC)
    undecided = session.execute(
        select(SignalLogEvent.asset, SignalLogEvent.signal_session)
        .outerjoin(
            SignalPublication,
            and_(
                SignalPublication.book_id == book.id,
                SignalPublication.asset == SignalLogEvent.asset,
                SignalPublication.signal_session == SignalLogEvent.signal_session,
            ),
        )
        .where(
            SignalLogEvent.strategy_version_id == book.strategy_version_id,
            SignalLogEvent.event_type == SignalEventType.ISSUED.value,
            SignalLogEvent.asset.in_(list(book.assets)),
            SignalLogEvent.signal_session >= book.first_session,
            SignalPublication.id.is_(None),
        )
    ).all()

    decided: list[DecidedPublication] = []
    for asset, signal_session in sorted(undecided, key=lambda row: (row[1], order_key(row[1], row[0]))):
        next_open = session.execute(
            select(func.min(MarketSession.market_open)).where(
                MarketSession.exchange == book.exchange, MarketSession.session_date > signal_session
            )
        ).scalar_one()
        open_before = open_published_count(session, book)
        if next_open is None or recorded_at >= next_open:
            outcome = PublicationDecision.HELD_BACK_LATE
        elif open_before >= book.max_open:
            outcome = PublicationDecision.HELD_BACK_OPEN_CAP
        else:
            outcome = PublicationDecision.PUBLISHED
        session.add(
            SignalPublication(
                book_id=book.id,
                asset=asset,
                signal_session=signal_session,
                outcome=outcome.value,
                order_key=order_key(signal_session, asset),
                open_before=open_before,
                recorded_at=recorded_at,
            )
        )
        session.flush()  # the next decision counts this one
        decided.append(DecidedPublication(asset, signal_session, outcome, open_before))
    return decided
