"""Uncertain-outcome recovery: the ONE domain predicate (REC-01, D-12/D-14/D-15, W-1 declined).

An ambiguous order POST leaves a registered intent whose broker state is unknown. This
module decides, from persisted evidence only, whether a strategy's uncertain outcomes are
RESOLVED, and feeds every consumer of that answer from the same computation: the
paper-session submission gate (``strategy_recovery_status``), the Job recovery read
(``get_job_recovery``, R3), the handover/seeding check A5 and the Phase 21 issue
projection (``account_recovery_status`` / ``outcome_issue_inputs``).

Resolution rules (03 sec.3.7 B, amended round 5, 2026-10-04):

* every registered intent must be ESTABLISHED: ``nothing_submitted`` (a Job with no
  linked ``paper_execution`` run, or a ``broker-order-sync`` Job; zero order rows alone
  never proves it), ``not_sent`` (its OWN attempt history proves it never left the
  process), ``rejected_at_submission`` (a recorded 4xx) or ``found_verified`` (the broker
  shows the order, in any state, matched by id). ``not_sent`` and the unestablished rest come
  from the ONE shared function ``attempts.classify_submission_evidence`` (20.1-17), the same
  verdict the run-time send guard G2 reads: zero attempt rows prove not-sent only for an
  attempt-log-registered (operation-bound) pending_submission / submission_failed order, so a
  legacy zero-attempt order is never ``not_sent``. The predicate lists such an order, and an
  unparked ambiguous order, whether or not its Job is flagged ``outcome_uncertain``;
* a never-found order stays unresolved whatever the elapsed time, the absence evidence,
  a broker statement or a terminated executor; there is no resend path
  (``resubmission_permitted`` is False for every intent);
* resolution also needs a fresh CLEAN standalone reconciliation (account or owner level,
  never the in-session check) completed after the latest broker-touching effect;
* a strategy with no uncertain Job and no unestablished intent is resolved outright.

Reads (``strategy_recovery_status``, ``account_recovery_status``, ``get_job_recovery``,
``outcome_issue_inputs``) perform no writes and a fixed number of statements
(two for a status read) whatever the amount of history. Evidence is appended ONLY by
``assess_unestablished_intents``, called from the broker-sync passes, and by
``record_broker_statement``; the append-only ``recovery_records`` table has no update or
delete path here (a source test pins it).
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal, Protocol

from sqlalchemy import String, literal_column, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from trading_platform.core import clock
from trading_platform.core.settings import load_settings
from trading_platform.db.models import (
    AbsenceEvidenceItem,
    AttemptOutcomeClass,
    BrokerState,
    BrokerStatementKind,
    EvidenceResult,
    ExecutionOperation,
    ExecutionOperationIntent,
    Job,
    OrderLifecycleState,
    PaperOrder,
    RecoveryClassification,
    RecoveryRecord,
    RecoveryRecordKind,
    UnresolvedReason,
)
from trading_platform.db.models.order_event import OrderTransitionEventType
from trading_platform.services.broker_jobs import (
    BROKER_ORDER_SYNC_JOB_TYPE,
    PAPER_SESSION_JOB_TYPE,
    RECORD_EXTERNAL_ACTIVITY_JOB_TYPE,
    STATE_CHANGING_BROKER_JOB_TYPES,
    job_strategy_public_id_sql,
)
from trading_platform.services.broker_status import BrokerStatusClass, classify_broker_status
from trading_platform.services.execution.attempts import (
    AttemptRecord,
    SubmissionEvidence,
    attempt_log_registered,
    classify_submission_evidence,
)

# ---------------------------------------------------------------------------
# Closed vocabularies
# ---------------------------------------------------------------------------


class GateCode(StrEnum):
    """Closed gate codes of an unresolved strategy (D-15); exactly the three typed 409 codes."""

    OUTCOME_UNRESOLVED = "outcome_unresolved"
    RECONCILIATION_REQUIRED = "reconciliation_required"
    RECONCILIATION_NOT_CLEAN = "reconciliation_not_clean"


class UncertainOutcomeIssue(StrEnum):
    """The two issue-projection inputs of Phase 21, from the SAME predicate."""

    OUTCOME_UNCERTAIN_UNVERIFIED = "outcome_uncertain_unverified"
    OUTCOME_UNCERTAIN_UNRESOLVED = "outcome_uncertain_unresolved"


#: Reasons an intent stays unresolved; each maps to ``outcome_uncertain_unresolved``.
UNRESOLVED_REASON_ISSUE = {
    reason: UncertainOutcomeIssue.OUTCOME_UNCERTAIN_UNRESOLVED for reason in UnresolvedReason
}

RECOVERY_GATE_CODES: frozenset[str] = frozenset(code.value for code in GateCode)

#: Why ``resubmission_permitted`` is False for every intent in this version (round 5).
RESUBMISSION_UNAVAILABLE_REASON = "resubmission_unavailable"

#: Required job type reported with a gate (the operator action that clears the gate).
REQUIRED_JOB_TYPE = "reconciliation"

#: ``StrategyRun.trigger_source`` written by the standalone ``reconciliation`` Job handler.
#: Equals ``services.reconciliation.latest.STANDALONE_TRIGGER_SOURCE`` (pinned by a parity
#: test); duplicated because importing the reconciliation package here would be cyclic.
_STANDALONE_TRIGGER_SOURCE = "job"
_RECONCILIATION_JOB_TYPE = "reconciliation"

_UNCERTAIN_JOB_TYPES = (PAPER_SESSION_JOB_TYPE, BROKER_ORDER_SYNC_JOB_TYPE)
_EFFECT_JOB_TYPES = tuple(
    sorted(STATE_CHANGING_BROKER_JOB_TYPES - {RECORD_EXTERNAL_ACTIVITY_JOB_TYPE})
)

_ESTABLISHED_CLASSIFICATIONS = frozenset(
    {
        RecoveryClassification.NOTHING_SUBMITTED,
        RecoveryClassification.NOT_SENT,
        RecoveryClassification.REJECTED_AT_SUBMISSION,
        RecoveryClassification.FOUND_VERIFIED,
    }
)

BROKER_STATEMENT_MAX_CHARS = 500
RECOVERY_RECORD_ACTOR_SYNC = "broker_sync"

#: Operation context of an intent: ``open`` (running, paused or requires_reevaluation),
#: ``terminated``, ``completed`` or ``none`` (the order belongs to no operation).
OperationState = Literal["open", "terminated", "completed", "none"]


class OperationView(Protocol):
    """Seam to the execution-operation record (the real view is ``DbOperationView``).

    Only used to REPORT the operation context of an intent; the predicate itself never
    reads operation STATE (ending or expiring an operation resolves nothing, J-2). It reads the
    pinned intent rows only to decide attempt-log registration (``first_intent_at``, 20.1-17).
    """

    def state_for_intent(self, session: Session, paper_order_id: uuid.UUID) -> OperationState: ...


class NullOperationView:
    """A view reporting no operation (tests and callers without an operation record)."""

    def state_for_intent(self, session: Session, paper_order_id: uuid.UUID) -> OperationState:
        return "none"


class DbOperationView:
    """The real view: reads the operation record (``execution_operation_intents`` ->
    ``execution_operations``). An order in several operations reports the most relevant one:
    an open operation first, else the newest. One statement for any number of intents via
    ``states_for_intents`` (the Job recovery read uses it so its statement count stays
    independent of the number of intents)."""

    def state_for_intent(self, session: Session, paper_order_id: uuid.UUID) -> OperationState:
        return self.states_for_intents(session, [paper_order_id]).get(paper_order_id, "none")

    def states_for_intents(
        self, session: Session, paper_order_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, OperationState]:
        if not paper_order_ids:
            return {}
        rows = session.execute(
            select(
                ExecutionOperationIntent.paper_order_id,
                ExecutionOperation.state,
                ExecutionOperation.created_at,
            )
            .join(
                ExecutionOperation,
                ExecutionOperation.id == ExecutionOperationIntent.operation_id,
            )
            .where(ExecutionOperationIntent.paper_order_id.in_(list(paper_order_ids)))
        ).all()
        best: dict[uuid.UUID, tuple[int, datetime, OperationState]] = {}
        for order_id, state, created_at in rows:
            if order_id is None:
                continue
            mapped: OperationState
            if state in ("running", "paused", "requires_reevaluation"):
                mapped, rank = "open", 1
            elif state == "completed":
                mapped, rank = "completed", 0
            else:
                mapped, rank = "terminated", 0
            candidate = (rank, created_at, mapped)
            current = best.get(order_id)
            if current is None or candidate[:2] > current[:2]:
                best[order_id] = candidate
        return {order_id: value[2] for order_id, value in best.items()}


# ---------------------------------------------------------------------------
# Typed errors
# ---------------------------------------------------------------------------


class IntentNotFoundError(LookupError):
    """No registered paper order has this id."""

    def __init__(self, intent_id: uuid.UUID) -> None:
        self.intent_id = intent_id
        super().__init__(f"Intent '{intent_id}' was not found.")


class IntentNotOnMissingOrderPathError(ValueError):
    """The intent is established, matched at the broker or not an ambiguous registered intent."""

    def __init__(self, intent_id: uuid.UUID) -> None:
        self.intent_id = intent_id
        super().__init__(f"Intent '{intent_id}' is not on the missing-order path.")


class StatementConflictError(ValueError):
    """A different broker statement is already recorded for the intent."""

    def __init__(self, intent_id: uuid.UUID, existing_statement: str) -> None:
        self.intent_id = intent_id
        self.existing_statement = existing_statement
        super().__init__(f"Intent '{intent_id}' already has a '{existing_statement}' statement.")


class InvalidBrokerStatementError(ValueError):
    """A statement field is blank, oversized, contains NUL, or is outside the closed set."""

    def __init__(self, field_name: Literal["statement", "reference", "reason"]) -> None:
        self.field_name = field_name
        super().__init__(f"Invalid broker statement field '{field_name}'.")


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RecordView:
    """Immutable projection of one ``recovery_records`` row."""

    id: uuid.UUID
    kind: RecoveryRecordKind
    classification: RecoveryClassification | None
    broker_state: BrokerState | None
    unresolved_reason: UnresolvedReason | None
    evidence_item: AbsenceEvidenceItem | None
    evidence_result: EvidenceResult | None
    observed_at: datetime | None
    statement: BrokerStatementKind | None
    reference: str | None
    reason: str | None
    recorded_by: str
    created_at: datetime


@dataclass(frozen=True)
class IntentRecovery:
    """Recovery state of one registered intent (or a Job-level ``nothing_submitted`` row)."""

    intent_id: uuid.UUID | None
    job_id: uuid.UUID | None
    strategy_id: str | None
    client_order_id: str | None
    order_status: str | None
    broker_order_id: str | None
    broker_status: str | None
    classification: RecoveryClassification
    broker_state: BrokerState | None
    unresolved_reason: UnresolvedReason | None
    attempts: tuple[AttemptRecord, ...]
    absence_evidence: tuple[RecordView, ...]
    statement: RecordView | None
    absence_evidence_complete: bool

    @property
    def established(self) -> bool:
        return self.classification in _ESTABLISHED_CLASSIFICATIONS

    @property
    def blocking(self) -> bool:
        return not self.established

    @property
    def on_missing_order_path(self) -> bool:
        """An order-level intent the broker has not shown (no broker id) and that is unestablished."""

        return self.intent_id is not None and not self.established and self.broker_order_id is None

    @property
    def has_evidence(self) -> bool:
        """Any absence evidence, unresolved reason or statement has been recorded."""

        return (
            bool(self.absence_evidence)
            or self.unresolved_reason is not None
            or (self.statement is not None)
        )

    @property
    def resubmission_permitted(self) -> bool:
        return resubmission_permitted(self)


def resubmission_permitted(intent: IntentRecovery) -> bool:
    """Always False in this version, with reason ``resubmission_unavailable`` (round 5).

    Resubmission would need proof that the already-sent request can no longer reach the
    broker and produce an execution, and neither a statement, absence evidence, executor
    termination nor elapsed time provides it (20.1-11 S1-R3 (4)). A future fence mechanism
    would change only this function and needs separate approval.
    """

    return False


@dataclass(frozen=True)
class UncertainJob:
    job_id: uuid.UUID
    job_type: str
    status: str
    completed_at: datetime | None
    strategy_id: str | None
    execution_run_count: int
    intent_count: int
    established: bool


@dataclass(frozen=True)
class QualifyingReconciliation:
    """The newest completed standalone reconciliation found after the boundary."""

    scope: Literal["account", "strategy"]
    run_id: uuid.UUID
    strategy_id: str | None
    status: str
    completed_at: datetime | None
    blocks_execution: bool
    unresolved_reasons: tuple[str, ...]

    @property
    def is_clean(self) -> bool:
        return (
            self.status == "succeeded" and not self.blocks_execution and not self.unresolved_reasons
        )


@dataclass(frozen=True)
class RecoveryStatus:
    """Result of the predicate for one strategy (or the account-level subject)."""

    strategy_id: str | None
    resolved: bool
    gate_code: GateCode | None
    intents: tuple[IntentRecovery, ...]
    jobs: tuple[UncertainJob, ...]
    reconciliation: QualifyingReconciliation | None
    boundary: datetime | None
    as_of: datetime

    @property
    def unestablished(self) -> tuple[IntentRecovery, ...]:
        return tuple(intent for intent in self.intents if intent.blocking)


@dataclass(frozen=True)
class AccountRecoveryStatus:
    """The predicate for every strategy plus the account level (A5, issue projection)."""

    resolved: bool
    subjects: tuple[RecoveryStatus, ...]
    as_of: datetime

    @property
    def gate_codes(self) -> tuple[GateCode, ...]:
        return tuple(s.gate_code for s in self.subjects if s.gate_code is not None)


@dataclass(frozen=True)
class OutcomeIssueInput:
    """One closed row feeding the Phase 21 issue projection."""

    kind: UncertainOutcomeIssue
    strategy_public_id: str | None
    job_id: uuid.UUID | None
    intent_id: uuid.UUID | None


@dataclass(frozen=True)
class BrokerStatementResult:
    intent_id: uuid.UUID
    strategy_id: str
    statement: BrokerStatementKind
    changed: bool
    classification: RecoveryClassification
    record_id: uuid.UUID


# ---------------------------------------------------------------------------
# Pure classification
# ---------------------------------------------------------------------------


def broker_state_for(order_status: str | None, broker_status: str | None) -> BrokerState | None:
    """Closed broker state of a matched order; ``None`` when the status is unmapped."""

    if broker_status:
        status_class = classify_broker_status(broker_status)
        if status_class is BrokerStatusClass.UNKNOWN:
            return None
        if broker_status == "replaced":
            return BrokerState.REPLACED
        if broker_status == "filled":
            return BrokerState.FILLED
        if broker_status == "partially_filled":
            return BrokerState.PARTIALLY_FILLED
        if broker_status == "rejected":
            return BrokerState.REJECTED
        if broker_status in {"canceled", "expired"}:
            return BrokerState.CANCELED_EXPIRED
        return BrokerState.WORKING
    by_lifecycle = {
        OrderLifecycleState.SUBMITTED.value: BrokerState.WORKING,
        OrderLifecycleState.PARTIALLY_FILLED.value: BrokerState.PARTIALLY_FILLED,
        OrderLifecycleState.FILLED.value: BrokerState.FILLED,
        OrderLifecycleState.CANCELED.value: BrokerState.CANCELED_EXPIRED,
        OrderLifecycleState.EXPIRED.value: BrokerState.CANCELED_EXPIRED,
        OrderLifecycleState.REJECTED.value: BrokerState.REJECTED,
    }
    return by_lifecycle.get(order_status or "")


def classify_intent(
    *,
    order_status: str,
    broker_order_id: str | None,
    broker_status: str | None,
    attempts: Sequence[AttemptRecord],
    records: Sequence[RecordView],
    attempt_log_registered: bool,
) -> tuple[RecoveryClassification, BrokerState | None, UnresolvedReason | None]:
    """Per-intent classification (closed), evaluated in this order.

    1. the broker shows the order (a broker id is stored): ``found_verified`` in any state,
       or ``unresolved(unmapped_status)`` for an unmapped broker status (broker evidence
       beats the attempt history, which cannot contradict it);
    2. a locally REJECTED order, or a complete history whose class is ``rejected``:
       ``rejected_at_submission``;
    3. the shared ``classify_submission_evidence`` (the verdict the run-time send guard reads):
       ``proven_not_sent`` -> ``not_sent`` (a complete history of pre_connection /
       deadline_expired attempts, or ZERO attempt rows for an attempt-log-registered order in
       pending_submission / submission_failed); ``rejected`` -> ``rejected_at_submission``. A
       legacy order (no attempt rows, not operation-bound) is never ``not_sent`` (TL-4): it is
       classified only by broker evidence;
    4. otherwise the order is not at the broker: ``unresolved(reason)`` when the latest
       recorded classification says so, else ``not_found``. Time, absence evidence, a
       statement and executor termination never change this.
    """

    if broker_order_id:
        state = broker_state_for(order_status, broker_status)
        if state is None:
            return (
                RecoveryClassification.UNRESOLVED,
                None,
                UnresolvedReason.UNMAPPED_STATUS,
            )
        return RecoveryClassification.FOUND_VERIFIED, state, None
    if order_status == OrderLifecycleState.REJECTED.value:
        return RecoveryClassification.REJECTED_AT_SUBMISSION, None, None
    evidence = classify_submission_evidence(
        status=order_status,
        broker_order_id=broker_order_id,
        attempts=attempts,
        attempt_log_registered=attempt_log_registered,
    )
    if evidence is SubmissionEvidence.PROVEN_NOT_SENT:
        return RecoveryClassification.NOT_SENT, None, None
    if evidence is SubmissionEvidence.REJECTED:
        return RecoveryClassification.REJECTED_AT_SUBMISSION, None, None
    latest = latest_classification_record(records)
    if latest is not None and latest.classification is RecoveryClassification.UNRESOLVED:
        return RecoveryClassification.UNRESOLVED, None, latest.unresolved_reason
    return RecoveryClassification.NOT_FOUND, None, None


def latest_classification_record(records: Sequence[RecordView]) -> RecordView | None:
    """The newest ``classification`` record (records arrive ordered oldest first)."""

    newest: RecordView | None = None
    for record in records:
        if record.kind is RecoveryRecordKind.CLASSIFICATION:
            newest = record
    return newest


def absence_evidence_complete(evidence: Sequence[RecordView], grace_seconds: int) -> bool:
    """Items (a)-(d) all confirmed, with two (a) checks at least ``grace_seconds`` apart.

    Evidence only: it never resolves anything, never gates recording a statement and never
    permits a resubmission (round 5).
    """

    a_times = sorted(
        e.observed_at
        for e in evidence
        if e.evidence_item is AbsenceEvidenceItem.A_CLIENT_ORDER_ID_404
        and e.evidence_result is EvidenceResult.CONFIRMED
        and e.observed_at is not None
    )
    if len(a_times) < 2 or (a_times[-1] - a_times[0]).total_seconds() < grace_seconds:
        return False
    for item in (
        AbsenceEvidenceItem.B_LIST_SCAN_NO_MATCH,
        AbsenceEvidenceItem.C_NO_FILL_REFERENCE,
        AbsenceEvidenceItem.D_NO_EXPOSURE_CHANGE,
    ):
        if not any(
            e.evidence_item is item and e.evidence_result is EvidenceResult.CONFIRMED
            for e in evidence
        ):
            return False
    return True


# ---------------------------------------------------------------------------
# Loading (reads: a fixed number of statements, no writes)
# ---------------------------------------------------------------------------

_RECORD_JSON = """
json_build_object(
    'id', r.id, 'kind', r.kind, 'classification', r.classification,
    'broker_state', r.broker_state, 'unresolved_reason', r.unresolved_reason,
    'evidence_item', r.evidence_item, 'evidence_result', r.evidence_result,
    'observed_at', r.observed_at, 'statement', r.statement, 'reference', r.reference,
    'reason', r.reason, 'recorded_by', r.recorded_by, 'created_at', r.created_at
)
"""

_ORDER_COLUMNS = f"""
    po.id AS order_id, po.client_order_id AS client_order_id,
    po.status::text AS order_status, po.broker_order_id AS broker_order_id,
    po.broker_status AS broker_status, st.strategy_id AS order_strategy,
    (SELECT coalesce(json_agg(json_build_object(
                'n', a.attempt_number, 'outcome', a.outcome_class,
                'started_at', a.started_at, 'completed_at', a.completed_at,
                'http_status', a.http_status, 'error_type', a.error_type,
                'broker_message', a.broker_message) ORDER BY a.attempt_number), '[]'::json)
       FROM order_submission_attempts a WHERE a.paper_order_id = po.id) AS attempts,
    po.created_at AS order_created_at,
    (SELECT min(oi.created_at) FROM execution_operation_intents oi
      WHERE oi.paper_order_id = po.id) AS first_intent_at,
    (SELECT coalesce(json_agg({_RECORD_JSON} ORDER BY r.created_at, r.id), '[]'::json)
       FROM recovery_records r WHERE r.paper_order_id = po.id) AS order_records
