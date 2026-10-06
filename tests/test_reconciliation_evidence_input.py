"""The shared submission verdict as a reconciliation INPUT (G-1 input half, 20.1-32).

Reconciliation must decide "does this unmatched pre-send local order's absence at the broker need
explaining?" with the SAME shared verdict the recovery gate uses
(``attempts.classify_submission_evidence``), over the order's COMPLETE attempt history, not with
``submission_attempt_count`` and not with the absence of attempt rows. This module pins:

* the batch loader ``attempts.load_submission_evidence`` (two statements for any number of orders,
  parity with ``intent_identity.load_strategy_order_facts`` on every shape of the shared matrix);
* the module-level ``SHAPES`` table, which 20.1-35 imports (do not rename a shape id);
* (later in this module) the closed ``LocalSubmissionEvidence`` mirror enum, the per-scope
  projection into ``LocalOrderSnapshot``, the fixed statement budget and the purity of the pure
  reconciliation modules;
* (later in this module) the real-reconciliation test helper.

Fixtures are direct ORM INSERTs (migration 0029 makes orders, attempts, runs and the protected
Jobs undeletable); every test owns a throwaway migrated database.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from tests.support import real_reconciliation
from tests.support.basis_fixtures import seed_fresh_broker_snapshot
from tests.support.migrated_db import migrated_database
from tests.support.operation_fixtures import seed_operation
from tests.support.query_counter import count_queries
from tests.support.real_reconciliation import (
    WallClockTimeline,
    reconciliation_completed_at,
    run_real_account_reconciliation,
    run_real_strategy_reconciliation,
    scripted_read_broker,
)
from tests.support.recovery_fixtures import (
    OWNER,
    SESSION_DATE,
    seed_intent,
    seed_operation_bound_intent,
    seed_paper_run,
    strategy_row,
)
from tests.test_paper_execution import FakeBrokerClient

from trading_platform.core.settings import load_settings
from trading_platform.db.models import (
    AccountReconciliationRun,
    AttemptOutcomeClass,
    ExecutionOperationIntent,
    Job,
    JobStatus,
    OrderLifecycleState,
    PaperOrder,
    StrategyRun,
)
from trading_platform.db.session import session_scope
from trading_platform.services.alpaca import BrokerAccountSnapshot
from trading_platform.services.execution import OrderSide
from trading_platform.services.execution.attempts import (
    SubmissionEvidence,
    load_submission_evidence,
)
from trading_platform.services.execution.intent_identity import load_strategy_order_facts
from trading_platform.services.reconciliation import account as account_module
from trading_platform.services.reconciliation import (
    latest_standalone_reconciliation,
    reconcile_account,
    reconcile_paper_execution,
)
from trading_platform.services.reconciliation import report as report_module
from trading_platform.services.reconciliation.findings import ReconciliationFinding
from trading_platform.services.reconciliation.report import BrokerStateSnapshot
from trading_platform.services.reconciliation.snapshot import (
    LocalOrderSnapshot,
    LocalSubmissionEvidence,
)
from trading_platform.strategies.registry import build_default_registry

PENDING = OrderLifecycleState.PENDING_SUBMISSION
FAILED = OrderLifecycleState.SUBMISSION_FAILED

PRE_CONNECTION = AttemptOutcomeClass.PRE_CONNECTION
DEADLINE = AttemptOutcomeClass.DEADLINE_EXPIRED
AMBIGUOUS = AttemptOutcomeClass.AMBIGUOUS
DUPLICATE_REPORTED = AttemptOutcomeClass.DUPLICATE_REPORTED
REJECTED = AttemptOutcomeClass.REJECTED
ACCEPTED = AttemptOutcomeClass.ACCEPTED

BROKER_EVIDENCE = SubmissionEvidence.BROKER_EVIDENCE
REJECTED_VERDICT = SubmissionEvidence.REJECTED
PROVEN_NOT_SENT = SubmissionEvidence.PROVEN_NOT_SENT
UNESTABLISHED = SubmissionEvidence.UNESTABLISHED


@pytest.fixture()
def recon_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    with migrated_database(monkeypatch, "recon_evidence_input") as name:
        yield name


# ---------------------------------------------------------------------------
# The shared SHAPES table (imported by 20.1-35: do not rename a shape id)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ShapeSpec:
    """One pre-send order shape and the shared verdict it must classify to.

    ``build(session, run)`` INSERTs the order (and everything it needs) and returns it. Give EVERY
    shape its OWN run: ``risk_events`` is unique per (run, symbol, session, direction) and every
    shape uses the same symbol. Every shape is an order in ``pending_submission`` or
    ``submission_failed``.

    ``submission_attempt_count`` is chosen deliberately: it is 1 for every shape (``register()``
    raises it before T1, so a registered order never has 0) except the two ``*_count0*`` shapes
    (0: the pre-register legacy state the current matcher treats as inactive). The count is not
    part of the shared verdict; the matcher ignores it for ``submission_failed``.
    """

    build: Callable[[Session, StrategyRun], PaperOrder]
    expected: SubmissionEvidence


def _with_count(session: Session, order: PaperOrder, count: int) -> PaperOrder:
    # An UPDATE of a non-origin paper_orders column is legal (0029 guards only strategy_run_id).
    order.submission_attempt_count = count
    session.flush()
    return order


def _legacy(
    status: OrderLifecycleState,
    attempts: tuple[AttemptOutcomeClass | None, ...] = (),
    *,
    count: int = 1,
    with_broker_id: bool = False,
) -> Callable[[Session, StrategyRun], PaperOrder]:
    """A LEGACY order: no operation intent row references it (it pre-dates the attempt log)."""

    def build(session: Session, run: StrategyRun) -> PaperOrder:
        order = seed_intent(
            session,
            run,
            status=status,
            attempts=attempts,
            broker_order_id=f"b-{uuid.uuid4().hex[:12]}" if with_broker_id else None,
        )
        return _with_count(session, order, count)

    return build


def _operation_bound(
    status: OrderLifecycleState, *, count: int = 1
) -> Callable[[Session, StrategyRun], PaperOrder]:
    """An ATTEMPT-LOG-REGISTERED order with zero attempt rows: its pinned intent row pre-dates it."""

    def build(session: Session, run: StrategyRun) -> PaperOrder:
        order = seed_operation_bound_intent(session, run, status=status, attempts=())
        return _with_count(session, order, count)

    return build


def _reference_in_later_operation(session: Session, order: PaperOrder) -> None:
    """A LATER operation's intent row references ``order``.

    ``created_at`` is explicit and strictly AFTER the order's (``now()`` is the transaction start,
    so a server default would equal the order's and read as "registered")."""

    operation = seed_operation(session, state="terminated", reason="cancelled_by_operator")
    session.add(
        ExecutionOperationIntent(
            operation_id=operation.id,
            sequence=1,
            symbol_id=order.symbol_id,
            side=order.side,
            quantity=order.quantity,
            reference_price=Decimal("100"),
            client_order_id=order.client_order_id,
            paper_order_id=order.id,
            decision_fingerprint=uuid.uuid4().hex + uuid.uuid4().hex,
            prior_execution_refs=[],
            disposition="open",
            created_at=order.created_at + timedelta(minutes=1),
        )
    )
    session.flush()


