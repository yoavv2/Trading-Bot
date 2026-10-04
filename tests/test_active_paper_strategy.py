"""PAPER-01 unit tests: the ownership read, the shared gate loader (R-Q1), R-8
(new strategies are created disabled), the typed 409 carrier and the read-only
GET /api/v1/controls/active-paper-strategy route."""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.support.paper_ownership import (  # noqa: E402
    clear_active_paper_strategy,
    seed_registered_strategy,
    seed_strategy,
    set_active_paper_strategy,
)
from tests.support.query_counter import count_queries  # noqa: E402
from tests.test_paper_execution import migrated_paper_db  # noqa: E402, F401

from trading_platform.api.app import create_app  # noqa: E402
from trading_platform.core.settings import clear_settings_cache, load_settings  # noqa: E402
from trading_platform.db.models import (  # noqa: E402
    ActivePaperStrategy,
    KillSwitchState,
    Strategy,
    StrategyStatus,
)
from trading_platform.db.session import get_engine, session_scope  # noqa: E402
from trading_platform.jobs.registry import (  # noqa: E402
    JobCancellationMode,
    JobRegistry,
    JobSubmissionConflictError,
)
from trading_platform.services import operator_controls  # noqa: E402
from trading_platform.services.active_paper_strategy import (  # noqa: E402
    BLOCKED_REASON_NOT_ACTIVE_PAPER_STRATEGY,
    ActivePaperStrategyUnavailableError,
    OwnershipBlock,
    load_active_paper_strategy,
    ownership_block_for,
)
from trading_platform.services.bootstrap import ensure_strategy_record  # noqa: E402
from trading_platform.services.operator_controls import (  # noqa: E402
    ControlStateUnavailableError,
    ensure_strategy_control_state,
    load_kill_switch_state,
    load_strategy_control_state,
    load_trading_gate_state,
    read_trading_gate_state,
)
from trading_platform.strategies.registry import build_default_registry  # noqa: E402

TREND = "trend_following_daily"
OTHER = "donchian_breakout_daily"


def _metadata(settings, strategy_id: str):
    return build_default_registry(settings).resolve(strategy_id).metadata


@pytest.fixture()
def settings(migrated_paper_db: str):  # noqa: F811
    return load_settings()


# --- closed vocabulary -------------------------------------------------------


def test_ownership_block_is_a_closed_set() -> None:
    assert {member.value for member in OwnershipBlock} == {
        "no_active_paper_strategy",
        "strategy_not_active_paper_strategy",
    }
    assert BLOCKED_REASON_NOT_ACTIVE_PAPER_STRATEGY == "not_active_paper_strategy"
    assert BLOCKED_REASON_NOT_ACTIVE_PAPER_STRATEGY not in {m.value for m in OwnershipBlock}


# --- domain read / predicate -------------------------------------------------


def test_initial_state_has_no_owner(settings) -> None:
    state = load_active_paper_strategy(settings)
    assert state.strategy_id is None
    assert state.display_name is None
    assert state.set_by_run_id is None
    assert state.reason == "initial state: no active paper strategy"
    assert state.to_dict()["since"] == state.since.isoformat()


@pytest.mark.parametrize(
    ("owner", "expected"),
    [
        (TREND, None),
        (OTHER, OwnershipBlock.STRATEGY_NOT_ACTIVE_PAPER_STRATEGY),
        (None, OwnershipBlock.NO_ACTIVE_PAPER_STRATEGY),
    ],
    ids=["owner", "non-owner", "no-owner"],
)
def test_ownership_block_for_owner_non_owner_and_no_owner(settings, owner, expected) -> None:
    seed_registered_strategy(settings, TREND)
    seed_registered_strategy(settings, OTHER)
    set_active_paper_strategy(settings, owner)

    assert ownership_block_for(TREND, settings=settings) is expected


