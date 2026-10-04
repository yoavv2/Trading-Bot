"""ORCH-07 / D-19 mutation guard: disabled-by-default, typed 403, zero rows.

Covers the model-level default, the HTTP-layer 403 for submit and cancel,
guard-before-idempotency/schema ordering, the enabled happy path, and a
route-walk proving every mutating route (present or future) carries the
guard dependency.
"""

from __future__ import annotations

import os
import sys
import uuid
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import psycopg
import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.migrate import build_alembic_config  # noqa: E402

from trading_platform.api.app import create_app  # noqa: E402
from trading_platform.api.dependencies import require_mutations_enabled  # noqa: E402
from trading_platform.core.settings import (  # noqa: E402
    OrchestrationSettings,
    Settings,
    clear_settings_cache,
    load_settings,
)
from trading_platform.db.models import (  # noqa: E402
    Job,
    JobEvent,
    JobMutation,
    JobStatus,
    StrategyRun,
)
from trading_platform.db.session import clear_engine_cache, session_scope  # noqa: E402
from trading_platform.jobs.contracts import JobContext  # noqa: E402
from trading_platform.jobs.registry import (  # noqa: E402
    InvalidJobPayloadError,
    JobCancellationMode,
    JobRegistry,
)


class _GuardProbeHandler:
    job_type = "mutation_guard_probe"

    def run(self, context: JobContext) -> Mapping[str, Any]:
        return {"message": "done"}