def _operation_bound_also_referenced_later(session: Session, run: StrategyRun) -> PaperOrder:
    """Operation-bound, and ALSO referenced by a later operation: the MIN over ALL intent rows
    (the first one, which pre-dates the order) decides, so it stays registered."""

    order = _operation_bound(PENDING)(session, run)
    _reference_in_later_operation(session, order)
    return order


def _legacy_referenced_later(session: Session, run: StrategyRun) -> PaperOrder:
    """A LEGACY order (created before ANY intent row) that only a LATER operation references:
    its earliest intent row is AFTER the order, so it is not attempt-log-registered."""

    order = _legacy(PENDING)(session, run)
    _reference_in_later_operation(session, order)
    return order


SHAPES: dict[str, ShapeSpec] = {
    # --- PROVEN_NOT_SENT ---------------------------------------------------------------------
    # The G-1 shape: operation-bound, zero attempt rows, count 1 (register() raised it before T1).
    "op_bound_pending_count1_no_attempts": ShapeSpec(_operation_bound(PENDING), PROVEN_NOT_SENT),
    "op_bound_pending_count0_no_attempts": ShapeSpec(
        _operation_bound(PENDING, count=0), PROVEN_NOT_SENT
    ),
    "op_bound_failed_no_attempts": ShapeSpec(_operation_bound(FAILED), PROVEN_NOT_SENT),
    "pending_pre_connection": ShapeSpec(_legacy(PENDING, (PRE_CONNECTION,)), PROVEN_NOT_SENT),
    "failed_pre_connection_deadline_expired": ShapeSpec(
        _legacy(FAILED, (PRE_CONNECTION, DEADLINE)), PROVEN_NOT_SENT
    ),
    "op_bound_order_also_referenced_later": ShapeSpec(
        _operation_bound_also_referenced_later, PROVEN_NOT_SENT
    ),
    # --- REJECTED (a definitive, recorded 4xx; no ambiguity anywhere in the history) ----------
    "pending_rejected": ShapeSpec(_legacy(PENDING, (REJECTED,)), REJECTED_VERDICT),
    "failed_pre_connection_then_rejected": ShapeSpec(
        _legacy(FAILED, (PRE_CONNECTION, REJECTED)), REJECTED_VERDICT
    ),
    "pending_rejected_then_deadline_expired": ShapeSpec(
        _legacy(PENDING, (REJECTED, DEADLINE)), REJECTED_VERDICT
    ),
    # --- UNESTABLISHED -----------------------------------------------------------------------
    "legacy_pending_count0": ShapeSpec(_legacy(PENDING, count=0), UNESTABLISHED),
    "legacy_pending_count1": ShapeSpec(_legacy(PENDING), UNESTABLISHED),
    "legacy_failed": ShapeSpec(_legacy(FAILED), UNESTABLISHED),
    "legacy_order_later_referenced": ShapeSpec(_legacy_referenced_later, UNESTABLISHED),
    "pending_unfinished_null": ShapeSpec(_legacy(PENDING, (None,)), UNESTABLISHED),
    "failed_unfinished_null": ShapeSpec(_legacy(FAILED, (None,)), UNESTABLISHED),
    "pending_ambiguous": ShapeSpec(_legacy(PENDING, (AMBIGUOUS,)), UNESTABLISHED),
    "failed_ambiguous_then_pre_connection": ShapeSpec(
        _legacy(FAILED, (AMBIGUOUS, PRE_CONNECTION)), UNESTABLISHED
    ),
    "pending_pre_connection_then_null": ShapeSpec(
        _legacy(PENDING, (PRE_CONNECTION, None)), UNESTABLISHED
    ),
    "pending_accepted_without_broker_id": ShapeSpec(_legacy(PENDING, (ACCEPTED,)), UNESTABLISHED),
    # Ambiguity dominance: an unfinished, ambiguous, duplicate_reported or accepted attempt before
    # or after a rejection makes the whole history UNESTABLISHED.
    "failed_ambiguous_then_rejected": ShapeSpec(
        _legacy(FAILED, (AMBIGUOUS, REJECTED)), UNESTABLISHED
    ),
    "pending_unfinished_then_rejected": ShapeSpec(
        _legacy(PENDING, (None, REJECTED)), UNESTABLISHED
    ),
    "pending_rejected_then_unfinished": ShapeSpec(
        _legacy(PENDING, (REJECTED, None)), UNESTABLISHED
    ),
    "failed_duplicate_reported_then_rejected": ShapeSpec(
        _legacy(FAILED, (DUPLICATE_REPORTED, REJECTED)), UNESTABLISHED
    ),
    # --- BROKER_EVIDENCE ---------------------------------------------------------------------
    "failed_with_broker_id": ShapeSpec(_legacy(FAILED, with_broker_id=True), BROKER_EVIDENCE),
}

