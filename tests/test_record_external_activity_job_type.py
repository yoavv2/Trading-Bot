"""RecordExternalActivitySubmissionSpec + RecordExternalActivityJobHandler tests
(EXT-01, D-10, R-18).

Spec half: strict payload, one parametrized case per closed rejection value,
normalization, catalog attributes, SER admission hook. Handler half: record then fresh
account reconciliation in ONE run sharing ONE broker client, closed refusal translated
into a domain conflict, no ``external_``-prefixed event code. Production path: POST
/api/v1/jobs then ``run-jobs --once`` over the real handler and services with a scripted
broker.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any, Mapping

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from tests.test_external_activity import (
    ExtBroker,
    _ext_fill,
    _ext_order,
    _pair_broker,
)
from tests.test_paper_session_job_e2e import (
    BrokerFakes,
    _log_codes,
    _submit,
    _submit_and_run,
    job_operations_env,  # noqa: F401
    migrated_backtest_db,  # noqa: F401
    paper_jobs_env,  # noqa: F401
    strategy_config_override,  # noqa: F401
)

from trading_platform.api.app import create_app
from trading_platform.core.settings import load_settings
from trading_platform.db.models import (
    AccountReconciliationRun,
    ExternalBrokerActivity,
    PaperFill,
    PaperOrder,
    Position,
)
from trading_platform.db.session import session_scope
from trading_platform.jobs.contracts import JobDomainConflictError
from trading_platform.jobs.handlers import record_external_activity as handler_module
from trading_platform.jobs.handlers.record_external_activity import (
    RecordExternalActivityJobHandler,
)
from trading_platform.jobs.handlers.record_external_activity_submission import (
    RecordExternalActivityPayloadRejection,
    RecordExternalActivitySubmissionSpec,
)
from trading_platform.jobs.registry import (
    InvalidJobPayloadError,
    JobCancellationMode,
    admission_check_for,
    admission_lock_for,
    build_default_registry,
    retry_prerequisite_for,
)
from trading_platform.services.broker_jobs import BrokerEffect
from trading_platform.services.config.validation import ExecutionMode
from trading_platform.services.external_activity import (
    ExternalActivityRecordResult,
    ExternalActivityRejectedError,
    ExternalActivityRejection,
)
from trading_platform.services.reconciliation.account import AccountReconciliationReport

__all__ = ["migrated_backtest_db", "strategy_config_override"]

_VALID = {"order_ids": ["order-1", "order-2"], "reason": "manual round trip in the console"}


def _spec() -> RecordExternalActivitySubmissionSpec:
    return RecordExternalActivitySubmissionSpec(load_settings())


# --- spec -------------------------------------------------------------------------------


def test_rejection_enum_is_closed() -> None:
    assert {member.value for member in RecordExternalActivityPayloadRejection} == {
        "unknown_payload_keys",
        "missing_required_field",
        "invalid_field_type",
        "order_ids_empty",
        "order_ids_too_many",
        "duplicate_order_ids",
        "invalid_order_id",
        "invalid_reason",
    }


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        pytest.param(
            {**_VALID, "extra": 1},
            RecordExternalActivityPayloadRejection.UNKNOWN_PAYLOAD_KEYS,
            id="unknown_payload_keys",
        ),
        pytest.param(
            {**_VALID, "strategy_id": "trend_following_daily"},
            RecordExternalActivityPayloadRejection.UNKNOWN_PAYLOAD_KEYS,
            id="strategy_id_is_an_unknown_key",
        ),
        pytest.param(
            {**_VALID, "as_of_session": "2024-01-05"},
            RecordExternalActivityPayloadRejection.UNKNOWN_PAYLOAD_KEYS,
            id="as_of_session_is_an_unknown_key",
        ),
        pytest.param(
            {"order_ids": ["a"]},
            RecordExternalActivityPayloadRejection.MISSING_REQUIRED_FIELD,
            id="missing_reason",
        ),
        pytest.param(
            {"reason": "r"},
            RecordExternalActivityPayloadRejection.MISSING_REQUIRED_FIELD,
            id="missing_order_ids",
        ),
        pytest.param(
            {"order_ids": "order-1", "reason": "r"},
            RecordExternalActivityPayloadRejection.INVALID_FIELD_TYPE,
            id="order_ids_not_a_list",
        ),
        pytest.param(
            {"order_ids": [1], "reason": "r"},
            RecordExternalActivityPayloadRejection.INVALID_FIELD_TYPE,
            id="order_id_not_a_string",
        ),
        pytest.param(
            {"order_ids": ["a"], "reason": 5},
            RecordExternalActivityPayloadRejection.INVALID_FIELD_TYPE,
            id="reason_not_a_string",
        ),
        pytest.param(
            {"order_ids": [], "reason": "r"},
            RecordExternalActivityPayloadRejection.ORDER_IDS_EMPTY,
            id="order_ids_empty",
        ),
        pytest.param(
            {"order_ids": [f"o-{i}" for i in range(51)], "reason": "r"},
            RecordExternalActivityPayloadRejection.ORDER_IDS_TOO_MANY,
            id="order_ids_too_many",
        ),
        pytest.param(
            {"order_ids": ["a", "b", "a"], "reason": "r"},
            RecordExternalActivityPayloadRejection.DUPLICATE_ORDER_IDS,
            id="duplicate_order_ids",
        ),
        pytest.param(
            {"order_ids": ["a", " a "], "reason": "r"},
            RecordExternalActivityPayloadRejection.DUPLICATE_ORDER_IDS,
            id="duplicate_after_strip",
        ),
        pytest.param(
            {"order_ids": ["   "], "reason": "r"},
            RecordExternalActivityPayloadRejection.INVALID_ORDER_ID,
            id="blank_order_id",
        ),
        pytest.param(
            {"order_ids": ["x" * 65], "reason": "r"},
            RecordExternalActivityPayloadRejection.INVALID_ORDER_ID,
            id="order_id_too_long",
        ),
        pytest.param(
            {"order_ids": ["ab\x00c"], "reason": "r"},
            RecordExternalActivityPayloadRejection.INVALID_ORDER_ID,
            id="order_id_with_nul",
        ),
        pytest.param(
            {"order_ids": ["a"], "reason": "   "},
            RecordExternalActivityPayloadRejection.INVALID_REASON,
            id="blank_reason",
        ),
        pytest.param(
            {"order_ids": ["a"], "reason": "r" * 501},
            RecordExternalActivityPayloadRejection.INVALID_REASON,
            id="reason_too_long",
        ),
        pytest.param(
            {"order_ids": ["a"], "reason": "bad\x00reason"},
            RecordExternalActivityPayloadRejection.INVALID_REASON,
            id="reason_with_nul",
        ),
    ],
)
def test_validate_payload_rejections(
    payload: Mapping[str, Any], expected: RecordExternalActivityPayloadRejection
) -> None:
    with pytest.raises(InvalidJobPayloadError) as excinfo:
        _spec().validate_payload(payload)
    assert excinfo.value.job_type == "record-external-activity"
    assert excinfo.value.reason == expected.value


def test_every_rejection_value_has_a_parametrized_case() -> None:
    covered = {
        case.values[1]
        for case in test_validate_payload_rejections.pytestmark[0].args[1]  # type: ignore[attr-defined]
    }
    assert covered == set(RecordExternalActivityPayloadRejection)


def test_payload_boundaries_are_accepted() -> None:
    normalized = _spec().validate_payload(
        {"order_ids": [f"{i:02d}" + "x" * 62 for i in range(50)], "reason": "r" * 500}
    )
    assert len(normalized["order_ids"]) == 50
    assert all(len(value) == 64 for value in normalized["order_ids"])
    assert len(normalized["reason"]) == 500


def test_payload_is_normalized_stripped_and_in_original_order() -> None:
    normalized = _spec().validate_payload(
        {"order_ids": ["  zz-9 ", "aa-1"], "reason": "  manual trade  "}
    )
    assert normalized == {"order_ids": ["zz-9", "aa-1"], "reason": "manual trade"}


def test_spec_attributes_and_catalog_metadata() -> None:
    spec = _spec()
    assert spec.job_type == "record-external-activity"
    assert spec.cancellation_mode is JobCancellationMode.QUEUED_ONLY
    assert spec.broker_effect is BrokerEffect.READS_BROKER
    assert retry_prerequisite_for(spec) is None
    assert 1 <= len(spec.description) <= 200
    assert spec.submission_defaults() is None


def test_registry_registers_the_type_with_its_handler_and_spec() -> None:
    registry = build_default_registry(load_settings())
    assert "record-external-activity" in registry.list_job_types()
    assert len(registry.list_job_types()) == 9
    handler = registry.resolve("record-external-activity")
    assert handler.required_execution_mode is ExecutionMode.PAPER
    spec = registry.resolve_submission_spec("record-external-activity")
    assert isinstance(spec, RecordExternalActivitySubmissionSpec)


def test_admission_takes_the_ownership_singleton_shared_lock_and_needs_no_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from trading_platform.jobs.handlers import record_external_activity_submission as module

    locked: list[object] = []
    monkeypatch.setattr(module, "lock_active_paper_strategy_shared", locked.append)
    spec = _spec()
    sentinel = object()

    assert admission_check_for(spec) is not None
    assert admission_lock_for(spec) is not None
    spec.check_admission(dict(_VALID), session=sentinel)
    assert locked == [sentinel]
    spec.lock_admission(session=sentinel)
    assert locked == [sentinel, sentinel]


# --- handler (fake services) --------------------------------------------------------------


class _FakeContext:
    def __init__(self, payload: Mapping[str, Any] | None = None) -> None:
        self.job_id = uuid.uuid4()
        self.job_type = "record-external-activity"
        self.payload = dict(payload or _VALID)
        self.progress_calls: list[dict[str, Any]] = []
        self.log_calls: list[dict[str, Any]] = []

    def report_progress(self, *, percent=None, step=None, current=None, total=None) -> None:
        call = {k: v for k, v in (("percent", percent), ("step", step)) if v is not None}
        self.progress_calls.append(call)

    def log(self, *, level: str, event_code: str, message: str, context=None) -> None:
        self.log_calls.append(
            {"level": level, "event_code": event_code, "message": message, "context": context}
        )

    def is_cancellation_requested(self) -> bool:
        return False

    def raise_if_cancelled(self) -> None:
        raise AssertionError("a queued-only handler has no cancellation checkpoint")


class _FakeClient:
    instances: list[_FakeClient] = []

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        self.closed = False
        _FakeClient.instances.append(self)

    def close(self) -> None:
        self.closed = True


def _fake_report(**overrides: Any) -> AccountReconciliationReport:
    defaults: dict[str, Any] = {
        "run_id": str(uuid.uuid4()),
        "as_of_session": None,
        "checked_at": "2024-01-05T15:00:00+00:00",
        "finding_count": 0,
        "blocking_count": 0,
        "blocks_execution": False,
    }
    defaults.update(overrides)
    return AccountReconciliationReport(**defaults)


@pytest.fixture()
def fake_services(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    _FakeClient.instances = []
    calls: dict[str, Any] = {"order": [], "report": _fake_report()}

    def fake_record(order_ids, reason, job_id=None, settings=None, broker_client=None):
        calls["order"].append("record")
        calls["record"] = {
            "order_ids": list(order_ids),
            "reason": reason,
            "job_id": job_id,
            "client": broker_client,
        }
        return ExternalActivityRecordResult(
            recorded_activity_ids=("act-1", "act-2"), already_recorded_order_ids=("order-9",)
        )

    def fake_reconcile(**kwargs: Any) -> AccountReconciliationReport:
        calls["order"].append("reconcile")
        calls["reconcile"] = kwargs
        return calls["report"]

    monkeypatch.setattr(handler_module, "AlpacaClient", _FakeClient)
    monkeypatch.setattr(handler_module, "record_external_orders", fake_record)
    monkeypatch.setattr(handler_module, "reconcile_account", fake_reconcile)
    return calls


def test_handler_records_then_runs_the_fresh_account_check_with_one_shared_client(
    fake_services: dict[str, Any],
) -> None:
    context = _FakeContext()
    result = RecordExternalActivityJobHandler().run(context)

    assert fake_services["order"] == ["record", "reconcile"]
    assert fake_services["record"]["job_id"] == context.job_id
    assert fake_services["record"]["order_ids"] == ["order-1", "order-2"]
    assert fake_services["record"]["reason"] == _VALID["reason"]
    reconcile = fake_services["reconcile"]
    assert reconcile["job_id"] == context.job_id
    assert reconcile["trigger_source"] == "record_external_activity"
    assert reconcile["broker_client"] is fake_services["record"]["client"]
    assert len(_FakeClient.instances) == 1
    assert _FakeClient.instances[0].closed is True

    report = fake_services["report"]
    assert result == {
        "scope": "account",
        "outcome": "clean",
        "recorded_activity_ids": ["act-1", "act-2"],
        "already_recorded_order_ids": ["order-9"],
        "run_id": report.run_id,
        "produced_run_ids": [report.run_id],
        "blocks_execution": False,
        "finding_count": 0,
        "blocking_count": 0,
        "unresolved_reasons": [],
    }


def test_blocking_fresh_check_is_a_domain_result_not_a_failure(
    fake_services: dict[str, Any],
) -> None:
    fake_services["report"] = _fake_report(
        blocks_execution=True,
        blocking_count=1,
        finding_count=1,
        unresolved_reasons=("broker_history_exceeds_cap",),
    )
    result = RecordExternalActivityJobHandler().run(_FakeContext())
    assert result["outcome"] == "blocking"
    assert result["blocks_execution"] is True
    assert result["unresolved_reasons"] == ["broker_history_exceeds_cap"]
    assert result["produced_run_ids"] == [result["run_id"]]


def test_handler_progress_is_step_text_only_and_logs_no_external_prefixed_event(
    fake_services: dict[str, Any],
) -> None:
    context = _FakeContext()
    RecordExternalActivityJobHandler().run(context)

    assert context.progress_calls
    assert all(set(call) == {"step"} for call in context.progress_calls)
    codes = [call["event_code"] for call in context.log_calls]
    assert "record_external_activity_recording_started" in codes
    assert "record_external_activity_recorded" in codes
    # The runner reads an ``external_`` prefix as "an external side effect may have
    # happened" and would mark a later handler error uncertain; this handler only reads.
    assert not [code for code in codes if code.startswith("external_")]


@pytest.mark.parametrize("reason", list(ExternalActivityRejection))
def test_rejected_error_is_a_certain_domain_conflict_with_the_reason_first(
    monkeypatch: pytest.MonkeyPatch, reason: ExternalActivityRejection
) -> None:
    _FakeClient.instances = []
    reconcile_calls: list[object] = []

    def refusing_record(*_args: Any, **_kwargs: Any):
        raise ExternalActivityRejectedError(reason, ["order-1"])

    monkeypatch.setattr(handler_module, "AlpacaClient", _FakeClient)
    monkeypatch.setattr(handler_module, "record_external_orders", refusing_record)
    monkeypatch.setattr(handler_module, "reconcile_account", reconcile_calls.append)

    context = _FakeContext()
    with pytest.raises(JobDomainConflictError) as excinfo:
        RecordExternalActivityJobHandler().run(context)

    assert excinfo.value.outcome_uncertain is False
    assert excinfo.value.message.split()[0] == f"{reason.value}:"
    assert reconcile_calls == []
    assert _FakeClient.instances[0].closed is True
    rejected = [
        c for c in context.log_calls if c["event_code"] == "record_external_activity_rejected"
    ]
    assert len(rejected) == 1
    assert rejected[0]["context"] == {"reason": reason.value, "order_ids": ["order-1"]}


def test_handler_declares_paper_mode_and_has_no_cancellation_checkpoint() -> None:
    import inspect

    assert RecordExternalActivityJobHandler.required_execution_mode is ExecutionMode.PAPER
    assert "raise_if_cancelled" not in inspect.getsource(handler_module)


# --- production path: POST /api/v1/jobs + run-jobs --once ------------------------------------


@pytest.fixture()
def record_env(
    paper_jobs_env: BrokerFakes,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[dict[str, Any]]:
    """The paper Job environment plus a scripted external broker behind the handler."""

    state: dict[str, Any] = {"broker": _pair_broker()}
    monkeypatch.setattr(handler_module, "AlpacaClient", lambda *_a, **_k: state["broker"])
    yield state


def _count(model: type) -> int:
    with session_scope(load_settings()) as session:
        return session.scalar(select(func.count()).select_from(model)) or 0


_PAYLOAD = {"order_ids": ["b1", "s1"], "reason": "manual round trip in the console"}


def test_record_job_end_to_end_records_the_pair_and_the_fresh_check_is_the_outcome(
    record_env: dict[str, Any],
) -> None:
    before = {m: _count(m) for m in (PaperOrder, PaperFill, Position)}
    with TestClient(create_app()) as client:
        detail = _submit_and_run(
            client, "rec-happy", job_type="record-external-activity", payload=_PAYLOAD
        )
        assert detail["status"] == "succeeded", detail["failure_message"]
        assert detail["outcome_uncertain"] is False
        summary = detail["result_summary"]
        assert summary["outcome"] == "clean"
        assert summary["blocks_execution"] is False
        assert len(summary["recorded_activity_ids"]) == 2
        assert summary["already_recorded_order_ids"] == []

        assert [r["kind"] for r in detail["resources"]] == ["account_reconciliation_run"]
        assert set(summary["produced_run_ids"]) == {r["id"] for r in detail["resources"]}
        assert summary["produced_run_ids"] == [summary["run_id"]]

        codes = _log_codes(client, detail["id"])
        assert "record_external_activity_recorded" in codes
        assert not {code for code in codes if code.startswith("external_")}

    with session_scope(load_settings()) as session:
        rows = session.execute(select(ExternalBrokerActivity)).scalars().all()
        assert {r.broker_order_id for r in rows} == {"b1", "s1"}
        assert {str(r.job_id) for r in rows} == {detail["id"]}
        run = session.execute(select(AccountReconciliationRun)).scalar_one()
        assert str(run.job_id) == detail["id"]
        assert run.trigger_source == "record_external_activity"
        assert run.blocks_execution is False
    assert {m: _count(m) for m in (PaperOrder, PaperFill, Position)} == before


def test_record_job_with_a_failing_fresh_check_succeeds_as_blocking_and_keeps_the_block(
    record_env: dict[str, Any],
) -> None:
    record_env["broker"]._orders = [
        *record_env["broker"]._orders,
        _ext_order("u1", symbol="MSFT", status="new", filled_qty="0"),
    ]
    with TestClient(create_app()) as client:
        detail = _submit_and_run(
            client, "rec-blocking", job_type="record-external-activity", payload=_PAYLOAD
        )
    assert detail["status"] == "succeeded", detail["failure_message"]
    assert detail["result_summary"]["outcome"] == "blocking"
    assert detail["result_summary"]["blocks_execution"] is True
    assert _count(ExternalBrokerActivity) == 2
    with session_scope(load_settings()) as session:
        run = session.execute(select(AccountReconciliationRun)).scalar_one()
        assert run.blocks_execution is True


@pytest.mark.parametrize(
    ("script", "expected"),
    [
        pytest.param(
            lambda: ExtBroker(orders=[_ext_order("b1", status="new", filled_qty="0")]),
            ExternalActivityRejection.EXTERNAL_ORDER_NOT_TERMINAL,
            id="external_order_not_terminal",
        ),
        pytest.param(
            lambda: ExtBroker(orders=[_ext_order("b1")], fills=[_ext_fill("f1", "b1")]),
            ExternalActivityRejection.EXTERNAL_EXPOSURE_NONZERO,
            id="external_exposure_nonzero",
        ),
        pytest.param(
            lambda: ExtBroker(orders=[]),
            ExternalActivityRejection.BROKER_RECORD_UNAVAILABLE,
            id="broker_record_unavailable",
        ),
    ],
)
def test_refused_record_job_fails_certain_with_the_reason_first_and_stores_nothing(
    record_env: dict[str, Any], script: Any, expected: ExternalActivityRejection
) -> None:
    record_env["broker"] = script()
    with TestClient(create_app()) as client:
        detail = _submit_and_run(
            client,
            f"rec-refused-{expected.value}",
            job_type="record-external-activity",
            payload={"order_ids": ["b1"], "reason": "should be refused"},
        )
        assert "record_external_activity_rejected" in _log_codes(client, detail["id"])
    assert detail["status"] == "failed"
    assert detail["failure_reason"] == "domain_conflict"
    assert detail["outcome_uncertain"] is False
    assert detail["failure_message"].split()[0] == f"{expected.value}:"
    assert _count(ExternalBrokerActivity) == 0
    assert _count(AccountReconciliationRun) == 0


def test_owned_order_is_refused_through_the_job(record_env: dict[str, Any]) -> None:
    # Seed a local order the broker order claims, owned by the active strategy.
    from tests.test_attribution_reconciliation import _broker_order, _seed_orders

    from trading_platform.db.models import PaperOrder as _PaperOrder

    ((client_id, broker_id),) = _seed_orders(
        "trend_following_daily", status="filled", broker_status="filled"
    )
    owned = _broker_order(
        broker_order_id=broker_id, client_order_id=client_id, broker_status="filled"
    )
    record_env["broker"] = ExtBroker(orders=[owned])
    assert _count(_PaperOrder) == 1
    with TestClient(create_app()) as client:
        detail = _submit_and_run(
            client,
            "rec-owned",
            job_type="record-external-activity",
            payload={"order_ids": [broker_id], "reason": "must not adopt"},
        )
    assert detail["failure_reason"] == "domain_conflict"
    assert detail["failure_message"].split()[0] == "order_owned_by_strategy:"
    assert _count(ExternalBrokerActivity) == 0


def test_fresh_check_failure_keeps_the_rows_fails_the_job_uncertain_false_and_retry_is_idempotent(
    record_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    real_reconcile = handler_module.reconcile_account

    def exploding_reconcile(**_kwargs: Any):
        raise RuntimeError("broker listing blew up")

    monkeypatch.setattr(handler_module, "reconcile_account", exploding_reconcile)
    with TestClient(create_app()) as client:
        failed = _submit_and_run(
            client, "rec-explode", job_type="record-external-activity", payload=_PAYLOAD
        )
        assert failed["status"] == "failed"
        assert failed["failure_reason"] == "handler_error"
        assert failed["outcome_uncertain"] is False  # no external_ event code was logged
        assert _count(ExternalBrokerActivity) == 2  # rows stay
        assert _count(AccountReconciliationRun) == 0  # no clean result: the block is not lifted

        monkeypatch.setattr(handler_module, "reconcile_account", real_reconcile)
        retried = _submit_and_run(
            client, "rec-explode-retry", job_type="record-external-activity", payload=_PAYLOAD
        )
    assert retried["status"] == "succeeded", retried["failure_message"]
    assert retried["result_summary"]["recorded_activity_ids"] == []
    assert sorted(retried["result_summary"]["already_recorded_order_ids"]) == ["b1", "s1"]
    assert retried["result_summary"]["outcome"] == "clean"
    assert _count(ExternalBrokerActivity) == 2  # no duplicate row


def test_http_payload_with_a_strategy_key_is_a_typed_422_and_creates_nothing(
    record_env: dict[str, Any],
) -> None:
    with TestClient(create_app()) as client:
        response = _submit(
            client,
            "rec-spoof",
            job_type="record-external-activity",
            payload={**_PAYLOAD, "strategy_id": "trend_following_daily"},
        )
    assert response.status_code == 422, response.text
    assert "unknown_payload_keys" in response.text
    assert _count(ExternalBrokerActivity) == 0
    assert _count(AccountReconciliationRun) == 0


def test_catalog_lists_the_type_additively(record_env: dict[str, Any]) -> None:
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/job-types")
    assert response.status_code == 200
    item = next(i for i in response.json()["items"] if i["job_type"] == "record-external-activity")
    assert item["cancellation_mode"] == "queued_only"
    assert item["broker_effect"] == "reads_broker"
    assert "submission_defaults" not in item or item["submission_defaults"] is None
