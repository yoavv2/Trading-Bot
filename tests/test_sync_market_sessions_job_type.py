"""SyncMarketSessionsSubmissionSpec + SyncMarketSessionsJobHandler tests
(OPS-05, D-25, D-01).

Mirrors ``tests.test_ingest_bars_job_type`` function-by-function,
substituting ``sync-market-sessions``'s ``from_date``/``to_date``-only
payload (no ``symbols`` field) for ``ingest-bars``'s three-field shape.
"""

from __future__ import annotations

import inspect
import json
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any, Mapping

import pytest

from trading_platform.core.settings import load_settings
from trading_platform.jobs.contracts import JobCancelledError
from trading_platform.jobs.handlers.sync_market_sessions import (
    STEP_RECORDING,
    STEP_RESOLVING,
    STEP_SYNCING,
    SyncMarketSessionsJobHandler,
)
from trading_platform.jobs.handlers.sync_market_sessions_submission import (
    SyncMarketSessionsPayloadRejection,
    SyncMarketSessionsSubmissionSpec,
)
from trading_platform.jobs.registry import (
    InvalidJobPayloadError,
    JobRegistry,
    retry_prerequisite_for,
)
from trading_platform.services.calendar import MarketSessionSyncResult
from trading_platform.services.config.validation import ExecutionMode
from trading_platform.worker.commands.run_jobs import required_mode_preflight

_VALID_PAYLOAD = {"from_date": "2024-01-01", "to_date": "2024-01-10"}


# ---------------------------------------------------------------------------
# SyncMarketSessionsSubmissionSpec (Task 2, spec half)
# ---------------------------------------------------------------------------


def test_rejection_enum_is_closed() -> None:
    assert {member.value for member in SyncMarketSessionsPayloadRejection} == {
        "unknown_payload_keys",
        "missing_required_field",
        "invalid_field_type",
        "invalid_date",
        "from_date_after_to_date",
        "to_date_in_future",
        "date_range_out_of_calendar_range",
    }


def test_invalid_field_type_member_is_map_validation_error_fallback() -> None:
    """``INVALID_FIELD_TYPE`` is part of the closed enum (matching the
    shared ``PayloadFieldRejection`` vocabulary every Phase 20 spec draws
    from) and is ``map_validation_error``'s residual fallback case, but is
    not reachable through this spec's own payload: both fields
    (``from_date``/``to_date``) go through the shared ``parse_iso_date``
    before-validator (payload_fields.py), which maps *every* wrong-typed or
    malformed value to ``INVALID_DATE`` -- exactly like ``ingest-bars`` and
    ``backtest`` do for their own date fields (neither has a reachable
    invalid_field_type case via a date field either; both reach it only via
    a non-date field this spec does not have: symbols/strategy_id). Without
    the member, ``map_validation_error``'s fallback would raise
    ``ValueError`` when constructing ``SyncMarketSessionsPayloadRejection``
    from an unexpected pydantic error, turning that edge case into a 500
    instead of a typed 422."""

    from pydantic import ValidationError

    from trading_platform.jobs.handlers.payload_fields import (
        PayloadFieldRejection,
        map_validation_error,
    )

    synthetic = ValidationError.from_exception_data(
        "SyntheticModel",
        [{"type": "int_type", "loc": ("from_date",), "input": 123, "ctx": {}}],
    )

    fallback = map_validation_error(synthetic)
    assert fallback == PayloadFieldRejection.INVALID_FIELD_TYPE
    # The fallback value round-trips through the spec's own closed enum.
    assert SyncMarketSessionsPayloadRejection(fallback.value) == (
        SyncMarketSessionsPayloadRejection.INVALID_FIELD_TYPE
    )


