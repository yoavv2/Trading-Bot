"""IngestBarsSubmissionSpec + IngestBarsJobHandler tests (OPS-05, D-24, D-25,
D-01, D-08, D-09, D-11, P19 D-22).

Mirrors ``tests.test_risk_evaluation_job_type`` function-by-function,
substituting the ``ingest-bars`` Job type's explicit ``from_date``/``to_date``
range + ``symbols`` list for ``risk-evaluation``'s single ``as_of_session``
field. The service-level ``run_id`` test reuses
``tests/test_market_data_ingestion.py``'s Postgres fixture and Polygon
fakes.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Mapping
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select
from tests.test_market_data_ingestion import (
    FIXTURE_PATH,
    _make_market_data_settings,
    migrated_ingest_db,
)

from trading_platform.core.settings import load_settings
from trading_platform.db.models import MarketDataIngestionRun
from trading_platform.db.session import session_scope
from trading_platform.jobs.contracts import JobCancelledError
from trading_platform.jobs.handlers.ingest_bars import (
    STEP_INGESTING,
    STEP_RECORDING,
    STEP_RESOLVING,
    IngestBarsJobHandler,
)
from trading_platform.jobs.handlers.ingest_bars_submission import (
    IngestBarsPayloadRejection,
    IngestBarsSubmissionSpec,
)
from trading_platform.jobs.registry import (
    InvalidJobPayloadError,
    JobRegistry,
    retry_prerequisite_for,
)
from trading_platform.services.config.validation import ExecutionMode
from trading_platform.services.data import IngestionResult
from trading_platform.services.ingestion import ingest_daily_bars
from trading_platform.worker.commands.run_jobs import required_mode_preflight

__all__ = ["migrated_ingest_db"]

_VALID_PAYLOAD = {
    "from_date": "2024-01-08",
    "to_date": "2024-01-10",
    "symbols": ["AAPL", "SPY"],
}


# ---------------------------------------------------------------------------
# IngestBarsSubmissionSpec (Task 1, spec half)
# ---------------------------------------------------------------------------


def test_rejection_enum_is_closed() -> None:
    assert {member.value for member in IngestBarsPayloadRejection} == {
        "unknown_payload_keys",
        "missing_required_field",
        "invalid_field_type",
        "invalid_date",
        "from_date_after_to_date",
        "to_date_in_future",
        "empty_symbols",
        "invalid_symbol",
        "too_many_symbols",
    }


@pytest.mark.parametrize(
    ("payload", "expected_reason"),
    [
        pytest.param(
            {**_VALID_PAYLOAD, "extra": 1},
            IngestBarsPayloadRejection.UNKNOWN_PAYLOAD_KEYS,
            id="unknown_payload_keys",
        ),
        pytest.param(
            {"from_date": "2024-01-08", "to_date": "2024-01-10"},
            IngestBarsPayloadRejection.MISSING_REQUIRED_FIELD,
            id="missing_required_field",
        ),
        pytest.param(
            {**_VALID_PAYLOAD, "symbols": "AAPL"},
            IngestBarsPayloadRejection.INVALID_FIELD_TYPE,
            id="invalid_field_type",
        ),
        pytest.param(
            {**_VALID_PAYLOAD, "from_date": "2024-13-01"},
            IngestBarsPayloadRejection.INVALID_DATE,
            id="invalid_date",
        ),
        pytest.param(
            {**_VALID_PAYLOAD, "from_date": "2024-01-10", "to_date": "2024-01-08"},
            IngestBarsPayloadRejection.FROM_DATE_AFTER_TO_DATE,
            id="from_date_after_to_date",
        ),
        pytest.param(
            {**_VALID_PAYLOAD, "from_date": "2026-01-01", "to_date": "2026-01-10"},
            IngestBarsPayloadRejection.TO_DATE_IN_FUTURE,
            id="to_date_in_future",
        ),
        pytest.param(
            {**_VALID_PAYLOAD, "symbols": []},
            IngestBarsPayloadRejection.EMPTY_SYMBOLS,
            id="empty_symbols",
        ),
        pytest.param(
            {**_VALID_PAYLOAD, "symbols": ["1AAPL"]},
            IngestBarsPayloadRejection.INVALID_SYMBOL,
            id="invalid_symbol",
        ),
        pytest.param(
            {**_VALID_PAYLOAD, "symbols": [f"SYM{i}" for i in range(501)]},
            IngestBarsPayloadRejection.TOO_MANY_SYMBOLS,
            id="too_many_symbols",
        ),
    ],
)
def test_validate_payload_rejects(
    payload: dict[str, Any], expected_reason: IngestBarsPayloadRejection
) -> None:
    spec = IngestBarsSubmissionSpec(
        load_settings(), clock=lambda: datetime(2026, 1, 6, 3, 0, tzinfo=UTC)
    )

    with pytest.raises(InvalidJobPayloadError) as exc_info:
        spec.validate_payload(payload)

    assert exc_info.value.job_type == "ingest-bars"
    assert exc_info.value.reason == expected_reason.value


def test_validate_payload_normalizes() -> None:
    spec = IngestBarsSubmissionSpec(load_settings())

    normalized = spec.validate_payload(
        {
            "from_date": "2024-01-08",
            "to_date": "2024-01-10",
            "symbols": [" spy", "aapl", "SPY"],
        }
    )

    assert normalized == {
        "from_date": "2024-01-08",
        "to_date": "2024-01-10",
        "symbols": ["AAPL", "SPY"],
    }


def _seed_bar(session, *, ticker: str, session_date: date, close: str) -> None:
    from trading_platform.db.models.daily_bar import DailyBar
    from trading_platform.db.models.symbol import Symbol

    symbol = session.execute(select(Symbol).where(Symbol.ticker == ticker)).scalar_one_or_none()
    if symbol is None:
        symbol = Symbol(ticker=ticker, active=True)
        session.add(symbol)
        session.flush()
    session.add(
        DailyBar(
            symbol_id=symbol.id,
            session_date=session_date,
            open=Decimal(close),
            high=Decimal(close),
            low=Decimal(close),
            close=Decimal(close),
            volume=1_000_000,
            adjusted=True,
            provider="polygon",
        )
    )
    session.flush()


def test_submission_defaults_none_without_sessions(migrated_ingest_db: str) -> None:
    spec = IngestBarsSubmissionSpec(load_settings())

    assert spec.submission_defaults() is None


def test_submission_defaults_from_latest_session(migrated_ingest_db: str) -> None:
    settings = load_settings()
    from trading_platform.services.calendar import upsert_market_sessions

    with session_scope(settings) as session:
        upsert_market_sessions(session, date(2024, 1, 3), date(2024, 1, 5))
        _seed_bar(session, ticker="AAPL", session_date=date(2024, 1, 5), close="100")

    spec = IngestBarsSubmissionSpec(settings)
    defaults = spec.submission_defaults()

    assert defaults is not None
    assert defaults["to_date"] == "2024-01-05"
    lookback_days = settings.market_data.ingest.default_lookback_days
    expected_from = (date(2024, 1, 5) - timedelta(days=lookback_days)).isoformat()
    assert defaults["from_date"] == expected_from
    assert defaults["symbols"] == ",".join(
        sorted({s.upper() for s in settings.market_data.ingest.universe})
    )


def test_spec_satisfies_registry_contract() -> None:
    class _MinimalHandler:
        job_type = "ingest-bars"

        def run(self, context: object) -> Mapping[str, Any]:
            return {}

    registry = JobRegistry()
    registry.register(_MinimalHandler(), submission_spec=IngestBarsSubmissionSpec(load_settings()))

    assert registry.list_job_types() == ["ingest-bars"]


def test_spec_declares_no_retry_prerequisite() -> None:
    spec = IngestBarsSubmissionSpec(load_settings())

    assert retry_prerequisite_for(spec) is None


# ---------------------------------------------------------------------------
# IngestBarsJobHandler (Task 1, handler half)
# ---------------------------------------------------------------------------


class _FakeContext:
    def __init__(self, *, job_id: uuid.UUID | None = None, payload: Mapping[str, Any] | None = None) -> None:
        self.job_id = job_id or uuid.uuid4()
        self.job_type = "ingest-bars"
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


def _fake_result(**overrides: Any) -> IngestionResult:
    defaults: dict[str, Any] = {
        "provider": "polygon",
        "from_date": date(2024, 1, 8),
        "to_date": date(2024, 1, 10),
        "symbols_requested": ["AAPL", "SPY"],
        "bars_upserted": 6,
        "symbols_failed": [],
        "run_id": str(uuid.uuid4()),
        "run_status": "succeeded",
    }
    defaults.update(overrides)
    return IngestionResult(**defaults)


def test_handler_progress_steps_have_no_percent(monkeypatch: pytest.MonkeyPatch) -> None:
    import trading_platform.jobs.handlers.ingest_bars as ingest_bars_module

    monkeypatch.setattr(
        ingest_bars_module, "ingest_daily_bars", lambda **kwargs: _fake_result()
    )

    context = _FakeContext()
    handler = IngestBarsJobHandler()
    handler.run(context)

    assert context.progress_calls == [
        {"step": STEP_RESOLVING},
        {"step": STEP_INGESTING},
        {"step": STEP_RECORDING},
    ]
    for call in context.progress_calls:
        assert "percent" not in call


def test_cancel_before_start_never_calls_service(monkeypatch: pytest.MonkeyPatch) -> None:
    call_count = 0

    def _fake_ingest_daily_bars(**kwargs: Any) -> IngestionResult:
        nonlocal call_count
        call_count += 1
        return _fake_result()

    import trading_platform.jobs.handlers.ingest_bars as ingest_bars_module

    monkeypatch.setattr(ingest_bars_module, "ingest_daily_bars", _fake_ingest_daily_bars)

    context = _FakeContext()
    context.cancelled = True
    handler = IngestBarsJobHandler()

    with pytest.raises(JobCancelledError):
        handler.run(context)

    assert call_count == 0


def test_cancel_during_call_acknowledged_after_service(monkeypatch: pytest.MonkeyPatch) -> None:
    call_count = 0

    def _fake_ingest_daily_bars(**kwargs: Any) -> IngestionResult:
        nonlocal call_count
        call_count += 1
        context.cancelled = True
        return _fake_result()

    import trading_platform.jobs.handlers.ingest_bars as ingest_bars_module

    monkeypatch.setattr(ingest_bars_module, "ingest_daily_bars", _fake_ingest_daily_bars)

    context = _FakeContext()
    handler = IngestBarsJobHandler()

    with pytest.raises(JobCancelledError):
        handler.run(context)

    assert call_count == 1


def test_handler_passes_job_id_trigger_source_db_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_ingest_daily_bars(**kwargs: Any) -> IngestionResult:
        captured.update(kwargs)
        return _fake_result()

    import trading_platform.jobs.handlers.ingest_bars as ingest_bars_module

    monkeypatch.setattr(ingest_bars_module, "ingest_daily_bars", _fake_ingest_daily_bars)

    context = _FakeContext()
    handler = IngestBarsJobHandler()
    handler.run(context)

    assert captured["job_id"] == context.job_id
    assert captured["trigger_source"] == "job"
    assert captured["db_settings"] is not None
    assert captured["from_date"] == date(2024, 1, 8)
    assert captured["to_date"] == date(2024, 1, 10)
    assert captured["symbols"] == ["AAPL", "SPY"]


def test_result_summary_carries_expected_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _fake_result()

    import trading_platform.jobs.handlers.ingest_bars as ingest_bars_module

    monkeypatch.setattr(ingest_bars_module, "ingest_daily_bars", lambda **kwargs: result)

    context = _FakeContext()
    handler = IngestBarsJobHandler()
    summary = handler.run(context)

    assert summary == {
        "run_id": result.run_id,
        "produced_run_ids": [result.run_id],
        "from_date": "2024-01-08",
        "to_date": "2024-01-10",
        "symbol_count": result.symbol_count,
        "bars_upserted": result.bars_upserted,
        "symbols_failed": result.symbols_failed,
        "ingestion_succeeded": result.succeeded,
    }
    json.dumps(summary)


def test_handler_declares_backtest_mode() -> None:
    assert IngestBarsJobHandler.required_execution_mode is ExecutionMode.BACKTEST
    assert required_mode_preflight(IngestBarsJobHandler()) is None


def test_handler_source_has_no_percent_progress() -> None:
    import trading_platform.jobs.handlers.ingest_bars as ingest_bars_module

    source = __import__("inspect").getsource(ingest_bars_module)
    assert "percent=" not in source


# ---------------------------------------------------------------------------
# Service-level run_id (D-08/D-09)
# ---------------------------------------------------------------------------


def _polygon_response() -> MagicMock:
    fixture = json.loads(FIXTURE_PATH.read_text())
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = fixture
    return mock_response


def test_ingest_daily_bars_run_id_matches_created_run(migrated_ingest_db: str) -> None:
    settings = load_settings()
    md_settings = _make_market_data_settings()

    with patch("httpx.Client.get", return_value=_polygon_response()):
        result = ingest_daily_bars(
            from_date=date(2024, 1, 1),
            to_date=date(2024, 1, 3),
            symbols=["AAPL"],
            settings=md_settings,
            trigger_source="job",
            db_settings=settings,
        )

    assert result.run_id is not None

    with session_scope(settings) as session:
        run = session.execute(select(MarketDataIngestionRun)).scalars().one()

    assert result.run_id == str(run.id)
