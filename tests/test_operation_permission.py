"""Per-intent permission check (REC-02, D-17/D-25/D-26, S2-R3): fixed precedence and the S2 price step.

One migrated database per module; every case runs inside a transaction that is rolled back,
so the table-driven precedence cases are cheap. Calendar facts use XNYS: evaluation session
S = Tuesday 2025-12-02, execution session D = Wednesday 2025-12-03, window open 09:30 ET until
15:45 ET (15 minute cutoff).
"""

from __future__ import annotations

import itertools
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import update
from sqlalchemy.orm import Session
from tests.support.calendar_facts import et, seed_calendar
from tests.support.migrated_db import migrated_database
from tests.support.operation_fixtures import seed_risk_run
from tests.support.paper_ownership import seed_registered_strategy, set_active_paper_strategy
from tests.support.price_source import ScriptedPriceSource, observation
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

from trading_platform.core import clock
from trading_platform.core.settings import Settings, load_settings
from trading_platform.db.models import (
    AccountSnapshot,
    AttemptOutcomeClass,
    KillSwitchState,
    OrderLifecycleState,
    StrategyRun,
    StrategyStatus,
    SystemControl,
)
from trading_platform.db.session import get_engine
from trading_platform.services.alpaca import PriceFailure, PriceObservation
from trading_platform.services.evaluation_manifest import (
    ManifestVerification,
    ManifestVerificationStatus,
)
from trading_platform.services.execution import permission as permission_module
from trading_platform.services.execution.operations import (
    PausedReason,
    ReevaluationReason,
    TerminatedReason,
)
from trading_platform.services.execution.permission import (
    PERMISSION_STEPS,
    PermissionOutcome,
    PermissionVerdict,
    PinnedIntent,
    check_intent_permission,
    strategy_working_orders,
)
from trading_platform.services.operator_controls import GLOBAL_KILL_SWITCH_NAME

S = date(2025, 12, 2)
IN_WINDOW = et(2025, 12, 3, 10, 0)
BEFORE_OPEN = et(2025, 12, 3, 9, 0)
PAST_CUTOFF = et(2025, 12, 3, 15, 50)
NEXT_DAY = et(2025, 12, 4, 10, 0)
BEYOND_CALENDAR = datetime(2031, 6, 3, 15, 0, tzinfo=UTC)
REFERENCE = Decimal("100")


@pytest.fixture(scope="module")
def perm_db() -> Iterator[str]:
    patcher = pytest.MonkeyPatch()
    try:
        with migrated_database(patcher, "operation_permission") as name:
            seed_calendar(date(2025, 11, 24), date(2026, 1, 9))
            settings = load_settings()
            seed_registered_strategy(settings, OWNER, enabled=True, owner=True)
            seed_registered_strategy(settings, OTHER, enabled=True, owner=False)
            yield name
    finally:
        patcher.undo()


@pytest.fixture()
def db(perm_db: str) -> Iterator[Session]:
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


@dataclass
class World:
    """The mutable inputs of one case: the clock, the manifest verdict, the price script."""

    now: datetime = IN_WINDOW
    manifest: ManifestVerificationStatus = ManifestVerificationStatus.MATCHES
    continuation: bool = False
    price: Any = None
    quantity: Decimal = Decimal("10")
    side: str = "buy"
    settings: Settings = field(default_factory=load_settings)


def _fresh(world: World, price: str = "100", *, age: int = 5) -> PriceObservation:
    return observation(
        "AAPL", price, observed_at=world.now - timedelta(seconds=age), fetched_at=world.now
    )