@pytest.mark.parametrize(
    ("payload", "expected_reason"),
    [
        pytest.param(
            {**_VALID_PAYLOAD, "extra": 1},
            SyncMarketSessionsPayloadRejection.UNKNOWN_PAYLOAD_KEYS,
            id="unknown_payload_keys",
        ),
        pytest.param(
            {"from_date": "2024-01-01"},
            SyncMarketSessionsPayloadRejection.MISSING_REQUIRED_FIELD,
            id="missing_required_field",
        ),
        pytest.param(
            {**_VALID_PAYLOAD, "from_date": "2024-13-01"},
            SyncMarketSessionsPayloadRejection.INVALID_DATE,
            id="invalid_date_malformed_string",
        ),
        pytest.param(
            {**_VALID_PAYLOAD, "from_date": 123},
            SyncMarketSessionsPayloadRejection.INVALID_DATE,
            id="wrong_typed_date_maps_to_invalid_date",
        ),
        pytest.param(
            {"from_date": "2024-01-10", "to_date": "2024-01-01"},
            SyncMarketSessionsPayloadRejection.FROM_DATE_AFTER_TO_DATE,
            id="from_date_after_to_date",
        ),
        pytest.param(
            {"from_date": "2026-01-01", "to_date": "2026-01-10"},
            SyncMarketSessionsPayloadRejection.TO_DATE_IN_FUTURE,
            id="to_date_in_future",
        ),
        pytest.param(
            {"from_date": "2006-09-27", "to_date": "2024-01-10"},
            SyncMarketSessionsPayloadRejection.DATE_RANGE_OUT_OF_CALENDAR_RANGE,
            id="from_date_before_calendar_window",
        ),
        pytest.param(
            {"from_date": "0001-01-01", "to_date": "2024-01-10"},
            SyncMarketSessionsPayloadRejection.DATE_RANGE_OUT_OF_CALENDAR_RANGE,
            id="from_date_year_one",
        ),
    ],
)
def test_validate_payload_rejects(
    payload: dict[str, Any], expected_reason: SyncMarketSessionsPayloadRejection
) -> None:
    spec = SyncMarketSessionsSubmissionSpec(
        load_settings(), clock=lambda: datetime(2026, 1, 6, 3, 0, tzinfo=UTC)
    )

    with pytest.raises(InvalidJobPayloadError) as exc_info:
        spec.validate_payload(payload)

    assert exc_info.value.job_type == "sync-market-sessions"
    assert exc_info.value.reason == expected_reason.value


def test_validate_payload_normalizes() -> None:
    spec = SyncMarketSessionsSubmissionSpec(load_settings())

    normalized = spec.validate_payload({"from_date": "2024-01-01", "to_date": "2024-01-10"})

    assert normalized == {"from_date": "2024-01-01", "to_date": "2024-01-10"}


def test_submission_defaults_from_injected_clock() -> None:
    settings = load_settings()
    spec = SyncMarketSessionsSubmissionSpec(
        settings, clock=lambda: datetime(2026, 1, 6, 3, 0, tzinfo=UTC)
    )

    defaults = spec.submission_defaults()

    assert defaults["to_date"] == "2026-01-05"
    lookback_days = settings.market_data.ingest.default_lookback_days
    expected_from = (date(2026, 1, 5) - timedelta(days=lookback_days)).isoformat()
    assert defaults["from_date"] == expected_from


def test_submission_defaults_never_none() -> None:
    spec = SyncMarketSessionsSubmissionSpec(load_settings())

    assert spec.submission_defaults() is not None


def test_spec_satisfies_registry_contract() -> None:
    class _MinimalHandler:
        job_type = "sync-market-sessions"

        def run(self, context: object) -> Mapping[str, Any]:
            return {}

    registry = JobRegistry()
    registry.register(
        _MinimalHandler(), submission_spec=SyncMarketSessionsSubmissionSpec(load_settings())
    )

    assert registry.list_job_types() == ["sync-market-sessions"]


def test_spec_declares_no_retry_prerequisite() -> None:
    spec = SyncMarketSessionsSubmissionSpec(load_settings())

    assert retry_prerequisite_for(spec) is None


# ---------------------------------------------------------------------------
# SyncMarketSessionsJobHandler (Task 2, handler half)
# ---------------------------------------------------------------------------


class _FakeContext:
    def __init__(self, *, job_id: uuid.UUID | None = None, payload: Mapping[str, Any] | None = None) -> None:
        self.job_id = job_id or uuid.uuid4()
        self.job_type = "sync-market-sessions"
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


