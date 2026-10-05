"""SAF-09 (COR-01 / D-27): execution sizes only against a fresh, broker-observed cash basis.

Four decision points are pinned: the per-intent permission check (step 5) pauses
``awaiting_reconciliation`` for a missing (configured-cash fallback) or stale snapshot;
``verify_evaluation_basis`` refuses a recorded basis that is not broker-observed or is stale;
account snapshots are stamped AFTER the broker account read with the application clock.
No broker is contacted: the price source and the broker are fakes and nothing can POST.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session
from tests.support.basis_fixtures import seed_fresh_broker_snapshot
from tests.support.calendar_facts import et, seed_calendar
from tests.support.migrated_db import migrated_database
from tests.support.operation_fixtures import seed_risk_run
from tests.support.paper_ownership import seed_registered_strategy
from tests.support.price_source import ScriptedPriceSource, observation
from tests.support.recovery_fixtures import OTHER, OWNER

from trading_platform.core import clock
from trading_platform.core.settings import Settings, load_settings
from trading_platform.db.models import OrderLifecycleState, StrategyRun
from trading_platform.db.session import get_engine, session_scope
from trading_platform.services.account_baseline import latest_broker_observed_account_snapshot
from trading_platform.services.alpaca import (
    BrokerAccountSnapshot,
    BrokerFillSnapshot,
    BrokerOrderSnapshot,
    BrokerPositionSnapshot,
)
from trading_platform.services.evaluation_manifest import (
    ManifestVerification,
    ManifestVerificationStatus,
)
from trading_platform.services.execution import permission as permission_module
from trading_platform.services.execution.attempts import SubmissionEvidence
from trading_platform.services.execution.intent_identity import (
    BasisFailure,
    BasisRows,
    OrderFact,
    load_basis_verification_rows,
    verify_evaluation_basis,
)
from trading_platform.services.execution.operations import PausedReason
from trading_platform.services.execution.permission import (
    PermissionVerdict,
    PinnedIntent,
    check_intent_permission,
)
from trading_platform.services.execution.sync_orders import sync_account_state, sync_paper_state
from trading_platform.services.portfolio import PortfolioBasis, execution_basis_problem

S = date(2025, 12, 2)
NOW = et(2025, 12, 3, 10, 0)
MAX_AGE = load_settings().execution.account_snapshot_max_age_seconds


@pytest.fixture(scope="module")
def freshness_db() -> Iterator[str]:
    patcher = pytest.MonkeyPatch()
    try:
        with migrated_database(patcher, "account_basis_freshness") as name:
            seed_calendar(date(2025, 11, 24), date(2026, 1, 9))
            settings = load_settings()
            seed_registered_strategy(settings, OWNER, enabled=True, owner=True)
            seed_registered_strategy(settings, OTHER, enabled=True, owner=False)
            yield name
    finally:
        patcher.undo()


@pytest.fixture()
def db(freshness_db: str) -> Iterator[Session]:
    """A session inside a transaction that is always rolled back."""

    engine = get_engine(load_settings())
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


def _check(session: Session, risk_run: StrategyRun, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        permission_module,
        "verify_risk_run_manifest",
        lambda **kwargs: ManifestVerification(ManifestVerificationStatus.MATCHES),
    )
    monkeypatch.setattr(clock, "now_utc", lambda: NOW)
    price = observation("AAPL", "100", observed_at=NOW - timedelta(seconds=5), fetched_at=NOW)
    return check_intent_permission(
        session,
        strategy_id=OWNER,
        as_of_session=S,
        risk_run_id=risk_run.id,
        intent=PinnedIntent(
            symbol="AAPL",
            side="buy",
            quantity=Decimal("10"),
            reference_price=Decimal("100"),
            client_order_id="tp-test",
        ),
        price_source=ScriptedPriceSource([price]),
        settings=load_settings(),
        continuation=False,
        now=NOW,
    )


# ---------------------------------------------------------------------------
# Permission check, step (5)
# ---------------------------------------------------------------------------


def test_permission_pauses_without_a_broker_snapshot(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No broker snapshot -> the basis is the configured-cash fallback -> it never sizes a send."""

    risk_run = seed_risk_run(db)

    outcome = _check(db, risk_run, monkeypatch)

    assert outcome.verdict is PermissionVerdict.PAUSE
    assert outcome.reason == PausedReason.AWAITING_RECONCILIATION.value
    assert outcome.detail == "account_snapshot_missing"
    assert outcome.revalidation is None


