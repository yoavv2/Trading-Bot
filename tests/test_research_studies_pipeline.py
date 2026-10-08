"""S3 end to end on a throwaway research database: readiness refusal with every error,
the initial Job graph (development + validation only), per-asset runs and benchmarks,
evaluation and comparison.json, freeze and final-test rules under concurrency, global
exposure visibility and the inspected flip, technical reruns and reproducibility,
restoration from preserved inputs, exports, and trading isolation.

The Tiingo client is stubbed at the ingestion seam with synthetic rows; the shared
budget, the engine, the Jobs and the database are real.
"""

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from tests.support.migrated_db import migrated_database
from tests.support.research_fixtures import make_bars

import trading_platform.api.app as api_app
from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.db.models import Job, StrategyRun
from trading_platform.db.models.daily_bar import DailyBar
from trading_platform.db.models.research import (
    AssetCatalogEntry,
    DataFreeze,
    ResearchRunLink,
    StudyEvaluation,
)
from trading_platform.db.models.research import (
    TestWindowExposure as ExposureRow,  # noqa: N814 - avoids pytest collecting the ORM class
)
from trading_platform.db.session import session_scope
from trading_platform.jobs.registry import build_research_registry
from trading_platform.jobs.runner import run_worker_loop
from trading_platform.orchestration.research_studies import build_study_service
from trading_platform.services import calendar as calendar_module
from trading_platform.services.calendar import sessions_in_range, upsert_market_sessions
from trading_platform.services.research import tiingo_ingestion
from trading_platform.services.research.backtest import ResearchRunRequest, run_research_backtest
from trading_platform.services.research.freeze import restore_inputs
from trading_platform.services.research.strategies import ResearchStrategyService
from trading_platform.services.research.studies import (
    AlreadyFrozenError,
    FinalTestAlreadyRunError,
    FinalTestNotFrozenError,
    FreezeNotAllowedError,
    RevisionNotReadyError,
    RunAlreadyStartedError,
)
from trading_platform.services.tiingo import TiingoAssetMetadata, TiingoDailyRow

FAST_YAML = """spec_version: 1
name: Fast cross 3/8
timeframe: daily
direction: long_only
indicators:
  fast: {type: sma, source: close, window: 3}
  slow: {type: sma, source: close, window: 8}
entry:
  all_of:
    - {left: close, op: gt, right: fast}
    - {left: fast, op: gt, right: slow}
exit:
  any_of:
    - {left: close, op: lt, right: fast}
"""
SLOW_YAML = FAST_YAML.replace("Fast cross 3/8", "Slow cross 5/20").replace("window: 3}", "window: 5}").replace("window: 8}", "window: 20}")

RANGE = (date(2016, 1, 4), date(2019, 12, 31))
WINDOWS = {
    "development": {"start": "2017-01-03", "end": "2018-06-29"},
    "validation": {"start": "2018-07-02", "end": "2019-03-29"},
    "final_test": {"start": "2019-04-01", "end": "2019-12-31"},
}
ASSETS = ("AAA", "BBB")
PIN = date(2014, 1, 2)


class StubTiingoClient:
    """Serves synthetic raw/adjusted rows for any ticker over the requested range."""

    calls: list[tuple[str, date, date]] = []

    def __init__(self, settings, *, budget=None, **kwargs) -> None:
        self.requests_made = 0

    def fetch_metadata(self, ticker: str) -> TiingoAssetMetadata:
        self.requests_made += 1
        return TiingoAssetMetadata(ticker=ticker, name=f"{ticker} Corp", exchange_code="NASDAQ", start_date=date(1990, 1, 2), end_date=date(2026, 10, 6))

    def fetch_daily_prices(self, ticker: str, from_date: date, to_date: date) -> list[TiingoDailyRow]:
        self.requests_made += 1
        StubTiingoClient.calls.append((ticker, from_date, to_date))
        sessions = sessions_in_range(RANGE[0], RANGE[1])
        bars = make_bars(ticker, sessions, seed={"AAA": 3, "BBB": 11}.get(ticker, 5), drift=0.0006, volatility=0.018)
        return [
            TiingoDailyRow(session_date=b.session_date, open=b.open, high=b.high, low=b.low, close=b.close, volume=b.volume, adj_open=b.open, adj_high=b.high, adj_low=b.low, adj_close=b.close, adj_volume=b.volume, div_cash=Decimal(0), split_factor=Decimal(1))
            for b in bars
            if from_date <= b.session_date <= to_date
        ]

    def close(self) -> None:
        pass


