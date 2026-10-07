"""S2: strategy authoring over HTTP and in the service (research mode, research DB).

Drafts (create/read/edit/delete/duplicate), validation and explanation through the one
shared validator, explicit approval into an immutable version, version listing, lineage,
edit/duplicate of approved versions, concurrency of approvals, and isolation from the
trading tables, the Job queue and the broker.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.exc import DBAPIError
from tests.support.migrated_db import migrated_database

import trading_platform.api.app as api_app
from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.db.models.research import StrategyDraft, StrategyVersion
from trading_platform.db.session import session_scope
from trading_platform.services.research.strategies import (
    SOURCE_DUPLICATE_OF_VERSION,
    SOURCE_EDIT_OF_VERSION,
    DraftInvalidError,
    DraftNotFoundError,
    ResearchStrategyService,
    VersionNotFoundError,
    family_lock_key,
    validate_yaml_text,
)
from trading_platform.strategies.spec.explain import explain
from trading_platform.strategies.spec.validate import validate_yaml

VALID_YAML = """spec_version: 1
name: Trend following 50/200
description: Long above both averages; exit below the fast one.
timeframe: daily
direction: long_only
indicators:
  sma_fast: {type: sma, source: close, window: 50}
  sma_slow: {type: sma, source: close, window: 200}
entry:
  all_of:
    - {left: close, op: gt, right: sma_slow}
    - {left: sma_fast, op: gt, right: sma_slow}
exit:
  any_of:
    - {left: close, op: lt, right: sma_fast}
