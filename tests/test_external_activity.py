"""DB-backed tests for audited external-activity recording (EXT-01; D-08, D-10).

A real PostgreSQL database and a scripted fake broker. Recording stores immutable,
hash-verified snapshots with no strategy and no position; only the fresh account-level
reconciliation that follows can lift a block, and every later check re-verifies the
recorded items from the broker lists it already loaded.
"""

from __future__ import annotations

import ast
import threading
import uuid
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select
from tests.support.migrated_db import migrated_database
from tests.support.query_counter import count_queries
from tests.test_attribution_reconciliation import (
    BROKER_CREATED,
    OWNER,
    SESSION,
    FakeBroker,
    _broker_order,
    _cap_error,
    _ensure_strategy,
    _seed_orders,
)

from trading_platform.core.settings import load_settings
from trading_platform.db.models import (
    AccountReconciliationRun,
    ActivePaperStrategy,
    ExternalBrokerActivity,
    Job,
    JobStatus,
    PaperFill,
    PaperOrder,
    Position,
    StrategyRun,
)
from trading_platform.db.session import session_scope
from trading_platform.services import external_activity as external_activity_module
from trading_platform.services.alpaca import (
    AlpacaClientError,
    BrokerFillSnapshot,
    BrokerOrderSnapshot,
)
from trading_platform.services.attribution_inputs import load_recorded_external
from trading_platform.services.broker_jobs import (
    BROKER_TOUCHING_JOB_TYPES,
    STATE_CHANGING_BROKER_JOB_TYPES,
    BrokerEffect,
)
from trading_platform.services.broker_status import broker_status_reason
from trading_platform.services.execution import ExecutionOrderStatus, OrderSide
from trading_platform.services.external_activity import (
    ExternalActivityRejectedError,
    ExternalActivityRejection,
    content_hash,
    record_external_orders,
)
from trading_platform.services.reconciliation import account as account_module
from trading_platform.services.reconciliation import (
    latest_broker_effect_at,
    latest_standalone_reconciliation,
    reconcile_account,
)
from trading_platform.services.reconciliation.report import BrokerStateSnapshot

TX_TIME = "2024-01-05T14:36:00Z"


@pytest.fixture()
def ext_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    with migrated_database(monkeypatch, "external_activity") as name:
        yield name


# --- scripted broker --------------------------------------------------------------------


def _ext_order(
    order_id: str,
    *,
    side: OrderSide = OrderSide.BUY,
    qty: str = "10",
    status: str = "filled",
    symbol: str = "AAPL",
    price: str = "100",
    filled_qty: str | None = None,
    successor: str | None = None,
    client_order_id: str | None = None,
) -> BrokerOrderSnapshot:
    filled = filled_qty if filled_qty is not None else (qty if status == "filled" else "0")
    return BrokerOrderSnapshot(
        broker_order_id=order_id,
        client_order_id=client_order_id or f"manual-{order_id}",
        symbol=symbol,
        side=side,
        quantity=Decimal(qty),
        status=ExecutionOrderStatus.FILLED if status == "filled" else ExecutionOrderStatus.PENDING,
        broker_status=status,
        submitted_at=BROKER_CREATED,
        filled_at=BROKER_CREATED if status == "filled" else None,
        canceled_at=None,
        updated_at=BROKER_CREATED,
        raw_payload={"id": order_id},
        status_reason=broker_status_reason(status),
        created_at=BROKER_CREATED,
        order_type="market",
        successor_order_id=successor,
        filled_quantity=Decimal(filled),
        filled_avg_price=Decimal(price),
    )


def _ext_fill(
    fill_id: str,
    order_id: str,
    *,
    side: OrderSide = OrderSide.BUY,
    qty: str = "10",
    price: str = "100",
    symbol: str = "AAPL",
) -> BrokerFillSnapshot:
    return BrokerFillSnapshot(
        broker_fill_id=fill_id,
        broker_order_id=order_id,
        symbol=symbol,
        side=side,
        quantity=Decimal(qty),
        price=Decimal(price),
        filled_at=BROKER_CREATED,
        raw_payload={"transaction_time": TX_TIME},
    )


