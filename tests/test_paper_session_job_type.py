"""Tests for the ``paper-session`` Job type (OPS-03, OPS-08, Phase 20 Plan 09).

Task 1 covers the service-level ``job_id`` threading behavior of
``run_paper_session``/``run_paper_order_submission`` (D-08/D-09). Task 2
(appended below) covers ``PaperSessionSubmissionSpec``/``PaperSessionJobHandler``.

Fixtures and fakes are reused from ``tests.test_paper_execution`` (the DB
migration fixture, ``FakeExecutionService``/``FakeBrokerClient`` and the
strategy/risk-batch/paper-order seed helpers) rather than duplicated --
``migrated_paper_db`` is re-exported via ``__all__`` following the
``tests/test_job_operations_e2e.py`` precedent so pytest resolves the fixture
from this module's namespace.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Mapping

import pytest
from sqlalchemy import select
from tests.support.calendar_facts import clock_at, et, seed_bars, seed_calendar
from tests.support.paper_eligibility import allow_paper_execution
from tests.support.paper_execution_seams import allow_direct_paper_execution
from tests.support.paper_ownership import seed_registered_strategy, set_active_paper_strategy
from tests.test_paper_execution import (
    ExplodingBrokerClient,
    FakeBrokerClient,
    FakeExecutionService,
    _seed_approved_risk_batch,
    _seed_existing_paper_order,
    migrated_paper_db,
)

from trading_platform.core.settings import load_settings
from trading_platform.db.models import Job, StrategyRun, StrategyRunType
from trading_platform.db.session import session_scope
from trading_platform.jobs.contracts import JobCancelledError, JobDomainConflictError
from trading_platform.jobs.handlers.paper_session import (
    STEP_RECORDING,
    STEP_RESOLVING,
    STEP_RUNNING,
    PaperSessionJobHandler,
)
from trading_platform.jobs.handlers.paper_session_submission import (
    PaperSessionPayloadRejection,
    PaperSessionSubmissionSpec,
    PaperSessionSubmitConflict,
)
from trading_platform.jobs.registry import (
    InvalidJobPayloadError,
    JobCancellationMode,
    JobRegistry,
    JobSubmissionConflictError,
    retry_prerequisite_for,
)
from trading_platform.services.alpaca import BrokerAccountSnapshot, BrokerOrderSnapshot
from trading_platform.services.concurrency_guard import ConcurrentRunLockedError
from trading_platform.services.config.validation import ExecutionMode
from trading_platform.services.evaluation_manifest import (
    ManifestVerification,
    ManifestVerificationStatus,
)
from trading_platform.services.execution import (
    ExecutionOrderStatus,
    OrderSide,
    PaperSessionRunReport,
    build_client_order_id,
    run_paper_session,
)
from trading_platform.services.operator_controls import OperatorControlService
from trading_platform.worker.commands.run_jobs import required_mode_preflight

__all__ = ["migrated_paper_db"]


@pytest.fixture(autouse=True)
def _eligible_paper_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    """This module's subject is not eligibility (COR-04): see
    tests/support/paper_eligibility.py. Real eligibility is tested in
    tests/test_paper_session_eligibility.py."""

    allow_paper_execution(monkeypatch)


def _seed_job(*, job_type: str = "paper-session") -> uuid.UUID:
    """FK-satisfying ``jobs`` row (``strategy_runs.job_id`` references
    ``jobs.id``, migration 0021) -- a bare ``uuid.uuid4()`` would violate the
    foreign key."""
    with session_scope(load_settings()) as session:
        job = Job(job_type=job_type, payload={})
        session.add(job)
        session.flush()
        return job.id


def _clean_broker_account() -> BrokerAccountSnapshot:
    return BrokerAccountSnapshot(
        cash=Decimal("100000.000000"),
        buying_power=Decimal("100000.000000"),
        equity=Decimal("100000.000000"),
        long_market_value=Decimal("0"),
        short_market_value=Decimal("0"),
        raw_payload={"equity": "100000.000000"},
    )


def test_run_paper_session_threads_job_id_to_both_created_runs(
    migrated_paper_db: str,
) -> None:
    risk_run_id, approved_event_ids = _seed_approved_risk_batch()
    settings = load_settings()
    _seed_existing_paper_order(
        risk_run_id=risk_run_id,
        risk_event_id=approved_event_ids["AAPL"],
        symbol="AAPL",
        session_date=date(2024, 1, 5),
        status="pending_submission",
        # 20.1-24 (authorized deviation, same as the 20.1-17 reseed in tests/test_paper_execution.py):
        # a broker-bound order (a previously verified identity) is ESTABLISHED and passes the D-15
        # session gate; a legacy zero-attempt order with no broker id is UNESTABLISHED and blocked.
        broker_order_id="recovered-aapl-001",
        broker_status=None,
    )
    execution_service = FakeExecutionService()
    job_id = _seed_job()

    report = run_paper_session(
        "trend_following_daily",
        as_of_session=date(2024, 1, 5),
        settings=settings,
        execution_service=execution_service,
        job_id=job_id,
        broker_client=FakeBrokerClient(
            orders=[
                BrokerOrderSnapshot(
                    broker_order_id="recovered-aapl-001",
                    client_order_id=build_client_order_id(
                        prefix=settings.execution.client_order_id_prefix,
                        strategy_id="trend_following_daily",
                        session_date=date(2024, 1, 5),
                        symbol="AAPL",
                        side=OrderSide.BUY,
                        quantity=Decimal("10.000000"),
                    ),
                    symbol="AAPL",
                    side=OrderSide.BUY,
                    quantity=Decimal("10.000000"),
                    status=ExecutionOrderStatus.PENDING,
                    broker_status="new",
                    submitted_at=datetime(2024, 1, 5, 14, 35, tzinfo=UTC),
                    filled_at=None,
                    canceled_at=None,
                    updated_at=datetime(2024, 1, 5, 14, 35, tzinfo=UTC),
                    raw_payload={"id": "recovered-aapl-001", "status": "new"},
                    created_at=datetime(2024, 1, 5, 14, 35, tzinfo=UTC),
                    order_type="market",
                )
            ],
            fills=[],
            positions=[],
            account=_clean_broker_account(),
        ),
    )

    assert report.action == "submitted_missing_orders"
    assert report.execution_run_id is not None
    assert report.reconciliation_run_id is not None

    with session_scope(settings) as session:
        job_linked_runs = (
            session.execute(select(StrategyRun).where(StrategyRun.job_id == job_id))
            .scalars()
            .all()
        )

    assert {run.id for run in job_linked_runs} == {
        uuid.UUID(report.reconciliation_run_id),
        uuid.UUID(report.execution_run_id),
    }
    assert {run.run_type for run in job_linked_runs} == {
        StrategyRunType.RECONCILIATION,
        StrategyRunType.PAPER_EXECUTION,
    }


def test_run_paper_session_blocked_strategy_disabled_threads_job_id_to_execution_run_only(
    migrated_paper_db: str,
) -> None:
    _seed_approved_risk_batch()
    settings = load_settings()
    execution_service = FakeExecutionService()
    control_service = OperatorControlService(settings=settings)
    control_service.disable_strategy(
        "trend_following_daily",
        reason="maintenance window",
        actor="pytest",
        trigger_source="pytest",
    )
    job_id = _seed_job()

    report = run_paper_session(
        "trend_following_daily",
        as_of_session=date(2024, 1, 5),
        settings=settings,
        execution_service=execution_service,
        broker_client=ExplodingBrokerClient(),
        trigger_source="pytest",
        job_id=job_id,
    )

    assert report.action == "blocked_strategy_disabled"
    assert report.reconciliation_run_id is None
    assert report.execution_run_id is not None

    with session_scope(settings) as session:
        job_linked_runs = (
            session.execute(select(StrategyRun).where(StrategyRun.job_id == job_id))
            .scalars()
            .all()
        )

    assert len(job_linked_runs) == 1
    assert job_linked_runs[0].id == uuid.UUID(report.execution_run_id)
    assert job_linked_runs[0].run_type == StrategyRunType.PAPER_EXECUTION


def test_run_paper_session_omitted_job_id_leaves_no_run_linked(
    migrated_paper_db: str,
) -> None:
    _seed_approved_risk_batch()
    settings = load_settings()
    execution_service = FakeExecutionService()

    report = run_paper_session(
        "trend_following_daily",
        as_of_session=date(2024, 1, 5),
        settings=settings,
        execution_service=execution_service,
    )

    assert report.action == "submitted_missing_orders"

    with session_scope(settings) as session:
        runs = session.execute(select(StrategyRun)).scalars().all()

    assert runs
    assert all(run.job_id is None for run in runs)


# ---------------------------------------------------------------------------
# PaperSessionSubmissionSpec (Task 2, spec half)
# ---------------------------------------------------------------------------

_VALID_PAYLOAD = {
    "strategy_id": "trend_following_daily",
    "as_of_session": "2024-01-10",
    "risk_run_id": None,
}


def test_paper_session_rejection_enum_is_closed() -> None:
    assert {member.value for member in PaperSessionPayloadRejection} == {
        "unknown_payload_keys",
        "missing_required_field",
        "invalid_field_type",
        "invalid_date",
        "unknown_strategy_id",
        "as_of_session_in_future",
        "as_of_session_not_trading_session",
        "as_of_session_out_of_calendar_range",
        "invalid_risk_run_id",
        "risk_run_not_eligible",
        # D-19 / 20.1-16: the Continue mode payload.
        "invalid_mode",
        "operation_id_required",
        "invalid_operation_id",
        "operation_id_forbidden_in_start_mode",
        "continue_forbids_start_fields",
        "operation_not_found",
    }


@pytest.mark.parametrize(
    ("payload", "expected_reason"),
    [
        pytest.param(
            {**_VALID_PAYLOAD, "extra": 1},
            PaperSessionPayloadRejection.UNKNOWN_PAYLOAD_KEYS,
            id="unknown_payload_keys",
        ),
        pytest.param(
            {"as_of_session": "2024-01-10", "risk_run_id": None},
            PaperSessionPayloadRejection.MISSING_REQUIRED_FIELD,
            id="missing_required_field_strategy_id",
        ),
        pytest.param(
            {"strategy_id": "trend_following_daily", "as_of_session": "2024-01-10"},
            PaperSessionPayloadRejection.MISSING_REQUIRED_FIELD,
            id="missing_required_field_risk_run_id_key_omitted",
        ),
        pytest.param(
            {**_VALID_PAYLOAD, "strategy_id": 123},
            PaperSessionPayloadRejection.INVALID_FIELD_TYPE,
            id="invalid_field_type",
        ),
        pytest.param(
            {**_VALID_PAYLOAD, "as_of_session": "2024-13-01"},
            PaperSessionPayloadRejection.INVALID_DATE,
            id="invalid_date",
        ),
        pytest.param(
            {**_VALID_PAYLOAD, "strategy_id": "nope"},
            PaperSessionPayloadRejection.UNKNOWN_STRATEGY_ID,
            id="unknown_strategy_id",
        ),
        pytest.param(
            {**_VALID_PAYLOAD, "as_of_session": "2026-01-06"},
            PaperSessionPayloadRejection.AS_OF_SESSION_IN_FUTURE,
            id="as_of_session_in_future",
        ),
        pytest.param(
            {**_VALID_PAYLOAD, "as_of_session": "2024-01-06"},
            PaperSessionPayloadRejection.AS_OF_SESSION_NOT_TRADING_SESSION,
            id="as_of_session_not_trading_session",
        ),
        pytest.param(
            {**_VALID_PAYLOAD, "as_of_session": "2000-01-03"},
            PaperSessionPayloadRejection.AS_OF_SESSION_OUT_OF_CALENDAR_RANGE,
            id="as_of_session_out_of_calendar_range_2000-01-03",
        ),
        pytest.param(
            {**_VALID_PAYLOAD, "as_of_session": "0001-01-01"},
            PaperSessionPayloadRejection.AS_OF_SESSION_OUT_OF_CALENDAR_RANGE,
            id="as_of_session_out_of_calendar_range_0001-01-01",
        ),
        pytest.param(
            {**_VALID_PAYLOAD, "risk_run_id": "not-a-uuid"},
            PaperSessionPayloadRejection.INVALID_RISK_RUN_ID,
            id="invalid_risk_run_id",
        ),
        # D-19 / 20.1-16: one case per Continue-mode rejection value (mode dispatch precedes the
        # start-field validation, so the ten start rejections above are unchanged).
        pytest.param(
            {**_VALID_PAYLOAD, "mode": "resume"},
            PaperSessionPayloadRejection.INVALID_MODE,
            id="invalid_mode",
        ),
        pytest.param(
            {"mode": "continue"},
            PaperSessionPayloadRejection.OPERATION_ID_REQUIRED,
            id="operation_id_required",
        ),
        pytest.param(
            {"mode": "continue", "operation_id": "not-a-uuid"},
            PaperSessionPayloadRejection.INVALID_OPERATION_ID,
            id="invalid_operation_id",
        ),
        pytest.param(
            {**_VALID_PAYLOAD, "operation_id": str(uuid.uuid4())},
            PaperSessionPayloadRejection.OPERATION_ID_FORBIDDEN_IN_START_MODE,
            id="operation_id_forbidden_in_start_mode",
        ),
        pytest.param(
            {"mode": "start", **_VALID_PAYLOAD, "operation_id": str(uuid.uuid4())},
            PaperSessionPayloadRejection.OPERATION_ID_FORBIDDEN_IN_START_MODE,
            id="operation_id_forbidden_in_explicit_start_mode",
        ),
        pytest.param(
            {"mode": "continue", "operation_id": str(uuid.uuid4()), "strategy_id": "trend_following_daily"},
            PaperSessionPayloadRejection.CONTINUE_FORBIDS_START_FIELDS,
            id="continue_forbids_strategy_id",
        ),
        pytest.param(
            {"mode": "continue", "operation_id": str(uuid.uuid4()), "as_of_session": "2024-01-10"},
            PaperSessionPayloadRejection.CONTINUE_FORBIDS_START_FIELDS,
            id="continue_forbids_as_of_session",
        ),
        pytest.param(
            {"mode": "continue", "operation_id": str(uuid.uuid4()), "risk_run_id": None},
            PaperSessionPayloadRejection.CONTINUE_FORBIDS_START_FIELDS,
            id="continue_forbids_risk_run_id",
        ),
        pytest.param(
            {"mode": "continue", "operation_id": str(uuid.uuid4()), "extra": 1},
            PaperSessionPayloadRejection.UNKNOWN_PAYLOAD_KEYS,
            id="continue_unknown_payload_keys",
        ),
    ],
)
def test_paper_session_validate_payload_rejects(
    payload: dict[str, Any], expected_reason: PaperSessionPayloadRejection
) -> None:
    spec = PaperSessionSubmissionSpec(
        load_settings(), clock=lambda: datetime(2026, 1, 6, 3, 0, tzinfo=UTC)
    )

    with pytest.raises(InvalidJobPayloadError) as exc_info:
        spec.validate_payload(payload)

    assert exc_info.value.job_type == "paper-session"
    assert exc_info.value.reason == expected_reason.value


def test_paper_session_submit_conflict_is_a_closed_set() -> None:
    assert {member.value for member in PaperSessionSubmitConflict} == {
        "no_active_paper_strategy",
        "strategy_not_active_paper_strategy",
        "historical_execution_rejected",
        "outside_execution_window",
        "evaluation_data_not_ready",
        "calendar_data_unavailable",
        "evaluation_data_changed",
        "strategy_settings_changed",
        # D-15 / 20.1-10: the three uncertain-outcome recovery gate codes. The exact-set pin
        # is extended (six + three recovery) because the recovery gate supersedes Phase 20
        # D-19 and raises through the same typed conflict.
        "outcome_unresolved",
        "reconciliation_required",
        "reconciliation_not_clean",
        # REC-02 / 20.1-15: the start-mode operation gates (precedence: operation_open ->
        # working_order_commitments_unaccounted -> risk_run_already_operated) and the S3-R4
        # evaluation_basis_unverified refusal (a fourth member beyond the plan's three,
        # because the S4 catalog lists it as a typed 409 at Job submit).
        "operation_open",
        "working_order_commitments_unaccounted",
        "risk_run_already_operated",
        "evaluation_basis_unverified",
        # D-19 / 20.1-16: the Continue-mode gates (working_order_commitments_unaccounted is shared
        # with the start-mode gate above).
        "operation_not_paused",
        "awaiting_reconciliation",
    }


@pytest.mark.parametrize(
    "gate_code",
    ["outcome_unresolved", "reconciliation_required", "reconciliation_not_clean"],
)
def test_paper_session_raises_each_recovery_gate_code_as_a_typed_conflict(
    migrated_paper_db: str, monkeypatch: pytest.MonkeyPatch, gate_code: str
) -> None:
    """D-15: one case per recovery code (the predicate itself is tested in
    tests/test_recovery_predicate.py; the HTTP shape in tests/test_recovery_gates.py)."""

    from trading_platform.services.recovery import GateCode

    seed_registered_strategy(load_settings(), "trend_following_daily", owner=True)

    class _Status:
        pass

    status = _Status()
    status.gate_code = GateCode(gate_code)  # type: ignore[attr-defined]
    monkeypatch.setattr(
        "trading_platform.jobs.handlers.paper_session_submission.strategy_recovery_status",
        lambda session, strategy_id: status,
    )
    spec = PaperSessionSubmissionSpec(load_settings())

    with pytest.raises(JobSubmissionConflictError) as exc_info:
        spec.validate_payload(
            {"strategy_id": "trend_following_daily", "as_of_session": "2024-01-10", "risk_run_id": None}
        )

    assert exc_info.value.code == gate_code
    assert exc_info.value.job_type == "paper-session"
    assert exc_info.value.detail["strategy_id"] == "trend_following_daily"
    assert exc_info.value.detail["required_job_type"] == "reconciliation"
    assert all(isinstance(value, str) for value in exc_info.value.detail.values())


@pytest.mark.parametrize(
    "gate_code",
    ["operation_open", "working_order_commitments_unaccounted", "risk_run_already_operated"],
)
def test_paper_session_raises_each_start_mode_operation_gate_as_a_typed_conflict(
    migrated_paper_db: str, gate_code: str
) -> None:
    """REC-02 / 20.1-15: one case per start-mode operation gate code (the precedence table and the
    S3-R4 evaluation_basis_unverified refusal are tested in tests/test_paper_session_operations.py)."""
    from tests.support.operation_fixtures import seed_operation, seed_risk_run
    from tests.support.recovery_fixtures import seed_intent, seed_paper_run

    from trading_platform.db.models import AttemptOutcomeClass, OrderLifecycleState

    settings = load_settings()
    seed_registered_strategy(settings, "trend_following_daily", owner=True)
    pinned: str | None = None
    with session_scope(settings) as session:
        if gate_code == "operation_open":
            seed_operation(session, state="paused", reason="awaiting_reconciliation")
        elif gate_code == "working_order_commitments_unaccounted":
            run = seed_paper_run(session, None)
            seed_intent(
                session,
                run,
                status=OrderLifecycleState.SUBMITTED,
                attempts=(AttemptOutcomeClass.ACCEPTED,),
                broker_order_id="working-1",
                broker_status="new",
            )
        else:
            risk_run = seed_risk_run(session, session_date=date(2024, 1, 10))
            seed_operation(
                session,
                state="terminated",
                reason="cancelled_by_operator",
                session_date=date(2024, 1, 10),
                risk_run=risk_run,
            )
            pinned = str(risk_run.id)
    spec = PaperSessionSubmissionSpec(settings)

    with pytest.raises(JobSubmissionConflictError) as exc_info:
        spec.validate_payload(
            {"strategy_id": "trend_following_daily", "as_of_session": "2024-01-10", "risk_run_id": pinned}
        )

    assert exc_info.value.code == gate_code
    assert exc_info.value.job_type == "paper-session"
    assert exc_info.value.detail["strategy_id"] == "trend_following_daily"
    assert all(isinstance(value, str) for value in exc_info.value.detail.values())
    if gate_code == "operation_open":
        assert exc_info.value.detail["next_action"] == "continue"
        assert "operation_id" in exc_info.value.detail


@pytest.mark.parametrize(
    ("status", "expected_code"),
    [
        pytest.param(
            ManifestVerificationStatus.EVALUATION_DATA_CHANGED,
            PaperSessionSubmitConflict.EVALUATION_DATA_CHANGED,
            id="evaluation_data_changed",
        ),
        pytest.param(
            ManifestVerificationStatus.STRATEGY_SETTINGS_CHANGED,
            PaperSessionSubmitConflict.STRATEGY_SETTINGS_CHANGED,
            id="strategy_settings_changed",
        ),
        pytest.param(
            ManifestVerificationStatus.MANIFEST_MISSING,
            PaperSessionSubmitConflict.EVALUATION_DATA_NOT_READY,
            id="manifest_missing",
        ),
    ],
)
def test_paper_session_maps_each_manifest_verification_status_to_its_typed_conflict(
    migrated_paper_db: str,
    monkeypatch: pytest.MonkeyPatch,
    status: ManifestVerificationStatus,
    expected_code: PaperSessionSubmitConflict,
) -> None:
    seed_registered_strategy(load_settings(), "trend_following_daily", owner=True)
    monkeypatch.setattr(
        "trading_platform.jobs.handlers.paper_session_submission.verify_risk_run_manifest",
        lambda **kwargs: ManifestVerification(status),
    )
    spec = PaperSessionSubmissionSpec(load_settings())

    with pytest.raises(JobSubmissionConflictError) as exc_info:
        spec.validate_payload(
            {"strategy_id": "trend_following_daily", "as_of_session": "2024-01-10", "risk_run_id": None}
        )

    assert exc_info.value.code == expected_code.value
    assert exc_info.value.detail["strategy_id"] == "trend_following_daily"
    assert exc_info.value.detail["as_of_session"] == "2024-01-10"
    assert all(isinstance(value, str) for value in exc_info.value.detail.values())


def test_paper_session_without_an_eligible_risk_run_is_evaluation_data_not_ready(
    migrated_paper_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_registered_strategy(load_settings(), "trend_following_daily", owner=True)
    monkeypatch.setattr(
        "trading_platform.jobs.handlers.paper_session_submission.latest_eligible_risk_run_id",
        lambda **kwargs: None,
    )
    spec = PaperSessionSubmissionSpec(load_settings())

    with pytest.raises(JobSubmissionConflictError) as exc_info:
        spec.validate_payload(
            {"strategy_id": "trend_following_daily", "as_of_session": "2024-01-10", "risk_run_id": None}
        )

    assert exc_info.value.code == "evaluation_data_not_ready"
    assert exc_info.value.detail["reason"] == "no_eligible_risk_run"


@pytest.mark.parametrize(
    ("conflict", "owner"),
    [
        pytest.param(
            PaperSessionSubmitConflict.NO_ACTIVE_PAPER_STRATEGY, None, id="no_active_paper_strategy"
        ),
        pytest.param(
            PaperSessionSubmitConflict.STRATEGY_NOT_ACTIVE_PAPER_STRATEGY,
            "donchian_breakout_daily",
            id="strategy_not_active_paper_strategy",
        ),
    ],
)
def test_paper_session_validate_payload_raises_each_ownership_conflict(
    migrated_paper_db: str, conflict: PaperSessionSubmitConflict, owner: str | None
) -> None:
    settings = load_settings()
    seed_registered_strategy(settings, "trend_following_daily")
    seed_registered_strategy(settings, "donchian_breakout_daily")
    set_active_paper_strategy(settings, owner)
    spec = PaperSessionSubmissionSpec(settings)

    with pytest.raises(JobSubmissionConflictError) as exc_info:
        spec.validate_payload(
            {"strategy_id": "trend_following_daily", "as_of_session": "2024-01-10", "risk_run_id": None}
        )

    assert exc_info.value.job_type == "paper-session"
    assert exc_info.value.code == conflict.value
    assert dict(exc_info.value.detail) == {"strategy_id": "trend_following_daily"}


def test_paper_session_validate_payload_normalizes_null_risk_run_id(migrated_paper_db: str) -> None:
    seed_registered_strategy(load_settings(), "trend_following_daily", owner=True)  # explicit owner (D-03)
    spec = PaperSessionSubmissionSpec(load_settings())

    normalized = spec.validate_payload(
        {
            "strategy_id": "  trend_following_daily  ",
            "as_of_session": "2024-01-10",
            "risk_run_id": None,
        }
    )

    assert normalized == {
        "strategy_id": "trend_following_daily",
        "as_of_session": "2024-01-10",
        "risk_run_id": None,
    }


def test_paper_session_validate_payload_normalizes_eligible_risk_run_id_to_canonical_lowercase(
    migrated_paper_db: str,
) -> None:
    risk_run_id, _approved_event_ids = _seed_approved_risk_batch(session_date=date(2024, 1, 5))
    spec = PaperSessionSubmissionSpec(load_settings())

    normalized = spec.validate_payload(
        {
            "strategy_id": "trend_following_daily",
            "as_of_session": "2024-01-05",
            "risk_run_id": str(risk_run_id).upper(),
        }
    )

    assert normalized["risk_run_id"] == str(risk_run_id).lower()


def test_paper_session_validate_payload_rejects_ineligible_risk_run_id(
    migrated_paper_db: str,
) -> None:
    # SUCCEEDED risk_evaluation run exists for 2024-01-05, but the payload
    # asks about a different (also valid trading) session -- ineligible.
    risk_run_id, _approved_event_ids = _seed_approved_risk_batch(session_date=date(2024, 1, 5))
    spec = PaperSessionSubmissionSpec(load_settings())

    with pytest.raises(InvalidJobPayloadError) as exc_info:
        spec.validate_payload(
            {
                "strategy_id": "trend_following_daily",
                "as_of_session": "2024-01-04",
                "risk_run_id": str(risk_run_id),
            }
        )

    assert exc_info.value.reason == PaperSessionPayloadRejection.RISK_RUN_NOT_ELIGIBLE.value


def test_paper_session_submission_defaults_none_without_sessions(
    migrated_paper_db: str,
) -> None:
    spec = PaperSessionSubmissionSpec(load_settings())

    assert spec.submission_defaults() is None


def test_paper_session_submission_defaults_are_the_candidate_and_omit_risk_run_id(
    migrated_paper_db: str,
) -> None:
    """D-24: candidate session, never "latest session with bars"; D-23: no risk_run_id."""

    seed_calendar(date(2025, 11, 20), date(2026, 3, 31))
    seed_bars(["AAA"], [date(2025, 12, 2)])
    spec = PaperSessionSubmissionSpec(load_settings(), clock=clock_at(et(2025, 12, 2, 10, 0)))

    defaults = spec.submission_defaults()

    assert defaults == {"as_of_session": "2025-12-01"}
    assert "risk_run_id" not in (defaults or {})


def test_defaults_are_none_when_calendar_unavailable(migrated_paper_db: str) -> None:
    seed_calendar(date(2026, 1, 2), date(2026, 3, 13))
    seed_bars(["AAA"], [date(2026, 3, 13)])
    spec = PaperSessionSubmissionSpec(load_settings(), clock=clock_at(et(2026, 9, 29, 10, 0)))

    assert spec.submission_defaults() is None


def test_paper_session_spec_satisfies_registry_contract() -> None:
    class _MinimalHandler:
        job_type = "paper-session"

        def run(self, context: object) -> Mapping[str, Any]:
            return {}

    registry = JobRegistry()
    registry.register(
        _MinimalHandler(), submission_spec=PaperSessionSubmissionSpec(load_settings())
    )

    assert registry.list_job_types() == ["paper-session"]


def test_paper_session_spec_declares_no_retry_prerequisite_but_is_recovery_gated() -> None:
    """Superseded by D-15 / 20.1-10: the reconcile-first retry prerequisite is replaced by the
    recovery gate inside validate_payload (the ``recovery_gated`` marker)."""

    from trading_platform.jobs.registry import recovery_gated_for

    spec = PaperSessionSubmissionSpec(load_settings())

    assert retry_prerequisite_for(spec) is None
    assert recovery_gated_for(spec) is True


def test_paper_session_cancellation_mode_is_queued_only() -> None:
    spec = PaperSessionSubmissionSpec(load_settings())

    assert spec.cancellation_mode is JobCancellationMode.QUEUED_ONLY
    assert (
        "Cancellable only while queued; once running, the session runs to completion."
        in spec.description
    )


# ---------------------------------------------------------------------------
# PaperSessionJobHandler (Task 2, handler half)
# ---------------------------------------------------------------------------


class _FakeContext:
    def __init__(
        self, *, job_id: uuid.UUID | None = None, payload: Mapping[str, Any] | None = None
    ) -> None:
        self.job_id = job_id or uuid.uuid4()
        self.job_type = "paper-session"
        self.payload = payload or dict(_VALID_PAYLOAD)
        self.progress_calls: list[dict[str, Any]] = []
        self.log_calls: list[dict[str, Any]] = []
        self.cancelled = False

    def report_progress(self, *, percent=None, step=None, current=None, total=None) -> None:
        call: dict[str, Any] = {}
        if percent is not None:
            call["percent"] = percent
        if step is not None:
            call["step"] = step
        if current is not None:
            call["current"] = current
        if total is not None:
            call["total"] = total
        self.progress_calls.append(call)

    def log(self, *, level: str, event_code: str, message: str, context=None) -> None:
        self.log_calls.append(
            {"level": level, "event_code": event_code, "message": message, "context": context}
        )

    def is_cancellation_requested(self) -> bool:
        return self.cancelled

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise JobCancelledError(self.job_id)


def _fake_report(**overrides: Any) -> PaperSessionRunReport:
    defaults: dict[str, Any] = {
        "strategy_id": "trend_following_daily",
        "session_date": "2024-01-10",
        "trigger_source": "job",
        "source_risk_run_id": str(uuid.uuid4()),
        "action": "submitted_missing_orders",
        "execution_run_id": str(uuid.uuid4()),
        "execution_status": "succeeded",
        "result_summary": {},
        "reconciliation_run_id": str(uuid.uuid4()),
    }
    defaults.update(overrides)
    return PaperSessionRunReport(**defaults)


def test_paper_session_handler_passes_job_id_trigger_source_and_null_risk_run_id_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def _fake_run_paper_session(strategy_id: str, **kwargs: Any) -> PaperSessionRunReport:
        captured["strategy_id"] = strategy_id
        captured.update(kwargs)
        return _fake_report()

    import trading_platform.jobs.handlers.paper_session as paper_session_module

    monkeypatch.setattr(paper_session_module, "run_paper_session", _fake_run_paper_session)

    context = _FakeContext(payload={**_VALID_PAYLOAD, "risk_run_id": None})
    handler = PaperSessionJobHandler()
    handler.run(context)

    assert captured["job_id"] == context.job_id
    assert captured["trigger_source"] == "job"
    assert captured["risk_run_id"] is None
    assert captured["as_of_session"] == date(2024, 1, 10)


def test_paper_session_handler_passes_risk_run_id_string_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def _fake_run_paper_session(strategy_id: str, **kwargs: Any) -> PaperSessionRunReport:
        captured.update(kwargs)
        return _fake_report()

    import trading_platform.jobs.handlers.paper_session as paper_session_module

    monkeypatch.setattr(paper_session_module, "run_paper_session", _fake_run_paper_session)

    risk_run_id = str(uuid.uuid4())
    context = _FakeContext(payload={**_VALID_PAYLOAD, "risk_run_id": risk_run_id})
    handler = PaperSessionJobHandler()
    handler.run(context)

    assert captured["risk_run_id"] == risk_run_id


def test_paper_session_handler_progress_steps_have_no_percent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import trading_platform.jobs.handlers.paper_session as paper_session_module

    monkeypatch.setattr(
        paper_session_module,
        "run_paper_session",
        lambda strategy_id, **kwargs: _fake_report(),
    )

    context = _FakeContext()
    handler = PaperSessionJobHandler()
    handler.run(context)

    assert context.progress_calls == [
        {"step": STEP_RESOLVING},
        {"step": STEP_RUNNING},
        {"step": STEP_RECORDING},
    ]
    for call in context.progress_calls:
        assert "percent" not in call


def test_paper_session_handler_logs_external_marker_before_service_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D-19: the external_* marker must be recorded BEFORE run_paper_session
    is invoked, so any later handler_error is recorded outcome_uncertain."""

    order: list[str] = []

    class _OrderedContext(_FakeContext):
        def log(self, *, level: str, event_code: str, message: str, context=None) -> None:
            order.append(f"log:{event_code}")
            super().log(level=level, event_code=event_code, message=message, context=context)

    def _fake_run_paper_session(strategy_id: str, **kwargs: Any) -> PaperSessionRunReport:
        order.append("service_called")
        return _fake_report()

    import trading_platform.jobs.handlers.paper_session as paper_session_module

    monkeypatch.setattr(paper_session_module, "run_paper_session", _fake_run_paper_session)

    context = _OrderedContext()
    handler = PaperSessionJobHandler()
    handler.run(context)

    assert order == [
        "log:external_broker_session_started",
        "service_called",
        "log:paper_session_completed",
    ]


