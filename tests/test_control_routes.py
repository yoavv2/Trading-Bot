"""HTTP contract tests for the synchronous safety-control routes (CTRL-01/02).

Both routes call ``OperatorControlService`` directly -- no Job, no worker,
and no ``Idempotency-Key`` -- and are idempotent by target state (D-10).
"""

from __future__ import annotations

import os
import sys
import threading
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.exc import OperationalError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.migrate import build_alembic_config  # noqa: E402

from trading_platform.api.app import create_app  # noqa: E402
from trading_platform.core.settings import clear_settings_cache, load_settings  # noqa: E402
from trading_platform.db.models import (  # noqa: E402
    GLOBAL_KILL_SWITCH_NAME,
    ExecutionEvent,
    Job,
    KillSwitchState,
    Strategy,
    StrategyRun,
    StrategyRunType,
    StrategyStatus,
    SystemControl,
)
from trading_platform.db.session import (  # noqa: E402
    clear_engine_cache,
    get_session_factory,
    session_scope,
)
from trading_platform.services.bootstrap import ensure_strategy_record  # noqa: E402
from trading_platform.services.operator_controls import OperatorControlService  # noqa: E402

_KNOWN_STRATEGY_ID = "trend_following_daily"


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
def migrated_control_routes_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    database_name = f"control_routes_{uuid.uuid4().hex[:8]}"
    admin_params = _admin_connection_settings()
    try:
        with _connect_admin(admin_params) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f'CREATE DATABASE "{database_name}"')
    except psycopg.Error as exc:  # pragma: no cover
        pytest.fail(f"PostgreSQL is required for control route tests: {exc}")

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


