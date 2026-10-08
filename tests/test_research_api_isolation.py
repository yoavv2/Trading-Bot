"""Research-mode API isolation (S0/S1 gap closed before S2).

A research process serves only the research routes plus the infrastructure routes
(health/readiness and the generic Job surface over the research-only registry). No
trading route exists on it, and the trading application is byte-for-byte the surface it
was: no research path, the same eight mutating routes.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from tests.support.migrated_db import migrated_database

import trading_platform.api.app as api_app
from trading_platform.api.dependencies import require_mutations_enabled
from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.db.session import session_scope
from trading_platform.jobs.registry import RESEARCH_JOB_TYPES

INFRASTRUCTURE_ROUTES = {
    ("GET", "/health"),
    ("GET", "/ready"),
    ("GET", "/api/v1/job-types"),
    ("GET", "/api/v1/jobs"),
    ("GET", "/api/v1/jobs/{job_id}"),
    ("GET", "/api/v1/jobs/{job_id}/events"),
    ("GET", "/api/v1/jobs/{job_id}/logs"),
    ("GET", "/api/v1/jobs/{job_id}/progress"),
    ("GET", "/api/v1/jobs/{job_id}/recovery"),
    ("POST", "/api/v1/jobs"),
    ("POST", "/api/v1/jobs/{job_id}/cancel"),
    ("POST", "/api/v1/jobs/{job_id}/retry"),
}

RESEARCH_ROUTES = {
    ("GET", "/api/v1/research/strategies"),
    ("GET", "/api/v1/research/strategies/drafts"),
    ("GET", "/api/v1/research/strategies/drafts/{draft_id}"),
    ("GET", "/api/v1/research/strategies/drafts/{draft_id}/validation"),
    ("GET", "/api/v1/research/strategies/versions"),
    ("GET", "/api/v1/research/strategies/versions/{version_id}"),
    ("GET", "/api/v1/research/strategies/versions/{version_id}/lineage"),
    ("POST", "/api/v1/research/strategies/validate"),
    ("POST", "/api/v1/research/strategies/drafts"),
    ("PUT", "/api/v1/research/strategies/drafts/{draft_id}"),
    ("DELETE", "/api/v1/research/strategies/drafts/{draft_id}"),
    ("POST", "/api/v1/research/strategies/drafts/{draft_id}/duplicate"),
    ("POST", "/api/v1/research/strategies/drafts/{draft_id}/approve"),
    ("POST", "/api/v1/research/strategies/versions/{version_id}/edit"),
    ("POST", "/api/v1/research/strategies/versions/{version_id}/duplicate"),
    ("GET", "/api/v1/research/studies"),
    ("POST", "/api/v1/research/studies"),
    ("GET", "/api/v1/research/studies/{study_id}"),
    ("POST", "/api/v1/research/studies/{study_id}/revisions"),
    ("GET", "/api/v1/research/revisions/{revision_id}"),
    ("GET", "/api/v1/research/revisions/{revision_id}/readiness"),
    ("POST", "/api/v1/research/revisions/{revision_id}/run"),
    ("GET", "/api/v1/research/revisions/{revision_id}/progress"),
    ("GET", "/api/v1/research/revisions/{revision_id}/results"),
    ("GET", "/api/v1/research/revisions/{revision_id}/comparison"),
    ("GET", "/api/v1/research/revisions/{revision_id}/exposures"),
    ("POST", "/api/v1/research/revisions/{revision_id}/freeze"),
    ("POST", "/api/v1/research/revisions/{revision_id}/final-test"),
    ("POST", "/api/v1/research/revisions/{revision_id}/export"),
    ("GET", "/api/v1/research/revisions/{revision_id}/runs/{run_id}/curve"),
    ("GET", "/api/v1/research/revisions/{revision_id}/report"),
    ("GET", "/api/v1/research/catalog"),
    ("GET", "/api/v1/research/catalog/search"),
    ("GET", "/api/v1/research/catalog/assets/{ticker}"),
    ("GET", "/api/v1/research/asset-lists"),
    ("POST", "/api/v1/research/asset-lists"),
    ("GET", "/api/v1/research/asset-lists/{list_id}"),
    ("PUT", "/api/v1/research/asset-lists/{list_id}"),
    ("DELETE", "/api/v1/research/asset-lists/{list_id}"),
    # S5 assistant
    ("GET", "/api/v1/research/assistant"),
    ("GET", "/api/v1/research/assistant/proposals/{ai_draft_id}"),
    ("POST", "/api/v1/research/assistant/proposals"),
    ("POST", "/api/v1/research/assistant/proposals/{ai_draft_id}/apply"),
}

#: The research writes; every one carries ``require_mutations_enabled``.
RESEARCH_MUTATING_ROUTES = {(m, p) for m, p in RESEARCH_ROUTES if m != "GET"} - {
    ("POST", "/api/v1/research/strategies/validate")
}

#: The one research POST that writes nothing (validates submitted text for the editor).
RESEARCH_READ_ONLY_POSTS = {("POST", "/api/v1/research/strategies/validate")}

TRADING_MUTATING_ROUTES = {
    ("POST", "/api/v1/jobs"),
    ("POST", "/api/v1/jobs/{job_id}/cancel"),
    ("POST", "/api/v1/jobs/{job_id}/retry"),
    ("PUT", "/api/v1/controls/kill-switch"),
    ("PUT", "/api/v1/controls/strategies/{strategy_id}"),
    ("PUT", "/api/v1/controls/active-paper-strategy"),
    ("POST", "/api/v1/recovery/intents/{intent_id}/broker-statement"),
    ("POST", "/api/v1/execution-operations/{operation_id}/end"),
}

#: Representative trading mutations and reads a research process must not serve.
TRADING_PROBES = [
    ("PUT", "/api/v1/controls/kill-switch", {"target": "tripped", "reason": "probe"}),
    ("PUT", "/api/v1/controls/active-paper-strategy", {"strategy_id": "trend_following_daily", "reason": "probe"}),
    ("PUT", "/api/v1/controls/strategies/trend_following_daily", {"target": "disabled", "reason": "probe"}),
    ("POST", f"/api/v1/recovery/intents/{uuid.uuid4()}/broker-statement", {"statement": "not_sent", "reason": "probe"}),
    ("POST", f"/api/v1/execution-operations/{uuid.uuid4()}/end", {"reason": "probe"}),
    ("GET", "/api/v1/strategies", None),
    ("GET", "/api/v1/system", None),
    ("GET", "/api/v1/operations/orders", None),
    ("GET", "/api/v1/market-data/calendar-state", None),
]


def _routes(app) -> dict[tuple[str, str], object]:
    out: dict[tuple[str, str], object] = {}
    for route in app.routes:
        candidates = route.effective_candidates() if hasattr(route, "effective_candidates") else [route]
        for candidate in candidates:
            path = str(getattr(candidate, "path", ""))
            if not path.startswith("/api/") and path not in ("/health", "/ready"):
                continue  # /docs, /openapi.json, /redoc
            for method in set(getattr(candidate, "methods", set()) or set()) - {"HEAD"}:
                out[(method, path)] = candidate
    return out


def _guarded(candidate) -> bool:
    dependant = getattr(candidate, "dependant", None)
    return dependant is not None and any(d.call is require_mutations_enabled for d in dependant.dependencies)


# ---------------------------------------------------------------------------
# Route inventories
# ---------------------------------------------------------------------------


def test_research_mode_route_inventory_is_exactly_research_plus_infrastructure() -> None:
    routes = _routes(api_app.create_app(research_mode=True))
    assert set(routes) == INFRASTRUCTURE_ROUTES | RESEARCH_ROUTES  # 12 infrastructure + 41 research routes
    for key in RESEARCH_MUTATING_ROUTES:
        assert _guarded(routes[key]), f"{key} is missing require_mutations_enabled"
    for key in RESEARCH_READ_ONLY_POSTS:
        assert not _guarded(routes[key])
    for key in TRADING_MUTATING_ROUTES - INFRASTRUCTURE_ROUTES:
        assert key not in routes


def test_trading_mode_route_inventory_is_unchanged() -> None:
    clear_settings_cache()
    assert load_settings().research.mode is False
    default_routes = _routes(api_app.create_app())
    explicit_routes = _routes(api_app.create_app(research_mode=False))
    assert set(default_routes) == set(explicit_routes)
    assert not [p for _, p in default_routes if p.startswith("/api/v1/research")]
    mutating = {k for k in default_routes if k[0] != "GET"}
    assert mutating == TRADING_MUTATING_ROUTES
    assert INFRASTRUCTURE_ROUTES <= set(default_routes)
    assert ("PUT", "/api/v1/controls/kill-switch") in default_routes


def test_research_mode_is_selected_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRADING_PLATFORM_RESEARCH__MODE", "true")
    clear_settings_cache()
    routes = _routes(api_app.create_app())
    assert set(routes) == INFRASTRUCTURE_ROUTES | RESEARCH_ROUTES


def test_research_package_declares_exactly_the_research_mutations() -> None:
    """AST pin of ``api/research``, the research counterpart of the trading allowlist pin
    in tests/test_orchestration_boundaries.py (which scans ``api/routes`` only)."""

    import ast
    from pathlib import Path

    package = Path(api_app.__file__).resolve().parent / "research"
    found: set[tuple[str, str]] = set()
    for path in package.glob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        prefixes: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and getattr(node.value.func, "id", "") == "APIRouter":
                prefix = next((k.value.value for k in node.value.keywords if k.arg == "prefix"), "")
                for target in node.targets:
                    prefixes[getattr(target, "id", "")] = prefix
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute):
                    if decorator.func.attr in {"post", "put", "patch", "delete"}:
                        router_name = getattr(decorator.func.value, "id", "")
                        found.add((decorator.func.attr.upper(), prefixes[router_name] + decorator.args[0].value))
    assert found == RESEARCH_MUTATING_ROUTES | RESEARCH_READ_ONLY_POSTS


# ---------------------------------------------------------------------------
# Behaviour on a research process
# ---------------------------------------------------------------------------


@pytest.fixture()
def research_app_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    with migrated_database(monkeypatch, "research_api") as name:
        monkeypatch.setenv("TRADING_PLATFORM_RESEARCH__MODE", "true")
        monkeypatch.setenv("TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED", "true")
        clear_settings_cache()
        settings = load_settings()
        monkeypatch.setattr(api_app, "enforce_startup_config", lambda **kwargs: settings)
        yield name


class _Spy:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError(f"{self.name} must not be reached from a research process")


@pytest.fixture()
def trading_spies(monkeypatch: pytest.MonkeyPatch) -> dict[str, _Spy]:
    """Trading services and the broker client fail loudly if anything reaches them."""

    import trading_platform.services.alpaca as alpaca
    import trading_platform.services.execution.submit_orders as submit_orders
    import trading_platform.services.operator_controls as operator_controls

    spies = {
        "operator_controls": _Spy("OperatorControlService"),
        "paper_session": _Spy("run_paper_session"),
        "broker": _Spy("AlpacaClient"),
    }
    monkeypatch.setattr(operator_controls.OperatorControlService, "__init__", spies["operator_controls"])
    monkeypatch.setattr(submit_orders, "run_paper_session", spies["paper_session"])
    monkeypatch.setattr(alpaca.AlpacaClient, "__init__", spies["broker"])
    return spies


def _count(table: str) -> int:
    with session_scope(load_settings()) as session:
        return int(session.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one())


@pytest.mark.parametrize(("method", "path", "body"), TRADING_PROBES)
def test_trading_routes_do_not_exist_on_a_research_process(
    research_app_db: str, trading_spies: dict[str, _Spy], method: str, path: str, body
) -> None:
    before = (_count("jobs"), _count("system_controls"), _count("active_paper_strategy"), _count("execution_operations"))
    with TestClient(api_app.create_app()) as client:
        response = client.request(method, path, json=body, headers={"Idempotency-Key": str(uuid.uuid4())})
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}  # Starlette route miss: no handler ran
    assert all(spy.calls == 0 for spy in trading_spies.values())
    assert (_count("jobs"), _count("system_controls"), _count("active_paper_strategy"), _count("execution_operations")) == before


@pytest.mark.parametrize(
    "job_type",
    ["paper-session", "broker-order-sync", "reconciliation", "risk-evaluation", "record-external-activity", "ingest-bars", "backtest"],
)
def test_trading_job_types_cannot_be_enqueued_on_a_research_process(
    research_app_db: str, trading_spies: dict[str, _Spy], job_type: str
) -> None:
    with TestClient(api_app.create_app()) as client:
        catalog = client.get("/api/v1/job-types").json()
        assert sorted(i["job_type"] for i in catalog["items"]) == sorted(RESEARCH_JOB_TYPES)
        response = client.post(
            "/api/v1/jobs",
            json={"job_type": job_type, "payload": {}},
            headers={"Idempotency-Key": str(uuid.uuid4())},
        )
    assert response.status_code == 422
    assert response.json()["detail"] == {"code": "unknown_job_type", "job_type": job_type}
    assert _count("jobs") == 0 and _count("job_mutations") == 0
    assert all(spy.calls == 0 for spy in trading_spies.values())


def test_research_job_type_is_submittable_and_health_serves(research_app_db: str, trading_spies: dict[str, _Spy]) -> None:
    with TestClient(api_app.create_app()) as client:
        assert client.get("/health").status_code == 200
        response = client.post(
            "/api/v1/jobs",
            json={"job_type": "catalog-sync", "payload": {"include_names": False}},
            headers={"Idempotency-Key": str(uuid.uuid4())},
        )
        assert response.status_code == 202, response.json()
        listed = client.get("/api/v1/jobs").json()
    assert listed["count"] == 1 and listed["items"][0]["job_type"] == "catalog-sync"
    assert all(spy.calls == 0 for spy in trading_spies.values())


def test_lifespan_refuses_a_mode_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Routers chosen for one mode never serve under settings of the other."""

    clear_settings_cache()
    settings = load_settings()
    assert settings.research.mode is False
    monkeypatch.setattr(api_app, "enforce_startup_config", lambda **kwargs: settings)
    app = api_app.create_app(research_mode=True)  # research routers, trading settings
    with pytest.raises(RuntimeError, match="research.mode changed"):
        with TestClient(app):
            pass


def test_research_mutations_are_guarded_when_mutations_are_disabled(
    research_app_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED", "false")
    clear_settings_cache()
    settings = load_settings()
    monkeypatch.setattr(api_app, "enforce_startup_config", lambda **kwargs: settings)
    with TestClient(api_app.create_app()) as client:
        for method, path in sorted(RESEARCH_MUTATING_ROUTES):
            concrete = path.replace("{draft_id}", str(uuid.uuid4())).replace("{version_id}", str(uuid.uuid4())).replace("{study_id}", str(uuid.uuid4())).replace("{revision_id}", str(uuid.uuid4())).replace("{list_id}", str(uuid.uuid4()))
            response = client.request(method, concrete, json={"not": "valid"})
            assert response.status_code == 403, (method, path)
            assert response.json()["detail"] == {"code": "mutations_disabled"}
        # The read-only POST is not a mutation and keeps working.
        ok = client.post("/api/v1/research/strategies/validate", json={"yaml_text": "spec_version: 1"})
        assert ok.status_code == 200 and ok.json()["validation"]["valid"] is False
    assert _count("strategy_drafts") == 0 and _count("strategy_versions") == 0