def test_paper_session_handler_never_checks_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D-02/D-03: queued-only cancellation -- a RUNNING paper-session Job is
    never cancellable, so this handler must never call
    ``raise_if_cancelled``. Proven both at the source level (no call exists)
    and at runtime (a cancellation-requested context still runs the service
    call to completion instead of raising)."""

    import inspect

    import trading_platform.jobs.handlers.paper_session as paper_session_module

    source = inspect.getsource(paper_session_module)
    assert "raise_if_cancelled" not in source

    call_count = 0

    def _fake_run_paper_session(strategy_id: str, **kwargs: Any) -> PaperSessionRunReport:
        nonlocal call_count
        call_count += 1
        return _fake_report()

    monkeypatch.setattr(paper_session_module, "run_paper_session", _fake_run_paper_session)

    context = _FakeContext()
    context.cancelled = True
    handler = PaperSessionJobHandler()

    result = handler.run(context)

    assert call_count == 1
    assert result["action"]


def test_paper_session_handler_translates_concurrent_run_locked_error_to_domain_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(strategy_id: str, **kwargs: Any) -> PaperSessionRunReport:
        raise ConcurrentRunLockedError("trend_following_daily", date(2024, 1, 5))

    import trading_platform.jobs.handlers.paper_session as paper_session_module

    monkeypatch.setattr(paper_session_module, "run_paper_session", _raise)

    context = _FakeContext()
    handler = PaperSessionJobHandler()

    with pytest.raises(JobDomainConflictError) as exc_info:
        handler.run(context)

    assert "trend_following_daily" in str(exc_info.value)
    assert "2024-01-05" in str(exc_info.value)


def test_paper_session_handler_returns_blocked_report_as_normal_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D-05: a blocked_* report is a normal successful result -- the handler
    never raises or branches on report.action."""

    report = _fake_report(
        action="blocked_strategy_disabled",
        execution_status="failed",
        reconciliation_run_id=None,
    )

    import trading_platform.jobs.handlers.paper_session as paper_session_module

    monkeypatch.setattr(
        paper_session_module, "run_paper_session", lambda strategy_id, **kwargs: report
    )

    context = _FakeContext()
    handler = PaperSessionJobHandler()
    result = handler.run(context)

    assert result["action"] == "blocked_strategy_disabled"
    assert result["reconciliation_run_id"] is None


