"""BrokerOrderSyncSubmissionSpec + BrokerOrderSyncJobHandler tests (OPS-06,
D-01, D-19, D-21, D-22, D-25).

Mirrors ``tests.test_reconciliation_job_type`` function-by-function,
substituting the queued-only cancellation contract (D-01/D-02: no
cooperative-cancellation checkpoint anywhere in the handler -- a cancel
request against a RUNNING broker-order-sync Job is rejected upstream, never
observed here) and the opaque state-sync service call
(``sync_paper_state``, D-19: the handler logs an ``external_``-prefixed
marker immediately before calling it, so a later ``handler_error`` is
recorded ``outcome_uncertain=true``). Reuses the same Postgres fixtures for
the ``submission_defaults`` tests that touch the database.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
from typing import Any, Mapping

import pytest
from tests.support.calendar_facts import clock_at, et, seed_bars, seed_calendar
from tests.test_backtest_runner import (
    migrated_backtest_db,
    strategy_config_override,
)

from trading_platform.core.settings import load_settings
from trading_platform.jobs.contracts import JobCancelledError
from trading_platform.jobs.handlers.broker_order_sync import (
    STEP_RECORDING,
    STEP_RESOLVING,
    STEP_SYNCING,
    BrokerOrderSyncJobHandler,
)
from trading_platform.jobs.handlers.broker_order_sync_submission import (
    BrokerOrderSyncPayloadRejection,
    BrokerOrderSyncSubmissionSpec,
)
from trading_platform.jobs.registry import (
    InvalidJobPayloadError,
    JobCancellationMode,
    JobRegistry,
    retry_prerequisite_for,
)
from trading_platform.services.config.validation import ExecutionMode
from trading_platform.services.execution import PaperStateSyncReport
from trading_platform.worker.commands.run_jobs import required_mode_preflight

__all__ = ["migrated_backtest_db", "strategy_config_override"]

_VALID_PAYLOAD = {
    "strategy_id": "trend_following_daily",
    "as_of_session": "2024-01-10",
}


# ---------------------------------------------------------------------------
# BrokerOrderSyncSubmissionSpec (Task 1, spec half)
# ---------------------------------------------------------------------------


def test_rejection_enum_is_closed() -> None:
    assert {member.value for member in BrokerOrderSyncPayloadRejection} == {
        "unknown_payload_keys",
        "missing_required_field",
        "invalid_field_type",
        "invalid_date",
        "unknown_strategy_id",
        "as_of_session_in_future",
        "as_of_session_not_trading_session",
        "as_of_session_out_of_calendar_range",
        "invalid_scope",
        "account_scope_forbids_strategy_id",
    }


@pytest.mark.parametrize(
    ("payload", "expected_reason"),
    [
        pytest.param(
            {**_VALID_PAYLOAD, "extra": 1},
            BrokerOrderSyncPayloadRejection.UNKNOWN_PAYLOAD_KEYS,
            id="unknown_payload_keys",
        ),
        pytest.param(
            {"strategy_id": "trend_following_daily"},
            BrokerOrderSyncPayloadRejection.MISSING_REQUIRED_FIELD,
            id="missing_required_field",
        ),
        pytest.param(
            {**_VALID_PAYLOAD, "strategy_id": 123},
            BrokerOrderSyncPayloadRejection.INVALID_FIELD_TYPE,
            id="invalid_field_type",
        ),
        pytest.param(
            {**_VALID_PAYLOAD, "as_of_session": "2024-13-01"},
            BrokerOrderSyncPayloadRejection.INVALID_DATE,
            id="invalid_date",
        ),
        pytest.param(
            {**_VALID_PAYLOAD, "strategy_id": "nope"},
            BrokerOrderSyncPayloadRejection.UNKNOWN_STRATEGY_ID,
            id="unknown_strategy_id",
        ),
        pytest.param(
            {**_VALID_PAYLOAD, "as_of_session": "2026-01-06"},
            BrokerOrderSyncPayloadRejection.AS_OF_SESSION_IN_FUTURE,
            id="as_of_session_in_future",
        ),
        pytest.param(
            {**_VALID_PAYLOAD, "as_of_session": "2024-01-06"},
            BrokerOrderSyncPayloadRejection.AS_OF_SESSION_NOT_TRADING_SESSION,
            id="as_of_session_not_trading_session",
        ),
        pytest.param(
            {**_VALID_PAYLOAD, "as_of_session": "2000-01-03"},
            BrokerOrderSyncPayloadRejection.AS_OF_SESSION_OUT_OF_CALENDAR_RANGE,
            id="as_of_session_out_of_calendar_range_2000-01-03",
        ),
        pytest.param(
            {**_VALID_PAYLOAD, "as_of_session": "0001-01-01"},
            BrokerOrderSyncPayloadRejection.AS_OF_SESSION_OUT_OF_CALENDAR_RANGE,
            id="as_of_session_out_of_calendar_range_0001-01-01",
        ),
        pytest.param(
            {**_VALID_PAYLOAD, "scope": "everything"},
            BrokerOrderSyncPayloadRejection.INVALID_SCOPE,
            id="invalid_scope",
        ),
        pytest.param(
            {**_VALID_PAYLOAD, "scope": 7},
            BrokerOrderSyncPayloadRejection.INVALID_SCOPE,
            id="invalid_scope_non_string",
        ),
        pytest.param(
            {"scope": "account", "strategy_id": "trend_following_daily"},
            BrokerOrderSyncPayloadRejection.ACCOUNT_SCOPE_FORBIDS_STRATEGY_ID,
            id="account_scope_forbids_strategy_id",
        ),
        pytest.param(
            {"scope": "account", "extra": 1},
            BrokerOrderSyncPayloadRejection.UNKNOWN_PAYLOAD_KEYS,
            id="account_scope_unknown_payload_keys",
        ),
        pytest.param(
            {"scope": "account", "as_of_session": "2024-13-01"},
            BrokerOrderSyncPayloadRejection.INVALID_DATE,
            id="account_scope_invalid_date",
        ),
        pytest.param(
            {"scope": "account", "as_of_session": "2026-01-06"},
            BrokerOrderSyncPayloadRejection.AS_OF_SESSION_IN_FUTURE,
            id="account_scope_as_of_session_in_future",
        ),
        pytest.param(
            {"scope": "account", "as_of_session": "2024-01-06"},
            BrokerOrderSyncPayloadRejection.AS_OF_SESSION_NOT_TRADING_SESSION,
            id="account_scope_as_of_session_not_trading_session",
        ),
    ],
)
def test_validate_payload_rejects(
    payload: dict[str, Any], expected_reason: BrokerOrderSyncPayloadRejection
) -> None:
    spec = BrokerOrderSyncSubmissionSpec(
        load_settings(), clock=lambda: datetime(2026, 1, 6, 3, 0, tzinfo=UTC)
    )

    with pytest.raises(InvalidJobPayloadError) as exc_info:
        spec.validate_payload(payload)

    assert exc_info.value.job_type == "broker-order-sync"
    assert exc_info.value.reason == expected_reason.value


def test_validate_payload_normalizes() -> None:
    spec = BrokerOrderSyncSubmissionSpec(load_settings())

    normalized = spec.validate_payload(
        {"strategy_id": "  trend_following_daily  ", "as_of_session": "2024-01-10"}
    )

    assert normalized == {
        "strategy_id": "trend_following_daily",
        "as_of_session": "2024-01-10",
    }


def test_omitted_and_explicit_strategy_scope_normalize_identically() -> None:
    spec = BrokerOrderSyncSubmissionSpec(load_settings())

    assert (
        spec.validate_payload(dict(_VALID_PAYLOAD))
        == spec.validate_payload({**_VALID_PAYLOAD, "scope": "strategy"})
        == _VALID_PAYLOAD
    )


def test_account_scope_normalizes_with_and_without_a_session() -> None:
    spec = BrokerOrderSyncSubmissionSpec(
        load_settings(), clock=lambda: datetime(2026, 1, 6, 3, 0, tzinfo=UTC)
    )

    assert spec.validate_payload({"scope": "account"}) == {"scope": "account"}
    assert spec.validate_payload({"scope": "account", "as_of_session": "2024-01-10"}) == {
        "scope": "account",
        "as_of_session": "2024-01-10",
    }


def test_spec_declares_reads_broker_effect() -> None:
    from trading_platform.services.broker_jobs import BrokerEffect

    assert BrokerOrderSyncSubmissionSpec(load_settings()).broker_effect is BrokerEffect.READS_BROKER


def test_submission_defaults_none_without_sessions(migrated_backtest_db: str) -> None:
    spec = BrokerOrderSyncSubmissionSpec(load_settings())

    assert spec.submission_defaults() is None


def test_submission_defaults_are_the_evaluation_candidate_session(
    migrated_backtest_db: str,
) -> None:
    """D-24: the pre-fill is the calendar-completed evaluation candidate
    (latest persisted session closed before the clock), never the latest
    session that merely has bars."""

    seed_calendar(date(2025, 11, 20), date(2026, 3, 31))
    seed_bars(["AAA"], [date(2025, 12, 2), date(2025, 12, 3)])
    settings = load_settings()
    spec = BrokerOrderSyncSubmissionSpec(settings, clock=clock_at(et(2025, 12, 2, 10, 0)))

    assert spec.submission_defaults() == {"as_of_session": "2025-12-01"}


def test_defaults_are_none_when_calendar_unavailable(migrated_backtest_db: str) -> None:
    """D-24/04 acceptance: the calendar ending 2026-03-13 evaluated at
    2026-09-29 offers NO default (never 2026-03-13), even with bars for it."""

    seed_calendar(date(2026, 1, 2), date(2026, 3, 13))
    seed_bars(["AAA"], [date(2026, 3, 13)])
    spec = BrokerOrderSyncSubmissionSpec(load_settings(), clock=clock_at(et(2026, 9, 29, 10, 0)))

    assert spec.submission_defaults() is None


def test_spec_satisfies_registry_contract() -> None:
    class _MinimalHandler:
        job_type = "broker-order-sync"

        def run(self, context: object) -> Mapping[str, Any]:
            return {}

    registry = JobRegistry()
    registry.register(
        _MinimalHandler(), submission_spec=BrokerOrderSyncSubmissionSpec(load_settings())
    )

    assert registry.list_job_types() == ["broker-order-sync"]


def test_spec_declares_reconciliation_retry_prerequisite() -> None:
    spec = BrokerOrderSyncSubmissionSpec(load_settings())

    assert retry_prerequisite_for(spec) == "reconciliation"


def test_cancellation_mode_is_queued_only() -> None:
    spec = BrokerOrderSyncSubmissionSpec(load_settings())

    assert spec.cancellation_mode is JobCancellationMode.QUEUED_ONLY
    assert "queued" in spec.description.lower()


def test_job_type_matches_on_spec_and_handler() -> None:
    assert BrokerOrderSyncSubmissionSpec.job_type == "broker-order-sync"
    assert BrokerOrderSyncJobHandler.job_type == "broker-order-sync"


# ---------------------------------------------------------------------------
# BrokerOrderSyncJobHandler (Task 1, handler half)
# ---------------------------------------------------------------------------


class _FakeContext:
    def __init__(
        self, *, job_id: uuid.UUID | None = None, payload: Mapping[str, Any] | None = None
    ) -> None:
        self.job_id = job_id or uuid.uuid4()
        self.job_type = "broker-order-sync"
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


def _fake_report(**overrides: Any) -> PaperStateSyncReport:
    defaults: dict[str, Any] = {
        "strategy_id": "trend_following_daily",
        "session_date": "2024-01-10",
        "synced_at": "2024-01-10T00:05:00+00:00",
        "orders_synced": 0,
        "fills_ingested": 0,
        "positions_opened": 0,
        "positions_closed": 0,
        "open_positions": 0,
        "account_snapshot_id": str(uuid.uuid4()),
    }
    defaults.update(overrides)
    return PaperStateSyncReport(**defaults)


def _fake_sync_paper_state_factory(report: PaperStateSyncReport, captured: dict[str, Any]):
    """A discriminating fake matching ``sync_paper_state``'s real signature --
    a stray/renamed kwarg raises TypeError instead of silently passing."""

    def _fake(
        strategy_id: str | None = None,
        *,
        as_of_session: date,
        settings: Any = None,
        registry: Any = None,
        broker_client: Any = None,
    ) -> PaperStateSyncReport:
        captured["strategy_id"] = strategy_id
        captured["as_of_session"] = as_of_session
        captured["settings"] = settings
        return report

    return _fake


def test_handler_passes_strategy_id_explicitly_and_as_of_session_as_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    import trading_platform.jobs.handlers.broker_order_sync as broker_order_sync_module

    monkeypatch.setattr(
        broker_order_sync_module,
        "sync_paper_state",
        _fake_sync_paper_state_factory(_fake_report(), captured),
    )

    context = _FakeContext()
    handler = BrokerOrderSyncJobHandler()
    handler.run(context)

    assert captured["strategy_id"] == "trend_following_daily"
    assert captured["strategy_id"] is not None
    assert isinstance(captured["as_of_session"], date)
    assert captured["as_of_session"] == date(2024, 1, 10)


def test_handler_progress_steps_have_no_percent(monkeypatch: pytest.MonkeyPatch) -> None:
    import trading_platform.jobs.handlers.broker_order_sync as broker_order_sync_module

    monkeypatch.setattr(
        broker_order_sync_module,
        "sync_paper_state",
        _fake_sync_paper_state_factory(_fake_report(), {}),
    )

    context = _FakeContext()
    handler = BrokerOrderSyncJobHandler()
    handler.run(context)

    assert context.progress_calls == [
        {"step": STEP_RESOLVING},
        {"step": STEP_SYNCING},
        {"step": STEP_RECORDING},
    ]
    for call in context.progress_calls:
        assert "percent" not in call


def test_handler_logs_external_marker_before_service_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D-19: the external_* marker must be recorded BEFORE sync_paper_state
    is invoked, so any later handler_error is recorded outcome_uncertain."""

    order: list[str] = []

    class _OrderedContext(_FakeContext):
        def log(self, *, level: str, event_code: str, message: str, context=None) -> None:
            order.append(f"log:{event_code}")
            super().log(level=level, event_code=event_code, message=message, context=context)

    def _fake_sync_paper_state(
        strategy_id: str | None = None, **kwargs: Any
    ) -> PaperStateSyncReport:
        order.append("service_called")
        return _fake_report()

    import trading_platform.jobs.handlers.broker_order_sync as broker_order_sync_module

    monkeypatch.setattr(broker_order_sync_module, "sync_paper_state", _fake_sync_paper_state)

    context = _OrderedContext()
    handler = BrokerOrderSyncJobHandler()
    handler.run(context)

    assert order == [
        "log:external_broker_sync_started",
        "service_called",
        "log:broker_order_sync_completed",
    ]