class _GuardProbeSubmissionSpec:
    job_type = "mutation_guard_probe"
    description = "Mutation guard probe submission spec."
    cancellation_mode = JobCancellationMode.STEP_BOUNDARY

    def validate_payload(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        if payload != {"message": "hello"}:
            raise InvalidJobPayloadError(job_type=self.job_type, reason="message must be hello")
        return {"message": "hello"}

    def submission_defaults(self) -> None:
        return None


def _registry() -> JobRegistry:
    registry = JobRegistry()
    registry.register(_GuardProbeHandler(), submission_spec=_GuardProbeSubmissionSpec())
    return registry


def _admin_connection_settings() -> dict[str, str]:
    return {
        "host": os.getenv("TRADING_PLATFORM_DATABASE__HOST", "localhost"),
        "port": os.getenv("TRADING_PLATFORM_DATABASE__PORT", "5432"),
        "user": os.getenv("TRADING_PLATFORM_DATABASE__USER", "trading_platform"),
        "password": os.getenv("TRADING_PLATFORM_DATABASE__PASSWORD", "trading_platform"),
        "dbname": os.getenv("TRADING_PLATFORM_ADMIN_DB", "postgres"),
    }


def _connect_admin(params: dict[str, str] | None = None) -> psycopg.Connection:
    return psycopg.connect(**(params or _admin_connection_settings()), autocommit=True)


def _set_database_env(monkeypatch: pytest.MonkeyPatch, database_name: str) -> None:
    for key, value in _admin_connection_settings().items():
        if key != "dbname":
            monkeypatch.setenv(f"TRADING_PLATFORM_DATABASE__{key.upper()}", value)
    monkeypatch.setenv("TRADING_PLATFORM_DATABASE__NAME", database_name)


@pytest.fixture()
def migrated_mutation_guard_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    database_name = f"mutation_guard_{uuid.uuid4().hex[:8]}"
    admin_params = _admin_connection_settings()
    try:
        with _connect_admin(admin_params) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f'CREATE DATABASE "{database_name}"')
    except psycopg.Error as exc:  # pragma: no cover
        pytest.fail(f"PostgreSQL is required for mutation guard tests: {exc}")

    _set_database_env(monkeypatch, database_name)
    clear_settings_cache()
    clear_engine_cache()
    command.upgrade(build_alembic_config(), "head")
    try:
        yield database_name
    finally:
        clear_settings_cache()
        clear_engine_cache()
        with _connect_admin(admin_params) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT pg_terminate_backend(pid)
                    FROM pg_stat_activity
                    WHERE datname = %s AND usename = current_user AND pid <> pg_backend_pid()
                    """,
                    (database_name,),
                )
                cursor.execute(f'DROP DATABASE IF EXISTS "{database_name}"')


def _counts() -> tuple[int, int, int, int]:
    with session_scope(load_settings()) as session:
        return (
            session.scalar(select(func.count()).select_from(Job)) or 0,
            session.scalar(select(func.count()).select_from(JobMutation)) or 0,
            session.scalar(select(func.count()).select_from(JobEvent)) or 0,
            session.scalar(select(func.count()).select_from(StrategyRun)) or 0,
        )


def _seed_job(*, status: JobStatus) -> Job:
    with session_scope(load_settings()) as session:
        job = Job(job_type=_GuardProbeHandler.job_type, payload={"message": "hello"}, status=status)
        session.add(job)
        session.flush()
        session.expunge(job)
        return job


def test_orchestration_flag_model_default_is_disabled() -> None:
    assert OrchestrationSettings().mutations_enabled is False
    assert Settings().orchestration.mutations_enabled is False


def test_submit_rejected_when_mutations_disabled(
    migrated_mutation_guard_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED", "false")
    clear_settings_cache()
    app = create_app()
    app.state.job_registry = _registry()
    before = _counts()

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/jobs",
            headers={"Idempotency-Key": "guard-submit-disabled"},
            json={"job_type": _GuardProbeHandler.job_type, "payload": {"message": "hello"}},
        )

    assert response.status_code == 403
    assert response.json()["detail"] == {"code": "mutations_disabled"}
    assert _counts() == before


def test_cancel_rejected_when_mutations_disabled(
    migrated_mutation_guard_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Seed with mutations enabled so the QUEUED job exists independent of the guard,
    # then flip the flag off for the cancel attempt under test.
    monkeypatch.setenv("TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED", "true")
    clear_settings_cache()
    queued = _seed_job(status=JobStatus.QUEUED)

    monkeypatch.setenv("TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED", "false")
    clear_settings_cache()
    app = create_app()
    app.state.job_registry = _registry()
    before = _counts()

    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/jobs/{queued.id}/cancel",
            headers={"Idempotency-Key": "guard-cancel-disabled"},
            json={"reason": "attempted stop"},
        )

    assert response.status_code == 403
    assert response.json()["detail"] == {"code": "mutations_disabled"}
    assert _counts() == before
    with session_scope(load_settings()) as session:
        persisted = session.get(Job, queued.id)
        assert persisted is not None
        assert persisted.status == JobStatus.QUEUED
        assert persisted.cancellation_reason is None


def test_disabled_guard_precedes_idempotency_and_schema_validation(
    migrated_mutation_guard_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED", "false")
    clear_settings_cache()
    app = create_app()
    app.state.job_registry = _registry()

    with TestClient(app) as client:
        no_key = client.post(
            "/api/v1/jobs",
            json={"job_type": _GuardProbeHandler.job_type, "payload": {"message": "hello"}},
        )
        assert no_key.status_code == 403
        assert no_key.json()["detail"] == {"code": "mutations_disabled"}

        schema_invalid = client.post(
            "/api/v1/jobs",
            headers={"Idempotency-Key": "guard-schema-invalid"},
            json={},
        )
        assert schema_invalid.status_code == 403
        assert schema_invalid.json()["detail"] == {"code": "mutations_disabled"}

        cancel_bad_path = client.post(
            "/api/v1/jobs/not-a-uuid/cancel",
            headers={"Idempotency-Key": "guard-cancel-bad-path"},
            json={},
        )
        assert cancel_bad_path.status_code == 403
        assert cancel_bad_path.json()["detail"] == {"code": "mutations_disabled"}


def test_submit_allowed_when_mutations_enabled(
    migrated_mutation_guard_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED", "true")
    clear_settings_cache()
    app = create_app()
    app.state.job_registry = _registry()

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/jobs",
            headers={"Idempotency-Key": "guard-submit-enabled"},
            json={"job_type": _GuardProbeHandler.job_type, "payload": {"message": "hello"}},
        )

    assert response.status_code == 202


def test_every_mutating_route_requires_mutation_guard() -> None:
    # FastAPI 0.131 wraps included routers as _IncludedRouter placeholders on
    # app.routes; effective_candidates() resolves the actual routed methods
    # and each candidate's dependant, mirroring the pattern
    # tests/test_orchestration_boundaries.py::_effective_routes() already
    # established for this same FastAPI version.
    app = create_app()
    checked = 0
    for route in app.routes:
        candidates = (
            route.effective_candidates() if hasattr(route, "effective_candidates") else [route]
        )
        for candidate in candidates:
            methods = set(getattr(candidate, "methods", set()) or set())
            mutating_methods = methods - {"GET", "HEAD"}
            if not mutating_methods:
                continue
            dependant = getattr(candidate, "dependant", None)
            if dependant is None:
                continue
            checked += 1
            guarded = any(
                dependency.call is require_mutations_enabled
                for dependency in dependant.dependencies
            )
            path = getattr(candidate, "path", "?")
            assert guarded, f"Route {methods} {path} is missing require_mutations_enabled"

    # D-12: the mutating surface is pinned to exactly eight routes (POST
    # /api/v1/jobs, /{job_id}/cancel, /{job_id}/retry, PUT
    # /api/v1/controls/kill-switch, /api/v1/controls/strategies/{id}, the REC-01
    # POST /api/v1/recovery/intents/{id}/broker-statement of 20.1-10 and the REC-02
    # POST /api/v1/execution-operations/{id}/end of 20.1-11 and the PAPER-02
    # PUT /api/v1/controls/active-paper-strategy of 20.1-12: the final Phase 20.1 surface).
    assert checked == 8


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/v1/jobs/not-a-real-job-id/retry"),
        ("PUT", "/api/v1/controls/kill-switch"),
        ("PUT", "/api/v1/controls/strategies/not-a-real-strategy-id"),
        ("PUT", "/api/v1/controls/active-paper-strategy"),
        ("POST", "/api/v1/recovery/intents/not-a-real-intent/broker-statement"),
        ("POST", "/api/v1/execution-operations/not-a-real-operation/end"),
    ],
)
def test_new_phase20_routes_rejected_when_mutations_disabled(
    migrated_mutation_guard_db: str,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
) -> None:
    """D-12: retry and both control routes are guarded exactly like submit/
    cancel -- 403 before header/path/body validation, with zero writes."""

    monkeypatch.setenv("TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED", "false")
    clear_settings_cache()
    app = create_app()
    app.state.job_registry = _registry()
    before = _counts()

    with TestClient(app) as client:
        # No Idempotency-Key header, and a malformed body -- proves the
        # guard precedes both header and body/path validation.
        response = client.request(method, path, json={"not": "a-valid-target"})

    assert response.status_code == 403
    assert response.json()["detail"] == {"code": "mutations_disabled"}
    assert _counts() == before