class ExtBroker(FakeBroker):
    """A fake broker with by-id order lookup, scriptable failures and call counters."""

    def __init__(self, **kwargs: Any) -> None:
        self.lookup_error: Exception | None = kwargs.pop("lookup_error", None)
        self.fills_error: Exception | None = kwargs.pop("fills_error", None)
        super().__init__(**kwargs)
        self.calls: dict[str, int] = {
            "get_order_by_broker_order_id": 0,
            "list_orders": 0,
            "list_fills": 0,
            "list_positions": 0,
            "get_account": 0,
        }

    def get_order_by_broker_order_id(self, order_id: str) -> BrokerOrderSnapshot | None:
        self.calls["get_order_by_broker_order_id"] += 1
        if self.lookup_error is not None:
            raise self.lookup_error
        return next((o for o in self._orders if o.broker_order_id == order_id), None)

    def list_orders(self) -> list[BrokerOrderSnapshot]:
        self.calls["list_orders"] += 1
        return super().list_orders()

    def list_fills(self) -> list[BrokerFillSnapshot]:
        self.calls["list_fills"] += 1
        if self.fills_error is not None:
            raise self.fills_error
        return super().list_fills()

    def list_positions(self) -> list[Any]:
        self.calls["list_positions"] += 1
        return super().list_positions()

    def get_account(self) -> Any:
        self.calls["get_account"] += 1
        return super().get_account()

    def set_order(self, order: BrokerOrderSnapshot) -> None:
        self._orders = [
            order if o.broker_order_id == order.broker_order_id else o for o in self._orders
        ]

    def set_fill(self, fill: BrokerFillSnapshot) -> None:
        self._fills = [fill if f.broker_fill_id == fill.broker_fill_id else f for f in self._fills]

    def add_fill(self, fill: BrokerFillSnapshot) -> None:
        self._fills = [*self._fills, fill]

    def drop_order(self, order_id: str) -> None:
        self._orders = [o for o in self._orders if o.broker_order_id != order_id]
        self._fills = [f for f in self._fills if f.broker_order_id != order_id]


def _pair_broker(**kwargs: Any) -> ExtBroker:
    """A scripted external buy 10 + sell 10 (net 0) in AAPL."""

    return ExtBroker(
        orders=[_ext_order("b1"), _ext_order("s1", side=OrderSide.SELL)],
        fills=[_ext_fill("f1", "b1"), _ext_fill("f2", "s1", side=OrderSide.SELL)],
        **kwargs,
    )


def _record(broker: ExtBroker, ids: list[str] | None = None, reason: str = "manual round trip"):
    return record_external_orders(
        ids or ["b1", "s1"],
        reason,
        job_id=None,
        settings=load_settings(),
        broker_client=broker,
    )


def _reconcile(broker: ExtBroker):
    return reconcile_account(as_of_session=SESSION, settings=load_settings(), broker_client=broker)


def _rows() -> list[ExternalBrokerActivity]:
    with session_scope(load_settings()) as session:
        rows = (
            session.execute(
                select(ExternalBrokerActivity).order_by(
                    ExternalBrokerActivity.created_at, ExternalBrokerActivity.broker_order_id
                )
            )
            .scalars()
            .all()
        )
        session.expunge_all()
        return list(rows)


def _count(model: type) -> int:
    with session_scope(load_settings()) as session:
        return session.execute(select(func.count()).select_from(model)).scalar_one()


def _rejection(broker: ExtBroker, ids: list[str]) -> ExternalActivityRejectedError:
    with pytest.raises(ExternalActivityRejectedError) as excinfo:
        _record(broker, ids)
    assert _rows() == []
    return excinfo.value


# --- closed vocabulary ------------------------------------------------------------------


def test_rejection_reasons_are_a_closed_set() -> None:
    assert {member.value for member in ExternalActivityRejection} == {
        "external_order_not_terminal",
        "external_exposure_nonzero",
        "broker_record_unavailable",
        "order_owned_by_strategy",
    }


def test_rejected_error_message_starts_with_the_closed_reason() -> None:
    error = ExternalActivityRejectedError(
        ExternalActivityRejection.EXTERNAL_EXPOSURE_NONZERO, ["b1", "b2"]
    )
    assert error.failure_message().split()[0].rstrip(":") == "external_exposure_nonzero"
    assert error.order_ids == ("b1", "b2")


def test_record_external_activity_is_a_broker_touching_state_changing_type() -> None:
    assert BROKER_TOUCHING_JOB_TYPES["record-external-activity"] is BrokerEffect.READS_BROKER
    assert "record-external-activity" in STATE_CHANGING_BROKER_JOB_TYPES