def test_load_active_paper_strategy_reports_owner_identity(settings) -> None:
    seed_registered_strategy(settings, TREND, owner=True)

    state = load_active_paper_strategy(settings)

    assert state.strategy_id == TREND
    assert state.display_name == _metadata(settings, TREND).display_name


def test_ownership_is_reread_every_call_never_cached(settings) -> None:
    seed_registered_strategy(settings, TREND, owner=True)
    assert ownership_block_for(TREND, settings=settings) is None

    clear_active_paper_strategy(settings)
    assert ownership_block_for(TREND, settings=settings) is OwnershipBlock.NO_ACTIVE_PAPER_STRATEGY

    seed_registered_strategy(settings, OTHER, owner=True)
    assert (
        ownership_block_for(TREND, settings=settings)
        is OwnershipBlock.STRATEGY_NOT_ACTIVE_PAPER_STRATEGY
    )


def test_ownership_block_for_issues_one_statement_per_call(settings) -> None:
    seed_registered_strategy(settings, TREND, owner=True)
    engine = get_engine(settings)

    with count_queries(engine) as counter:
        ownership_block_for(TREND, settings=settings)
    assert counter.count == 1

    with count_queries(engine) as counter:
        ownership_block_for(TREND, settings=settings)
        ownership_block_for(OTHER, settings=settings)
    assert counter.count == 2  # fresh read each time, no caching


def test_ownership_block_for_writes_nothing(settings) -> None:
    seed_registered_strategy(settings, TREND, owner=True)
    engine = get_engine(settings)

    with session_scope(settings) as session:
        with count_queries(engine) as counter:
            ownership_block_for(TREND, session=session)
        assert not (session.new or session.dirty or session.deleted)
    assert counter.count == 1
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in counter.statements)


def test_missing_singleton_row_fails_closed(settings) -> None:
    with session_scope(settings) as session:
        session.execute(text("DELETE FROM active_paper_strategy"))

    with pytest.raises(ActivePaperStrategyUnavailableError):
        load_active_paper_strategy(settings)
    with pytest.raises(ActivePaperStrategyUnavailableError):
        ownership_block_for(TREND, settings=settings)


# --- R-Q1 shared gate loader -------------------------------------------------


def test_gate_loader_reads_kill_switch_owner_and_status_in_one_statement(settings) -> None:
    seed_registered_strategy(settings, TREND, enabled=False, owner=True)
    engine = get_engine(settings)

    with session_scope(settings) as session:
        with count_queries(engine) as counter:
            gate = load_trading_gate_state(session)
    assert counter.count == 1
    assert gate.kill_switch.state == KillSwitchState.ARMED.value
    assert gate.owner.strategy_id == TREND
    assert gate.owner_status == StrategyStatus.DISABLED.value
    assert gate.strategy is None  # no strategy_id requested


def test_gate_loader_returns_requested_strategy_state_in_the_same_statement(settings) -> None:
    seed_registered_strategy(settings, TREND, enabled=True, owner=True)
    seed_registered_strategy(settings, OTHER, enabled=False)
    engine = get_engine(settings)

    with session_scope(settings) as session:
        with count_queries(engine) as counter:
            gate = load_trading_gate_state(session, strategy_id=OTHER)
    assert counter.count == 1
    assert gate.strategy is not None
    assert gate.strategy.strategy_id == OTHER
    assert gate.strategy.status == StrategyStatus.DISABLED.value
    assert gate.owner_status == StrategyStatus.ACTIVE.value


