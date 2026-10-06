"""20.1-14: ``current_trading_blockers`` -- the closed "why can trading not happen now" list.

Built from the SAME predicates as ``check_intent_permission`` (single source). One migrated
database per module; each case runs in a rolled-back transaction. Calendar facts use XNYS:
evaluation session S = Tuesday 2025-12-02, execution session D = Wednesday 2025-12-03.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import update
from sqlalchemy.orm import Session
from tests.support.basis_fixtures import seed_fresh_broker_snapshot
from tests.support.calendar_facts import seed_calendar
from tests.support.migrated_db import migrated_database
from tests.support.operation_fixtures import seed_risk_run
from tests.support.paper_ownership import seed_registered_strategy, set_active_paper_strategy
from tests.support.price_source import ScriptedPriceSource, observation
from tests.support.query_counter import count_queries
from tests.support.recovery_fixtures import (
    OTHER,
    OWNER,
    at,
    seed_account_run,
    seed_intent,
    seed_paper_run,
    seed_uncertain_session,
    strategy_row,
)
from tests.test_operation_permission import IN_WINDOW, REFERENCE, S

from trading_platform.core import clock
from trading_platform.core.settings import load_settings
from trading_platform.db.models import (
    AttemptOutcomeClass,
    KillSwitchState,
    OrderLifecycleState,
    StrategyStatus,
    SystemControl,
)
from trading_platform.db.session import get_engine
from trading_platform.services.evaluation_manifest import (
    ManifestVerification,
    ManifestVerificationStatus,
)
from trading_platform.services.execution import permission as permission_module
from trading_platform.services.execution.permission import (
    PinnedIntent,
    TradingBlocker,
    check_intent_permission,
    current_trading_blockers,
)
from trading_platform.services.operator_controls import GLOBAL_KILL_SWITCH_NAME
from trading_platform.services.recovery import GateCode


@pytest.fixture(scope="module")
def blockers_db() -> Iterator[str]:
    patcher = pytest.MonkeyPatch()
    try:
        with migrated_database(patcher, "trading_blockers") as name:
            seed_calendar(date(2025, 11, 24), date(2026, 1, 9))
            settings = load_settings()
            seed_registered_strategy(settings, OWNER, enabled=True, owner=True)
            seed_registered_strategy(settings, OTHER, enabled=True, owner=False)
            yield name
    finally:
        patcher.undo()


@pytest.fixture()
def db(blockers_db: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[Session]:
    engine = get_engine(load_settings())
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    monkeypatch.setattr(clock, "now_utc", lambda: IN_WINDOW)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


# ---------------------------------------------------------------------------
# Conditions: one per blocker, with the per-intent pause reason it must share
# ---------------------------------------------------------------------------


def _no_owner(session: Session) -> None:
    set_active_paper_strategy(session, None)


def _disabled(session: Session) -> None:
    strategy_row(session, OWNER).status = StrategyStatus.DISABLED
    session.flush()


def _kill_switch(session: Session) -> None:
    session.execute(
        update(SystemControl)
        .where(SystemControl.name == GLOBAL_KILL_SWITCH_NAME)
        .values(state=KillSwitchState.TRIPPED)
    )
    session.flush()


def _unresolved(session: Session) -> None:
    seed_uncertain_session(session)


def _blocking_reconciliation(session: Session) -> None:
    seed_account_run(session, completed_at=at(5), blocks=True)


def _unrecognized(session: Session) -> None:
    seed_account_run(
        session,
        completed_at=at(5),
        blocks=True,
        classification_summary={"orders": {"unrecognized": 1}, "fills": {"unrecognized": 0}},
    )


def _working(session: Session, broker_order_id: str = "broker-working-1") -> None:
    run = seed_paper_run(session, None)
    seed_intent(
        session,
        run,
        status=OrderLifecycleState.SUBMITTED,
        attempts=(AttemptOutcomeClass.ACCEPTED,),
        broker_order_id=broker_order_id,
        broker_status="new",
    )


#: blocker -> (arming function, the per-intent pause reason of the same condition)
CASES: dict[TradingBlocker, tuple[Callable[[Session], None], str]] = {
    TradingBlocker.NO_ACTIVE_PAPER_STRATEGY: (_no_owner, "not_active_paper_strategy"),
    TradingBlocker.STRATEGY_DISABLED: (_disabled, "strategy_disabled"),
    TradingBlocker.KILL_SWITCH_TRIPPED: (_kill_switch, "kill_switch_tripped"),
    TradingBlocker.OUTCOME_UNRESOLVED: (_unresolved, "outcome_unresolved"),
    TradingBlocker.RECONCILIATION_BLOCKING: (_blocking_reconciliation, "reconciliation_blocking"),
    TradingBlocker.UNRECOGNIZED_BROKER_ACTIVITY: (_unrecognized, "unrecognized_broker_activity"),
    TradingBlocker.WORKING_ORDER_COMMITMENTS_UNACCOUNTED: (
        _working,
        "working_order_commitments_unaccounted",
    ),
}


def _permission(session: Session, monkeypatch: pytest.MonkeyPatch):
    risk_run = seed_risk_run(session)
    monkeypatch.setattr(
        permission_module,
        "verify_risk_run_manifest",
        lambda **kwargs: ManifestVerification(ManifestVerificationStatus.MATCHES),
    )
    source = ScriptedPriceSource(
        [observation("AAPL", "100", observed_at=IN_WINDOW, fetched_at=IN_WINDOW)]
    )
    return check_intent_permission(
        session,
        strategy_id=OWNER,
        as_of_session=S,
        risk_run_id=risk_run.id,
        intent=PinnedIntent(
            symbol="AAPL",
            side="buy",
            quantity=Decimal("10"),
            reference_price=REFERENCE,
            client_order_id="tp-test",
        ),
        price_source=source,
        settings=load_settings(),
        now=IN_WINDOW,
    )


def test_trading_blocker_value_set_is_closed() -> None:
    assert {b.value for b in TradingBlocker} == {
        "no_active_paper_strategy",
        "strategy_disabled",
        "kill_switch_tripped",
        "outcome_unresolved",
        "reconciliation_blocking",
        "unrecognized_broker_activity",
        "working_order_commitments_unaccounted",
    }


def test_empty_list_when_nothing_blocks(db: Session) -> None:
    assert current_trading_blockers(db) == []


@pytest.mark.parametrize("blocker", list(CASES), ids=lambda b: b.value)
def test_each_blocker_is_reported_on_its_own(db: Session, blocker: TradingBlocker) -> None:
    CASES[blocker][0](db)

    assert current_trading_blockers(db) == [blocker]


def test_no_blocker_means_the_per_intent_check_is_ok(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No blocker -> the per-intent check does not pause (owner, enabled, kill switch, recovery,
    working orders, reconciliation)."""

    seed_fresh_broker_snapshot(db, at=IN_WINDOW)  # SAF-09 (20.1-24): a fresh observed account
    assert current_trading_blockers(db) == []
    assert _permission(db, monkeypatch).ok