# --- content hash -----------------------------------------------------------------------


def test_content_hash_is_deterministic_and_order_independent() -> None:
    order = _ext_order("b1")
    fills = [_ext_fill("f1", "b1", qty="4"), _ext_fill("f2", "b1", qty="6")]
    reference = content_hash(order, fills)
    assert len(reference) == 64
    assert content_hash(order, list(reversed(fills))) == reference
    # Numerically equal decimals encode identically.
    assert content_hash(replace(order, quantity=Decimal("10.000000")), fills) == reference


@pytest.mark.parametrize(
    "mutate",
    [
        lambda o, f: (replace(o, quantity=Decimal("11")), f),
        lambda o, f: (replace(o, broker_status="canceled"), f),
        lambda o, f: (replace(o, side=OrderSide.SELL), f),
        lambda o, f: (replace(o, symbol="MSFT"), f),
        lambda o, f: (replace(o, filled_avg_price=Decimal("101")), f),
        lambda o, f: (o, [replace(f[0], price=Decimal("101")), f[1]]),
        lambda o, f: (o, [replace(f[0], quantity=Decimal("5")), f[1]]),
        lambda o, f: (o, f[:1]),
        lambda o, f: (o, [*f, _ext_fill("f3", "b1", qty="1")]),
    ],
)
def test_content_hash_changes_with_every_verified_field(mutate) -> None:
    order = _ext_order("b1")
    fills = [_ext_fill("f1", "b1", qty="4"), _ext_fill("f2", "b1", qty="6")]
    changed_order, changed_fills = mutate(order, fills)
    assert content_hash(changed_order, changed_fills) != content_hash(order, fills)


# --- recording --------------------------------------------------------------------------


def test_buy_sell_pair_is_recorded_and_the_fresh_account_check_is_clean(ext_db: str) -> None:
    broker = _pair_broker()
    result = _record(broker)

    assert len(result.recorded_activity_ids) == 2
    assert result.already_recorded_order_ids == ()
    rows = _rows()
    assert {r.broker_order_id for r in rows} == {"b1", "s1"}
    assert {r.origin_tag for r in rows} == {"external_format"}
    assert all(r.reason == "manual round trip" and r.job_id is None for r in rows)
    for row in rows:
        assert row.content_hash == content_hash(
            _ext_order(row.broker_order_id, side=OrderSide(row.side)),
            [f for f in broker.list_fills() if f.broker_order_id == row.broker_order_id],
        )
    assert broker.calls["get_order_by_broker_order_id"] == 2

    report = _reconcile(broker)
    assert report.blocks_execution is False
    assert report.classification_summary["orders"]["recorded_external"] == 2
    with session_scope(load_settings()) as session:
        latest = latest_standalone_reconciliation(session)
        assert latest is not None and latest.is_clean


def test_without_recording_the_same_broker_state_blocks(ext_db: str) -> None:
    report = _reconcile(_pair_broker())
    assert report.blocks_execution is True
    assert report.classification_summary["orders"]["unrecognized"] == 2


def test_non_terminal_order_is_rejected_with_nothing_stored(ext_db: str) -> None:
    broker = ExtBroker(orders=[_ext_order("b1", status="new", filled_qty="0")], fills=[])
    error = _rejection(broker, ["b1"])
    assert error.reason is ExternalActivityRejection.EXTERNAL_ORDER_NOT_TERMINAL
    assert error.order_ids == ("b1",)
    assert _count(AccountReconciliationRun) == 0


def test_unmapped_status_counts_as_not_terminal(ext_db: str) -> None:
    broker = ExtBroker(orders=[_ext_order("b1", status="brand_new_status", filled_qty="0")])
    assert (
        _rejection(broker, ["b1"]).reason is ExternalActivityRejection.EXTERNAL_ORDER_NOT_TERMINAL
    )