def test_gate_loader_distinguishes_missing_rows_from_no_owner(settings) -> None:
    # no owner: singleton present, strategy_id NULL
    gate = read_trading_gate_state(settings)
    assert gate.owner.strategy_id is None
    assert gate.owner_status is None

    # missing singleton -> typed, but the kill switch is still readable
    with session_scope(settings) as session:
        session.execute(text("DELETE FROM active_paper_strategy"))
    gate = read_trading_gate_state(settings)
    assert gate.kill_switch.is_tripped is False
    with pytest.raises(ActivePaperStrategyUnavailableError):
        gate.owner  # noqa: B018

    # missing kill switch row -> typed, but ownership is still readable
    with session_scope(settings) as session:
        session.execute(
            text(
                "INSERT INTO active_paper_strategy (id, reason) VALUES (1, 'restored')"
            )
        )
        session.execute(text("DELETE FROM system_controls"))
    gate = read_trading_gate_state(settings)
    assert gate.owner.strategy_id is None
    with pytest.raises(ControlStateUnavailableError):
        gate.kill_switch  # noqa: B018
    with pytest.raises(ControlStateUnavailableError):
        load_kill_switch_state(settings=settings)


def test_existing_gate_callables_are_thin_wrappers_over_the_one_loader(
    settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Identity: kill switch, strategy control state and ownership all go
    through operator_controls.load_trading_gate_state (R-Q1)."""
    seed_registered_strategy(settings, TREND, owner=True)
    calls: list[str] = []
    real = operator_controls.load_trading_gate_state

    def spy(session, **kwargs):
        calls.append(kwargs.get("strategy_id") or "-")
        return real(session, **kwargs)

    monkeypatch.setattr(operator_controls, "load_trading_gate_state", spy)

    load_kill_switch_state(settings=settings)
    load_strategy_control_state(TREND, settings=settings)
    ownership_block_for(TREND, settings=settings)
    read_trading_gate_state(settings)

    assert calls == ["-", TREND, "-", "-"]


# --- R-8: disabled by default ------------------------------------------------


def test_ensure_strategy_record_creates_new_rows_disabled(settings) -> None:
    with session_scope(settings) as session:
        record = ensure_strategy_record(session, _metadata(settings, TREND))
        assert record.status == StrategyStatus.DISABLED


def test_ensure_strategy_record_never_changes_an_existing_status(settings) -> None:
    with session_scope(settings) as session:
        seed_strategy(session, _metadata(settings, TREND), enabled=True)
    with session_scope(settings) as session:
        again = ensure_strategy_record(session, _metadata(settings, TREND))
        assert again.status == StrategyStatus.ACTIVE
        again_explicit = ensure_strategy_record(
            session, _metadata(settings, TREND), initial_status=StrategyStatus.DISABLED
        )
        assert again_explicit.status == StrategyStatus.ACTIVE


def test_initial_status_argument_is_honoured_for_new_rows(settings) -> None:
    with session_scope(settings) as session:
        record = ensure_strategy_record(
            session, _metadata(settings, TREND), initial_status=StrategyStatus.ACTIVE
        )
        assert record.status == StrategyStatus.ACTIVE


def test_pure_control_read_and_get_or_create_agree_on_disabled(settings) -> None:
    """R-8: the no-row read default equals what ensure_strategy_state persists."""
    read_state = load_strategy_control_state(TREND, settings=settings)
    with session_scope(settings) as session:
        assert session.execute(select(Strategy)).first() is None  # the read wrote nothing
    ensured = ensure_strategy_control_state(TREND, settings=settings)

    assert read_state.status == ensured.status == StrategyStatus.DISABLED.value
    assert read_state.is_execution_enabled is False


# --- read-only GET route -----------------------------------------------------


@pytest.fixture()
def client(migrated_paper_db: str):  # noqa: F811
    clear_settings_cache()
    with TestClient(create_app()) as test_client:
        yield test_client


def test_get_active_paper_strategy_with_no_owner_is_null(client: TestClient) -> None:
    response = client.get("/api/v1/controls/active-paper-strategy")

    assert response.status_code == 200
    body = response.json()
    # 20.1-12 extends the read additively (R1): every 20.1-01 field stays.
    assert {"strategy_id", "display_name", "since", "reason", "set_by_run_id"} <= set(body)
    assert {"checks", "seeding_available", "handover_available", "as_of"} <= set(body)
    assert body["strategy_id"] is None
    assert body["display_name"] is None
    assert body["set_by_run_id"] is None
    assert body["since"]


def test_get_active_paper_strategy_with_owner(client: TestClient) -> None:
    settings = load_settings()
    seed_registered_strategy(settings, TREND, owner=True)

    body = client.get("/api/v1/controls/active-paper-strategy").json()

    assert body["strategy_id"] == TREND
    assert body["display_name"] == _metadata(settings, TREND).display_name
    assert body["reason"] == "test"


def test_get_active_paper_strategy_missing_row_is_typed_503(client: TestClient) -> None:
    with session_scope(load_settings()) as session:
        session.execute(text("DELETE FROM active_paper_strategy"))

    response = client.get("/api/v1/controls/active-paper-strategy")

    assert response.status_code == 503
    assert response.json()["detail"] == {"code": "control_state_unavailable"}


def test_get_active_paper_strategy_performs_no_write(client: TestClient) -> None:
    settings = load_settings()
    engine = get_engine(settings)
    with count_queries(engine) as counter:
        response = client.get("/api/v1/controls/active-paper-strategy")
    assert response.status_code == 200
    assert counter.statements
    # SELECTs and read-only CTEs (the 20.1-10 recovery predicate behind check A5).
    assert all(
        statement.lstrip().upper().startswith(("SELECT", "WITH"))
        for statement in counter.statements
    )
    with session_scope(settings) as session:
        assert session.execute(select(ActivePaperStrategy.strategy_id)).scalar_one() is None
        assert session.execute(select(Strategy)).first() is None


def test_active_paper_strategy_route_has_no_post_delete_or_patch(client: TestClient) -> None:
    # The PUT is the 20.1-12 owner control (tests/test_paper_ownership_controls.py).
    for method in ("post", "delete", "patch"):
        response = getattr(client, method)("/api/v1/controls/active-paper-strategy")
        assert response.status_code == 405


# --- typed 409 carrier -------------------------------------------------------


class _ConflictHandler:
    job_type = "conflict_probe"

    def run(self, context: Any) -> Mapping[str, Any]:  # pragma: no cover - never claimed
        return {}


class _ConflictSpec:
    job_type = "conflict_probe"
    description = "Raises a typed state conflict."
    cancellation_mode = JobCancellationMode.STEP_BOUNDARY

    def validate_payload(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        raise JobSubmissionConflictError(
            job_type=self.job_type, code="probe_conflict", detail={"strategy_id": "s-1"}
        )

    def submission_defaults(self) -> None:
        return None


def test_job_submission_conflict_error_maps_to_http_409(
    migrated_paper_db: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED", "true")
    clear_settings_cache()
    registry = JobRegistry()
    registry.register(_ConflictHandler(), submission_spec=_ConflictSpec())

    with TestClient(create_app(job_registry=registry)) as test_client:
        response = test_client.post(
            "/api/v1/jobs",
            headers={"Idempotency-Key": "conflict-1"},
            json={"job_type": "conflict_probe", "payload": {}},
        )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "probe_conflict",
        "job_type": "conflict_probe",
        "strategy_id": "s-1",
    }


def test_job_submission_conflict_error_is_not_a_payload_error() -> None:
    from trading_platform.jobs.registry import InvalidJobPayloadError

    error = JobSubmissionConflictError(job_type="t", code="c")
    assert dict(error.detail) == {}
    assert not isinstance(error, InvalidJobPayloadError)
    assert "c" in str(error)


def test_default_strategy_id_setting_is_documented_as_not_a_trading_default() -> None:
    from trading_platform.core.settings import PaperSessionRunnerSettings

    field = PaperSessionRunnerSettings.model_fields["default_strategy_id"]
    assert field.default == "trend_following_daily"  # still loadable for report fallbacks
    assert field.description is not None
    assert "not a paper-trading default" in field.description.lower()
