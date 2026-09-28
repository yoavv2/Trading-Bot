"""SyncSymbolMetadataSubmissionSpec + SyncSymbolMetadataJobHandler tests
(OPS-05, ORCH-02, D-25, D-01).

Mirrors ``tests.test_ingest_bars_job_type`` function-by-function,
substituting ``sync-symbol-metadata``'s ``symbols``-only payload (no date
range) for ``ingest-bars``'s ``from_date``/``to_date``/``symbols`` shape.
"""

from __future__ import annotations

import inspect
import json
import uuid
from typing import Any, Mapping

import pytest

from trading_platform.core.settings import load_settings
from trading_platform.jobs.contracts import JobCancelledError
from trading_platform.jobs.handlers.sync_symbol_metadata import (
    STEP_RECORDING,
    STEP_RESOLVING,
    STEP_SYNCING,
    SyncSymbolMetadataJobHandler,
)
from trading_platform.jobs.handlers.sync_symbol_metadata_submission import (
    SyncSymbolMetadataPayloadRejection,
    SyncSymbolMetadataSubmissionSpec,
)
from trading_platform.jobs.registry import (
    InvalidJobPayloadError,
    JobRegistry,
    retry_prerequisite_for,
)
from trading_platform.services.config.validation import ExecutionMode
from trading_platform.services.symbol_metadata_sync import (
    MetadataSyncResult,
    SymbolMetadataSyncFailedError,
)
from trading_platform.worker.commands.run_jobs import required_mode_preflight

_VALID_PAYLOAD = {"symbols": ["AAPL", "SPY"]}


# ---------------------------------------------------------------------------
# SyncSymbolMetadataSubmissionSpec (Task 1, spec half)
# ---------------------------------------------------------------------------


def test_rejection_enum_is_closed() -> None:
    assert {member.value for member in SyncSymbolMetadataPayloadRejection} == {
        "unknown_payload_keys",
        "missing_required_field",
        "invalid_field_type",
        "empty_symbols",
        "invalid_symbol",
        "too_many_symbols",
    }


@pytest.mark.parametrize(
    ("payload", "expected_reason"),
    [
        pytest.param(
            {**_VALID_PAYLOAD, "extra": 1},
            SyncSymbolMetadataPayloadRejection.UNKNOWN_PAYLOAD_KEYS,
            id="unknown_payload_keys",
        ),
        pytest.param(
            {},
            SyncSymbolMetadataPayloadRejection.MISSING_REQUIRED_FIELD,
            id="missing_required_field",
        ),
        pytest.param(
            {"symbols": "AAPL"},
            SyncSymbolMetadataPayloadRejection.INVALID_FIELD_TYPE,
            id="invalid_field_type",
        ),
        pytest.param(
            {"symbols": []},
            SyncSymbolMetadataPayloadRejection.EMPTY_SYMBOLS,
            id="empty_symbols",
        ),
        pytest.param(
            {"symbols": ["1AAPL"]},
            SyncSymbolMetadataPayloadRejection.INVALID_SYMBOL,
            id="invalid_symbol",
        ),
        pytest.param(
            {"symbols": [f"SYM{i}" for i in range(501)]},
            SyncSymbolMetadataPayloadRejection.TOO_MANY_SYMBOLS,
            id="too_many_symbols",
        ),
    ],
)
def test_validate_payload_rejects(
    payload: dict[str, Any], expected_reason: SyncSymbolMetadataPayloadRejection
) -> None:
    spec = SyncSymbolMetadataSubmissionSpec(load_settings())

    with pytest.raises(InvalidJobPayloadError) as exc_info:
        spec.validate_payload(payload)

    assert exc_info.value.job_type == "sync-symbol-metadata"
    assert exc_info.value.reason == expected_reason.value


def test_validate_payload_normalizes() -> None:
    spec = SyncSymbolMetadataSubmissionSpec(load_settings())

    normalized = spec.validate_payload({"symbols": [" spy", "aapl", "SPY"]})

    assert normalized == {"symbols": ["AAPL", "SPY"]}


def test_submission_defaults_from_metadata_universe() -> None:
    settings = load_settings()
    spec = SyncSymbolMetadataSubmissionSpec(settings)

    expected = ",".join(sorted({s.upper() for s in settings.market_data.metadata.universe}))
    assert spec.submission_defaults() == {"symbols": expected}


def test_spec_satisfies_registry_contract() -> None:
    class _MinimalHandler:
        job_type = "sync-symbol-metadata"

        def run(self, context: object) -> Mapping[str, Any]:
            return {}

    registry = JobRegistry()
    registry.register(
        _MinimalHandler(), submission_spec=SyncSymbolMetadataSubmissionSpec(load_settings())
    )

    assert registry.list_job_types() == ["sync-symbol-metadata"]


def test_spec_declares_no_retry_prerequisite() -> None:
    spec = SyncSymbolMetadataSubmissionSpec(load_settings())

    assert retry_prerequisite_for(spec) is None