def test_replaced_order_requires_its_successor_to_be_listed_and_terminal(ext_db: str) -> None:
    orders = [
        _ext_order("a1", status="replaced", filled_qty="0", successor="a2"),
        _ext_order("a2", status="canceled", filled_qty="0"),
    ]
    broker = ExtBroker(orders=orders)
    # Successor not listed.
    assert (
        _rejection(broker, ["a1"]).reason is ExternalActivityRejection.EXTERNAL_ORDER_NOT_TERMINAL
    )
    # Successor listed but still working.
    working = ExtBroker(
        orders=[orders[0], _ext_order("a2", status="new", filled_qty="0")],
    )
    error = _rejection(working, ["a1", "a2"])
    assert error.reason is ExternalActivityRejection.EXTERNAL_ORDER_NOT_TERMINAL
    assert error.order_ids == ("a2",)
    # Both listed and terminal: recorded.
    result = _record(broker, ["a1", "a2"])
    assert len(result.recorded_activity_ids) == 2


def test_lone_filled_order_has_nonzero_exposure_and_is_rejected(ext_db: str) -> None:
    broker = ExtBroker(orders=[_ext_order("b1")], fills=[_ext_fill("f1", "b1")])
    error = _rejection(broker, ["b1"])
    assert error.reason is ExternalActivityRejection.EXTERNAL_EXPOSURE_NONZERO


def test_exposure_includes_previously_recorded_activity(ext_db: str) -> None:
    broker = _pair_broker()
    _record(broker)
    # A new lone buy is non-zero on its own even though the earlier pair netted to zero.
    broker._orders = [*broker._orders, _ext_order("b2")]
    broker._fills = [*broker._fills, _ext_fill("f3", "b2")]
    with pytest.raises(ExternalActivityRejectedError) as excinfo:
        _record(broker, ["b2"])
    assert excinfo.value.reason is ExternalActivityRejection.EXTERNAL_EXPOSURE_NONZERO
    assert len(_rows()) == 2
    # A matching sell listed together nets to zero again.
    broker._orders = [*broker._orders, _ext_order("s2", side=OrderSide.SELL)]
    broker._fills = [*broker._fills, _ext_fill("f4", "s2", side=OrderSide.SELL)]
    result = _record(broker, ["b2", "s2"])
    assert len(result.recorded_activity_ids) == 2


def test_exposure_is_netted_per_symbol(ext_db: str) -> None:
    broker = ExtBroker(
        orders=[
            _ext_order("b1", symbol="AAPL"),
            _ext_order("s1", side=OrderSide.SELL, symbol="MSFT"),
        ],
        fills=[
            _ext_fill("f1", "b1", symbol="AAPL"),
            _ext_fill("f2", "s1", side=OrderSide.SELL, symbol="MSFT"),
        ],
    )
    error = _rejection(broker, ["b1", "s1"])
    assert error.reason is ExternalActivityRejection.EXTERNAL_EXPOSURE_NONZERO


@pytest.mark.parametrize(
    "broker_kwargs",
    [
        pytest.param({"orders": []}, id="http_404"),
        pytest.param(
            {"orders": [_ext_order("b1")], "lookup_error": AlpacaClientError("500")},
            id="lookup_failure",
        ),
        pytest.param(
            {"orders": [_ext_order("b1")], "lookup_error": httpx.ConnectError("down")},
            id="transport_error",
        ),
        pytest.param(
            {"orders": [_ext_order("b1")], "fills_error": _cap_error()},
            id="pagination_cap",
        ),
        pytest.param(
            {"orders": [_ext_order("b1")], "fills": [_ext_fill("f1", "b1", qty="4")]},
            id="fills_do_not_add_up",
        ),
    ],
)
def test_broker_record_unavailable_stores_nothing(
    ext_db: str, broker_kwargs: dict[str, Any]
) -> None:
    error = _rejection(ExtBroker(**broker_kwargs), ["b1"])
    assert error.reason is ExternalActivityRejection.BROKER_RECORD_UNAVAILABLE


def test_owned_order_is_rejected_and_never_recorded(ext_db: str) -> None:
    _ensure_strategy(OWNER)
    ((client_id, broker_id),) = _seed_orders(
        OWNER, with_fill=True, status="filled", broker_status="filled"
    )
    owned = replace(
        _broker_order(broker_order_id=broker_id, client_order_id=client_id, broker_status="filled"),
        filled_quantity=Decimal("10"),
    )
    broker = ExtBroker(orders=[owned], fills=[_ext_fill("f-own", broker_id)])
    error = _rejection(broker, [broker_id])
    assert error.reason is ExternalActivityRejection.ORDER_OWNED_BY_STRATEGY
    assert error.order_ids == (broker_id,)