_EXPECTED_IDS_BY_VERDICT: dict[SubmissionEvidence, frozenset[str]] = {
    PROVEN_NOT_SENT: frozenset(
        {
            "op_bound_pending_count1_no_attempts",
            "op_bound_pending_count0_no_attempts",
            "op_bound_failed_no_attempts",
            "pending_pre_connection",
            "failed_pre_connection_deadline_expired",
            "op_bound_order_also_referenced_later",
        }
    ),
    REJECTED_VERDICT: frozenset(
        {
            "pending_rejected",
            "failed_pre_connection_then_rejected",
            "pending_rejected_then_deadline_expired",
        }
    ),
    UNESTABLISHED: frozenset(
        {
            "legacy_pending_count0",
            "legacy_pending_count1",
            "legacy_failed",
            "legacy_order_later_referenced",
            "pending_unfinished_null",
            "failed_unfinished_null",
            "pending_ambiguous",
            "failed_ambiguous_then_pre_connection",
            "pending_pre_connection_then_null",
            "pending_accepted_without_broker_id",
            "failed_ambiguous_then_rejected",
            "pending_unfinished_then_rejected",
            "pending_rejected_then_unfinished",
            "failed_duplicate_reported_then_rejected",
        }
    ),
    BROKER_EVIDENCE: frozenset({"failed_with_broker_id"}),
}


def _build_shape(shape_id: str) -> uuid.UUID:
    """INSERT one shape on its own run in its own transaction; return the order id."""

    with session_scope(load_settings()) as session:
        order = SHAPES[shape_id].build(session, seed_paper_run(session, None))
        return order.id