# ---------------------------------------------------------------------------
# SyncSymbolMetadataJobHandler (Task 1, handler half)
# ---------------------------------------------------------------------------


class _FakeContext:
    def __init__(self, *, job_id: uuid.UUID | None = None, payload: Mapping[str, Any] | None = None) -> None:
        self.job_id = job_id or uuid.uuid4()
        self.job_type = "sync-symbol-metadata"
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


def _fake_result(**overrides: Any) -> MetadataSyncResult:
    defaults: dict[str, Any] = {"synced": ["AAPL", "SPY"], "skipped": [], "failed": []}
    defaults.update(overrides)
    return MetadataSyncResult(**defaults)


def test_handler_progress_steps_have_no_percent(monkeypatch: pytest.MonkeyPatch) -> None:
    import trading_platform.jobs.handlers.sync_symbol_metadata as handler_module

    monkeypatch.setattr(handler_module, "sync_symbol_metadata", lambda symbols, **kwargs: _fake_result())

    context = _FakeContext()
    handler = SyncSymbolMetadataJobHandler()
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

    def _fake_sync(symbols: list[str], **kwargs: Any) -> MetadataSyncResult:
        nonlocal call_count
        call_count += 1
        return _fake_result()

    import trading_platform.jobs.handlers.sync_symbol_metadata as handler_module

    monkeypatch.setattr(handler_module, "sync_symbol_metadata", _fake_sync)

    context = _FakeContext()
    context.cancelled = True
    handler = SyncSymbolMetadataJobHandler()

    with pytest.raises(JobCancelledError):
        handler.run(context)

    assert call_count == 0


def test_cancel_during_call_acknowledged_after_service(monkeypatch: pytest.MonkeyPatch) -> None:
    call_count = 0

    def _fake_sync(symbols: list[str], **kwargs: Any) -> MetadataSyncResult:
        nonlocal call_count
        call_count += 1
        context.cancelled = True
        return _fake_result()

    import trading_platform.jobs.handlers.sync_symbol_metadata as handler_module

    monkeypatch.setattr(handler_module, "sync_symbol_metadata", _fake_sync)

    context = _FakeContext()
    handler = SyncSymbolMetadataJobHandler()

    with pytest.raises(JobCancelledError):
        handler.run(context)

    assert call_count == 1


def test_handler_passes_symbols_and_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_sync(symbols: list[str], **kwargs: Any) -> MetadataSyncResult:
        captured["symbols"] = symbols
        captured.update(kwargs)
        return _fake_result()

    import trading_platform.jobs.handlers.sync_symbol_metadata as handler_module

    monkeypatch.setattr(handler_module, "sync_symbol_metadata", _fake_sync)

    settings = load_settings()
    context = _FakeContext()
    handler = SyncSymbolMetadataJobHandler(settings)
    handler.run(context)

    assert captured["symbols"] == ["AAPL", "SPY"]
    assert captured["settings"] is settings


def test_result_summary_carries_expected_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _fake_result()

    import trading_platform.jobs.handlers.sync_symbol_metadata as handler_module

    monkeypatch.setattr(handler_module, "sync_symbol_metadata", lambda symbols, **kwargs: result)

    context = _FakeContext()
    handler = SyncSymbolMetadataJobHandler()
    summary = handler.run(context)

    assert summary == {
        "synced": ["AAPL", "SPY"],
        "skipped": [],
        "failed": [],
        "synced_count": 2,
        "skipped_count": 0,
        "failed_count": 0,
        "produced_run_ids": [],
    }
    assert "succeeded" not in summary
    json.dumps(summary)


def test_failed_tickers_raise_after_completion_log(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _fake_result(synced=[], skipped=[], failed=["ZZZZ"])

    import trading_platform.jobs.handlers.sync_symbol_metadata as handler_module

    monkeypatch.setattr(handler_module, "sync_symbol_metadata", lambda symbols, **kwargs: result)

    context = _FakeContext()
    handler = SyncSymbolMetadataJobHandler()

    with pytest.raises(SymbolMetadataSyncFailedError, match="ZZZZ"):
        handler.run(context)

    assert any(
        call["event_code"] == "symbol_metadata_sync_completed" for call in context.log_calls
    )


def test_handler_declares_backtest_mode() -> None:
    assert SyncSymbolMetadataJobHandler.required_execution_mode is ExecutionMode.BACKTEST
    assert required_mode_preflight(SyncSymbolMetadataJobHandler()) is None


def test_no_dry_run_field_anywhere() -> None:
    import trading_platform.jobs.handlers.sync_symbol_metadata as handler_module
    import trading_platform.jobs.handlers.sync_symbol_metadata_submission as submission_module

    assert "dry_run" not in inspect.getsource(handler_module)
    assert "dry_run" not in inspect.getsource(submission_module)
