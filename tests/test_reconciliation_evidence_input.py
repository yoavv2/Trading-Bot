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

import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from tests.support.migrated_db import migrated_database
from tests.support.operation_fixtures import seed_operation
from tests.support.query_counter import count_queries
from tests.support.recovery_fixtures import (
    OWNER,
    seed_intent,
    seed_operation_bound_intent,
    seed_paper_run,
)

from trading_platform.core.settings import load_settings
from trading_platform.db.models import (
    AttemptOutcomeClass,
    ExecutionOperationIntent,
    OrderLifecycleState,
    PaperOrder,
    StrategyRun,
)
from trading_platform.db.session import session_scope
from trading_platform.services.execution.attempts import (
    SubmissionEvidence,
    load_submission_evidence,
)
from trading_platform.services.execution.intent_identity import load_strategy_order_facts

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
