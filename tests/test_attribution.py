"""Pure, table-driven tests for evidence-based broker-activity attribution (COR-05, D-07/D-08).

No DB and no broker client: ``classify_broker_activity`` is a pure function over typed
inputs, so every case constructs the dataclasses directly.
"""

from __future__ import annotations

import inspect
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trading_platform.services import attribution as attribution_module
from trading_platform.services.alpaca import (
    BrokerFillSnapshot,
    BrokerOrderSnapshot,
    BrokerPositionSnapshot,
)
from trading_platform.services.attribution import (
    AttributionAnomaly,
    AttributionResult,
    LocalIntentRecord,
    OrderClass,
    OriginTag,
    OwnershipPeriod,
    UnresolvedReason,
    classify_broker_activity,
    looks_like_platform_client_order_id,
)
from trading_platform.services.execution import ExecutionOrderStatus, OrderSide
from trading_platform.services.execution.idempotency import build_client_order_id

PREFIX = "tp"
REGISTERED = datetime(2024, 1, 5, 14, 30, tzinfo=UTC)
BROKER_CREATED = datetime(2024, 1, 5, 14, 35, tzinfo=UTC)


def _platform_id(symbol: str = "AAPL", strategy: str = "alpha") -> str:
    return build_client_order_id(
        prefix=PREFIX,
        strategy_id=strategy,
        session_date=BROKER_CREATED.date(),
        symbol=symbol,
        side=OrderSide.BUY,
        quantity=Decimal("10"),
    )


def _order(
    *,
    broker_order_id: str = "b-1",
    client_order_id: str = "c-1",
    symbol: str = "AAPL",
    side: OrderSide = OrderSide.BUY,
    quantity: str = "10",
    broker_status: str = "filled",
    created_at: datetime | None = BROKER_CREATED,
    order_type: str | None = "market",
    replaces_order_id: str | None = None,
    status_reason: str | None = None,
) -> BrokerOrderSnapshot:
    return BrokerOrderSnapshot(
        broker_order_id=broker_order_id,
        client_order_id=client_order_id,
        symbol=symbol,
        side=side,
        quantity=Decimal(quantity),
        status=ExecutionOrderStatus.FILLED,
        broker_status=broker_status,
        submitted_at=BROKER_CREATED,
        filled_at=None,
        canceled_at=None,
        updated_at=BROKER_CREATED,
        raw_payload={},
        status_reason=status_reason,
        created_at=created_at,
        order_type=order_type,
        replaces_order_id=replaces_order_id,
    )


def _fill(
    *,
    fill_id: str = "f-1",
    order_id: str = "b-1",
    symbol: str = "AAPL",
    side: OrderSide = OrderSide.BUY,
    quantity: str = "10",
) -> BrokerFillSnapshot:
    return BrokerFillSnapshot(
        broker_fill_id=fill_id,
        broker_order_id=order_id,
        symbol=symbol,
        side=side,
        quantity=Decimal(quantity),
        price=Decimal("100"),
        filled_at=BROKER_CREATED,
        raw_payload={},
    )


def _position(symbol: str = "AAPL", quantity: str = "10") -> BrokerPositionSnapshot:
    return BrokerPositionSnapshot(
        symbol=symbol,
        quantity=Decimal(quantity),
        average_entry_price=Decimal("100"),
        cost_basis=Decimal("1000"),
        market_value=Decimal("1000"),
        current_price=Decimal("100"),
        raw_payload={},
    )


def _intent(
    *,
    strategy_id: str = "alpha",
    client_order_id: str = "c-1",
    broker_order_id: str | None = "b-1",
    symbol: str = "AAPL",
    side: str = "buy",
    quantity: str = "10",
    order_type: str = "market",
    registered_at: datetime = REGISTERED,
) -> LocalIntentRecord:
    return LocalIntentRecord(
        strategy_id=strategy_id,
        client_order_id=client_order_id,
        broker_order_id=broker_order_id,
        symbol=symbol,
        side=side,
        quantity=Decimal(quantity),
        order_type=order_type,
        registered_at=registered_at,
    )


def _classify(
    *,
    orders: list[BrokerOrderSnapshot] | None = None,
    fills: list[BrokerFillSnapshot] | None = None,
    positions: list[BrokerPositionSnapshot] | None = None,
    intents: list[LocalIntentRecord] | None = None,
    periods: tuple[OwnershipPeriod, ...] = (),
    recorded_external: frozenset[str] = frozenset(),
    unresolved: tuple[UnresolvedReason, ...] = (),
) -> AttributionResult:
    return classify_broker_activity(
        broker_orders=orders or [],
        broker_fills=fills or [],
        broker_positions=positions or [],
        local_intents=intents or [],
        ownership_periods=periods,
        recorded_external_order_ids=recorded_external,
        platform_prefix=PREFIX,
        unresolved_reasons=unresolved,
    )