def _build_batch(count: int) -> list[tuple[str, uuid.UUID]]:
    """``count`` orders cycling through the shapes (each on its own run), in one transaction."""

    shape_ids = sorted(SHAPES)
    built: list[tuple[str, uuid.UUID]] = []
    with session_scope(load_settings()) as session:
        for index in range(count):
            shape_id = shape_ids[index % len(shape_ids)]
            order = SHAPES[shape_id].build(session, seed_paper_run(session, None))
            built.append((shape_id, order.id))
    return built


def test_shapes_table_is_the_agreed_24_shape_set() -> None:
    assert len(SHAPES) == 24
    by_verdict: dict[SubmissionEvidence, set[str]] = {}
    for shape_id, spec in SHAPES.items():
        by_verdict.setdefault(spec.expected, set()).add(shape_id)
    assert {verdict: frozenset(ids) for verdict, ids in by_verdict.items()} == (
        _EXPECTED_IDS_BY_VERDICT
    )
    assert {verdict: len(ids) for verdict, ids in _EXPECTED_IDS_BY_VERDICT.items()} == {
        PROVEN_NOT_SENT: 6,
        REJECTED_VERDICT: 3,
        UNESTABLISHED: 14,
        BROKER_EVIDENCE: 1,
    }


# ---------------------------------------------------------------------------
# The batch loader
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("shape_id", sorted(SHAPES))
def test_loader_verdict_matrix(recon_db: str, shape_id: str) -> None:
    order_id = _build_shape(shape_id)
    with session_scope(load_settings()) as session:
        order = session.get(PaperOrder, order_id)
        assert order is not None
        assert order.status in (PENDING, FAILED)
        verdicts = load_submission_evidence(session, [order])
    assert verdicts == {order_id: SHAPES[shape_id].expected}


def test_loader_matches_the_recovery_verdict(recon_db: str) -> None:
    """Parity oracle: the verdict the recovery gate reads for the same order, every shape."""

    built = {shape_id: _build_shape(shape_id) for shape_id in sorted(SHAPES)}
    with session_scope(load_settings()) as session:
        facts = {fact.paper_order_id: fact for fact in load_strategy_order_facts(session, OWNER)}
        orders = list(session.execute(select(PaperOrder)).scalars().all())
        assert {order.id for order in orders} == set(built.values())
        verdicts = load_submission_evidence(session, orders)
    assert set(verdicts) == set(built.values())
    for shape_id, order_id in built.items():
        assert verdicts[order_id] is facts[order_id].submission_evidence, shape_id
        assert verdicts[order_id] is SHAPES[shape_id].expected, shape_id


@pytest.mark.parametrize(("order_count", "statements"), [(0, 0), (1, 2), (50, 2)])
def test_loader_statement_budget(recon_db: str, order_count: int, statements: int) -> None:
    """No orders: no statement. Any number of orders: exactly two (attempts, min intent)."""

    built = _build_batch(order_count)
    with session_scope(load_settings()) as session:
        loaded = {order.id: order for order in session.execute(select(PaperOrder)).scalars()}
        orders = [loaded[order_id] for _shape_id, order_id in built]
        with count_queries(session) as counter:
            verdicts = load_submission_evidence(session, orders)
    assert counter.count == statements
    assert len(verdicts) == order_count
    for shape_id, order_id in built:
        assert verdicts[order_id] is SHAPES[shape_id].expected, shape_id


# ---------------------------------------------------------------------------
# The verdict crosses the reconciliation boundary (no rule change; 20.1-32, task 2)
# ---------------------------------------------------------------------------

#: Orders that are NOT pre-send. The shared classifier WOULD give most of them a verdict (a REJECTED
#: order, an UNKNOWN order with an ambiguous attempt, orders carrying a broker id), but the matcher
#: never consults the verdict for these statuses, so they must carry NOT_COMPUTED (not vacuous).
_NON_PRE_SEND_ORDERS: tuple[tuple[str, OrderLifecycleState, AttemptOutcomeClass, bool], ...] = (
    ("unknown_ambiguous", OrderLifecycleState.UNKNOWN, AMBIGUOUS, False),
    ("rejected_status", OrderLifecycleState.REJECTED, REJECTED, False),
    ("submitted_with_broker_id", OrderLifecycleState.SUBMITTED, ACCEPTED, True),
    ("filled_with_broker_id", OrderLifecycleState.FILLED, ACCEPTED, True),
)


def _flat_broker() -> FakeBrokerClient:
    """The read-side broker of a reconciliation: no orders, fills or positions and a flat account
    (no snapshot and no positions is the non-blocking B2 case). Nothing is ever POSTed through it."""

    return FakeBrokerClient(
        orders=[],
        fills=[],
        positions=[],
        account=BrokerAccountSnapshot(
            cash=Decimal("100000.000000"),
            buying_power=Decimal("100000.000000"),
            equity=Decimal("100000.000000"),
            long_market_value=Decimal("0"),
            short_market_value=Decimal("0"),
            raw_payload={"equity": "100000.000000"},
        ),
    )