def test_paper_session_handler_result_summary_shape_and_produced_run_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = _fake_report(
        action="submitted_missing_orders",
        execution_run_id="exec-1",
        execution_status="succeeded",
        reconciliation_run_id="recon-1",
        source_risk_run_id="risk-1",
    )

    import trading_platform.jobs.handlers.paper_session as paper_session_module

    monkeypatch.setattr(
        paper_session_module, "run_paper_session", lambda strategy_id, **kwargs: report
    )

    context = _FakeContext()
    handler = PaperSessionJobHandler()
    result = handler.run(context)

    assert result == {
        "action": "submitted_missing_orders",
        "strategy_id": report.strategy_id,
        "as_of_session": "2024-01-10",
        "source_risk_run_id": "risk-1",
        "execution_run_id": "exec-1",
        "execution_status": "succeeded",
        "reconciliation_run_id": "recon-1",
        "produced_run_ids": ["recon-1", "exec-1"],
    }
    json.dumps(result)


def test_paper_session_handler_produced_run_ids_omits_none_execution_run_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = _fake_report(
        action="blocked_reconciliation",
        execution_run_id=None,
        execution_status=None,
        reconciliation_run_id="recon-2",
    )

    import trading_platform.jobs.handlers.paper_session as paper_session_module

    monkeypatch.setattr(
        paper_session_module, "run_paper_session", lambda strategy_id, **kwargs: report
    )

    context = _FakeContext()
    handler = PaperSessionJobHandler()
    result = handler.run(context)

    assert result["produced_run_ids"] == ["recon-2"]


