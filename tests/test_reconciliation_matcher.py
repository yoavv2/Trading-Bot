"""Unit tests for the pure, indexed reconciliation matcher (RECON-06, RECON-08).

No DB, ORM, or broker-client fixtures — ``match_snapshots`` is a pure function over
typed snapshots, so these tests construct dataclasses directly with plain Python
values, exactly like ``test_reconciliation_types.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from trading_platform.services.alpaca import (
    BrokerFillSnapshot,
    BrokerOrderSnapshot,
    BrokerPositionSnapshot,
)
from trading_platform.services.execution import ExecutionOrderStatus, OrderSide
from trading_platform.services.reconciliation.findings import (
    Finding,
    ReconciliationFinding,
)
from trading_platform.services.reconciliation.matcher import (
    _match_fills,
    _match_orders,
    _match_positions,
    match_snapshots,
    match_snapshots_with_comparisons,
)
from trading_platform.services.reconciliation.snapshot import (
    LocalFillSnapshot,
    LocalOrderSnapshot,
    LocalPositionSnapshot,
)


def _local_position(
    *,
    symbol: str = "AAPL",
    quantity: Decimal = Decimal("10"),
    average_entry_price: Decimal = Decimal("150.00"),
) -> LocalPositionSnapshot:
    return LocalPositionSnapshot(
        symbol=symbol,
        quantity=quantity,
        average_entry_price=average_entry_price,
        cost_basis=quantity * average_entry_price,
        status="open",
    )


def _broker_position(
    *,
    symbol: str = "AAPL",
    quantity: Decimal = Decimal("10"),
    average_entry_price: Decimal = Decimal("150.00"),
) -> BrokerPositionSnapshot:
    return BrokerPositionSnapshot(
        symbol=symbol,
        quantity=quantity,
        average_entry_price=average_entry_price,
        cost_basis=quantity * average_entry_price,
        market_value=quantity * average_entry_price,
        current_price=average_entry_price,
        raw_payload={},
    )


def _local_order(
    *,
    paper_order_id: str = "order-1",
    symbol: str = "AAPL",
    client_order_id: str = "client-1",
    broker_order_id: str | None = None,
    status: str = "submitted",
    broker_status: str | None = "new",
    submission_attempt_count: int = 1,
) -> LocalOrderSnapshot:
    return LocalOrderSnapshot(
        paper_order_id=paper_order_id,
        strategy_run_id="run-1",
        symbol=symbol,
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        client_order_id=client_order_id,
        broker_order_id=broker_order_id,
        status=status,
        broker_status=broker_status,
        submission_attempt_count=submission_attempt_count,
        sync_failure_count=0,
    )


def _broker_order(
    *,
    broker_order_id: str = "broker-1",
    client_order_id: str = "client-1",
    symbol: str = "AAPL",
    status: ExecutionOrderStatus = ExecutionOrderStatus.ACCEPTED,
    broker_status: str = "new",
) -> BrokerOrderSnapshot:
    now = datetime(2024, 1, 5, 14, 35, tzinfo=UTC)
    return BrokerOrderSnapshot(
        broker_order_id=broker_order_id,
        client_order_id=client_order_id,
        symbol=symbol,
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        status=status,
        broker_status=broker_status,
        submitted_at=now,
        filled_at=None,
        canceled_at=None,
        updated_at=now,
        raw_payload={"id": broker_order_id, "status": broker_status},
    )


def _local_fill(
    *,
    broker_fill_id: str = "fill-1",
    broker_order_id: str = "broker-1",
    symbol: str = "AAPL",
) -> LocalFillSnapshot:
    return LocalFillSnapshot(
        broker_fill_id=broker_fill_id,
        broker_order_id=broker_order_id,
        symbol=symbol,
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        price=Decimal("150.00"),
        filled_at=datetime(2024, 1, 5, 14, 40, tzinfo=UTC),
    )


def _broker_fill(
    *,
    broker_fill_id: str = "fill-1",
    broker_order_id: str = "broker-1",
    symbol: str = "AAPL",
) -> BrokerFillSnapshot:
    return BrokerFillSnapshot(
        broker_fill_id=broker_fill_id,
        broker_order_id=broker_order_id,
        symbol=symbol,
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        price=Decimal("150.00"),
        filled_at=datetime(2024, 1, 5, 14, 40, tzinfo=UTC),
        raw_payload={},
    )


# --- Position categories: MISSING_LOCAL / MISSING_BROKER / QUANTITY_MISMATCH / PRICE_MISMATCH ---


def test_broker_only_position_is_missing_local():
    findings, comparisons = _match_positions([], [_broker_position()])
    assert len(findings) == 1
    assert findings[0].category is ReconciliationFinding.MISSING_LOCAL
    assert comparisons == 1


def test_local_only_position_is_missing_broker():
    findings, comparisons = _match_positions([_local_position()], [])
    assert len(findings) == 1
    assert findings[0].category is ReconciliationFinding.MISSING_BROKER
    assert comparisons == 1


def test_quantity_divergence_beyond_tolerance_is_quantity_mismatch():
    findings, _ = _match_positions(
        [_local_position(quantity=Decimal("10"))],
        [_broker_position(quantity=Decimal("11"))],
    )
    assert len(findings) == 1
    assert findings[0].category is ReconciliationFinding.QUANTITY_MISMATCH


def test_quantity_divergence_within_tolerance_is_not_a_finding():
    findings, _ = _match_positions(
        [_local_position(quantity=Decimal("10.0000001"))],
        [_broker_position(quantity=Decimal("10"))],
    )
    assert findings == ()


def test_price_divergence_beyond_tolerance_is_price_mismatch():
    findings, _ = _match_positions(
        [_local_position(average_entry_price=Decimal("150.00"))],
        [_broker_position(average_entry_price=Decimal("150.50"))],
    )
    assert len(findings) == 1
    assert findings[0].category is ReconciliationFinding.PRICE_MISMATCH


def test_price_divergence_within_tolerance_is_not_a_finding():
    findings, _ = _match_positions(
        [_local_position(average_entry_price=Decimal("150.00"))],
        [_broker_position(average_entry_price=Decimal("150.005"))],
    )
    assert findings == ()


def test_position_can_emit_both_quantity_and_price_mismatch():
    findings, _ = _match_positions(
        [_local_position(quantity=Decimal("10"), average_entry_price=Decimal("150.00"))],
        [_broker_position(quantity=Decimal("11"), average_entry_price=Decimal("151.00"))],
    )
    categories = {finding.category for finding in findings}
    assert categories == {
        ReconciliationFinding.QUANTITY_MISMATCH,
        ReconciliationFinding.PRICE_MISMATCH,
    }


# --- RECON-08: flat positions produce zero findings ---


def test_local_flat_and_broker_flat_produce_zero_findings():
    findings, _ = _match_positions(
        [_local_position(quantity=Decimal("0"))],
        [_broker_position(quantity=Decimal("0"))],
    )
    assert findings == ()


def test_local_absent_and_broker_flat_produce_zero_findings():
    findings, _ = _match_positions([], [_broker_position(quantity=Decimal("0"))])
    assert findings == ()


def test_local_flat_and_broker_absent_produce_zero_findings():
    findings, _ = _match_positions([_local_position(quantity=Decimal("0"))], [])
    assert findings == ()


# --- SIGN-FLIP: side is part of the identity key, so a flipped side is two findings ---


def test_side_flip_yields_missing_local_and_missing_broker_not_quantity_mismatch():
    findings, _ = _match_positions(
        [_local_position(quantity=Decimal("10"))],
        [_broker_position(quantity=Decimal("-5"))],
    )
    categories = sorted(finding.category.name for finding in findings)
    assert categories == ["MISSING_BROKER", "MISSING_LOCAL"]


# --- Order categories: MISSING_LOCAL / MISSING_BROKER / STATE_MISMATCH ---


def test_broker_only_order_is_missing_local():
    findings, comparisons = _match_orders([], [_broker_order()])
    assert len(findings) == 1
    assert findings[0].category is ReconciliationFinding.MISSING_LOCAL
    assert findings[0].broker_order_id == "broker-1"
    assert comparisons == 1


def test_local_active_order_with_no_broker_match_is_missing_broker():
    local = _local_order(status="submitted", submission_attempt_count=2)
    findings, _ = _match_orders([local], [])
    assert len(findings) == 1
    assert findings[0].category is ReconciliationFinding.MISSING_BROKER
    assert findings[0].paper_order_id == "order-1"


def test_local_pending_submission_with_zero_attempts_and_no_broker_match_is_not_a_finding():
    local = _local_order(status="pending_submission", submission_attempt_count=0)
    findings, _ = _match_orders([local], [])
    assert findings == ()


def test_local_submission_failed_with_no_broker_match_is_not_a_finding():
    local = _local_order(status="submission_failed", submission_attempt_count=3)
    findings, _ = _match_orders([local], [])
    assert findings == ()


def test_matched_order_with_diverging_status_is_state_mismatch():
    local = _local_order(status="submitted", broker_status="new")
    broker = _broker_order(status=ExecutionOrderStatus.FILLED, broker_status="filled")
    findings, _ = _match_orders([local], [broker])
    assert len(findings) == 1
    assert findings[0].category is ReconciliationFinding.STATE_MISMATCH
    assert findings[0].paper_order_id == "order-1"
    assert findings[0].broker_order_id == "broker-1"


def test_matched_order_with_aligned_status_is_not_a_finding():
    local = _local_order(status="submitted", broker_status="new")
    broker = _broker_order(status=ExecutionOrderStatus.ACCEPTED, broker_status="new")
    findings, _ = _match_orders([local], [broker])
    assert findings == ()


def test_order_matching_prefers_client_order_id_when_version_chain_exists():
    # Two local orders share a broker_order_id (a version-chain successor takes over an
    # in-flight broker order), but only one has the client_order_id the broker reports.
    predecessor = _local_order(
        paper_order_id="predecessor",
        client_order_id="predecessor-client",
        broker_order_id="shared-broker-id",
        status="canceled",
        broker_status="canceled",
    )
    successor = _local_order(
        paper_order_id="successor",
        client_order_id="successor-client",
        broker_order_id=None,
        status="submitted",
        broker_status="new",
    )
    broker = _broker_order(
        broker_order_id="shared-broker-id",
        client_order_id="successor-client",
        status=ExecutionOrderStatus.ACCEPTED,
        broker_status="new",
    )
    findings, _ = _match_orders([predecessor, successor], [broker])
    # The successor (matched via client_order_id) is aligned -> no STATE_MISMATCH.
    # The predecessor is NOT active (canceled) so it does not raise MISSING_BROKER either.
    assert findings == ()


# --- Fill category: MISSING_LOCAL ---


def test_broker_only_fill_is_missing_local():
    local_order = _local_order(paper_order_id="order-9", broker_order_id="broker-9")
    findings, comparisons = _match_fills(
        [],
        [_broker_fill(broker_fill_id="fill-9", broker_order_id="broker-9")],
        local_orders=[local_order],
    )
    assert len(findings) == 1
    assert findings[0].category is ReconciliationFinding.MISSING_LOCAL
    assert findings[0].paper_order_id == "order-9"


def test_matched_fill_is_not_a_finding():
    findings, _ = _match_fills([_local_fill()], [_broker_fill()], local_orders=[])
    assert findings == ()


# --- Every finding is a closed-enum value ---


def test_every_emitted_finding_category_is_a_reconciliation_finding_enum_member():
    findings = match_snapshots(
        local_orders=[],
        local_fills=[],
        local_positions=[],
        broker_orders=[_broker_order()],
        broker_fills=[_broker_fill(broker_fill_id="fill-x", broker_order_id="broker-x")],
        broker_positions=[_broker_position(symbol="MSFT")],
    )
    assert len(findings) == 3
    for finding in findings:
        assert isinstance(finding, Finding)
        assert isinstance(finding.category, ReconciliationFinding)


# --- Success Criterion 2: comparison count scales linearly, not quadratically ---


def _synthetic_matched_positions(count: int) -> tuple[list[LocalPositionSnapshot], list[BrokerPositionSnapshot]]:
    local_positions = [
        _local_position(symbol=f"SYM{i}", quantity=Decimal("10"), average_entry_price=Decimal("100.00"))
        for i in range(count)
    ]
    broker_positions = [
        _broker_position(symbol=f"SYM{i}", quantity=Decimal("10"), average_entry_price=Decimal("100.00"))
        for i in range(count)
    ]
    return local_positions, broker_positions


def test_matcher_comparison_count_scales_linearly_not_quadratically():
    # Success-Criterion-2 guard: do not relax this to wall-clock timing or remove it.
    # A nested-scan (O(n^2)) matcher would make comparisons(k*n) ~= k^2 * comparisons(n),
    # blowing past the linear bound asserted below.
    n = 200
    k = 10

    local_n, broker_n = _synthetic_matched_positions(n)
    _, comparisons_n = _match_positions(local_n, broker_n)

    local_kn, broker_kn = _synthetic_matched_positions(k * n)
    _, comparisons_kn = _match_positions(local_kn, broker_kn)

    # Linear-scaling assertion: a 10x entity-count increase should produce at most a
    # ~10x (not ~100x) increase in comparisons, with slack for a small constant factor.
    assert comparisons_kn <= 1.5 * k * comparisons_n

    # Fixed-multiple-of-total-entities bound, documenting the linear contract directly.
    total_entities_kn = len(local_kn) + len(broker_kn)
    assert comparisons_kn <= 2 * total_entities_kn


def _synthetic_matched_orders(count: int) -> tuple[list[LocalOrderSnapshot], list[BrokerOrderSnapshot]]:
    # Aligned statuses (local "submitted"/"new" <-> broker ACCEPTED/"new") so every pair
    # matches with zero findings, mirroring test_matched_order_with_aligned_status_is_not_a_finding.
    local_orders = [
        _local_order(
            paper_order_id=f"order-{i}",
            symbol=f"SYM{i}",
            client_order_id=f"client-{i}",
            broker_order_id=f"broker-{i}",
            status="submitted",
            broker_status="new",
        )
        for i in range(count)
    ]
    broker_orders = [
        _broker_order(
            broker_order_id=f"broker-{i}",
            client_order_id=f"client-{i}",
            symbol=f"SYM{i}",
            status=ExecutionOrderStatus.ACCEPTED,
            broker_status="new",
        )
        for i in range(count)
    ]
    return local_orders, broker_orders


def _synthetic_matched_fills(count: int) -> tuple[list[LocalFillSnapshot], list[BrokerFillSnapshot]]:
    local_fills = [
        _local_fill(broker_fill_id=f"fill-{i}", broker_order_id=f"broker-{i}", symbol=f"SYM{i}")
        for i in range(count)
    ]
    broker_fills = [
        _broker_fill(broker_fill_id=f"fill-{i}", broker_order_id=f"broker-{i}", symbol=f"SYM{i}")
        for i in range(count)
    ]
    return local_fills, broker_fills


def test_order_matcher_comparison_count_scales_linearly_not_quadratically():
    # Success-Criterion-2 guard: do not relax this to wall-clock timing or remove it.
    # A nested-scan (O(n^2)) matcher would make comparisons(k*n) ~= k^2 * comparisons(n),
    # blowing past the linear bound asserted below.
    n = 200
    k = 10

    local_n, broker_n = _synthetic_matched_orders(n)
    _, comparisons_n = _match_orders(local_n, broker_n)

    local_kn, broker_kn = _synthetic_matched_orders(k * n)
    _, comparisons_kn = _match_orders(local_kn, broker_kn)

    assert comparisons_kn <= 1.5 * k * comparisons_n

    total_entities_kn = len(local_kn) + len(broker_kn)
    assert comparisons_kn <= 2 * total_entities_kn


def test_fill_matcher_comparison_count_scales_linearly_not_quadratically():
    # Success-Criterion-2 guard: do not relax this to wall-clock timing or remove it.
    # A nested-scan (O(n^2)) matcher would make comparisons(k*n) ~= k^2 * comparisons(n),
    # blowing past the linear bound asserted below.
    n = 200
    k = 10

    local_n, broker_n = _synthetic_matched_fills(n)
    _, comparisons_n = _match_fills(local_n, broker_n, local_orders=[])

    local_kn, broker_kn = _synthetic_matched_fills(k * n)
    _, comparisons_kn = _match_fills(local_kn, broker_kn, local_orders=[])

    assert comparisons_kn <= 1.5 * k * comparisons_n

    total_entities_kn = len(local_kn) + len(broker_kn)
    assert comparisons_kn <= 2 * total_entities_kn


def test_match_snapshots_comparison_count_scales_linearly_not_quadratically():
    # Success-Criterion-2 guard: do not relax this to wall-clock timing or remove it.
    # Proves the linear invariant on the public entry point reconcile actually calls,
    # across a mixed positions+orders+fills workload — not just a single private matcher.
    n = 200
    k = 10

    local_positions_n, broker_positions_n = _synthetic_matched_positions(n)
    local_orders_n, broker_orders_n = _synthetic_matched_orders(n)
    local_fills_n, broker_fills_n = _synthetic_matched_fills(n)
    _, comparisons_n = match_snapshots_with_comparisons(
        local_orders=local_orders_n,
        local_fills=local_fills_n,
        local_positions=local_positions_n,
        broker_orders=broker_orders_n,
        broker_fills=broker_fills_n,
        broker_positions=broker_positions_n,
    )

    local_positions_kn, broker_positions_kn = _synthetic_matched_positions(k * n)
    local_orders_kn, broker_orders_kn = _synthetic_matched_orders(k * n)
    local_fills_kn, broker_fills_kn = _synthetic_matched_fills(k * n)
    _, comparisons_kn = match_snapshots_with_comparisons(
        local_orders=local_orders_kn,
        local_fills=local_fills_kn,
        local_positions=local_positions_kn,
        broker_orders=broker_orders_kn,
        broker_fills=broker_fills_kn,
        broker_positions=broker_positions_kn,
    )

    assert comparisons_kn <= 1.5 * k * comparisons_n

    total_entities_kn = (
        len(local_positions_kn)
        + len(broker_positions_kn)
        + len(local_orders_kn)
        + len(broker_orders_kn)
        + len(local_fills_kn)
        + len(broker_fills_kn)
    )
    assert comparisons_kn <= 2 * total_entities_kn
