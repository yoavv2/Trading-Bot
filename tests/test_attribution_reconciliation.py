"""DB-backed tests for attribution-aware reconciliation (COR-05, D-07/D-08/D-11)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from tests.support.migrated_db import migrated_database
from tests.support.paper_ownership import seed_strategy, set_active_paper_strategy
from tests.support.query_counter import count_queries

from trading_platform.core.settings import load_settings
from trading_platform.db.models import (
    AccountSnapshot,
    PaperFill,
    PaperOrder,
    RiskEvent,
    StrategyRun,
    StrategyRunStatus,
    StrategyRunType,
)
from trading_platform.db.models.symbol import Symbol
from trading_platform.db.session import session_scope
from trading_platform.services import attribution_inputs
from trading_platform.services.alpaca import (
    AlpacaPaginationCapExceededError,
    BrokerAccountSnapshot,
    BrokerFillSnapshot,
    BrokerOrderSnapshot,
    BrokerPositionSnapshot,
)
from trading_platform.services.execution import (
    ExecutionOrderStatus,
    OrderSide,
    build_client_order_id,
)
from trading_platform.services.execution.idempotency import build_intent_hash
from trading_platform.services.reconciliation import reconcile_paper_execution
from trading_platform.services.reconciliation import report as report_module
from trading_platform.strategies.registry import build_default_registry

SESSION = date(2024, 1, 5)
REGISTERED = datetime(2024, 1, 5, 14, 30, tzinfo=UTC)
BROKER_CREATED = datetime(2024, 1, 5, 14, 35, tzinfo=UTC)
OWNER = "trend_following_daily"
OTHER = "donchian_breakout_daily"


@pytest.fixture()
def attribution_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    with migrated_database(monkeypatch, "attribution_reconciliation") as name:
        yield name


class FakeBroker:
    def __init__(
        self,
        *,
        orders: list[BrokerOrderSnapshot] | None = None,
        fills: list[BrokerFillSnapshot] | None = None,
        positions: list[BrokerPositionSnapshot] | None = None,
        account: BrokerAccountSnapshot | None = None,
        orders_error: Exception | None = None,
    ) -> None:
        self._orders = orders or []
        self._fills = fills or []
        self._positions = positions or []
        self._account = account or _account()
        self._orders_error = orders_error

    def close(self) -> None:
        return None

    def list_orders(self) -> list[BrokerOrderSnapshot]:
        if self._orders_error is not None:
            raise self._orders_error
        return list(self._orders)

    def list_fills(self) -> list[BrokerFillSnapshot]:
        return list(self._fills)

    def list_positions(self) -> list[BrokerPositionSnapshot]:
        return list(self._positions)

    def get_account(self) -> BrokerAccountSnapshot:
        return self._account


def _account(equity: str = "100000.000000") -> BrokerAccountSnapshot:
    return BrokerAccountSnapshot(
        cash=Decimal(equity),
        buying_power=Decimal(equity),
        equity=Decimal(equity),
        long_market_value=Decimal("0"),
        short_market_value=Decimal("0"),
        raw_payload={},
    )


def _broker_order(
    *,
    broker_order_id: str,
    client_order_id: str,
    symbol: str = "AAPL",
    quantity: str = "10",
    broker_status: str = "new",
    side: OrderSide = OrderSide.BUY,
    created_at: datetime | None = BROKER_CREATED,
) -> BrokerOrderSnapshot:
    return BrokerOrderSnapshot(
        broker_order_id=broker_order_id,
        client_order_id=client_order_id,
        symbol=symbol,
        side=side,
        quantity=Decimal(quantity),
        status=ExecutionOrderStatus.FILLED
        if broker_status == "filled"
        else ExecutionOrderStatus.PENDING,
        broker_status=broker_status,
        submitted_at=BROKER_CREATED,
        filled_at=None,
        canceled_at=None,
        updated_at=BROKER_CREATED,
        raw_payload={"id": broker_order_id},
        created_at=created_at,
        order_type="market",
    )


def _broker_fill(*, fill_id: str, order_id: str, symbol: str = "AAPL", quantity: str = "10"):
    return BrokerFillSnapshot(
        broker_fill_id=fill_id,
        broker_order_id=order_id,
        symbol=symbol,
        side=OrderSide.BUY,
        quantity=Decimal(quantity),
        price=Decimal("100"),
        filled_at=BROKER_CREATED,
        raw_payload={},
    )


def _broker_position(symbol: str = "AAPL", quantity: str = "10") -> BrokerPositionSnapshot:
    return BrokerPositionSnapshot(
        symbol=symbol,
        quantity=Decimal(quantity),
        average_entry_price=Decimal("100"),
        cost_basis=Decimal("1000"),
        market_value=Decimal("1000"),
        current_price=Decimal("100"),
        raw_payload={},
    )


def _platform_id(strategy_id: str, symbol: str, quantity: str) -> str:
    return build_client_order_id(
        prefix=load_settings().execution.client_order_id_prefix,
        strategy_id=strategy_id,
        session_date=SESSION,
        symbol=symbol,
        side=OrderSide.BUY,
        quantity=Decimal(quantity),
    )


def _seed_orders(
    strategy_id: str,
    *,
    count: int = 1,
    symbol: str = "AAPL",
    quantity: str = "10",
    with_fill: bool = False,
    status: str = "submitted",
    broker_status: str = "new",
    first_suffix: int = 0,
) -> list[tuple[str, str]]:
    """Seed ``count`` paper orders for ``strategy_id``; returns (client_id, broker_id) pairs."""

    settings = load_settings()
    metadata = build_default_registry(settings).resolve(strategy_id).metadata
    created: list[tuple[str, str]] = []
    with session_scope(settings) as session:
        strategy_record = seed_strategy(session, metadata, enabled=True)
        symbol_row = session.execute(
            select(Symbol).where(Symbol.ticker == symbol)
        ).scalar_one_or_none()
        if symbol_row is None:
            symbol_row = Symbol(ticker=symbol, active=True)
            session.add(symbol_row)
            session.flush()
        risk_run = StrategyRun(
            strategy_id=strategy_record.id,
            run_type=StrategyRunType.RISK_EVALUATION,
            status=StrategyRunStatus.SUCCEEDED,
            trigger_source="test_suite",
            parameters_snapshot={},
            result_summary={},
        )
        execution_run = StrategyRun(
            strategy_id=strategy_record.id,
            run_type=StrategyRunType.PAPER_EXECUTION,
            status=StrategyRunStatus.SUCCEEDED,
            trigger_source="test_seed",
            parameters_snapshot={},
            result_summary={},
        )
        session.add_all([risk_run, execution_run])
        session.flush()
        risk_events = [
            RiskEvent(
                strategy_run_id=risk_run.id,
                symbol_id=symbol_row.id,
                session_date=SESSION - timedelta(days=first_suffix + offset),
                signal_direction="long",
                signal_reason="trend_entry",
                outcome="approved",
                decision_code="approved",
                decision_reason="Approved.",
                reference_price=Decimal("100"),
                proposed_quantity=Decimal(quantity),
                proposed_notional=Decimal("1000"),
                risk_metadata={},
            )
            for offset in range(count)
        ]
        session.add_all(risk_events)
        session.flush()
        for index, risk_event in enumerate(risk_events):
            suffix = first_suffix + index
            client_order_id = (
                _platform_id(strategy_id, symbol, quantity)
                if count == 1 and first_suffix == 0
                else f"seed-{strategy_id[:6]}-{suffix}"
            )
            broker_order_id = f"broker-{strategy_id[:6]}-{symbol}-{suffix}"
            order = PaperOrder(
                strategy_run_id=execution_run.id,
                source_risk_event_id=risk_event.id,
                symbol_id=symbol_row.id,
                intended_session_date=SESSION,
                side="buy",
                quantity=Decimal(quantity),
                order_type="market",
                time_in_force="day",
                intent_hash=build_intent_hash(
                    strategy_id=f"{strategy_id}-{suffix}",
                    session_date=SESSION,
                    symbol=symbol,
                    side=OrderSide.BUY,
                    quantity=Decimal(quantity),
                ),
                intent_version=1,
                client_order_id=client_order_id,
                broker_order_id=broker_order_id,
                status=status,
                broker_status=broker_status,
                submitted_at=BROKER_CREATED,
                submission_attempt_count=1,
                broker_payload={},
                created_at=REGISTERED,
            )
            session.add(order)
            session.flush()
            if with_fill:
                session.add(
                    PaperFill(
                        paper_order_id=order.id,
                        symbol_id=symbol_row.id,
                        broker_fill_id=f"fill-{broker_order_id}",
                        broker_order_id=broker_order_id,
                        side="buy",
                        quantity=Decimal(quantity),
                        price=Decimal("100"),
                        filled_at=BROKER_CREATED,
                        broker_payload={},
                    )
                )
            created.append((client_order_id, broker_order_id))
    return created


def _seed_baseline_snapshot(strategy_id: str, *, open_positions: int, gross: str) -> None:
    settings = load_settings()
    metadata = build_default_registry(settings).resolve(strategy_id).metadata
    with session_scope(settings) as session:
        strategy_record = seed_strategy(session, metadata, enabled=True)
        session.add(
            AccountSnapshot(
                strategy_id=strategy_record.id,
                source_run_id=None,
                snapshot_source="broker_sync",
                snapshot_at=BROKER_CREATED,
                cash=Decimal("100000"),
                gross_exposure=Decimal(gross),
                total_equity=Decimal("100000"),
                buying_power=Decimal("100000"),
                open_positions=open_positions,
            )
        )


def _reconcile(broker: FakeBroker, strategy_id: str = OWNER):
    return reconcile_paper_execution(
        strategy_id,
        as_of_session=SESSION,
        settings=load_settings(),
        broker_client=broker,
    )


def _ensure_strategy(strategy_id: str) -> None:
    settings = load_settings()
    metadata = build_default_registry(settings).resolve(strategy_id).metadata
    with session_scope(settings) as session:
        seed_strategy(session, metadata, enabled=True)


# --- (a)/(b) unrecognized orders block --------------------------------------------------


def test_manual_broker_order_blocks_with_external_format_origin(attribution_db: str) -> None:
    _ensure_strategy(OWNER)
    report = _reconcile(
        FakeBroker(
            orders=[
                _broker_order(
                    broker_order_id="b-manual", client_order_id="manual-1", symbol="MSFT"
                )
            ]
        )
    )

    assert report.blocks_execution is True
    assert report.attribution is not None
    unrecognized = report.attribution["unrecognized_orders"]
    assert [item["origin_tag"] for item in unrecognized] == ["external_format"]
    assert unrecognized[0]["broker_order_id"] == "b-manual"
    assert report.unresolved_reasons == ()


def test_platform_format_id_without_local_record_blocks_as_unverified(attribution_db: str) -> None:
    _ensure_strategy(OWNER)
    report = _reconcile(
        FakeBroker(
            orders=[
                _broker_order(
                    broker_order_id="b-fmt",
                    client_order_id=_platform_id(OWNER, "AAPL", "10"),
                )
            ]
        )
    )

    assert report.blocks_execution is True
    assert report.attribution is not None
    assert [i["origin_tag"] for i in report.attribution["unrecognized_orders"]] == [
        "platform_format_unverified"
    ]
    assert report.attribution["orders"]["owned"] == 0


# --- (c) owned attribute mismatch -------------------------------------------------------


def test_local_record_with_different_quantity_blocks(attribution_db: str) -> None:
    ((client_id, broker_id),) = _seed_orders(OWNER, quantity="10")
    report = _reconcile(
        FakeBroker(
            orders=[
                _broker_order(
                    broker_order_id=broker_id, client_order_id=client_id, quantity="9"
                )
            ]
        )
    )

    assert report.blocks_execution is True
    # the matcher alone sees nothing wrong with this order: attribution is what blocks
    assert report.findings == ()
    assert report.attribution is not None
    assert [a["anomaly"] for a in report.attribution["anomalies"]] == [
        "owned_order_attribute_mismatch"
    ]


def test_clean_owned_order_does_not_block(attribution_db: str) -> None:
    ((client_id, broker_id),) = _seed_orders(OWNER)
    report = _reconcile(
        FakeBroker(orders=[_broker_order(broker_order_id=broker_id, client_order_id=client_id)])
    )
    assert report.blocks_execution is False
    assert report.attribution is not None
    assert report.attribution["orders"]["owned"] == 1
    assert report.attribution["anomalies"] == []


# --- (d) two strategies -----------------------------------------------------------------


def _prior_owner_book(*, with_manual_order: bool = False) -> FakeBroker:
    ((client_id, broker_id),) = _seed_orders(
        OTHER, quantity="10", with_fill=True, status="filled", broker_status="filled"
    )
    _ensure_strategy(OWNER)
    set_active_paper_strategy(load_settings(), OWNER)
    _seed_baseline_snapshot(OWNER, open_positions=1, gross="1000")
    orders = [
        _broker_order(
            broker_order_id=broker_id, client_order_id=client_id, broker_status="filled"
        )
    ]
    if with_manual_order:
        orders.append(
            _broker_order(
                broker_order_id="b-manual",
                client_order_id="manual-2019",
                symbol="MSFT",
                broker_status="filled",
                created_at=datetime(2019, 3, 1, tzinfo=UTC),
            )
        )
    return FakeBroker(
        orders=orders,
        fills=[_broker_fill(fill_id=f"fill-{broker_id}", order_id=broker_id)],
        positions=[_broker_position("AAPL", "10")],
    )


def test_prior_owner_orders_do_not_block_the_new_owner(attribution_db: str) -> None:
    report = _reconcile(_prior_owner_book())

    assert report.blocks_execution is False
    assert report.findings == ()
    assert report.attribution is not None
    assert report.attribution["orders"]["owned"] == 1
    assert report.attribution["unexplained_exposure"] == {}
    assert report.attribution["unrecognized_orders"] == []

    # the previous owner reconciling its own scope sees the same order as ITS order
    own_scope = _reconcile(_prior_owner_book_view(), OTHER)
    assert own_scope.attribution is not None
    assert own_scope.attribution["orders"]["owned"] == 1


def _prior_owner_book_view() -> FakeBroker:
    with session_scope(load_settings()) as session:
        broker_id = session.execute(select(PaperOrder.broker_order_id)).scalar_one()
        client_id = session.execute(select(PaperOrder.client_order_id)).scalar_one()
    return FakeBroker(
        orders=[
            _broker_order(
                broker_order_id=broker_id, client_order_id=client_id, broker_status="filled"
            )
        ],
        fills=[_broker_fill(fill_id=f"fill-{broker_id}", order_id=broker_id)],
        positions=[_broker_position("AAPL", "10")],
    )


def test_manual_order_in_old_history_still_blocks_the_new_owner(attribution_db: str) -> None:
    report = _reconcile(_prior_owner_book(with_manual_order=True))

    assert report.blocks_execution is True
    assert report.attribution is not None
    assert [i["broker_order_id"] for i in report.attribution["unrecognized_orders"]] == [
        "b-manual"
    ]
    # the prior owner's order is still not part of the new owner's findings
    assert all(
        (finding.details or {}).get("symbol") != "AAPL" or finding.event_type != "MISSING_LOCAL"
        for finding in report.findings
    )


# --- (e) unexplained exposure -----------------------------------------------------------


def test_unexplained_exposure_blocks_with_symbol_and_quantity(attribution_db: str) -> None:
    _ensure_strategy(OWNER)
    report = _reconcile(FakeBroker(positions=[_broker_position("AAPL", "5")]))

    assert report.blocks_execution is True
    assert report.attribution is not None
    assert {k: Decimal(v) for k, v in report.attribution["unexplained_exposure"].items()} == {
        "AAPL": Decimal("5")
    }


# --- (f) cap overflow -------------------------------------------------------------------


def _cap_error() -> AlpacaPaginationCapExceededError:
    return AlpacaPaginationCapExceededError(
        endpoint="/v2/orders", pages_fetched=20, items_fetched=10_000, max_pages=20
    )


def test_cap_overflow_is_a_blocking_unresolved_result_never_truncation(
    attribution_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ensure_strategy(OWNER)

    def _fail_if_called(**_kwargs: object) -> None:
        raise AssertionError("the matcher must never run against partial broker history")

    monkeypatch.setattr(report_module, "match_snapshots", _fail_if_called)

    report = _reconcile(FakeBroker(orders_error=_cap_error()))

    assert report.blocks_execution is True
    assert report.unresolved_reasons == ("broker_history_exceeds_cap",)
    assert report.findings == ()
    assert report.finding_count == 0
    with session_scope(load_settings()) as session:
        run = (
            session.execute(
                select(StrategyRun).where(StrategyRun.run_type == StrategyRunType.RECONCILIATION)
            )
            .scalars()
            .one()
        )
        assert run.status == StrategyRunStatus.SUCCEEDED
        assert run.result_summary["blocks_execution"] is True
        assert run.result_summary["unresolved_reasons"] == ["broker_history_exceeds_cap"]


def test_non_cap_failures_keep_todays_behavior(
    attribution_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ensure_strategy(OWNER)

    # a broker listing failure other than the cap still propagates (no run is created)
    with pytest.raises(RuntimeError):
        _reconcile(FakeBroker(orders_error=RuntimeError("broker down")))
    with session_scope(load_settings()) as session:
        assert session.execute(select(StrategyRun)).scalars().all() == []

    # a failure after the run exists still FAILS the run
    def _boom(**_kwargs: object) -> None:
        raise RuntimeError("matcher exploded")

    monkeypatch.setattr(report_module, "match_snapshots", _boom)
    with pytest.raises(RuntimeError, match="matcher exploded"):
        _reconcile(FakeBroker())
    with session_scope(load_settings()) as session:
        run = (
            session.execute(
                select(StrategyRun).where(StrategyRun.run_type == StrategyRunType.RECONCILIATION)
            )
            .scalars()
            .one()
        )
        assert run.status == StrategyRunStatus.FAILED


def test_report_to_dict_has_additive_keys_only(attribution_db: str) -> None:
    _ensure_strategy(OWNER)
    payload = _reconcile(FakeBroker()).to_dict()
    assert payload["blocks_execution"] is False
    assert payload["unresolved_reasons"] == []
    assert payload["attribution"]["blocks_execution"] is False
    assert {
        "run_id",
        "strategy_id",
        "session_date",
        "checked_at",
        "finding_count",
        "blocking_count",
        "recovered_order_count",
        "blocks_execution",
        "findings",
    } <= payload.keys()


# --- (h) loaders ------------------------------------------------------------------------


def test_load_local_intent_records_is_one_statement_regardless_of_order_count(
    attribution_db: str,
) -> None:
    settings = load_settings()
    _seed_orders(OWNER, count=1, with_fill=False)
    with session_scope(settings) as session:
        with count_queries(session) as small:
            records = attribution_inputs.load_local_intent_records(session)
    assert len(records) == 1
    assert small.count == 1

    _seed_orders(OWNER, count=200, first_suffix=1)
    _seed_orders(OTHER, count=3)
    with session_scope(settings) as session:
        with count_queries(session) as large:
            records = attribution_inputs.load_local_intent_records(session)
    assert len(records) == 204
    assert large.count == 1
    assert {r.strategy_id for r in records} == {OWNER, OTHER}
    assert all(r.registered_at == REGISTERED for r in records)


def test_loaders_report_owner_period_and_empty_recorded_external(attribution_db: str) -> None:
    settings = load_settings()
    _ensure_strategy(OWNER)
    with session_scope(settings) as session:
        assert attribution_inputs.load_ownership_periods(session) == ()
        assert attribution_inputs.load_recorded_external(session) == {}
    set_active_paper_strategy(settings, OWNER)
    with session_scope(settings) as session:
        periods = attribution_inputs.load_ownership_periods(session)
    assert len(periods) == 1
    assert periods[0].strategy_id == OWNER
    assert periods[0].end is None
    assert periods[0].start <= datetime.now(UTC) + timedelta(seconds=1)


def test_reconcile_writes_no_order_fill_position_or_snapshot_rows(attribution_db: str) -> None:
    ((client_id, broker_id),) = _seed_orders(OWNER, with_fill=True)
    settings = load_settings()

    def _row_counts() -> tuple[int, ...]:
        with session_scope(settings) as session:
            return tuple(
                len(session.execute(select(model)).scalars().all())
                for model in (PaperOrder, PaperFill, AccountSnapshot)
            )

    before = _row_counts()
    _reconcile(
        FakeBroker(
            orders=[_broker_order(broker_order_id=broker_id, client_order_id=client_id)],
            positions=[_broker_position("AAPL", "3")],
        )
    )
    assert _row_counts() == before
