"""``POST /api/v1/research/studies/preflight``: the read-only coverage / warm-up check the
study wizard calls while it is being filled. It must agree with readiness (same code path),
write nothing, and keep working when mutations are disabled."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from tests.test_research_console_contract import console_db  # noqa: F401  (fixture)
from tests.test_research_studies_pipeline import RANGE, WINDOWS, _settings_dict

import trading_platform.api.app as api_app
from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.db.models.research import AssetCatalogEntry
from trading_platform.db.session import session_scope
from trading_platform.services.research import studies as studies_module

URL = "/api/v1/research/studies/preflight"
COVERAGE_CODES = {
    "strategy_version_not_approved",
    "asset_not_in_catalog",
    "warmup_not_satisfiable",
    "coverage_start_too_late",
    "coverage_end_too_early",
    "calendar_start_not_pinned",
}
DEV_START = WINDOWS["development"]["start"]
RANGE_END = RANGE[1].isoformat()


def _add_catalog(settings, ticker: str, start: date | None, end: date | None) -> None:
    with session_scope(settings) as session:
        session.add(
            AssetCatalogEntry(
                id=uuid.uuid4(), provider="tiingo", ticker=ticker, exchange="NYSE", asset_type="Stock", currency="USD",
                catalog_start=start, catalog_end=end, name=f"{ticker} Inc", name_source="nasdaq_trader", synced_at=datetime.now(UTC),
            )
        )


def _body(version_ids, assets, *, start=RANGE[0].isoformat(), end=RANGE_END, dev=DEV_START) -> dict:
    return {"strategy_version_ids": version_ids, "assets": assets, "range": {"start": start, "end": end}, "development_start": dev}


def _preflight(client: TestClient, body: dict) -> dict:
    response = client.post(URL, json=body)
    assert response.status_code == 200, response.json()
    return response.json()["preflight"]


def test_preflight_agrees_with_readiness(console_db) -> None:  # noqa: F811
    settings = console_db["settings"]
    version_id = console_db["fast"]["version_id"]
    _add_catalog(settings, "LATE", date(2016, 12, 30), date(2018, 1, 1))  # starts after the warm-up start, ends before the range
    assets = ["AAA", "LATE", "ZZZ"]
    with TestClient(api_app.create_app()) as client:
        created = client.post("/api/v1/research/studies", json={"name": "agree", "settings": _settings_dict([version_id], assets=assets)})
        assert created.status_code == 201, created.json()
        readiness = client.get(f"/api/v1/research/revisions/{created.json()['revision']['revision_id']}/readiness").json()["readiness"]
        preflight = _preflight(client, _body([version_id], assets))
        expected = [e for e in readiness["preflight"]["errors"] if e["code"] in COVERAGE_CODES]
        codes = {e["code"] for e in expected}
        assert {"coverage_start_too_late", "coverage_end_too_early", "asset_not_in_catalog"} <= codes
        assert preflight["errors"] == expected
        by_pair = readiness["checked"]["required_start_by_pair"]
        assert by_pair and preflight["required_start_by_version"] == {version_id: next(iter(by_pair.values()))}
        for pair, required in by_pair.items():
            assert pair.startswith(version_id) and required == preflight["required_start_by_version"][version_id]
        assert preflight["checked"] == {"strategy_versions": 1, "assets": 3, "pairs": 3}
        assert preflight["calendar"]["pinned_start"] == "2014-01-02" and preflight["calendar"]["exchange"] == "XNYS"
        assert [a["in_catalog"] for a in preflight["assets"]] == [True, True, False]
        version = preflight["versions"][0]
        assert version["version_id"] == version_id and version["approved"] is True and version["version_no"] == 1
        assert version["history_required"] == console_db["fast"]["history_required"]


def test_last_completed_session_is_strictly_before_today(console_db, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    with TestClient(api_app.create_app()) as client:
        live = _preflight(client, {})["calendar"]
        today, last = date.fromisoformat(live["today"]), date.fromisoformat(live["last_completed_session"])
        assert last < today and last.weekday() < 5

        monkeypatch.setattr(studies_module, "exchange_today", lambda exchange: date(2026, 10, 12))  # a Monday (Columbus Day: NYSE open)
        monday = _preflight(client, {})["calendar"]
        assert monday["today"] == "2026-10-12" and monday["last_completed_session"] == "2026-10-09"  # the previous Friday

        monkeypatch.setattr(studies_module, "exchange_today", lambda exchange: date(2026, 10, 10))  # a Saturday
        assert _preflight(client, {})["calendar"]["last_completed_session"] == "2026-10-09"

        monkeypatch.setattr(studies_module, "exchange_today", lambda exchange: date(2026, 9, 8))  # Tuesday after Labor Day
        assert _preflight(client, {})["calendar"]["last_completed_session"] == "2026-09-04"  # skips the holiday Monday and the weekend

        monkeypatch.setattr(studies_module, "exchange_today", lambda exchange: date(2300, 1, 1))  # beyond the library window
        assert _preflight(client, {})["calendar"]["last_completed_session"] is None


def test_unknown_ticker_and_unequal_coverage(console_db) -> None:  # noqa: F811
    settings = console_db["settings"]
    version_id = console_db["fast"]["version_id"]
    _add_catalog(settings, "SHORT", date(1995, 1, 3), date(2019, 6, 3))
    with TestClient(api_app.create_app()) as client:
        unknown = _preflight(client, _body([version_id], ["aaa", "zzz", " bbb ", "AAA", "  "]))
        assert [a["ticker"] for a in unknown["assets"]] == ["AAA", "ZZZ", "BBB"]
        zzz = unknown["assets"][1]
        assert zzz == {"ticker": "ZZZ", "in_catalog": False, "name": None, "catalog_start": None, "catalog_end": None}
        assert {"code": "asset_not_in_catalog", "item": "ZZZ"} in unknown["errors"]
        assert unknown["latest_common_catalog_end"] == "2026-10-06"  # ZZZ is ignored

        only_unknown = _preflight(client, _body([version_id], ["ZZZ"]))
        assert only_unknown["latest_common_catalog_end"] is None

        unequal = _preflight(client, _body([version_id], ["AAA", "SHORT"]))
        assert unequal["latest_common_catalog_end"] == "2019-06-03"
        assert unequal["assets"][1]["catalog_end"] == "2019-06-03" and unequal["assets"][1]["name"] == "SHORT Inc"
        assert [e["item"] for e in unequal["errors"] if e["code"] == "coverage_end_too_early"] == [f"{version_id}:SHORT"]

        unapproved = _preflight(client, _body([str(uuid.uuid4()), "not-a-uuid", version_id], ["AAA"]))
        assert [v["approved"] for v in unapproved["versions"]] == [False, False, True]
        assert unapproved["versions"][0]["name"] is None and unapproved["versions"][0]["history_required"] is None
        assert unapproved["versions"][1]["version_id"] == "not-a-uuid"
        assert [e["code"] for e in unapproved["errors"]] == ["strategy_version_not_approved"] * 2
        assert {e["item"] for e in unapproved["errors"]} == {"not-a-uuid", unapproved["versions"][0]["version_id"]}
        assert list(unapproved["required_start_by_version"]) == [version_id]


def test_missing_development_start_skips_warmup_but_keeps_end_checks(console_db) -> None:  # noqa: F811
    settings = console_db["settings"]
    version_id = console_db["fast"]["version_id"]
    _add_catalog(settings, "LATE", date(2016, 12, 30), date(2018, 1, 1))
    with TestClient(api_app.create_app()) as client:
        no_dev = _preflight(client, _body([version_id], ["AAA", "LATE"], dev=None))
        assert no_dev["required_start_by_version"] == {}
        assert [(e["code"], e["item"]) for e in no_dev["errors"]] == [("coverage_end_too_early", f"{version_id}:LATE")]
        no_end = _preflight(client, {"strategy_version_ids": [version_id], "assets": ["LATE"], "development_start": DEV_START})
        assert [e["code"] for e in no_end["errors"]] == ["coverage_start_too_late"]  # no range -> no end check
        empty = _preflight(client, {"strategy_version_ids": [], "assets": [], "range": None, "development_start": None})
        assert empty["errors"] == [] and empty["assets"] == [] and empty["versions"] == []
        assert empty["checked"] == {"strategy_versions": 0, "assets": 0, "pairs": 0}
        assert empty["calendar"]["today"] and empty["calendar"]["last_completed_session"] and empty["latest_common_catalog_end"] is None


def test_bad_dates_and_bodies_are_refused(console_db) -> None:  # noqa: F811
    version_id = console_db["fast"]["version_id"]
    with TestClient(api_app.create_app()) as client:
        for field_name, body in (
            ("development_start", _body([version_id], ["AAA"], dev="2017/01/03")),
            ("range_end", _body([version_id], ["AAA"], end="soon")),
            ("range_start", _body([version_id], ["AAA"], start="2016-13-01")),
        ):
            response = client.post(URL, json=body)
            assert response.status_code == 422, response.json()
            assert response.json()["detail"]["code"] == "invalid_study_settings" and response.json()["detail"]["field"] == field_name
        assert client.post(URL, json={"nope": 1}).json()["detail"]["code"] == "invalid_request"
        assert client.post(URL, json={"assets": "AAA"}).json()["detail"]["code"] == "invalid_request"
        assert client.post(URL, json={"range": "x"}).json()["detail"]["code"] == "invalid_request"
        assert client.post(URL, json={"assets": [3]}).json()["detail"]["code"] == "invalid_study_settings"


def test_preflight_is_read_only_and_works_with_mutations_disabled(console_db, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    version_id = console_db["fast"]["version_id"]
    monkeypatch.setenv("TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED", "false")
    clear_settings_cache()
    disabled = load_settings()
    assert disabled.orchestration.mutations_enabled is False
    monkeypatch.setattr(api_app, "enforce_startup_config", lambda **kwargs: disabled)

    def counts() -> tuple[int, int]:
        with session_scope(disabled) as session:
            return (
                session.execute(sa.text("SELECT count(*) FROM research_studies")).scalar_one(),
                session.execute(sa.text("SELECT count(*) FROM jobs")).scalar_one(),
            )

    before = counts()
    with TestClient(api_app.create_app()) as client:
        refused = client.post("/api/v1/research/studies", json={"name": "x", "settings": _settings_dict([version_id])})
        assert refused.status_code == 403 and refused.json()["detail"]["code"] == "mutations_disabled"
        assert _preflight(client, _body([version_id], ["AAA", "BBB"]))["errors"] == []
    assert counts() == before