"""

#: Statement 1: uncertain Jobs with their linked paper_execution runs and orders, plus the
#: unflagged candidate orders (UNKNOWN, or without a broker id and still pending_submission /
#: submission_failed; ``_load_intents`` keeps only the UNESTABLISHED ones through the shared
#: classifier), with attempt logs, the attempt-log registration inputs and recovery records as
#: correlated JSON. ``origin_job_id`` is the Job of the order's run (the flagged Job, or the
#: unflagged originating Job).
_INTENTS_SQL = f"""
WITH flagged AS (
    SELECT j.id, j.job_type, j.status::text AS job_status, j.completed_at,
           {job_strategy_public_id_sql("j")} AS job_strategy
      FROM jobs j
     WHERE j.job_type IN ({", ".join(f"'{t}'" for t in _UNCERTAIN_JOB_TYPES)})
       AND j.outcome_uncertain
       AND (CAST(:sid AS text) IS NULL
            OR {job_strategy_public_id_sql("j")} IS NULL
            OR {job_strategy_public_id_sql("j")} = CAST(:sid AS text))
)
SELECT f.id AS job_id, f.job_type AS job_type, f.job_status AS job_status,
       f.completed_at AS job_completed_at, f.job_strategy AS job_strategy,
       (SELECT count(*) FROM strategy_runs x
         WHERE x.job_id = f.id AND x.run_type = 'paper_execution') AS run_count,
       (SELECT coalesce(json_agg({_RECORD_JSON} ORDER BY r.created_at, r.id), '[]'::json)
          FROM recovery_records r
         WHERE r.job_id = f.id AND r.paper_order_id IS NULL) AS job_records,
       {_ORDER_COLUMNS},
       f.id AS origin_job_id
  FROM flagged f
  LEFT JOIN strategy_runs sr ON sr.job_id = f.id AND sr.run_type = 'paper_execution'
  LEFT JOIN paper_orders po ON po.strategy_run_id = sr.id
       OR EXISTS (SELECT 1 FROM order_submission_attempts oa
                   WHERE oa.paper_order_id = po.id AND oa.executor_job_id = f.id
                     AND sr.id IS NOT NULL)
  LEFT JOIN strategies st ON st.id = sr.strategy_id
 WHERE (CAST(:order_id AS uuid) IS NULL OR po.id = CAST(:order_id AS uuid))
