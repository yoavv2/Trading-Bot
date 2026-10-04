"""S3-R4 intent identity and action justification (final correction, amended round 5/6).

A candidate of a risk run becomes a NEW intent only if every check holds, in this order; the
first failing check decides the candidate's closed disposition:

1. no uncertainty (the recovery predicate; decided by the caller: 409/blocked ``outcome_unresolved``);
2. VERIFIED BASIS (``verify_evaluation_basis``, a pure function over the rows
   ``load_basis_verification_rows`` reads): with an empty order history the basis is verified
   trivially; otherwise the risk run's recorded portfolio basis names a broker-observed
   snapshot written by a broker-order-sync Job ``S_sync`` (located through that Job's
   ``result_summary.snapshot_id``) and, from persisted records only, (i) ``S_sync`` completed
   after the strategy's execution watermark, (ii) ``S_sync`` applied the final terminal state
   of every earlier order that reached the broker, (iii) the ingested fill quantity of each
   equals its broker filled quantity, (iv) a CLEAN standalone reconciliation completed inside
   the window (S_sync completion, evaluation completion), and (v) the evaluation's basis
   positions equal the positions derived from the ingested fills. A later snapshot timestamp
   alone never satisfies this. Failure details, in this order: ``predates_executions``,
   ``executions_not_synced``, ``fills_not_ingested``, ``reconciliation_missing``,
   ``basis_positions_mismatch``;
3. no working order on the symbol (handled by the permission check as a pause);
4. NOT A REPLAY: ``decision_fingerprint`` equal to that of an earlier intent of the strategy that
   REACHED OR MAY HAVE REACHED THE BROKER -> ``replay_of_earlier_decision`` (detection only: a
   different fingerprint never proves a new intent);
5. ACTION JUSTIFIED against the verified basis: an entry needs the symbol flat, an exit needs a
   held position -> ``duplicate_open_position`` / ``no_open_position``;
6. SESSION ALLOWANCE (TL-10): at most ONE broker-reaching action per (strategy, evaluation
   session, symbol, side), consumed by any earlier order of the strategy that reached or may
   have reached the broker (legacy orders without proof included) ->
   ``action_already_submitted``;
7. risk-approved at evaluation (already true of every candidate) and revalidated at the fresh
   price (the permission check).

Everything here reads the database and writes nothing.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from trading_platform.core.settings import Settings, get_strategy_config
from trading_platform.db.models import (
    ExecutionOperation,
    ExecutionOperationIntent,
    Job,
    JobStatus,
    OrderEvent,
    OrderLifecycleState,
    OrderSubmissionAttempt,
    OrderTransitionOutcome,
    PaperFill,
    PaperOrder,
    RiskEvent,
    Strategy,
    StrategyRun,
    Symbol,
)
from trading_platform.services.broker_status import BrokerStatusClass, classify_broker_status
from trading_platform.services.execution.attempts import (
    AttemptRecord,
    reached_or_may_have_reached_broker,
)
from trading_platform.services.execution.attempts import (
    _record_from_row as _attempt_record,
)
from trading_platform.services.read_recording import canonical_json, sha256_hex
from trading_platform.services.reconciliation.latest import (
    StandaloneReconciliation,
    latest_standalone_reconciliation,
)

BROKER_ORDER_SYNC_JOB_TYPE = "broker-order-sync"
#: Manifest parameter keys that carry an as-of bound or a time-derived value (D-25 handoff):
#: ``as_of`` (bar windows), ``as_of_bound`` (latest-session lookups), ``end`` (session dates).
TIME_DERIVED_PARAM_KEYS = frozenset({"as_of", "as_of_bound", "end"})

_TERMINAL_LOCAL = frozenset(
    {
        OrderLifecycleState.FILLED,
        OrderLifecycleState.CANCELED,
        OrderLifecycleState.EXPIRED,
        OrderLifecycleState.REJECTED,
    }
)


class BasisFailure(StrEnum):
    """Closed details of ``evaluation_basis_unverified`` (in precedence order)."""

    PREDATES_EXECUTIONS = "predates_executions"
    EXECUTIONS_NOT_SYNCED = "executions_not_synced"
    FILLS_NOT_INGESTED = "fills_not_ingested"
    RECONCILIATION_MISSING = "reconciliation_missing"
    BASIS_POSITIONS_MISMATCH = "basis_positions_mismatch"


class CandidateDisposition(StrEnum):
    """Closed dispositions of a candidate that did NOT become a new intent (round 6 record)."""

    REPLAY_OF_EARLIER_DECISION = "replay_of_earlier_decision"
    ACTION_ALREADY_SUBMITTED = "action_already_submitted"
    DUPLICATE_OPEN_POSITION = "duplicate_open_position"
    NO_OPEN_POSITION = "no_open_position"


class EvaluationBasisUnverifiedError(RuntimeError):
    """The pinned risk run's portfolio basis is not verified against the strategy's executions."""

    def __init__(self, failure: BasisFailure) -> None:
        super().__init__(f"Evaluation basis is not verified ({failure.value}).")
        self.failure = failure


class VersionBypassRefusedError(RuntimeError):
    """``create_new_version`` refused: an earlier version of the identity reached the broker."""

    code = "version_bypass_refused"

    def __init__(self, client_order_id: str | None = None) -> None:
        super().__init__(
            "version_bypass_refused: an earlier version of this order identity reached or may "
            "have reached the broker; a new version is never created for it."
        )
        self.client_order_id = client_order_id


# ---------------------------------------------------------------------------
# Rows (loaded once) and the pure basis verification
# ---------------------------------------------------------------------------


def _norm(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


@dataclass(frozen=True)
class OrderFact:
    """One earlier PaperOrder of the strategy with the facts verification needs."""

    paper_order_id: uuid.UUID
    #: The risk run whose approved event this order realises (None for a legacy order).
    source_risk_run_id: uuid.UUID | None
    status: OrderLifecycleState
    broker_order_id: str | None
    symbol: str
    side: str
    session_date: date
    quantity: Decimal
    intent_hash: str
    created_at: datetime
    last_synced_at: datetime | None
    terminal_at: datetime | None
    fill_quantity: Decimal
    attempts: tuple[AttemptRecord, ...]
    #: Registered under the attempt-log invariant: operation-bound or has an attempt row.
    attempt_log_registered: bool
    reached_broker: bool

    @property
    def has_broker_evidence(self) -> bool:
        return bool(
            self.broker_order_id
            or self.status
            in (
                OrderLifecycleState.SUBMITTED,
                OrderLifecycleState.PARTIALLY_FILLED,
                OrderLifecycleState.FILLED,
                OrderLifecycleState.CANCELED,
                OrderLifecycleState.EXPIRED,
            )
        )


@dataclass(frozen=True)
class AppliedOrder:
    paper_order_id: str
    broker_status: str | None
    broker_filled_qty: Decimal | None
    applied_at: datetime | None


@dataclass(frozen=True)
class SyncFact:
    job_id: uuid.UUID
    completed_at: datetime
    snapshot_id: str
    applied: Mapping[str, AppliedOrder]


@dataclass(frozen=True)
class BasisRows:
    """Everything ``verify_evaluation_basis`` judges; a pure value (no session)."""

    strategy_public_id: str
    risk_run_id: uuid.UUID
    risk_completed_at: datetime | None
    basis_source: str | None
    basis_snapshot_id: str | None
    #: The evaluation's recorded basis positions of THIS strategy: symbol -> quantity.
    basis_positions: Mapping[str, Decimal]
    #: Positions derived from the ingested fills of THIS strategy: symbol -> net quantity.
    local_positions: Mapping[str, Decimal]
    orders: tuple[OrderFact, ...]
    sync: SyncFact | None
    reconciliation: StandaloneReconciliation | None


@dataclass(frozen=True)
class BasisVerification:
    """Result of ``verify_evaluation_basis``: ``failure`` is None when verified."""

    failure: BasisFailure | None
    record: Mapping[str, Any] = field(default_factory=dict)

    @property
    def verified(self) -> bool:
        return self.failure is None


def verification_orders(rows: BasisRows) -> tuple[OrderFact, ...]:
    """The earlier orders the basis must reflect: every order of the strategy EXCEPT those realising
    the pinned risk run itself (that evaluation's own, earlier, partial execution can never be in
    its basis; the permission check revalidates against the refreshed portfolio instead)."""

    return tuple(order for order in rows.orders if order.source_risk_run_id != rows.risk_run_id)


def execution_watermark(rows: BasisRows) -> datetime | None:
    """The later of every earlier order becoming locally terminal and the strategy's latest
    submission attempt start."""

    candidates: list[datetime] = []
    for order in verification_orders(rows):
        if order.status in _TERMINAL_LOCAL and order.terminal_at is not None:
            candidates.append(order.terminal_at)
        for attempt in order.attempts:
            started = _aware(attempt.started_at)
            if started is not None:
                candidates.append(started)
    return max(candidates) if candidates else None


def verify_evaluation_basis(rows: BasisRows) -> BasisVerification:
    """Pure verification of the evaluation basis against the strategy's execution history."""

    earlier = verification_orders(rows)
    if not earlier:
        return BasisVerification(
            None,
            {
                "empty_history": True,
                "basis_source": rows.basis_source,
                "sync_job_id": None,
                "snapshot_id": rows.basis_snapshot_id,
                "reconciliation_run_id": None,
                "watermark": None,
                "covered_orders": [],
            },
        )

    watermark = execution_watermark(rows)
    sync = rows.sync

    # (i) the sync completed after the watermark
    if sync is None or (watermark is not None and sync.completed_at <= watermark):
        return BasisVerification(BasisFailure.PREDATES_EXECUTIONS)

    # (ii) the final broker state of every earlier order that reached the broker was applied
    covered: list[dict[str, Any]] = []
    for order in earlier:
        needs_apply = order.has_broker_evidence
        if not needs_apply:
            if order.status is OrderLifecycleState.REJECTED:
                continue  # rejected at submission: the broker never created an order
            if order.status is OrderLifecycleState.SUBMISSION_FAILED and not order.attempts:
                continue  # a legacy failed submission: gate-neutral (Phase 20 classification)
            if order.reached_broker:
                return BasisVerification(BasisFailure.EXECUTIONS_NOT_SYNCED)
            continue  # never sent (proven not sent): nothing to sync
        applied = sync.applied.get(str(order.paper_order_id))
        if applied is None or classify_broker_status(applied.broker_status) not in (
            BrokerStatusClass.TERMINAL,
            BrokerStatusClass.TERMINAL_WITH_SUCCESSOR,
        ):
            return BasisVerification(BasisFailure.EXECUTIONS_NOT_SYNCED)
        if order.status not in _TERMINAL_LOCAL:
            return BasisVerification(BasisFailure.EXECUTIONS_NOT_SYNCED)
        if applied.applied_at is not None and applied.applied_at > sync.completed_at:
            return BasisVerification(BasisFailure.EXECUTIONS_NOT_SYNCED)
        # (iii) the ingested fills equal the broker filled quantity
        broker_filled = (
            applied.broker_filled_qty if applied.broker_filled_qty is not None else Decimal("0")
        )
        if order.fill_quantity != broker_filled:
            return BasisVerification(BasisFailure.FILLS_NOT_INGESTED)
        covered.append(
            {
                "paper_order_id": str(order.paper_order_id),
                "status": order.status.value,
                "filled_qty": _norm(order.fill_quantity),
            }
        )

    # (iv) a clean standalone reconciliation inside the window
    reconciliation = rows.reconciliation
    if reconciliation is None or not reconciliation.is_clean:
        return BasisVerification(BasisFailure.RECONCILIATION_MISSING)

    # (v) the basis positions equal the positions derived from the ingested fills
    symbols = set(rows.basis_positions) | set(rows.local_positions)
    for symbol in symbols:
        recorded = rows.basis_positions.get(symbol, Decimal("0"))
        derived = rows.local_positions.get(symbol, Decimal("0"))
        if recorded != derived:
            return BasisVerification(BasisFailure.BASIS_POSITIONS_MISMATCH)

    return BasisVerification(
        None,
        {
            "empty_history": False,
            "basis_source": rows.basis_source,
            "sync_job_id": str(sync.job_id),
            "snapshot_id": sync.snapshot_id,
            "reconciliation_run_id": str(reconciliation.run_id),
            "watermark": watermark.isoformat() if watermark is not None else None,
            "covered_orders": covered,
        },
    )


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        parsed = Decimal(str(value))
    except InvalidOperation:
        return None
    return parsed if parsed.is_finite() else None


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return _aware(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


def load_strategy_order_facts(session: Session, strategy_public_id: str) -> tuple[OrderFact, ...]:
    """Every PaperOrder of the strategy with fills, attempts and terminal time (bounded statements:
    orders, fill sums, attempts, order events, operation-bound ids)."""

    rows = session.execute(
        select(PaperOrder, Symbol.ticker, RiskEvent.strategy_run_id)
        .join(StrategyRun, StrategyRun.id == PaperOrder.strategy_run_id)
        .join(Strategy, Strategy.id == StrategyRun.strategy_id)
        .join(Symbol, Symbol.id == PaperOrder.symbol_id)
        .outerjoin(RiskEvent, RiskEvent.id == PaperOrder.source_risk_event_id)
        .where(Strategy.strategy_id == strategy_public_id)
        .order_by(PaperOrder.created_at, PaperOrder.id)
    ).all()
    if not rows:
        return ()
    order_ids = [order.id for order, _ticker, _run in rows]
    fills = dict(
        session.execute(
            select(PaperFill.paper_order_id, func.coalesce(func.sum(PaperFill.quantity), 0))
            .where(PaperFill.paper_order_id.in_(order_ids))
            .group_by(PaperFill.paper_order_id)
        ).all()
    )
    attempts_by_order: dict[uuid.UUID, list[AttemptRecord]] = {}
    for attempt in session.execute(
        select(OrderSubmissionAttempt)
        .where(OrderSubmissionAttempt.paper_order_id.in_(order_ids))
        .order_by(OrderSubmissionAttempt.paper_order_id, OrderSubmissionAttempt.attempt_number)
    ).scalars():
        attempts_by_order.setdefault(attempt.paper_order_id, []).append(_attempt_record(attempt))
    terminal_at = dict(
        session.execute(
            select(OrderEvent.paper_order_id, func.max(OrderEvent.event_at))
            .where(
                OrderEvent.paper_order_id.in_(order_ids),
                OrderEvent.outcome == OrderTransitionOutcome.ACCEPTED,
                OrderEvent.to_state.in_(sorted(_TERMINAL_LOCAL, key=lambda s: s.value)),
            )
            .group_by(OrderEvent.paper_order_id)
        ).all()
    )
    # Registered under the attempt-log invariant (S1-R3): an attempt row exists, or the order was
    # created at or after the EARLIEST pinned intent that references it (the same rule as
    # ``operations._attempt_log_registered``); a legacy order that only a LATER reuse row
    # references is never proven not sent.
    first_intent_at = dict(
        session.execute(
            select(
                ExecutionOperationIntent.paper_order_id,
                func.min(ExecutionOperationIntent.created_at),
            )
            .where(ExecutionOperationIntent.paper_order_id.in_(order_ids))
            .group_by(ExecutionOperationIntent.paper_order_id)
        ).all()
    )
    facts: list[OrderFact] = []
    for order, ticker, source_run_id in rows:
        attempts = tuple(attempts_by_order.get(order.id, ()))
        first_at = first_intent_at.get(order.id)
        registered = bool(attempts) or (first_at is not None and order.created_at >= first_at)
        facts.append(
            OrderFact(
                paper_order_id=order.id,
                source_risk_run_id=source_run_id,
                status=order.status,
                broker_order_id=order.broker_order_id,
                symbol=ticker,
                side=order.side,
                session_date=order.intended_session_date,
                quantity=order.quantity,
                intent_hash=order.intent_hash,
                created_at=_aware(order.created_at) or datetime.min.replace(tzinfo=UTC),
                last_synced_at=_aware(order.last_synced_at),
                terminal_at=(
                    _aware(terminal_at.get(order.id))
                    or _aware(order.last_broker_update_at)
                    or _aware(order.created_at)
                ),
                fill_quantity=Decimal(str(fills.get(order.id, 0))),
                attempts=attempts,
                attempt_log_registered=registered,
                reached_broker=reached_or_may_have_reached_broker(
                    order, attempts, attempt_log_registered=registered
                ),
            )
        )
    return tuple(facts)


def _derived_local_positions(
    session: Session, strategy_public_id: str, *, excluding_risk_run: uuid.UUID
) -> dict[str, Decimal]:
    rows = session.execute(
        select(Symbol.ticker, PaperFill.side, func.sum(PaperFill.quantity))
        .join(PaperOrder, PaperOrder.id == PaperFill.paper_order_id)
        .join(StrategyRun, StrategyRun.id == PaperOrder.strategy_run_id)
        .join(Strategy, Strategy.id == StrategyRun.strategy_id)
        .join(Symbol, Symbol.id == PaperFill.symbol_id)
        .outerjoin(RiskEvent, RiskEvent.id == PaperOrder.source_risk_event_id)
        .where(
            Strategy.strategy_id == strategy_public_id,
            or_(
                RiskEvent.strategy_run_id.is_(None), RiskEvent.strategy_run_id != excluding_risk_run
            ),
        )
        .group_by(Symbol.ticker, PaperFill.side)
    ).all()
    net: dict[str, Decimal] = {}
    for ticker, side, quantity in rows:
        signed = Decimal(str(quantity)) * (1 if side == "buy" else -1)
        net[ticker] = net.get(ticker, Decimal("0")) + signed
    return {symbol: qty for symbol, qty in net.items() if qty != 0}


def _basis_positions(basis: Mapping[str, Any], strategy_public_id: str) -> dict[str, Decimal]:
    positions: dict[str, Decimal] = {}
    raw = basis.get("positions")
    if not isinstance(raw, list):
        return positions
    for item in raw:
        if not isinstance(item, Mapping) or item.get("strategy_id") != strategy_public_id:
            continue
        quantity = _decimal(item.get("quantity"))
        symbol = item.get("symbol")
        if isinstance(symbol, str) and quantity is not None and quantity != 0:
            positions[symbol] = positions.get(symbol, Decimal("0")) + quantity
    return positions


def _find_sync(session: Session, snapshot_id: str | None) -> SyncFact | None:
    if not snapshot_id:
        return None
    job = session.execute(
        select(Job)
        .where(
            Job.job_type == BROKER_ORDER_SYNC_JOB_TYPE,
            Job.status == JobStatus.SUCCEEDED,
            Job.completed_at.is_not(None),
            Job.result_summary["snapshot_id"].as_string() == snapshot_id,
        )
        .order_by(Job.completed_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if job is None or job.completed_at is None:
        return None
    applied: dict[str, AppliedOrder] = {}
    summary = job.result_summary or {}
    raw = summary.get("applied_orders")
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, Mapping) or not isinstance(item.get("paper_order_id"), str):
                continue
            record = AppliedOrder(
                paper_order_id=item["paper_order_id"],
                broker_status=item.get("broker_status"),
                broker_filled_qty=_decimal(item.get("broker_filled_qty")),
                applied_at=_parse_dt(item.get("applied_at")),
            )
            applied[record.paper_order_id] = record  # the last record per order wins
    return SyncFact(
        job_id=job.id,
        completed_at=_aware(job.completed_at) or job.completed_at,
        snapshot_id=snapshot_id,
        applied=applied,
    )


def load_basis_verification_rows(
    session: Session, *, strategy_public_id: str, risk_run: StrategyRun
) -> BasisRows:
    """Read every persisted record ``verify_evaluation_basis`` needs (read-only)."""

    orders = load_strategy_order_facts(session, strategy_public_id)
    summary = risk_run.result_summary or {}
    basis = summary.get("portfolio_basis")
    basis = basis if isinstance(basis, Mapping) else {}
    snapshot_id = basis.get("snapshot_id") if isinstance(basis.get("snapshot_id"), str) else None
    has_earlier = any(order.source_risk_run_id != risk_run.id for order in orders)
    sync = _find_sync(session, snapshot_id) if has_earlier else None
    risk_completed = _aware(risk_run.completed_at)
    reconciliation: StandaloneReconciliation | None = None
    if sync is not None and risk_completed is not None:
        reconciliation = latest_standalone_reconciliation(
            session,
            strategy_public_id,
            "either",
            completed_after=sync.completed_at,
            completed_before=risk_completed,
        )
    return BasisRows(
        strategy_public_id=strategy_public_id,
        risk_run_id=risk_run.id,
        risk_completed_at=risk_completed,
        basis_source=basis.get("source") if isinstance(basis.get("source"), str) else None,
        basis_snapshot_id=snapshot_id,
        basis_positions=_basis_positions(basis, strategy_public_id),
        local_positions=(
            _derived_local_positions(session, strategy_public_id, excluding_risk_run=risk_run.id)
            if has_earlier
            else {}
        ),
        orders=orders,
        sync=sync,
        reconciliation=reconciliation,
    )


# ---------------------------------------------------------------------------
# Fingerprint
# ---------------------------------------------------------------------------


def risk_config_digest(settings: Settings, strategy_id: str) -> str:
    """Digest of the CURRENT risk-limit configuration (excluded from provenance, decides approval)."""

    config = get_strategy_config(settings, strategy_id)
    return sha256_hex(
        {
            "risk": config.risk.model_dump(mode="json"),
            "portfolio": settings.portfolio.model_dump(mode="json"),
        }
    )


def decision_inputs_digest(manifest: Mapping[str, Any] | None, *, risk_config: str) -> str:
    """TIMESTAMP-FREE digest of the decision inputs.

    The sorted list of (accessor, parameters with every as-of bound / time-derived value
    removed, result digest) of the recorded manifest, the strategy settings digest and the
    digest of the current risk configuration. A re-run over the same data, settings, risk
    policy and portfolio yields the same value whatever the run ids, wall clock or resolved
    as-of bounds.
    """

    requests: list[list[str]] = []
    settings_digest = ""
    if isinstance(manifest, Mapping):
        settings_digest = str(manifest.get("settings_digest") or "")
        for request in manifest.get("requests") or []:
            if not isinstance(request, Mapping):
                continue
            params = {
                key: value
                for key, value in dict(request.get("params") or {}).items()
                if key not in TIME_DERIVED_PARAM_KEYS
            }
            requests.append(
                [str(request.get("kind")), canonical_json(params), str(request.get("digest"))]
            )
    requests.sort()
    return sha256_hex(
        {"requests": requests, "settings_digest": settings_digest, "risk_config": risk_config}
    )


def portfolio_state_digest(
    positions: Mapping[str, Decimal], working_orders: Iterable[tuple[str, str, Decimal]]
) -> str:
    """Digest of the verified basis state: positions by symbol and quantity plus the set of
    working orders (cash and timestamps excluded)."""

    return sha256_hex(
        {
            "positions": sorted((symbol, _norm(qty)) for symbol, qty in positions.items()),
            "working_orders": sorted(
                (symbol, side, _norm(qty)) for symbol, side, qty in working_orders
            ),
        }
    )


def decision_fingerprint(
    *,
    strategy_id: str,
    session_date: date,
    symbol: str,
    side: str,
    quantity: Decimal,
    inputs_digest: str,
    portfolio_digest: str,
) -> str:
    """SHA-256 over (strategy, evaluation session, symbol, side, quantity, decision-inputs
    digest, portfolio-state digest of the verified basis)."""

    payload = json.dumps(
        {
            "strategy_id": strategy_id,
            "session_date": session_date.isoformat(),
            "symbol": symbol,
            "side": side,
            "quantity": _norm(quantity),
            "inputs": inputs_digest,
            "portfolio": portfolio_digest,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Candidate classification (checks 4-6)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CandidateKey:
    symbol: str
    side: str
    session_date: date
    quantity: Decimal


@dataclass(frozen=True)
class EarlierIntent:
    """An earlier operation intent of the strategy with the broker-reach fact of its order."""

    intent_id: uuid.UUID
    fingerprint: str
    reached_broker: bool


@dataclass(frozen=True)
class CandidateVerdict:
    """The disposition of one candidate: ``eligible`` (a NEW intent) or a closed refusal."""

    disposition: CandidateDisposition | None
    earlier_intent_id: uuid.UUID | None = None
    prior_execution_refs: tuple[str, ...] = ()

    @property
    def eligible(self) -> bool:
        return self.disposition is None


def load_earlier_intents(
    session: Session, strategy_public_id: str, orders: Sequence[OrderFact]
) -> list[EarlierIntent]:
    """Every operation intent of the strategy that has a PaperOrder, with its broker-reach."""

    by_id = {order.paper_order_id: order for order in orders}
    if not by_id:
        return []
    rows = session.execute(
        select(
            ExecutionOperationIntent.id,
            ExecutionOperationIntent.decision_fingerprint,
            ExecutionOperationIntent.paper_order_id,
        )
        .join(ExecutionOperation, ExecutionOperation.id == ExecutionOperationIntent.operation_id)
        .join(Strategy, Strategy.id == ExecutionOperation.strategy_id)
        .where(
            Strategy.strategy_id == strategy_public_id,
            ExecutionOperationIntent.paper_order_id.is_not(None),
        )
    ).all()
    intents: list[EarlierIntent] = []
    for intent_id, fingerprint, paper_order_id in rows:
        order = by_id.get(paper_order_id) if paper_order_id is not None else None
        if order is None:
            continue
        intents.append(EarlierIntent(intent_id, fingerprint, order.reached_broker))
    return intents


def classify_candidate(
    key: CandidateKey,
    fingerprint: str,
    *,
    orders: Sequence[OrderFact],
    earlier_intents: Sequence[EarlierIntent],
    basis_positions: Mapping[str, Decimal] | None,
) -> CandidateVerdict:
    """Checks 4-6 of S3-R4 for one candidate (check 7 is the permission check).

    ``basis_positions`` is the evaluation's recorded basis of THIS strategy, or ``None`` when
    the risk run recorded no portfolio basis (a hand-seeded run): check 5 is then skipped.
    """

    # (4) not a replay of a decision that reached (or may have reached) the broker
    for earlier in earlier_intents:
        if earlier.reached_broker and earlier.fingerprint == fingerprint:
            return CandidateVerdict(
                CandidateDisposition.REPLAY_OF_EARLIER_DECISION, earlier_intent_id=earlier.intent_id
            )
    # (5) the action changes the verified state in the strategy's direction
    if basis_positions is not None:
        held = basis_positions.get(key.symbol, Decimal("0"))
        if key.side == "buy" and held != 0:
            return CandidateVerdict(CandidateDisposition.DUPLICATE_OPEN_POSITION)
        if key.side == "sell" and held <= 0:
            return CandidateVerdict(CandidateDisposition.NO_OPEN_POSITION)
    # (6) TL-10 session allowance: one broker-reaching action per (session, symbol, side)
    same_key = [
        order
        for order in orders
        if order.symbol == key.symbol
        and order.side == key.side
        and order.session_date == key.session_date
    ]
    if any(order.reached_broker for order in same_key):
        return CandidateVerdict(CandidateDisposition.ACTION_ALREADY_SUBMITTED)
    # ``prior_execution_refs``: the strategy's earlier orders on this symbol (any session or
    # side), so a later-session exit names the buy it closes.
    return CandidateVerdict(
        None,
        prior_execution_refs=tuple(
            str(order.paper_order_id) for order in orders if order.symbol == key.symbol
        ),
    )


__all__ = [
    "BROKER_ORDER_SYNC_JOB_TYPE",
    "TIME_DERIVED_PARAM_KEYS",
    "AppliedOrder",
    "BasisFailure",
    "BasisRows",
    "BasisVerification",
    "CandidateDisposition",
    "CandidateKey",
    "CandidateVerdict",
    "EarlierIntent",
    "EvaluationBasisUnverifiedError",
    "OrderFact",
    "SyncFact",
    "VersionBypassRefusedError",
    "classify_candidate",
    "decision_fingerprint",
    "decision_inputs_digest",
    "execution_watermark",
    "load_basis_verification_rows",
    "load_earlier_intents",
    "load_strategy_order_facts",
    "portfolio_state_digest",
    "risk_config_digest",
    "verify_evaluation_basis",
]
