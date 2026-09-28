"""ReconciliationSubmissionSpec + ReconciliationJobHandler tests (OPS-04,
D-01, D-06, D-21, D-22, D-25).

Mirrors ``tests.test_risk_evaluation_job_type`` function-by-function,
substituting the queued-only cancellation contract (D-01/D-02: no
``raise_if_cancelled`` checkpoint anywhere in the handler -- a cancel
request against a RUNNING reconciliation Job is rejected upstream, never
observed here) and the report-only service call
(``reconcile_paper_execution``, never ``apply_reconciliation_corrections``,
RECON-04/D-06). Reuses the same Postgres fixtures for the
``submission_defaults`` tests that touch the database.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
from typing import Any, Mapping

import pytest
from tests.test_backtest_runner import (
    _seed_market_data,
    migrated_backtest_db,
    strategy_config_override,
)

from trading_platform.core.settings import load_settings
from trading_platform.jobs.contracts import JobCancelledError
from trading_platform.jobs.handlers.reconciliation import (
    STEP_RECONCILING,
    STEP_RECORDING,
    STEP_RESOLVING,
    ReconciliationJobHandler,
)
from trading_platform.jobs.handlers.reconciliation_submission import (
    ReconciliationPayloadRejection,
    ReconciliationSubmissionSpec,
)
from trading_platform.jobs.registry import (
    InvalidJobPayloadError,
    JobCancellationMode,
    JobRegistry,
    retry_prerequisite_for,
)
from trading_platform.services.config.validation import ExecutionMode
from trading_platform.services.market_data_access import latest_completed_session
from trading_platform.services.reconciliation.report import ReconciliationReport
from trading_platform.worker.commands.run_jobs import required_mode_preflight

__all__ = ["migrated_backtest_db", "strategy_config_override"]

_VALID_PAYLOAD = {
    "strategy_id": "trend_following_daily",
    "as_of_session": "2024-01-10",
}


# ---------------------------------------------------------------------------
# ReconciliationSubmissionSpec (Task 1, spec half)
# ---------------------------------------------------------------------------


def test_rejection_enum_is_closed() -> None:
    assert {member.value for member in ReconciliationPayloadRejection} == {
        "unknown_payload_keys",
        "missing_required_field",
        "invalid_field_type",
        "invalid_date",
        "unknown_strategy_id",
        "as_of_session_in_future",
        "as_of_session_not_trading_session",
        "as_of_session_out_of_calendar_range",
    }


@pytest.mark.parametrize(
    ("payload", "expected_reason"),
    [
        pytest.param(
            {**_VALID_PAYLOAD, "extra": 1},
            ReconciliationPayloadRejection.UNKNOWN_PAYLOAD_KEYS,
            id="unknown_payload_keys",
        ),
        pytest.param(
            {"strategy_id": "trend_following_daily"},
            ReconciliationPayloadRejection.MISSING_REQUIRED_FIELD,
            id="missing_required_field",
        ),
        pytest.param(
            {**_VALID_PAYLOAD, "strategy_id": 123},
            ReconciliationPayloadRejection.INVALID_FIELD_TYPE,
            id="invalid_field_type",
        ),
        pytest.param(
            {**_VALID_PAYLOAD, "as_of_session": "2024-13-01"},
            ReconciliationPayloadRejection.INVALID_DATE,
            id="invalid_date",
        ),
        pytest.param(
            {**_VALID_PAYLOAD, "strategy_id": "nope"},
            ReconciliationPayloadRejection.UNKNOWN_STRATEGY_ID,
            id="unknown_strategy_id",
        ),
        pytest.param(
            {**_VALID_PAYLOAD, "as_of_session": "2026-01-06"},
            ReconciliationPayloadRejection.AS_OF_SESSION_IN_FUTURE,
            id="as_of_session_in_future",
        ),
        pytest.param(
            {**_VALID_PAYLOAD, "as_of_session": "2024-01-06"},
            ReconciliationPayloadRejection.AS_OF_SESSION_NOT_TRADING_SESSION,
            id="as_of_session_not_trading_session",
        ),
        pytest.param(
            {**_VALID_PAYLOAD, "as_of_session": "2000-01-03"},
            ReconciliationPayloadRejection.AS_OF_SESSION_OUT_OF_CALENDAR_RANGE,
            id="as_of_session_out_of_calendar_range_2000-01-03",
        ),
        pytest.param(
            {**_VALID_PAYLOAD, "as_of_session": "0001-01-01"},
            ReconciliationPayloadRejection.AS_OF_SESSION_OUT_OF_CALENDAR_RANGE,
            id="as_of_session_out_of_calendar_range_0001-01-01",
        ),
    ],
)
def test_validate_payload_rejects(
    payload: dict[str, Any], expected_reason: ReconciliationPayloadRejection
) -> None:
    spec = ReconciliationSubmissionSpec(
        load_settings(), clock=lambda: datetime(2026, 1, 6, 3, 0, tzinfo=UTC)
    )

    with pytest.raises(InvalidJobPayloadError) as exc_info:
        spec.validate_payload(payload)

    assert exc_info.value.job_type == "reconciliation"
    assert exc_info.value.reason == expected_reason.value


def test_validate_payload_normalizes() -> None:
    spec = ReconciliationSubmissionSpec(load_settings())

    normalized = spec.validate_payload(
        {"strategy_id": "  trend_following_daily  ", "as_of_session": "2024-01-10"}
    )

    assert normalized == {
        "strategy_id": "trend_following_daily",
        "as_of_session": "2024-01-10",
    }


def test_submission_defaults_none_without_sessions(migrated_backtest_db: str) -> None:
    spec = ReconciliationSubmissionSpec(load_settings())

    assert spec.submission_defaults() is None


def test_submission_defaults_from_latest_session(migrated_backtest_db: str) -> None:
    _seed_market_data()
    settings = load_settings()
    spec = ReconciliationSubmissionSpec(settings)

    from trading_platform.db.session import session_scope

    with session_scope(settings) as session:
        latest = latest_completed_session(session, exchange=settings.market_data.calendar.exchange)
    assert latest is not None

    assert spec.submission_defaults() == {"as_of_session": latest.isoformat()}


def test_spec_satisfies_registry_contract() -> None:
    class _MinimalHandler:
        job_type = "reconciliation"

        def run(self, context: object) -> Mapping[str, Any]:
            return {}

    registry = JobRegistry()
    registry.register(
        _MinimalHandler(), submission_spec=ReconciliationSubmissionSpec(load_settings())
    )

    assert registry.list_job_types() == ["reconciliation"]


def test_spec_declares_no_retry_prerequisite() -> None:
    spec = ReconciliationSubmissionSpec(load_settings())

    assert retry_prerequisite_for(spec) is None


def test_cancellation_mode_is_queued_only() -> None:
    spec = ReconciliationSubmissionSpec(load_settings())

    assert spec.cancellation_mode is JobCancellationMode.QUEUED_ONLY
    assert "queued" in spec.description.lower()


# ---------------------------------------------------------------------------
# ReconciliationJobHandler (Task 1, handler half)
# ---------------------------------------------------------------------------


class _FakeContext:
    def __init__(self, *, job_id: uuid.UUID | None = None, payload: Mapping[str, Any] | None = None) -> None:
        self.job_id = job_id or uuid.uuid4()
        self.job_type = "reconciliation"
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


def _fake_report(**overrides: Any) -> ReconciliationReport:
    defaults: dict[str, Any] = {
        "run_id": str(uuid.uuid4()),
        "strategy_id": "trend_following_daily",
        "session_date": "2024-01-10",
        "checked_at": "2024-01-10T00:05:00+00:00",
        "finding_count": 0,
        "blocking_count": 0,
        "recovered_order_count": 0,
        "blocks_execution": False,
        "findings": (),
    }
    defaults.update(overrides)
    return ReconciliationReport(**defaults)


def test_handler_passes_job_id_and_job_trigger_source(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_reconcile_paper_execution(strategy_id: str, **kwargs: Any) -> ReconciliationReport:
        captured["strategy_id"] = strategy_id
        captured.update(kwargs)
        return _fake_report()

    import trading_platform.jobs.handlers.reconciliation as reconciliation_module

    monkeypatch.setattr(
        reconciliation_module, "reconcile_paper_execution", _fake_reconcile_paper_execution
    )

    context = _FakeContext()
    handler = ReconciliationJobHandler()
    handler.run(context)

    assert captured["job_id"] == context.job_id
    assert captured["trigger_source"] == "job"
    assert captured["as_of_session"] == date(2024, 1, 10)


def test_handler_progress_steps_have_no_percent(monkeypatch: pytest.MonkeyPatch) -> None:
    import trading_platform.jobs.handlers.reconciliation as reconciliation_module

    monkeypatch.setattr(
        reconciliation_module,
        "reconcile_paper_execution",
        lambda strategy_id, **kwargs: _fake_report(),
    )

    context = _FakeContext()
    handler = ReconciliationJobHandler()
    handler.run(context)

    assert context.progress_calls == [
        {"step": STEP_RESOLVING},
        {"step": STEP_RECONCILING},
        {"step": STEP_RECORDING},
    ]
    for call in context.progress_calls:
        assert "percent" not in call


def test_handler_never_checks_cancellation(monkeypatch: pytest.MonkeyPatch) -> None:
    """D-01/D-02: queued-only cancellation -- a RUNNING reconciliation Job is
    never cancellable, so this handler must never call
    ``raise_if_cancelled``. Proven both at the source level (no call exists)
    and at runtime (a cancellation-requested context still runs the service
    call to completion instead of raising)."""

    import inspect

    import trading_platform.jobs.handlers.reconciliation as reconciliation_module

    source = inspect.getsource(reconciliation_module)
    assert "raise_if_cancelled" not in source

    call_count = 0

    def _fake_reconcile_paper_execution(strategy_id: str, **kwargs: Any) -> ReconciliationReport:
        nonlocal call_count
        call_count += 1
        return _fake_report()

    monkeypatch.setattr(
        reconciliation_module, "reconcile_paper_execution", _fake_reconcile_paper_execution
    )

    context = _FakeContext()
    context.cancelled = True
    handler = ReconciliationJobHandler()

    result = handler.run(context)

    assert call_count == 1
    assert result["run_id"]


def test_result_summary_carries_run_id(monkeypatch: pytest.MonkeyPatch) -> None:
    report = _fake_report(finding_count=2, blocking_count=1, blocks_execution=True)

    import trading_platform.jobs.handlers.reconciliation as reconciliation_module

    monkeypatch.setattr(
        reconciliation_module, "reconcile_paper_execution", lambda strategy_id, **kwargs: report
    )

    context = _FakeContext()
    handler = ReconciliationJobHandler()
    result = handler.run(context)

    assert result == {
        "run_id": report.run_id,
        "produced_run_ids": [report.run_id],
        "strategy_id": report.strategy_id,
        "as_of_session": "2024-01-10",
        "finding_count": 2,
        "blocking_count": 1,
        "blocks_execution": True,
    }
    json.dumps(result)


def test_service_failure_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(strategy_id: str, **kwargs: Any) -> ReconciliationReport:
        raise RuntimeError("boom")

    import trading_platform.jobs.handlers.reconciliation as reconciliation_module

    monkeypatch.setattr(reconciliation_module, "reconcile_paper_execution", _raise)

    context = _FakeContext()
    handler = ReconciliationJobHandler()

    with pytest.raises(RuntimeError):
        handler.run(context)


def test_handler_declares_paper_mode() -> None:
    # D-22 (P19): `reconciliation` requires PAPER-level config (broker
    # credentials), unlike the BACKTEST-level `backtest`/`risk-evaluation`
    # siblings. Whether broker credentials happen to be configured in the
    # running environment is out of this unit test's scope (see
    # tests/test_job_runner_preflight.py for the CONFIG_INVALID contract);
    # this test only pins the declared mode and that the handler is
    # recognized as a mode-declaring handler at all (a non-None result
    # either way -- valid config returns None, invalid config returns a
    # message -- both prove `required_mode_preflight` evaluated PAPER, not
    # the "no required_execution_mode declared" failure-closed branch).
    assert ReconciliationJobHandler.required_execution_mode is ExecutionMode.PAPER
    preflight_result = required_mode_preflight(ReconciliationJobHandler())
    assert preflight_result is None or "paper mode" in preflight_result


def test_handler_never_writes_run_status_or_calls_corrections() -> None:
    import inspect

    import trading_platform.jobs.handlers.reconciliation as reconciliation_module

    source = inspect.getsource(reconciliation_module)
    assert "StrategyRunStatus" not in source
    assert "_update_reconciliation_run" not in source
    assert "apply_reconciliation_corrections" not in source
    assert "percent=" not in source