UNION ALL
SELECT NULL::uuid, NULL::text, NULL::text, NULL::timestamptz, NULL::text,
       0::bigint, '[]'::json,
       {_ORDER_COLUMNS},
       sr.job_id AS origin_job_id
  FROM paper_orders po
  JOIN strategy_runs sr ON sr.id = po.strategy_run_id
  JOIN strategies st ON st.id = sr.strategy_id
 WHERE (po.status = 'unknown'
        OR (po.broker_order_id IS NULL AND po.status IN ('pending_submission', 'submission_failed')))
   AND (CAST(:sid AS text) IS NULL OR st.strategy_id = CAST(:sid AS text))
   AND (CAST(:order_id AS uuid) IS NULL OR po.id = CAST(:order_id AS uuid))
   AND NOT EXISTS (SELECT 1 FROM flagged ff
                    JOIN strategy_runs fr ON fr.job_id = ff.id AND fr.run_type = 'paper_execution'
                   WHERE fr.id = po.strategy_run_id)
   AND NOT EXISTS (SELECT 1 FROM flagged ff
                    JOIN order_submission_attempts oa ON oa.executor_job_id = ff.id
                    JOIN strategy_runs fr ON fr.job_id = ff.id AND fr.run_type = 'paper_execution'
                   WHERE oa.paper_order_id = po.id)
