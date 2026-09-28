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
from tests.test_paper_execution import (
    ExplodingBrokerClient,
    FakeBrokerClient,
    FakeExecutionService,
    _seed_approved_risk_batch,
    _seed_existing_paper_order,
    _seed_market_data,
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
)
from trading_platform.jobs.registry import (
    InvalidJobPayloadError,
    JobCancellationMode,
    JobRegistry,
    retry_prerequisite_for,
)
from trading_platform.services.alpaca import BrokerAccountSnapshot, BrokerOrderSnapshot
from trading_platform.services.concurrency_guard import ConcurrentRunLockedError
from trading_platform.services.config.validation import ExecutionMode
from trading_platform.services.execution import (
    ExecutionOrderStatus,
    OrderSide,
    PaperSessionRunReport,
    build_client_order_id,
    run_paper_session,
)
from trading_platform.services.market_data_access import latest_completed_session
from trading_platform.services.operator_controls import OperatorControlService
from trading_platform.worker.commands.run_jobs import required_mode_preflight

__all__ = ["migrated_paper_db"]


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
        broker_order_id=None,
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
        "invalid_risk_run_id",
        "risk_run_not_eligible",
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
            {**_VALID_PAYLOAD, "risk_run_id": "not-a-uuid"},
            PaperSessionPayloadRejection.INVALID_RISK_RUN_ID,
            id="invalid_risk_run_id",
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


def test_paper_session_validate_payload_normalizes_null_risk_run_id() -> None:
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


def test_paper_session_submission_defaults_from_latest_session_omits_risk_run_id(
    migrated_paper_db: str,
) -> None:
    _seed_market_data(date(2024, 1, 5))
    settings = load_settings()
    spec = PaperSessionSubmissionSpec(settings)

    with session_scope(settings) as session:
        latest = latest_completed_session(session, exchange=settings.market_data.calendar.exchange)
    assert latest is not None

    defaults = spec.submission_defaults()

    assert defaults == {"as_of_session": latest.isoformat()}
    assert "risk_run_id" not in (defaults or {})


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


def test_paper_session_spec_declares_reconciliation_retry_prerequisite() -> None:
    spec = PaperSessionSubmissionSpec(load_settings())

    assert retry_prerequisite_for(spec) == "reconciliation"


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
