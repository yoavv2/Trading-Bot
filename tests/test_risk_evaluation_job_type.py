"""RiskEvaluationSubmissionSpec + RiskEvaluationJobHandler tests (OPS-02,
D-21, D-22, D-25, P19 D-08..D-13/D-16).

Mirrors ``tests.test_backtest_job_type`` function-by-function, substituting
the ``risk-evaluation`` Job type's single strategy-scoped ``as_of_session``
field for ``backtest``'s date range. Reuses the same Postgres fixtures for
the ``submission_defaults`` tests that touch the database.
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
from trading_platform.jobs.handlers.risk_evaluation import (
    STEP_EVALUATING,
    STEP_RECORDING,
    STEP_RESOLVING,
    RiskEvaluationJobHandler,
)
from trading_platform.jobs.handlers.risk_evaluation_submission import (
    RiskEvaluationPayloadRejection,
    RiskEvaluationSubmissionSpec,
)
from trading_platform.jobs.registry import (
    InvalidJobPayloadError,
    JobRegistry,
    retry_prerequisite_for,
)
from trading_platform.services.config.validation import ExecutionMode
from trading_platform.services.market_data_access import latest_completed_session
from trading_platform.services.risk import RiskRunReport
from trading_platform.worker.commands.run_jobs import required_mode_preflight

__all__ = ["migrated_backtest_db", "strategy_config_override"]

_VALID_PAYLOAD = {
    "strategy_id": "trend_following_daily",
    "as_of_session": "2024-01-10",
}


# ---------------------------------------------------------------------------
# RiskEvaluationSubmissionSpec (Task 1, spec half)
# ---------------------------------------------------------------------------


def test_rejection_enum_is_closed() -> None:
    assert {member.value for member in RiskEvaluationPayloadRejection} == {
        "unknown_payload_keys",
        "missing_required_field",
        "invalid_field_type",
        "invalid_date",
        "unknown_strategy_id",
        "as_of_session_in_future",
        "as_of_session_not_trading_session",
    }


@pytest.mark.parametrize(
    ("payload", "expected_reason"),
    [
        pytest.param(
            {**_VALID_PAYLOAD, "extra": 1},
            RiskEvaluationPayloadRejection.UNKNOWN_PAYLOAD_KEYS,
            id="unknown_payload_keys",
        ),
        pytest.param(
            {"strategy_id": "trend_following_daily"},
            RiskEvaluationPayloadRejection.MISSING_REQUIRED_FIELD,
            id="missing_required_field",
        ),
        pytest.param(
            {**_VALID_PAYLOAD, "strategy_id": 123},
            RiskEvaluationPayloadRejection.INVALID_FIELD_TYPE,
            id="invalid_field_type",
        ),
        pytest.param(
            {**_VALID_PAYLOAD, "as_of_session": "2024-13-01"},
            RiskEvaluationPayloadRejection.INVALID_DATE,
            id="invalid_date",
        ),
        pytest.param(
            {**_VALID_PAYLOAD, "strategy_id": "nope"},
            RiskEvaluationPayloadRejection.UNKNOWN_STRATEGY_ID,
            id="unknown_strategy_id",
        ),
        pytest.param(
            {**_VALID_PAYLOAD, "as_of_session": "2026-01-06"},
            RiskEvaluationPayloadRejection.AS_OF_SESSION_IN_FUTURE,
            id="as_of_session_in_future",
        ),
        pytest.param(
            {**_VALID_PAYLOAD, "as_of_session": "2024-01-06"},
            RiskEvaluationPayloadRejection.AS_OF_SESSION_NOT_TRADING_SESSION,
            id="as_of_session_not_trading_session",
        ),
    ],
)
def test_validate_payload_rejects(
    payload: dict[str, Any], expected_reason: RiskEvaluationPayloadRejection
) -> None:
    spec = RiskEvaluationSubmissionSpec(
        load_settings(), clock=lambda: datetime(2026, 1, 6, 3, 0, tzinfo=UTC)
    )

    with pytest.raises(InvalidJobPayloadError) as exc_info:
        spec.validate_payload(payload)

    assert exc_info.value.job_type == "risk-evaluation"
    assert exc_info.value.reason == expected_reason.value


def test_validate_payload_normalizes() -> None:
    spec = RiskEvaluationSubmissionSpec(load_settings())

    normalized = spec.validate_payload(
        {"strategy_id": "  trend_following_daily  ", "as_of_session": "2024-01-10"}
    )

    assert normalized == {
        "strategy_id": "trend_following_daily",
        "as_of_session": "2024-01-10",
    }


def test_submission_defaults_none_without_sessions(migrated_backtest_db: str) -> None:
    spec = RiskEvaluationSubmissionSpec(load_settings())

    assert spec.submission_defaults() is None


def test_submission_defaults_from_latest_session(migrated_backtest_db: str) -> None:
    _seed_market_data()
    settings = load_settings()
    spec = RiskEvaluationSubmissionSpec(settings)

    from trading_platform.db.session import session_scope

    with session_scope(settings) as session:
        latest = latest_completed_session(session, exchange=settings.market_data.calendar.exchange)
    assert latest is not None

    assert spec.submission_defaults() == {"as_of_session": latest.isoformat()}


def test_spec_satisfies_registry_contract() -> None:
    class _MinimalHandler:
        job_type = "risk-evaluation"

        def run(self, context: object) -> Mapping[str, Any]:
            return {}

    registry = JobRegistry()
    registry.register(_MinimalHandler(), submission_spec=RiskEvaluationSubmissionSpec(load_settings()))

    assert registry.list_job_types() == ["risk-evaluation"]


def test_spec_declares_no_retry_prerequisite() -> None:
    spec = RiskEvaluationSubmissionSpec(load_settings())

    assert retry_prerequisite_for(spec) is None


# ---------------------------------------------------------------------------
# RiskEvaluationJobHandler (Task 1, handler half)
# ---------------------------------------------------------------------------


class _FakeContext:
    def __init__(self, *, job_id: uuid.UUID | None = None, payload: Mapping[str, Any] | None = None) -> None:
        self.job_id = job_id or uuid.uuid4()
        self.job_type = "risk-evaluation"
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


def _fake_report(**overrides: Any) -> RiskRunReport:
    defaults: dict[str, Any] = {
        "run_id": str(uuid.uuid4()),
        "strategy_id": "trend_following_daily",
        "status": "succeeded",
        "trigger_source": "job",
        "started_at": "2024-01-10T00:00:00+00:00",
        "completed_at": "2024-01-10T00:05:00+00:00",
        "result_summary": {"stage": "completed", "approved_count": 1, "rejected_count": 0},
    }
    defaults.update(overrides)
    return RiskRunReport(**defaults)


def test_handler_passes_job_id_and_job_trigger_source(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_run_risk_evaluation(strategy_id: str, **kwargs: Any) -> RiskRunReport:
        captured["strategy_id"] = strategy_id
        captured.update(kwargs)
        return _fake_report()

    import trading_platform.jobs.handlers.risk_evaluation as risk_evaluation_module

    monkeypatch.setattr(risk_evaluation_module, "run_risk_evaluation", _fake_run_risk_evaluation)

    context = _FakeContext()
    handler = RiskEvaluationJobHandler()
    handler.run(context)

    assert captured["job_id"] == context.job_id
    assert captured["trigger_source"] == "job"
    assert captured["as_of_session"] == date(2024, 1, 10)


def test_handler_progress_steps_have_no_percent(monkeypatch: pytest.MonkeyPatch) -> None:
    import trading_platform.jobs.handlers.risk_evaluation as risk_evaluation_module

    monkeypatch.setattr(
        risk_evaluation_module, "run_risk_evaluation", lambda strategy_id, **kwargs: _fake_report()
    )

    context = _FakeContext()
    handler = RiskEvaluationJobHandler()
    handler.run(context)

    assert context.progress_calls == [
        {"step": STEP_RESOLVING},
        {"step": STEP_EVALUATING},
        {"step": STEP_RECORDING},
    ]
    for call in context.progress_calls:
        assert "percent" not in call


def test_cancel_before_start_never_calls_service(monkeypatch: pytest.MonkeyPatch) -> None:
    call_count = 0

    def _fake_run_risk_evaluation(strategy_id: str, **kwargs: Any) -> RiskRunReport:
        nonlocal call_count
        call_count += 1
        return _fake_report()

    import trading_platform.jobs.handlers.risk_evaluation as risk_evaluation_module

    monkeypatch.setattr(risk_evaluation_module, "run_risk_evaluation", _fake_run_risk_evaluation)

    context = _FakeContext()
    context.cancelled = True
    handler = RiskEvaluationJobHandler()

    with pytest.raises(JobCancelledError):
        handler.run(context)

    assert call_count == 0


def test_cancel_during_call_acknowledged_after_service(monkeypatch: pytest.MonkeyPatch) -> None:
    call_count = 0

    def _fake_run_risk_evaluation(strategy_id: str, **kwargs: Any) -> RiskRunReport:
        nonlocal call_count
        call_count += 1
        context.cancelled = True
        return _fake_report()

    import trading_platform.jobs.handlers.risk_evaluation as risk_evaluation_module

    monkeypatch.setattr(risk_evaluation_module, "run_risk_evaluation", _fake_run_risk_evaluation)

    context = _FakeContext()
    handler = RiskEvaluationJobHandler()

    with pytest.raises(JobCancelledError):
        handler.run(context)

    assert call_count == 1


def test_result_summary_carries_run_id(monkeypatch: pytest.MonkeyPatch) -> None:
    report = _fake_report()

    import trading_platform.jobs.handlers.risk_evaluation as risk_evaluation_module

    monkeypatch.setattr(
        risk_evaluation_module, "run_risk_evaluation", lambda strategy_id, **kwargs: report
    )

    context = _FakeContext()
    handler = RiskEvaluationJobHandler()
    result = handler.run(context)

    assert result == {
        "run_id": report.run_id,
        "produced_run_ids": [report.run_id],
        "strategy_id": report.strategy_id,
        "as_of_session": "2024-01-10",
        "run_status": report.status,
    }
    json.dumps(result)


def test_service_failure_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(strategy_id: str, **kwargs: Any) -> RiskRunReport:
        raise RuntimeError("boom")

    import trading_platform.jobs.handlers.risk_evaluation as risk_evaluation_module

    monkeypatch.setattr(risk_evaluation_module, "run_risk_evaluation", _raise)

    context = _FakeContext()
    handler = RiskEvaluationJobHandler()

    with pytest.raises(RuntimeError):
        handler.run(context)


def test_handler_declares_backtest_mode() -> None:
    assert RiskEvaluationJobHandler.required_execution_mode is ExecutionMode.BACKTEST
    assert required_mode_preflight(RiskEvaluationJobHandler()) is None


def test_handler_never_writes_run_status() -> None:
    import trading_platform.jobs.handlers.risk_evaluation as risk_evaluation_module

    source = __import__("inspect").getsource(risk_evaluation_module)
    assert "StrategyRunStatus" not in source
    assert "_update_risk_run" not in source
    assert "percent=" not in source
    assert "resolve_evaluation_session" not in source