def test_permission_pauses_on_a_stale_snapshot(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    risk_run = seed_risk_run(db)
    seed_fresh_broker_snapshot(db, at=NOW - timedelta(seconds=MAX_AGE + 1))

    outcome = _check(db, risk_run, monkeypatch)

    assert outcome.verdict is PermissionVerdict.PAUSE
    assert outcome.reason == PausedReason.AWAITING_RECONCILIATION.value
    assert outcome.detail == "account_snapshot_stale"
    assert outcome.revalidation is None


def test_permission_accepts_a_fresh_snapshot(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """One second inside the bound proceeds to risk revalidation (and the exact bound too)."""

    risk_run = seed_risk_run(db)
    seed_fresh_broker_snapshot(db, at=NOW - timedelta(seconds=MAX_AGE - 1))

    outcome = _check(db, risk_run, monkeypatch)

    assert outcome.ok
    assert outcome.revalidation is not None and outcome.revalidation.ok
    # The boundary itself is inclusive (age == max is not stale).
    boundary = _basis(age=float(MAX_AGE))
    assert execution_basis_problem(boundary, max_age_seconds=MAX_AGE) is None


def _basis(*, source: str = "broker_sync", age: float | None = 0.0) -> PortfolioBasis:
    return PortfolioBasis(
        source=source,  # type: ignore[arg-type]
        cash=Decimal("1000"),
        gross_exposure=Decimal("0"),
        total_equity=Decimal("1000"),
        positions=(),
        total_open_positions=0,
        as_of_session=None,
        snapshot_id=None,
        snapshot_at=None,
        age_seconds=age,
    )


def test_execution_basis_problem_closed_details() -> None:
    assert (
        execution_basis_problem(
            _basis(source="configured_starting_cash", age=None), max_age_seconds=MAX_AGE
        )
        == "account_snapshot_missing"
    )
    assert (
        execution_basis_problem(_basis(age=MAX_AGE + 1.0), max_age_seconds=MAX_AGE)
        == "account_snapshot_stale"
    )
    assert execution_basis_problem(_basis(age=None), max_age_seconds=MAX_AGE) == (
        "account_snapshot_stale"
    )
    assert execution_basis_problem(_basis(age=1.0), max_age_seconds=MAX_AGE) is None


def test_freshness_threshold_is_a_typed_setting_with_a_floor() -> None:
    assert load_settings().execution.account_snapshot_max_age_seconds == 21600
    with pytest.raises(ValueError):
        Settings.model_validate({"execution": {"account_snapshot_max_age_seconds": 59}})


# ---------------------------------------------------------------------------
# Basis verification
# ---------------------------------------------------------------------------


def _rows(
    *,
    source: str | None,
    age: float | None,
    orders: tuple[OrderFact, ...] = (),
    risk_run_id: uuid.UUID | None = None,
) -> BasisRows:
    return BasisRows(
        strategy_public_id=OWNER,
        risk_run_id=risk_run_id or uuid.uuid4(),
        risk_completed_at=NOW,
        basis_source=source,
        basis_snapshot_id=None,
        basis_age_seconds=age,
        max_basis_age_seconds=MAX_AGE,
        basis_positions={},
        local_positions={},
        orders=orders,
        sync=None,
        reconciliation=None,
    )


def _earlier_order() -> OrderFact:
    return OrderFact(
        paper_order_id=uuid.uuid4(),
        source_risk_run_id=uuid.uuid4(),
        status=OrderLifecycleState.FILLED,
        broker_order_id="broker-1",
        symbol="AAPL",
        side="buy",
        session_date=S,
        quantity=Decimal("10"),
        intent_hash="h",
        created_at=NOW - timedelta(days=1),
        last_synced_at=None,
        terminal_at=NOW - timedelta(hours=20),
        fill_quantity=Decimal("10"),
        attempts=(),
        attempt_log_registered=True,
        reached_broker=True,
        submission_evidence=SubmissionEvidence.BROKER_EVIDENCE,
    )


def test_basis_verification_refuses_configured_cash() -> None:
    empty = verify_evaluation_basis(_rows(source="configured_starting_cash", age=None))
    assert empty.failure is BasisFailure.BASIS_NOT_BROKER_OBSERVED

    # Precedence: before predates_executions (non-empty history, no sync at all).
    non_empty = verify_evaluation_basis(
        _rows(source="configured_starting_cash", age=None, orders=(_earlier_order(),))
    )
    assert non_empty.failure is BasisFailure.BASIS_NOT_BROKER_OBSERVED


def test_basis_verification_refuses_a_stale_recorded_basis() -> None:
    stale = verify_evaluation_basis(_rows(source="broker_sync", age=MAX_AGE + 1.0))
    assert stale.failure is BasisFailure.BASIS_STALE
    unrecorded_age = verify_evaluation_basis(_rows(source="broker_sync", age=None))
    assert unrecorded_age.failure is BasisFailure.BASIS_STALE

    non_empty = verify_evaluation_basis(
        _rows(source="broker_sync", age=MAX_AGE + 1.0, orders=(_earlier_order(),))
    )
    assert non_empty.failure is BasisFailure.BASIS_STALE
    # A fresh broker basis with an empty history is verified; with history the old rules apply.
    assert verify_evaluation_basis(_rows(source="broker_sync", age=MAX_AGE - 1.0)).verified
    assert (
        verify_evaluation_basis(
            _rows(source="broker_sync", age=1.0, orders=(_earlier_order(),))
        ).failure
        is BasisFailure.PREDATES_EXECUTIONS
    )


def test_hand_seeded_run_without_basis_keeps_existing_rules(db: Session) -> None:
    assert verify_evaluation_basis(_rows(source=None, age=None)).verified
    assert (
        verify_evaluation_basis(_rows(source=None, age=None, orders=(_earlier_order(),))).failure
        is BasisFailure.PREDATES_EXECUTIONS
    )
    # Through the loader: a run that recorded no basis is judged by the existing rules.
    risk_run = seed_risk_run(db)
    rows = load_basis_verification_rows(db, strategy_public_id=OWNER, risk_run=risk_run)
    assert rows.basis_source is None and rows.basis_age_seconds is None
    assert rows.max_basis_age_seconds == MAX_AGE
    assert verify_evaluation_basis(rows).verified


def test_loader_reads_the_recorded_basis_source_and_age(db: Session) -> None:
    risk_run = seed_risk_run(db)
    risk_run.result_summary = {
        "portfolio_basis": {"source": "broker_sync", "age_seconds": MAX_AGE + 5.0}
    }
    db.flush()

    rows = load_basis_verification_rows(db, strategy_public_id=OWNER, risk_run=risk_run)

    assert rows.basis_age_seconds == MAX_AGE + 5.0
    assert verify_evaluation_basis(rows).failure is BasisFailure.BASIS_STALE
    risk_run.result_summary = {
        "portfolio_basis": {"source": "configured_starting_cash", "age_seconds": None}
    }
    db.flush()
    rows = load_basis_verification_rows(db, strategy_public_id=OWNER, risk_run=risk_run)
    assert verify_evaluation_basis(rows).failure is BasisFailure.BASIS_NOT_BROKER_OBSERVED


# ---------------------------------------------------------------------------
# Snapshot stamping
# ---------------------------------------------------------------------------


T_BEFORE = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
T_AFTER = T_BEFORE + timedelta(hours=1)


def _account(cash: str) -> BrokerAccountSnapshot:
    return BrokerAccountSnapshot(
        cash=Decimal(cash),
        buying_power=Decimal(cash),
        equity=Decimal(cash),
        long_market_value=Decimal("0"),
        short_market_value=Decimal("0"),
        raw_payload={},
    )


class _FakeBroker:
    """A broker fake with an optional hook that runs inside ``get_account`` (no POST exists)."""

    def __init__(self, cash: str, *, on_account=None) -> None:
        self._cash = cash
        self._on_account = on_account

    def close(self) -> None:
        return None

    def list_orders(self) -> list[BrokerOrderSnapshot]:
        return []

    def list_fills(self) -> list[BrokerFillSnapshot]:
        return []

    def list_positions(self) -> list[BrokerPositionSnapshot]:
        return []

    def get_account(self) -> BrokerAccountSnapshot:
        if self._on_account is not None:
            self._on_account()
        return _account(self._cash)


def _snapshots(session: Session) -> list:
    from sqlalchemy import select

    from trading_platform.db.models import AccountSnapshot

    return list(session.execute(select(AccountSnapshot)).scalars())


@pytest.fixture()
def sync_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    with migrated_database(monkeypatch, "account_basis_stamp") as name:
        yield name


@pytest.mark.parametrize("path", ["account", "paper"])
def test_snapshot_is_stamped_after_the_account_read(
    sync_db: str, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    state = {"now": T_BEFORE}
    monkeypatch.setattr(clock, "now_utc", lambda: state["now"])
    broker = _FakeBroker("100", on_account=lambda: state.update(now=T_AFTER))

    if path == "account":
        sync_account_state(settings=load_settings(), broker_client=broker)  # type: ignore[arg-type]
    else:
        sync_paper_state(
            as_of_session=S,
            settings=load_settings(),
            broker_client=broker,  # type: ignore[arg-type]
        )

    with session_scope(load_settings()) as session:
        snapshots = _snapshots(session)
        assert len(snapshots) == 1
        assert snapshots[0].snapshot_at == T_AFTER
        assert snapshots[0].snapshot_source == "broker_sync"


def test_overlapping_syncs_keep_the_later_observation(
    sync_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sync A starts first but its account read finishes last: A is the latest observation."""

    ticks = {"n": 0}

    def tick() -> datetime:
        ticks["n"] += 1
        return T_BEFORE + timedelta(seconds=ticks["n"])

    monkeypatch.setattr(clock, "now_utc", tick)
    settings = load_settings()

    def run_b_inside_a() -> None:
        sync_account_state(settings=settings, broker_client=_FakeBroker("222"))  # type: ignore[arg-type]

    sync_account_state(
        settings=settings,
        broker_client=_FakeBroker("111", on_account=run_b_inside_a),  # type: ignore[arg-type]
    )

    with session_scope(settings) as session:
        latest = latest_broker_observed_account_snapshot(session)
        assert latest is not None
        assert latest.cash == Decimal("111")
        assert len(_snapshots(session)) == 2
