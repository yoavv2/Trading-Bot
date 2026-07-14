"""Unit tests for the reconciliation type contracts (RECON-05, RECON-07).

No DB, ORM, or broker fixtures — this module is pure value types, so these tests
construct dataclasses directly with plain Python values.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trading_platform.services.execution import OrderSide
from trading_platform.services.reconciliation.findings import (
    Finding,
    ReconciliationFinding,
)
from trading_platform.services.reconciliation.snapshot import (
    DEFAULT_ACCOUNT,
    LocalAccountSnapshot,
    LocalFillSnapshot,
    LocalOrderSnapshot,
    LocalPositionSnapshot,
    PositionSide,
    ReconciliationIdentity,
    identity_for_broker_position,
    side_from_quantity,
)

# --- Task 1: closed ReconciliationFinding enum + Finding value object -----------------


def test_reconciliation_finding_has_exactly_five_members():
    assert len(ReconciliationFinding) == 5
    assert {member.name for member in ReconciliationFinding} == {
        "MISSING_LOCAL",
        "MISSING_BROKER",
        "QUANTITY_MISMATCH",
        "PRICE_MISMATCH",
        "STATE_MISMATCH",
    }


def test_reconciliation_finding_rejects_unknown_string():
    with pytest.raises(ValueError):
        ReconciliationFinding("not_a_finding")


def test_finding_category_is_typed_as_enum():
    finding = Finding(
        category=ReconciliationFinding.QUANTITY_MISMATCH,
        identity=None,
        severity="error",
        blocks_execution=True,
        message="quantity mismatch",
        details={"expected": 1, "actual": 2},
    )
    assert isinstance(finding.category, ReconciliationFinding)
    assert finding.category is ReconciliationFinding.QUANTITY_MISMATCH


def test_finding_to_event_dict_matches_execution_event_shape():
    finding = Finding(
        category=ReconciliationFinding.STATE_MISMATCH,
        identity=None,
        severity="warning",
        blocks_execution=False,
        message="state drift",
        details={"local": "filled", "broker": "partially_filled"},
        paper_order_id="abc-123",
    )
    event = finding.to_event_dict()
    assert event == {
        "event_type": "STATE_MISMATCH",
        "severity": "warning",
        "blocks_execution": False,
        "message": "state drift",
        "paper_order_id": "abc-123",
        "details": {"local": "filled", "broker": "partially_filled"},
    }


def test_finding_to_event_dict_defaults_paper_order_id_to_none():
    finding = Finding(
        category=ReconciliationFinding.MISSING_BROKER,
        identity=None,
        severity="error",
        blocks_execution=True,
        message="missing on broker",
        details={},
    )
    assert finding.to_event_dict()["paper_order_id"] is None


# --- Task 2: PositionSide / side_from_quantity / ReconciliationIdentity ---------------


def test_side_from_quantity_positive_is_long():
    assert side_from_quantity(Decimal("10")) is PositionSide.LONG


def test_side_from_quantity_negative_is_short():
    assert side_from_quantity(Decimal("-5")) is PositionSide.SHORT


def test_side_from_quantity_zero_is_flat():
    assert side_from_quantity(Decimal("0")) is PositionSide.FLAT


def test_reconciliation_identity_equal_fields_are_equal_and_hash_equal():
    a = ReconciliationIdentity(symbol="AAPL", account=DEFAULT_ACCOUNT, side=PositionSide.LONG)
    b = ReconciliationIdentity(symbol="AAPL", account=DEFAULT_ACCOUNT, side=PositionSide.LONG)
    assert a == b
    assert hash(a) == hash(b)


def test_reconciliation_identity_usable_as_dict_key():
    a = ReconciliationIdentity(symbol="AAPL", account=DEFAULT_ACCOUNT, side=PositionSide.LONG)
    b = ReconciliationIdentity(symbol="AAPL", account=DEFAULT_ACCOUNT, side=PositionSide.LONG)
    mapping = {a: "local"}
    mapping[b] = "broker"
    assert len(mapping) == 1
    assert mapping[a] == "broker"


def test_reconciliation_identity_different_side_is_a_different_key():
    long_key = ReconciliationIdentity(symbol="AAPL", account=DEFAULT_ACCOUNT, side=PositionSide.LONG)
    short_key = ReconciliationIdentity(symbol="AAPL", account=DEFAULT_ACCOUNT, side=PositionSide.SHORT)
    assert long_key != short_key
    assert len({long_key, short_key}) == 2


def test_reconciliation_identity_is_frozen():
    identity = ReconciliationIdentity(symbol="AAPL", account=DEFAULT_ACCOUNT, side=PositionSide.LONG)
    with pytest.raises(FrozenInstanceError):
        identity.symbol = "MSFT"  # type: ignore[misc]


# --- Task 2: typed local snapshots + identity() helpers -------------------------------


def test_local_position_snapshot_identity_matches_side_from_quantity():
    snapshot = LocalPositionSnapshot(
        symbol="AAPL",
        quantity=Decimal("10"),
        average_entry_price=Decimal("150.00"),
        cost_basis=Decimal("1500.00"),
        status="open",
    )
    assert snapshot.identity() == ReconciliationIdentity(
        symbol="AAPL", account=DEFAULT_ACCOUNT, side=PositionSide.LONG
    )


def test_local_position_snapshot_short_identity():
    snapshot = LocalPositionSnapshot(
        symbol="MSFT",
        quantity=Decimal("-3"),
        average_entry_price=Decimal("300.00"),
        cost_basis=Decimal("900.00"),
        status="open",
    )
    assert snapshot.identity() == ReconciliationIdentity(
        symbol="MSFT", account=DEFAULT_ACCOUNT, side=PositionSide.SHORT
    )


def test_local_position_snapshot_is_frozen():
    snapshot = LocalPositionSnapshot(
        symbol="AAPL",
        quantity=Decimal("10"),
        average_entry_price=Decimal("150.00"),
        cost_basis=Decimal("1500.00"),
        status="open",
    )
    with pytest.raises(FrozenInstanceError):
        snapshot.quantity = Decimal("20")  # type: ignore[misc]


def test_identity_for_broker_position_matches_local_identity_on_same_key():
    class _FakeBrokerPositionSnapshot:
        symbol = "AAPL"
        quantity = Decimal("10")

    broker_identity = identity_for_broker_position(_FakeBrokerPositionSnapshot())
    local_snapshot = LocalPositionSnapshot(
        symbol="AAPL",
        quantity=Decimal("10"),
        average_entry_price=Decimal("150.00"),
        cost_basis=Decimal("1500.00"),
        status="open",
    )
    assert broker_identity == local_snapshot.identity()


def test_local_order_snapshot_side_is_order_side_enum():
    snapshot = LocalOrderSnapshot(
        paper_order_id="order-1",
        strategy_run_id="run-1",
        symbol="AAPL",
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        client_order_id="client-1",
        broker_order_id=None,
        status="pending_submission",
        broker_status=None,
        submission_attempt_count=0,
        sync_failure_count=0,
    )
    assert snapshot.side is OrderSide.BUY


def test_local_fill_snapshot_fields():
    now = datetime.now(tz=UTC)
    snapshot = LocalFillSnapshot(
        broker_fill_id="fill-1",
        broker_order_id="order-1",
        symbol="AAPL",
        side=OrderSide.SELL,
        quantity=Decimal("5"),
        price=Decimal("151.25"),
        filled_at=now,
    )
    assert snapshot.side is OrderSide.SELL
    assert snapshot.price == Decimal("151.25")
    assert snapshot.filled_at == now


def test_local_account_snapshot_fields():
    snapshot = LocalAccountSnapshot(
        cash=Decimal("1000.00"),
        gross_exposure=Decimal("500.00"),
        total_equity=Decimal("1500.00"),
        buying_power=Decimal("2000.00"),
        open_positions=2,
    )
    assert snapshot.cash == Decimal("1000.00")
    assert snapshot.open_positions == 2