def _only_order(result: AttributionResult):
    assert len(result.orders) == 1
    return result.orders[0]


# --- closed enums ---------------------------------------------------------------------


def test_closed_enum_member_sets() -> None:
    assert {m.value for m in OrderClass} == {"owned", "recorded_external", "unrecognized"}
    assert {m.value for m in OriginTag} == {
        "platform_format_unverified",
        "external_format",
        "status_unmapped",
        "replaced_by_successor",
    }
    assert {m.value for m in AttributionAnomaly} == {
        "owned_order_attribute_mismatch",
        "owned_order_before_registration",
        "owned_order_created_at_missing",
        "owned_order_outside_ownership_period",
    }
    assert {m.value for m in UnresolvedReason} == {"broker_history_exceeds_cap"}


# --- platform id format ---------------------------------------------------------------


def test_platform_format_matcher_accepts_exact_format_only() -> None:
    assert looks_like_platform_client_order_id(_platform_id(), prefix=PREFIX)
    assert looks_like_platform_client_order_id(_platform_id("BRK.B"), prefix=PREFIX)
    assert not looks_like_platform_client_order_id("manual-order-1", prefix=PREFIX)
    assert not looks_like_platform_client_order_id(_platform_id() + "x", prefix=PREFIX)
    assert not looks_like_platform_client_order_id(_platform_id(), prefix="other")
    assert not looks_like_platform_client_order_id("", prefix=PREFIX)


def test_platform_format_id_without_local_record_is_unrecognized_never_owned() -> None:
    cid = _platform_id()
    result = _classify(orders=[_order(client_order_id=cid)])
    order = _only_order(result)
    assert order.order_class is OrderClass.UNRECOGNIZED
    assert order.origin_tag is OriginTag.PLATFORM_FORMAT_UNVERIFIED
    assert order.owner_strategy_id is None
    assert result.blocks_execution is True


def test_non_platform_id_is_external_format() -> None:
    order = _only_order(_classify(orders=[_order(client_order_id="manual-123")]))
    assert order.order_class is OrderClass.UNRECOGNIZED
    assert order.origin_tag is OriginTag.EXTERNAL_FORMAT


def test_unmapped_status_tag_wins_over_format_tags() -> None:
    order = _only_order(
        _classify(
            orders=[
                _order(
                    client_order_id=_platform_id(),
                    broker_status="mystery",
                    status_reason="unmapped_broker_status",
                )
            ]
        )
    )
    assert order.origin_tag is OriginTag.STATUS_UNMAPPED
    # even without a pre-computed reason, the closed status mapping decides
    order = _only_order(_classify(orders=[_order(client_order_id="m-1", broker_status="mystery")]))
    assert order.origin_tag is OriginTag.STATUS_UNMAPPED


def test_successor_order_is_replaced_by_successor_with_highest_precedence() -> None:
    order = _only_order(
        _classify(
            orders=[
                _order(
                    client_order_id=_platform_id(),
                    broker_status="mystery",
                    replaces_order_id="b-0",
                )
            ]
        )
    )
    assert order.origin_tag is OriginTag.REPLACED_BY_SUCCESSOR


def test_replaced_original_with_a_local_record_stays_owned() -> None:
    result = _classify(orders=[_order(broker_status="replaced")], intents=[_intent()])
    order = _only_order(result)
    assert order.order_class is OrderClass.OWNED
    assert order.anomaly is None


def test_unrecognized_recorded_external_hook() -> None:
    result = _classify(
        orders=[_order(client_order_id="manual-9", broker_order_id="b-9")],
        fills=[_fill(order_id="b-9")],
        positions=[_position()],
        recorded_external=frozenset({"b-9"}),
    )
    order = _only_order(result)
    assert order.order_class is OrderClass.RECORDED_EXTERNAL
    assert result.fills[0].order_class is OrderClass.RECORDED_EXTERNAL
    assert result.unexplained_exposure == {}
    assert result.blocks_execution is False


# --- owned evidence -------------------------------------------------------------------