def test_paper_session_handler_declares_paper_mode() -> None:
    # D-22 (P19): `paper-session` requires PAPER-level config (broker
    # credentials), matching its `reconciliation` sibling (20-08). Whether
    # broker credentials happen to be configured in the running environment
    # is out of this unit test's scope; this test only pins the declared
    # mode and that the handler is recognized as a mode-declaring handler at
    # all (a non-None result either way -- valid config returns None,
    # invalid config returns a message -- both prove
    # `required_mode_preflight` evaluated PAPER, not the "no
    # required_execution_mode declared" failure-closed branch).
    assert PaperSessionJobHandler.required_execution_mode is ExecutionMode.PAPER
    preflight_result = required_mode_preflight(PaperSessionJobHandler())
    assert preflight_result is None or "paper mode" in preflight_result


def test_paper_session_handler_never_writes_run_status() -> None:
    import inspect

    import trading_platform.jobs.handlers.paper_session as paper_session_module

    source = inspect.getsource(paper_session_module)
    assert "StrategyRunStatus" not in source
    assert "percent=" not in source


@pytest.fixture(autouse=True)
def _direct_paper_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    """20.1-15: this module's subject is not the per-intent permission check or the S1 guard
    (tests/test_operation_permission.py, tests/test_paper_session_operations.py): see
    tests/support/paper_execution_seams.py."""

    allow_direct_paper_execution(monkeypatch)