@pytest.fixture()
def client(
    migrated_control_routes_db: str, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    # ORCH-07 / D-19: mutations default disabled; enable explicitly so this
    # file's control-route expectations stay accurate.
    monkeypatch.setenv("TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED", "true")
    clear_settings_cache()
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


def _audit_counts() -> tuple[int, int, int]:
    with session_scope(load_settings()) as session:
        return (
            session.scalar(
                select(func.count())
                .select_from(StrategyRun)
                .where(StrategyRun.run_type == StrategyRunType.OPERATOR_CONTROL)
            )
            or 0,
            session.scalar(select(func.count()).select_from(ExecutionEvent)) or 0,
            session.scalar(select(func.count()).select_from(Job)) or 0,
        )


def _put_kill_switch(client: TestClient, body: Any, *, raw: str | bytes | None = None) -> Any:
    if raw is not None:
        return client.put(
            "/api/v1/controls/kill-switch",
            content=raw,
            headers={"Content-Type": "application/json"},
        )
    return client.put("/api/v1/controls/kill-switch", json=body)


def _put_strategy(
    client: TestClient, strategy_id: str, body: Any, *, raw: str | bytes | None = None
) -> Any:
    if raw is not None:
        return client.put(
            f"/api/v1/controls/strategies/{strategy_id}",
            content=raw,
            headers={"Content-Type": "application/json"},
        )
    return client.put(f"/api/v1/controls/strategies/{strategy_id}", json=body)


def test_trip_kill_switch_is_idempotent_by_target_state_and_audited(client: TestClient) -> None:
    before = _audit_counts()

    tripped = _put_kill_switch(client, {"state": "tripped", "reason": "market anomaly"})
    assert tripped.status_code == 200
    body = tripped.json()
    assert body["state"] == "tripped"
    assert body["changed"] is True
    assert isinstance(body["run_id"], str)

    after_first = _audit_counts()
    assert after_first == (before[0] + 1, before[1] + 1, before[2])

    repeated = _put_kill_switch(client, {"state": "tripped", "reason": "still anomalous"})
    assert repeated.status_code == 200
    repeated_body = repeated.json()
    assert repeated_body["state"] == "tripped"
    assert repeated_body["changed"] is False

    after_second = _audit_counts()
    assert after_second == (before[0] + 2, before[1] + 2, before[2])


def test_noop_kill_switch_put_preserves_last_change_provenance(client: TestClient) -> None:
    """WR-B-01: a reaffirming PUT audits (run + event) but must not overwrite
    the state row's last_change_* provenance."""
    first = _put_kill_switch(client, {"state": "tripped", "reason": "original trip reason"})
    first_run_id = first.json()["run_id"]
    with session_scope(load_settings()) as session:
        before = session.execute(select(SystemControl)).scalar_one()
        original = (before.last_changed_at, before.last_change_actor)
    before_counts = _audit_counts()

    repeated = _put_kill_switch(client, {"state": "tripped", "reason": "double click"})
    assert repeated.json()["changed"] is False

    assert _audit_counts() == (before_counts[0] + 1, before_counts[1] + 1, before_counts[2])
    with session_scope(load_settings()) as session:
        control = session.execute(select(SystemControl)).scalar_one()
        assert control.state == KillSwitchState.TRIPPED
        assert control.last_change_reason == "original trip reason"
        assert str(control.last_change_run_id) == first_run_id
        assert (control.last_changed_at, control.last_change_actor) == original

    state = client.get("/api/v1/system/kill-switch").json()
    assert state["last_change_reason"] == "original trip reason"
    assert state["last_change_run_id"] == first_run_id


def test_reset_kill_switch_returns_armed(client: TestClient) -> None:
    _put_kill_switch(client, {"state": "tripped", "reason": "trip before reset"})

    reset = _put_kill_switch(client, {"state": "armed", "reason": "all clear"})
    assert reset.status_code == 200
    assert reset.json()["state"] == "armed"
    assert reset.json()["changed"] is True


def test_disable_strategy_is_idempotent_by_target_state_and_audited(client: TestClient) -> None:
    # R-8: a new strategy row is created disabled, so enable it first for the
    # first disable to be a real transition.
    _put_strategy(client, _KNOWN_STRATEGY_ID, {"status": "enabled", "reason": "arrange"})
    before = _audit_counts()

    disabled = _put_strategy(client, _KNOWN_STRATEGY_ID, {"status": "disabled", "reason": "maintenance"})
    assert disabled.status_code == 200
    body = disabled.json()
    assert body == {
        "strategy_id": _KNOWN_STRATEGY_ID,
        "status": "disabled",
        "changed": True,
        "run_id": body["run_id"],
    }

    after_first = _audit_counts()
    assert after_first == (before[0] + 1, before[1] + 1, before[2])

    repeated = _put_strategy(client, _KNOWN_STRATEGY_ID, {"status": "disabled", "reason": "still down"})
    assert repeated.status_code == 200
    assert repeated.json()["changed"] is False

    after_second = _audit_counts()
    assert after_second == (before[0] + 2, before[1] + 2, before[2])


def test_enable_strategy_after_disable_is_idempotent_by_target_state_and_audited(
    client: TestClient,
) -> None:
    _put_strategy(client, _KNOWN_STRATEGY_ID, {"status": "disabled", "reason": "maintenance"})
    before = _audit_counts()

    enabled = _put_strategy(client, _KNOWN_STRATEGY_ID, {"status": "enabled", "reason": "all clear"})
    assert enabled.status_code == 200
    body = enabled.json()
    assert body == {
        "strategy_id": _KNOWN_STRATEGY_ID,
        "status": "enabled",
        "changed": True,
        "run_id": body["run_id"],
    }

    after_first = _audit_counts()
    assert after_first == (before[0] + 1, before[1] + 1, before[2])

    repeated = _put_strategy(client, _KNOWN_STRATEGY_ID, {"status": "enabled", "reason": "still up"})
    assert repeated.status_code == 200
    assert repeated.json()["changed"] is False

    after_second = _audit_counts()
    assert after_second == (before[0] + 2, before[1] + 2, before[2])


def test_set_strategy_status_unknown_strategy_returns_404_with_zero_writes(
    client: TestClient,
) -> None:
    before = _audit_counts()
    response = _put_strategy(client, "not_a_real_strategy", {"status": "disabled", "reason": "x"})

    assert response.status_code == 404
    assert response.json()["detail"] == {
        "code": "strategy_not_found",
        "strategy_id": "not_a_real_strategy",
    }
    assert _audit_counts() == before


@pytest.mark.parametrize(
    ("body", "raw", "expected_code"),
    [
        ({}, None, "invalid_control_target"),
        ({"state": "on", "reason": "x"}, None, "invalid_control_target"),
        ({"state": ["tripped"], "reason": "x"}, None, "invalid_control_target"),
        ({"state": {}, "reason": "x"}, None, "invalid_control_target"),
        ({"state": "tripped"}, None, "invalid_control_reason"),
        ({"state": "tripped", "reason": "   "}, None, "invalid_control_reason"),
        ({"state": "tripped", "reason": "x" * 501}, None, "invalid_control_reason"),
        ({"state": "tripped", "reason": "x", "extra": 1}, None, "invalid_control_request"),
        (None, "[]", "invalid_control_request"),
        (None, "not json", "invalid_control_request"),
        (None, b"\xff\xfe not valid utf-8", "invalid_control_request"),
    ],
)
def test_kill_switch_rejections_are_typed_dicts_with_zero_writes(
    client: TestClient, body: Any, raw: str | bytes | None, expected_code: str
) -> None:
    before = _audit_counts()
    response = _put_kill_switch(client, body, raw=raw)

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, dict)
    assert detail["code"] == expected_code
    assert _audit_counts() == before


