"""``POST /api/v1/research/studies/preflight``: the read-only coverage / warm-up check the
study wizard calls while it is being filled. It must agree with readiness (same code path),
write nothing, and keep working when mutations are disabled."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from tests.test_research_console_contract import console_db  # noqa: F401  (fixture)
from tests.test_research_studies_pipeline import RANGE, SLOW_YAML, WINDOWS, _settings_dict

import trading_platform.api.app as api_app
from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.db.models.research import AssetCatalogEntry
from trading_platform.db.session import session_scope
from trading_platform.services.calendar import get_calendar
from trading_platform.services.research import studies as studies_module
from trading_platform.services.research.strategies import ResearchStrategyService
from trading_platform.services.research.studies import StudyService

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
        assert zzz == {
            "ticker": "ZZZ", "in_catalog": False, "name": None, "catalog_start": None, "catalog_end": None,
            "earliest_development_start": None, "feasibility": "unknown",
        }
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


# -- earliest development start / feasibility ---------------------------------------------

FEASIBILITY = {"covers_selected_period", "cannot_cover_selected_period", "no_feasible_period", "unknown"}


def _approve_slow(settings) -> dict:
    strategies = ResearchStrategyService(settings)
    return strategies.approve_draft(uuid.UUID(strategies.create_draft(title="slow", yaml_text=SLOW_YAML)["draft_id"]))


def _expected_candidate(settings, catalog_start: date, warmup: int) -> date:
    """Independent of the production helper: first session on/after the catalog start (and
    the pinned calendar), then ``warmup`` sessions forward."""

    cal = get_calendar(settings.market_data.calendar.exchange)
    first = cal.date_to_session(max(catalog_start, cal.first_session.date()), direction="next")
    return cal.session_offset(first, warmup).date()


def _asset(payload: dict, ticker: str) -> dict:
    return next(a for a in payload["assets"] if a["ticker"] == ticker)


def test_earliest_start_is_minimal_and_drives_feasibility(console_db) -> None:  # noqa: F811
    settings = console_db["settings"]
    fast, slow = console_db["fast"], _approve_slow(settings)
    h_fast, h_slow = fast["history_required"], slow["history_required"]
    assert h_fast != h_slow
    h_max = max(h_fast, h_slow)
    catalog_start = date(2016, 3, 2)
    _add_catalog(settings, "NEWCO", catalog_start, date(2026, 10, 6))
    service = StudyService(settings)
    cal = get_calendar(settings.market_data.calendar.exchange)
    expected = _expected_candidate(settings, catalog_start, h_max)
    previous = cal.previous_session(expected).date()
    ids = [fast["version_id"], slow["version_id"]]
    with TestClient(api_app.create_app()) as client:
        result = _preflight(client, _body(ids, ["NEWCO"], dev=expected.isoformat()))
        assert result["warmup_sessions_max"] == h_max
        asset = _asset(result, "NEWCO")
        assert asset["earliest_development_start"] == expected.isoformat() and asset["feasibility"] == "covers_selected_period"
        assert result["errors"] == []
        # Readiness boundary rules: covered for both versions at the candidate...
        for h in (h_fast, h_slow):
            required = service._required_start(expected, h)
            assert required is not None and required >= catalog_start
        # ... and minimal: one session earlier the longest warm-up reaches before the catalog.
        before = service._required_start(previous, h_max)
        assert before is not None and before < catalog_start
        # Brute force over every session: nothing earlier than the candidate is covered.
        covered = [
            d.date()
            for d in cal.sessions_in_range(cal.first_session, expected)
            if all((r := service._required_start(d.date(), h)) is not None and r >= catalog_start for h in (h_fast, h_slow))
        ]
        assert covered and covered[0] == expected

        # A requested start one session too early is flagged, and agrees with the error list.
        early = _preflight(client, _body(ids, ["NEWCO"], dev=previous.isoformat()))
        assert _asset(early, "NEWCO")["earliest_development_start"] == expected.isoformat()
        assert _asset(early, "NEWCO")["feasibility"] == "cannot_cover_selected_period"
        assert {e["code"] for e in early["errors"]} == {"coverage_start_too_late"}
        # The default wizard start (well after the candidate) is covered; a later one too.
        assert _asset(_preflight(client, _body(ids, ["NEWCO"])), "NEWCO")["feasibility"] == "covers_selected_period"
        # Range end beyond the catalog end: cannot cover although the start is fine.
        assert _asset(_preflight(client, _body(ids, ["NEWCO"], end="2027-06-01", dev=expected.isoformat())), "NEWCO")["feasibility"] == "cannot_cover_selected_period"
        # A single shorter version moves the candidate earlier.
        fast_only = _preflight(client, _body([fast["version_id"]], ["NEWCO"]))
        assert fast_only["warmup_sessions_max"] == h_fast
        assert _asset(fast_only, "NEWCO")["earliest_development_start"] == _expected_candidate(settings, catalog_start, h_fast).isoformat()
        # No development start given: the earliest start is still reported, feasibility unknown.
        no_dev = _preflight(client, _body(ids, ["NEWCO"], dev=None))
        assert _asset(no_dev, "NEWCO")["earliest_development_start"] == expected.isoformat()
        assert _asset(no_dev, "NEWCO")["feasibility"] == "unknown"


def test_catalog_reaching_before_the_pinned_calendar_is_clamped_to_it(console_db) -> None:  # noqa: F811
    settings = console_db["settings"]
    fast = console_db["fast"]
    h = fast["history_required"]
    service = StudyService(settings)
    with TestClient(api_app.create_app()) as client:
        asset = _asset(_preflight(client, _body([fast["version_id"]], ["AAA"])), "AAA")  # catalog_start 1990, pin 2014-01-02
    cal = get_calendar(settings.market_data.calendar.exchange)
    expected = cal.session_offset(cal.first_session, h).date()
    assert asset["earliest_development_start"] == expected.isoformat() and asset["feasibility"] == "covers_selected_period"
    required = service._required_start(expected, h)
    assert required == date(2014, 1, 2)
    previous = cal.previous_session(expected).date()
    assert service._required_start(previous, h) is None or service._required_start(previous, h) < date(2014, 1, 2)


def test_no_room_for_three_windows_is_no_feasible_period(console_db) -> None:  # noqa: F811
    settings = console_db["settings"]
    fast = console_db["fast"]
    h = fast["history_required"]
    catalog_start = date(2016, 3, 2)
    candidate = _expected_candidate(settings, catalog_start, h)
    _add_catalog(settings, "TINY", catalog_start, candidate + timedelta(days=1))  # candidate + 2 days > end
    _add_catalog(settings, "EDGE", catalog_start, candidate + timedelta(days=2))  # exactly three calendar days
    _add_catalog(settings, "NOEND", catalog_start, None)
    ids = [fast["version_id"]]
    with TestClient(api_app.create_app()) as client:
        tiny = _asset(_preflight(client, _body(ids, ["TINY"], end=(candidate + timedelta(days=1)).isoformat(), dev=candidate.isoformat())), "TINY")
        assert tiny["earliest_development_start"] == candidate.isoformat() and tiny["feasibility"] == "no_feasible_period"
        edge = _asset(_preflight(client, _body(ids, ["EDGE"], end=(candidate + timedelta(days=2)).isoformat(), dev=candidate.isoformat())), "EDGE")
        assert edge["feasibility"] == "covers_selected_period"
        noend = _asset(_preflight(client, _body(ids, ["NOEND"], end=None, dev=candidate.isoformat())), "NOEND")
        assert noend["earliest_development_start"] == candidate.isoformat() and noend["feasibility"] == "unknown"


def test_unknown_inputs_have_no_earliest_start(console_db) -> None:  # noqa: F811
    settings = console_db["settings"]
    fast = console_db["fast"]
    with session_scope(settings) as session:
        session.add(
            AssetCatalogEntry(
                id=uuid.uuid4(), provider="tiingo", ticker="NOSTART", exchange="NYSE", asset_type="Stock", currency="USD",
                catalog_start=None, catalog_end=date(2026, 10, 6), name="No Start", name_source="nasdaq_trader", synced_at=datetime.now(UTC),
            )
        )
    with TestClient(api_app.create_app()) as client:
        result = _preflight(client, _body([fast["version_id"]], ["ZZZ", "NOSTART", "AAA"]))
        assert result["warmup_sessions_max"] == fast["history_required"]
        for ticker in ("ZZZ", "NOSTART"):
            asset = _asset(result, ticker)
            assert asset["earliest_development_start"] is None and asset["feasibility"] == "unknown"
        assert _asset(result, "AAA")["earliest_development_start"] is not None

        no_versions = _preflight(client, _body([], ["AAA", "ZZZ"]))
        assert no_versions["warmup_sessions_max"] is None
        assert all(a["earliest_development_start"] is None and a["feasibility"] == "unknown" for a in no_versions["assets"])
        only_unapproved = _preflight(client, _body([str(uuid.uuid4())], ["AAA"]))
        assert only_unapproved["warmup_sessions_max"] is None and _asset(only_unapproved, "AAA")["earliest_development_start"] is None

        empty = _preflight(client, {})
        assert empty["warmup_sessions_max"] is None and empty["assets"] == []
        assert all(a["feasibility"] in FEASIBILITY for a in result["assets"])


def test_calendar_out_of_bounds_gives_no_candidate(console_db, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    settings = console_db["settings"]
    fast = console_db["fast"]
    _add_catalog(settings, "FUTURE", date(2300, 1, 3), date(2300, 12, 30))  # beyond the library window
    _add_catalog(settings, "NORMAL", date(2016, 3, 2), date(2026, 10, 6))
    ids = [fast["version_id"]]
    with TestClient(api_app.create_app()) as client:
        future = _asset(_preflight(client, _body(ids, ["FUTURE"])), "FUTURE")
        assert future["earliest_development_start"] is None and future["feasibility"] == "unknown"

        assert _asset(_preflight(client, _body(ids, ["NORMAL"])), "NORMAL")["earliest_development_start"] is not None
        # The readiness function itself says the warm-up is not satisfiable: no candidate verifies.
        monkeypatch.setattr(StudyService, "_required_start", lambda self, window_start, history_required: None)
        blocked = _asset(_preflight(client, _body(ids, ["NORMAL"])), "NORMAL")
        assert blocked["earliest_development_start"] is None and blocked["feasibility"] == "unknown"


def test_catalog_start_on_weekend_or_holiday_maps_to_the_next_session(console_db) -> None:  # noqa: F811
    settings = console_db["settings"]
    fast = console_db["fast"]
    h = fast["history_required"]
    saturday, holiday_weekend = date(2016, 3, 5), date(2016, 1, 16)  # Saturday; Saturday before MLK Monday 2016-01-18
    assert saturday.weekday() == 5 and holiday_weekend.weekday() == 5
    _add_catalog(settings, "SAT", saturday, date(2026, 10, 6))
    _add_catalog(settings, "MLK", holiday_weekend, date(2026, 10, 6))
    cal = get_calendar(settings.market_data.calendar.exchange)
    monday, tuesday = date(2016, 3, 7), date(2016, 1, 19)
    assert cal.is_session(monday) and not cal.is_session(date(2016, 1, 18)) and cal.is_session(tuesday)
    with TestClient(api_app.create_app()) as client:
        result = _preflight(client, _body([fast["version_id"]], ["SAT", "MLK"]))
    assert _asset(result, "SAT")["earliest_development_start"] == cal.session_offset(monday, h).date().isoformat()
    assert _asset(result, "MLK")["earliest_development_start"] == cal.session_offset(tuesday, h).date().isoformat()
