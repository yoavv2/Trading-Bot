"""TL-4 terminal states are explicit, visible and tested (SAF-04, 20.1-19).

Two states have no product-level release (spec E-5: zero order rows prove nothing; TL-4:
absence evidence never proves not-sent), so they are TERMINAL, blocking and visible through
``GET /api/v1/jobs/{id}/recovery`` and the active-paper-strategy check list (A5):

* a FLAGGED paper-session Job (a start Job or a Continue Job) whose linked ``paper_execution``
  run left zero order rows and zero attempts: ``unresolved / execution_path_unproven``;
* a legacy order the broker never had: ``not_found`` through any amount of absence evidence,
  a ``not_received`` statement and a clean reconciliation; it leaves the state only when the
  broker shows it.

No new mutating route and no new job type (D-30): nothing here releases either state.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import date
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
from tests.support.operation_fixtures import seed_operation
from tests.support.paper_ownership import set_active_paper_strategy
from tests.support.recovery_fixtures import (
    OTHER,
    OWNER,
    at,
    seed_account_run,
    seed_intent,
    seed_job,
    seed_paper_run,
    seed_uncertain_session,
    strategy_row,
)
from tests.test_recovery_predicate import LookupBroker, Timeline, _snapshot_for
from tests.test_recovery_shared_classifier import (
    _KINDS,
    PENDING,
    _a5_passed,
    _attempt_count,
    _gate,
    _owner_public_id,
    _put,
    _sync,
)

from trading_platform.api.app import create_app
from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.db.models import (
    JobStatus,
    OrderLifecycleState,
    PaperOrder,
    RecoveryRecord,
    StrategyRun,
    StrategyStatus,
)
from trading_platform.db.session import session_scope
from trading_platform.services.recovery import (
    GateCode,
    RecoveryClassification,
    UnresolvedReason,
    record_broker_statement,
    strategy_recovery_status,
)


@pytest.fixture()
def terminal_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    with migrated_database(monkeypatch, "terminal_states") as name:
        seed_calendar(date(2025, 11, 24), date(2026, 1, 9))
        yield name


@pytest.fixture()
def http(terminal_db: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED", "true")
    clear_settings_cache()
    with TestClient(create_app()) as client:
        yield client


def _arrange(builder: Any) -> Any:
    with session_scope(load_settings()) as session:
        return builder(session)


SHAPES = ("session", "continue")


def _flagged_job_with_run_and_no_orders(session: Any, shape: str) -> uuid.UUID:
    """A flagged Job whose own paper_execution run exists and left zero order rows."""

    if shape == "session":
        job = seed_job(session, completed_at=at(0))
    else:
        # A Continue Job: payload {mode: continue, operation_id}, no strategy_id. Its operation
        # has since been ended (End/expiry never releases the state).
        operation = seed_operation(
            session, strategy_id=OWNER, state="terminated", reason="execution_window_elapsed"
        )
        job = seed_job(
            session,
            strategy_id=None,
            completed_at=at(0),
            payload={"mode": "continue", "operation_id": str(operation.id)},
        )
    seed_paper_run(session, job, OWNER)
    return job.id


def _terminal_world(shape: str, *, owner: bool) -> uuid.UUID:
    """Quiet flat account, fresh clean reconciliation: A5 is the only check that can fail."""

    def build(session: Any) -> uuid.UUID:
        seed_quiet_account(session)
        if owner:
            strategy_row(session, OWNER).status = StrategyStatus.DISABLED
            session.flush()
            set_active_paper_strategy(session, OWNER)
        return _flagged_job_with_run_and_no_orders(session, shape)

    return _arrange(build)


def _job_level_records(job_id: uuid.UUID) -> list[RecoveryRecord]:
    with session_scope(load_settings()) as session:
        rows = list(
            session.execute(
                select(RecoveryRecord).where(
                    RecoveryRecord.job_id == job_id, RecoveryRecord.paper_order_id.is_(None)
                )
            ).scalars()
        )
        session.expunge_all()
        return rows


def _assert_terminal(job_id: uuid.UUID) -> None:
    with session_scope(load_settings()) as session:
        status = strategy_recovery_status(session, OWNER)
    (intent,) = status.intents
    assert intent.job_id == job_id and intent.intent_id is None
    assert intent.classification is RecoveryClassification.UNRESOLVED
    assert intent.unresolved_reason is UnresolvedReason.EXECUTION_PATH_UNPROVEN
    assert intent.blocking and not intent.resubmission_permitted
    assert status.gate_code is GateCode.OUTCOME_UNRESOLVED


def test_flagged_session_job_with_run_and_no_orders_is_terminal(
    terminal_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Syncs, a clean reconciliation completed after them and a month of elapsed time release nothing."""

    timeline = Timeline(monkeypatch, at(2))
    job_id = _terminal_world("session", owner=False)
    _assert_terminal(job_id)

    broker = LookupBroker()
    _sync(broker)
    timeline.advance(seconds=load_settings().execution.recovery_absence_grace_seconds + 1)
    _sync(broker)
    _arrange(lambda s: seed_clean_account_run(s, completed_at=at(120)))
    timeline.advance(days=30)

    _assert_terminal(job_id)
    assert _gate() is GateCode.OUTCOME_UNRESOLVED
    assert not _a5_passed()
    assert broker.post_count == 0 and _attempt_count() == 0