def _flat_state() -> BrokerStateSnapshot:
    return BrokerStateSnapshot(
        orders=(), fills=(), positions=(), account=_flat_broker().get_account()
    )


def _seed_world() -> dict[str, uuid.UUID]:
    """Every shape (own run each) plus the non-pre-send orders, in one transaction."""

    ids: dict[str, uuid.UUID] = {}
    with session_scope(load_settings()) as session:
        for shape_id in sorted(SHAPES):
            ids[shape_id] = SHAPES[shape_id].build(session, seed_paper_run(session, None)).id
        for name, status, outcome, with_broker_id in _NON_PRE_SEND_ORDERS:
            order = seed_intent(
                session,
                seed_paper_run(session, None),
                status=status,
                attempts=(outcome,),
                broker_order_id=f"b-{uuid.uuid4().hex[:12]}" if with_broker_id else None,
            )
            ids[name] = order.id
    return ids


def _spy_match_snapshots(
    monkeypatch: pytest.MonkeyPatch, module: object
) -> list[list[LocalOrderSnapshot]]:
    """Capture the local order snapshots the module hands to the pure matcher (still matches)."""

    captured: list[list[LocalOrderSnapshot]] = []
    real = module.match_snapshots  # type: ignore[attr-defined]

    def spy(*, local_orders: list[LocalOrderSnapshot], **kwargs: object):  # noqa: ANN202
        captured.append(list(local_orders))
        return real(local_orders=local_orders, **kwargs)

    monkeypatch.setattr(module, "match_snapshots", spy)
    return captured


def _assert_projection(ids: dict[str, uuid.UUID], snapshots: list[LocalOrderSnapshot]) -> None:
    by_order = {snapshot.paper_order_id: snapshot for snapshot in snapshots}
    assert set(by_order) == {str(order_id) for order_id in ids.values()}
    with session_scope(load_settings()) as session:
        pre_send = [
            order
            for order in session.execute(select(PaperOrder)).scalars()
            if order.status in (PENDING, FAILED)
        ]
        loaded = load_submission_evidence(session, pre_send)
    assert len(loaded) == len(SHAPES)
    for name, order_id in ids.items():
        snapshot = by_order[str(order_id)]
        if name in SHAPES:
            assert snapshot.status in {PENDING.value, FAILED.value}, name
            assert snapshot.submission_evidence.value == loaded[order_id].value, name
            assert snapshot.submission_evidence.value == SHAPES[name].expected.value, name
        else:
            assert snapshot.status not in {PENDING.value, FAILED.value}, name
            assert snapshot.submission_evidence is LocalSubmissionEvidence.NOT_COMPUTED, name


def test_local_submission_evidence_is_the_closed_shared_set() -> None:
    """Exact equality with the shared enum plus NOT_COMPUTED. The test may import ``attempts``
    (it imports the ORM); the pure reconciliation modules may not."""

    assert {member.value for member in LocalSubmissionEvidence} == (
        {member.value for member in SubmissionEvidence} | {"not_computed"}
    )
    assert {member.name for member in LocalSubmissionEvidence} == (
        {member.name for member in SubmissionEvidence} | {"NOT_COMPUTED"}
    )
    assert LocalSubmissionEvidence("not_computed") is LocalSubmissionEvidence.NOT_COMPUTED


def test_snapshot_defaults_to_not_computed_and_the_field_is_last() -> None:
    """Fail closed: a snapshot whose verdict was not loaded is never treated as explained, and
    every existing keyword constructor stays valid."""

    snapshot = LocalOrderSnapshot(
        paper_order_id="o-1",
        strategy_run_id="r-1",
        symbol="AAPL",
        side=OrderSide.BUY,
        quantity=Decimal("1"),
        client_order_id="c-1",
        broker_order_id=None,
        status="pending_submission",
        broker_status=None,
        submission_attempt_count=1,
        sync_failure_count=0,
    )
    assert snapshot.submission_evidence is LocalSubmissionEvidence.NOT_COMPUTED
    assert dataclasses.fields(LocalOrderSnapshot)[-1].name == "submission_evidence"


def test_project_local_order_requires_the_verdict_keyword() -> None:
    """No caller can forget the verdict: it is a REQUIRED keyword-only parameter."""

    parameter = inspect.signature(report_module._project_local_order).parameters[
        "submission_evidence"
    ]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is inspect.Parameter.empty
    assert parameter.annotation in (LocalSubmissionEvidence, "LocalSubmissionEvidence")


