"""ORCH-06: the read-only job-type catalog, ``GET /api/v1/job-types``.

Covers D-20 (response shape + mutation-capability flag), D-23 (minimal
item keys, no execution-mode leakage), the ORCH-06 enforcement invariant
(every default-registry type resolves a submission spec and appears in
the catalog), catalog resilience when ``submission_defaults()`` is
unavailable, and the registry's registration-time metadata contract
(closed ``JobCancellationMode`` enum, mandatory description/cancellation
mode/submission_defaults).

These tests build the FastAPI app directly with an in-memory
``JobRegistry`` and inject ``app.state.settings`` without entering the
app's lifespan context manager -- no PostgreSQL is required for this
route (it never touches the database).
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from trading_platform.api.app import create_app
from trading_platform.core.settings import Settings
from trading_platform.jobs.contracts import JobContext
from trading_platform.jobs.registry import (
    JobCancellationMode,
    JobRegistry,
    build_default_registry,
)

_ROOT = Path(__file__).resolve().parents[1]


class _ProbeHandler:
    job_type = "catalog_probe"

    def run(self, context: JobContext) -> Mapping[str, Any]:
        return {"message": "done"}


class _RunnerOnlyHandler:
    """A handler with no public submission spec -- not publicly submittable."""

    job_type = "catalog_runner_only"

    def run(self, context: JobContext) -> Mapping[str, Any]:
        return {"message": "done"}


class _ProbeSubmissionSpec:
    job_type = _ProbeHandler.job_type
    description = "Catalog probe submission spec."
    cancellation_mode = JobCancellationMode.STEP_BOUNDARY

    def __init__(self, defaults: Mapping[str, Any] | None = None) -> None:
        self._defaults = defaults

    def validate_payload(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return dict(payload)

    def submission_defaults(self) -> Mapping[str, Any] | None:
        return self._defaults


class _RaisingSubmissionSpec:
    job_type = _ProbeHandler.job_type
    description = "Catalog probe submission spec (raising defaults)."
    cancellation_mode = JobCancellationMode.STEP_BOUNDARY

    def validate_payload(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return dict(payload)

    def submission_defaults(self) -> Mapping[str, Any] | None:
        raise RuntimeError("defaults unavailable (e.g. DB unreachable)")


def _build_client(registry: JobRegistry, *, mutations_enabled: bool) -> TestClient:
    app = create_app(job_registry=registry)
    app.state.settings = Settings.model_validate(
        {"orchestration": {"mutations_enabled": mutations_enabled}}
    )
    return TestClient(app)


def test_cancellation_mode_enum_is_closed() -> None:
    assert {mode.value for mode in JobCancellationMode} == {"step_boundary", "queued_only"}


@pytest.mark.parametrize("mutations_enabled", [True, False])
def test_catalog_shape_and_mutation_flag(mutations_enabled: bool) -> None:
    registry = JobRegistry()
    registry.register(
        _ProbeHandler(),
        submission_spec=_ProbeSubmissionSpec(defaults={"from_date": "2024-01-02"}),
    )

    client = _build_client(registry, mutations_enabled=mutations_enabled)
    response = client.get("/api/v1/job-types")

    assert response.status_code == 200
    assert response.json() == {
        "mutations_enabled": mutations_enabled,
        "items": [
            {
                "job_type": "catalog_probe",
                "description": "Catalog probe submission spec.",
                "cancellation_mode": "step_boundary",
                "submission_defaults": {"from_date": "2024-01-02"},
            }
        ],
    }


def test_catalog_item_keys_are_minimal() -> None:
    """D-23: no execution-mode key or other extra field is ever exposed."""

    registry = JobRegistry()
    registry.register(
        _ProbeHandler(),
        submission_spec=_ProbeSubmissionSpec(defaults={"from_date": "2024-01-02"}),
    )

    client = _build_client(registry, mutations_enabled=True)
    response = client.get("/api/v1/job-types")

    assert response.status_code == 200
    allowed_keys = {"job_type", "description", "cancellation_mode", "submission_defaults"}
    for item in response.json()["items"]:
        assert set(item).issubset(allowed_keys)


def test_catalog_omits_defaults_when_none_or_raising() -> None:
    registry = JobRegistry()
    registry.register(_ProbeHandler(), submission_spec=_ProbeSubmissionSpec(defaults=None))

    other_handler = _RunnerOnlyHandler()
    # Reuse _RunnerOnlyHandler's job_type for a second, raising spec.
    raising_spec = _RaisingSubmissionSpec()
    raising_spec.job_type = other_handler.job_type
    registry.register(other_handler, submission_spec=raising_spec)

    client = _build_client(registry, mutations_enabled=True)
    response = client.get("/api/v1/job-types")

    assert response.status_code == 200
    items = {item["job_type"]: item for item in response.json()["items"]}
    assert set(items) == {"catalog_probe", "catalog_runner_only"}
    for item in items.values():
        assert "submission_defaults" not in item


def test_catalog_skips_runner_only_types() -> None:
    registry = JobRegistry()
    registry.register(_ProbeHandler(), submission_spec=_ProbeSubmissionSpec())
    registry.register(_RunnerOnlyHandler())  # no submission spec

    client = _build_client(registry, mutations_enabled=True)
    response = client.get("/api/v1/job-types")

    assert response.status_code == 200
    job_types = {item["job_type"] for item in response.json()["items"]}
    assert job_types == {"catalog_probe"}


def test_default_registry_types_all_appear_in_catalog() -> None:
    """ORCH-06 enforcement: every default-registry type resolves a submission
    spec and is present in the catalog. The default registry is empty until a
    later Phase 19 plan registers concrete operation types -- this test still
    proves the invariant holds (vacuously, today; non-vacuously once
    `build_default_registry` registers a type)."""

    settings = Settings()
    registry = build_default_registry(settings)

    for job_type in registry.list_job_types():
        registry.resolve_submission_spec(job_type)  # must not raise

    client = _build_client(registry, mutations_enabled=True)
    response = client.get("/api/v1/job-types")

    assert response.status_code == 200
    catalog_types = {item["job_type"] for item in response.json()["items"]}
    assert catalog_types == set(registry.list_job_types())


def test_register_rejects_incomplete_catalog_metadata() -> None:
    class _BlankDescriptionSpec:
        job_type = _ProbeHandler.job_type
        description = "   "
        cancellation_mode = JobCancellationMode.STEP_BOUNDARY

        def validate_payload(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
            return dict(payload)

        def submission_defaults(self) -> None:
            return None

    class _StringCancellationModeSpec:
        job_type = _ProbeHandler.job_type
        description = "Valid description."
        cancellation_mode = "step_boundary"  # plain str, not a JobCancellationMode member

        def validate_payload(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
            return dict(payload)

        def submission_defaults(self) -> None:
            return None

    class _MissingSubmissionDefaultsSpec:
        job_type = _ProbeHandler.job_type
        description = "Valid description."
        cancellation_mode = JobCancellationMode.STEP_BOUNDARY

        def validate_payload(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
            return dict(payload)

    for bad_spec in (
        _BlankDescriptionSpec(),
        _StringCancellationModeSpec(),
        _MissingSubmissionDefaultsSpec(),
    ):
        registry = JobRegistry()
        with pytest.raises(ValueError):
            registry.register(_ProbeHandler(), submission_spec=bad_spec)
        assert registry.list_job_types() == []


def test_job_types_route_is_separate_router() -> None:
    app = create_app(job_registry=JobRegistry())
    routes_by_path: dict[str, set[str]] = {}
    for route in app.routes:
        candidates = (
            route.effective_candidates() if hasattr(route, "effective_candidates") else [route]
        )
        for candidate in candidates:
            path = str(getattr(candidate, "path", ""))
            methods = set(getattr(candidate, "methods", set()))
            if methods:
                routes_by_path.setdefault(path, set()).update(methods)

    assert routes_by_path.get("/api/v1/job-types") == {"GET"}
    assert "/api/v1/jobs/job-types" not in routes_by_path


def test_job_types_route_imports_no_domain_layers() -> None:
    path = _ROOT / "src/trading_platform/api/routes/job_types.py"
    tree = ast.parse(path.read_text(), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            imports.add(node.module or "")

    forbidden_prefixes = ("trading_platform.services", "trading_platform.db", "sqlalchemy")
    assert not any(
        module == forbidden or module.startswith(f"{forbidden}.")
        for module in imports
        for forbidden in forbidden_prefixes
    )