@pytest.mark.parametrize("blocker", list(CASES), ids=lambda b: b.value)
def test_trading_blockers_parity_with_intent_permission(
    db: Session, monkeypatch: pytest.MonkeyPatch, blocker: TradingBlocker
) -> None:
    """One case per blocker: the list reports exactly it and the per-intent check pauses with the
    corresponding reason (single source of predicates)."""
    arm, pause_reason = CASES[blocker]
    arm(db)

    outcome = _permission(db, monkeypatch)

    assert current_trading_blockers(db) == [blocker]
    assert outcome.verdict.value == "pause"
    assert outcome.reason == pause_reason


def test_blockers_have_a_deterministic_order_and_stack(db: Session) -> None:
    _working(db)
    _kill_switch(db)
    _disabled(db)
    _blocking_reconciliation(db)

    assert current_trading_blockers(db) == [
        TradingBlocker.STRATEGY_DISABLED,
        TradingBlocker.KILL_SWITCH_TRIPPED,
        TradingBlocker.RECONCILIATION_BLOCKING,
        TradingBlocker.WORKING_ORDER_COMMITMENTS_UNACCOUNTED,
    ]


def test_no_owner_still_reports_a_tripped_kill_switch(db: Session) -> None:
    _no_owner(db)
    _kill_switch(db)

    assert current_trading_blockers(db) == [
        TradingBlocker.NO_ACTIVE_PAPER_STRATEGY,
        TradingBlocker.KILL_SWITCH_TRIPPED,
    ]


@pytest.mark.parametrize("gate", list(GateCode), ids=lambda g: g.value)
def test_the_three_recovery_gate_codes_all_map_to_outcome_unresolved(
    db: Session, monkeypatch: pytest.MonkeyPatch, gate: GateCode
) -> None:
    monkeypatch.setattr(
        permission_module,
        "strategy_recovery_status",
        lambda *args, **kwargs: SimpleNamespace(gate_code=gate),
    )

    assert current_trading_blockers(db) == [TradingBlocker.OUTCOME_UNRESOLVED]


def test_current_trading_blockers_writes_nothing_and_is_bounded(db: Session) -> None:
    _working(db)
    _blocking_reconciliation(db)
    db.flush()

    with count_queries(db) as few:
        current_trading_blockers(db)
    for index in range(3):
        _working(db, f"broker-working-extra-{index}")
        seed_account_run(db, completed_at=at(4), blocks=False)
    db.flush()
    with count_queries(db) as many:
        current_trading_blockers(db)

    assert few.count <= 12
    assert many.count == few.count  # independent of history size
    for statement in many.statements:
        assert statement.lstrip().split(None, 1)[0].upper() not in {"INSERT", "UPDATE", "DELETE"}