def test_owned_order_with_an_ownership_anomaly_is_still_rejected(ext_db: str) -> None:
    _ensure_strategy(OWNER)
    ((client_id, broker_id),) = _seed_orders(OWNER, status="filled", broker_status="filled")
    mismatched = replace(
        _broker_order(
            broker_order_id=broker_id,
            client_order_id=client_id,
            broker_status="filled",
            quantity="7",
        ),
        filled_quantity=Decimal("0"),
    )
    error = _rejection(ExtBroker(orders=[mismatched]), [broker_id])
    assert error.reason is ExternalActivityRejection.ORDER_OWNED_BY_STRATEGY


def test_already_recorded_order_is_an_idempotent_noop_and_the_check_still_runs(
    ext_db: str,
) -> None:
    broker = _pair_broker()
    _record(broker)
    broker._orders = [
        *broker._orders,
        _ext_order("u1", symbol="MSFT", status="new", filled_qty="0"),
    ]

    blocking = _reconcile(broker)
    assert blocking.blocks_execution is True  # the unrelated working order stays unrecognized

    again = _record(broker)
    assert again.recorded_activity_ids == ()
    assert set(again.already_recorded_order_ids) == {"b1", "s1"}
    assert len(_rows()) == 2
    assert _reconcile(broker).blocks_execution is True


def test_recording_alone_never_lifts_a_block_when_the_fresh_check_fails(ext_db: str) -> None:
    broker = _pair_broker()
    broker._orders = [
        *broker._orders,
        _ext_order("u1", symbol="MSFT", status="new", filled_qty="0"),
    ]
    _record(broker)
    assert len(_rows()) == 2

    report = _reconcile(broker)
    assert report.blocks_execution is True
    assert report.classification_summary["orders"]["recorded_external"] == 2
    assert report.classification_summary["orders"]["unrecognized"] == 1
    with session_scope(load_settings()) as session:
        latest = latest_standalone_reconciliation(session)
        assert latest is not None and latest.is_clean is False


def test_failing_fresh_check_leaves_block_in_place(ext_db: str) -> None:
    # A position divergence remains although every listed order is explained.
    broker = _pair_broker(positions=[])
    _record(broker)
    broker._positions = [_position_for("MSFT", "3")]
    report = _reconcile(broker)
    assert report.blocks_execution is True
    with session_scope(load_settings()) as session:
        latest = latest_standalone_reconciliation(session)
        assert latest is not None and latest.is_clean is False


def _position_for(symbol: str, quantity: str):
    from tests.test_attribution_reconciliation import _broker_position

    return _broker_position(symbol, quantity)


# --- tamper and disappearance -----------------------------------------------------------


def _tamper_quantity(broker: ExtBroker) -> None:
    broker.set_order(replace(_ext_order("b1"), quantity=Decimal("11")))


def _tamper_fill_price(broker: ExtBroker) -> None:
    broker.set_fill(_ext_fill("f1", "b1", price="101"))


def _tamper_status(broker: ExtBroker) -> None:
    broker.set_order(replace(_ext_order("b1"), broker_status="canceled"))


def _tamper_added_fill(broker: ExtBroker) -> None:
    broker.add_fill(_ext_fill("f9", "b1", qty="1"))


@pytest.mark.parametrize(
    "tamper",
    [
        pytest.param(_tamper_quantity, id="quantity"),
        pytest.param(_tamper_fill_price, id="fill_price"),
        pytest.param(_tamper_status, id="status"),
        pytest.param(_tamper_added_fill, id="added_fill"),
    ],
)
def test_tampered_broker_data_blocks_again(ext_db: str, tamper) -> None:
    broker = _pair_broker()
    _record(broker)
    assert _reconcile(broker).blocks_execution is False

    tamper(broker)
    report = _reconcile(broker)

    assert report.blocks_execution is True
    assert report.classification_summary["orders"]["recorded_external"] == 1
    assert report.classification_summary["orders"]["unrecognized"] == 1
    assert report.classification_summary["origin_tags"] == {"external_format": 1}
    with session_scope(load_settings()) as session:
        run = (
            session.execute(
                select(AccountReconciliationRun).order_by(
                    AccountReconciliationRun.created_at.desc()
                )
            )
            .scalars()
            .first()
        )
        assert run is not None
        items = run.result_summary["attribution"]["unrecognized_orders"]
        assert [i["broker_order_id"] for i in items] == ["b1"]
        assert items[0]["recorded_snapshot_mismatch"] is True
        assert items[0]["origin_tag"] == "external_format"
        assert run.blocks_execution is True