def test_handler_never_checks_cancellation(monkeypatch: pytest.MonkeyPatch) -> None:
    """D-01/D-02: queued-only cancellation -- a RUNNING broker-order-sync Job
    is never cancellable, so this handler must never call
    ``raise_if_cancelled``. Proven both at the source level (no call exists)
    and at runtime (a cancellation-requested context still runs the service
    call to completion instead of raising)."""

    import inspect

    import trading_platform.jobs.handlers.broker_order_sync as broker_order_sync_module

    source = inspect.getsource(broker_order_sync_module)
    assert "raise_if_cancelled" not in source
    assert "default_strategy_id" not in source

    call_count = 0

    def _fake_sync_paper_state(
        strategy_id: str | None = None, **kwargs: Any
    ) -> PaperStateSyncReport:
        nonlocal call_count
        call_count += 1
        return _fake_report()

    monkeypatch.setattr(broker_order_sync_module, "sync_paper_state", _fake_sync_paper_state)

    context = _FakeContext()
    context.cancelled = True
    handler = BrokerOrderSyncJobHandler()

    result = handler.run(context)

    assert call_count == 1
    assert result["strategy_id"]


def test_result_summary_carries_expected_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    report = _fake_report(
        orders_synced=3,
        fills_ingested=2,
        positions_opened=1,
        positions_closed=0,
        open_positions=4,
    )

    import trading_platform.jobs.handlers.broker_order_sync as broker_order_sync_module

    monkeypatch.setattr(
        broker_order_sync_module,
        "sync_paper_state",
        lambda strategy_id=None, **kwargs: report,
    )

    context = _FakeContext()
    handler = BrokerOrderSyncJobHandler()
    result = handler.run(context)

    assert result == {
        "strategy_id": report.strategy_id,
        "as_of_session": "2024-01-10",
        "orders_synced": 3,
        "fills_ingested": 2,
        "positions_opened": 1,
        "positions_closed": 0,
        "open_positions": 4,
        "account_snapshot_id": report.account_snapshot_id,
        "snapshot_id": report.account_snapshot_id,
        "applied_orders": [],
        "produced_run_ids": [],
    }
    json.dumps(result)