# ---------------------------------------------------------------------------
# Continue mode (D-19, 20.1-16)
# ---------------------------------------------------------------------------


def test_start_normalization_is_byte_identical_and_never_contains_mode(migrated_paper_db: str) -> None:
    """A payload without ``mode`` and one with ``mode: 'start'`` normalize to exactly the three
    start keys (golden dict); ``mode`` is never written into the normalized start payload."""

    seed_registered_strategy(load_settings(), "trend_following_daily", owner=True)
    spec = PaperSessionSubmissionSpec(load_settings())
    golden = {"strategy_id": "trend_following_daily", "as_of_session": "2024-01-10", "risk_run_id": None}

    assert spec.validate_payload(dict(_VALID_PAYLOAD)) == golden
    assert spec.validate_payload({**_VALID_PAYLOAD, "mode": "start"}) == golden
    assert list(spec.validate_payload({**_VALID_PAYLOAD, "mode": "start"})) == list(golden)


def test_continue_for_an_unknown_operation_is_a_422_payload_rejection(migrated_paper_db: str) -> None:
    seed_registered_strategy(load_settings(), "trend_following_daily", owner=True)
    spec = PaperSessionSubmissionSpec(load_settings())

    with pytest.raises(InvalidJobPayloadError) as exc_info:
        spec.validate_payload({"mode": "continue", "operation_id": str(uuid.uuid4())})

    assert exc_info.value.reason == PaperSessionPayloadRejection.OPERATION_NOT_FOUND.value


