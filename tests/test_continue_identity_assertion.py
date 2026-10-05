"""SAF-03 / D-19 / S2-R3: Continue sends only the pinned identity (20.1-22).

A planned, never-registered intent is paused by a price move; between that pause and Continue a
mutable input changes (the ``execution.client_order_id_prefix`` setting, the approved risk
event's quantity). Continue must assert, before any registration write and before T1, that the
identity it would send equals the pinned ``execution_operation_intents`` row. A drift sends
nothing (zero POST), registers nothing, re-links nothing and moves the operation to
``requires_reevaluation`` with ``pinned_identity_mismatch:<field>``; the Job ends normally.

Real PostgreSQL throwaway database, the production loop, the real T1 and permission check; only
the broker (``ScriptedExecutionService``) and the price source are replaced.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from tests.support.paper_eligibility import allow_paper_execution
from tests.support.paper_execution_seams import allow_direct_paper_execution
from tests.support.price_source import FreshPriceSource, ScriptedPriceSource, observation
from tests.test_paper_execution import migrated_paper_db  # noqa: F401  (database fixture)
from tests.test_paper_session_operations import (
    DEFAULT_BATCH,
    ScriptedExecutionService,
    _start,
    count,
    finish_jobs,
    manifest,
    operation_row,
    run_continue,
    seed_batch,
    seed_clean_reconciliation_after_now,
)

from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.db.models import (
    ExecutionEvent,
    ExecutionOperationIntent,
    Job,
    JobStatus,
    PaperOrder,
    RiskEvent,
)
from trading_platform.db.session import session_scope

PREFIX_ENV = "TRADING_PLATFORM_EXECUTION__CLIENT_ORDER_ID_PREFIX"


@pytest.fixture(autouse=True)
def _seams(monkeypatch: pytest.MonkeyPatch) -> Iterator[FreshPriceSource]:
    allow_paper_execution(monkeypatch)
    yield allow_direct_paper_execution(monkeypatch)
    monkeypatch.undo()
    clear_settings_cache()  # a prefix override must not leak into a sibling test


def _set_prefix(monkeypatch: pytest.MonkeyPatch, prefix: str) -> None:
    """Change ``execution.client_order_id_prefix`` for the next ``load_settings()``."""

    monkeypatch.setenv(PREFIX_ENV, prefix)
    clear_settings_cache()


def _price_paused_single_intent() -> uuid.UUID:
    """One approved AAPL intent, paused by a +6% price move: planned, never registered."""

    run, _ = seed_batch(DEFAULT_BATCH[:1], manifest=manifest())
    paused = _start(
        ScriptedExecutionService(["accept"]),
        risk_run_id=run,
        price_source=ScriptedPriceSource([observation("AAPL", "127.2")]),  # +6%
    )
    operation = paused.result_summary["operation"]
    assert operation["reason"] == "price_moved_beyond_tolerance"
    assert count(PaperOrder) == 0
    finish_jobs()
    seed_clean_reconciliation_after_now()
    return uuid.UUID(operation["id"])


def _intent_row() -> tuple[uuid.UUID | None, str, Decimal]:
    with session_scope(load_settings()) as session:
        row = session.execute(select(ExecutionOperationIntent)).scalar_one()
        return row.paper_order_id, row.client_order_id, row.quantity


def _mutate_risk_event_quantity(quantity: str) -> None:
    with session_scope(load_settings()) as session:
        event = session.execute(select(RiskEvent)).scalars().one()
        event.proposed_quantity = Decimal(quantity)
        event.proposed_notional = Decimal(quantity) * Decimal("120")


def _events(event_type: str) -> list[dict[str, Any]]:
    with session_scope(load_settings()) as session:
        return [
            dict(event.details or {})
            for event in session.execute(
                select(ExecutionEvent).where(ExecutionEvent.event_type == event_type)
            ).scalars()
        ]


def _assert_refused(
    operation_id: uuid.UUID,
    *,
    resumed: ScriptedExecutionService,
    report: Any,
    reason: str,
    detail: str,
    before: tuple[uuid.UUID | None, str, Decimal],
) -> None:
    assert resumed.post_attempts == 0 and resumed.submitted_intents == []
    assert count(PaperOrder) == 0  # nothing registered
    assert _intent_row() == before  # nothing re-linked, identity unchanged
    operation = operation_row(operation_id)
    assert (operation.state, operation.reason, operation.reason_detail) == (
        "requires_reevaluation",
        reason,
        detail,
    )
    assert (report.operation_state, report.operation_reason) == ("requires_reevaluation", reason)
    events = _events("pinned_identity_mismatch")
    assert len(events) == 1 and events[0]["field"] == detail.split(":", 1)[1]
    # the Job ends normally: the call returned (a raise would fail the Job and force it
    # outcome_uncertain), and the continuation Job is not left failed
    with session_scope(load_settings()) as session:
        failed = session.execute(select(Job).where(Job.status == JobStatus.FAILED)).scalars().all()
    assert failed == []


def test_prefix_change_between_price_pause_and_continue_sends_nothing(
    migrated_paper_db: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operation_id = _price_paused_single_intent()
    before = _intent_row()
    assert before[0] is None and before[2] == Decimal("10")
    _set_prefix(monkeypatch, "zz")  # symbol, side and quantity unchanged
    resumed = ScriptedExecutionService(["accept"])

    report = run_continue(resumed, operation_id=operation_id)

    _assert_refused(
        operation_id,
        resumed=resumed,
        report=report,
        reason="strategy_settings_changed",
        detail="pinned_identity_mismatch:client_order_id",
        before=before,
    )
    event = _events("pinned_identity_mismatch")[0]
    assert event["pinned"] == before[1] and event["derived"].startswith("zz-")


def test_mutated_risk_event_quantity_between_pause_and_continue_sends_nothing(
    migrated_paper_db: str,  # noqa: F811
) -> None:
    operation_id = _price_paused_single_intent()
    before = _intent_row()
    _mutate_risk_event_quantity("11")  # also changes the derived client_order_id
    resumed = ScriptedExecutionService(["accept"])

    report = run_continue(resumed, operation_id=operation_id)

    _assert_refused(
        operation_id,
        resumed=resumed,
        report=report,
        reason="evaluation_data_changed",
        detail="pinned_identity_mismatch:quantity",
        before=before,
    )


def test_prefix_and_quantity_change_reports_quantity_first(
    migrated_paper_db: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operation_id = _price_paused_single_intent()
    before = _intent_row()
    _mutate_risk_event_quantity("12")
    _set_prefix(monkeypatch, "zz")
    resumed = ScriptedExecutionService(["accept"])

    report = run_continue(resumed, operation_id=operation_id)

    _assert_refused(
        operation_id,
        resumed=resumed,
        report=report,
        reason="evaluation_data_changed",
        detail="pinned_identity_mismatch:quantity",
        before=before,
    )


def test_unchanged_continue_sends_the_pinned_identity(
    migrated_paper_db: str,  # noqa: F811
) -> None:
    operation_id = _price_paused_single_intent()
    _paper_order_id, original_client_order_id, pinned_quantity = _intent_row()
    resumed = ScriptedExecutionService(["accept"])

    report = run_continue(
        resumed,
        operation_id=operation_id,
        price_source=ScriptedPriceSource([observation("AAPL", "122.4")]),  # +2%: back in tolerance
    )

    assert resumed.post_attempts == 1
    sent = resumed.submitted_intents[0]
    assert (sent.client_order_id, sent.symbol, sent.quantity) == (
        original_client_order_id,
        "AAPL",
        pinned_quantity,
    )
    assert sent.side.value == "buy"
    assert report.operation_state == "paused"
    assert _events("pinned_identity_mismatch") == []


def test_existing_order_identity_matches_the_view(
    migrated_paper_db: str,  # noqa: F811
) -> None:
    """Retry of a registered, proven-not-sent intent: the existing order's identity equals the
    view, so the retry path is unchanged (one POST, the original client_order_id)."""

    run, _ = seed_batch(DEFAULT_BATCH[:1], manifest=manifest())
    first = ScriptedExecutionService(["not_sent"])
    report = _start(first, risk_run_id=run)
    operation_id = uuid.UUID(report.result_summary["operation"]["id"])
    assert report.result_summary["operation"]["reason"] == "broker_unavailable"
    finish_jobs()
    seed_clean_reconciliation_after_now()
    paper_order_id, original_client_order_id, pinned_quantity = _intent_row()
    assert paper_order_id is not None  # registered, so this is the retry_existing path
    with session_scope(load_settings()) as session:
        assert session.get(PaperOrder, paper_order_id).client_order_id == original_client_order_id  # type: ignore[union-attr]
    resumed = ScriptedExecutionService(["accept"])

    run_continue(resumed, operation_id=operation_id)

    assert resumed.post_attempts == 1
    sent = resumed.submitted_intents[0]
    assert (sent.client_order_id, sent.quantity) == (original_client_order_id, pinned_quantity)
    assert count(PaperOrder) == 1
    assert _events("pinned_identity_mismatch") == []