def test_clean_owned_order_does_not_block() -> None:
    result = _classify(
        orders=[_order()], fills=[_fill()], positions=[_position()], intents=[_intent()]
    )
    order = _only_order(result)
    assert order.order_class is OrderClass.OWNED
    assert order.owner_strategy_id == "alpha"
    assert order.anomaly is None
    assert result.blocks_execution is False


def test_owned_match_by_broker_order_id_when_client_id_differs() -> None:
    result = _classify(
        orders=[_order(client_order_id="other")],
        intents=[_intent(client_order_id="c-1", broker_order_id="b-1")],
    )
    assert _only_order(result).order_class is OrderClass.OWNED


@pytest.mark.parametrize(
    "order_kwargs",
    [
        {"quantity": "11"},
        {"symbol": "MSFT"},
        {"side": OrderSide.SELL},
        {"order_type": "limit"},
        {"order_type": None},
    ],
)
def test_owned_attribute_mismatch_is_a_blocking_anomaly(order_kwargs: dict) -> None:
    result = _classify(orders=[_order(**order_kwargs)], intents=[_intent()])
    order = _only_order(result)
    assert order.order_class is OrderClass.OWNED
    assert order.anomaly is AttributionAnomaly.OWNED_ORDER_ATTRIBUTE_MISMATCH
    assert result.blocks_execution is True


def test_local_record_with_different_quantity_is_an_anomaly() -> None:
    result = _classify(orders=[_order(quantity="9")], intents=[_intent(quantity="10")])
    assert _only_order(result).anomaly is AttributionAnomaly.OWNED_ORDER_ATTRIBUTE_MISMATCH
    assert result.blocks_execution is True


def test_quantity_within_tolerance_matches() -> None:
    result = _classify(orders=[_order(quantity="10.0000001")], intents=[_intent()])
    assert _only_order(result).anomaly is None


def test_broker_created_before_registration_is_an_anomaly() -> None:
    result = _classify(
        orders=[_order(created_at=REGISTERED - timedelta(seconds=1))], intents=[_intent()]
    )
    assert _only_order(result).anomaly is AttributionAnomaly.OWNED_ORDER_BEFORE_REGISTRATION


def test_broker_created_at_equal_registration_is_clean() -> None:
    result = _classify(orders=[_order(created_at=REGISTERED)], intents=[_intent()])
    assert _only_order(result).anomaly is None


def test_missing_created_at_is_an_anomaly() -> None:
    result = _classify(orders=[_order(created_at=None)], intents=[_intent()])
    assert _only_order(result).anomaly is AttributionAnomaly.OWNED_ORDER_CREATED_AT_MISSING


def test_naive_registration_is_treated_as_utc() -> None:
    result = _classify(
        orders=[_order()], intents=[_intent(registered_at=REGISTERED.replace(tzinfo=None))]
    )
    assert _only_order(result).anomaly is None


def test_attribute_mismatch_has_precedence_over_created_at() -> None:
    result = _classify(
        orders=[_order(quantity="11", created_at=None)], intents=[_intent()]
    )
    assert _only_order(result).anomaly is AttributionAnomaly.OWNED_ORDER_ATTRIBUTE_MISMATCH


def test_ownership_period_cases() -> None:
    start = REGISTERED - timedelta(days=1)
    own = OwnershipPeriod(strategy_id="alpha", start=start, end=None)
    other = OwnershipPeriod(strategy_id="beta", start=start, end=None)

    inside = _classify(orders=[_order()], intents=[_intent()], periods=(own,))
    assert _only_order(inside).anomaly is None

    outside = _classify(orders=[_order()], intents=[_intent()], periods=(other,))
    assert _only_order(outside).anomaly is AttributionAnomaly.OWNED_ORDER_OUTSIDE_OWNERSHIP_PERIOD
    assert outside.blocks_execution is True

    # registered before the earliest recorded period start: pre-ownership history
    late_period = OwnershipPeriod(strategy_id="beta", start=REGISTERED + timedelta(days=1))
    pre = _classify(orders=[_order()], intents=[_intent()], periods=(late_period,))
    assert _only_order(pre).anomaly is None

    # no periods at all: no evidence either way
    none = _classify(orders=[_order()], intents=[_intent()], periods=())
    assert _only_order(none).anomaly is None

    # a closed period excludes registrations at/after its end
    closed = OwnershipPeriod(strategy_id="alpha", start=start, end=REGISTERED)
    after_end = _classify(orders=[_order()], intents=[_intent()], periods=(closed,))
    assert (
        _only_order(after_end).anomaly is AttributionAnomaly.OWNED_ORDER_OUTSIDE_OWNERSHIP_PERIOD
    )