def _seed_continue_operation(session: Any, *, state: str, reason: str | None) -> uuid.UUID:
    from tests.support.operation_fixtures import seed_operation

    return seed_operation(session, state=state, reason=reason).id


@pytest.mark.parametrize(
    "gate_code",
    ["operation_not_paused", "working_order_commitments_unaccounted", "awaiting_reconciliation"],
)
def test_paper_session_raises_each_continue_mode_gate_as_a_typed_conflict(
    migrated_paper_db: str, gate_code: str
) -> None:
    """D-19: one case per Continue-mode gate code (the precedence and the run-time re-check are
    tested in tests/test_paper_session_operations.py)."""
    from tests.support.recovery_fixtures import seed_intent, seed_paper_run

    from trading_platform.db.models import AttemptOutcomeClass, OrderLifecycleState

    settings = load_settings()
    seed_registered_strategy(settings, "trend_following_daily", owner=True)
    with session_scope(settings) as session:
        if gate_code == "operation_not_paused":
            operation_id = _seed_continue_operation(
                session, state="requires_reevaluation", reason="evaluation_data_changed"
            )
        else:
            operation_id = _seed_continue_operation(
                session, state="paused", reason="awaiting_reconciliation"
            )
            if gate_code == "working_order_commitments_unaccounted":
                run = seed_paper_run(session, None)
                seed_intent(
                    session,
                    run,
                    status=OrderLifecycleState.SUBMITTED,
                    attempts=(AttemptOutcomeClass.ACCEPTED,),
                    broker_order_id="working-1",
                    broker_status="new",
                )
    spec = PaperSessionSubmissionSpec(settings)

    with pytest.raises(JobSubmissionConflictError) as exc_info:
        spec.validate_payload({"mode": "continue", "operation_id": str(operation_id)})

    assert exc_info.value.code == gate_code
    assert exc_info.value.job_type == "paper-session"
    assert exc_info.value.detail["strategy_id"] == "trend_following_daily"
    assert all(isinstance(value, str) for value in exc_info.value.detail.values())
    if gate_code == "operation_not_paused":
        assert exc_info.value.detail["operation_state"] == "requires_reevaluation"


