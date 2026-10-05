"""SAF-07 / D-07: a broker record is bound to an unresolved local intent only after the full
identity check (client_order_id, symbol, side, quantity, type, broker created_at >= local
registration) on EVERY binder: the bulk sync, the pre-lock recovery and the recovery lookup.
"""

from __future__ import annotations

import inspect
import uuid
from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import date, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select
from tests.support.migrated_db import migrated_database
from tests.support.recovery_fixtures import OWNER, at, seed_uncertain_session
from tests.test_recovery_predicate import (
    LookupBroker,
    Timeline,
    _arrange,
    _clean_account_run,
    _snapshot_for,
    _status,
)

from trading_platform.core.settings import load_settings
from trading_platform.db.models import (
    AttemptOutcomeClass,
    ExecutionEvent,
    OrderEvent,
    OrderLifecycleState,
    PaperOrder,
)
from trading_platform.db.session import session_scope
from trading_platform.services import recovery
from trading_platform.services.execution import OrderSide
from trading_platform.services.execution.broker_identity import broker_record_mismatch
from trading_platform.services.execution.sync_orders import sync_account_state, sync_paper_state
from trading_platform.services.reconciliation.report import (
    BrokerStateSnapshot,
    recover_inflight_paper_orders,
)
from trading_platform.services.recovery import GateCode, RecoveryClassification

EVENT = "broker_order_identity_mismatch"


@pytest.fixture()
def binding_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    with migrated_database(monkeypatch, "broker_binding_identity") as name:
        yield name


def _sync_account(broker: LookupBroker) -> None:
    sync_account_state(settings=load_settings(), broker_client=broker)


def _sync_paper(broker: LookupBroker) -> None:
    sync_paper_state(
        OWNER, as_of_session=date(2024, 1, 5), settings=load_settings(), broker_client=broker
    )


ENTRY_POINTS: dict[str, Callable[[LookupBroker], None]] = {
    "sync_account_state": _sync_account,
    "sync_paper_state": _sync_paper,
}

# field -> overrides handed to the snapshot builder (each breaks exactly one D-07 field).
MISMATCHES: dict[str, dict[str, Any]] = {
    "symbol": {"symbol": "MSFT"},
    "quantity": {"quantity": "11"},
    "side": {"side": OrderSide.SELL},
    "type": {"order_type": "limit"},
    "created_at": {"created_delta": timedelta(minutes=-10)},
}


def _snapshot(order_id: uuid.UUID, field: str | None, **extra: Any) -> Any:
    """A broker record for the intent; with ``field`` set, that one field breaks D-07."""

    spec = dict(MISMATCHES[field]) if field else {}
    spec.update(extra)
    created_delta = spec.pop("created_delta", None)
    kwargs: dict[str, Any] = {}
    if created_delta is not None:
        kwargs["created_delta"] = created_delta
    side = spec.pop("side", None)
    order_type = spec.pop("order_type", None)
    symbol = spec.pop("symbol", None)
    quantity = spec.pop("quantity", None)
    if symbol is not None:
        kwargs["symbol"] = symbol
    if quantity is not None:
        kwargs["quantity"] = quantity
    if order_type is not None:
        kwargs["order_type"] = order_type
    kwargs["broker_status"] = spec.pop("broker_status", "filled")
    snapshot = _snapshot_for(order_id, **kwargs)
    if side is not None:
        snapshot = replace(snapshot, side=side)
    return snapshot


def _order(order_id: uuid.UUID) -> tuple[str | None, OrderLifecycleState]:
    with session_scope(load_settings()) as session:
        stored = session.get(PaperOrder, order_id)
        assert stored is not None
        return stored.broker_order_id, stored.status


def _order_event_count(order_id: uuid.UUID) -> int:
    with session_scope(load_settings()) as session:
        return session.execute(
            select(func.count())
            .select_from(OrderEvent)
            .where(OrderEvent.paper_order_id == order_id)
        ).scalar_one()


def _identity_events(order_id: uuid.UUID) -> list[ExecutionEvent]:
    with session_scope(load_settings()) as session:
        rows = list(
            session.execute(
                select(ExecutionEvent).where(
                    ExecutionEvent.paper_order_id == order_id, ExecutionEvent.event_type == EVENT
                )
            ).scalars()
        )
        session.expunge_all()
        return rows


@pytest.mark.parametrize("entry", sorted(ENTRY_POINTS))
@pytest.mark.parametrize("field", sorted(MISMATCHES))
def test_bulk_sync_does_not_bind_a_mismatching_record(
    binding_db: str, monkeypatch: pytest.MonkeyPatch, field: str, entry: str
) -> None:
    _, _, order = _arrange(lambda s: seed_uncertain_session(s, completed_at=at(0)))
    Timeline(monkeypatch, at(2))
    snapshot = _snapshot(order.id, field)
    events_before = _order_event_count(order.id)

    broker = LookupBroker(orders=[snapshot])
    ENTRY_POINTS[entry](broker)

    assert broker.post_count == 0
    broker_order_id, status = _order(order.id)
    assert broker_order_id is None
    assert status is OrderLifecycleState.UNKNOWN
    assert _order_event_count(order.id) == events_before
    (event,) = _identity_events(order.id)
    assert event.blocks_execution is True
    assert event.severity == "error"
    assert event.details["field"] == field
    assert event.details["broker_order_id"] == snapshot.broker_order_id
    (intent,) = _status().intents
    assert intent.classification is not RecoveryClassification.FOUND_VERIFIED
    assert _status().gate_code is GateCode.OUTCOME_UNRESOLVED
    _clean_account_run(30)
    assert _status().gate_code is GateCode.OUTCOME_UNRESOLVED  # a clean pass does not release it