def test_prior_owner_orders_stay_owned_by_that_owner() -> None:
    start = REGISTERED - timedelta(days=30)
    handover = REGISTERED + timedelta(days=30)
    periods = (
        OwnershipPeriod(strategy_id="alpha", start=start, end=handover),
        OwnershipPeriod(strategy_id="beta", start=handover, end=None),
    )
    result = _classify(
        orders=[_order()],
        fills=[_fill()],
        positions=[_position()],
        intents=[_intent(strategy_id="alpha")],
        periods=periods,
    )
    order = _only_order(result)
    assert order.order_class is OrderClass.OWNED
    assert order.owner_strategy_id == "alpha"
    assert order.anomaly is None
    assert result.fills[0].owner_strategy_id == "alpha"
    assert result.blocks_execution is False
    assert result.excluded_order_ids_for("beta") == frozenset({"b-1"})
    assert result.excluded_order_ids_for("alpha") == frozenset()


def test_anomalous_other_owner_order_is_not_excluded() -> None:
    result = _classify(orders=[_order(quantity="9")], intents=[_intent(strategy_id="alpha")])
    assert result.excluded_order_ids_for("beta") == frozenset()


# --- fills ----------------------------------------------------------------------------


def test_fills_inherit_class_and_orphan_fill_is_unrecognized() -> None:
    result = _classify(
        orders=[_order(), _order(broker_order_id="b-2", client_order_id="manual-2")],
        fills=[
            _fill(fill_id="f-1", order_id="b-1"),
            _fill(fill_id="f-2", order_id="b-2"),
            _fill(fill_id="f-3", order_id="b-missing"),
        ],
        intents=[_intent()],
    )
    classes = {fill.broker_fill_id: fill for fill in result.fills}
    assert classes["f-1"].order_class is OrderClass.OWNED
    assert classes["f-1"].owner_strategy_id == "alpha"
    assert classes["f-2"].order_class is OrderClass.UNRECOGNIZED
    assert classes["f-3"].order_class is OrderClass.UNRECOGNIZED
    assert result.blocks_execution is True


def test_orphan_fill_alone_blocks() -> None:
    result = _classify(fills=[_fill(order_id="gone")])
    assert result.fills[0].order_class is OrderClass.UNRECOGNIZED
    assert result.blocks_execution is True


# --- exposure -------------------------------------------------------------------------


def test_manual_order_anywhere_in_history_blocks() -> None:
    old = datetime(2019, 3, 1, tzinfo=UTC)
    manual_buy = replace(_order(broker_order_id="b-m1", client_order_id="manual-1"), created_at=old)
    manual_sell = replace(
        _order(broker_order_id="b-m2", client_order_id="manual-2", side=OrderSide.SELL),
        created_at=old,
    )
    result = _classify(
        orders=[manual_buy, manual_sell],
        fills=[
            _fill(fill_id="m1", order_id="b-m1"),
            _fill(fill_id="m2", order_id="b-m2", side=OrderSide.SELL),
        ],
        positions=[],
    )
    # position is net-zero but the unrecognized orders still block
    assert result.unexplained_exposure == {}
    assert result.blocks_execution is True


def test_unexplained_exposure_non_zero_blocks() -> None:
    result = _classify(positions=[_position(quantity="5")])
    assert result.unexplained_exposure == {"AAPL": Decimal("5")}
    assert result.blocks_execution is True


