"""``services/research/bootstrap.py`` + ``scripts/bootstrap_research.py``: idempotent
preparation of the research database.

Pins that a second run keeps every row (versions, catalog, sessions), that the catalog
is downloaded only when empty or on refresh, that sessions come from the pinned
research calendar, and that the script refuses a trading-mode process.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
import sqlalchemy as sa

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import bootstrap_research  # noqa: E402
from tests.support.migrated_db import migrated_database  # noqa: E402
from tests.test_research_data_pipeline import NASDAQ_TXT, TIINGO_CSV  # noqa: E402

from trading_platform.core.settings import clear_settings_cache, load_settings  # noqa: E402
from trading_platform.services.calendar import pin_calendar_start  # noqa: E402
from trading_platform.services.research.bootstrap import (  # noqa: E402
    ResearchBootstrapError,
    bootstrap_research_database,
    default_sessions_through,
)
from trading_platform.services.research.catalog import CatalogSources  # noqa: E402


@pytest.fixture
def bootstrap_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    monkeypatch.setenv("TRADING_PLATFORM_RESEARCH__MODE", "true")
    monkeypatch.setenv("TRADING_PLATFORM_RESEARCH__CALENDAR_START", "2024-01-02")
    with migrated_database(monkeypatch, "research_bootstrap") as name:
        clear_settings_cache()
        try:
            yield name
        finally:
            pin_calendar_start(None)


def _counts(url: str) -> dict[str, int]:
    engine = sa.create_engine(url)
    with engine.connect() as conn:
        return {
            "versions": conn.execute(
                sa.text("SELECT count(*) FROM strategy_versions")
            ).scalar_one(),
            "catalog": conn.execute(sa.text("SELECT count(*) FROM asset_catalog")).scalar_one(),
            "sessions": conn.execute(sa.text("SELECT count(*) FROM market_sessions")).scalar_one(),
        }


def test_bootstrap_seeds_once_and_keeps_rows_on_rerun(bootstrap_db: str) -> None:
    settings = load_settings()
    downloads = 0

    def fetch() -> CatalogSources:
        nonlocal downloads
        downloads += 1
        return CatalogSources(tiingo_csv_text=TIINGO_CSV, nasdaq_text=NASDAQ_TXT)

    through = date(2024, 3, 28)
    first = bootstrap_research_database(settings, fetch_sources=fetch, sessions_through=through)
    assert len(first.example_versions) == 4
    assert {v["version_no"] for v in first.example_versions} == {1}
    assert first.catalog_action == "synced" and first.catalog_total > 0
    assert first.sessions_action == "synced"
    assert first.sessions_exchange == "XNYS"
    assert first.sessions_from == date(2024, 1, 2)
    # 2024-01-02 .. 2024-03-28 on XNYS: 61 sessions (MLK day, Presidents' day closed; Good Friday is the 29th).
    assert first.sessions_count == 61
    before = _counts(settings.database.url)

    second = bootstrap_research_database(settings, fetch_sources=fetch, sessions_through=through)
    assert downloads == 1, "a populated catalog is kept, not re-downloaded"
    assert second.catalog_action == "kept"
    assert second.sessions_action == "kept"
    assert len(second.example_versions) == 4
    assert _counts(settings.database.url) == before

    # Extending the session range only adds the missing sessions; a refresh re-syncs the catalog.
    third = bootstrap_research_database(
        settings, fetch_sources=fetch, sessions_through=date(2024, 4, 30), refresh_catalog=True
    )
    assert downloads == 2
    assert third.catalog_action == "synced"
    assert third.sessions_action == "synced"
    assert third.sessions_count == 61 + 22
    assert _counts(settings.database.url)["versions"] == before["versions"]
    assert _counts(settings.database.url)["catalog"] == before["catalog"]


def test_bootstrap_refuses_an_unpinned_calendar(
    bootstrap_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TRADING_PLATFORM_RESEARCH__CALENDAR_START")
    clear_settings_cache()
    with pytest.raises(ResearchBootstrapError) as excinfo:
        bootstrap_research_database(
            load_settings(),
            fetch_sources=lambda: CatalogSources(tiingo_csv_text=TIINGO_CSV),
            sessions_through=date(2024, 2, 1),
        )
    assert excinfo.value.code == "calendar_start_not_pinned"


def test_bootstrap_refuses_trading_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRADING_PLATFORM_RESEARCH__MODE", "false")
    clear_settings_cache()
    with pytest.raises(ResearchBootstrapError, match="research.mode is off") as excinfo:
        bootstrap_research_database(load_settings(), fetch_sources=lambda: None)
    assert excinfo.value.code == "research_mode_off"
    clear_settings_cache()


def test_main_refuses_the_trading_database(capsys: pytest.CaptureFixture[str]) -> None:
    assert bootstrap_research.main(["--database", "trading_platform"]) == 2
    assert "trading database" in capsys.readouterr().err
    clear_settings_cache()


def test_default_sessions_through_is_the_calendar_last_session() -> None:
    pin_calendar_start(date(2024, 1, 2))
    try:
        through = default_sessions_through("XNYS")
    finally:
        pin_calendar_start(None)
    today = date.today()
    assert (
        today
        < through
        <= date(today.year + 1, today.month, today.day + 1 if today.day < 28 else 28)
    )
    assert through.weekday() < 5
