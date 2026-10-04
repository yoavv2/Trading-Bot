"""DB-backed tests for owner-less account sync and reconciliation (ACCT-01, D-06/D-08/D-09/D-11).

The account scope is report-only: it assigns nothing to any owner, submits nothing,
creates no position and no strategy run, and stores its result without any owner
reference. ``latest_standalone_reconciliation`` is the one reader gates consult.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from tests.support.migrated_db import migrated_database
from tests.support.paper_ownership import set_active_paper_strategy
from tests.support.query_counter import count_queries
from tests.test_attribution_reconciliation import (
    BROKER_CREATED,
    OTHER,
    OWNER,
    SESSION,
    FakeBroker,
    _account,
    _broker_fill,
    _broker_order,
    _broker_position,
    _cap_error,
    _ensure_strategy,
    _seed_baseline_snapshot,
    _seed_orders,
)

from trading_platform.core.settings import load_settings
from trading_platform.db.models import (
    AccountReconciliationRun,
    AccountSnapshot,
    ActivePaperStrategy,
    Job,
    PaperFill,
    PaperOrder,
    Position,
    Strategy,
    StrategyRun,
    StrategyRunStatus,
    StrategyRunType,
)
from trading_platform.db.models.symbol import Symbol
from trading_platform.db.session import session_scope
from trading_platform.services.alpaca import BrokerOrderSnapshot
from trading_platform.services.execution import ExecutionOrderStatus
from trading_platform.services.execution.sync_orders import sync_account_state
from trading_platform.services.reconciliation import account as account_module
from trading_platform.services.reconciliation import (
    latest_standalone_reconciliation,
    reconcile_account,
)


@pytest.fixture()
def account_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    with migrated_database(monkeypatch, "account_reconciliation") as name:
        yield name


class CountingBroker(FakeBroker):
    """A fake broker that records any attempt to submit an order (a POST)."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.post_count = 0

    def submit_order(self, *_args: object, **_kwargs: object) -> None:
        self.post_count += 1
        raise AssertionError("account scope must never submit an order")


def _count(model: type) -> int:
    with session_scope(load_settings()) as session:
        return session.execute(select(func.count()).select_from(model)).scalar_one()


def _seed_open_position(strategy_id: str, *, symbol: str = "AAPL", quantity: str = "10") -> None:
    with session_scope(load_settings()) as session:
        strategy_pk = session.execute(
            select(Strategy.id).where(Strategy.strategy_id == strategy_id)
        ).scalar_one()
        symbol_pk = session.execute(select(Symbol.id).where(Symbol.ticker == symbol)).scalar_one()
        session.add(
            Position(
                strategy_id=strategy_pk,
                symbol_id=symbol_pk,
                status="open",
                quantity=Decimal(quantity),
                average_entry_price=Decimal("100"),
                cost_basis=Decimal(quantity) * Decimal("100"),
                opened_session_date=SESSION,
                opened_at=BROKER_CREATED,
            )
        )


def _singleton_row() -> dict[str, object]:
    with session_scope(load_settings()) as session:
        row = session.execute(select(ActivePaperStrategy)).scalar_one()
        return {c.name: getattr(row, c.name) for c in ActivePaperStrategy.__table__.columns}


def _reconcile(broker: FakeBroker, **kwargs: object):
    return reconcile_account(
        as_of_session=SESSION, settings=load_settings(), broker_client=broker, **kwargs
    )


def _stored_runs() -> list[AccountReconciliationRun]:
    with session_scope(load_settings()) as session:
        runs = (
            session.execute(
                select(AccountReconciliationRun).order_by(AccountReconciliationRun.created_at)
            )
            .scalars()
            .all()
        )
        session.expunge_all()
        return list(runs)


# --- reconcile_account ------------------------------------------------------------------


