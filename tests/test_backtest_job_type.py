"""BacktestSubmissionSpec + BacktestJobHandler tests (OPS-01, D-08..D-13, D-16,
D-06, D-22).

Reuses ``tests.test_backtest_runner``'s Postgres fixtures (``migrated_backtest_db``,
``strategy_config_override``, ``_seed_market_data``) for the handful of tests that
need a real database (registry lookup does not; ``submission_defaults`` does).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any, Mapping

import pytest
from tests.test_backtest_runner import (
    _seed_market_data,
    migrated_backtest_db,
    strategy_config_override,
)

from trading_platform.core.settings import load_settings
from trading_platform.jobs.contracts import JobCancelledError
from trading_platform.jobs.handlers.backtest import (
    STEP_RECORDING,
    STEP_RESOLVING,
    STEP_RUNNING,
    BacktestJobHandler,
)
from trading_platform.jobs.handlers.backtest_submission import (
    BacktestPayloadRejection,
    BacktestSubmissionSpec,
)
from trading_platform.jobs.registry import InvalidJobPayloadError, JobRegistry
from trading_platform.services.backtesting import BacktestRunReport
from trading_platform.services.config.validation import ExecutionMode
from trading_platform.services.market_data_access import latest_completed_session
from trading_platform.worker.commands.run_jobs import required_mode_preflight

__all__ = ["migrated_backtest_db", "strategy_config_override"]

_VALID_PAYLOAD = {
    "strategy_id": "trend_following_daily",
    "from_date": "2024-01-02",
    "to_date": "2024-01-10",
}


# ---------------------------------------------------------------------------
# BacktestSubmissionSpec (Task 1)
# ---------------------------------------------------------------------------


def test_rejection_enum_is_closed() -> None:
    assert {member.value for member in BacktestPayloadRejection} == {
        "unknown_payload_keys",
        "missing_required_field",
        "invalid_field_type",
        "invalid_date",
        "unknown_strategy_id",
        "from_date_after_to_date",
        "to_date_in_future",
    }


@pytest.mark.parametrize(
    ("payload", "expected_reason"),
    [
        pytest.param(
            {**_VALID_PAYLOAD, "extra": 1},
            BacktestPayloadRejection.UNKNOWN_PAYLOAD_KEYS,
            id="unknown_payload_keys",
        ),
        pytest.param(
            {"strategy_id": "trend_following_daily", "from_date": "2024-01-02"},
            BacktestPayloadRejection.MISSING_REQUIRED_FIELD,
            id="missing_required_field",
        ),
        pytest.param(
            {**_VALID_PAYLOAD, "strategy_id": 123},
            BacktestPayloadRejection.INVALID_FIELD_TYPE,
            id="invalid_field_type_wrong_type",
        ),
        pytest.param(
            {**_VALID_PAYLOAD, "strategy_id": "   "},
            BacktestPayloadRejection.INVALID_FIELD_TYPE,
            id="invalid_field_type_blank",
        ),
        pytest.param(
            {**_VALID_PAYLOAD, "from_date": "2024/01/02"},
            BacktestPayloadRejection.INVALID_DATE,
            id="invalid_date_wrong_format",
        ),
        pytest.param(
            {**_VALID_PAYLOAD, "from_date": 1704153600},
            BacktestPayloadRejection.INVALID_DATE,
            id="invalid_date_timestamp",
        ),
        pytest.param(
            {**_VALID_PAYLOAD, "strategy_id": "nope"},
            BacktestPayloadRejection.UNKNOWN_STRATEGY_ID,
            id="unknown_strategy_id",
        ),
        pytest.param(
            {**_VALID_PAYLOAD, "from_date": "2024-01-10", "to_date": "2024-01-02"},
            BacktestPayloadRejection.FROM_DATE_AFTER_TO_DATE,
            id="from_date_after_to_date",
        ),
        pytest.param(
            {**_VALID_PAYLOAD, "from_date": "2026-01-01", "to_date": "2026-01-06"},
            BacktestPayloadRejection.TO_DATE_IN_FUTURE,
            id="to_date_in_future",
        ),
    ],
)
def test_validate_payload_rejects(
    payload: dict[str, Any], expected_reason: BacktestPayloadRejection
) -> None:
    spec = BacktestSubmissionSpec(
        load_settings(), clock=lambda: datetime(2026, 1, 6, 3, 0, tzinfo=UTC)
    )

    with pytest.raises(InvalidJobPayloadError) as exc_info:
        spec.validate_payload(payload)

    assert exc_info.value.job_type == "backtest"
    assert exc_info.value.reason == expected_reason.value


def test_validate_payload_normalizes() -> None:
    spec = BacktestSubmissionSpec(load_settings())

    normalized = spec.validate_payload(_VALID_PAYLOAD)

    assert normalized == {
        "strategy_id": "trend_following_daily",
        "from_date": "2024-01-02",
        "to_date": "2024-01-10",
    }


def test_to_date_future_uses_exchange_calendar_date() -> None:
    clock = lambda: datetime(2026, 1, 6, 3, 0, tzinfo=UTC)  # noqa: E731
    spec = BacktestSubmissionSpec(load_settings(), clock=clock)

    with pytest.raises(InvalidJobPayloadError) as exc_info:
        spec.validate_payload({**_VALID_PAYLOAD, "from_date": "2026-01-01", "to_date": "2026-01-06"})
    assert exc_info.value.reason == BacktestPayloadRejection.TO_DATE_IN_FUTURE.value

    normalized = spec.validate_payload(
        {**_VALID_PAYLOAD, "from_date": "2026-01-01", "to_date": "2026-01-05"}
    )
    assert normalized["to_date"] == "2026-01-05"


def test_submission_module_never_reads_host_date() -> None:
    import trading_platform.jobs.handlers.backtest_submission as module

    source = __import__("inspect").getsource(module)
    assert "date.today" not in source


def test_submission_defaults_none_without_sessions(migrated_backtest_db: str) -> None:
    spec = BacktestSubmissionSpec(load_settings())

    assert spec.submission_defaults() is None


def test_submission_defaults_from_latest_session(migrated_backtest_db: str) -> None:
    _seed_market_data()
    settings = load_settings()
    spec = BacktestSubmissionSpec(settings)

    from trading_platform.db.session import session_scope

    with session_scope(settings) as session:
        latest = latest_completed_session(session, exchange=settings.market_data.calendar.exchange)
    assert latest is not None

    from datetime import timedelta

    expected = {
        "from_date": (latest - timedelta(days=settings.market_data.ingest.default_lookback_days)).isoformat(),
        "to_date": latest.isoformat(),
    }

    assert spec.submission_defaults() == expected


def test_spec_satisfies_registry_contract() -> None:
    class _MinimalHandler:
        job_type = "backtest"

        def run(self, context: object) -> Mapping[str, Any]:
            return {}

    registry = JobRegistry()
    registry.register(_MinimalHandler(), submission_spec=BacktestSubmissionSpec(load_settings()))

    assert registry.list_job_types() == ["backtest"]


# ---------------------------------------------------------------------------
# BacktestJobHandler (Task 2)
# ---------------------------------------------------------------------------


class _FakeContext:
    def __init__(self, *, job_id: uuid.UUID | None = None, payload: Mapping[str, Any] | None = None) -> None:
        self.job_id = job_id or uuid.uuid4()
        self.job_type = "backtest"
        self.payload = payload or dict(_VALID_PAYLOAD)
        self.progress_calls: list[dict[str, Any]] = []
        self.log_calls: list[dict[str, Any]] = []
        self.cancelled = False
        self._cancel_on_next_raise_check = False

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


def _fake_report(**overrides: Any) -> BacktestRunReport:
    defaults: dict[str, Any] = {
        "run_id": str(uuid.uuid4()),
        "strategy_id": "trend_following_daily",
        "status": "succeeded",
        "trigger_source": "job",
        "started_at": "2024-01-02T00:00:00+00:00",
        "completed_at": "2024-01-02T00:05:00+00:00",
        "result_summary": {
            "sessions_evaluated": 5,
            "trades_persisted": 2,
            "starting_capital": 100000.0,
            "ending_equity": 100500.0,
        },
    }
    defaults.update(overrides)
    return BacktestRunReport(**defaults)


def test_handler_passes_job_id_and_job_trigger_source(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_run_backtest(strategy_id: str, **kwargs: Any) -> BacktestRunReport:
        captured["strategy_id"] = strategy_id
        captured.update(kwargs)
        return _fake_report()

    import trading_platform.jobs.handlers.backtest as backtest_module

    monkeypatch.setattr(backtest_module, "run_backtest", _fake_run_backtest)

    context = _FakeContext()
    handler = BacktestJobHandler()
    handler.run(context)

    assert captured["job_id"] == context.job_id
    assert captured["trigger_source"] == "job"


def test_handler_progress_steps_have_no_percent(monkeypatch: pytest.MonkeyPatch) -> None:
    import trading_platform.jobs.handlers.backtest as backtest_module

    monkeypatch.setattr(backtest_module, "run_backtest", lambda strategy_id, **kwargs: _fake_report())

    context = _FakeContext()
    handler = BacktestJobHandler()
    handler.run(context)

    assert context.progress_calls == [
        {"step": STEP_RESOLVING},
        {"step": STEP_RUNNING},
        {"step": STEP_RECORDING},
    ]
    for call in context.progress_calls:
        assert "percent" not in call


def test_cancel_before_start_never_calls_service(monkeypatch: pytest.MonkeyPatch) -> None:
    call_count = 0

    def _fake_run_backtest(strategy_id: str, **kwargs: Any) -> BacktestRunReport:
        nonlocal call_count
        call_count += 1
        return _fake_report()

    import trading_platform.jobs.handlers.backtest as backtest_module

    monkeypatch.setattr(backtest_module, "run_backtest", _fake_run_backtest)

    context = _FakeContext()
    context.cancelled = True
    handler = BacktestJobHandler()

    with pytest.raises(JobCancelledError):
        handler.run(context)

    assert call_count == 0


def test_cancel_during_call_acknowledged_after_service(monkeypatch: pytest.MonkeyPatch) -> None:
    call_count = 0

    def _fake_run_backtest(strategy_id: str, **kwargs: Any) -> BacktestRunReport:
        nonlocal call_count
        call_count += 1
        context.cancelled = True
        return _fake_report()

    import trading_platform.jobs.handlers.backtest as backtest_module

    monkeypatch.setattr(backtest_module, "run_backtest", _fake_run_backtest)

    context = _FakeContext()
    handler = BacktestJobHandler()

    with pytest.raises(JobCancelledError):
        handler.run(context)

    assert call_count == 1


def test_result_summary_carries_run_id(monkeypatch: pytest.MonkeyPatch) -> None:
    report = _fake_report()

    import trading_platform.jobs.handlers.backtest as backtest_module

    monkeypatch.setattr(backtest_module, "run_backtest", lambda strategy_id, **kwargs: report)

    context = _FakeContext()
    handler = BacktestJobHandler()
    result = handler.run(context)

    assert result["run_id"] == report.run_id
    json.dumps(result)
    for key in ("sessions_evaluated", "trades_persisted", "starting_capital", "ending_equity"):
        assert key in result


def test_service_failure_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(strategy_id: str, **kwargs: Any) -> BacktestRunReport:
        raise RuntimeError("boom")

    import trading_platform.jobs.handlers.backtest as backtest_module

    monkeypatch.setattr(backtest_module, "run_backtest", _raise)

    context = _FakeContext()
    handler = BacktestJobHandler()

    with pytest.raises(RuntimeError):
        handler.run(context)


def test_handler_log_codes_are_not_external(monkeypatch: pytest.MonkeyPatch) -> None:
    import trading_platform.jobs.handlers.backtest as backtest_module

    monkeypatch.setattr(backtest_module, "run_backtest", lambda strategy_id, **kwargs: _fake_report())

    context = _FakeContext()
    handler = BacktestJobHandler()
    handler.run(context)

    assert context.log_calls
    for entry in context.log_calls:
        assert not entry["event_code"].startswith("external_")


def test_handler_declares_backtest_mode() -> None:
    assert BacktestJobHandler.required_execution_mode is ExecutionMode.BACKTEST
    assert required_mode_preflight(BacktestJobHandler()) is None


def test_handler_never_writes_run_status() -> None:
    import trading_platform.jobs.handlers.backtest as backtest_module

    source = __import__("inspect").getsource(backtest_module)
    assert "StrategyRunStatus" not in source
    assert "_update_backtest_run" not in source