def test_unexplained_exposure_table() -> None:
    owned_buy = _order()
    owned_sell = _order(broker_order_id="b-2", client_order_id="c-2", side=OrderSide.SELL)
    intents = [
        _intent(),
        _intent(client_order_id="c-2", broker_order_id="b-2", side="sell"),
    ]

    # position fully explained by owned fills
    explained = _classify(
        orders=[owned_buy], fills=[_fill()], positions=[_position()], intents=[_intent()]
    )
    assert explained.unexplained_exposure == {}

    # buy 10, sell 10, broker flat
    flat = _classify(
        orders=[owned_buy, owned_sell],
        fills=[_fill(), _fill(fill_id="f-2", order_id="b-2", side=OrderSide.SELL)],
        positions=[],
        intents=intents,
    )
    assert flat.unexplained_exposure == {}
    assert flat.blocks_execution is False

    # only an unrecognized fill explains nothing
    only_unrec = _classify(
        orders=[_order(client_order_id="manual-1")],
        fills=[_fill()],
        positions=[_position()],
    )
    assert only_unrec.unexplained_exposure == {"AAPL": Decimal("10")}

    # short position explained by an owned sell
    short = _classify(
        orders=[owned_sell],
        fills=[_fill(fill_id="f-2", order_id="b-2", side=OrderSide.SELL)],
        positions=[_position(quantity="-10")],
        intents=[intents[1]],
    )
    assert short.unexplained_exposure == {}

    # owned buys explain only part of the position
    partial = _classify(
        orders=[owned_buy],
        fills=[_fill(quantity="4")],
        positions=[_position(quantity="10")],
        intents=[_intent()],
    )
    assert partial.unexplained_exposure == {"AAPL": Decimal("6")}

    # owned fills with no broker position at all leave a (negative) residual
    missing_position = _classify(
        orders=[owned_buy], fills=[_fill()], positions=[], intents=[_intent()]
    )
    assert missing_position.unexplained_exposure == {"AAPL": Decimal("-10")}


# --- result shape ---------------------------------------------------------------------


def test_blocks_execution_truth_table() -> None:
    assert _classify().blocks_execution is False
    assert _classify(orders=[_order()], intents=[_intent()]).blocks_execution is False
    assert _classify(orders=[_order(client_order_id="m")]).blocks_execution is True
    assert _classify(fills=[_fill(order_id="x")]).blocks_execution is True
    assert (
        _classify(orders=[_order(quantity="9")], intents=[_intent()]).blocks_execution is True
    )
    assert _classify(positions=[_position()]).blocks_execution is True
    assert (
        _classify(unresolved=(UnresolvedReason.BROKER_HISTORY_EXCEEDS_CAP,)).blocks_execution
        is True
    )


def test_unresolved_reason_alone_is_reported() -> None:
    result = _classify(unresolved=(UnresolvedReason.BROKER_HISTORY_EXCEEDS_CAP,))
    payload = result.to_dict()
    assert payload["unresolved_reasons"] == ["broker_history_exceeds_cap"]
    assert result.blocks_execution is True


def test_to_dict_is_bounded_and_owned_orders_appear_only_in_counts() -> None:
    orders = [
        _order(broker_order_id=f"b-{i}", client_order_id=f"c-{i}") for i in range(1_000)
    ]
    intents = [
        _intent(client_order_id=f"c-{i}", broker_order_id=f"b-{i}") for i in range(1_000)
    ]
    manual = _order(broker_order_id="b-manual", client_order_id="manual-x", symbol="MSFT")
    result = _classify(orders=[*orders, manual], intents=intents)
    payload = result.to_dict()
    assert payload["orders"]["owned"] == 1_000
    assert payload["orders"]["unrecognized"] == 1
    assert payload["orders"]["recorded_external"] == 0
    assert payload["unrecognized_orders"] == [
        {
            "broker_order_id": "b-manual",
            "client_order_id": "manual-x",
            "symbol": "MSFT",
            "side": "buy",
            "quantity": "10",
            "origin_tag": "external_format",
        }
    ]
    assert payload["anomalies"] == []
    assert payload["unexplained_exposure"] == {}
    assert "b-5" not in repr(payload)
    assert payload["blocks_execution"] is True


def test_to_dict_caps_item_lists_but_keeps_exact_counts() -> None:
    orders = [
        _order(broker_order_id=f"b-{i}", client_order_id=f"manual-{i}") for i in range(500)
    ]
    payload = _classify(orders=orders).to_dict()
    assert payload["orders"]["unrecognized"] == 500
    assert len(payload["unrecognized_orders"]) == attribution_module.MAX_REPORTED_ITEMS
    assert payload["unrecognized_orders_truncated"] == 500 - attribution_module.MAX_REPORTED_ITEMS


def test_to_dict_serializes_decimals_as_strings() -> None:
    payload = _classify(positions=[_position(quantity="5.5")]).to_dict()
    assert payload["unexplained_exposure"] == {"AAPL": "5.5"}


# --- purity ---------------------------------------------------------------------------


def test_attribution_module_has_no_runtime_orm_or_http_imports() -> None:
    source = inspect.getsource(attribution_module)
    runtime_part = source.split("if TYPE_CHECKING:")[0]
    for forbidden in ("sqlalchemy", "httpx", "db.models", "import trading_platform.services.alpaca"):
        assert forbidden not in runtime_part
    assert "from trading_platform.services.alpaca import" not in runtime_part
