"""One shared classification of 'was this order sent?' for every consumer (20.1-17).

Closes VERIFICATION gap SC4/REC-01 (W-3 / W-4) and REVIEW SAF-01: the run-time send guard (G2),
the takeover rule, basis verification and the recovery predicate (A5, the Continue gate, R3, the
per-intent permission check) all read ``attempts.classify_submission_evidence``.

The pure table comes first; the DB-backed consumer-agreement, A5, R3, SAF-01, recovery-flow
and 29 Sep tests follow it. Recovery of an unestablished order is exercised ONLY through the
supported sync / reconciliation flow (a broker lookup by client_order_id bound by
``apply_broker_order``, evidence, a fresh clean standalone reconciliation); nothing here re-POSTs
and nothing releases an intent by hand (R3 is a read).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
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
from tests.support.operation_fixtures import seed_operation, seed_operation_job
from tests.support.paper_ownership import set_active_paper_strategy
from tests.support.recovery_fixtures import (
    OTHER,
    OWNER,
    SESSION_DATE,
    at,
    seed_account_run,
    seed_intent,
    seed_job,
    seed_operation_bound_intent,
    seed_paper_run,
    seed_uncertain_session,
    strategy_row,
)
from tests.test_recovery_predicate import LookupBroker, Timeline, _snapshot_for

from trading_platform.api.app import create_app
from trading_platform.core import clock
from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.db.models import (
    AttemptOutcomeClass,
    ExecutionOperationIntent,
    JobStatus,
    OrderLifecycleState,
    OrderSubmissionAttempt,
    PaperOrder,
    StrategyStatus,
)
from trading_platform.db.session import session_scope
from trading_platform.jobs.handlers.paper_session_submission import PaperSessionSubmissionSpec
from trading_platform.jobs.registry import JobSubmissionConflictError
from trading_platform.services.execution import operations as ops
from trading_platform.services.execution.attempts import (
    AttemptRecord,
    SubmissionEvidence,
    attempt_log_registered,
    classify_submission_evidence,
    load_submission_attempts,
    proven_not_sent,
    reached_or_may_have_reached_broker,
)
from trading_platform.services.execution.intent_identity import (
    load_strategy_order_facts,
    unestablished_orders,
)
from trading_platform.services.paper_account_checks import _check_a5
from trading_platform.services.recovery import (
    GateCode,
    RecoveryClassification,
    UnresolvedReason,
    account_recovery_status,
    record_broker_statement,
    strategy_recovery_status,
)

PRE = AttemptOutcomeClass.PRE_CONNECTION
DEADLINE = AttemptOutcomeClass.DEADLINE_EXPIRED
AMBIGUOUS = AttemptOutcomeClass.AMBIGUOUS
ACCEPTED = AttemptOutcomeClass.ACCEPTED
DUPLICATE = AttemptOutcomeClass.DUPLICATE_REPORTED
REJECTED_ATTEMPT = AttemptOutcomeClass.REJECTED

PENDING = OrderLifecycleState.PENDING_SUBMISSION
FAILED = OrderLifecycleState.SUBMISSION_FAILED
UNKNOWN = OrderLifecycleState.UNKNOWN
FILLED = OrderLifecycleState.FILLED
REJECTED_STATUS = OrderLifecycleState.REJECTED

NOW = datetime(2026, 1, 1, tzinfo=UTC)


class _Order:
    def __init__(self, status: OrderLifecycleState, broker_order_id: str | None = None) -> None:
        self.status = status
        self.broker_order_id = broker_order_id


def _history(*outcomes: AttemptOutcomeClass | None) -> list[AttemptRecord]:
    return [
        AttemptRecord(
            attempt_number=index + 1,
            started_at=NOW,
            completed_at=None if outcome is None else NOW,
            outcome_class=outcome,
        )
        for index, outcome in enumerate(outcomes)
    ]


# (status, broker_order_id, outcomes, attempt_log_registered, expected verdict)
_TABLE: list[tuple[OrderLifecycleState, str | None, tuple[Any, ...], bool, SubmissionEvidence]] = [
    (FILLED, None, (), True, SubmissionEvidence.BROKER_EVIDENCE),
    (PENDING, "b-1", (), True, SubmissionEvidence.BROKER_EVIDENCE),
    (FAILED, "b-1", (PRE,), True, SubmissionEvidence.BROKER_EVIDENCE),
    (REJECTED_STATUS, None, (), False, SubmissionEvidence.REJECTED),
    (FAILED, None, (REJECTED_ATTEMPT,), True, SubmissionEvidence.REJECTED),
    (PENDING, None, (PRE, DEADLINE), False, SubmissionEvidence.PROVEN_NOT_SENT),
    (FAILED, None, (PRE,), True, SubmissionEvidence.PROVEN_NOT_SENT),
    (UNKNOWN, None, (PRE,), True, SubmissionEvidence.PROVEN_NOT_SENT),
    (PENDING, None, (AMBIGUOUS,), True, SubmissionEvidence.UNESTABLISHED),
    (FAILED, None, (None,), True, SubmissionEvidence.UNESTABLISHED),
    (FAILED, None, (PRE, AMBIGUOUS), True, SubmissionEvidence.UNESTABLISHED),
    (FAILED, None, (DUPLICATE,), True, SubmissionEvidence.UNESTABLISHED),
    (FAILED, None, (ACCEPTED,), True, SubmissionEvidence.UNESTABLISHED),
    (PENDING, None, (), True, SubmissionEvidence.PROVEN_NOT_SENT),
    (FAILED, None, (), True, SubmissionEvidence.PROVEN_NOT_SENT),  # SAF-01 B
    (UNKNOWN, None, (), True, SubmissionEvidence.UNESTABLISHED),
    (PENDING, None, (), False, SubmissionEvidence.UNESTABLISHED),  # legacy
    (FAILED, None, (), False, SubmissionEvidence.UNESTABLISHED),  # legacy, TL-4
    (UNKNOWN, None, (), False, SubmissionEvidence.UNESTABLISHED),
]


def test_submission_evidence_is_a_closed_set_of_four() -> None:
    assert {member.value for member in SubmissionEvidence} == {
        "broker_evidence",
        "rejected",
        "proven_not_sent",
        "unestablished",
    }


@pytest.mark.parametrize(("status", "broker_id", "outcomes", "registered", "expected"), _TABLE)
def test_classify_submission_evidence_table(
    status: OrderLifecycleState,
    broker_id: str | None,
    outcomes: tuple[Any, ...],
    registered: bool,
    expected: SubmissionEvidence,
) -> None:
    verdict = classify_submission_evidence(
        status=status,
        broker_order_id=broker_id,
        attempts=_history(*outcomes),
        attempt_log_registered=registered,
    )
    assert verdict is expected
    # A plain string status (the recovery SQL returns text) classifies identically.
    assert (
        classify_submission_evidence(
            status=status.value,
            broker_order_id=broker_id,
            attempts=_history(*outcomes),
            attempt_log_registered=registered,
        )
        is expected
    )


def test_attempt_log_registered_rule() -> None:
    first = NOW
    # Any attempt row registers the order.
    assert attempt_log_registered(
        has_attempts=True, order_created_at=NOW, first_intent_created_at=None
    )
    # No pinned intent ever referenced it: legacy.
    assert not attempt_log_registered(
        has_attempts=False, order_created_at=NOW, first_intent_created_at=None
    )
    # Created before the first intent row that references it: legacy reuse.
    assert not attempt_log_registered(
        has_attempts=False,
        order_created_at=first - timedelta(seconds=1),
        first_intent_created_at=first,
    )
    # At or after the first intent row: operation-bound.
    assert attempt_log_registered(
        has_attempts=False, order_created_at=first, first_intent_created_at=first
    )
    assert attempt_log_registered(
        has_attempts=False,
        order_created_at=first + timedelta(seconds=1),
        first_intent_created_at=first,
    )
    # Naive and aware values compare without raising (both are read as UTC).
    assert attempt_log_registered(
        has_attempts=False,
        order_created_at=first.replace(tzinfo=None),
        first_intent_created_at=first,
    )


@pytest.mark.parametrize(("status", "broker_id", "outcomes", "registered", "expected"), _TABLE)
def test_proven_not_sent_is_the_shared_classifier(
    status: OrderLifecycleState,
    broker_id: str | None,
    outcomes: tuple[Any, ...],
    registered: bool,
    expected: SubmissionEvidence,
) -> None:
    order = _Order(status, broker_id)
    attempts = _history(*outcomes)
    proven = proven_not_sent(order, attempts, attempt_log_registered=registered)  # type: ignore[arg-type]
    assert proven is (expected is SubmissionEvidence.PROVEN_NOT_SENT)
    assert reached_or_may_have_reached_broker(
        order,  # type: ignore[arg-type]
        attempts,
        attempt_log_registered=registered,
    ) is (not proven)
    assert proven_not_sent(None, attempts, attempt_log_registered=registered)


# ===========================================================================
# DB-backed consumer agreement
# ===========================================================================

THIRD = "rsi_mean_reversion_daily"
URL = "/api/v1/controls/active-paper-strategy"


@pytest.fixture()
def shared_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    with migrated_database(monkeypatch, "shared_classifier") as name:
        seed_calendar(date(2025, 11, 24), date(2026, 1, 9))
        yield name


@pytest.fixture()
def http(shared_db: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED", "true")
    clear_settings_cache()
    with TestClient(create_app()) as client:
        yield client


def _arrange(builder: Any) -> Any:
    with session_scope(load_settings()) as session:
        return builder(session)


def _gate(strategy_id: str = OWNER) -> GateCode | None:
    with session_scope(load_settings()) as session:
        return strategy_recovery_status(session, strategy_id).gate_code


def _a5_passed() -> bool:
    with session_scope(load_settings()) as session:
        return _check_a5(session, now=clock.now_utc()).passed


def _seed_shape(
    session: Any,
    *,
    status: OrderLifecycleState,
    outcomes: tuple[AttemptOutcomeClass | None, ...],
    bound: bool,
    flagged: bool,
) -> tuple[uuid.UUID, uuid.UUID]:
    job = (
        seed_job(session, completed_at=at(0))
        if flagged
        else seed_job(session, uncertain=False, status=JobStatus.CANCELLED, completed_at=at(0))
    )
    run = seed_paper_run(session, job)
    if bound:
        order = seed_operation_bound_intent(session, run, status=status, attempts=outcomes)
    else:
        order = seed_intent(session, run, status=status, attempts=outcomes)
    return job.id, order.id


E = SubmissionEvidence
# (id, status, outcomes, bound, flagged, expected verdict)
_SHAPES = [
    ("S1", PENDING, (), False, False, E.UNESTABLISHED),
    ("S2", PENDING, (), False, True, E.UNESTABLISHED),
    ("S3", FAILED, (), False, False, E.UNESTABLISHED),
    ("S4", PENDING, (), True, True, E.PROVEN_NOT_SENT),
    ("S5", FAILED, (), True, True, E.PROVEN_NOT_SENT),
    ("S6", PENDING, (), True, False, E.PROVEN_NOT_SENT),
    ("S7", FAILED, (AMBIGUOUS,), True, False, E.UNESTABLISHED),
    ("S8", PENDING, (AMBIGUOUS,), True, False, E.UNESTABLISHED),
    ("S9", FAILED, (ACCEPTED,), True, False, E.UNESTABLISHED),
    ("S10", FAILED, (DUPLICATE,), True, False, E.UNESTABLISHED),
    ("S11", PENDING, (None,), True, False, E.UNESTABLISHED),
    ("S12", FAILED, (PRE, DEADLINE), True, False, E.PROVEN_NOT_SENT),
    ("S13", UNKNOWN, (), True, True, E.UNESTABLISHED),
    ("S14", UNKNOWN, (PRE,), True, False, E.PROVEN_NOT_SENT),
    ("S15", REJECTED_STATUS, (REJECTED_ATTEMPT,), True, False, E.REJECTED),
]


@pytest.mark.parametrize(
    ("shape", "status", "outcomes", "bound", "flagged", "expected"),
    _SHAPES,
    ids=[shape[0] for shape in _SHAPES],
)
def test_every_consumer_agrees_with_the_shared_classifier(
    shared_db: str,
    shape: str,
    status: OrderLifecycleState,
    outcomes: tuple[AttemptOutcomeClass | None, ...],
    bound: bool,
    flagged: bool,
    expected: SubmissionEvidence,
) -> None:
    job_id, order_id = _arrange(
        lambda s: _seed_shape(s, status=status, outcomes=outcomes, bound=bound, flagged=flagged)
    )
    unestablished = expected is SubmissionEvidence.UNESTABLISHED
    # A listed-but-not-unestablished intent (the UNKNOWN liveness rule) still awaits a reconciliation.
    awaits_reconciliation = flagged or status is OrderLifecycleState.UNKNOWN
    expected_gate = (
        GateCode.OUTCOME_UNRESOLVED
        if unestablished
        else (GateCode.RECONCILIATION_REQUIRED if awaits_reconciliation else None)
    )

    with session_scope(load_settings()) as session:
        order = session.get(PaperOrder, order_id)
        assert order is not None
        attempts = load_submission_attempts(session, order_id)
        if bound:
            row = session.execute(
                select(ExecutionOperationIntent).where(
                    ExecutionOperationIntent.paper_order_id == order_id
                )
            ).scalar_one()
            fact = ops.load_intent_facts(session, [row.operation_id])[row.operation_id][0]
            registered = fact.attempt_log_registered
            assert registered, f"{shape}: an operation-bound order is attempt-log registered"
        else:
            fact = None
            registered = False
        # (i) the shared function itself
        verdict = classify_submission_evidence(
            status=order.status,
            broker_order_id=order.broker_order_id,
            attempts=attempts,
            attempt_log_registered=registered,
        )
        assert verdict is expected, f"{shape}: classify_submission_evidence"

        # (ii) the recovery predicate (flagged and unflagged branch alike)
        status_read = strategy_recovery_status(session, OWNER)
        blocking_ids = {i.intent_id for i in status_read.intents if i.blocking}
        assert (order_id in blocking_ids) is unestablished, f"{shape}: predicate intent"
        assert status_read.gate_code is expected_gate, f"{shape}: strategy gate"

        # (iii) A5 reads the same predicate
        account = account_recovery_status(session)
        assert account.resolved is (expected_gate is None), f"{shape}: account status"
        a5 = _check_a5(session, now=clock.now_utc())
        assert a5.passed is (expected_gate is None), f"{shape}: A5"

        # (v) run-time G2 / basis verification
        facts = load_strategy_order_facts(session, OWNER)
        assert (order_id in {f.paper_order_id for f in unestablished_orders(facts)}) is (
            unestablished
        ), f"{shape}: G2 unestablished_orders"

        # (vi) the takeover / authorize_send fact
        if fact is not None:
            assert fact.proven_not_sent is (expected is SubmissionEvidence.PROVEN_NOT_SENT), (
                f"{shape}: IntentFact.proven_not_sent"
            )

    # (iv) the paper-session submit / Continue gate
    spec = PaperSessionSubmissionSpec(load_settings())
    if expected_gate is None:
        spec._require_recovery_resolved(strategy_id=OWNER, as_of_session=SESSION_DATE)
    else:
        with pytest.raises(JobSubmissionConflictError) as refused:
            spec._require_recovery_resolved(strategy_id=OWNER, as_of_session=SESSION_DATE)
        assert refused.value.code == expected_gate.value, f"{shape}: submit gate"

    # (vii) a fresh clean account reconciliation after the latest Job resolves everything that is
    # established or proven not sent, and nothing that is unestablished.
    _arrange(lambda s: seed_account_run(s, completed_at=at(60)))
    assert _gate() is (GateCode.OUTCOME_UNRESOLVED if unestablished else None), (
        f"{shape}: after a clean reconciliation"
    )
    assert _a5_passed() is (not unestablished)


# ---------------------------------------------------------------------------
# A5 refuses seeding, handover and release for both gap shapes; R3 lists them
# ---------------------------------------------------------------------------


def _gap_world(shape: str, *, with_order: bool, owner: bool) -> uuid.UUID:
    """Quiet, flat account with a fresh clean reconciliation (every check but A5 passes)."""

    def build(session: Any) -> uuid.UUID:
        seed_quiet_account(session)
        if owner:
            strategy_row(session, OWNER).status = StrategyStatus.DISABLED
            session.flush()
            set_active_paper_strategy(session, OWNER)
        job = seed_job(session, uncertain=False, status=JobStatus.CANCELLED, completed_at=at(0))
        if with_order:
            run = seed_paper_run(session, job)
            if shape == "gap_a":  # W-3: legacy zero-attempt pending order
                seed_intent(session, run, status=PENDING, attempts=())
            else:  # W-4: ambiguous, unparked, on an unflagged Job
                seed_operation_bound_intent(session, run, status=FAILED, attempts=(AMBIGUOUS,))
        return job.id

    return _arrange(build)


def _put(client: TestClient, strategy_id: str | None) -> Any:
    return client.put(URL, json={"strategy_id": strategy_id, "reason": "operator choice"})


def _owner_public_id() -> str | None:
    from trading_platform.db.models import ActivePaperStrategy, Strategy

    with session_scope(load_settings()) as session:
        return session.execute(
            select(Strategy.strategy_id)
            .join(ActivePaperStrategy, ActivePaperStrategy.strategy_id == Strategy.id)
            .where(ActivePaperStrategy.id == 1)
        ).scalar_one_or_none()


_KINDS = {
    "seeding": (False, OTHER),
    "handover": (True, THIRD),
    "release": (True, None),
}


@pytest.mark.parametrize("kind", list(_KINDS))
@pytest.mark.parametrize("shape", ["gap_a", "gap_b"])
def test_a5_refuses_seeding_handover_and_release_for_both_gap_shapes(
    http: TestClient, shape: str, kind: str
) -> None:
    owner, target = _KINDS[kind]
    _gap_world(shape, with_order=True, owner=owner)
    before_owner = _owner_public_id()

    response = _put(http, target)

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["failed_checks"] == ["A5"], detail
    assert detail["code"] == "check_failed:A5"
    assert _owner_public_id() == before_owner  # the singleton is unchanged


@pytest.mark.parametrize("kind", list(_KINDS))
def test_a5_control_without_the_order_passes(http: TestClient, kind: str) -> None:
    owner, target = _KINDS[kind]
    _gap_world("gap_a", with_order=False, owner=owner)

    response = _put(http, target)

    assert response.status_code == 200, response.text
    assert response.json()["kind"] == kind


@pytest.mark.parametrize("shape", ["gap_a", "gap_b"])
def test_recovery_read_lists_both_gap_shapes(http: TestClient, shape: str) -> None:
    job_id = _gap_world(shape, with_order=True, owner=False)
    with session_scope(load_settings()) as session:
        order_id = session.execute(select(PaperOrder.id)).scalar_one()

    response = http.get(f"/api/v1/jobs/{job_id}/recovery")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["resolved"] is False
    assert body["gate_code"] == "outcome_unresolved"
    (row,) = body["intents"]
    assert row["intent_id"] == str(order_id)
    assert row["blocking"] is True
    assert row["classification"] == "not_found"
    assert row["resubmission_permitted"] is False
    (package,) = body["evidence_package"]
    assert package["intent_id"] == str(order_id)


def test_recovery_read_is_read_only_evidence_and_never_releases_an_intent(
    http: TestClient,
) -> None:
    job_id = _gap_world("gap_a", with_order=True, owner=False)
    first = http.get(f"/api/v1/jobs/{job_id}/recovery").json()
    # Even with a fresh clean reconciliation afterwards, the read releases nothing.
    _arrange(lambda s: seed_account_run(s, completed_at=at(90)))
    second = http.get(f"/api/v1/jobs/{job_id}/recovery").json()
    assert first["gate_code"] == second["gate_code"] == "outcome_unresolved"
    assert second["intents"][0]["blocking"] is True


# ---------------------------------------------------------------------------
# SAF-01: an operation-bound unsent order on a FLAGGED Job
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status",
    [PENDING, FAILED],
    ids=["A_lease_loss_pending", "B_non_refusal_exception_submission_failed"],
)
def test_saf01_operation_bound_unsent_intent_on_flagged_job_is_sendable_after_clean_reconciliation(
    shared_db: str, status: OrderLifecycleState
) -> None:
    def build(session: Any) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
        flagged = seed_job(session, completed_at=at(0))  # FAILED, outcome_uncertain
        run = seed_paper_run(session, flagged)
        executor = seed_operation_job(
            session,
            status=JobStatus.RUNNING,
            lease_owner="worker-1",
            lease_expires_at=datetime(2100, 1, 1, tzinfo=UTC),
        )
        operation = seed_operation(
            session,
            state="running",
            reason=None,
            epoch=3,
            executor_job=executor,
            jobs=[(executor, "start")],
        )
        order = seed_operation_bound_intent(
            session, run, status=status, attempts=(), operation=operation
        )
        intent_id = session.execute(
            select(ExecutionOperationIntent.id).where(
                ExecutionOperationIntent.paper_order_id == order.id
            )
        ).scalar_one()
        return operation.id, intent_id, executor.id

    operation_id, intent_id, executor_id = _arrange(build)

    # Before reconciliation: only the missing fresh reconciliation gates (never outcome_unresolved).
    assert _gate() is GateCode.RECONCILIATION_REQUIRED
    assert not _a5_passed()

    _arrange(lambda s: seed_account_run(s, completed_at=at(30)))

    assert _gate() is None
    assert _a5_passed()
    PaperSessionSubmissionSpec(load_settings())._require_recovery_resolved(
        strategy_id=OWNER, as_of_session=SESSION_DATE
    )
    auth = ops.authorize_send(operation_id, intent_id, 3, executor_id, lease_owner="worker-1")
    assert auth.attempt_number == 1
    with session_scope(load_settings()) as session:
        assert (
            session.execute(select(func.count()).select_from(OrderSubmissionAttempt)).scalar_one()
            == 1
        )


# ---------------------------------------------------------------------------
# Recovery of a LEGACY zero-attempt order: only through sync / reconciliation, never a re-POST
# ---------------------------------------------------------------------------


def _legacy_pending(*, flagged: bool) -> tuple[uuid.UUID, uuid.UUID]:
    def build(session: Any) -> tuple[uuid.UUID, uuid.UUID]:
        if flagged:
            job, _run, order = seed_uncertain_session(
                session, status=PENDING, attempts=(), completed_at=at(0)
            )
            return job.id, order.id
        job = seed_job(session, uncertain=False, status=JobStatus.CANCELLED, completed_at=at(0))
        order = seed_intent(session, seed_paper_run(session, job), status=PENDING, attempts=())
        return job.id, order.id

    return _arrange(build)


def _sync(broker: LookupBroker) -> None:
    from trading_platform.services.execution.sync_orders import sync_account_state

    sync_account_state(settings=load_settings(), broker_client=broker)


def _attempt_count() -> int:
    with session_scope(load_settings()) as session:
        return session.execute(
            select(func.count()).select_from(OrderSubmissionAttempt)
        ).scalar_one()


@pytest.mark.parametrize("flagged", [True, False], ids=["flagged_job", "unflagged_job"])
def test_legacy_pending_order_found_by_the_broker_lookup_is_bound_and_never_reposted(
    shared_db: str, monkeypatch: pytest.MonkeyPatch, flagged: bool
) -> None:
    """FOUND branch: the supported sync looks the order up by client_order_id, verifies its
    identity (D-07), binds it with ``apply_broker_order`` and the strategy resolves; nothing is
    sent and no attempt row is created."""

    _job_id, order_id = _legacy_pending(flagged=flagged)
    Timeline(monkeypatch, at(2))
    assert _gate() is GateCode.OUTCOME_UNRESOLVED
    assert not _a5_passed()

    snapshot = _snapshot_for(order_id, broker_status="new")
    broker = LookupBroker(lookup={snapshot.client_order_id: snapshot})
    _sync(broker)

    assert broker.post_count == 0 and broker.lookup_calls == 1
    assert _attempt_count() == 0  # never re-POSTed: the legacy order still has no attempt row
    with session_scope(load_settings()) as session:
        stored = session.get(PaperOrder, order_id)
        assert stored is not None
        assert stored.broker_order_id == "b-found-1"
        assert stored.status is OrderLifecycleState.SUBMITTED
    if flagged:
        # Established, but the uncertain Job still needs the fresh clean standalone reconciliation.
        assert _gate() is GateCode.RECONCILIATION_REQUIRED
        assert not _a5_passed()
        _arrange(lambda s: seed_account_run(s, completed_at=at(30)))
    assert _gate() is None
    assert _a5_passed()
    assert broker.post_count == 0


def test_legacy_pending_order_with_a_mismatching_lookup_stays_unresolved(
    shared_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-07: a lookup result for the same client_order_id with another quantity is NOT applied."""

    _job_id, order_id = _legacy_pending(flagged=False)
    Timeline(monkeypatch, at(2))
    wrong = _snapshot_for(order_id, quantity="999")
    broker = LookupBroker(lookup={wrong.client_order_id: wrong})
    _sync(broker)

    with session_scope(load_settings()) as session:
        status = strategy_recovery_status(session, OWNER)
        (intent,) = status.intents
        stored = session.get(PaperOrder, order_id)
        assert stored is not None and stored.broker_order_id is None
        assert stored.status is OrderLifecycleState.PENDING_SUBMISSION
    assert intent.classification is RecoveryClassification.UNRESOLVED
    assert intent.unresolved_reason is UnresolvedReason.ID_MISMATCH
    assert status.gate_code is GateCode.OUTCOME_UNRESOLVED
    assert broker.post_count == 0 and _attempt_count() == 0