def test_a_mismatching_recorded_item_is_recordable_again(ext_db: str) -> None:
    broker = _pair_broker()
    _record(broker)
    _tamper_status(broker)
    assert _reconcile(broker).blocks_execution is True

    again = _record(broker)
    assert len(again.recorded_activity_ids) == 1
    assert again.already_recorded_order_ids == ("s1",)
    assert len([r for r in _rows() if r.broker_order_id == "b1"]) == 2
    assert _reconcile(broker).blocks_execution is False


def test_recorded_order_missing_from_the_broker_is_a_blocking_finding(ext_db: str) -> None:
    broker = _pair_broker()
    _record(broker)
    broker.drop_order("b1")

    report = _reconcile(broker)
    assert report.blocks_execution is True
    assert any(f["event_type"] == "recorded_external_missing" for f in report.findings)
    missing = [f for f in report.findings if f["event_type"] == "recorded_external_missing"]
    assert missing[0]["details"] == {"broker_order_id": "b1"}
    assert missing[0]["blocks_execution"] is True
    assert report.blocking_count >= 1


# --- idempotence, concurrency, cost -----------------------------------------------------


def test_re_recording_identical_data_stores_no_new_row_and_the_check_still_runs(
    ext_db: str,
) -> None:
    broker = _pair_broker()
    first = _record(broker)
    second = _record(broker)
    assert len(first.recorded_activity_ids) == 2
    assert second.recorded_activity_ids == ()
    assert len(_rows()) == 2
    assert _reconcile(broker).blocks_execution is False
    assert _count(AccountReconciliationRun) == 1


def test_concurrent_identical_requests_never_raise_and_store_one_row_per_order(
    ext_db: str,
) -> None:
    settings = load_settings()
    results: list[Any] = []
    errors: list[BaseException] = []
    barrier = threading.Barrier(2)

    def worker() -> None:
        broker = _pair_broker()
        try:
            barrier.wait(timeout=10)
            results.append(
                record_external_orders(
                    ["b1", "s1"], "concurrent", None, settings=settings, broker_client=broker
                )
            )
        except BaseException as exc:  # noqa: BLE001 - the assertion below reports it
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert errors == []
    assert len(results) == 2
    assert len(_rows()) == 2
    assert sum(len(r.recorded_activity_ids) for r in results) == 2
    assert sum(len(r.already_recorded_order_ids) for r in results) == 2


def test_latest_row_per_order_is_the_one_a_check_compares(ext_db: str) -> None:
    broker = _pair_broker()
    _record(broker)
    _tamper_status(broker)
    _record(broker)
    with session_scope(load_settings()) as session:
        recorded = load_recorded_external(session)
    assert set(recorded) == {"b1", "s1"}
    canceled_hash = content_hash(
        replace(_ext_order("b1"), broker_status="canceled"),
        [f for f in broker.list_fills() if f.broker_order_id == "b1"],
    )
    assert recorded["b1"].content_hash == canceled_hash


