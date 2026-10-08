"""S4 backend contract for the research console: the readiness verdict split into
preflight and inputs states (pending, failed with cascade, stale, verified), the catalog
and saved-list routes with their coverage notes, the run-curve read and the static
report renderer (deterministic, complete, inline SVG, written by export)."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from tests.support.migrated_db import migrated_database
from tests.test_research_studies_pipeline import (
    ASSETS,
    FAST_YAML,
    PIN,
    RANGE,
    StubTiingoClient,
    _drain,
    _settings_dict,
)

import trading_platform.api.app as api_app
from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.db.models.daily_bar import DailyBar
from trading_platform.db.models.research import AssetCatalogEntry
from trading_platform.db.session import session_scope
from trading_platform.services import calendar as calendar_module
from trading_platform.services.calendar import upsert_market_sessions
from trading_platform.services.research import tiingo_ingestion
from trading_platform.services.research.report import render_html, render_markdown, svg_line_chart
from trading_platform.services.research.strategies import ResearchStrategyService
from trading_platform.services.tiingo import TiingoDailyRow


class BrokenRowsClient(StubTiingoClient):
    """One asset carries an OHLC violation on one session: the freeze must fail."""

    def fetch_daily_prices(self, ticker: str, from_date: date, to_date: date) -> list[TiingoDailyRow]:
        rows = super().fetch_daily_prices(ticker, from_date, to_date)
        if ticker == "BBB" and rows:
            broken = rows[len(rows) // 2]
            rows[len(rows) // 2] = TiingoDailyRow(**{**broken.__dict__, "high": broken.low - Decimal(1), "adj_high": broken.adj_low - Decimal(1)})
        return rows


@pytest.fixture()
def console_db(monkeypatch: pytest.MonkeyPatch, tmp_path) -> Iterator[dict]:
    with migrated_database(monkeypatch, "research_s4") as name:
        monkeypatch.setenv("TRADING_PLATFORM_RESEARCH__MODE", "true")
        monkeypatch.setenv("TRADING_PLATFORM_RESEARCH__CALENDAR_START", PIN.isoformat())
        monkeypatch.setenv("TRADING_PLATFORM_RESEARCH__TIINGO__API_KEY", "stub")
        monkeypatch.setenv("TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED", "true")
        monkeypatch.setenv("TRADING_PLATFORM_PATHS__DATA_DIR", str(tmp_path / ".data"))
        clear_settings_cache()
        settings = load_settings()
        calendar_module.pin_calendar_start(PIN)
        # The provider seam is stubbed for EVERY test of this module: no test may reach the
        # real Tiingo endpoint (one run did before this line existed; see plan section 13).
        monkeypatch.setattr(tiingo_ingestion, "TiingoClient", StubTiingoClient)
        monkeypatch.setattr(api_app, "enforce_startup_config", lambda **kwargs: settings)
        StubTiingoClient.calls = []
        with session_scope(settings) as session:
            upsert_market_sessions(session, RANGE[0], RANGE[1])
            for ticker in ASSETS:
                session.add(AssetCatalogEntry(id=uuid.uuid4(), provider="tiingo", ticker=ticker, exchange="NASDAQ", asset_type="Stock", currency="USD", catalog_start=date(1990, 1, 2), catalog_end=date(2026, 10, 6), name=f"{ticker} Corp", name_source="nasdaq_trader", synced_at=datetime.now(UTC)))
            session.add(AssetCatalogEntry(id=uuid.uuid4(), provider="tiingo", ticker="NONAME", exchange="NYSE", asset_type="ETF", currency="USD", catalog_start=date(2001, 1, 2), catalog_end=date(2026, 10, 6), synced_at=datetime.now(UTC)))
        strategies = ResearchStrategyService(settings)
        fast = strategies.approve_draft(uuid.UUID(strategies.create_draft(title="fast", yaml_text=FAST_YAML)["draft_id"]))
        try:
            yield {"settings": settings, "fast": fast, "name": name, "tmp": tmp_path}
        finally:
            calendar_module.pin_calendar_start(None)


# ---------------------------------------------------------------------------
# Readiness: preflight vs inputs
# ---------------------------------------------------------------------------


def test_readiness_separates_preflight_from_input_states(console_db, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = console_db["settings"]
    with TestClient(api_app.create_app()) as client:
        created = client.post("/api/v1/research/studies", json={"name": "states", "settings": _settings_dict([console_db["fast"]["version_id"]])})
        revision_id = created.json()["revision"]["revision_id"]
        readiness = client.get(f"/api/v1/research/revisions/{revision_id}/readiness").json()["readiness"]
        # Preflight ready; inputs pending: nothing has been verified yet and nothing claims so.
        assert readiness["preflight"] == {"ready": True, "errors": []} and readiness["ready"] is True
        assert readiness["inputs"]["state"] == "pending" and readiness["inputs"]["verified"] is False
        assert readiness["inputs"]["attempt"] is None and readiness["inputs"]["data_freeze"] is None

        # A run with corrupt provider rows: the freeze fails, downstream Jobs are cancelled.
        monkeypatch.setattr(tiingo_ingestion, "TiingoClient", BrokenRowsClient)
        accepted = client.post(f"/api/v1/research/revisions/{revision_id}/run")
        assert accepted.status_code == 202
        pending = client.get(f"/api/v1/research/revisions/{revision_id}/readiness").json()["readiness"]["inputs"]
        assert pending["state"] == "pending" and pending["attempt"]["status"] == "queued"
        _drain(settings, 2)  # ingest + freeze; the freeze fails
        failed = client.get(f"/api/v1/research/revisions/{revision_id}/readiness").json()["readiness"]
        assert failed["preflight"]["ready"] is True  # preflight never changes with the inputs
        inputs = failed["inputs"]
        assert inputs["state"] == "failed" and inputs["verified"] is False
        assert inputs["attempt"]["status"] == "failed" and inputs["attempt"]["job_id"] == accepted.json()["jobs"]["freeze"]
        assert {e["code"] for e in inputs["errors"]} == {"integrity_error"}
        assert all(e["item"] == "BBB" for e in inputs["errors"])
        assert any(e.get("session_date") for e in inputs["errors"])  # findings name the session
        assert "cancels every backtest" in inputs["downstream"]
        progress = client.get(f"/api/v1/research/revisions/{revision_id}/progress").json()
        assert progress["by_status"] == {"succeeded": 1, "failed": 1, "cancelled": 9}  # 4 backtests + 4 benchmarks + evaluate
        assert client.get(f"/api/v1/research/revisions/{revision_id}/results").json()["initial"] is None
        assert client.post(f"/api/v1/research/revisions/{revision_id}/run").json()["detail"]["code"] == "run_already_started"

        # A clean second revision: pending while queued, verified after the freeze, stale after a change.
        monkeypatch.setattr(tiingo_ingestion, "TiingoClient", StubTiingoClient)
        study_id = created.json()["study"]["study_id"]
        second = client.post(f"/api/v1/research/studies/{study_id}/revisions", json={"settings": _settings_dict([console_db["fast"]["version_id"]], note="clean")}).json()["revision"]
        rid2 = second["revision_id"]
        client.post(f"/api/v1/research/revisions/{rid2}/run")
        _drain(settings, 2)
        verified = client.get(f"/api/v1/research/revisions/{rid2}/readiness").json()["readiness"]["inputs"]
        assert verified["state"] == "verified" and verified["verified"] is True and verified["errors"] == []
        assert verified["data_freeze"]["integrity"]["ok"] is True and verified["attempt"]["status"] == "succeeded"
        with session_scope(settings) as session:
            bar = session.execute(sa.select(DailyBar).limit(1)).scalar_one()
            bar.close = bar.close + Decimal("0.5")
            bar.updated_at = datetime.now(UTC)
        stale = client.get(f"/api/v1/research/revisions/{rid2}/readiness").json()["readiness"]
        assert stale["preflight"]["ready"] is True and stale["inputs"]["state"] == "stale"
        assert stale["inputs"]["errors"][0]["code"] == "inputs_changed_after_freeze"
        assert stale["inputs"]["data_freeze"]["data_freeze_id"] == verified["data_freeze"]["data_freeze_id"]
        # A final-test submission on stale inputs is refused at the API, not queued to fail later.
        jobs_before = client.get("/api/v1/jobs", params={"limit": 100}).json()["count"]
        with session_scope(settings) as session:
            session.execute(sa.text("INSERT INTO research_freezes (id, study_revision_id, strategy_version_id, asset, acceptance_json, ranking_criteria_hash, code_sha, input_digest, calendar_start, frozen_at) VALUES (:id, :rid, :vid, 'AAA', '{\"constraint_value\": 0.9, \"objective_minimum\": -1}', 'h', 'c', :digest, '2014-01-02', now())"), {"id": str(uuid.uuid4()), "rid": rid2, "vid": console_db["fast"]["version_id"], "digest": verified["data_freeze"]["input_digest"]})
        refused = client.post(f"/api/v1/research/revisions/{rid2}/final-test")
        assert refused.status_code == 409 and refused.json()["detail"]["code"] == "inputs_changed_after_freeze", refused.json()
        assert client.get("/api/v1/jobs", params={"limit": 100}).json()["count"] == jobs_before
        # The failed first revision still reports its own failure, never the second's verdict.
        assert client.get(f"/api/v1/research/revisions/{revision_id}/readiness").json()["readiness"]["inputs"]["state"] == "failed"


def test_settings_refuse_a_repeated_version_and_readiness_names_missing_sessions(console_db) -> None:
    """Several versions of one family are fine; the SAME version twice is refused (it would
    silently double the pairs). Sessions not yet synced for the download range are a
    preflight error naming the gap, instead of a freeze that fails on ``date_not_session``."""

    version_id = console_db["fast"]["version_id"]
    with TestClient(api_app.create_app()) as client:
        repeated = client.post("/api/v1/research/studies", json={"name": "dup", "settings": _settings_dict([version_id, version_id])})
        assert repeated.status_code == 422 and repeated.json()["detail"]["code"] == "invalid_study_settings"
        assert repeated.json()["detail"]["field"] == "strategy_version_ids"

        # Sessions are stored for RANGE only; a range reaching into 2020 is not covered.
        beyond = client.post(
            "/api/v1/research/studies",
            json={
                "name": "beyond sessions",
                "settings": _settings_dict(
                    [version_id],
                    range={"start": RANGE[0].isoformat(), "end": "2020-06-30"},
                    windows={"development": {"start": "2017-01-03", "end": "2018-06-29"}, "validation": {"start": "2018-07-02", "end": "2019-06-28"}, "final_test": {"start": "2019-07-01", "end": "2020-06-30"}},
                ),
            },
        )
        assert beyond.status_code == 201, beyond.json()
        readiness = client.get(f"/api/v1/research/revisions/{beyond.json()['revision']['revision_id']}/readiness").json()["readiness"]
        gaps = [e for e in readiness["preflight"]["errors"] if e["code"] == "market_sessions_not_synced"]
        assert readiness["ready"] is False and len(gaps) == 1
        assert gaps[0]["item"] == "market_sessions" and gaps[0]["first_missing"] == "2020-01-02" and gaps[0]["last_missing"] == "2020-06-30"
        assert gaps[0]["to_date"] == "2020-06-30" and gaps[0]["missing_sessions"] > 100
        # The covered study of the other tests reports no such gap.
        covered = client.post("/api/v1/research/studies", json={"name": "covered", "settings": _settings_dict([version_id])}).json()["revision"]["revision_id"]
        assert client.get(f"/api/v1/research/revisions/{covered}/readiness").json()["readiness"]["preflight"] == {"ready": True, "errors": []}


# ---------------------------------------------------------------------------
# Catalog and saved lists
# ---------------------------------------------------------------------------


def test_catalog_search_coverage_and_saved_lists(console_db) -> None:
    with TestClient(api_app.create_app()) as client:
        coverage = client.get("/api/v1/research/catalog").json()["catalog"]
        assert (coverage["rows_total"], coverage["rows_named"]) == (3, 2) and "populated names only" in coverage["name_search_note"]
        assert coverage["max_assets_per_study"] == 10
        search = client.get("/api/v1/research/catalog/search", params={"q": "aaa corp"}).json()
        assert [i["ticker"] for i in search["items"]] == ["AAA"] and search["name_coverage"] == {"rows_named": 2, "rows_total": 3}
        assert client.get("/api/v1/research/catalog/search", params={"q": "non"}).json()["items"][0]["ticker"] == "NONAME"
        assert client.get("/api/v1/research/catalog/search", params={"q": ""}).json()["items"] == []
        asset = client.get("/api/v1/research/catalog/assets/noname").json()["asset"]
        assert asset["name"] is None and asset["catalog_start"] == "2001-01-02" and "checked" in asset["coverage_note"]
        assert client.get("/api/v1/research/catalog/assets/ZZZ").json()["detail"] == {"code": "asset_not_in_catalog", "ticker": "ZZZ"}

        created = client.post("/api/v1/research/asset-lists", json={"name": "Core", "tickers": ["aaa", "BBB", "AAA"]})
        assert created.status_code == 201 and created.json()["list"]["tickers"] == ["AAA", "BBB"]
        list_id = created.json()["list"]["list_id"]
        assert client.post("/api/v1/research/asset-lists", json={"name": "Core", "tickers": []}).json()["detail"]["code"] == "asset_list_name_taken"
        assert client.post("/api/v1/research/asset-lists", json={"name": " ", "tickers": []}).json()["detail"]["code"] == "invalid_asset_list"
        updated = client.put(f"/api/v1/research/asset-lists/{list_id}", json={"tickers": ["BBB"], "name": "Core 2"}).json()["list"]
        assert updated["tickers"] == ["BBB"] and updated["name"] == "Core 2"
        assert client.get("/api/v1/research/asset-lists").json()["count"] == 1
        # A study created from the list snapshots its tickers.
        study = client.post("/api/v1/research/studies", json={"name": "from list", "settings": _settings_dict([console_db["fast"]["version_id"]], assets=[], asset_list_id=list_id)})
        assert study.status_code == 201 and study.json()["revision"]["settings"]["assets"] == ["BBB"]
        assert client.delete(f"/api/v1/research/asset-lists/{list_id}").json() == {"list_id": list_id, "deleted": True}
        assert client.get(f"/api/v1/research/asset-lists/{list_id}").status_code == 404
        assert client.get(f"/api/v1/research/studies/{study.json()['study']['study_id']}").json()["study"]["revisions"][0]["settings"]["assets"] == ["BBB"]  # snapshot survives list deletion


# ---------------------------------------------------------------------------
# Curves and the static report
# ---------------------------------------------------------------------------


def test_svg_chart_is_deterministic_and_escapes() -> None:
    points = [{"session_date": f"2020-01-0{i}", "total_equity": 100 + i} for i in range(1, 5)]
    svg = svg_line_chart(points, key="total_equity", title="Equity <AAA>")
    assert svg == svg_line_chart(points, key="total_equity", title="Equity <AAA>")
    assert "&lt;AAA&gt;" in svg and "<polyline" in svg and "2020-01-01" in svg
    assert "no series" in svg_line_chart(points[:1], key="total_equity", title="x")


def test_report_contains_every_results_section_and_export_writes_it(console_db) -> None:
    settings = console_db["settings"]
    from trading_platform.orchestration.research_studies import build_study_service

    with TestClient(api_app.create_app()) as client:
        created = client.post("/api/v1/research/studies", json={"name": "report <study>", "settings": _settings_dict([console_db["fast"]["version_id"]], assets=["AAA"])})
        revision_id = created.json()["revision"]["revision_id"]
        assert client.get(f"/api/v1/research/revisions/{revision_id}/report").status_code == 409
        client.post(f"/api/v1/research/revisions/{revision_id}/run")
        _drain(settings, 2 + 4 + 1)
        results = client.get(f"/api/v1/research/revisions/{revision_id}/results").json()
        run = next(r for r in results["runs"] if r["window_role"] == "validation" and not r["benchmark"])
        curve = client.get(f"/api/v1/research/revisions/{revision_id}/runs/{run['run_id']}/curve").json()
        assert curve["asset"] == "AAA" and len(curve["points"]) > 100 and all(p["drawdown"] <= 0 for p in curve["points"])
        assert client.get(f"/api/v1/research/revisions/{revision_id}/runs/{uuid.uuid4()}/curve").status_code == 404

        html_report = client.get(f"/api/v1/research/revisions/{revision_id}/report")
        assert html_report.status_code == 200 and html_report.headers["content-type"].startswith("text/html")
        body = html_report.text
        for needle in ("Study report: report &lt;study&gt;", "independent tests", "Ranking", "Buy-and-hold benchmark", "Evidence grade", "Final test", "not_frozen", "Limitations", "Only research done inside this application", "<svg", "Drawdown, AAA validation", "Profit factor affects the order: False", "entered by the user"):
            assert needle in body, needle
        markdown = client.get(f"/api/v1/research/revisions/{revision_id}/report", params={"format": "md"}).text
        assert markdown.startswith("# Study report: report <study>") and "| Rank |" in markdown and "## Final test" in markdown
        assert markdown == client.get(f"/api/v1/research/revisions/{revision_id}/report", params={"format": "md"}).text  # deterministic

    export = build_study_service(settings).export(uuid.UUID(revision_id))
    assert {"comparison.json", "report.html", "report.md"} <= set(export["files"])
    from pathlib import Path

    comparison = json.loads((Path(export["path"]) / "comparison.json").read_text())
    assert render_markdown(comparison, {}).startswith("# Study report")
    assert "<svg" not in render_html(comparison, {})  # without curves the report carries no charts
    assert (Path(export["path"]) / "report.html").read_text().startswith("<!doctype html>")


def test_health_reports_the_api_mode(console_db) -> None:
    with TestClient(api_app.create_app()) as client:
        assert client.get("/health").json()["mode"] == "research"
    clear_settings_cache()
    assert api_app.create_app(research_mode=False).state.research_mode is False