def test_continue_normalization_is_the_mode_and_the_canonical_operation_id(
    migrated_paper_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.support.operation_fixtures import seed_operation

    from trading_platform.jobs.handlers import paper_session_submission as submission_module

    settings = load_settings()
    seed_registered_strategy(settings, "trend_following_daily", owner=True)
    with session_scope(settings) as session:
        operation_id = seed_operation(session, state="paused", reason="awaiting_reconciliation").id
    # The reconciliation gate has its own case above; here the precondition holds.
    monkeypatch.setattr(submission_module, "continue_precheck", lambda *a, **k: None)
    spec = PaperSessionSubmissionSpec(settings)

    normalized = spec.validate_payload(
        {"mode": "continue", "operation_id": f"  {str(operation_id).upper()}  "}
    )

    assert normalized == {"mode": "continue", "operation_id": str(operation_id)}


def test_paper_session_handler_runs_continue_mode_through_run_paper_continuation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D-19 (20.1-16): a continue-mode Job reads no strategy or session from its payload; the run
    reports them back and the result keeps the additive ``operation`` object."""
    captured: dict[str, Any] = {}
    operation_id = uuid.uuid4()

    def _fake_continuation(operation: uuid.UUID, **kwargs: Any) -> PaperSessionRunReport:
        captured["operation_id"] = operation
        captured.update(kwargs)
        return _fake_report(
            action="continued_session",
            result_summary={"operation": {"id": str(operation), "state": "paused", "reason": "price_unavailable"}},
        )

    import trading_platform.jobs.handlers.paper_session as paper_session_module

    monkeypatch.setattr(paper_session_module, "run_paper_continuation", _fake_continuation)
    context = _FakeContext(payload={"mode": "continue", "operation_id": str(operation_id)})

    result = PaperSessionJobHandler().run(context)

    assert captured["operation_id"] == operation_id
    assert captured["job_id"] == context.job_id and captured["trigger_source"] == "job"
    assert result["action"] == "continued_session"
    assert result["as_of_session"] == "2024-01-10"
    assert result["operation"]["state"] == "paused"
    started = next(c for c in context.log_calls if c["event_code"] == "external_broker_session_started")
    assert started["context"]["mode"] == "continue"