@pytest.mark.parametrize("field", sorted(MISMATCHES))
def test_pre_lock_recovery_does_not_bind_a_mismatching_record(
    binding_db: str, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    _, _, order = _arrange(
        lambda s: seed_uncertain_session(
            s, status=OrderLifecycleState.PENDING_SUBMISSION, completed_at=at(0)
        )
    )
    Timeline(monkeypatch, at(2))
    snapshot = _snapshot(order.id, field)
    events_before = _order_event_count(order.id)

    state = BrokerStateSnapshot(
        orders=(snapshot,), fills=(), positions=(), account=LookupBroker().get_account()
    )
    recovered = recover_inflight_paper_orders(OWNER, settings=load_settings(), broker_state=state)

    assert recovered == 0
    broker_order_id, status = _order(order.id)
    assert broker_order_id is None
    assert status is OrderLifecycleState.PENDING_SUBMISSION
    assert _order_event_count(order.id) == events_before
    (event,) = _identity_events(order.id)
    assert event.details["field"] == field
    assert event.blocks_execution is True
    assert _status().gate_code is GateCode.OUTCOME_UNRESOLVED


@pytest.mark.parametrize("entry", sorted(ENTRY_POINTS))
def test_matching_record_is_bound_and_verified(
    binding_db: str, monkeypatch: pytest.MonkeyPatch, entry: str
) -> None:
    _, _, order = _arrange(lambda s: seed_uncertain_session(s, completed_at=at(0)))
    Timeline(monkeypatch, at(2))
    snapshot = _snapshot(order.id, None)

    ENTRY_POINTS[entry](LookupBroker(orders=[snapshot]))

    broker_order_id, status = _order(order.id)
    assert broker_order_id == snapshot.broker_order_id
    assert status is OrderLifecycleState.FILLED
    assert _identity_events(order.id) == []
    (intent,) = _status().intents
    assert intent.classification is RecoveryClassification.FOUND_VERIFIED


def test_matching_record_is_bound_by_pre_lock_recovery(
    binding_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, order = _arrange(
        lambda s: seed_uncertain_session(
            s, status=OrderLifecycleState.PENDING_SUBMISSION, completed_at=at(0)
        )
    )
    Timeline(monkeypatch, at(2))
    snapshot = _snapshot(order.id, None, broker_status="new")
    state = BrokerStateSnapshot(
        orders=(snapshot,), fills=(), positions=(), account=LookupBroker().get_account()
    )
    assert recover_inflight_paper_orders(OWNER, settings=load_settings(), broker_state=state) == 1
    broker_order_id, status = _order(order.id)
    assert broker_order_id == snapshot.broker_order_id
    assert status is OrderLifecycleState.SUBMITTED
    assert _identity_events(order.id) == []


def test_known_broker_id_updates_are_unchanged(
    binding_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The check guards only the FIRST binding: an order already bound is matched and updated."""

    _, _, order = _arrange(
        lambda s: seed_uncertain_session(
            s,
            status=OrderLifecycleState.SUBMITTED,
            broker_order_id="b-found-1",
            broker_status="new",
            attempts=(AttemptOutcomeClass.ACCEPTED,),
            completed_at=at(0),
        )
    )
    Timeline(monkeypatch, at(2))
    # created_at precedes the registration: it would fail D-07 if the order were unbound.
    snapshot = _snapshot(order.id, "created_at")

    _sync_account(LookupBroker(orders=[snapshot]))

    broker_order_id, status = _order(order.id)
    assert broker_order_id == "b-found-1"
    assert status is OrderLifecycleState.FILLED
    assert _identity_events(order.id) == []


def test_lookup_mismatch_delegates_to_the_shared_check(
    binding_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, order = _arrange(lambda s: seed_uncertain_session(s, completed_at=at(0)))
    assert "broker_record_mismatch(" in inspect.getsource(recovery._lookup_mismatch)
    with session_scope(load_settings()) as session:
        stored = session.get(PaperOrder, order.id)
        assert stored is not None
        assert recovery._lookup_mismatch(stored, "AAPL", _snapshot(order.id, None)) is None
        for field in MISMATCHES:
            snapshot = _snapshot(order.id, field)
            assert recovery._lookup_mismatch(stored, "AAPL", snapshot) == field
            assert broker_record_mismatch(stored, "AAPL", snapshot) == field
        foreign = replace(_snapshot(order.id, None), client_order_id="someone-else")
        assert recovery._lookup_mismatch(stored, "AAPL", foreign) == "client_order_id"