_APPLIED = [
    {
        "paper_order_id": "11111111-1111-1111-1111-111111111111",
        "broker_status": "filled",
        "broker_filled_qty": "10.000000",
        "applied_at": "2024-01-10T00:05:00+00:00",
    },
    {
        "paper_order_id": "22222222-2222-2222-2222-222222222222",
        "broker_status": "canceled",
        "broker_filled_qty": "0.000000",
        "applied_at": "2024-01-10T00:05:00+00:00",
    },
]


def test_strategy_result_records_basis_traceability(monkeypatch: pytest.MonkeyPatch) -> None:
    """S3-R4: snapshot_id and applied_orders for a sync that applied a filled and a canceled order."""

    report = _fake_report(orders_synced=2, applied_orders=tuple(_APPLIED))

    import trading_platform.jobs.handlers.broker_order_sync as broker_order_sync_module

    monkeypatch.setattr(
        broker_order_sync_module,
        "sync_paper_state",
        lambda strategy_id=None, **kwargs: report,
    )

    result = BrokerOrderSyncJobHandler().run(_FakeContext())

    assert result["snapshot_id"] == report.account_snapshot_id
    assert result["applied_orders"] == _APPLIED
    assert {record["broker_status"] for record in result["applied_orders"]} == {
        "filled",
        "canceled",
    }
    for record in result["applied_orders"]:
        assert set(record) == {"paper_order_id", "broker_status", "broker_filled_qty", "applied_at"}
    json.dumps(result)