@pytest.fixture()
def pipeline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[dict]:
    with migrated_database(monkeypatch, "research_s3") as name:
        monkeypatch.setenv("TRADING_PLATFORM_RESEARCH__MODE", "true")
        monkeypatch.setenv("TRADING_PLATFORM_RESEARCH__CALENDAR_START", PIN.isoformat())
        monkeypatch.setenv("TRADING_PLATFORM_RESEARCH__TIINGO__API_KEY", "stub")
        monkeypatch.setenv("TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED", "true")
        monkeypatch.setenv("TRADING_PLATFORM_PATHS__DATA_DIR", str(tmp_path / ".data"))
        clear_settings_cache()
        settings = load_settings()
        calendar_module.pin_calendar_start(PIN)
        monkeypatch.setattr(tiingo_ingestion, "TiingoClient", StubTiingoClient)
        monkeypatch.setattr(api_app, "enforce_startup_config", lambda **kwargs: settings)
        StubTiingoClient.calls = []
        with session_scope(settings) as session:
            upsert_market_sessions(session, RANGE[0], RANGE[1])
            for ticker in ASSETS:
                session.add(AssetCatalogEntry(id=uuid.uuid4(), provider="tiingo", ticker=ticker, exchange="NASDAQ", asset_type="Stock", currency="USD", catalog_start=date(1990, 1, 2), catalog_end=date(2026, 10, 6), name=f"{ticker} Corp", synced_at=datetime.now(UTC)))
            session.add(AssetCatalogEntry(id=uuid.uuid4(), provider="tiingo", ticker="LATE", exchange="NASDAQ", asset_type="Stock", currency="USD", catalog_start=date(2018, 1, 2), catalog_end=date(2019, 6, 28), synced_at=datetime.now(UTC)))
        strategies = ResearchStrategyService(settings)
        fast = strategies.approve_draft(uuid.UUID(strategies.create_draft(title="fast", yaml_text=FAST_YAML)["draft_id"]))
        slow = strategies.approve_draft(uuid.UUID(strategies.create_draft(title="slow", yaml_text=SLOW_YAML)["draft_id"]))
        try:
            yield {"settings": settings, "fast": fast, "slow": slow, "name": name, "tmp": tmp_path}
        finally:
            calendar_module.pin_calendar_start(None)


def _settings_dict(versions: list[str], **overrides) -> dict:
    payload = {
        "mode": "single_asset_independent",
        "strategy_version_ids": versions,
        "assets": list(ASSETS),
        "range": {"start": RANGE[0].isoformat(), "end": RANGE[1].isoformat()},
        "windows": WINDOWS,
        "initial_capital": "100000",
        "quantity_policy": "fractional",
        "costs": {"slippage_bps": "5", "commission_per_order": "1"},
        "objective": "return_first",
        "constraint_value": "0.9",
    }
    payload.update(overrides)
    return payload


def _drain(settings, max_jobs: int) -> dict:
    """Execute up to ``max_jobs`` Jobs, one worker pass at a time, and stop early once no
    Job is claimable (a failed graph cascades its dependents to cancelled, so waiting for a
    fixed count would spin forever)."""

    registry = build_research_registry(settings)
    executed, idle = 0, 0
    while executed < max_jobs and idle < 10:
        report = run_worker_loop(worker_id="s3-test", registry=registry, max_jobs=1, once=True, poll_interval_seconds=0.01, settings=settings)
        if report["jobs_executed"]:
            executed += report["jobs_executed"]
            idle = 0
        else:
            idle += 1
    return {"jobs_executed": executed}


def _count(table: str) -> int:
    with session_scope(load_settings()) as session:
        return int(session.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one())


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------