def test_reverification_makes_no_extra_broker_call_and_at_most_one_extra_statement(
    ext_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker = _pair_broker()
    _record(broker)
    state = BrokerStateSnapshot(
        orders=tuple(broker.list_orders()),
        fills=tuple(broker.list_fills()),
        positions=tuple(broker.list_positions()),
        account=broker.get_account(),
    )

    def evaluate() -> int:
        with session_scope(load_settings()) as session:
            with count_queries(session) as counter:
                account_module._evaluate_account(
                    session, state, platform_prefix="tp", failure_threshold=3
                )
        return counter.count

    with_records = evaluate()
    with monkeypatch.context() as patched:
        patched.setattr(account_module, "load_recorded_external", lambda _session: {})
        without_provider = evaluate()
    assert with_records <= without_provider + 1

    calls_before = dict(broker.calls)
    report = _reconcile(broker)
    assert report.blocks_execution is False
    after = broker.calls
    assert after["get_order_by_broker_order_id"] == calls_before["get_order_by_broker_order_id"]
    assert after["list_orders"] == calls_before["list_orders"] + 1
    assert after["list_fills"] == calls_before["list_fills"] + 1
    assert after["list_positions"] == calls_before["list_positions"] + 1


# --- no adoption ------------------------------------------------------------------------


def test_recording_never_writes_orders_fills_positions_or_ownership(ext_db: str) -> None:
    _ensure_strategy(OWNER)
    before = {
        "orders": _count(PaperOrder),
        "fills": _count(PaperFill),
        "positions": _count(Position),
        "runs": _count(StrategyRun),
    }
    with session_scope(load_settings()) as session:
        singleton_before = {
            c.name: getattr(row, c.name)
            for row in [session.execute(select(ActivePaperStrategy)).scalar_one()]
            for c in ActivePaperStrategy.__table__.columns
        }

    broker = _pair_broker()
    _record(broker)
    _reconcile(broker)

    assert {
        "orders": _count(PaperOrder),
        "fills": _count(PaperFill),
        "positions": _count(Position),
        "runs": _count(StrategyRun),
    } == before
    with session_scope(load_settings()) as session:
        singleton_after = {
            c.name: getattr(row, c.name)
            for row in [session.execute(select(ActivePaperStrategy)).scalar_one()]
            for c in ActivePaperStrategy.__table__.columns
        }
    assert singleton_after == singleton_before
    assert not hasattr(ExternalBrokerActivity, "strategy_id")


def test_external_activity_module_imports_no_local_order_fill_position_or_ownership() -> None:
    forbidden = {"PaperOrder", "PaperFill", "Position", "StrategyRun", "ActivePaperStrategy"}
    tree = ast.parse(Path(external_activity_module.__file__).read_text())
    imported: set[str] = set()
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    assert not (imported & forbidden)
    assert not {
        m
        for m in modules
        if m.endswith(
            ("paper_order", "paper_fill", "position", "strategy_run", "active_paper_strategy")
        )
    }
    assert "trading_platform.services.active_paper_strategy" not in modules


# --- effect-time boundary ---------------------------------------------------------------


def _make_job(job_type: str, *, completed_at: datetime) -> uuid.UUID:
    with session_scope(load_settings()) as session:
        job = Job(
            job_type=job_type,
            payload={},
            status=JobStatus.SUCCEEDED,
            completed_at=completed_at,
        )
        session.add(job)
        session.flush()
        return job.id


def test_latest_broker_effect_at_is_none_without_any_effect(ext_db: str) -> None:
    with session_scope(load_settings()) as session:
        assert latest_broker_effect_at(session) is None


def test_latest_broker_effect_at_uses_row_time_for_recording(ext_db: str) -> None:
    broker = _pair_broker()
    _reconcile(broker)  # an older reconciliation: before any recording
    _record(broker)

    with session_scope(load_settings()) as session:
        effect = latest_broker_effect_at(session)
        assert effect is not None
        assert effect == max(r.created_at for r in _rows())
        assert latest_standalone_reconciliation(session, completed_after=effect) is None

    fresh = _reconcile(broker)
    with session_scope(load_settings()) as session:
        latest = latest_standalone_reconciliation(session, completed_after=effect)
        assert latest is not None
        assert str(latest.run_id) == fresh.run_id
        assert latest.is_clean


def test_recording_job_completion_time_does_not_move_the_boundary(ext_db: str) -> None:
    broker = _pair_broker()
    _record(broker)
    with session_scope(load_settings()) as session:
        effect = latest_broker_effect_at(session)
    # The recording Job completes AFTER its inner fresh run; it must not count.
    _make_job("record-external-activity", completed_at=datetime.now(UTC) + timedelta(hours=1))
    with session_scope(load_settings()) as session:
        assert latest_broker_effect_at(session) == effect


@pytest.mark.parametrize("job_type", ["paper-session", "broker-order-sync"])
def test_state_changing_jobs_still_contribute_their_completed_at(
    ext_db: str, job_type: str
) -> None:
    broker = _pair_broker()
    _record(broker)
    later = datetime.now(UTC) + timedelta(hours=2)
    _make_job(job_type, completed_at=later)
    with session_scope(load_settings()) as session:
        assert latest_broker_effect_at(session) == later
    _make_job("reconciliation", completed_at=later + timedelta(hours=1))
    with session_scope(load_settings()) as session:
        assert latest_broker_effect_at(session) == later  # report-only Jobs never count
