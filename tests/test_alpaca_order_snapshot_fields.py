"""Evidence fields on BrokerOrderSnapshot (D-07): created_at, order_type, replacement links."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from trading_platform.services.alpaca import BrokerOrderSnapshot, _normalized_order_snapshot
from trading_platform.services.execution import ExecutionOrderStatus, OrderSide


def test_normalizer_fills_evidence_fields_from_the_payload() -> None:
    snapshot = _normalized_order_snapshot(
        {
            "id": "b-1",
            "client_order_id": "c-1",
            "symbol": "AAPL",
            "side": "buy",
            "qty": "10",
            "status": "new",
            "type": "market",
            "created_at": "2024-01-05T14:35:00Z",
            "replaces": "b-0",
            "replaced_by": "b-2",
        }
    )
    assert snapshot.created_at == datetime(2024, 1, 5, 14, 35, tzinfo=UTC)
    assert snapshot.order_type == "market"
    assert snapshot.replaces_order_id == "b-0"
    assert snapshot.successor_order_id == "b-2"


def test_normalizer_leaves_evidence_fields_none_when_absent() -> None:
    snapshot = _normalized_order_snapshot({"id": "b-1", "status": "new"})
    assert snapshot.created_at is None
    assert snapshot.order_type is None
    assert snapshot.replaces_order_id is None
    assert snapshot.successor_order_id is None


def test_normalizer_treats_malformed_created_at_as_missing() -> None:
    snapshot = _normalized_order_snapshot({"id": "b-1", "status": "new", "created_at": "garbage"})
    assert snapshot.created_at is None


def test_pre_existing_keyword_construction_still_works() -> None:
    snapshot = BrokerOrderSnapshot(
        broker_order_id="b-1",
        client_order_id="c-1",
        symbol="AAPL",
        side=OrderSide.BUY,
        quantity=Decimal("1"),
        status=ExecutionOrderStatus.PENDING,
        broker_status="new",
        submitted_at=None,
        filled_at=None,
        canceled_at=None,
        updated_at=None,
        raw_payload={},
    )
    assert snapshot.created_at is None
    assert snapshot.status_reason is None