@pytest.mark.parametrize(
    ("body", "raw", "expected_code"),
    [
        ({}, None, "invalid_control_target"),
        ({"status": "on", "reason": "x"}, None, "invalid_control_target"),
        ({"status": ["disabled"], "reason": "x"}, None, "invalid_control_target"),
        ({"status": {}, "reason": "x"}, None, "invalid_control_target"),
        ({"status": "disabled"}, None, "invalid_control_reason"),
        ({"status": "disabled", "reason": "   "}, None, "invalid_control_reason"),
        ({"status": "disabled", "reason": "x" * 501}, None, "invalid_control_reason"),
        ({"status": "disabled", "reason": "x", "extra": 1}, None, "invalid_control_request"),
        (None, "[]", "invalid_control_request"),
        (None, "not json", "invalid_control_request"),
        (None, b"\xff\xfe not valid utf-8", "invalid_control_request"),
    ],
)
def test_strategy_status_rejections_are_typed_dicts_with_zero_writes(
    client: TestClient, body: Any, raw: str | bytes | None, expected_code: str
) -> None:
    before = _audit_counts()
    response = _put_strategy(client, _KNOWN_STRATEGY_ID, body, raw=raw)

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, dict)
    assert detail["code"] == expected_code
    assert _audit_counts() == before


def test_control_routes_ignore_idempotency_key_header(client: TestClient) -> None:
    response = client.put(
        "/api/v1/controls/kill-switch",
        headers={"Idempotency-Key": "unused-key"},
        json={"state": "tripped", "reason": "present but ignored"},
    )
    assert response.status_code == 200
    assert "Idempotency-Replayed" not in response.headers


def test_get_strategy_control_status_reflects_db_state_not_config_flag(
    client: TestClient,
) -> None:
    config_view = client.get(f"/api/v1/strategies/{_KNOWN_STRATEGY_ID}")
    assert config_view.status_code == 200
    assert config_view.json()["strategy"]["enabled"] is True

    control_view_before = client.get(f"/api/v1/controls/strategies/{_KNOWN_STRATEGY_ID}")
    assert control_view_before.status_code == 200
    # R-8: a strategy with no DB row reports `disabled` (never auto-enabled).
    assert control_view_before.json()["status"] == "disabled"
    assert control_view_before.json()["updated_at"] is None

    _put_strategy(client, _KNOWN_STRATEGY_ID, {"status": "enabled", "reason": "control view proof"})

    control_view_after = client.get(f"/api/v1/controls/strategies/{_KNOWN_STRATEGY_ID}")
    assert control_view_after.status_code == 200
    assert control_view_after.json()["status"] == "enabled"
    assert control_view_after.json()["updated_at"] is not None

    still_config_enabled = client.get(f"/api/v1/strategies/{_KNOWN_STRATEGY_ID}")
    assert still_config_enabled.json()["strategy"]["enabled"] is True


def test_get_strategy_control_status_unknown_strategy_returns_404(client: TestClient) -> None:
    response = client.get("/api/v1/controls/strategies/not_a_real_strategy")
    assert response.status_code == 404
    assert response.json()["detail"] == {
        "code": "strategy_not_found",
        "strategy_id": "not_a_real_strategy",
    }