@pytest.mark.parametrize("kind", list(_KINDS))
def test_legacy_pending_order_never_found_stays_unresolved_and_ownership_stays_blocked(
    http: TestClient, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    """NOT-FOUND branch: absence evidence (two lookups a grace period apart), a clean account
    reconciliation, elapsed time and a broker not_received statement are all evidence only: the
    legacy order stays UNRESOLVED and A5 refuses seeding, handover and release."""

    owner, target = _KINDS[kind]
    timeline = Timeline(monkeypatch, at(2))
    _gap_world("gap_a", with_order=True, owner=owner)
    with session_scope(load_settings()) as session:
        order_id = session.execute(select(PaperOrder.id)).scalar_one()
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

    with session_scope(load_settings()) as session:
        status = strategy_recovery_status(session, OWNER, now=timeline.now + timedelta(days=30))
        (intent,) = status.intents
    assert intent.absence_evidence_complete and intent.statement is not None
    assert intent.classification is RecoveryClassification.NOT_FOUND
    assert intent.resubmission_permitted is False
    assert status.gate_code is GateCode.OUTCOME_UNRESOLVED

    response = _put(http, target)

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["failed_checks"] == ["A5"]
    assert broker.post_count == 0 and _attempt_count() == 0


# ---------------------------------------------------------------------------
# Regression: the 29 Sep carry-over is not captured by the shared classifier
# ---------------------------------------------------------------------------


def test_29_sep_carry_over_is_not_captured_by_the_shared_classifier(shared_db: str) -> None:
    def build(session: Any) -> None:
        seed_snapshot(session, snapshot_at=at(0))
        seed_clean_account_run(session, completed_at=at(-10))
        seed_job(session, job_type="paper-session", completed_at=at(1))
        seed_job(session, job_type="paper-session", completed_at=at(2))
        seed_job(session, job_type="broker-order-sync", completed_at=at(3))

    _arrange(build)

    with session_scope(load_settings()) as session:
        status = strategy_recovery_status(session, OWNER)
        assert session.execute(select(func.count()).select_from(PaperOrder)).scalar_one() == 0
    assert len(status.intents) == 3
    assert all(
        i.intent_id is None and i.classification is RecoveryClassification.NOTHING_SUBMITTED
        for i in status.intents
    )
    assert status.gate_code is GateCode.RECONCILIATION_REQUIRED
    assert not _a5_passed()

    _arrange(lambda s: seed_clean_account_run(s, completed_at=at(30)))

    assert _gate() is None
    assert _a5_passed()
