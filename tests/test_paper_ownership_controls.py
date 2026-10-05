"""PAPER-02: seeding, handover, release and reaffirm of the paper owner (D-04, 20.1-12).

Part 1 (service level, real DB): ``OperatorControlService.set_active_paper_strategy``.
Part 2 (HTTP): ``PUT``/``GET /api/v1/controls/active-paper-strategy`` (see the HTTP section).
The account state is arranged with direct test-only writes; the control under test is the
only thing that changes the owner.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from datetime import date, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from tests.support.account_check_fixtures import (
    seed_clean_account_run,
    seed_quiet_account,
    seed_snapshot,
)
from tests.support.calendar_facts import seed_calendar
from tests.support.migrated_db import migrated_database
from tests.support.operation_fixtures import seed_operation, seed_operation_intent
from tests.support.paper_eligibility import allow_paper_execution
from tests.support.paper_execution_seams import allow_direct_paper_execution
from tests.support.paper_ownership import seed_registered_strategy, set_active_paper_strategy
from tests.support.recovery_fixtures import OTHER, OWNER, at, seed_job, strategy_row
from tests.test_active_paper_strategy_gates import RecordingBrokerClient, RecordingExecutionService
from tests.test_backtest_runner import migrated_backtest_db, strategy_config_override  # noqa: F401
from tests.test_job_operations_e2e import job_operations_env  # noqa: F401
from tests.test_paper_account_checks import REASON_CASES, arrange_not_received_world
from tests.test_paper_execution import (
    _seed_approved_risk_batch,
    migrated_paper_db,  # noqa: F401
)
from tests.test_paper_session_job_e2e import (
    PAYLOAD,
    BrokerFakes,
    paper_jobs_env,  # noqa: F401
)

from trading_platform.api.app import create_app
from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.db.models import (
    ActivePaperStrategy,
    ExecutionEvent,
    Job,
    JobStatus,
    Strategy,
    StrategyRun,
    StrategyRunType,
    StrategyStatus,
)
from trading_platform.db.session import get_session_factory, session_scope
from trading_platform.jobs.registry import JobSubmissionConflictError
from trading_platform.jobs.registry import build_default_registry as build_job_registry
from trading_platform.orchestration.job_mutations import JobOrchestrationService
from trading_platform.services import operator_controls
from trading_platform.services.active_paper_strategy import (
    BLOCKED_REASON_NOT_ACTIVE_PAPER_STRATEGY,
    lock_active_paper_strategy_shared,
)
from trading_platform.services.execution import run_paper_session
from trading_platform.services.operator_controls import (
    AccountCheckFailedError,
    ControlStateUnavailableError,
    OperatorControlService,
    StrategyArchivedError,
)
from trading_platform.services.paper_account_checks import CheckId, CheckReason
from trading_platform.strategies.registry import UnknownStrategyError, build_default_registry

THIRD = "rsi_mean_reversion_daily"
ANCHOR = "trend_following_daily"


@pytest.fixture()
def owner_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    with migrated_database(monkeypatch, "paper_ownership_controls") as name:
        seed_calendar(date(2025, 11, 24), date(2026, 1, 9))
        yield name


def _all_strategy_ids() -> list[str]:
    return [m.strategy_id for m in build_default_registry(load_settings()).list_metadata()]


def _enable_all_strategies() -> None:
    """The local DB today: all four registry strategies are enabled."""

    settings = load_settings()
    for strategy_id in _all_strategy_ids():
        seed_registered_strategy(settings, strategy_id, enabled=True)


def _quiet_account() -> None:
    with session_scope(load_settings()) as session:
        seed_quiet_account(session)


def _service() -> OperatorControlService:
    return OperatorControlService(settings=load_settings())


def _set(strategy_id: str | None, *, reason: str = "operator choice") -> Any:
    return _service().set_active_paper_strategy(
        strategy_id, reason=reason, actor="pytest", trigger_source="pytest"
    )


def _own(strategy_id: str, *, enabled: bool) -> None:
    """Direct arrangement: ``strategy_id`` owns the account with an explicit status."""

    with session_scope(load_settings()) as session:
        row = strategy_row(session, strategy_id)
        row.status = StrategyStatus.ACTIVE if enabled else StrategyStatus.DISABLED
        session.flush()
        set_active_paper_strategy(session, strategy_id)


def _fingerprint() -> dict[str, Any]:
    with session_scope(load_settings()) as session:
        singleton = session.execute(select(ActivePaperStrategy)).scalar_one()
        statuses = {
            row.strategy_id: row.status.value for row in session.execute(select(Strategy)).scalars()
        }
        return {
            "owner": (
                singleton.strategy_id,
                singleton.since,
                singleton.reason,
                singleton.set_by_run_id,
            ),
            "statuses": statuses,
            "runs": session.scalar(select(func.count()).select_from(StrategyRun)),
            "events": session.scalar(select(func.count()).select_from(ExecutionEvent)),
        }


def _owner_public_id() -> str | None:
    with session_scope(load_settings()) as session:
        return session.execute(
            select(Strategy.strategy_id)
            .join(ActivePaperStrategy, ActivePaperStrategy.strategy_id == Strategy.id)
            .where(ActivePaperStrategy.id == 1)
        ).scalar_one_or_none()


def _status_of(strategy_id: str) -> str:
    with session_scope(load_settings()) as session:
        return (
            session.execute(select(Strategy.status).where(Strategy.strategy_id == strategy_id))
            .scalar_one()
            .value
        )


def _audit_of(run_id: str) -> tuple[StrategyRun, ExecutionEvent, str]:
    import uuid

    with session_scope(load_settings()) as session:
        run = session.get(StrategyRun, uuid.UUID(run_id))
        assert run is not None
        event = session.execute(
            select(ExecutionEvent).where(ExecutionEvent.strategy_run_id == run.id)
        ).scalar_one()
        attached = session.execute(
            select(Strategy.strategy_id).where(Strategy.id == run.strategy_id)
        ).scalar_one()
        session.expunge_all()
        return run, event, attached


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


def test_seeding_succeeds_with_a_quiet_account_and_leaves_the_new_owner_disabled(
    owner_db: str,
) -> None:
    _enable_all_strategies()
    _quiet_account()

    report = _set(OTHER, reason="choose the breakout strategy")

    assert (report.kind, report.changed) == ("seeding", True)
    assert report.previous_strategy_id is None and report.strategy_id == OTHER
    assert report.new_owner_status == "disabled" and report.new_owner_disabled is True
    assert _owner_public_id() == OTHER
    assert _status_of(OTHER) == "disabled"
    assert {s: _status_of(s) for s in _all_strategy_ids() if s != OTHER} == {
        s: "active" for s in _all_strategy_ids() if s != OTHER
    }
    with session_scope(load_settings()) as session:
        singleton = session.execute(select(ActivePaperStrategy)).scalar_one()
        assert singleton.reason == "choose the breakout strategy"
        assert str(singleton.set_by_run_id) == report.run_id
        assert singleton.since == datetime.fromisoformat(report.since)
    run, event, attached = _audit_of(report.run_id)
    assert attached == OTHER and run.run_type is StrategyRunType.OPERATOR_CONTROL
    assert event.event_type == "active_paper_strategy_changed"
    assert event.details["kind"] == "seeding"
    assert event.details["previous_strategy_id"] is None
    assert event.details["new_strategy_id"] == OTHER
    assert event.details["reason"] == "choose the breakout strategy"
    assert event.details["new_owner_disabled"] is True
    assert [c["id"] for c in event.details["checks"]] == ["A1", "A2", "A3", "A4", "A5", "A6"]
    assert all(c["passed"] for c in event.details["checks"])


def test_new_owner_starts_disabled_even_if_enabled(owner_db: str) -> None:
    _enable_all_strategies()
    _quiet_account()
    assert _status_of(ANCHOR) == "active"

    report = _set(ANCHOR)

    assert _status_of(ANCHOR) == "disabled"
    assert report.new_owner_disabled is True
    # Enabling stays a separate act through the existing strategy control.
    _service().enable_strategy(ANCHOR, reason="go live", actor="pytest", trigger_source="pytest")
    assert _status_of(ANCHOR) == "active" and _owner_public_id() == ANCHOR


def test_seeding_an_already_disabled_target_records_no_status_change(owner_db: str) -> None:
    _quiet_account()
    seed_registered_strategy(load_settings(), OTHER, enabled=False)

    report = _set(OTHER)

    assert report.new_owner_disabled is False and report.new_owner_status is None
    _, event, _ = _audit_of(report.run_id)
    assert event.details["new_owner_disabled"] is False


def test_seeding_an_unseen_registered_strategy_creates_its_row_disabled(owner_db: str) -> None:
    _quiet_account()
    with session_scope(load_settings()) as session:
        assert (
            session.execute(
                select(Strategy).where(Strategy.strategy_id == THIRD)
            ).scalar_one_or_none()
            is None
        )

    report = _set(THIRD)

    assert report.changed is True and _status_of(THIRD) == "disabled"


def test_fresh_install_seeding_is_refused_a6_until_a_clean_account_reconciliation(
    owner_db: str,
) -> None:
    """05 E1: M6 before M5 -> check_failed:A6; after the clean run it succeeds."""

    _enable_all_strategies()
    before = _fingerprint()

    with pytest.raises(AccountCheckFailedError) as refused:
        _set(OTHER)

    assert refused.value.code == "check_failed:A6"
    assert set(refused.value.failed_checks) == {"A2", "A3", "A4", "A6"}
    assert _fingerprint() == before

    _quiet_account()
    assert _set(OTHER).changed is True


def test_seeding_today_requires_resolving_29_sep_outcomes(owner_db: str) -> None:
    """E2: two uncertain paper-session Jobs and one uncertain broker-order-sync Job without
    linked runs: refused A5 (A6 also failing) until a fresh clean account reconciliation."""

    _enable_all_strategies()

    def build(session: Any) -> None:
        seed_snapshot(session, snapshot_at=at(0))
        seed_clean_account_run(session, completed_at=at(-10))
        seed_job(session, job_type="paper-session", completed_at=at(1))
        seed_job(session, job_type="paper-session", completed_at=at(2))
        seed_job(session, job_type="broker-order-sync", completed_at=at(3))

    with session_scope(load_settings()) as session:
        build(session)
    before = _fingerprint()

    with pytest.raises(AccountCheckFailedError) as refused:
        _set(OTHER)

    assert refused.value.code == "check_failed:A5"
    assert {"A5", "A6"} <= set(refused.value.failed_checks)
    assert _fingerprint() == before

    with session_scope(load_settings()) as session:
        seed_clean_account_run(session, completed_at=at(30))
    assert _set(OTHER).kind == "seeding"


# One refusal per check, seeding (or handover for A7), zero rows written.
_FIRST_FAILING = {CheckReason.NO_ACCOUNT_RECONCILIATION: CheckId.A6}


@pytest.mark.parametrize(
    ("reason", "arrange", "check_id", "_kinds", "handover"),
    REASON_CASES,
    ids=[case[0].value for case in REASON_CASES],
)
def test_refusal_writes_nothing(
    owner_db: str,
    reason: CheckReason,
    arrange: Callable[[Any], None],
    check_id: CheckId,
    _kinds: object,
    handover: bool,
) -> None:
    _enable_all_strategies()
    with session_scope(load_settings()) as session:
        arrange(session)
    before = _fingerprint()

    with pytest.raises(AccountCheckFailedError) as refused:
        _set(THIRD if handover else OTHER)

    expected = _FIRST_FAILING.get(reason, check_id)
    assert refused.value.code == f"check_failed:{expected.value}"
    assert refused.value.check == expected.value
    failing = [c for c in refused.value.checks if not c["passed"]]
    assert [c["id"] for c in failing] == refused.value.failed_checks
    assert any(c["reason_code"] == reason.value for c in failing)
    assert _fingerprint() == before


# ---------------------------------------------------------------------------
# Handover and release
# ---------------------------------------------------------------------------


def _handover_world(failing: Callable[[Any], None] | None = None) -> None:
    """OWNER is the (disabled) outgoing owner; a quiet account unless ``failing`` breaks it."""

    _enable_all_strategies()
    with session_scope(load_settings()) as session:
        (failing or (lambda s: seed_quiet_account(s)))(session)
    _own(OWNER, enabled=False)


def _open_position(session: Any) -> None:
    seed_snapshot(session, open_positions=2)
    seed_clean_account_run(session, completed_at=at(10))


def _open_order(session: Any) -> None:
    seed_snapshot(session)
    seed_clean_account_run(session, completed_at=at(10), non_terminal=1)


def _open_operation(session: Any) -> None:
    seed_quiet_account(session)
    operation = seed_operation(session, state="paused", reason="awaiting_reconciliation")
    seed_operation_intent(session, operation)


@pytest.mark.parametrize(
    ("failing", "expected"),
    [
        (_open_position, "A3"),
        (_open_order, "A2"),
        (_open_operation, "A1"),
    ],
    ids=["position", "open_order", "open_operation"],
)
def test_handover_is_refused_unless_the_account_is_flat_and_quiet(
    owner_db: str, failing: Callable[[Any], None], expected: str
) -> None:
    _handover_world(failing)
    before = _fingerprint()

    with pytest.raises(AccountCheckFailedError) as refused:
        _set(OTHER)

    assert refused.value.code == f"check_failed:{expected}"
    assert _fingerprint() == before
    assert _owner_public_id() == OWNER


def test_handover_requires_a_disabled_outgoing_owner_and_never_disables_it(
    owner_db: str,
) -> None:
    _handover_world()
    _own(OWNER, enabled=True)
    before = _fingerprint()

    with pytest.raises(AccountCheckFailedError) as refused:
        _set(OTHER)

    assert refused.value.code == "check_failed:A7"
    assert refused.value.failed_checks == ["A7"]
    assert _fingerprint() == before
    assert _status_of(OWNER) == "active"  # never silently disabled


def test_handover_moves_ownership_and_leaves_both_strategies_disabled(owner_db: str) -> None:
    _handover_world()

    report = _set(OTHER, reason="rotate")

    assert (report.kind, report.changed) == ("handover", True)
    assert report.previous_strategy_id == OWNER and report.strategy_id == OTHER
    assert _owner_public_id() == OTHER
    assert _status_of(OTHER) == "disabled" and _status_of(OWNER) == "disabled"
    assert report.new_owner_disabled is True
    _, event, attached = _audit_of(report.run_id)
    assert attached == OTHER
    assert event.details["kind"] == "handover" and event.details["previous_strategy_id"] == OWNER
    assert [c["id"] for c in event.details["checks"]] == [
        "A1",
        "A2",
        "A3",
        "A4",
        "A5",
        "A6",
        "A7",
    ]


def test_release_needs_a7_and_clears_the_owner_without_touching_statuses(owner_db: str) -> None:
    _handover_world()
    _own(OWNER, enabled=True)
    with pytest.raises(AccountCheckFailedError) as refused:
        _set(None)
    assert refused.value.code == "check_failed:A7"

    _own(OWNER, enabled=False)
    report = _set(None, reason="stand down")

    assert (report.kind, report.changed) == ("release", True)
    assert report.previous_strategy_id == OWNER and report.strategy_id is None
    assert report.new_owner_status is None and report.new_owner_disabled is False
    assert _owner_public_id() is None
    assert _status_of(OWNER) == "disabled"
    with session_scope(load_settings()) as session:
        singleton = session.execute(select(ActivePaperStrategy)).scalar_one()
        assert singleton.strategy_id is None and singleton.reason == "stand down"
        assert str(singleton.set_by_run_id) == report.run_id
    _, event, attached = _audit_of(report.run_id)
    assert attached == OWNER  # the OUTGOING owner
    assert event.event_type == "active_paper_strategy_changed"
    assert event.details["kind"] == "release" and event.details["new_strategy_id"] is None
    assert event.details["anchor_only"] is False


# ---------------------------------------------------------------------------
# Reaffirm and audit attachment
# ---------------------------------------------------------------------------


def test_reaffirm_is_unchanged_and_audited_without_checks(
    owner_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_all_strategies()
    _own(OWNER, enabled=True)  # an ENABLED owner: reaffirm must not touch its status
    before = _fingerprint()

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("a reaffirm must evaluate no checks")

    monkeypatch.setattr(operator_controls, "evaluate_account_checks", _boom)

    report = _set(OWNER, reason="confirming")

    assert (report.kind, report.changed) == ("reaffirm", False)
    assert report.strategy_id == report.previous_strategy_id == OWNER
    assert report.new_owner_status is None and report.checks == []
    after = _fingerprint()
    assert after["owner"] == before["owner"]  # since / reason / set_by_run_id untouched
    assert after["statuses"] == before["statuses"]
    assert (after["runs"], after["events"]) == (before["runs"] + 1, before["events"] + 1)
    run, event, attached = _audit_of(report.run_id)
    assert attached == OWNER and run.run_type is StrategyRunType.OPERATOR_CONTROL
    assert event.event_type == "active_paper_strategy_unchanged"
    assert event.details["kind"] == "reaffirm"


def test_none_to_none_reaffirm_uses_the_kill_switch_anchor_strategy(owner_db: str) -> None:
    before = _fingerprint()

    report = _set(None, reason="still nobody")

    assert (report.kind, report.changed, report.anchor_only) == ("reaffirm", False, True)
    run, event, attached = _audit_of(report.run_id)
    assert attached == ANCHOR
    assert event.event_type == "active_paper_strategy_unchanged"
    assert event.details["anchor_only"] is True
    assert (
        event.details["previous_strategy_id"] is None and event.details["new_strategy_id"] is None
    )
    after = _fingerprint()
    assert after["owner"] == before["owner"]
    assert (after["runs"], after["events"]) == (before["runs"] + 1, before["events"] + 1)
    assert _owner_public_id() is None


def test_audit_attachment_is_the_beginning_outgoing_or_same_owner(owner_db: str) -> None:
    _enable_all_strategies()
    _quiet_account()
    seeding = _set(OWNER)
    assert _audit_of(seeding.run_id)[2] == OWNER
    assert _audit_of(seeding.run_id)[1].details["anchor_only"] is False

    handover = _set(OTHER)
    assert _audit_of(handover.run_id)[2] == OTHER

    reaffirm = _set(OTHER)
    assert _audit_of(reaffirm.run_id)[2] == OTHER

    release = _set(None)
    assert _audit_of(release.run_id)[2] == OTHER

    anchor = _set(None)
    assert _audit_of(anchor.run_id)[2] == ANCHOR and anchor.anchor_only is True
    # The owner is never derived from these runs (D-06): only the singleton decides.
    assert _owner_public_id() is None


# ---------------------------------------------------------------------------
# Typed refusals that write nothing
# ---------------------------------------------------------------------------


def test_archived_target_is_a_typed_error_with_zero_writes(owner_db: str) -> None:
    _quiet_account()
    with session_scope(load_settings()) as session:
        strategy_row(session, OTHER).status = StrategyStatus.ARCHIVED
    before = _fingerprint()

    with pytest.raises(StrategyArchivedError):
        _set(OTHER)

    assert _fingerprint() == before


def test_unregistered_target_is_a_typed_error_with_zero_writes(owner_db: str) -> None:
    _quiet_account()
    before = _fingerprint()

    with pytest.raises(UnknownStrategyError):
        _set("not_a_registered_strategy")

    assert _fingerprint() == before


def test_missing_singleton_row_fails_closed_without_creating_it(owner_db: str) -> None:
    from sqlalchemy import text

    with session_scope(load_settings()) as session:
        session.execute(text("DELETE FROM active_paper_strategy"))

    with pytest.raises(ControlStateUnavailableError):
        _set(OTHER)

    with session_scope(load_settings()) as session:
        assert session.scalar(select(func.count()).select_from(ActivePaperStrategy)) == 0


# ---------------------------------------------------------------------------
# No broker, no worker
# ---------------------------------------------------------------------------


def test_no_broker_call_and_no_worker(owner_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from trading_platform.services import alpaca as alpaca_module
    from trading_platform.services.reconciliation import report as report_module

    constructed: list[str] = []

    def _no_client(*_args: object, **_kwargs: object) -> None:
        constructed.append("AlpacaClient")
        raise AssertionError("the control must never construct a broker client")

    monkeypatch.setattr(alpaca_module.AlpacaClient, "__init__", _no_client)
    monkeypatch.setattr(report_module, "load_broker_state", _no_client)
    monkeypatch.delenv("TRADING_PLATFORM_ALPACA__API_KEY", raising=False)
    monkeypatch.delenv("TRADING_PLATFORM_ALPACA__SECRET_KEY", raising=False)
    _enable_all_strategies()
    _quiet_account()

    seeding = _set(OTHER)
    _own(OTHER, enabled=False)
    handover = _set(OWNER)
    release = _set(None)

    assert (seeding.kind, handover.kind, release.kind) == ("seeding", "handover", "release")
    assert constructed == []
    # No worker was started: the control is synchronous and enqueued no Job.
    with session_scope(load_settings()) as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 0


# ---------------------------------------------------------------------------
# Serialization (SER)
# ---------------------------------------------------------------------------


def test_concurrent_different_targets_serialize(owner_db: str) -> None:
    """One target wins as seeding; the other is evaluated against the NEW state, so it is
    a handover from the winner (never a second seeding)."""

    _enable_all_strategies()
    _quiet_account()
    barrier = threading.Barrier(2)
    results: dict[str, Any] = {}

    def worker(name: str, target: str) -> None:
        service = _service()
        barrier.wait()
        try:
            results[name] = service.set_active_paper_strategy(
                target, reason=f"race {name}", actor="pytest", trigger_source="pytest"
            )
        except Exception as exc:  # pragma: no cover - failure path asserted below
            results[name] = exc

    threads = [
        threading.Thread(target=worker, args=("a", OWNER)),
        threading.Thread(target=worker, args=("b", OTHER)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    reports = [results["a"], results["b"]]
    assert all(not isinstance(report, Exception) for report in reports), reports
    by_kind = {report.kind: report for report in reports}
    assert set(by_kind) == {"seeding", "handover"}
    assert by_kind["seeding"].previous_strategy_id is None
    assert by_kind["handover"].previous_strategy_id == by_kind["seeding"].strategy_id
    assert _owner_public_id() == by_kind["handover"].strategy_id
    assert _status_of(OWNER) == "disabled" and _status_of(OTHER) == "disabled"
    with session_scope(load_settings()) as session:
        events = session.scalar(
            select(func.count())
            .select_from(ExecutionEvent)
            .where(ExecutionEvent.event_type == "active_paper_strategy_changed")
        )
    assert events == 2


def test_concurrent_same_target_one_seeds_and_the_other_reaffirms(owner_db: str) -> None:
    _enable_all_strategies()
    _quiet_account()
    barrier = threading.Barrier(2)
    results: list[Any] = []

    def worker() -> None:
        service = _service()
        barrier.wait()
        results.append(
            service.set_active_paper_strategy(
                OTHER, reason="race", actor="pytest", trigger_source="pytest"
            )
        )

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert sorted(report.kind for report in results) == ["reaffirm", "seeding"]
    assert _owner_public_id() == OTHER


# ---------------------------------------------------------------------------
# R1 view
# ---------------------------------------------------------------------------


def test_view_reports_owner_checks_and_availability_without_writes(owner_db: str) -> None:
    _enable_all_strategies()
    _quiet_account()
    before = _fingerprint()

    view = _service().get_active_paper_strategy_view()
    assert view.state.strategy_id is None
    assert [c["id"] for c in view.checks] == ["A1", "A2", "A3", "A4", "A5", "A6"]
    assert view.seeding_available is True and view.handover_available is False
    body = view.to_dict()
    assert {"checks", "seeding_available", "handover_available", "as_of"} <= set(body)
    # 20.1-14 (additive): the closed trading-blocked list rides on the same GET; with no owner
    # nothing may trade, and every earlier key (golden subset) is still present.
    assert {
        "strategy_id",
        "since",
        "display_name",
        "checks",
        "seeding_available",
        "handover_available",
        "as_of",
        "trading_blocked_reasons",
    } <= set(body)
    assert body["trading_blocked_reasons"] == ["no_active_paper_strategy"]
    assert _fingerprint() == before

    _own(OWNER, enabled=True)
    with_owner = _service().get_active_paper_strategy_view()
    assert [c["id"] for c in with_owner.checks][-1] == "A7"
    assert with_owner.seeding_available is False
    assert with_owner.handover_available is False  # the outgoing owner is enabled
    _own(OWNER, enabled=False)
    assert _service().get_active_paper_strategy_view().handover_available is True


def test_view_fails_closed_on_a_missing_singleton(owner_db: str) -> None:
    from sqlalchemy import text

    with session_scope(load_settings()) as session:
        session.execute(text("DELETE FROM active_paper_strategy"))

    with pytest.raises(ControlStateUnavailableError):
        _service().get_active_paper_strategy_view()


# ---------------------------------------------------------------------------
# Round 5: a recorded not_received statement does not bypass an in-doubt submission
# ---------------------------------------------------------------------------


def test_not_received_statement_cannot_be_bypassed_by_handover_or_release(
    owner_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker, _now = arrange_not_received_world(monkeypatch)
    before = _fingerprint()

    with pytest.raises(AccountCheckFailedError) as handover:
        _set(OTHER)
    with pytest.raises(AccountCheckFailedError) as release:
        _set(None)

    assert handover.value.code == release.value.code == "check_failed:A5"
    assert handover.value.failed_checks == ["A5"]
    assert _fingerprint() == before
    assert broker.post_count == 0


# ---------------------------------------------------------------------------
# SER: the control and Job admission serialize on the singleton row
# ---------------------------------------------------------------------------


def _admission_service() -> JobOrchestrationService:
    settings = load_settings()
    return JobOrchestrationService(settings, build_job_registry(settings))


def _run_in_thread(fn: Callable[[], Any]) -> tuple[threading.Thread, dict[str, Any]]:
    outcome: dict[str, Any] = {}

    def target() -> None:
        try:
            outcome["result"] = fn()
        except BaseException as exc:  # noqa: BLE001 - surfaced to the test
            outcome["error"] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread, outcome


def _paper_session_job_count() -> int:
    with session_scope(load_settings()) as session:
        return (
            session.scalar(
                select(func.count()).select_from(Job).where(Job.job_type == "paper-session")
            )
            or 0
        )


def test_race_handover_first_then_submit_one_consistent_outcome(
    paper_jobs_env: BrokerFakes,
    monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    """The handover holds the singleton FOR UPDATE (checks in flight) when a paper-session
    submit arrives: the submit waits, then its in-transaction re-check sees the new owner and
    refuses. The handover wins, no Job is inserted."""

    allow_paper_execution(monkeypatch)
    settings = load_settings()
    seed_registered_strategy(settings, OTHER, enabled=True)
    _own(OWNER, enabled=False)
    with session_scope(settings) as session:
        seed_quiet_account(session)

    locked, proceed = threading.Event(), threading.Event()
    real = operator_controls.evaluate_account_checks

    def slow_evaluate(*args: Any, **kwargs: Any) -> Any:
        locked.set()
        assert proceed.wait(30)
        return real(*args, **kwargs)

    monkeypatch.setattr(operator_controls, "evaluate_account_checks", slow_evaluate)
    put_thread, put_outcome = _run_in_thread(lambda: _set(OTHER))
    assert locked.wait(15)
    submit_thread, submit_outcome = _run_in_thread(
        lambda: _admission_service().submit(
            job_type="paper-session", payload=dict(PAYLOAD), idempotency_key="ser-1"
        )
    )
    submit_thread.join(timeout=1.5)
    assert submit_thread.is_alive(), "admission must wait for the control's singleton lock"
    proceed.set()
    put_thread.join(timeout=30)
    submit_thread.join(timeout=30)

    assert put_outcome["result"].kind == "handover"
    error = submit_outcome.get("error")
    assert isinstance(error, JobSubmissionConflictError), submit_outcome
    assert error.code == "strategy_not_active_paper_strategy"
    assert _paper_session_job_count() == 0
    assert _owner_public_id() == OTHER


def test_race_submit_first_then_handover_is_refused_a1(
    paper_jobs_env: BrokerFakes,
    monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    """Admission holds the singleton FOR SHARE and inserts a queued paper-session Job; the
    handover (FOR UPDATE) waits, then evaluates A1 against the committed Job and refuses."""

    allow_paper_execution(monkeypatch)
    settings = load_settings()
    seed_registered_strategy(settings, OTHER, enabled=True)
    _own(OWNER, enabled=False)
    with session_scope(settings) as session:
        seed_quiet_account(session)

    holder = get_session_factory(settings)()
    try:
        lock_active_paper_strategy_shared(holder)
        put_thread, put_outcome = _run_in_thread(lambda: _set(OTHER))
        put_thread.join(timeout=1.5)
        assert put_thread.is_alive(), "the control must wait for the admission's share lock"
        holder.add(
            Job(
                job_type="paper-session",
                payload=dict(PAYLOAD),
                status=JobStatus.QUEUED,
            )
        )
        holder.commit()
    finally:
        holder.close()
    put_thread.join(timeout=30)

    error = put_outcome.get("error")
    assert isinstance(error, AccountCheckFailedError), put_outcome
    assert error.code == "check_failed:A1"
    assert _owner_public_id() == OWNER


def test_put_is_refused_a1_while_a_paper_session_job_is_queued(
    paper_jobs_env: BrokerFakes,
    monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    allow_paper_execution(monkeypatch)
    settings = load_settings()
    seed_registered_strategy(settings, OTHER, enabled=True)
    with session_scope(settings) as session:
        seed_quiet_account(session)
    _own(OWNER, enabled=True)  # the submit below needs an owner to be accepted
    _admission_service().submit(
        job_type="paper-session", payload=dict(PAYLOAD), idempotency_key="queued-1"
    )
    _own(OWNER, enabled=False)

    with pytest.raises(AccountCheckFailedError) as refused:
        _set(OTHER)

    assert refused.value.code == "check_failed:A1"
    assert _owner_public_id() == OWNER


# ---------------------------------------------------------------------------
# Run-time gate after a real handover (20.1-01)
# ---------------------------------------------------------------------------


def test_job_queued_for_previous_owner_is_blocked_at_run_time_after_handover(
    migrated_paper_db: str,
    monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    allow_paper_execution(monkeypatch)
    _seed_approved_risk_batch()  # trend_following_daily, enabled
    settings = load_settings()
    seed_registered_strategy(settings, OTHER, enabled=True)
    _own(OWNER, enabled=False)
    with session_scope(settings) as session:
        seed_quiet_account(session)

    report = _set(OTHER, reason="rotate")
    assert report.kind == "handover"

    # A Job for the previous owner inserted by a direct DB write after the change.
    with session_scope(settings) as session:
        session.add(Job(job_type="paper-session", payload=dict(PAYLOAD), status=JobStatus.QUEUED))
    broker = RecordingBrokerClient()
    execution = RecordingExecutionService()
    result = run_paper_session(
        OWNER,
        as_of_session=date(2024, 1, 5),
        settings=settings,
        execution_service=execution,
        broker_client=broker,
        trigger_source="pytest",
    )

    assert result.action == "blocked_not_active_paper_strategy"
    assert result.result_summary["blocked_reason"] == BLOCKED_REASON_NOT_ACTIVE_PAPER_STRATEGY
    assert broker.calls == [] and execution.calls == [] and execution.submitted_intents == []


# ===========================================================================
# Part 2: HTTP (PUT / GET /api/v1/controls/active-paper-strategy)
# ===========================================================================

URL = "/api/v1/controls/active-paper-strategy"


@pytest.fixture()
def http(owner_db: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED", "true")
    clear_settings_cache()
    with TestClient(create_app()) as client:
        yield client


def _put(client: TestClient, strategy_id: object, reason: object = "operator choice") -> Any:
    return client.put(URL, json={"strategy_id": strategy_id, "reason": reason})


def test_e1_seeding_over_http_refused_a6_then_accepted_and_enabling_is_separate(
    http: TestClient,
) -> None:
    _enable_all_strategies()
    before = _fingerprint()

    refused = _put(http, OTHER)

    assert refused.status_code == 409
    detail = refused.json()["detail"]
    assert detail["code"] == "check_failed:A6" and detail["check"] == "A6"
    assert set(detail["failed_checks"]) == {"A2", "A3", "A4", "A6"}
    assert [c["id"] for c in detail["checks"]] == ["A1", "A2", "A3", "A4", "A5", "A6"]
    assert _fingerprint() == before

    _quiet_account()
    accepted = _put(http, OTHER, "go")

    assert accepted.status_code == 200, accepted.text
    body = accepted.json()
    assert set(body) == {
        "active_paper_strategy",
        "previous_strategy_id",
        "changed",
        "kind",
        "new_owner_status",
        "run_id",
    }
    assert body["active_paper_strategy"]["strategy_id"] == OTHER
    assert body["active_paper_strategy"]["since"]
    assert body["previous_strategy_id"] is None
    assert (body["changed"], body["kind"], body["new_owner_status"]) == (
        True,
        "seeding",
        "disabled",
    )
    assert _status_of(OTHER) == "disabled"

    enabled = http.put(
        f"/api/v1/controls/strategies/{OTHER}", json={"status": "enabled", "reason": "live"}
    )
    assert enabled.status_code == 200 and enabled.json()["status"] == "enabled"
    assert _owner_public_id() == OTHER


_CHECK_CASE_REASONS = {
    CheckReason.BROKER_JOB_ACTIVE,
    CheckReason.NON_TERMINAL_ORDERS,
    CheckReason.OPEN_POSITIONS,
    CheckReason.UNRECOGNIZED_ITEMS,
    CheckReason.UNRESOLVED_OUTCOME,
    CheckReason.RECONCILIATION_STALE,
    CheckReason.OUTGOING_OWNER_ENABLED,
}
_PER_CHECK_CASES = [case for case in REASON_CASES if case[0] in _CHECK_CASE_REASONS]


def test_there_is_one_http_refusal_case_per_check() -> None:
    assert [case[2] for case in _PER_CHECK_CASES] == list(CheckId)


@pytest.mark.parametrize(
    ("reason", "arrange", "check_id", "_kinds", "handover"),
    _PER_CHECK_CASES,
    ids=[case[2].value for case in _PER_CHECK_CASES],
)
def test_each_failing_check_is_a_typed_409_naming_it_with_zero_writes(
    http: TestClient,
    reason: CheckReason,
    arrange: Callable[[Any], None],
    check_id: CheckId,
    _kinds: object,
    handover: bool,
) -> None:
    _enable_all_strategies()
    with session_scope(load_settings()) as session:
        arrange(session)
    before = _fingerprint()

    response = _put(http, THIRD if handover else OTHER)

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == f"check_failed:{check_id.value}"
    assert detail["check"] == check_id.value
    expected_ids = ["A1", "A2", "A3", "A4", "A5", "A6"] + (["A7"] if handover else [])
    assert [c["id"] for c in detail["checks"]] == expected_ids
    assert all(set(c) == {"id", "passed", "reason_code", "evidence_refs"} for c in detail["checks"])
    failing = [c for c in detail["checks"] if not c["passed"]]
    assert [c["id"] for c in failing] == detail["failed_checks"]
    named = next(c for c in failing if c["id"] == check_id.value)
    assert named["reason_code"] == reason.value
    assert _fingerprint() == before


def test_e13_handover_with_a_position_is_refused_a3_then_accepted_when_flat(
    http: TestClient,
) -> None:
    _handover_world(_open_position)

    refused = _put(http, OTHER)

    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "check_failed:A3"
    assert _owner_public_id() == OWNER

    with session_scope(load_settings()) as session:
        seed_snapshot(session, snapshot_at=at(20), open_positions=0)
        seed_clean_account_run(session, completed_at=at(30))
    accepted = _put(http, OTHER, "rotate")

    assert accepted.status_code == 200, accepted.text
    body = accepted.json()
    assert (body["kind"], body["changed"], body["new_owner_status"]) == (
        "handover",
        True,
        "disabled",
    )
    assert body["previous_strategy_id"] == OWNER
    assert _status_of(OTHER) == "disabled"


def test_a5_not_received_statement_does_not_unblock_handover_over_http(
    http: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker, _now = arrange_not_received_world(monkeypatch)

    response = _put(http, OTHER)

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "check_failed:A5" and detail["failed_checks"] == ["A5"]
    assert _owner_public_id() == OWNER
    assert broker.post_count == 0


def test_release_and_reaffirm_over_http(http: TestClient) -> None:
    _handover_world()

    reaffirm = _put(http, OWNER, "still me")
    assert reaffirm.status_code == 200
    assert reaffirm.json()["changed"] is False and reaffirm.json()["kind"] == "reaffirm"
    assert reaffirm.json()["new_owner_status"] is None

    release = _put(http, None, "stand down")
    assert release.status_code == 200
    assert release.json()["kind"] == "release"
    assert release.json()["active_paper_strategy"]["strategy_id"] is None
    assert release.json()["previous_strategy_id"] == OWNER

    again = _put(http, None, "still nobody")
    assert again.status_code == 200
    assert (again.json()["changed"], again.json()["kind"]) == (False, "reaffirm")


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"content": b"[1]"}, "invalid_control_request"),
        ({"content": b'"x"'}, "invalid_control_request"),
        ({"content": b"not json"}, "invalid_control_request"),
        ({"json": {"strategy_id": OTHER, "reason": "r", "extra": 1}}, "invalid_control_request"),
        ({"json": {"reason": "r"}}, "invalid_control_request"),
        ({"content": b"[" * 100000}, "invalid_control_request"),
        ({"json": {"strategy_id": 123, "reason": "r"}}, "invalid_control_target"),
        ({"json": {"strategy_id": ["a"], "reason": "r"}}, "invalid_control_target"),
        ({"json": {"strategy_id": {"a": 1}, "reason": "r"}}, "invalid_control_target"),
        ({"json": {"strategy_id": True, "reason": "r"}}, "invalid_control_target"),
        ({"json": {"strategy_id": OTHER}}, "invalid_control_reason"),
        ({"json": {"strategy_id": OTHER, "reason": 5}}, "invalid_control_reason"),
        ({"json": {"strategy_id": OTHER, "reason": "   "}}, "invalid_control_reason"),
        ({"json": {"strategy_id": OTHER, "reason": "x" * 501}}, "invalid_control_reason"),
        ({"json": {"strategy_id": OTHER, "reason": "a\u0000b"}}, "invalid_control_reason"),
    ],
)
def test_strict_body_validation_is_typed_422_with_zero_writes(
    http: TestClient, kwargs: dict[str, Any], code: str
) -> None:
    _enable_all_strategies()
    _quiet_account()
    before = _fingerprint()
    if "content" in kwargs:
        kwargs = {**kwargs, "headers": {"Content-Type": "application/json"}}

    response = http.put(URL, **kwargs)

    assert response.status_code == 422, response.text
    assert response.json()["detail"] == {"code": code}
    assert _fingerprint() == before


def test_unregistered_strategy_is_404_and_archived_is_409_with_zero_writes(
    http: TestClient,
) -> None:
    _quiet_account()
    with session_scope(load_settings()) as session:
        strategy_row(session, OTHER).status = StrategyStatus.ARCHIVED
    before = _fingerprint()

    missing = _put(http, "not_a_registered_strategy")
    archived = _put(http, OTHER)

    assert missing.status_code == 404
    assert missing.json()["detail"] == {
        "code": "strategy_not_found",
        "strategy_id": "not_a_registered_strategy",
    }
    assert archived.status_code == 409
    assert archived.json()["detail"] == {"code": "strategy_archived", "strategy_id": OTHER}
    assert _fingerprint() == before


def test_mutations_disabled_is_403_with_zero_writes(
    owner_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED", "false")
    clear_settings_cache()
    _quiet_account()
    before = _fingerprint()

    with TestClient(create_app()) as client:
        response = client.put(URL, json={"not": "valid"})
        valid = _put(client, OTHER)

    assert response.status_code == valid.status_code == 403
    assert response.json()["detail"] == {"code": "mutations_disabled"}
    assert _fingerprint() == before


def test_missing_singleton_row_is_503_and_a_write_failure_is_503(
    http: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy import text

    from trading_platform.services.operator_controls import ControlWriteError

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise ControlWriteError("db down")

    with monkeypatch.context() as patch:
        patch.setattr(OperatorControlService, "set_active_paper_strategy", _boom)
        failed = _put(http, OTHER)
    assert failed.status_code == 503
    assert failed.json()["detail"] == {"code": "control_write_failed"}

    with session_scope(load_settings()) as session:
        session.execute(text("DELETE FROM active_paper_strategy"))
    unavailable = _put(http, OTHER)
    assert unavailable.status_code == 503
    assert unavailable.json()["detail"] == {"code": "control_state_unavailable"}


def test_get_shape_with_and_without_an_owner(http: TestClient) -> None:
    _enable_all_strategies()
    _quiet_account()

    without = http.get(URL).json()
    assert [c["id"] for c in without["checks"]] == ["A1", "A2", "A3", "A4", "A5", "A6"]
    assert without["strategy_id"] is None
    assert without["seeding_available"] is True and without["handover_available"] is False
    assert without["as_of"]
    assert all(c["passed"] and c["reason_code"] is None for c in without["checks"])

    _own(OWNER, enabled=True)
    with_owner = http.get(URL).json()
    assert [c["id"] for c in with_owner["checks"]][-1] == "A7"
    assert with_owner["strategy_id"] == OWNER
    assert with_owner["seeding_available"] is False
    assert with_owner["handover_available"] is False
    a7 = with_owner["checks"][-1]
    assert a7["passed"] is False and a7["reason_code"] == "outgoing_owner_enabled"
    assert a7["evidence_refs"] == [{"kind": "strategy", "id": OWNER}]
    for key in ("strategy_id", "display_name", "since", "reason", "set_by_run_id"):
        assert key in with_owner

    _own(OWNER, enabled=False)
    assert http.get(URL).json()["handover_available"] is True


def test_get_is_read_only_bounded_and_makes_no_broker_call(
    http: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.support.query_counter import count_queries

    from trading_platform.db.session import get_engine
    from trading_platform.services import alpaca as alpaca_module

    def _no_client(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the read must never construct a broker client")

    monkeypatch.setattr(alpaca_module.AlpacaClient, "__init__", _no_client)
    _enable_all_strategies()
    _quiet_account()
    _own(OWNER, enabled=False)
    engine = get_engine(load_settings())

    def statements() -> list[str]:
        with count_queries(engine) as counter:
            assert http.get(URL).status_code == 200
        return counter.statements

    small = statements()
    assert all(s.lstrip().upper().startswith(("SELECT", "WITH")) for s in small)
    assert not any(k in s.upper() for s in small for k in ("INSERT ", "UPDATE ", "DELETE "))

    with session_scope(load_settings()) as session:
        for minute in range(25):
            seed_clean_account_run(session, completed_at=at(-100 - minute))
            seed_snapshot(session, snapshot_at=at(-100 - minute))
            seed_job(
                session,
                job_type="broker-order-sync",
                uncertain=False,
                status=JobStatus.SUCCEEDED,
                completed_at=at(-300 - minute),
            )
    before = _fingerprint()
    assert len(statements()) == len(small)
    assert _fingerprint() == before


def test_put_makes_no_broker_call(http: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from trading_platform.services import alpaca as alpaca_module

    def _no_client(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the control must never construct a broker client")

    monkeypatch.setattr(alpaca_module.AlpacaClient, "__init__", _no_client)
    _enable_all_strategies()
    _quiet_account()

    assert _put(http, OTHER).status_code == 200


@pytest.fixture(autouse=True)
def _direct_paper_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    """20.1-15: this module's subject is not the per-intent permission check or the S1 guard
    (tests/test_operation_permission.py, tests/test_paper_session_operations.py): see
    tests/support/paper_execution_seams.py."""

    allow_direct_paper_execution(monkeypatch)