def test_29_sep_shape_is_a_clean_result_without_any_strategy_reference(account_db: str) -> None:
    runs_before = _count(StrategyRun)
    report = reconcile_account(settings=load_settings(), broker_client=FakeBroker())

    assert report.blocks_execution is False
    assert report.finding_count == 0
    assert report.unresolved_reasons == ()
    (stored,) = _stored_runs()
    assert stored.status == "succeeded"
    assert stored.scope == "account"
    assert stored.blocks_execution is False
    assert stored.findings == []
    assert stored.completed_at is not None
    assert stored.job_id is None
    assert not hasattr(stored, "strategy_id")
    assert _count(StrategyRun) == runs_before


def test_no_owner_reports_unknown_exposure_and_writes_nothing_else(account_db: str) -> None:
    _ensure_strategy(OWNER)
    assert _singleton_row()["strategy_id"] is None
    before = {
        "positions": _count(Position),
        "orders": _count(PaperOrder),
        "fills": _count(PaperFill),
        "strategy_runs": _count(StrategyRun),
        "snapshots": _count(AccountSnapshot),
    }

    report = _reconcile(FakeBroker(positions=[_broker_position("AAPL", "5")]))

    assert report.blocks_execution is True
    assert report.finding_count >= 1
    assert {k: Decimal(v) for k, v in report.unexplained_exposure.items()} == {"AAPL": Decimal("5")}
    assert {
        "positions": _count(Position),
        "orders": _count(PaperOrder),
        "fills": _count(PaperFill),
        "strategy_runs": _count(StrategyRun),
        "snapshots": _count(AccountSnapshot),
    } == before
    (stored,) = _stored_runs()
    assert stored.blocks_execution is True
    assert stored.finding_count == len(stored.findings) >= 1
    assert {k: Decimal(v) for k, v in stored.unexplained_exposure.items()} == {
        "AAPL": Decimal("5")
    }


def test_unknown_broker_order_is_unrecognized_never_owned_and_blocks(account_db: str) -> None:
    _ensure_strategy(OWNER)
    report = _reconcile(
        FakeBroker(
            orders=[
                _broker_order(broker_order_id="b-manual", client_order_id="manual-1", symbol="MSFT")
            ]
        )
    )

    assert report.blocks_execution is True
    assert report.classification_summary["orders"]["unrecognized"] == 1
    assert report.classification_summary["orders"]["owned"] == 0
    assert report.classification_summary["origin_tags"] == {"external_format": 1}
    assert _count(PaperOrder) == 0


def test_every_owners_records_match_so_two_strategies_reconcile_clean(account_db: str) -> None:
    ((own_client, own_broker),) = _seed_orders(
        OWNER, symbol="AAPL", with_fill=True, status="filled", broker_status="filled"
    )
    ((other_client, other_broker),) = _seed_orders(
        OTHER, symbol="MSFT", with_fill=True, status="filled", broker_status="filled"
    )
    _seed_open_position(OWNER, symbol="AAPL")
    _seed_open_position(OTHER, symbol="MSFT")
    _seed_baseline_snapshot(OWNER, open_positions=2, gross="2000")
    broker = FakeBroker(
        orders=[
            _broker_order(
                broker_order_id=own_broker, client_order_id=own_client, broker_status="filled"
            ),
            _broker_order(
                broker_order_id=other_broker,
                client_order_id=other_client,
                symbol="MSFT",
                broker_status="filled",
            ),
        ],
        fills=[
            _broker_fill(fill_id=f"fill-{own_broker}", order_id=own_broker),
            _broker_fill(fill_id=f"fill-{other_broker}", order_id=other_broker, symbol="MSFT"),
        ],
        positions=[_broker_position("AAPL", "10"), _broker_position("MSFT", "10")],
    )
    broker._account = _account()  # noqa: SLF001

    report = _reconcile(broker)

    assert report.finding_count == 0, report.findings
    assert report.blocks_execution is False
    assert report.classification_summary["orders"]["owned"] == 2