"""

EDITED_YAML = VALID_YAML.replace("window: 50}", "window: 60}").replace("Trend following 50/200", "Trend following 60/200")

INVALID_YAML = VALID_YAML.replace("op: gt, right: sma_slow}\n    - {left: sma_fast", "op: gt, right: nope}\n    - {left: sma_fast")

BASE = "/api/v1/research/strategies"
TRADING_TABLES = ("jobs", "job_mutations", "strategy_runs", "strategies", "paper_orders", "execution_operations", "system_controls")


@pytest.fixture()
def research_api(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    with migrated_database(monkeypatch, "research_s2") as _name:
        monkeypatch.setenv("TRADING_PLATFORM_RESEARCH__MODE", "true")
        monkeypatch.setenv("TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED", "true")
        clear_settings_cache()
        settings = load_settings()
        monkeypatch.setattr(api_app, "enforce_startup_config", lambda **kwargs: settings)
        with TestClient(api_app.create_app()) as client:
            yield client


@pytest.fixture()
def service(monkeypatch: pytest.MonkeyPatch) -> Iterator[ResearchStrategyService]:
    with migrated_database(monkeypatch, "research_s2_svc") as _name:
        clear_settings_cache()
        yield ResearchStrategyService(load_settings())


def _counts(tables=TRADING_TABLES) -> dict[str, int]:
    with session_scope(load_settings()) as session:
        return {t: int(session.execute(sa.text(f"SELECT count(*) FROM {t}")).scalar_one()) for t in tables}


@pytest.fixture()
def broker_and_jobs_untouched(monkeypatch: pytest.MonkeyPatch):
    import trading_platform.orchestration.job_mutations as job_mutations
    import trading_platform.services.alpaca as alpaca

    def _refuse(*args, **kwargs):
        raise AssertionError("authoring must not enqueue work or build a broker client")

    monkeypatch.setattr(alpaca.AlpacaClient, "__init__", _refuse)
    monkeypatch.setattr(job_mutations.JobOrchestrationService, "submit", _refuse)
    yield


# ---------------------------------------------------------------------------
# HTTP flow
# ---------------------------------------------------------------------------


def test_author_validate_approve_edit_and_duplicate_over_http(research_api: TestClient, broker_and_jobs_untouched) -> None:
    client = research_api
    before = _counts()

    # Create a draft whose YAML is invalid, read it back, see the validator's findings.
    created = client.post(f"{BASE}/drafts", json={"title": "My first idea", "yaml_text": INVALID_YAML})
    assert created.status_code == 201, created.json()
    draft = created.json()["draft"]
    draft_id = draft["draft_id"]
    assert draft["source"] == "manual" and draft["parent_version_id"] is None

    listed = client.get(f"{BASE}/drafts").json()
    assert listed["count"] == 1 and listed["drafts"][0]["draft_id"] == draft_id and "as_of" in listed

    validation = client.get(f"{BASE}/drafts/{draft_id}/validation").json()["validation"]
    assert validation["valid"] is False
    assert {e["code"] for e in validation["errors"]} == {"undefined_reference"}
    assert validation["derived"] is None and validation["explanation"] is None

    # An invalid draft cannot be approved.
    refused = client.post(f"{BASE}/drafts/{draft_id}/approve")
    assert refused.status_code == 422
    assert refused.json()["detail"]["code"] == "draft_invalid"
    assert refused.json()["detail"]["errors"][0]["code"] == "undefined_reference"
    assert client.get(f"{BASE}/versions").json()["count"] == 0

    # Edit the draft; validation now carries the derived values and the explanation.
    updated = client.put(f"{BASE}/drafts/{draft_id}", json={"yaml_text": VALID_YAML})
    assert updated.status_code == 200 and updated.json()["draft"]["yaml_text"] == VALID_YAML
    validation = client.get(f"{BASE}/drafts/{draft_id}/validation").json()["validation"]
    compiled = validate_yaml(VALID_YAML)
    assert validation["valid"] is True
    assert validation["derived"]["spec_sha256"] == compiled.spec_sha256
    assert validation["derived"]["history_required"] == 200
    assert validation["explanation"] == explain(compiled)

    # Ad-hoc validation of text (no draft, no write) uses the same validator.
    adhoc = client.post(f"{BASE}/validate", json={"yaml_text": VALID_YAML}).json()["validation"]
    assert adhoc == {k: v for k, v in validation.items() if k != "draft_id"}

    # Explicit approval: one immutable version, consistent hash/explanation/derived, draft gone.
    approved = client.post(f"{BASE}/drafts/{draft_id}/approve")
    assert approved.status_code == 201, approved.json()
    v1 = approved.json()["version"]
    assert (v1["version_no"], v1["source"], v1["parent_version_id"]) == (1, "manual", None)
    assert v1["spec_sha256"] == compiled.spec_sha256
    assert v1["explanation"] == explain(compiled)
    assert v1["history_required"] == 200 and v1["scale_class"] == "price_scale_free"
    assert v1["name"] == "Trend following 50/200" and v1["yaml_text"] == VALID_YAML
    assert client.get(f"{BASE}/drafts/{draft_id}").status_code == 404
    assert client.post(f"{BASE}/drafts/{draft_id}/approve").status_code == 404

    detail = client.get(f"{BASE}/versions/{v1['version_id']}").json()
    assert detail["version"]["spec_json"] == compiled.spec.model_dump(mode="json")
    lineage = client.get(f"{BASE}/versions/{v1['version_id']}/lineage").json()["lineage"]
    assert [v["version_id"] for v in lineage] == [v1["version_id"]]

    # Editing an approved version opens a draft in its family; approval is v2 with lineage.
    edit = client.post(f"{BASE}/versions/{v1['version_id']}/edit")
    assert edit.status_code == 201
    edit_draft = edit.json()["draft"]
    assert (edit_draft["source"], edit_draft["parent_version_id"]) == (SOURCE_EDIT_OF_VERSION, v1["version_id"])
    assert edit_draft["yaml_text"] == VALID_YAML
    client.put(f"{BASE}/drafts/{edit_draft['draft_id']}", json={"yaml_text": EDITED_YAML, "title": "sixty"})
    v2 = client.post(f"{BASE}/drafts/{edit_draft['draft_id']}/approve").json()["version"]
    assert (v2["strategy_id"], v2["version_no"], v2["parent_version_id"]) == (v1["strategy_id"], 2, v1["version_id"])
    assert v2["spec_sha256"] != v1["spec_sha256"]
    lineage = client.get(f"{BASE}/versions/{v2['version_id']}/lineage").json()["lineage"]
    assert [v["version_no"] for v in lineage] == [1, 2]

    # Duplicating an approved version opens a draft that approves into a NEW family.
    dup = client.post(f"{BASE}/versions/{v2['version_id']}/duplicate").json()["draft"]
    assert (dup["source"], dup["parent_version_id"]) == (SOURCE_DUPLICATE_OF_VERSION, v2["version_id"])
    v3 = client.post(f"{BASE}/drafts/{dup['draft_id']}/approve").json()["version"]
    assert v3["strategy_id"] != v1["strategy_id"] and v3["version_no"] == 1
    assert v3["parent_version_id"] == v2["version_id"]
    lineage = client.get(f"{BASE}/versions/{v3['version_id']}/lineage").json()["lineage"]
    assert [(v["strategy_id"] == v1["strategy_id"], v["version_no"]) for v in lineage] == [(True, 1), (True, 2), (False, 1)]

    families = client.get(BASE).json()
    assert families["count"] == 2
    by_id = {f["strategy_id"]: f for f in families["families"]}
    assert by_id[v1["strategy_id"]]["version_count"] == 2 and by_id[v1["strategy_id"]]["latest"]["version_no"] == 2
    assert client.get(f"{BASE}/versions", params={"strategy_id": v1["strategy_id"]}).json()["count"] == 2
    assert client.get(f"{BASE}/versions").json()["count"] == 3

    # Draft duplication and deletion.
    seed = client.post(f"{BASE}/drafts", json={"title": "scratch", "yaml_text": VALID_YAML}).json()["draft"]
    copy = client.post(f"{BASE}/drafts/{seed['draft_id']}/duplicate").json()["draft"]
    assert copy["draft_id"] != seed["draft_id"] and copy["yaml_text"] == VALID_YAML and copy["title"] == "scratch (copy)"
    assert client.delete(f"{BASE}/drafts/{seed['draft_id']}").json() == {"draft_id": seed["draft_id"], "deleted": True}
    assert client.delete(f"{BASE}/drafts/{seed['draft_id']}").status_code == 404
    assert client.get(f"{BASE}/drafts").json()["count"] == 1

    # Nothing on the trading side moved: no Job, no run, no control row.
    assert _counts() == before


def test_request_shape_errors_are_typed_objects(research_api: TestClient) -> None:
    client = research_api
    assert client.post(f"{BASE}/drafts", json={"title": "x"}).json()["detail"] == {
        "code": "invalid_request", "reason": "missing_field", "field": "yaml_text"
    }
    assert client.post(f"{BASE}/drafts", json={"title": "x", "yaml_text": "a: 1", "extra": 1}).status_code == 422
    assert client.post(f"{BASE}/drafts", content=b"not json").json()["detail"]["reason"] == "body_not_json"
    bad_title = client.post(f"{BASE}/drafts", json={"title": " ", "yaml_text": VALID_YAML})
    assert bad_title.status_code == 422 and bad_title.json()["detail"] == {
        "code": "invalid_draft_input", "field": "title", "reason": "must be 1..120 characters"
    }
    assert client.get(f"{BASE}/drafts/not-a-uuid").json()["detail"] == {"code": "draft_not_found"}
    assert client.get(f"{BASE}/versions/{uuid.uuid4()}").json()["detail"]["code"] == "version_not_found"
    assert client.post(f"{BASE}/versions/{uuid.uuid4()}/edit").status_code == 404
    unparseable = client.post(f"{BASE}/validate", json={"yaml_text": "entry: [unclosed"}).json()["validation"]
    assert unparseable["valid"] is False and unparseable["errors"][0]["code"] == "invalid_type"


# ---------------------------------------------------------------------------
# Database guarantees
# ---------------------------------------------------------------------------


def test_approved_versions_are_immutable_and_drafts_are_not(service: ResearchStrategyService) -> None:
    draft = service.create_draft(title="t", yaml_text=VALID_YAML)
    service.update_draft(uuid.UUID(draft["draft_id"]), title="renamed")
    version = service.approve_draft(uuid.UUID(draft["draft_id"]))
    settings = load_settings()
    for statement in (
        sa.update(StrategyVersion).where(StrategyVersion.id == uuid.UUID(version["version_id"])).values(name="tampered"),
        sa.delete(StrategyVersion).where(StrategyVersion.id == uuid.UUID(version["version_id"])),
    ):
        with pytest.raises(DBAPIError) as info, session_scope(settings) as session:
            session.execute(statement)
        assert "append-only" in str(info.value)
    assert service.get_version(uuid.UUID(version["version_id"]))["name"] == "Trend following 50/200"
    with session_scope(settings) as session:
        assert session.execute(sa.select(sa.func.count()).select_from(StrategyDraft)).scalar_one() == 0


def test_concurrent_approvals_never_duplicate_version_numbers(service: ResearchStrategyService) -> None:
    """Two edits of the same version approved at once get distinct version numbers; two
    approvals of the same draft yield exactly one version."""

    base = service.approve_draft(uuid.UUID(service.create_draft(title="b", yaml_text=VALID_YAML)["draft_id"]))
    family = uuid.UUID(base["strategy_id"])
    assert family_lock_key(family) == family_lock_key(family)
    edits = [
        uuid.UUID(service.draft_from_version(uuid.UUID(base["version_id"]), mode="edit")["draft_id"])
        for _ in range(4)
    ]
    results: list[object] = []
    barrier = threading.Barrier(len(edits))

    def approve(draft_id: uuid.UUID) -> None:
        barrier.wait()
        try:
            results.append(service.approve_draft(draft_id))
        except Exception as exc:  # noqa: BLE001 - recorded for the assertion below
            results.append(exc)

    threads = [threading.Thread(target=approve, args=(d,)) for d in edits]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(isinstance(r, dict) for r in results), results
    numbers = sorted(r["version_no"] for r in results if isinstance(r, dict))
    assert numbers == [2, 3, 4, 5]
    assert len(service.list_versions(strategy_id=family)) == 5

    shared = uuid.UUID(service.draft_from_version(uuid.UUID(base["version_id"]), mode="edit")["draft_id"])
    outcomes: list[object] = []
    barrier = threading.Barrier(3)

    def approve_shared() -> None:
        barrier.wait()
        try:
            outcomes.append(service.approve_draft(shared))
        except DraftNotFoundError as exc:
            outcomes.append(exc)

    threads = [threading.Thread(target=approve_shared) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(isinstance(o, dict) for o in outcomes) == 1
    assert sum(isinstance(o, DraftNotFoundError) for o in outcomes) == 2
    assert len(service.list_versions(strategy_id=family)) == 6


def test_service_errors_and_validation_outcome(service: ResearchStrategyService) -> None:
    missing = uuid.uuid4()
    with pytest.raises(DraftNotFoundError):
        service.get_draft(missing)
    with pytest.raises(VersionNotFoundError):
        service.lineage(missing)
    bad = service.create_draft(title="bad", yaml_text=INVALID_YAML)
    with pytest.raises(DraftInvalidError) as info:
        service.approve_draft(uuid.UUID(bad["draft_id"]))
    assert info.value.code == "draft_invalid" and info.value.errors[0]["code"] == "undefined_reference"
    assert service.list_versions() == []
    outcome = validate_yaml_text(VALID_YAML)
    assert outcome.valid and outcome.derived["terms_used"] == ["close", "sma_fast", "sma_slow"]
    assert validate_yaml_text("spec_version: 1").to_dict()["errors"]
    approved = service.approve_draft(uuid.UUID(service.create_draft(title="ok", yaml_text=VALID_YAML)["draft_id"]), now=datetime(2026, 10, 7, tzinfo=UTC))
    assert approved["approved_at"].startswith("2026-10-07")