"""

#: Statement 2: the reconciliation facts. Per-strategy effect times (NULL group =
#: account-level Jobs), the newest recording time, the newest completed account run and the
#: newest completed standalone strategy run per strategy.
_RECONCILIATION_SQL = f"""
SELECT 'effect' AS k, e.sid AS sid, max(e.completed_at) AS at,
       NULL::uuid AS run_id, NULL::text AS status, NULL::text AS blocks, NULL::text AS reasons
  FROM (
    SELECT j.completed_at AS completed_at, {job_strategy_public_id_sql("j")} AS sid
      FROM jobs j
     WHERE j.job_type IN ({", ".join(f"'{t}'" for t in _EFFECT_JOB_TYPES)})
       AND j.completed_at IS NOT NULL
  ) e
 WHERE (CAST(:sid AS text) IS NULL OR e.sid IS NULL OR e.sid = CAST(:sid AS text))
 GROUP BY e.sid
UNION ALL
SELECT 'record', NULL::text, max(e.created_at), NULL::uuid, NULL::text, NULL::text, NULL::text
  FROM external_broker_activity e
UNION ALL
SELECT * FROM (
    SELECT 'account_run', NULL::text, a.completed_at, a.id, a.status::text,
           a.blocks_execution::text, a.unresolved_reasons::text
      FROM account_reconciliation_runs a
     WHERE a.completed_at IS NOT NULL
     ORDER BY a.completed_at DESC
     LIMIT 1
) account_latest
UNION ALL
SELECT * FROM (
    SELECT DISTINCT ON (st.strategy_id)
           'strategy_run', st.strategy_id, sr.completed_at, sr.id, sr.status::text,
           coalesce((sr.result_summary -> 'blocks_execution')::text, 'null'),
           coalesce((sr.result_summary -> 'unresolved_reasons')::text, '[]')
      FROM strategy_runs sr
      JOIN jobs rj ON rj.id = sr.job_id
      JOIN strategies st ON st.id = sr.strategy_id
     WHERE sr.run_type = 'reconciliation'
       AND sr.trigger_source = '{_STANDALONE_TRIGGER_SOURCE}'
       AND rj.job_type = '{_RECONCILIATION_JOB_TYPE}'
       AND sr.completed_at IS NOT NULL
       AND (CAST(:sid AS text) IS NULL OR st.strategy_id = CAST(:sid AS text))
     ORDER BY st.strategy_id, sr.completed_at DESC
) strategy_latest
"""


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _record_from_json(raw: Mapping[str, Any]) -> RecordView:
    def _enum(enum_cls: Any, value: Any) -> Any:
        return enum_cls(value) if value is not None else None

    return RecordView(
        id=uuid.UUID(str(raw["id"])),
        kind=RecoveryRecordKind(raw["kind"]),
        classification=_enum(RecoveryClassification, raw["classification"]),
        broker_state=_enum(BrokerState, raw["broker_state"]),
        unresolved_reason=_enum(UnresolvedReason, raw["unresolved_reason"]),
        evidence_item=_enum(AbsenceEvidenceItem, raw["evidence_item"]),
        evidence_result=_enum(EvidenceResult, raw["evidence_result"]),
        observed_at=_parse_dt(raw["observed_at"]),
        statement=_enum(BrokerStatementKind, raw["statement"]),
        reference=raw["reference"],
        reason=raw["reason"],
        recorded_by=raw["recorded_by"],
        created_at=_parse_dt(raw["created_at"]),  # type: ignore[arg-type]
    )


def _attempt_from_json(raw: Mapping[str, Any]) -> AttemptRecord:
    outcome = raw.get("outcome")
    return AttemptRecord(
        attempt_number=int(raw["n"]),
        started_at=_parse_dt(raw.get("started_at")),
        completed_at=_parse_dt(raw.get("completed_at")),
        outcome_class=AttemptOutcomeClass(outcome) if outcome is not None else None,
        http_status=raw.get("http_status"),
        error_type=raw.get("error_type"),
        broker_message=raw.get("broker_message"),
    )


def _grace_seconds(grace_seconds: int | None) -> int:
    if grace_seconds is not None:
        return grace_seconds
    return load_settings().execution.recovery_absence_grace_seconds


@dataclass
class _JobFacts:
    job_id: uuid.UUID
    job_type: str
    status: str
    completed_at: datetime | None
    strategy_id: str | None
    run_count: int
    records: list[RecordView]
    order_ids: list[uuid.UUID] = field(default_factory=list)


def _load_intents(
    session: Session,
    strategy_public_id: str | None,
    *,
    grace_seconds: int,
    order_id: uuid.UUID | None = None,
) -> tuple[list[_JobFacts], list[IntentRecovery]]:
    """Statement 1: classify every registered intent and uncertain Job in scope."""

    rows = session.execute(
        text(_INTENTS_SQL), {"sid": strategy_public_id, "order_id": order_id}
    ).mappings()
    jobs: dict[uuid.UUID, _JobFacts] = {}
    intents: list[IntentRecovery] = []
    for row in rows:
        job_id = row["job_id"]
        job: _JobFacts | None = None
        if job_id is not None:
            job = jobs.get(job_id)
            if job is None:
                job = _JobFacts(
                    job_id=job_id,
                    job_type=row["job_type"],
                    status=row["job_status"],
                    completed_at=_parse_dt(row["job_completed_at"]),
                    strategy_id=row["job_strategy"],
                    run_count=int(row["run_count"]),
                    records=[_record_from_json(r) for r in (row["job_records"] or [])],
                )
                jobs[job_id] = job
        if row["order_id"] is None:
            continue
        records = [_record_from_json(r) for r in (row["order_records"] or [])]
        attempts = tuple(_attempt_from_json(a) for a in (row["attempts"] or []))
        registered = attempt_log_registered(
            has_attempts=bool(attempts),
            order_created_at=_parse_dt(row["order_created_at"]),  # type: ignore[arg-type]
            first_intent_created_at=_parse_dt(row["first_intent_at"]),
        )
        classification, broker_state, reason = classify_intent(
            order_status=row["order_status"],
            broker_order_id=row["broker_order_id"],
            broker_status=row["broker_status"],
            attempts=attempts,
            records=records,
            attempt_log_registered=registered,
        )
        if job_id is None and row["order_status"] != "unknown":
            # An unflagged order is an uncertain outcome only when the shared classifier leaves
            # it UNESTABLISHED (20.1-17); a proven-not-sent or rejected pending/failed order
            # (e.g. the registered_unsent intent of a paused operation) creates no gate.
            if classification in _ESTABLISHED_CLASSIFICATIONS:
                continue
        evidence = tuple(r for r in records if r.kind is RecoveryRecordKind.ABSENCE_EVIDENCE)
        statement = next(
            (r for r in records if r.kind is RecoveryRecordKind.BROKER_STATEMENT), None
        )
        if job is not None:
            job.order_ids.append(row["order_id"])
        intents.append(
            IntentRecovery(
                intent_id=row["order_id"],
                # The flagged Job, or (unflagged branch) the originating Job of the order's run.
                job_id=row["origin_job_id"],
                strategy_id=row["order_strategy"],
                client_order_id=row["client_order_id"],
                order_status=row["order_status"],
                broker_order_id=row["broker_order_id"],
                broker_status=row["broker_status"],
                classification=classification,
                broker_state=broker_state,
                unresolved_reason=reason,
                attempts=attempts,
                absence_evidence=evidence,
                statement=statement,
                absence_evidence_complete=absence_evidence_complete(evidence, grace_seconds),
            )
        )

    # Job-level entries: nothing_submitted / execution_path_unproven for Jobs without orders.
    for job in jobs.values():
        if job.order_ids:
            continue
        if job.job_type == PAPER_SESSION_JOB_TYPE and job.run_count > 0:
            # A linked paper_execution run with ZERO order rows proves nothing (E-5).
            classification = RecoveryClassification.UNRESOLVED
            job_reason: UnresolvedReason | None = UnresolvedReason.EXECUTION_PATH_UNPROVEN
        else:
            classification = RecoveryClassification.NOTHING_SUBMITTED
            job_reason = None
        intents.append(
            IntentRecovery(
                intent_id=None,
                job_id=job.job_id,
                strategy_id=job.strategy_id,
                client_order_id=None,
                order_status=None,
                broker_order_id=None,
                broker_status=None,
                classification=classification,
                broker_state=None,
                unresolved_reason=job_reason,
                attempts=(),
                absence_evidence=(),
                statement=None,
                absence_evidence_complete=False,
            )
        )
    return list(jobs.values()), intents


@dataclass(frozen=True)
class _RunFact:
    run_id: uuid.UUID
    strategy_id: str | None
    status: str
    completed_at: datetime | None
    blocks: str | None
    reasons: str | None


@dataclass
class _ReconciliationFacts:
    effects: dict[str | None, datetime] = field(default_factory=dict)
    record_effect: datetime | None = None
    account_run: _RunFact | None = None
    strategy_runs: dict[str, _RunFact] = field(default_factory=dict)


def _load_reconciliation_facts(
    session: Session, strategy_public_id: str | None
) -> _ReconciliationFacts:
    """Statement 2: boundaries and the newest standalone runs of both tables."""

    facts = _ReconciliationFacts()
    for row in session.execute(text(_RECONCILIATION_SQL), {"sid": strategy_public_id}).mappings():
        kind = row["k"]
        at = _parse_dt(row["at"])
        if kind == "effect":
            if at is not None:
                facts.effects[row["sid"]] = at
        elif kind == "record":
            facts.record_effect = at
        else:
            run = _RunFact(
                run_id=row["run_id"],
                strategy_id=row["sid"],
                status=row["status"],
                completed_at=at,
                blocks=row["blocks"],
                reasons=row["reasons"],
            )
            if kind == "account_run":
                facts.account_run = run
            elif row["sid"] is not None:
                facts.strategy_runs[row["sid"]] = run
    return facts


def _json_list(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    import json

    parsed = json.loads(raw)
    return tuple(str(item) for item in parsed) if isinstance(parsed, list) else ("unparseable",)


def _qualifying(run: _RunFact, scope: Literal["account", "strategy"]) -> QualifyingReconciliation:
    if scope == "account":
        # An account run is blocking unless its column is exactly false (fail closed).
        blocks = run.blocks != "false"
    else:
        # A strategy run counts as blocking unless it SUCCEEDED and its summary says
        # blocks_execution is exactly false (fail closed; same rule as 20.1-08).
        blocks = run.status != "succeeded" or run.blocks != "false"
    return QualifyingReconciliation(
        scope=scope,
        run_id=run.run_id,
        strategy_id=run.strategy_id,
        status=run.status,
        completed_at=run.completed_at,
        blocks_execution=blocks,
        unresolved_reasons=_json_list(run.reasons),
    )


def _evaluate(
    subject: str | None,
    *,
    jobs: Sequence[_JobFacts],
    intents: Sequence[IntentRecovery],
    facts: _ReconciliationFacts,
    as_of: datetime,
) -> RecoveryStatus:
    """The predicate for one subject (a strategy id, or None = the account level).

    A strategy subject is affected by its own items and every account-level item; the
    account subject only by account-level items and needs an ACCOUNT run.
    """

    def relevant_strategy(item_strategy: str | None) -> bool:
        if subject is None:
            return item_strategy is None
        return item_strategy is None or item_strategy == subject

    own_jobs = tuple(j for j in jobs if relevant_strategy(j.strategy_id))
    own_intents = tuple(i for i in intents if relevant_strategy(i.strategy_id))
    job_summaries = tuple(
        UncertainJob(
            job_id=j.job_id,
            job_type=j.job_type,
            status=j.status,
            completed_at=j.completed_at,
            strategy_id=j.strategy_id,
            execution_run_count=j.run_count,
            intent_count=len(j.order_ids),
            established=all(i.established for i in own_intents if i.job_id == j.job_id),
        )
        for j in own_jobs
    )

    # Boundary: the latest broker-touching effect relevant to the subject.
    effect_times = [
        t for sid, t in facts.effects.items() if subject is None or sid is None or sid == subject
    ]
    boundary_candidates = [t for t in (*effect_times, facts.record_effect) if t is not None]
    boundary = max(boundary_candidates) if boundary_candidates else None

    # Newest qualifying run: an account run always qualifies; a strategy run only for its
    # own strategy.
    candidates: list[QualifyingReconciliation] = []
    if facts.account_run is not None and facts.account_run.completed_at is not None:
        candidates.append(_qualifying(facts.account_run, "account"))
    if subject is not None and subject in facts.strategy_runs:
        strategy_run = facts.strategy_runs[subject]
        if strategy_run.completed_at is not None:
            candidates.append(_qualifying(strategy_run, "strategy"))
    newest = (
        max(candidates, key=lambda c: c.completed_at or datetime.min.replace(tzinfo=as_of.tzinfo))
        if candidates
        else None
    )
    after_boundary = (
        newest
        if (
            newest is not None
            and newest.completed_at is not None
            and (boundary is None or newest.completed_at > boundary)
        )
        else None
    )

    gate: GateCode | None
    if not own_jobs and not own_intents:
        gate = None  # no uncertain outcome at all: nothing to resolve
    elif any(i.blocking for i in own_intents):
        gate = GateCode.OUTCOME_UNRESOLVED
    elif after_boundary is None:
        gate = GateCode.RECONCILIATION_REQUIRED
    elif not after_boundary.is_clean:
        gate = GateCode.RECONCILIATION_NOT_CLEAN
    else:
        gate = None
    return RecoveryStatus(
        strategy_id=subject,
        resolved=gate is None,
        gate_code=gate,
        intents=own_intents,
        jobs=job_summaries,
        reconciliation=after_boundary,
        boundary=boundary,
        as_of=as_of,
    )


# ---------------------------------------------------------------------------
# Public reads
# ---------------------------------------------------------------------------


def strategy_recovery_status(
    session: Session,
    strategy_public_id: str,
    *,
    operation_view: OperationView | None = None,
    now: datetime | None = None,
    grace_seconds: int | None = None,
) -> RecoveryStatus:
    """The predicate for one strategy. Read-only; exactly two statements.

    Uncertain Jobs and unestablished intents of the strategy count, as do all account-level
    uncertain Jobs. With none, the result is resolved without any reconciliation.
    """

    as_of = now or clock.now_utc()
    jobs, intents = _load_intents(
        session, strategy_public_id, grace_seconds=_grace_seconds(grace_seconds)
    )
    facts = _load_reconciliation_facts(session, strategy_public_id)
    return _evaluate(
        strategy_public_id,
        jobs=jobs,
        intents=intents,
        facts=facts,
        as_of=as_of,
    )


def account_recovery_status(
    session: Session,
    *,
    operation_view: OperationView | None = None,
    now: datetime | None = None,
    grace_seconds: int | None = None,
) -> AccountRecoveryStatus:
    """The predicate for every strategy plus the account level. Read-only; two statements."""

    as_of = now or clock.now_utc()
    jobs, intents = _load_intents(session, None, grace_seconds=_grace_seconds(grace_seconds))
    facts = _load_reconciliation_facts(session, None)
    subjects: list[str | None] = list(
        sorted(
            {j.strategy_id for j in jobs if j.strategy_id is not None}
            | {i.strategy_id for i in intents if i.strategy_id is not None}
        )
    )
    if any(j.strategy_id is None for j in jobs):
        subjects.append(None)
    statuses = tuple(
        _evaluate(
            subject,
            jobs=jobs,
            intents=intents,
            facts=facts,
            as_of=as_of,
        )
        for subject in subjects
    )
    return AccountRecoveryStatus(
        resolved=all(s.resolved for s in statuses), subjects=statuses, as_of=as_of
    )


def outcome_issue_inputs(
    session: Session,
    *,
    operation_view: OperationView | None = None,
    now: datetime | None = None,
    grace_seconds: int | None = None,
) -> list[OutcomeIssueInput]:
    """Closed issue-projection rows from the SAME predicate; empty when everything is resolved.

    * ``outcome_uncertain_unverified``: an unestablished intent with no evidence and no
      unresolved reason recorded yet, or an uncertain Job whose intents are established and
      that awaits the fresh clean standalone reconciliation (the 29 Sep fixture);
    * ``outcome_uncertain_unresolved``: an unestablished intent that has evidence, an
      unresolved reason or a statement and is still not found, or any item while the newest
      qualifying reconciliation is blocking.
    """

    status = account_recovery_status(
        session, operation_view=operation_view, now=now, grace_seconds=grace_seconds
    )
    rows: dict[tuple[str | None, uuid.UUID | None, uuid.UUID | None], OutcomeIssueInput] = {}
    for subject in status.subjects:
        if subject.resolved:
            continue
        for intent in subject.intents:
            if intent.blocking:
                kind = (
                    UncertainOutcomeIssue.OUTCOME_UNCERTAIN_UNRESOLVED
                    if intent.has_evidence
                    else UncertainOutcomeIssue.OUTCOME_UNCERTAIN_UNVERIFIED
                )
            elif subject.gate_code is GateCode.RECONCILIATION_NOT_CLEAN:
                kind = UncertainOutcomeIssue.OUTCOME_UNCERTAIN_UNRESOLVED
            else:
                kind = UncertainOutcomeIssue.OUTCOME_UNCERTAIN_UNVERIFIED
            key = (intent.strategy_id, intent.job_id, intent.intent_id)
            existing = rows.get(key)
            if existing is not None and (
                existing.kind is UncertainOutcomeIssue.OUTCOME_UNCERTAIN_UNRESOLVED
            ):
                continue
            rows[key] = OutcomeIssueInput(
                kind=kind,
                strategy_public_id=intent.strategy_id,
                job_id=intent.job_id,
                intent_id=intent.intent_id,
            )
    return list(rows.values())


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _record_dict(record: RecordView) -> dict[str, Any]:
    return {
        "item": record.evidence_item.value if record.evidence_item else None,
        "result": record.evidence_result.value if record.evidence_result else None,
        "observed_at": _iso(record.observed_at),
    }


def _intent_dict(
    intent: IntentRecovery,
    *,
    session: Session,
    operation_view: OperationView,
    prefetched: Mapping[uuid.UUID, OperationState] | None = None,
) -> dict[str, Any]:
    operation_state: OperationState = "none"
    if intent.intent_id is not None:
        if prefetched is not None:
            operation_state = prefetched.get(intent.intent_id, "none")
        else:
            operation_state = operation_view.state_for_intent(session, intent.intent_id)
    return {
        "intent_id": str(intent.intent_id) if intent.intent_id is not None else None,
        "client_order_id": intent.client_order_id,
        "classification": intent.classification.value,
        "broker_state": intent.broker_state.value if intent.broker_state else None,
        "unresolved_reason": intent.unresolved_reason.value if intent.unresolved_reason else None,
        "absence_evidence": [_record_dict(e) for e in intent.absence_evidence],
        "absence_evidence_complete": intent.absence_evidence_complete,
        "statement": (
            {
                "statement": intent.statement.statement.value
                if intent.statement.statement
                else None,
                "reference": intent.statement.reference,
                "created_at": _iso(intent.statement.created_at),
            }
            if intent.statement is not None
            else None
        ),
        "resubmission_permitted": resubmission_permitted(intent),
        "resubmission_reason": RESUBMISSION_UNAVAILABLE_REASON,
        "operation_state": operation_state,
        "blocking": intent.blocking,
    }


def _evidence_package(intent: IntentRecovery, grace_seconds: int) -> dict[str, Any]:
    by_item: dict[AbsenceEvidenceItem, list[dict[str, Any]]] = {i: [] for i in AbsenceEvidenceItem}
    for record in intent.absence_evidence:
        if record.evidence_item is not None:
            by_item[record.evidence_item].append(_record_dict(record))
    return {
        "intent_id": str(intent.intent_id),
        "client_order_id": intent.client_order_id,
        "attempts": [
            {
                "attempt_number": a.attempt_number,
                "started_at": _iso(a.started_at),
                "completed_at": _iso(a.completed_at),
                "outcome_class": a.outcome_class.value if a.outcome_class else None,
                "http_status": a.http_status,
            }
            for a in intent.attempts
        ],
        "lookup_results": by_item[AbsenceEvidenceItem.A_CLIENT_ORDER_ID_404],
        "scan_results": by_item[AbsenceEvidenceItem.B_LIST_SCAN_NO_MATCH],
        "fill_reference_results": by_item[AbsenceEvidenceItem.C_NO_FILL_REFERENCE],
        "exposure_results": by_item[AbsenceEvidenceItem.D_NO_EXPOSURE_CHANGE],
        "grace_period_seconds": grace_seconds,
        "complete": intent.absence_evidence_complete,
        "statement_recorded": intent.statement is not None,
    }


def get_job_recovery(
    session: Session,
    job_id: uuid.UUID,
    *,
    operation_view: OperationView | None = None,
    now: datetime | None = None,
    grace_seconds: int | None = None,
) -> dict[str, Any]:
    """R3: the recovery view of one Job (plain dicts, read-only, three statements).

    Raises ``LookupError`` for an unknown Job. A Job that is not an uncertain
    paper-session / broker-order-sync Job has no intents and reads as resolved. Available
    before and after any execution operation ends (the operation is never an input).
    """

    view = operation_view or DbOperationView()
    grace = _grace_seconds(grace_seconds)
    # SAF-05: the Job's strategy is resolved in this same statement (payload strategy_id, else
    # the Continue payload's operation, else the run-time operation link).
    job = session.execute(
        select(
            Job.id,
            Job.job_type,
            literal_column(job_strategy_public_id_sql("jobs"), String).label("job_strategy"),
        ).where(Job.id == job_id)
    ).one_or_none()
    if job is None:
        raise LookupError(f"Job '{job_id}' was not found.")
    strategy_id = job.job_strategy if isinstance(job.job_strategy, str) else None

    if strategy_id is not None:
        status = strategy_recovery_status(
            session, strategy_id, operation_view=view, now=now, grace_seconds=grace
        )
    else:
        account = account_recovery_status(
            session, operation_view=view, now=now, grace_seconds=grace
        )
        # The account subject, when present, is the stricter view of account-level Jobs.
        status = next(
            (s for s in account.subjects if s.strategy_id is None),
            RecoveryStatus(
                strategy_id=None,
                resolved=True,
                gate_code=None,
                intents=(),
                jobs=(),
                reconciliation=None,
                boundary=None,
                as_of=account.as_of,
            ),
        )

    own = tuple(i for i in status.intents if i.job_id == job_id)
    prefetched: dict[uuid.UUID, OperationState] | None = None
    batch = getattr(view, "states_for_intents", None)
    if callable(batch):
        prefetched = batch(session, [i.intent_id for i in own if i.intent_id is not None])
    job_is_uncertain = any(j.job_id == job_id for j in status.jobs)
    gate: GateCode | None
    if any(i.blocking for i in own):
        # An unestablished intent blocks whether or not its Job is flagged outcome_uncertain
        # (20.1-17: the unflagged origin Job of a legacy / unparked ambiguous order).
        gate = GateCode.OUTCOME_UNRESOLVED
    elif not job_is_uncertain:
        gate = None
    else:
        gate = (
            status.gate_code
            if status.gate_code
            in (GateCode.RECONCILIATION_REQUIRED, GateCode.RECONCILIATION_NOT_CLEAN)
            else None
        )
    return {
        "job_id": str(job_id),
        "job_type": job.job_type,
        "strategy_id": strategy_id,
        "resolved": gate is None,
        "gate_code": gate.value if gate else None,
        "intents": [
            _intent_dict(i, session=session, operation_view=view, prefetched=prefetched)
            for i in own
        ],
        "evidence_package": [_evidence_package(i, grace) for i in own if i.on_missing_order_path],
        "as_of": status.as_of.isoformat(),
    }


# ---------------------------------------------------------------------------
# Writers (append-only; no update or delete of recovery records exists here)
# ---------------------------------------------------------------------------


def _append_record(session: Session, **columns: Any) -> RecoveryRecord:
    record = RecoveryRecord(**columns)
    session.add(record)
    session.flush()
    return record


def _validate_text(value: object, field_name: Literal["reference", "reason"]) -> str:
    if not isinstance(value, str):
        raise InvalidBrokerStatementError(field_name)
    trimmed = value.strip()
    if not trimmed or len(trimmed) > BROKER_STATEMENT_MAX_CHARS or "\x00" in trimmed:
        raise InvalidBrokerStatementError(field_name)
    return trimmed


def load_intent(session: Session, intent_id: uuid.UUID) -> IntentRecovery:
    """The recovery state of one registered intent (``IntentNotFoundError`` when absent).

    Raises ``IntentNotOnMissingOrderPathError`` for an order that exists but is not an
    ambiguous registered intent (not in an uncertain Job's run, not UNKNOWN, not a crash
    leftover).
    """

    if session.get(PaperOrder, intent_id) is None:
        raise IntentNotFoundError(intent_id)
    _jobs, intents = _load_intents(
        session, None, grace_seconds=_grace_seconds(None), order_id=intent_id
    )
    for intent in intents:
        if intent.intent_id == intent_id:
            return intent
    raise IntentNotOnMissingOrderPathError(intent_id)


def record_broker_statement(
    session: Session,
    intent_id: uuid.UUID,
    statement: str,
    reference: str,
    reason: str,
    actor: str,
) -> BrokerStatementResult:
    """Append the (single) broker statement of an intent: audited EVIDENCE only.

    The intent must be on the missing-order path (unestablished and not matched at the
    broker). The statement never changes the classification, never resolves the intent and
    never permits a resend (round 5, 2026-10-04): an intent resolves only when the broker
    shows it or its own attempt history proves it not sent. Idempotent: the same statement
    and reference returns ``changed=False``; a different statement or reference for the
    same intent raises ``StatementConflictError``.
    """

    try:
        kind = BrokerStatementKind(statement)
    except ValueError as exc:
        raise InvalidBrokerStatementError("statement") from exc
    clean_reference = _validate_text(reference, "reference")
    clean_reason = _validate_text(reason, "reason")

    intent = load_intent(session, intent_id)
    if not intent.on_missing_order_path:
        raise IntentNotOnMissingOrderPathError(intent_id)
    existing = intent.statement
    if existing is not None:
        if existing.statement is kind and existing.reference == clean_reference:
            return BrokerStatementResult(
                intent_id=intent_id,
                strategy_id=intent.strategy_id or "",
                statement=kind,
                changed=False,
                classification=intent.classification,
                record_id=existing.id,
            )
        raise StatementConflictError(
            intent_id, existing.statement.value if existing.statement else "unknown"
        )
    try:
        with session.begin_nested():
            record = _append_record(
                session,
                kind=RecoveryRecordKind.BROKER_STATEMENT.value,
                job_id=intent.job_id,
                paper_order_id=intent_id,
                strategy_public_id=intent.strategy_id,
                statement=kind.value,
                reference=clean_reference,
                reason=clean_reason,
                recorded_by=actor,
            )
    except IntegrityError:
        # A concurrent writer recorded the statement first: re-read and decide.
        winner = load_intent(session, intent_id).statement
        if winner is not None and winner.statement is kind and winner.reference == clean_reference:
            return BrokerStatementResult(
                intent_id=intent_id,
                strategy_id=intent.strategy_id or "",
                statement=kind,
                changed=False,
                classification=intent.classification,
                record_id=winner.id,
            )
        raise StatementConflictError(
            intent_id,
            winner.statement.value if winner is not None and winner.statement else "unknown",
        ) from None
    return BrokerStatementResult(
        intent_id=intent_id,
        strategy_id=intent.strategy_id or "",
        statement=kind,
        changed=True,
        classification=intent.classification,
        record_id=record.id,
    )


_HTTP_STATUS_PATTERN = re.compile(r"status (\d{3})")


def _lookup_failure_reason(exc: BaseException) -> UnresolvedReason:
    """Map a failed client-order-id lookup to its closed unresolved reason."""

    match = _HTTP_STATUS_PATTERN.search(str(exc))
    if match is not None and match.group(1).startswith("4") and match.group(1) != "404":
        # A 4xx other than 404 is an undocumented not-found shape, never absence evidence.
        return UnresolvedReason.UNDOCUMENTED_NOT_FOUND_RESPONSE
    return UnresolvedReason.LOOKUP_ERROR


def _lookup_mismatch(order: PaperOrder, ticker: str, snapshot: Any) -> str | None:
    """Why a looked-up broker record is NOT evidence for the local intent (D-07), or ``None``."""

    raw = snapshot.raw_payload
    if snapshot.client_order_id != order.client_order_id:
        return "client_order_id"
    if snapshot.symbol != ticker:
        return "symbol"
    if snapshot.side.value != order.side:
        return "side"
    if raw.get("qty") is None or Decimal(str(snapshot.quantity)) != Decimal(str(order.quantity)):
        return "quantity"
    if str(raw.get("type") or "") != order.order_type:
        return "type"
    created = _parse_dt(raw.get("created_at")) if raw.get("created_at") else None
    registered = order.created_at
    if registered is not None:
        if created is None:
            return "created_at"
        if registered.tzinfo is None:
            registered = registered.replace(tzinfo=created.tzinfo)
        if created < registered:
            return "created_at"
    return None


def assess_unestablished_intents(
    session: Session,
    *,
    broker_client: Any,
    broker_orders: Sequence[Any],
    broker_fills: Sequence[Any],
    broker_positions: Sequence[Any],
    apply_broker_order: Callable[[Any, PaperOrder], None],
    strategy_public_id: str | None = None,
    now: datetime | None = None,
    grace_seconds: int | None = None,
) -> int:
    """Evidence collection inside a broker-sync pass (path 1; never called from a read).

    For every unestablished intent in scope: one ``get_order_by_client_order_id`` lookup
    (absence item a), then the already-loaded ``status=all`` listing, the fills and the
    positions for items b, c and d. A lookup that FINDS the order and verifies it (D-07) is
    applied through ``apply_broker_order`` (the legal-transition sync path) and recorded as
    ``found_verified``; nothing is ever re-POSTed. Absence evidence is appended for a
    never-found order but never changes its classification. A ``not_sent`` intent left
    ``UNKNOWN`` (a takeover) is returned to the retryable ``submission_failed`` state, the
    only way an UNKNOWN intent becomes sendable. Also appends the Job-level classification
    once per uncertain Job. Returns the number of records appended.
    """

    from trading_platform.services.execution.transition import (
        OrderTransitionRequest,
        apply_order_transition,
    )

    observed = now or clock.now_utc()
    grace = _grace_seconds(grace_seconds)
    appended = 0
    jobs, intents = _load_intents(session, strategy_public_id, grace_seconds=grace)

    def append(**columns: Any) -> None:
        nonlocal appended
        _append_record(session, recorded_by=RECOVERY_RECORD_ACTOR_SYNC, **columns)
        appended += 1

    # Job-level classification, once per uncertain Job.
    for job in jobs:
        if job.records or job.order_ids:
            continue
        job_level = next(i for i in intents if i.job_id == job.job_id and i.intent_id is None)
        append(
            kind=RecoveryRecordKind.CLASSIFICATION.value,
            job_id=job.job_id,
            strategy_public_id=job.strategy_id,
            classification=job_level.classification.value,
            unresolved_reason=(
                job_level.unresolved_reason.value if job_level.unresolved_reason else None
            ),
        )

    scanned_client_ids = {getattr(o, "client_order_id", None) for o in broker_orders}
    scanned_broker_ids = {getattr(o, "broker_order_id", None) for o in broker_orders}
    fills_by_order = {getattr(f, "broker_order_id", None) for f in broker_fills}
    position_qty: dict[str, Decimal] = {}
    for position in broker_positions:
        signed = Decimal(str(getattr(position, "quantity", 0)))
        position_qty[str(getattr(position, "symbol", ""))] = (
            position_qty.get(str(getattr(position, "symbol", "")), Decimal("0")) + signed
        )

    for intent in intents:
        if intent.intent_id is None:
            continue
        order = session.get(PaperOrder, intent.intent_id)
        if order is None:
            continue
        # Liveness: a proven-not-sent intent parked in UNKNOWN becomes retryable.
        if (
            intent.classification is RecoveryClassification.NOT_SENT
            and order.status is OrderLifecycleState.UNKNOWN
            and not order.broker_order_id
        ):
            apply_order_transition(
                order.id,
                OrderTransitionRequest(
                    strategy_run_id=order.strategy_run_id,
                    event_type=OrderTransitionEventType.SUBMISSION_FAILED,
                    details={"recovery": "not_sent_proven_by_attempt_history"},
                    event_at=observed,
                ),
                session=session,
            )
            append(
                kind=RecoveryRecordKind.CLASSIFICATION.value,
                job_id=intent.job_id,
                paper_order_id=order.id,
                strategy_public_id=intent.strategy_id,
                classification=RecoveryClassification.NOT_SENT.value,
            )
            continue
        if not intent.blocking:
            continue
        if intent.broker_order_id is not None:
            # Matched at the broker but with an unmapped status: nothing to look up.
            continue

        ticker = order.symbol_ref.ticker if order.symbol_ref is not None else ""
        a_result = EvidenceResult.CONFIRMED
        unresolved: UnresolvedReason | None = None
        found = False
        try:
            snapshot = broker_client.get_order_by_client_order_id(order.client_order_id)
        except Exception as exc:  # broker boundary: any failure is lookup evidence, never absence
            snapshot = None
            a_result = EvidenceResult.ERROR
            unresolved = _lookup_failure_reason(exc)
        else:
            if snapshot is not None:
                mismatch = _lookup_mismatch(order, ticker, snapshot)
                if mismatch is not None:
                    a_result = EvidenceResult.NOT_CONFIRMED
                    unresolved = UnresolvedReason.ID_MISMATCH
                else:
                    apply_broker_order(snapshot, order)
                    found = True
                    a_result = EvidenceResult.NOT_CONFIRMED
        append(
            kind=RecoveryRecordKind.ABSENCE_EVIDENCE.value,
            job_id=intent.job_id,
            paper_order_id=order.id,
            strategy_public_id=intent.strategy_id,
            evidence_item=AbsenceEvidenceItem.A_CLIENT_ORDER_ID_404.value,
            evidence_result=a_result.value,
            observed_at=observed,
        )
        if found:
            state = broker_state_for(order.status.value, order.broker_status)
            if state is None:
                append(
                    kind=RecoveryRecordKind.CLASSIFICATION.value,
                    job_id=intent.job_id,
                    paper_order_id=order.id,
                    strategy_public_id=intent.strategy_id,
                    classification=RecoveryClassification.UNRESOLVED.value,
                    unresolved_reason=UnresolvedReason.UNMAPPED_STATUS.value,
                )
            else:
                append(
                    kind=RecoveryRecordKind.CLASSIFICATION.value,
                    job_id=intent.job_id,
                    paper_order_id=order.id,
                    strategy_public_id=intent.strategy_id,
                    classification=RecoveryClassification.FOUND_VERIFIED.value,
                    broker_state=state.value,
                )
            continue

        b_confirmed = (
            order.client_order_id not in scanned_client_ids
            and (order.broker_order_id or "") not in scanned_broker_ids
        )
        c_confirmed = b_confirmed and not (
            order.broker_order_id and order.broker_order_id in fills_by_order
        )
        local_net = _local_net_fills(session, order)
        d_confirmed = position_qty.get(ticker, Decimal("0")) == local_net
        for item, confirmed in (
            (AbsenceEvidenceItem.B_LIST_SCAN_NO_MATCH, b_confirmed),
            (AbsenceEvidenceItem.C_NO_FILL_REFERENCE, c_confirmed),
            (AbsenceEvidenceItem.D_NO_EXPOSURE_CHANGE, d_confirmed),
        ):
            append(
                kind=RecoveryRecordKind.ABSENCE_EVIDENCE.value,
                job_id=intent.job_id,
                paper_order_id=order.id,
                strategy_public_id=intent.strategy_id,
                evidence_item=item.value,
                evidence_result=(
                    EvidenceResult.CONFIRMED if confirmed else EvidenceResult.NOT_CONFIRMED
                ).value,
                observed_at=observed,
            )
        # Record the classification only when it differs from the latest recorded one.
        target = unresolved
        latest = None
        existing_records = session.execute(
            select(RecoveryRecord)
            .where(
                RecoveryRecord.paper_order_id == order.id,
                RecoveryRecord.kind == RecoveryRecordKind.CLASSIFICATION.value,
            )
            .order_by(RecoveryRecord.created_at.desc(), RecoveryRecord.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        if existing_records is not None:
            latest = (existing_records.classification, existing_records.unresolved_reason)
        wanted = (
            (RecoveryClassification.UNRESOLVED.value, target.value)
            if target is not None
            else (RecoveryClassification.NOT_FOUND.value, None)
        )
        if latest != wanted:
            append(
                kind=RecoveryRecordKind.CLASSIFICATION.value,
                job_id=intent.job_id,
                paper_order_id=order.id,
                strategy_public_id=intent.strategy_id,
                classification=wanted[0],
                unresolved_reason=wanted[1],
            )
    return appended


def _local_net_fills(session: Session, order: PaperOrder) -> Decimal:
    """Signed net quantity of recorded fills in the order's symbol (all strategies)."""

    from sqlalchemy import case, func

    from trading_platform.db.models import PaperFill

    net: Any = session.execute(
        select(
            func.coalesce(
                func.sum(
                    case((PaperFill.side == "buy", PaperFill.quantity), else_=-PaperFill.quantity)
                ),
                0,
            )
        ).where(PaperFill.symbol_id == order.symbol_id)
    ).scalar_one()
    return Decimal(str(net))