def test_account_scope_handler_calls_sync_account_state_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import trading_platform.jobs.handlers.broker_order_sync as broker_order_sync_module
    from trading_platform.services.execution import AccountStateSyncReport

    report = AccountStateSyncReport(
        session_date=None,
        synced_at="2024-01-10T00:05:00+00:00",
        orders_synced=2,
        fills_ingested=1,
        open_positions=3,
        account_snapshot_id=str(uuid.uuid4()),
        applied_orders=tuple(_APPLIED),
    )
    captured: dict[str, Any] = {}

    def _fake_sync_account_state(*, as_of_session=None, settings=None, broker_client=None):
        captured["as_of_session"] = as_of_session
        return report

    def _fail(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("strategy-scope sync must not run at account scope")

    monkeypatch.setattr(broker_order_sync_module, "sync_account_state", _fake_sync_account_state)
    monkeypatch.setattr(broker_order_sync_module, "sync_paper_state", _fail)

    context = _FakeContext(payload={"scope": "account"})
    result = BrokerOrderSyncJobHandler().run(context)

    assert captured["as_of_session"] is None
    assert [log["event_code"] for log in context.log_calls][0] == "external_broker_sync_started"
    assert result == {
        "scope": "account",
        "strategy_id": None,
        "as_of_session": None,
        "orders_synced": 2,
        "fills_ingested": 1,
        "positions_opened": 0,
        "positions_closed": 0,
        "open_positions": 3,
        "account_snapshot_id": report.account_snapshot_id,
        "snapshot_id": report.account_snapshot_id,
        "applied_orders": _APPLIED,
        "produced_run_ids": [],
    }
    json.dumps(result)


def test_service_failure_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(strategy_id: str | None = None, **kwargs: Any) -> PaperStateSyncReport:
        raise RuntimeError("boom")

    import trading_platform.jobs.handlers.broker_order_sync as broker_order_sync_module

    monkeypatch.setattr(broker_order_sync_module, "sync_paper_state", _raise)

    context = _FakeContext()
    handler = BrokerOrderSyncJobHandler()

    with pytest.raises(RuntimeError):
        handler.run(context)


def test_handler_declares_paper_mode() -> None:
    # D-22 (P19): `broker-order-sync` requires PAPER-level config (broker
    # credentials), like its `reconciliation`/`paper-session` siblings.
    # Whether broker credentials happen to be configured in the running
    # environment is out of this unit test's scope (see
    # tests/test_job_runner_preflight.py for the CONFIG_INVALID contract);
    # this test only pins the declared mode and that the handler is
    # recognized as a mode-declaring handler at all (a non-None result
    # either way -- valid config returns None, invalid config returns a
    # message -- both prove `required_mode_preflight` evaluated PAPER, not
    # the "no required_execution_mode declared" failure-closed branch).
    assert BrokerOrderSyncJobHandler.required_execution_mode is ExecutionMode.PAPER
    preflight_result = required_mode_preflight(BrokerOrderSyncJobHandler())
    assert preflight_result is None or "paper mode" in preflight_result