def test_projection_carries_the_verdict_strategy_scope(
    recon_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = _seed_world()
    captured = _spy_match_snapshots(monkeypatch, report_module)
    reconcile_paper_execution(
        OWNER,
        as_of_session=SESSION_DATE,
        settings=load_settings(),
        broker_client=_flat_broker(),
        trigger_source="job",
    )
    (snapshots,) = captured
    _assert_projection(ids, snapshots)


def test_projection_carries_the_verdict_account_scope(
    recon_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = _seed_world()
    captured = _spy_match_snapshots(monkeypatch, account_module)
    reconcile_account(settings=load_settings(), broker_client=_flat_broker())
    (snapshots,) = captured
    _assert_projection(ids, snapshots)


def test_projection_carries_the_verdict_in_session_trigger(
    recon_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``run_paper_session`` calls ``reconcile_paper_execution`` with ``<trigger>_reconciliation``:
    the same strategy-scope function, so the same verdicts."""

    ids = _seed_world()
    captured = _spy_match_snapshots(monkeypatch, report_module)
    reconcile_paper_execution(
        OWNER,
        as_of_session=SESSION_DATE,
        settings=load_settings(),
        broker_client=_flat_broker(),
        trigger_source="pytest_reconciliation",
    )
    (snapshots,) = captured
    _assert_projection(ids, snapshots)


def _seed_pending_orders(count: int) -> None:
    """``count`` pending_submission orders of ONE symbol (AAPL), each on its own run. The default
    submission_attempt_count 0 is inactive in the current matcher: no finding, so no ExecutionEvent
    insert can add statements to the measured call."""

    with session_scope(load_settings()) as session:
        for _ in range(count):
            seed_intent(session, seed_paper_run(session, None), status=PENDING, attempts=())


def _scope_statements(scope: str, monkeypatch: pytest.MonkeyPatch, *, without_loader: bool) -> int:
    """Statements of ONE ``_reconcile_against_broker_state`` / ``_evaluate_account`` call, with the
    evidence loader real or replaced by ``{}`` (the pre-20.1-32 statement shape)."""

    settings = load_settings()
    state = _flat_state()
    metadata = build_default_registry(settings).resolve(OWNER).metadata
    prefix = settings.execution.client_order_id_prefix
    threshold = settings.execution.safety.repeated_failure_threshold
    module = report_module if scope == "strategy" else account_module
    with monkeypatch.context() as patched:
        if without_loader:
            patched.setattr(module, "_load_reconciliation_evidence", lambda _session, _orders: {})
        with session_scope(settings) as session:
            with count_queries(session) as counter:
                if scope == "strategy":
                    report_module._reconcile_against_broker_state(
                        session,
                        metadata,
                        state,
                        run_id=uuid.uuid4(),
                        checked_at=datetime.now(UTC),
                        platform_prefix=prefix,
                        failure_threshold=threshold,
                    )
                else:
                    account_module._evaluate_account(
                        session, state, platform_prefix=prefix, failure_threshold=threshold
                    )
    return counter.count


@pytest.mark.parametrize("scope", ["strategy", "account"])
def test_reconciliation_statement_budget_is_independent_of_order_count(
    recon_db: str, monkeypatch: pytest.MonkeyPatch, scope: str
) -> None:
    """The evidence is batch-loaded: +2 statements when any pre-send order exists, +0 when none,
    and the same +2 for 1 and for 50 candidate orders (no per-order query)."""

    with session_scope(load_settings()) as session:
        # Not pre-send: it only makes the strategy exist, it is no candidate for the loader.
        seed_intent(session, seed_paper_run(session, None), status=OrderLifecycleState.UNKNOWN)
    assert _scope_statements(scope, monkeypatch, without_loader=False) == _scope_statements(
        scope, monkeypatch, without_loader=True
    )

    _seed_pending_orders(1)
    one_real = _scope_statements(scope, monkeypatch, without_loader=False)
    one_patched = _scope_statements(scope, monkeypatch, without_loader=True)
    assert one_real == one_patched + 2

    _seed_pending_orders(49)
    fifty_real = _scope_statements(scope, monkeypatch, without_loader=False)
    fifty_patched = _scope_statements(scope, monkeypatch, without_loader=True)
    assert fifty_real == fifty_patched + 2
    assert fifty_real == one_real
    assert fifty_patched == one_patched


def test_findings_unchanged_by_the_input_plan(recon_db: str) -> None:
    """CURRENT behaviour, pinned so this input-only plan is provably behaviour-neutral: the matcher
    does not read the verdict yet. 20.1-34 changes the matcher rule and REVERSES the two
    MISSING_BROKER expectations below (the G-1 shape and the recorded-rejection shape)."""

    ids = {
        shape_id: _build_shape(shape_id)
        for shape_id in (
            "op_bound_pending_count1_no_attempts",
            "pending_rejected",
            "legacy_failed",
            "legacy_pending_count0",
        )
    }
    strategy_report = reconcile_paper_execution(
        OWNER,
        as_of_session=SESSION_DATE,
        settings=load_settings(),
        broker_client=_flat_broker(),
        trigger_source="job",
    )
    account_report = reconcile_account(settings=load_settings(), broker_client=_flat_broker())

    missing_broker = ReconciliationFinding.MISSING_BROKER.name
    expected = {
        (missing_broker, str(ids["op_bound_pending_count1_no_attempts"])),
        (missing_broker, str(ids["pending_rejected"])),
    }
    assert {(f.event_type, f.paper_order_id) for f in strategy_report.findings} == expected
    assert {(f["event_type"], f["paper_order_id"]) for f in account_report.findings} == expected
    assert strategy_report.blocks_execution is True
    assert account_report.blocks_execution is True


# --- purity of the pure reconciliation modules (AST walk) ----------------------------------

_RECONCILIATION_DIR = (
    Path(__file__).resolve().parents[1] / "src" / "trading_platform" / "services" / "reconciliation"
)
_FORBIDDEN_IMPORT_PREFIXES = (
    "sqlalchemy",
    "trading_platform.db",
    "trading_platform.services.alpaca",
    "trading_platform.services.execution.attempts",
)


def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def _runtime_imports(node: ast.AST) -> Iterator[ast.Import | ast.ImportFrom]:
    """Imports that run when the module is imported: everything except the body of an
    ``if TYPE_CHECKING:`` block (its ``else`` branch does run)."""

    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.If) and _is_type_checking(child.test):
            for statement in child.orelse:
                if isinstance(statement, (ast.Import, ast.ImportFrom)):
                    yield statement
                yield from _runtime_imports(statement)
            continue
        if isinstance(child, (ast.Import, ast.ImportFrom)):
            yield child
        yield from _runtime_imports(child)


def _forbidden_runtime_imports(source: str) -> list[str]:
    violations: list[str] = []
    for node in _runtime_imports(ast.parse(source)):
        if isinstance(node, ast.Import):
            targets = [alias.name for alias in node.names]
        else:
            module = node.module or ""
            targets = [module]
            targets += [f"{module}.{alias.name}" for alias in node.names]
            if any(alias.name == "session_scope" for alias in node.names):
                targets.append("session_scope")
        for target in targets:
            if target == "session_scope" or target.startswith(_FORBIDDEN_IMPORT_PREFIXES):
                violations.append(target)
    return violations


def test_matcher_and_snapshot_stay_pure() -> None:
    for name in ("matcher.py", "snapshot.py"):
        assert _forbidden_runtime_imports((_RECONCILIATION_DIR / name).read_text()) == [], name


def test_purity_walk_sees_runtime_imports_and_skips_type_checking_only_ones() -> None:
    """The walk is not vacuous: it flags runtime imports (also in an ``else`` branch), ignores the
    ``if TYPE_CHECKING`` body, and really sees the real modules' runtime imports."""

    synthetic = "\n".join(
        [
            "from typing import TYPE_CHECKING",
            "from sqlalchemy import select",
            "from trading_platform.db.session import session_scope",
            "from trading_platform.services.execution import attempts",
            "if TYPE_CHECKING:",
            "    from trading_platform.services.alpaca import BrokerOrderSnapshot",
            "else:",
            "    import trading_platform.db.models",
        ]
    )
    flagged = _forbidden_runtime_imports(synthetic)
    assert "sqlalchemy" in flagged
    assert "trading_platform.db.session" in flagged
    assert "session_scope" in flagged
    assert "trading_platform.services.execution.attempts" in flagged
    assert "trading_platform.db.models" in flagged
    assert not any("alpaca" in target for target in flagged)

    snapshot_tree = ast.parse((_RECONCILIATION_DIR / "snapshot.py").read_text())
    snapshot_modules = {
        node.module for node in _runtime_imports(snapshot_tree) if isinstance(node, ast.ImportFrom)
    }
    assert "trading_platform.services.execution" in snapshot_modules
    assert "trading_platform.services.alpaca" not in snapshot_modules


# ---------------------------------------------------------------------------
# The real-reconciliation helper (20.1-32, task 3)
# ---------------------------------------------------------------------------


def test_wall_clock_timeline_is_strictly_increasing_and_never_in_the_future() -> None:
    timeline = WallClockTimeline()
    before = datetime.now(UTC)
    instants = [timeline.next() for _ in range(50)]
    after = datetime.now(UTC)
    assert all(earlier < later for earlier, later in zip(instants, instants[1:], strict=False))
    assert before <= instants[0]
    assert instants[-1] <= after


def test_wall_clock_timeline_places_an_effect_before_a_real_reconciliation(recon_db: str) -> None:
    """The point of wall-clock time: an effect boundary placed with the timeline can be FOLLOWED by
    a real reconciliation (a future-dated Job never could, both services stamp the real clock)."""

    timeline = WallClockTimeline()
    effect_at = timeline.next()
    report, _job_id = run_real_account_reconciliation(broker=scripted_read_broker())
    completed_at = reconciliation_completed_at(report)
    assert completed_at > effect_at
    assert timeline.next() > completed_at


def test_scripted_read_broker_mirrors_the_latest_broker_observed_snapshot(recon_db: str) -> None:
    flat = scripted_read_broker().get_account()
    assert (flat.cash, flat.buying_power, flat.equity) == (Decimal("100000"),) * 3

    with session_scope(load_settings()) as session:
        seed_fresh_broker_snapshot(session, cash=Decimal("12345.500000"))
    mirrored = scripted_read_broker().get_account()
    assert (mirrored.cash, mirrored.buying_power, mirrored.equity) == (Decimal("12345.5"),) * 3
    assert mirrored.long_market_value == mirrored.short_market_value == Decimal("0")

    # Account divergence is zero against the mirrored account: the real result is clean.
    report, _job_id = run_real_account_reconciliation(broker=scripted_read_broker())
    assert report.blocks_execution is False
    assert report.finding_count == 0


def test_real_reconciliation_helper_runs_both_services_and_qualifies(recon_db: str) -> None:
    with session_scope(load_settings()) as session:
        strategy_row(session, OWNER)

    account_report, account_job = run_real_account_reconciliation(broker=scripted_read_broker())
    strategy_report, strategy_job = run_real_strategy_reconciliation(
        strategy_id=OWNER, as_of_session=SESSION_DATE, broker=scripted_read_broker()
    )
    for report in (account_report, strategy_report):
        assert report.blocks_execution is False
        assert report.finding_count == 0

    account_run_id = uuid.UUID(account_report.run_id)
    strategy_run_id = uuid.UUID(strategy_report.run_id)
    with session_scope(load_settings()) as session:
        strategy_run = session.get(StrategyRun, strategy_run_id)
        account_run = session.get(AccountReconciliationRun, account_run_id)
        strategy_job_row = session.get(Job, strategy_job)
        account_job_row = session.get(Job, account_job)
        assert strategy_run is not None and account_run is not None
        assert strategy_job_row is not None and account_job_row is not None
        # Exactly what the recovery gate requires of a standalone result.
        assert strategy_run.trigger_source == "job"
        assert strategy_run.job_id == strategy_job
        assert strategy_job_row.job_type == "reconciliation"
        assert strategy_job_row.status is JobStatus.SUCCEEDED
        assert account_run.trigger_source == "job"
        assert account_run.job_id == account_job
        assert account_job_row.job_type == "reconciliation"
        assert account_job_row.status is JobStatus.SUCCEEDED

        by_scope = {
            scope: latest_standalone_reconciliation(session, OWNER, scope=scope)
            for scope in ("account", "strategy")
        }
        newest = latest_standalone_reconciliation(session, strategy_public_id=OWNER)

    assert by_scope["account"] is not None and by_scope["account"].run_id == account_run_id
    assert by_scope["strategy"] is not None and by_scope["strategy"].run_id == strategy_run_id
    assert by_scope["account"].is_clean and by_scope["strategy"].is_clean
    completed = {
        account_run_id: reconciliation_completed_at(account_report),
        strategy_run_id: reconciliation_completed_at(strategy_report),
    }
    assert newest is not None and newest.is_clean
    assert newest.run_id == max(completed, key=completed.__getitem__)


def test_helper_never_seeds_reconciliation_rows() -> None:
    """Every stored result comes from the real services: the helper source must not contain a
    seeding fixture or a direct insert of a reconciliation row."""

    source = Path(real_reconciliation.__file__).read_text()
    forbidden = (
        "seed_account_run",
        "seed_strategy_reconciliation",
        "AccountReconciliationRun(",
        "INSERT INTO account_reconciliation_runs",
        "INSERT INTO strategy_runs",
    )
    assert [token for token in forbidden if token in source] == []