def _run_check(
    session: Session,
    world: World,
    risk_run: StrategyRun,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[PermissionOutcome, ScriptedPriceSource]:
    monkeypatch.setattr(
        permission_module,
        "verify_risk_run_manifest",
        lambda **kwargs: ManifestVerification(world.manifest),
    )
    monkeypatch.setattr(clock, "now_utc", lambda: world.now)
    script = world.price if world.price is not None else _fresh(world)
    source = ScriptedPriceSource([script])
    outcome = check_intent_permission(
        session,
        strategy_id=OWNER,
        as_of_session=S,
        risk_run_id=risk_run.id,
        intent=PinnedIntent(
            symbol="AAPL",
            side=world.side,
            quantity=world.quantity,
            reference_price=REFERENCE,
            client_order_id="tp-test",
        ),
        price_source=source,
        settings=world.settings,
        continuation=world.continuation,
        now=world.now,
    )
    return outcome, source


# ---------------------------------------------------------------------------
# Conditions (one per reason of the fixed precedence)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Condition:
    name: str
    group: str
    apply: Callable[[Session, World], None]
    verdict: PermissionVerdict
    reason: str
    detail: str | None = None


def _set_now(instant: datetime) -> Callable[[Session, World], None]:
    def apply(session: Session, world: World) -> None:
        world.now = instant

    return apply


def _set_manifest(status: ManifestVerificationStatus) -> Callable[[Session, World], None]:
    def apply(session: Session, world: World) -> None:
        world.manifest = status

    return apply


def _not_owner(session: Session, world: World) -> None:
    set_active_paper_strategy(session, OTHER)


def _disabled(session: Session, world: World) -> None:
    row = strategy_row(session, OWNER)
    row.status = StrategyStatus.DISABLED
    session.flush()


def _kill_switch(session: Session, world: World) -> None:
    session.execute(
        update(SystemControl)
        .where(SystemControl.name == GLOBAL_KILL_SWITCH_NAME)
        .values(state=KillSwitchState.TRIPPED)
    )
    session.flush()


def _unresolved(session: Session, world: World) -> None:
    seed_uncertain_session(session)


def _working(session: Session, world: World) -> None:
    run = seed_paper_run(session, None)
    seed_intent(
        session,
        run,
        status=OrderLifecycleState.SUBMITTED,
        attempts=(AttemptOutcomeClass.ACCEPTED,),
        broker_order_id="broker-working-1",
        broker_status="new",
    )


def _awaiting(session: Session, world: World) -> None:
    world.continuation = True


def _blocking_reconciliation(session: Session, world: World) -> None:
    seed_account_run(session, completed_at=at(5), blocks=True)


def _unrecognized_reconciliation(session: Session, world: World) -> None:
    run = seed_account_run(session, completed_at=at(5), blocks=True)
    run.classification_summary = {"orders": {"unrecognized": 1}, "fills": {"unrecognized": 0}}
    session.flush()


def _price(script: Callable[[World], Any]) -> Callable[[Session, World], None]:
    def apply(session: Session, world: World) -> None:
        world.price = script(world)

    return apply


def _cash_shortfall(session: Session, world: World) -> None:
    session.add(
        AccountSnapshot(
            snapshot_source="broker_sync",
            snapshot_at=at(0),
            cash=Decimal("500"),
            gross_exposure=Decimal("0"),
            total_equity=Decimal("500"),
            buying_power=Decimal("500"),
            open_positions=0,
        )
    )
    session.flush()


CONDITIONS: tuple[Condition, ...] = (
    Condition(
        "window_elapsed", "window", _set_now(PAST_CUTOFF),
        PermissionVerdict.TERMINATE, TerminatedReason.EXECUTION_WINDOW_ELAPSED.value,
    ),
    Condition(
        "window_superseded", "window", _set_now(NEXT_DAY),
        PermissionVerdict.TERMINATE, TerminatedReason.EVALUATION_SUPERSEDED.value,
    ),
    Condition(
        "window_not_yet_open", "window", _set_now(BEFORE_OPEN),
        PermissionVerdict.PAUSE, PausedReason.EXECUTION_WINDOW_NOT_OPEN.value, "not_yet_open",
    ),
    Condition(
        "calendar_unknown", "window", _set_now(BEYOND_CALENDAR),
        PermissionVerdict.PAUSE, PausedReason.EXECUTION_WINDOW_NOT_OPEN.value,
        "calendar_data_unavailable",
    ),
    Condition(
        "evaluation_data_changed", "provenance",
        _set_manifest(ManifestVerificationStatus.EVALUATION_DATA_CHANGED),
        PermissionVerdict.REEVALUATE, ReevaluationReason.EVALUATION_DATA_CHANGED.value,
    ),
    Condition(
        "strategy_settings_changed", "provenance",
        _set_manifest(ManifestVerificationStatus.STRATEGY_SETTINGS_CHANGED),
        PermissionVerdict.REEVALUATE, ReevaluationReason.STRATEGY_SETTINGS_CHANGED.value,
    ),
    Condition(
        "manifest_missing", "provenance",
        _set_manifest(ManifestVerificationStatus.MANIFEST_MISSING),
        PermissionVerdict.REEVALUATE, ReevaluationReason.EVALUATION_DATA_CHANGED.value,
        "manifest_missing",
    ),
    Condition(
        "not_owner", "owner", _not_owner,
        PermissionVerdict.PAUSE, PausedReason.NOT_ACTIVE_PAPER_STRATEGY.value,
    ),
    Condition(
        "disabled", "enabled", _disabled,
        PermissionVerdict.PAUSE, PausedReason.STRATEGY_DISABLED.value,
    ),
    Condition(
        "kill_switch", "kill_switch", _kill_switch,
        PermissionVerdict.PAUSE, PausedReason.KILL_SWITCH_TRIPPED.value,
    ),
    Condition(
        "outcome_unresolved", "outcome_unresolved", _unresolved,
        PermissionVerdict.PAUSE, PausedReason.OUTCOME_UNRESOLVED.value,
    ),
    Condition(
        "working_order", "working_orders", _working,
        PermissionVerdict.PAUSE, PausedReason.WORKING_ORDER_COMMITMENTS_UNACCOUNTED.value,
    ),
    Condition(
        "awaiting_reconciliation", "awaiting_reconciliation", _awaiting,
        PermissionVerdict.PAUSE, PausedReason.AWAITING_RECONCILIATION.value,
    ),
    Condition(
        "reconciliation_blocking", "reconciliation_blocking", _blocking_reconciliation,
        PermissionVerdict.PAUSE, PausedReason.RECONCILIATION_BLOCKING.value,
    ),
    Condition(
        "unrecognized_broker_activity", "reconciliation_blocking", _unrecognized_reconciliation,
        PermissionVerdict.PAUSE, PausedReason.UNRECOGNIZED_BROKER_ACTIVITY.value,
    ),
    Condition(
        "price_unavailable", "price",
        _price(lambda world: PriceFailure.PRICE_LOOKUP_FAILED),
        PermissionVerdict.PAUSE, PausedReason.PRICE_UNAVAILABLE.value, "price_lookup_failed",
    ),
    Condition(
        "price_moved", "price_moved", _price(lambda world: _fresh(world, "106")),
        PermissionVerdict.PAUSE, PausedReason.PRICE_MOVED_BEYOND_TOLERANCE.value,
    ),
    Condition(
        "risk_limit_failed", "risk", _cash_shortfall,
        PermissionVerdict.REEVALUATE, "risk_limit_failed:insufficient_cash",
    ),
)

_BY_NAME = {condition.name: condition for condition in CONDITIONS}

# Pairs whose conditions cannot hold together (the second one's trigger removes the first's).
_EXCLUSIVE = {
    frozenset({"awaiting_reconciliation", "reconciliation_blocking"}),
    frozenset({"awaiting_reconciliation", "unrecognized_broker_activity"}),
    frozenset({"price_unavailable", "price_moved"}),
}


def _arm(session: Session, world: World, *names: str) -> None:
    for name in names:
        _BY_NAME[name].apply(session, world)


def _assert_outcome(outcome: PermissionOutcome, condition: Condition) -> None:
    assert outcome.verdict is condition.verdict, (condition.name, outcome)
    assert outcome.reason == condition.reason, (condition.name, outcome)
    if condition.detail is not None:
        assert outcome.detail == condition.detail, (condition.name, outcome)
    assert outcome.step is not None and outcome.step in PERMISSION_STEPS


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_ok_when_every_check_passes_and_the_audit_carries_the_observation(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    risk_run = seed_risk_run(db)
    world = World()

    outcome, source = _run_check(db, world, risk_run, monkeypatch)

    assert outcome.ok
    assert outcome.observation is not None and outcome.observation.price == Decimal("100")
    assert outcome.revalidation is not None and outcome.revalidation.ok
    audit = outcome.audit()
    assert audit["verdict"] == "ok"
    assert audit["price_observation"]["source"] == "test_scripted"
    assert audit["risk_revalidation"]["code"] == "approved"
    assert source.calls == ["AAPL"]


@pytest.mark.parametrize("condition", CONDITIONS, ids=lambda c: c.name)
def test_every_closed_reason_is_reachable_on_its_own(
    db: Session, monkeypatch: pytest.MonkeyPatch, condition: Condition
) -> None:
    risk_run = seed_risk_run(db)
    world = World()
    condition.apply(db, world)

    outcome, _source = _run_check(db, world, risk_run, monkeypatch)

    _assert_outcome(outcome, condition)


def test_the_reachable_reasons_cover_the_closed_check_vocabulary() -> None:
    reasons = {condition.reason for condition in CONDITIONS}
    assert {
        "execution_window_elapsed",
        "evaluation_superseded",
        "execution_window_not_open",
        "evaluation_data_changed",
        "strategy_settings_changed",
        "not_active_paper_strategy",
        "strategy_disabled",
        "kill_switch_tripped",
        "outcome_unresolved",
        "working_order_commitments_unaccounted",
        "awaiting_reconciliation",
        "reconciliation_blocking",
        "unrecognized_broker_activity",
        "price_unavailable",
        "price_moved_beyond_tolerance",
        "risk_limit_failed:insufficient_cash",
    } <= reasons
    assert len(PERMISSION_STEPS) == len(set(PERMISSION_STEPS))


def _adjacent_pairs() -> list[tuple[Condition, Condition]]:
    firsts: dict[str, Condition] = {}
    order: list[str] = []
    for condition in CONDITIONS:
        if condition.group not in firsts:
            firsts[condition.group] = condition
            order.append(condition.group)
    pairs = [(firsts[a], firsts[b]) for a, b in itertools.pairwise(order)]
    # Also the within-step neighbours that matter: unknown calendar vs a later pause, and the
    # not-yet-open pause versus provenance.
    pairs.append((_BY_NAME["window_not_yet_open"], _BY_NAME["evaluation_data_changed"]))
    pairs.append((_BY_NAME["calendar_unknown"], _BY_NAME["not_owner"]))
    return [
        pair for pair in pairs if frozenset({pair[0].name, pair[1].name}) not in _EXCLUSIVE
    ]


@pytest.mark.parametrize(
    "earlier,later", _adjacent_pairs(), ids=lambda c: c.name if isinstance(c, Condition) else str(c)
)
def test_permission_precedence_table(
    db: Session, monkeypatch: pytest.MonkeyPatch, earlier: Condition, later: Condition
) -> None:
    """Every adjacent pair of steps: with both conditions true the EARLIER step decides."""

    risk_run = seed_risk_run(db)
    world = World()
    # Arm the later condition first so a condition that changes the clock (the window
    # group) is the last writer of ``world.now`` exactly as in the solo case.
    _arm(db, world, later.name, earlier.name)

    outcome, _source = _run_check(db, world, risk_run, monkeypatch)

    _assert_outcome(outcome, earlier)


def test_price_moved_loses_to_a_failing_risk_only_after_the_price_step(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Price (step 4) precedes risk (step 5): +6% with a cash shortfall pauses, not re-evaluates."""

    risk_run = seed_risk_run(db)
    world = World()
    _arm(db, world, "price_moved", "risk_limit_failed")

    outcome, _source = _run_check(db, world, risk_run, monkeypatch)

    _assert_outcome(outcome, _BY_NAME["price_moved"])


def test_working_orders_of_a_terminated_operation_still_block(db: Session) -> None:
    from tests.support.operation_fixtures import seed_operation, seed_operation_intent

    operation = seed_operation(db, state="terminated", reason="cancelled_by_operator")
    seed_operation_intent(
        db,
        operation,
        order_status=OrderLifecycleState.SUBMITTED,
        attempts=(AttemptOutcomeClass.ACCEPTED,),
        broker_order_id="broker-from-terminated-op",
        broker_status="new",
    )

    working = strategy_working_orders(db, OWNER)

    assert [order.symbol for order in working] == ["AAPL"]
    assert working[0].status == "submitted"


def test_strategy_working_orders_is_scoped_to_the_strategy_and_ignores_synced_terminals(
    db: Session,
) -> None:
    run = seed_paper_run(db, None)
    filled_synced = seed_intent(
        db,
        run,
        status=OrderLifecycleState.FILLED,
        attempts=(AttemptOutcomeClass.ACCEPTED,),
        broker_order_id="b-synced",
    )
    filled_synced.last_synced_at = at(3)
    seed_intent(
        db,
        run,
        status=OrderLifecycleState.FILLED,
        attempts=(AttemptOutcomeClass.ACCEPTED,),
        broker_order_id="b-unsynced",
        ticker="MSFT",
    )
    other_run = seed_paper_run(db, None, OTHER)
    seed_intent(
        db,
        other_run,
        status=OrderLifecycleState.SUBMITTED,
        attempts=(AttemptOutcomeClass.ACCEPTED,),
        broker_order_id="b-other",
        ticker="NVDA",
    )
    db.flush()

    mine = strategy_working_orders(db, OWNER)
    theirs = strategy_working_orders(db, OTHER)

    assert [order.symbol for order in mine] == ["MSFT"]  # filled but fills never synced
    assert [order.symbol for order in theirs] == ["NVDA"]


# ---------------------------------------------------------------------------
# S2-R3 price cases
# ---------------------------------------------------------------------------


def test_fresh_plus_two_percent_with_enough_cash_is_ok_and_unchanged(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    risk_run = seed_risk_run(db)
    world = World()
    world.price = _fresh(world, "102")

    outcome, _source = _run_check(db, world, risk_run, monkeypatch)

    assert outcome.ok
    assert outcome.revalidation is not None
    assert outcome.revalidation.valuation_price == Decimal("102")
    assert outcome.revalidation.notional == Decimal("1020.000000")


def test_a_symbol_the_account_does_not_hold_needs_only_a_fresh_trade(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    risk_run = seed_risk_run(db)
    world = World()
    world.price = observation(
        "ZZZZ", "100", observed_at=world.now - timedelta(seconds=3), fetched_at=world.now
    )

    outcome, _source = _run_check(db, world, risk_run, monkeypatch)

    assert outcome.ok


def test_plus_six_percent_pauses_beyond_tolerance_and_the_same_intent_passes_when_back(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    risk_run = seed_risk_run(db)
    world = World()
    world.price = _fresh(world, "106")

    first, _ = _run_check(db, world, risk_run, monkeypatch)
    world.price = _fresh(world, "102")
    second, _ = _run_check(db, world, risk_run, monkeypatch)

    assert first.verdict is PermissionVerdict.PAUSE
    assert first.reason == PausedReason.PRICE_MOVED_BEYOND_TOLERANCE.value
    assert first.detail is not None and first.detail.startswith("deviation_0.06")
    assert first.observation is not None and first.observation.price == Decimal("106")
    assert second.ok  # PD-1: the same pinned intent may be sent once the price is back


def test_minus_six_percent_also_pauses(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    risk_run = seed_risk_run(db)
    world = World()
    world.price = _fresh(world, "94")

    outcome, _ = _run_check(db, world, risk_run, monkeypatch)

    assert outcome.reason == PausedReason.PRICE_MOVED_BEYOND_TOLERANCE.value


@pytest.mark.parametrize(
    "script,detail",
    [
        (lambda world: _fresh(world, "100", age=121), "price_stale"),
        (
            lambda world: observation(
                "AAPL",
                "100",
                observed_at=et(2025, 12, 2, 15, 59),  # the previous day's trade
                fetched_at=world.now,
            ),
            "no_trade_today",
        ),
        (lambda world: PriceFailure.NO_TRADE_TODAY, "no_trade_today"),
        (lambda world: PriceFailure.FEED_NOT_AUTHORIZED, "feed_not_authorized"),
        (lambda world: PriceFailure.PRICE_LOOKUP_FAILED, "price_lookup_failed"),
        (lambda world: PriceFailure.PRICE_INVALID, "price_invalid"),
        (lambda world: _fresh(world, "0"), "price_invalid"),
        (lambda world: RuntimeError("boom"), "price_lookup_failed"),
    ],
    ids=[
        "stale",
        "previous_day",
        "404",
        "403",
        "lookup_failed",
        "invalid",
        "zero_price",
        "unexpected_error",
    ],
)
def test_unusable_observations_pause_price_unavailable_with_the_matching_detail(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    script: Callable[[World], Any],
    detail: str,
) -> None:
    risk_run = seed_risk_run(db)
    world = World()
    world.price = script(world)

    outcome, _ = _run_check(db, world, risk_run, monkeypatch)

    assert outcome.verdict is PermissionVerdict.PAUSE
    assert outcome.reason == PausedReason.PRICE_UNAVAILABLE.value
    assert outcome.detail == detail


def test_cash_shortfall_at_the_fresh_price_requires_reevaluation(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    risk_run = seed_risk_run(db)
    world = World()
    # 1010 cash covers 10 x 100 at the evaluation price but not 10 x 102 at the fresh price.
    db.add(
        AccountSnapshot(
            snapshot_source="broker_sync",
            snapshot_at=at(0),
            cash=Decimal("1010"),
            gross_exposure=Decimal("0"),
            total_equity=Decimal("1010"),
            buying_power=Decimal("1010"),
            open_positions=0,
        )
    )
    db.flush()
    world.price = _fresh(world, "102")

    outcome, _ = _run_check(db, world, risk_run, monkeypatch)

    assert outcome.verdict is PermissionVerdict.REEVALUATE
    assert outcome.reason == "risk_limit_failed:insufficient_cash"
    assert outcome.revalidation is not None and outcome.revalidation.failed


def test_tolerance_and_age_settings_take_effect(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    risk_run = seed_risk_run(db)
    base = load_settings()
    loose = base.model_copy(
        update={
            "execution": base.execution.model_copy(
                update={"pre_send_max_price_deviation": 0.10, "pre_send_price_max_age_seconds": 600}
            )
        }
    )
    world = World(settings=loose)
    world.price = _fresh(world, "106", age=300)

    outcome, _ = _run_check(db, world, risk_run, monkeypatch)

    assert outcome.ok


def test_the_check_is_read_only_and_never_changes_the_pinned_identity(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    risk_run = seed_risk_run(db)
    world = World()
    intent = PinnedIntent("AAPL", "buy", Decimal("10"), REFERENCE, client_order_id="tp-keep")
    monkeypatch.setattr(clock, "now_utc", lambda: world.now)
    monkeypatch.setattr(
        permission_module,
        "verify_risk_run_manifest",
        lambda **kwargs: ManifestVerification(ManifestVerificationStatus.MATCHES),
    )
    snapshot = (set(db.new), set(db.dirty), set(db.deleted))
    check_intent_permission(
        db,
        strategy_id=OWNER,
        as_of_session=S,
        risk_run_id=risk_run.id,
        intent=intent,
        price_source=ScriptedPriceSource([_fresh(world, "103")]),
        settings=world.settings,
        now=world.now,
    )

    assert (set(db.new), set(db.dirty), set(db.deleted)) == snapshot
    assert (intent.client_order_id, intent.quantity, intent.side) == ("tp-keep", Decimal("10"), "buy")