def record_scan_failure_for_unestablished(
    session: Session,
    reason: UnresolvedReason,
    *,
    strategy_public_id: str | None = None,
) -> int:
    """Record ``unresolved(reason)`` for every unestablished order intent (e.g. a page cap).

    Used by a sync pass whose broker listing failed before evidence could be collected.
    """

    _jobs, intents = _load_intents(session, strategy_public_id, grace_seconds=_grace_seconds(None))
    count = 0
    for intent in intents:
        if intent.intent_id is None or not intent.on_missing_order_path:
            continue
        _append_record(
            session,
            kind=RecoveryRecordKind.CLASSIFICATION.value,
            job_id=intent.job_id,
            paper_order_id=intent.intent_id,
            strategy_public_id=intent.strategy_id,
            classification=RecoveryClassification.UNRESOLVED.value,
            unresolved_reason=reason.value,
            recorded_by=RECOVERY_RECORD_ACTOR_SYNC,
        )
        count += 1
    return count


__all__ = [
    "BROKER_STATEMENT_MAX_CHARS",
    "RECOVERY_GATE_CODES",
    "REQUIRED_JOB_TYPE",
    "RESUBMISSION_UNAVAILABLE_REASON",
    "UNRESOLVED_REASON_ISSUE",
    "AbsenceEvidenceItem",
    "AccountRecoveryStatus",
    "BrokerState",
    "BrokerStatementKind",
    "BrokerStatementResult",
    "EvidenceResult",
    "GateCode",
    "IntentNotFoundError",
    "IntentNotOnMissingOrderPathError",
    "IntentRecovery",
    "InvalidBrokerStatementError",
    "DbOperationView",
    "NullOperationView",
    "OperationView",
    "OutcomeIssueInput",
    "QualifyingReconciliation",
    "RecordView",
    "RecoveryClassification",
    "RecoveryStatus",
    "StatementConflictError",
    "UncertainJob",
    "UncertainOutcomeIssue",
    "UnresolvedReason",
    "absence_evidence_complete",
    "account_recovery_status",
    "assess_unestablished_intents",
    "broker_state_for",
    "classify_intent",
    "get_job_recovery",
    "load_intent",
    "outcome_issue_inputs",
    "record_broker_statement",
    "record_scan_failure_for_unestablished",
    "resubmission_permitted",
    "strategy_recovery_status",
]