def _fake_result(**overrides: Any) -> MarketSessionSyncResult:
    defaults: dict[str, Any] = {
        "exchange": "XNYS",
        "from_date": date(2024, 1, 1),
        "to_date": date(2024, 1, 10),
        "sessions_upserted": 7,
    }
    defaults.update(overrides)
    return MarketSessionSyncResult(**defaults)


def test_handler_progress_steps_have_no_percent(monkeypatch: pytest.MonkeyPatch) -> None:
    import trading_platform.jobs.handlers.sync_market_sessions as handler_module

    monkeypatch.setattr(handler_module, "sync_market_sessions", lambda **kwargs: _fake_result())

    context = _FakeContext()
    handler = SyncMarketSessionsJobHandler()
    handler.run(context)

    assert context.progress_calls == [
        {"step": STEP_RESOLVING},
        {"step": STEP_SYNCING},
        {"step": STEP_RECORDING},
    ]
    for call in context.progress_calls:
        assert "percent" not in call


def test_cancel_before_start_never_calls_service(monkeypatch: pytest.MonkeyPatch) -> None:
    call_count = 0

    def _fake_sync(**kwargs: Any) -> MarketSessionSyncResult:
        nonlocal call_count
        call_count += 1
        return _fake_result()

    import trading_platform.jobs.handlers.sync_market_sessions as handler_module

    monkeypatch.setattr(handler_module, "sync_market_sessions", _fake_sync)

    context = _FakeContext()
    context.cancelled = True
    handler = SyncMarketSessionsJobHandler()

    with pytest.raises(JobCancelledError):
        handler.run(context)

    assert call_count == 0


def test_cancel_during_call_acknowledged_after_service(monkeypatch: pytest.MonkeyPatch) -> None:
    call_count = 0

    def _fake_sync(**kwargs: Any) -> MarketSessionSyncResult:
        nonlocal call_count
        call_count += 1
        context.cancelled = True
        return _fake_result()

    import trading_platform.jobs.handlers.sync_market_sessions as handler_module

    monkeypatch.setattr(handler_module, "sync_market_sessions", _fake_sync)

    context = _FakeContext()
    handler = SyncMarketSessionsJobHandler()

    with pytest.raises(JobCancelledError):
        handler.run(context)

    assert call_count == 1


def test_handler_passes_from_date_to_date_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_sync(**kwargs: Any) -> MarketSessionSyncResult:
        captured.update(kwargs)
        return _fake_result()

    import trading_platform.jobs.handlers.sync_market_sessions as handler_module

    monkeypatch.setattr(handler_module, "sync_market_sessions", _fake_sync)

    settings = load_settings()
    context = _FakeContext()
    handler = SyncMarketSessionsJobHandler(settings)
    handler.run(context)

    assert captured["from_date"] == date(2024, 1, 1)
    assert captured["to_date"] == date(2024, 1, 10)
    assert captured["settings"] is settings


def test_result_summary_carries_expected_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _fake_result()

    import trading_platform.jobs.handlers.sync_market_sessions as handler_module

    monkeypatch.setattr(handler_module, "sync_market_sessions", lambda **kwargs: result)

    context = _FakeContext()
    handler = SyncMarketSessionsJobHandler()
    summary = handler.run(context)

    assert summary == {
        "exchange": "XNYS",
        "from_date": "2024-01-01",
        "to_date": "2024-01-10",
        "sessions_upserted": 7,
        "produced_run_ids": [],
    }
    json.dumps(summary)


def test_handler_declares_backtest_mode() -> None:
    assert SyncMarketSessionsJobHandler.required_execution_mode is ExecutionMode.BACKTEST
    assert required_mode_preflight(SyncMarketSessionsJobHandler()) is None


def test_handler_source_has_no_session_scope_or_upsert() -> None:
    import trading_platform.jobs.handlers.sync_market_sessions as handler_module

    source = inspect.getsource(handler_module)
    assert "session_scope" not in source
    assert "upsert_market_sessions" not in source