def test_readiness_lists_every_error_and_refuses_the_run(pipeline, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = pipeline["settings"]
    service = build_study_service(settings)
    monkeypatch.setenv("TRADING_PLATFORM_RESEARCH__MAX_ASSETS_PER_STUDY", "2")
    clear_settings_cache()
    service = build_study_service(load_settings())
    unknown_version = str(uuid.uuid4())
    broken = _settings_dict(
        [pipeline["fast"]["version_id"], unknown_version],
        assets=["AAA", "BBB", "ZZZ"],
        windows={**WINDOWS, "validation": {"start": "2018-06-01", "end": "2019-03-29"}},
        costs=None,
        objective=None,
        constraint_value=None,
        mode="portfolio_combined",
    )
    created = service.create_study(name="broken", kind="substantive", settings=broken)
    revision_id = uuid.UUID(created["revision"]["revision_id"])
    calendar_module.pin_calendar_start(None)
    readiness = service.readiness(revision_id)
    calendar_module.pin_calendar_start(PIN)
    codes = {(e["code"], e["item"]) for e in readiness["errors"]}
    assert readiness["ready"] is False
    assert ("mode_not_supported_yet", "portfolio_combined") in codes
    assert ("asset_limit_exceeded", "assets") in codes
    assert ("costs_missing", "costs") in codes and ("objective_missing", "objective") in codes and ("constraint_missing", "constraint_value") in codes
    assert ("windows_overlap_or_unordered", "windows") in codes
    assert ("calendar_start_not_pinned", "calendar") in codes
    assert ("strategy_version_not_approved", unknown_version) in codes
    assert ("asset_not_in_catalog", "ZZZ") in codes
    assert len(readiness["errors"]) >= 9  # every item, nothing collapsed
    assert broken["assets"] == ["AAA", "BBB", "ZZZ"] and service.get_revision(revision_id)["settings"]["assets"] == ["AAA", "BBB", "ZZZ"]  # nothing dropped
    with pytest.raises(RevisionNotReadyError) as info:
        service.run_initial(revision_id)
    assert {e["code"] for e in info.value.detail["errors"]} >= {"asset_limit_exceeded", "costs_missing"}
    assert _count("jobs") == 0


def test_readiness_coverage_and_warmup_checks_per_pair(pipeline) -> None:
    service = build_study_service(pipeline["settings"])
    created = service.create_study(name="coverage", kind="substantive", settings=_settings_dict([pipeline["fast"]["version_id"]], assets=["AAA", "LATE"]))
    readiness = service.readiness(uuid.UUID(created["revision"]["revision_id"]))
    pair = f"{pipeline['fast']['version_id']}:LATE"
    codes = {(e["code"], e["item"]) for e in readiness["errors"]}
    assert codes == {("coverage_start_too_late", pair), ("coverage_end_too_early", pair)}
    assert readiness["checked"]["required_start_by_pair"][pair] < "2017-01-03"
    # A window that needs more history than the pinned calendar offers is warmup_not_satisfiable.
    early = service.create_study(name="early", kind="substantive", settings=_settings_dict([pipeline["fast"]["version_id"]], assets=["AAA"], range={"start": "2014-01-02", "end": "2019-12-31"}, windows={**WINDOWS, "development": {"start": "2014-01-03", "end": "2018-06-29"}}))
    early_readiness = service.readiness(uuid.UUID(early["revision"]["revision_id"]))
    assert {e["code"] for e in early_readiness["errors"]} == {"warmup_not_satisfiable"}


# ---------------------------------------------------------------------------
# The whole pipeline
# ---------------------------------------------------------------------------


def test_initial_run_evaluation_freeze_final_test_exposures_rerun_restore_and_export(pipeline, tmp_path: Path) -> None:
    settings = pipeline["settings"]
    service = build_study_service(settings)
    versions = [pipeline["fast"]["version_id"], pipeline["slow"]["version_id"]]
    trading_before = {t: _count(t) for t in ("paper_orders", "execution_operations", "system_controls", "risk_events", "active_paper_strategy")}

    # Create through the API, as the UI will.
    with TestClient(api_app.create_app()) as client:
        created = client.post("/api/v1/research/studies", json={"name": "S3 pipeline", "kind": "substantive", "settings": _settings_dict(versions)})
        assert created.status_code == 201, created.json()
        revision_id = uuid.UUID(created.json()["revision"]["revision_id"])
        assert client.get(f"/api/v1/research/revisions/{revision_id}/readiness").json()["readiness"]["ready"] is True
        accepted = client.post(f"/api/v1/research/revisions/{revision_id}/run")
        assert accepted.status_code == 202, accepted.json()
        submitted = accepted.json()
        assert client.post(f"/api/v1/research/revisions/{revision_id}/run").json()["detail"]["code"] == "run_already_started"
    # 1 ingest + 1 freeze + (2 versions + 1 benchmark) x 2 assets x 2 windows + 1 evaluate
    assert len(submitted["jobs"]["backtests"]) == 12 and submitted["final_test_runs_created"] == 0
    total_jobs = 2 + 12 + 1
    assert _count("jobs") == total_jobs

    # Concurrent second starts are refused by the database anchor.
    outcomes: list[object] = []
    barrier = threading.Barrier(3)

    def start_again() -> None:
        barrier.wait()
        try:
            outcomes.append(service.run_initial(revision_id))
        except RunAlreadyStartedError as exc:
            outcomes.append(exc)

    threads = [threading.Thread(target=start_again) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(isinstance(o, RunAlreadyStartedError) for o in outcomes) and _count("jobs") == total_jobs

    report = _drain(settings, total_jobs)
    progress = service.progress(revision_id)
    assert progress["by_status"] == {"succeeded": total_jobs}, (report, [j for j in progress["jobs"] if j["status"] != "succeeded"])
    assert StubTiingoClient.calls and all(c[1] < date(2017, 1, 3) and c[2] == RANGE[1] for c in StubTiingoClient.calls)
    # Every authenticated request went through the shared ledger.
    assert _count("provider_request_ledger") == 0  # the stub bypasses HTTP; the real client charges the ledger (tests/test_research_budget.py)

    with session_scope(settings) as session:
        links = list(session.execute(sa.select(ResearchRunLink)).scalars())
        assert len(links) == 12 and {link.window_role for link in links} == {"development", "validation"}
        assert all(link.input_digest and link.code_sha and link.study_revision_id == revision_id for link in links)
        assert all((link.spec_sha256 is None) == (link.strategy_version_id is None) for link in links)
        runs = {r.id: r for r in session.execute(sa.select(StrategyRun)).scalars()}
        assert all(runs[link.run_id].parameters_snapshot["engine_options"]["bar_provider"] == "tiingo" for link in links)
        assert all(runs[link.run_id].parameters_snapshot["engine_options"]["quantity_policy"] == "fractional" for link in links)
        assert all(runs[link.run_id].parameters_snapshot["engine_options"]["max_concurrent_positions"] == 1 for link in links)
        freeze_row = session.execute(sa.select(DataFreeze)).scalar_one()
        inputs_path = Path(freeze_row.inputs_path)
        assert inputs_path.exists() and str(inputs_path).startswith(str(tmp_path))

    results = service.results(revision_id)
    initial = results["initial"]
    assert initial["windows_evaluated"] == ["development", "validation"]
    assert results["final_test"] == {"state": "not_run"} and initial["final_test"]["state"] == "not_frozen"
    assert len(initial["candidates"]) == 4 and initial["mode_label"].startswith("independent tests")
    benchmarks = [r for r in results["runs"] if r["benchmark"]]
    assert len(benchmarks) == 4 and all(r["metrics"]["closed_trades"] == 0 and r["metrics"]["net_total_return"] is not None for r in benchmarks)
    assert all(c["benchmark"]["validation"]["net_total_return"] is not None for c in initial["candidates"])
    assert all(c["windows"]["validation"]["excess_return_vs_benchmark"] is not None for c in initial["candidates"] if c["status"] != "not_evaluable")
    assert initial["verdict"] in {"leading_candidate_identified", "insufficient_evidence", "no_candidate_qualifies"}
    assert initial["verdict"] == "leading_candidate_identified", [(c["asset"], c["status"], c["status_reasons"]) for c in initial["candidates"]]
    for candidate in initial["candidates"]:
        for role in ("development", "validation"):
            metrics = candidate["windows"][role]["metrics"]
            assert metrics["net_total_return"] is not None and metrics["max_drawdown"] <= 0
            assert candidate["windows"][role]["evidence"]["limitations"]
    assert "Only research done inside this application" in " ".join(initial["limitations"])
    assert results["exposures_marked_inspected"] == 0
    comparison = service.comparison(revision_id)["comparison"]
    assert comparison["ranking"]["profit_factor_affects_order"] is False and comparison["schema_version"] == 1
    json.dumps(comparison)  # serialisable for the UI

    # Freeze rules.
    top = comparison["ranking"]["ranking"][0]
    with pytest.raises(FinalTestNotFrozenError):
        service.run_final_test(revision_id)
    with pytest.raises(FreezeNotAllowedError) as info:
        service.freeze_candidate(revision_id, strategy_version_id=uuid.UUID(top["strategy_version_id"]), asset=top["asset"], acceptance={"constraint_value": 0.9})
    assert info.value.detail["reason"] == "acceptance_incomplete"
    not_eligible = next((c for c in initial["candidates"] if c["status"] != "eligible"), None)
    if not_eligible is not None:
        with pytest.raises(FreezeNotAllowedError):
            service.freeze_candidate(revision_id, strategy_version_id=uuid.UUID(not_eligible["strategy_version_id"]), asset=not_eligible["asset"], acceptance={"constraint_value": 0.9, "objective_minimum": -1})
    acceptance = {"constraint_value": 0.9, "objective_minimum": -0.5}
    freeze_outcomes: list[object] = []
    barrier = threading.Barrier(2)

    def freeze() -> None:
        barrier.wait()
        try:
            freeze_outcomes.append(service.freeze_candidate(revision_id, strategy_version_id=uuid.UUID(top["strategy_version_id"]), asset=top["asset"], acceptance=acceptance, co_leading_choice_reason="picked the first co-leader"))
        except AlreadyFrozenError as exc:
            freeze_outcomes.append(exc)

    threads = [threading.Thread(target=freeze) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(isinstance(o, dict) for o in freeze_outcomes) == 1 and sum(isinstance(o, AlreadyFrozenError) for o in freeze_outcomes) == 1
    frozen = next(o for o in freeze_outcomes if isinstance(o, dict))
    assert frozen["freeze"]["acceptance"]["objective_minimum"] == -0.5 and frozen["exposures"]["count"] == 0
    assert frozen["freeze"]["input_digest"] == initial["data"]["input_digest"]

    # Final test: exactly one graph under concurrent requests; runs only the frozen pair.
    final_outcomes: list[object] = []
    barrier = threading.Barrier(3)

    def final() -> None:
        barrier.wait()
        try:
            final_outcomes.append(service.run_final_test(revision_id))
        except FinalTestAlreadyRunError as exc:
            final_outcomes.append(exc)

    threads = [threading.Thread(target=final) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(isinstance(o, dict) for o in final_outcomes) == 1 and sum(isinstance(o, FinalTestAlreadyRunError) for o in final_outcomes) == 2
    assert _count("jobs") == total_jobs + 3
    exposures = service.exposures(revision_id)
    assert exposures["count"] == 1 and exposures["items"][0]["state"] == "run_recorded" and exposures["items"][0]["run_id"] is None
    _drain(settings, 3)
    with session_scope(settings) as session:
        final_links = list(session.execute(sa.select(ResearchRunLink).where(ResearchRunLink.window_role == "final_test")).scalars())
        assert len(final_links) == 2 and {link.asset for link in final_links} == {top["asset"]}
        assert {str(link.strategy_version_id) for link in final_links} == {top["strategy_version_id"], "None"}
    exposures = service.exposures(revision_id)
    assert exposures["items"][0]["run_id"] is not None and exposures["items"][0]["state"] == "run_recorded"
    results = service.results(revision_id)  # retrieving results flips the state
    final_block = results["final_test"]
    assert final_block["outcome"] in {"frozen_criteria_met", "frozen_criteria_not_met", "insufficient_evidence"}
    assert final_block["benchmark_comparison"] is not None and "Beating buy-and-hold is not required" in final_block["wording"]
    assert final_block["re_ranking"].startswith("none")
    assert results["initial"]["ranking"] == initial["ranking"]  # never re-ranked
    assert results["exposures_marked_inspected"] == 1
    assert service.exposures(revision_id)["items"][0]["state"] == "results_inspected"

    # Global exposure visibility from a duplicated version in a new family and a new study.
    strategies = ResearchStrategyService(settings)
    duplicate = strategies.draft_from_version(uuid.UUID(top["strategy_version_id"]), mode="duplicate")
    new_family = strategies.approve_draft(uuid.UUID(duplicate["draft_id"]))
    other = service.create_study(name="sibling", kind="substantive", settings=_settings_dict([new_family["version_id"]], assets=[top["asset"]], windows={**WINDOWS, "final_test": {"start": "2019-06-03", "end": "2019-12-31"}}))
    seen = service.exposures(uuid.UUID(other["revision"]["revision_id"]))
    assert seen["count"] == 1 and seen["items"][0]["revision_id"] == str(revision_id)
    assert seen["items"][0]["context"]["study_name"] == "S3 pipeline" and seen["items"][0]["context"]["outcome"] == final_block["outcome"]
    assert seen["items"][0]["state"] == "results_inspected" and seen["limitation"].startswith("The application records only")

    # Technical rerun: byte-identical results, then a tampered rerun is a reproducibility failure.
    service.run_final_test(revision_id, rerun=True)
    _drain(settings, 3)
    history = service.results(revision_id)["final_test_history"]
    assert history[-1]["is_rerun"] is True
    latest = service.results(revision_id)["final_test"]
    assert latest["reproducibility"] == {"byte_identical": True, "checks": {"candidate": True, "benchmark": True}, "status": "reproduced"}
    tampered = service.run_final_test(revision_id, rerun=True)
    _drain(settings, 2)
    with session_scope(settings) as session:
        job = session.get(Job, uuid.UUID(tampered["jobs"]["candidate"]))
        run = session.execute(sa.select(StrategyRun).where(StrategyRun.job_id == job.id)).scalar_one()
        run.result_summary = {**run.result_summary, "research": {**run.result_summary["research"], "results_digest": "0" * 64}}
    _drain(settings, 1)
    latest = service.results(revision_id)["final_test"]
    assert latest["reproducibility"]["status"] == "reproducibility_failure" and latest["reproducibility"]["checks"]["candidate"] is False
    with session_scope(settings) as session:
        reasons = sorted(e.reason for e in session.execute(sa.select(ExposureRow).where(ExposureRow.study_revision_id == revision_id)).scalars())
    assert reasons.count("reproducibility_failure") >= 1 and "final_test" in reasons

    # Restoration: the preserved inputs reproduce the original validation run byte for byte.
    original = next(r for r in results["runs"] if r["window_role"] == "validation" and r["strategy_version_id"] == top["strategy_version_id"] and r["asset"] == top["asset"])
    with session_scope(settings) as session:
        session.execute(sa.delete(DailyBar))
        assert session.execute(sa.select(sa.func.count()).select_from(DailyBar)).scalar_one() == 0
        restored = restore_inputs(session, inputs_path)
        assert restored.digest_verified and restored.bars_inserted > 0
    settings_json = service.get_revision(revision_id)["settings"]
    replay = run_research_backtest(
        settings,
        ResearchRunRequest(
            study_revision_id=revision_id,
            strategy_version_id=uuid.UUID(top["strategy_version_id"]),
            asset=top["asset"],
            window_role="validation",
            window_start=date.fromisoformat(WINDOWS["validation"]["start"]),
            window_end=date.fromisoformat(WINDOWS["validation"]["end"]),
            initial_capital=Decimal(settings_json["initial_capital"]),
            commission_per_order=Decimal(settings_json["costs"]["commission_per_order"]),
            slippage_bps=Decimal(settings_json["costs"]["slippage_bps"]),
            quantity_policy=settings_json["quantity_policy"],
            bar_provider="tiingo",
            bar_adjusted=True,
            rerun_of=uuid.UUID(original["run_id"]),
        ),
    )
    assert replay.results_digest == original["results_digest"] and replay.input_digest == original["input_digest"]

    # Export under the data directory only.
    export = service.export(revision_id)
    assert export["path"].startswith(str(tmp_path)) and "comparison.json" in export["files"]
    exported = json.loads((Path(export["path"]) / "comparison.json").read_text())
    assert exported["final_test"]["outcome"] == latest["outcome"]
    assert (Path(export["path"]) / "runs" / original["run_id"] / "trades.csv").exists()

    # Trading side untouched throughout.
    assert {t: _count(t) for t in trading_before} == trading_before
    with session_scope(settings) as session:
        assert session.execute(sa.select(sa.func.count()).select_from(StudyEvaluation)).scalar_one() == 4  # initial + final + 2 reruns