def test_get_strategy_control_status_performs_zero_writes(client: TestClient) -> None:
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    writes: list[str] = []

    def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        stripped = statement.lstrip()
        first_token = stripped.split(None, 1)[0].upper() if stripped else ""
        if first_token in {"INSERT", "UPDATE", "DELETE"}:
            writes.append(statement)

    event.listen(Engine, "before_cursor_execute", _before_cursor_execute)
    try:
        response = client.get(f"/api/v1/controls/strategies/{_KNOWN_STRATEGY_ID}")
    finally:
        event.remove(Engine, "before_cursor_execute", _before_cursor_execute)

    assert response.status_code == 200
    assert response.json()["updated_at"] is None
    assert writes == []


# ---------------------------------------------------------------------------
# WR-B-02: row locking and the first-use ensure race
# ---------------------------------------------------------------------------


def _run_in_thread(target: Any) -> tuple[threading.Thread, dict[str, Any]]:
    outcome: dict[str, Any] = {}

    def runner() -> None:
        try:
            outcome["result"] = target()
        except BaseException as exc:  # noqa: BLE001 - surfaced to the test
            outcome["error"] = exc

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    return thread, outcome


def test_kill_switch_mutator_serializes_on_the_control_row(client: TestClient) -> None:
    """WR-B-02: a concurrent mutator waits for the row lock and then reports
    `changed: False` against the state the other writer committed."""
    settings = load_settings()
    service = OperatorControlService(settings=settings)
    holder = get_session_factory(settings)()
    try:
        control = holder.execute(
            select(SystemControl)
            .where(SystemControl.name == GLOBAL_KILL_SWITCH_NAME)
            .with_for_update()
        ).scalar_one()
        control.state = KillSwitchState.TRIPPED
        holder.flush()

        thread, outcome = _run_in_thread(
            lambda: service.trip_kill_switch(reason="race", actor="pytest", trigger_source="pytest")
        )
        thread.join(timeout=1.0)
        assert thread.is_alive(), "mutator must block on the locked control row"
        holder.commit()
    finally:
        holder.close()
    thread.join(timeout=15)

    assert "error" not in outcome, outcome.get("error")
    report = outcome["result"]
    assert report.previous_state == "tripped"
    assert report.changed is False


def test_strategy_mutator_serializes_on_the_strategy_row(client: TestClient) -> None:
    settings = load_settings()
    service = OperatorControlService(settings=settings)
    service.ensure_strategy_state(_KNOWN_STRATEGY_ID)
    holder = get_session_factory(settings)()
    try:
        record = holder.execute(
            select(Strategy)
            .where(Strategy.strategy_id == _KNOWN_STRATEGY_ID)
            .with_for_update()
        ).scalar_one()
        record.status = StrategyStatus.DISABLED
        holder.flush()

        thread, outcome = _run_in_thread(
            lambda: service.disable_strategy(
                _KNOWN_STRATEGY_ID, reason="race", actor="pytest", trigger_source="pytest"
            )
        )
        thread.join(timeout=1.0)
        assert thread.is_alive(), "mutator must block on the locked strategy row"
        holder.commit()
    finally:
        holder.close()
    thread.join(timeout=15)

    assert "error" not in outcome, outcome.get("error")
    report = outcome["result"]
    assert report.previous_status == "disabled"
    assert report.changed is False


def test_concurrent_first_use_strategy_ensure_does_not_raise(client: TestClient) -> None:
    """WR-B-02: losing the first-use INSERT race must be recovered (no
    IntegrityError, no 500), then act on the winner's committed row."""
    settings = load_settings()
    service = OperatorControlService(settings=settings)
    metadata = service.registry.resolve(_KNOWN_STRATEGY_ID).metadata
    with session_scope(settings) as session:
        for existing in session.execute(select(Strategy)).scalars():
            session.delete(existing)

    holder = get_session_factory(settings)()
    try:
        ensure_strategy_record(holder, metadata)  # winner: INSERT, uncommitted
        holder.flush()

        thread, outcome = _run_in_thread(
            lambda: service.enable_strategy(
                _KNOWN_STRATEGY_ID, reason="race", actor="pytest", trigger_source="pytest"
            )
        )
        thread.join(timeout=1.0)
        assert thread.is_alive(), "loser must wait on the winner's unique-key INSERT"
        holder.commit()
    finally:
        holder.close()
    thread.join(timeout=15)

    assert "error" not in outcome, outcome.get("error")
    # R-8: the winner created the row disabled, so the loser's enable is a real
    # transition on the winner's committed row.
    assert outcome["result"].changed is True
    with session_scope(settings) as session:
        assert len(session.execute(select(Strategy)).scalars().all()) == 1


