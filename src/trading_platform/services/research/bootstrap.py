"""Idempotent preparation of the persistent research database for the research product.

Called by ``scripts/bootstrap_research.py`` (``make research-bootstrap``) on an already
migrated research database. Every step keeps existing rows:

* the four example strategy versions (``seed_example_versions``: keyed by
  ``(strategy_id, spec_sha256)``, so drafts, later versions and studies stay);
* the public asset catalog (keyless downloads, no Tiingo API budget) when the catalog is
  empty, or on ``refresh_catalog``;
* the exchange sessions from the pinned research calendar start through
  ``sessions_through`` (default: the calendar's last available session, about one year
  ahead) when not already covered.

Nothing here downloads prices, submits Jobs or contacts a broker.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trading_platform.core.settings import Settings
from trading_platform.db.models import MarketSession
from trading_platform.db.session import session_scope
from trading_platform.services.calendar import (
    get_calendar,
    sessions_in_range,
    upsert_market_sessions,
)
from trading_platform.services.research.catalog import (
    CatalogSources,
    fetch_catalog_sources,
    name_coverage,
    sync_asset_catalog,
)
from trading_platform.services.research.environment import apply_research_environment
from trading_platform.services.research.examples import seed_example_versions


class ResearchBootstrapError(RuntimeError):
    """Refusal with a closed reason (``research_mode_off`` or ``calendar_start_not_pinned``)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class BootstrapReport:
    database: str
    created: bool = False
    example_versions: list[dict[str, Any]] = field(default_factory=list)
    catalog_action: str = "skipped"
    catalog_named: int = 0
    catalog_total: int = 0
    sessions_action: str = "skipped"
    sessions_exchange: str = ""
    sessions_from: date | None = None
    sessions_through: date | None = None
    sessions_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "database": self.database,
            "created": self.created,
            "example_versions": self.example_versions,
            "catalog": {
                "action": self.catalog_action,
                "named": self.catalog_named,
                "total": self.catalog_total,
            },
            "market_sessions": {
                "action": self.sessions_action,
                "exchange": self.sessions_exchange,
                "from": self.sessions_from.isoformat() if self.sessions_from else None,
                "through": self.sessions_through.isoformat() if self.sessions_through else None,
                "count": self.sessions_count,
            },
        }


def default_sessions_through(exchange: str) -> date:
    """The last session the exchange calendar can answer for (the library bounds its
    calendars about one year ahead of today and refuses later dates)."""

    return get_calendar(exchange).last_session.date()


def ensure_example_versions(session: Session) -> list[dict[str, Any]]:
    versions = seed_example_versions(session)
    return [
        {
            "strategy_id": str(v.strategy_id),
            "version_no": v.version_no,
            "name": v.name,
            "spec_sha256": v.spec_sha256,
        }
        for v in versions
    ]


def ensure_catalog(
    session: Session, *, fetch_sources: Callable[[], CatalogSources], refresh: bool
) -> tuple[str, int, int]:
    """``(action, named, total)``; downloads only when the catalog is empty or ``refresh``."""

    named, total = name_coverage(session)
    if total > 0 and not refresh:
        return "kept", named, total
    report = sync_asset_catalog(session, fetch_sources())
    return "synced", report.rows_named, report.rows_total


def _stored_sessions(session: Session, exchange: str, start: date, through: date) -> int:
    return int(
        session.execute(
            select(func.count())
            .select_from(MarketSession)
            .where(
                MarketSession.exchange == exchange,
                MarketSession.session_date >= start,
                MarketSession.session_date <= through,
            )
        ).scalar_one()
    )


def ensure_market_sessions(
    session: Session, *, settings: Settings, through: date
) -> tuple[str, str, date, int]:
    """Upsert sessions from the pinned calendar start through ``through`` unless covered."""

    start = settings.research.calendar_start
    if start is None:
        raise ResearchBootstrapError(
            "calendar_start_not_pinned",
            "research.calendar_start is not pinned; set TRADING_PLATFORM_RESEARCH__CALENDAR_START.",
        )
    exchange = settings.market_data.calendar.exchange
    expected = len(sessions_in_range(start, through, exchange))
    stored = _stored_sessions(session, exchange, start, through)
    if stored >= expected:
        return "kept", exchange, start, stored
    upsert_market_sessions(session, start, through, exchange)
    return "synced", exchange, start, _stored_sessions(session, exchange, start, through)


def public_catalog_sources(settings: Settings) -> Callable[[], CatalogSources]:
    """Downloader for the public ticker list and name directories (no API key, no budget)."""

    def fetch() -> CatalogSources:
        import httpx

        user_agent = f"{settings.app.slug}/{settings.app.version} research bootstrap"
        with httpx.Client(
            timeout=settings.research.tiingo.timeout_seconds, follow_redirects=True
        ) as client:
            return fetch_catalog_sources(client, user_agent=user_agent, include_names=True)

    return fetch


def bootstrap_research_database(
    settings: Settings,
    *,
    fetch_sources: Callable[[], CatalogSources] | None = None,
    refresh_catalog: bool = False,
    sessions_through: date | None = None,
    created: bool = False,
) -> BootstrapReport:
    """Seed examples, catalog and sessions on an already migrated research database."""

    if not settings.research.mode:
        raise ResearchBootstrapError(
            "research_mode_off",
            "Refusing: research.mode is off; set TRADING_PLATFORM_RESEARCH__MODE=true.",
        )
    apply_research_environment(settings)
    report = BootstrapReport(database=settings.database.name, created=created)
    through = sessions_through or default_sessions_through(settings.market_data.calendar.exchange)
    with session_scope(settings) as session:
        report.example_versions = ensure_example_versions(session)
        report.catalog_action, report.catalog_named, report.catalog_total = ensure_catalog(
            session,
            fetch_sources=fetch_sources or public_catalog_sources(settings),
            refresh=refresh_catalog,
        )
        (
            report.sessions_action,
            report.sessions_exchange,
            report.sessions_from,
            report.sessions_count,
        ) = ensure_market_sessions(session, settings=settings, through=through)
        report.sessions_through = through
    return report