def test_flagged_continue_job_with_run_and_no_orders_is_terminal(
    terminal_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """run_paper_continuation creates its own paper_execution run, so a Continue Job that fails
    before registering or attempting anything (lease loss, a non-refusal exception) is in the same
    terminal state; it is attributed to its operation's strategy (SAF-05), not the account."""

    timeline = Timeline(monkeypatch, at(2))
    job_id = _terminal_world("continue", owner=False)
    _assert_terminal(job_id)
    assert _attempt_count() == 0

    broker = LookupBroker()
    _sync(broker)
    timeline.advance(seconds=load_settings().execution.recovery_absence_grace_seconds + 1)
    _sync(broker)
    _arrange(lambda s: seed_clean_account_run(s, completed_at=at(120)))
    timeline.advance(days=30)

    _assert_terminal(job_id)
    assert not _a5_passed()
    with session_scope(load_settings()) as session:
        # Another strategy is untouched: the Job is not account-level.
        strategy_row(session, OTHER)
        assert strategy_recovery_status(session, OTHER).gate_code is None
    assert broker.post_count == 0 and _attempt_count() == 0


@pytest.mark.parametrize("shape", SHAPES)
def test_job_level_record_is_appended_once_and_never_an_input(
    terminal_db: str, monkeypatch: pytest.MonkeyPatch, shape: str
) -> None:
    timeline = Timeline(monkeypatch, at(2))
    job_id = _terminal_world(shape, owner=False)
    assert _job_level_records(job_id) == []
    before = _gate()

    broker = LookupBroker()
    _sync(broker)
    timeline.advance(seconds=load_settings().execution.recovery_absence_grace_seconds + 1)
    _sync(broker)

    (record,) = _job_level_records(job_id)  # exactly one across both sync passes
    assert record.classification == "unresolved"
    assert record.unresolved_reason == "execution_path_unproven"
    # Audit only: the record changes no read.
    assert _gate() is before is GateCode.OUTCOME_UNRESOLVED
    _assert_terminal(job_id)


@pytest.mark.parametrize("shape", SHAPES)
def test_recovery_read_lists_the_execution_path_unproven_entry(
    http: TestClient, shape: str
) -> None:
    job_id = _terminal_world(shape, owner=False)

    first = http.get(f"/api/v1/jobs/{job_id}/recovery")
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["resolved"] is False and body["gate_code"] == "outcome_unresolved"
    (row,) = body["intents"]
    assert row["intent_id"] is None
    assert row["classification"] == "unresolved"
    assert row["unresolved_reason"] == "execution_path_unproven"
    assert row["blocking"] is True
    assert row["resubmission_permitted"] is False

    # The read releases nothing, with or without a later clean reconciliation.
    _arrange(lambda s: seed_account_run(s, completed_at=at(90)))
    second = http.get(f"/api/v1/jobs/{job_id}/recovery").json()
    assert second["resolved"] is False and second["intents"] == body["intents"]


def _legacy_order(*, flagged: bool) -> uuid.UUID:
    def build(session: Any) -> uuid.UUID:
        if flagged:
            _job, _run, order = seed_uncertain_session(
                session, status=PENDING, attempts=(), completed_at=at(0)
            )
            return order.id
        job = seed_job(session, uncertain=False, status=JobStatus.CANCELLED, completed_at=at(0))
        return seed_intent(session, seed_paper_run(session, job), status=PENDING, attempts=()).id

    return _arrange(build)


def _never_found_through_everything(
    monkeypatch: pytest.MonkeyPatch, order_id: uuid.UUID
) -> tuple[LookupBroker, Timeline]:
    timeline = Timeline(monkeypatch, at(2))
    broker = LookupBroker()  # a clean 404 for everything
    _sync(broker)
    timeline.advance(seconds=load_settings().execution.recovery_absence_grace_seconds + 1)
    _sync(broker)
    with session_scope(load_settings()) as session:
        record_broker_statement(
            session, order_id, "not_received", "ticket-1", "broker support: not received", "op"
        )
    timeline.advance(hours=2)
    _arrange(
        lambda s: (
            seed_snapshot(s, snapshot_at=at(60)),
            seed_clean_account_run(s, completed_at=at(90)),
        )
    )
    return broker, timeline


@pytest.mark.parametrize("flagged", [False, True], ids=["unflagged_job", "flagged_job"])
def test_legacy_never_found_order_is_terminal_through_evidence_statement_and_reconciliation(
    http: TestClient, monkeypatch: pytest.MonkeyPatch, flagged: bool
) -> None:
    order_id = _legacy_order(flagged=flagged)
    broker, timeline = _never_found_through_everything(monkeypatch, order_id)
    # Repeated syncs after the statement and the clean reconciliation change nothing either.
    _sync(broker)
    timeline.advance(days=30)
    _sync(broker)

    with session_scope(load_settings()) as session:
        status = strategy_recovery_status(session, OWNER)
        (intent,) = status.intents
    assert intent.absence_evidence_complete and intent.statement is not None
    assert intent.classification is RecoveryClassification.NOT_FOUND
    assert intent.blocking and intent.resubmission_permitted is False
    assert status.gate_code is GateCode.OUTCOME_UNRESOLVED
    assert not _a5_passed()

    with session_scope(load_settings()) as session:
        origin_job = session.execute(
            select(StrategyRun.job_id)
            .join(PaperOrder, PaperOrder.strategy_run_id == StrategyRun.id)
            .where(PaperOrder.id == order_id)
        ).scalar_one()
    body = http.get(f"/api/v1/jobs/{origin_job}/recovery").json()
    assert body["resolved"] is False and body["gate_code"] == "outcome_unresolved"
    (row,) = body["intents"]
    assert row["intent_id"] == str(order_id)
    assert row["classification"] == "not_found"
    assert row["blocking"] is True and row["resubmission_permitted"] is False
    (package,) = body["evidence_package"]
    assert package["complete"] is True and package["statement_recorded"] is True
    assert broker.post_count == 0 and _attempt_count() == 0


@pytest.mark.parametrize("flagged", [False, True], ids=["unflagged_job", "flagged_job"])
def test_legacy_order_resolves_only_when_the_broker_shows_it(
    terminal_db: str, monkeypatch: pytest.MonkeyPatch, flagged: bool
) -> None:
    order_id = _legacy_order(flagged=flagged)
    broker, timeline = _never_found_through_everything(monkeypatch, order_id)
    assert _gate() is GateCode.OUTCOME_UNRESOLVED and not _a5_passed()

    # The broker now shows the order: the next sync verifies it (D-07) and binds it.
    snapshot = _snapshot_for(order_id, broker_status="new")
    broker.lookup[snapshot.client_order_id] = snapshot
    timeline.advance(minutes=5)
    _sync(broker)

    with session_scope(load_settings()) as session:
        stored = session.get(PaperOrder, order_id)
        assert stored is not None and stored.broker_order_id == "b-found-1"
        assert stored.status is OrderLifecycleState.SUBMITTED
        status = strategy_recovery_status(session, OWNER)
        assert not [i for i in status.intents if i.blocking]
    # A fresh clean reconciliation completed after that sync resolves the strategy.
    _arrange(
        lambda s: (
            seed_snapshot(s, snapshot_at=at(300)),
            seed_clean_account_run(s, completed_at=at(310)),
        )
    )
    timeline.now = at(320)
    assert _gate() is None
    assert _a5_passed()
    assert broker.post_count == 0 and _attempt_count() == 0
    with session_scope(load_settings()) as session:
        assert session.execute(select(func.count()).select_from(PaperOrder)).scalar_one() == 1


@pytest.mark.parametrize("kind", list(_KINDS))
@pytest.mark.parametrize("state", ["session", "continue", "legacy"])
def test_terminal_states_refuse_ownership_changes(
    http: TestClient, monkeypatch: pytest.MonkeyPatch, state: str, kind: str
) -> None:
    owner, target = _KINDS[kind]
    Timeline(monkeypatch, at(2))
    if state == "legacy":

        def build(session: Any) -> None:
            seed_quiet_account(session)
            if owner:
                strategy_row(session, OWNER).status = StrategyStatus.DISABLED
                session.flush()
                set_active_paper_strategy(session, OWNER)
            job = seed_job(session, uncertain=False, status=JobStatus.CANCELLED, completed_at=at(0))
            seed_intent(session, seed_paper_run(session, job), status=PENDING, attempts=())

        _arrange(build)
    else:
        _terminal_world(state, owner=owner)
    before_owner = _owner_public_id()

    response = _put(http, target)

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["failed_checks"] == ["A5"], detail
    assert _owner_public_id() == before_owner


@pytest.mark.parametrize("kind", list(_KINDS))
def test_ownership_control_passes_without_the_terminal_job(http: TestClient, kind: str) -> None:
    """Control: the same world minus the flagged Job passes, so A5 above is the terminal state."""

    owner, target = _KINDS[kind]

    def build(session: Any) -> None:
        seed_quiet_account(session)
        if owner:
            strategy_row(session, OWNER).status = StrategyStatus.DISABLED
            session.flush()
            set_active_paper_strategy(session, OWNER)

    _arrange(build)
    response = _put(http, target)
    assert response.status_code == 200, response.text