# ---------------------------------------------------------------------------
# WR-B-03: archived strategies are never silently un-archived
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("target", ["enabled", "disabled"])
def test_put_on_archived_strategy_is_a_typed_409_with_zero_writes(
    client: TestClient, target: str
) -> None:
    settings = load_settings()
    OperatorControlService(settings=settings).ensure_strategy_state(_KNOWN_STRATEGY_ID)
    with session_scope(settings) as session:
        record = session.execute(select(Strategy)).scalar_one()
        record.status = StrategyStatus.ARCHIVED
    before = _audit_counts()

    response = _put_strategy(client, _KNOWN_STRATEGY_ID, {"status": target, "reason": "x"})

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "strategy_archived",
        "strategy_id": _KNOWN_STRATEGY_ID,
    }
    assert _audit_counts() == before
    with session_scope(settings) as session:
        assert session.execute(select(Strategy)).scalar_one().status == StrategyStatus.ARCHIVED


# ---------------------------------------------------------------------------
# WR-B-04: every control-route failure is a JSON body with detail.code
# ---------------------------------------------------------------------------


def test_reason_containing_nul_is_a_typed_422_with_zero_writes(client: TestClient) -> None:
    before = _audit_counts()

    kill_switch = _put_kill_switch(client, {"state": "tripped", "reason": "bad\x00reason"})
    strategy = _put_strategy(
        client, _KNOWN_STRATEGY_ID, {"status": "disabled", "reason": "bad\x00reason"}
    )

    for response in (kill_switch, strategy):
        assert response.status_code == 422
        assert response.json()["detail"] == {"code": "invalid_control_reason"}
    assert _audit_counts() == before


def test_deeply_nested_json_body_is_a_typed_422(client: TestClient) -> None:
    response = _put_kill_switch(client, None, raw="[" * 200000)

    assert response.status_code == 422
    assert response.json()["detail"] == {"code": "invalid_control_request"}


def test_missing_kill_switch_row_is_a_typed_503(client: TestClient) -> None:
    with session_scope(load_settings()) as session:
        session.execute(delete(SystemControl))

    response = _put_kill_switch(client, {"state": "tripped", "reason": "no row"})

    assert response.status_code == 503
    assert response.json()["detail"] == {"code": "control_state_unavailable"}


def _break_control_database(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*_args: Any, **_kwargs: Any) -> Any:
        raise OperationalError("UPDATE", {}, Exception("connection lost"))

    monkeypatch.setattr("trading_platform.services.operator_controls.session_scope", broken)


def test_database_errors_on_control_writes_are_typed_503s(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _break_control_database(monkeypatch)

    kill_switch = _put_kill_switch(client, {"state": "tripped", "reason": "db down"})
    strategy = _put_strategy(client, _KNOWN_STRATEGY_ID, {"status": "disabled", "reason": "db down"})

    for response in (kill_switch, strategy):
        assert response.status_code == 503
        assert response.json()["detail"] == {"code": "control_write_failed"}


def test_database_errors_on_strategy_control_read_are_a_typed_503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _break_control_database(monkeypatch)

    response = client.get(f"/api/v1/controls/strategies/{_KNOWN_STRATEGY_ID}")

    assert response.status_code == 503
    assert response.json()["detail"] == {"code": "control_state_unavailable"}


def test_unhandled_errors_on_mutation_routes_are_json_with_a_code(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr(OperatorControlService, "trip_kill_switch", explode)

    with TestClient(create_app(), raise_server_exceptions=False) as lenient:
        response = lenient.put(
            "/api/v1/controls/kill-switch", json={"state": "tripped", "reason": "boom"}
        )

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"detail": {"code": "internal_error"}}