def test_cap_overflow_is_a_stored_blocking_unresolved_result(
    account_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fail_if_called(**_kwargs: object) -> None:
        raise AssertionError("the matcher must never run against partial broker history")

    monkeypatch.setattr(account_module, "match_snapshots", _fail_if_called)

    report = _reconcile(FakeBroker(orders_error=_cap_error()))

    assert report.blocks_execution is True
    assert report.unresolved_reasons == ("broker_history_exceeds_cap",)
    assert report.findings == ()
    (stored,) = _stored_runs()
    assert stored.status == "succeeded"
    assert stored.blocks_execution is True
    assert stored.unresolved_reasons == ["broker_history_exceeds_cap"]
    assert stored.result_summary["unresolved_reasons"] == ["broker_history_exceeds_cap"]


def test_other_failure_finalizes_failed_blocking_and_reraises(account_db: str) -> None:
    with pytest.raises(RuntimeError, match="broker down"):
        _reconcile(FakeBroker(orders_error=RuntimeError("broker down")))
    (stored,) = _stored_runs()
    assert stored.status == "failed"
    assert stored.blocks_execution is True
    assert stored.completed_at is not None
    assert "broker down" in (stored.error_message or "")
    assert _count(StrategyRun) == 0


def test_account_scope_never_submits_or_changes_ownership(account_db: str) -> None:
    _ensure_strategy(OWNER)
    set_active_paper_strategy(load_settings(), OWNER)
    before = _singleton_row()
    broker = CountingBroker(positions=[_broker_position("AAPL", "5")])

    _reconcile(broker)
    sync_account_state(settings=load_settings(), broker_client=broker)

    assert broker.post_count == 0
    assert _singleton_row() == before


# --- sync_account_state -----------------------------------------------------------------


def _with_broker_state(order: BrokerOrderSnapshot, **changes: object) -> BrokerOrderSnapshot:
    return replace(order, **changes)  # type: ignore[arg-type]


def test_account_sync_applies_known_orders_across_strategies_only(account_db: str) -> None:
    ((own_client, own_broker),) = _seed_orders(OWNER, symbol="AAPL")
    ((other_client, other_broker),) = _seed_orders(OTHER, symbol="MSFT")
    filled = _with_broker_state(
        _broker_order(
            broker_order_id=own_broker, client_order_id=own_client, broker_status="filled"
        ),
        filled_quantity=Decimal("10"),
        filled_at=BROKER_CREATED + timedelta(minutes=1),
    )
    canceled = _with_broker_state(
        _broker_order(
            broker_order_id=other_broker,
            client_order_id=other_client,
            symbol="MSFT",
            broker_status="canceled",
        ),
        status=ExecutionOrderStatus.CANCELED,
        filled_quantity=Decimal("0"),
        canceled_at=BROKER_CREATED + timedelta(minutes=2),
    )
    unknown = _broker_order(
        broker_order_id="b-unknown", client_order_id="manual-9", symbol="NVDA", broker_status="filled"
    )
    broker = FakeBroker(
        orders=[filled, canceled, unknown],
        fills=[
            _broker_fill(fill_id="f-own", order_id=own_broker),
            _broker_fill(fill_id="f-unknown", order_id="b-unknown", symbol="NVDA"),
        ],
        positions=[_broker_position("AAPL", "10"), _broker_position("NVDA", "10")],
    )
    before = {
        "orders": _count(PaperOrder),
        "positions": _count(Position),
        "strategy_runs": _count(StrategyRun),
    }

    report = sync_account_state(
        as_of_session=SESSION, settings=load_settings(), broker_client=broker
    )

    assert report.orders_synced == 2
    assert report.fills_ingested == 1
    assert report.open_positions == 2
    with session_scope(load_settings()) as session:
        orders = {
            o.broker_order_id: o for o in session.execute(select(PaperOrder)).scalars().all()
        }
        assert orders[own_broker].status.value == "filled"
        assert orders[other_broker].status.value == "canceled"
        assert set(orders) == {own_broker, other_broker}  # no PaperOrder created for the unknown
        fills = session.execute(select(PaperFill.broker_fill_id)).scalars().all()
        assert fills == ["f-own"]  # the unknown order's fill is not ingested
        snapshot = session.execute(select(AccountSnapshot)).scalar_one()
        assert snapshot.strategy_id is None
        assert snapshot.snapshot_source == "broker_sync"
        assert str(snapshot.id) == report.account_snapshot_id
    assert _count(PaperOrder) == before["orders"]
    assert _count(Position) == before["positions"] == 0
    assert _count(StrategyRun) == before["strategy_runs"]

    by_order = {record["paper_order_id"]: record for record in report.applied_orders}
    assert len(by_order) == 2
    assert {r["broker_status"] for r in by_order.values()} == {"filled", "canceled"}
    assert {Decimal(r["broker_filled_qty"]) for r in by_order.values()} == {
        Decimal("10"),
        Decimal("0"),
    }
    assert all(r["applied_at"] for r in by_order.values())


def test_account_sync_with_unknown_broker_position_creates_no_local_position(
    account_db: str,
) -> None:
    sync_account_state(
        settings=load_settings(),
        broker_client=FakeBroker(positions=[_broker_position("AAPL", "10")]),
    )
    assert _count(Position) == 0


# --- latest_standalone_reconciliation ---------------------------------------------------


def _insert_strategy_run(
    *, trigger_source: str, job_type: str | None, completed_at: datetime, blocks: bool = False
) -> StrategyRun:
    _ensure_strategy(OWNER)
    with session_scope(load_settings()) as session:
        strategy_pk = session.execute(
            select(Strategy.id).where(Strategy.strategy_id == OWNER)
        ).scalar_one()
        job_pk = None
        if job_type is not None:
            job = Job(job_type=job_type, payload={})
            session.add(job)
            session.flush()
            job_pk = job.id
        run = StrategyRun(
            strategy_id=strategy_pk,
            job_id=job_pk,
            run_type=StrategyRunType.RECONCILIATION,
            status=StrategyRunStatus.SUCCEEDED,
            trigger_source=trigger_source,
            completed_at=completed_at,
            parameters_snapshot={},
            result_summary={"blocks_execution": blocks, "unresolved_reasons": []},
        )
        session.add(run)
        session.flush()
        session.expunge(run)
        return run


def _insert_account_run(*, completed_at: datetime | None, blocks: bool = False) -> None:
    with session_scope(load_settings()) as session:
        session.add(
            AccountReconciliationRun(
                trigger_source="job",
                status="succeeded" if completed_at is not None else "pending",
                completed_at=completed_at,
                blocks_execution=blocks,
            )
        )


NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def test_latest_standalone_returns_the_account_run_for_a_strategy_level_query(
    account_db: str,
) -> None:
    _insert_account_run(completed_at=NOW)
    with session_scope(load_settings()) as session:
        found = latest_standalone_reconciliation(session, strategy_public_id=OWNER)
    assert found is not None
    assert found.scope == "account"
    assert found.strategy_id is None
    assert found.is_clean is True
    assert found.completed_at == NOW


def test_latest_standalone_ignores_in_session_reconciliation(account_db: str) -> None:
    # An in-session run (trigger '<x>_reconciliation', paper-session Job) is never standalone,
    # even when it is the newest run of all.
    _insert_strategy_run(
        trigger_source="paper_session_reconciliation",
        job_type="paper-session",
        completed_at=NOW + timedelta(hours=2),
    )
    # A 'job' trigger linked to a non-reconciliation Job is not standalone either.
    _insert_strategy_run(
        trigger_source="job", job_type="paper-session", completed_at=NOW + timedelta(hours=3)
    )
    with session_scope(load_settings()) as session:
        assert latest_standalone_reconciliation(session, strategy_public_id=OWNER) is None

    standalone = _insert_strategy_run(
        trigger_source="job", job_type="reconciliation", completed_at=NOW
    )
    with session_scope(load_settings()) as session:
        found = latest_standalone_reconciliation(session, strategy_public_id=OWNER)
    assert found is not None
    assert found.scope == "strategy"
    assert found.run_id == standalone.id
    assert found.strategy_id == OWNER


def test_latest_standalone_picks_newest_across_scopes_and_honours_filters(
    account_db: str,
) -> None:
    strategy_run = _insert_strategy_run(
        trigger_source="job", job_type="reconciliation", completed_at=NOW + timedelta(hours=1)
    )
    _insert_account_run(completed_at=NOW)
    _insert_account_run(completed_at=None)  # a pending run never qualifies

    with session_scope(load_settings()) as session:
        either = latest_standalone_reconciliation(session, strategy_public_id=OWNER)
        assert either is not None and either.run_id == strategy_run.id
        account_only = latest_standalone_reconciliation(session, scope="account")
        assert account_only is not None and account_only.scope == "account"
        strategy_only = latest_standalone_reconciliation(session, scope="strategy")
        assert strategy_only is not None and strategy_only.scope == "strategy"
        # a different strategy has no strategy-level standalone run, so the account run answers
        other = latest_standalone_reconciliation(session, strategy_public_id=OTHER)
        assert other is not None and other.scope == "account"
        # completed_after is strict
        assert (
            latest_standalone_reconciliation(session, completed_after=NOW + timedelta(hours=1))
            is None
        )
        assert latest_standalone_reconciliation(session, completed_after=NOW) is not None


def test_is_clean_requires_succeeded_not_blocking_and_nothing_unresolved(account_db: str) -> None:
    _insert_account_run(completed_at=NOW, blocks=True)
    with session_scope(load_settings()) as session:
        found = latest_standalone_reconciliation(session)
    assert found is not None and found.is_clean is False

    with session_scope(load_settings()) as session:
        session.add(
            AccountReconciliationRun(
                trigger_source="job",
                status="succeeded",
                completed_at=NOW + timedelta(minutes=1),
                blocks_execution=False,
                unresolved_reasons=["broker_history_exceeds_cap"],
            )
        )
    with session_scope(load_settings()) as session:
        found = latest_standalone_reconciliation(session)
    assert found is not None and found.is_clean is False


def test_latest_standalone_query_count_is_bounded_independent_of_history(account_db: str) -> None:
    def _statements() -> int:
        with session_scope(load_settings()) as session:
            with count_queries(session) as counter:
                latest_standalone_reconciliation(session, strategy_public_id=OWNER)
            return counter.count

    _insert_account_run(completed_at=NOW)
    _insert_strategy_run(trigger_source="job", job_type="reconciliation", completed_at=NOW)
    small = _statements()
    for offset in range(1, 15):
        _insert_account_run(completed_at=NOW + timedelta(minutes=offset))
    large = _statements()
    assert small == large <= 2


def test_same_symbol_held_by_two_owners_aggregates_against_the_broker_position(
    account_db: str,
) -> None:
    ((own_client, own_broker),) = _seed_orders(
        OWNER, symbol="AAPL", with_fill=True, status="filled", broker_status="filled"
    )
    ((other_client, other_broker),) = _seed_orders(
        OTHER, symbol="AAPL", quantity="5", with_fill=True, status="filled", broker_status="filled"
    )
    _seed_open_position(OWNER, symbol="AAPL", quantity="10")
    _seed_open_position(OTHER, symbol="AAPL", quantity="5")
    _seed_baseline_snapshot(OWNER, open_positions=1, gross="1500")
    broker_position = _broker_position("AAPL", "15")
    broker_position = replace(broker_position, cost_basis=Decimal("1500"), market_value=Decimal("1500"))
    report = _reconcile(
        FakeBroker(
            orders=[
                _broker_order(
                    broker_order_id=own_broker, client_order_id=own_client, broker_status="filled"
                ),
                _broker_order(
                    broker_order_id=other_broker,
                    client_order_id=other_client,
                    quantity="5",
                    broker_status="filled",
                ),
            ],
            fills=[
                _broker_fill(fill_id=f"fill-{own_broker}", order_id=own_broker),
                _broker_fill(fill_id=f"fill-{other_broker}", order_id=other_broker, quantity="5"),
            ],
            positions=[broker_position],
        )
    )

    assert report.finding_count == 0, report.findings
    assert report.blocks_execution is False


def test_account_module_source_stays_report_only() -> None:
    from pathlib import Path

    source = Path(account_module.__file__).read_text()
    for forbidden in (
        "sync_positions_from_broker",
        "apply_reconciliation_corrections",
        "StrategyRun(",
        "ExecutionEvent(",
        "session.add_all",
    ):
        assert forbidden not in source, forbidden
