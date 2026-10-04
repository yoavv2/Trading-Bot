"""Account checks A1-A7 for seeding, handover and release of the paper owner (PAPER-02, D-04).

The single paper-account owner may be seeded (none -> B), handed over (A -> B) or
released (A -> none) only when the account is provably quiet and clean. Each check is
evaluated from PERSISTED evidence only: no broker call, no write, a fixed number of
statements whatever the size of the history.

- A1 no broker-touching Job queued or running (any scope) and no open execution operation
  of any strategy (effective state, 20.1-11);
- A2 every broker order is terminal per the latest completed account reconciliation;
- A3 the account is flat: 0 open positions in the latest broker-observed snapshot and no
  unexplained exposure in the latest completed account reconciliation;
- A4 no unrecognized items in the latest completed account reconciliation;
- A5 no unresolved uncertain outcome for any strategy or the account level (the 20.1-10
  predicate; a recorded broker statement never satisfies it);
- A6 a clean account reconciliation completed after the latest broker effect (20.1-09);
- A7 (handover and release only) the outgoing owner is disabled.

Absent evidence (no completed account reconciliation, no broker-observed snapshot) fails
closed. Because a fresh install lacks both, such an A2/A3/A4 failure is SUBORDINATE to A6
when A6 is also failing: it is listed but never the named check (05 E1: "M6 before M5 ->
check_failed:A6").
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_platform.core import clock
from trading_platform.core.settings import Settings, load_settings
from trading_platform.db.models import (
    OPEN_OPERATION_STATES,
    AccountReconciliationRun,
    ActivePaperStrategy,
    ExecutionOperation,
    Job,
    JobStatus,
    Strategy,
    StrategyStatus,
)
from trading_platform.db.models.active_paper_strategy import ACTIVE_PAPER_STRATEGY_SINGLETON_ID
from trading_platform.services.account_baseline import latest_broker_observed_account_snapshot
from trading_platform.services.broker_jobs import BROKER_TOUCHING_JOB_TYPES
from trading_platform.services.calendar_facts import load_calendar_window
from trading_platform.services.execution.operations import effective_state, live_job_ids
from trading_platform.services.reconciliation.latest import (
    latest_broker_effect_at,
    latest_standalone_reconciliation,
)
from trading_platform.services.recovery import account_recovery_status

#: Upper bound of evidence references returned per check (the response stays bounded).
MAX_EVIDENCE_REFS = 20


class CheckId(StrEnum):
    """Closed set of account checks, in evaluation order."""

    A1 = "A1"
    A2 = "A2"
    A3 = "A3"
    A4 = "A4"
    A5 = "A5"
    A6 = "A6"
    A7 = "A7"


class CheckReason(StrEnum):
    """Closed set of failure reason codes (a passing check has no reason)."""

    BROKER_JOB_ACTIVE = "broker_job_active"
    OPEN_OPERATION = "open_operation"
    NO_ACCOUNT_RECONCILIATION = "no_account_reconciliation"
    NON_TERMINAL_ORDERS = "non_terminal_orders"
    NO_BROKER_OBSERVED_SNAPSHOT = "no_broker_observed_snapshot"
    OPEN_POSITIONS = "open_positions"
    UNEXPLAINED_EXPOSURE = "unexplained_exposure"
    UNRECOGNIZED_ITEMS = "unrecognized_items"
    UNRESOLVED_OUTCOME = "unresolved_outcome"
    RECONCILIATION_STALE = "reconciliation_stale"
    RECONCILIATION_NOT_CLEAN = "reconciliation_not_clean"
    OUTGOING_OWNER_ENABLED = "outgoing_owner_enabled"


class EvidenceKind(StrEnum):
    """Closed set of evidence reference kinds."""

    JOB = "job"
    OPERATION = "operation"
    ACCOUNT_RECONCILIATION_RUN = "account_reconciliation_run"
    ACCOUNT_SNAPSHOT = "account_snapshot"
    STRATEGY = "strategy"
    RECOVERY_INTENT = "recovery_intent"


#: Absent-evidence reasons: they say "nothing to read yet", not "the account is dirty".
ABSENT_EVIDENCE_REASONS = frozenset(
    {CheckReason.NO_ACCOUNT_RECONCILIATION, CheckReason.NO_BROKER_OBSERVED_SNAPSHOT}
)
_SUBORDINATE_TO_A6 = frozenset({CheckId.A2, CheckId.A3, CheckId.A4})


@dataclass(frozen=True)
class EvidenceRef:
    kind: EvidenceKind
    id: str

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind.value, "id": self.id}


@dataclass(frozen=True)
class CheckResult:
    id: CheckId
    passed: bool
    reason_code: CheckReason | None
    evidence_refs: tuple[EvidenceRef, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id.value,
            "passed": self.passed,
            "reason_code": self.reason_code.value if self.reason_code is not None else None,
            "evidence_refs": [ref.to_dict() for ref in self.evidence_refs],
        }


@dataclass(frozen=True)
class AccountChecks:
    """Every evaluated check (A1..A6 always, A7 only for handover/release with an owner)."""

    checks: tuple[CheckResult, ...]
    as_of: datetime

    def get(self, check_id: CheckId) -> CheckResult | None:
        return next((check for check in self.checks if check.id is check_id), None)

    def failed_ids(self) -> tuple[CheckId, ...]:
        return tuple(check.id for check in self.checks if not check.passed)

    @property
    def all_passed(self) -> bool:
        return all(check.passed for check in self.checks)

    def first_failed(self) -> CheckResult | None:
        """The first failing check in A1..A7 order, with the A6 subordination rule."""

        a6 = self.get(CheckId.A6)
        a6_failing = a6 is not None and not a6.passed
        for check in self.checks:
            if check.passed:
                continue
            if (
                a6_failing
                and check.id in _SUBORDINATE_TO_A6
                and check.reason_code in ABSENT_EVIDENCE_REASONS
            ):
                continue
            return check
        return None

    def to_list(self) -> list[dict[str, Any]]:
        return [check.to_dict() for check in self.checks]


def _passed(check_id: CheckId) -> CheckResult:
    return CheckResult(check_id, True, None)


def _failed(
    check_id: CheckId, reason: CheckReason, refs: Sequence[EvidenceRef] = ()
) -> CheckResult:
    return CheckResult(check_id, False, reason, tuple(refs[:MAX_EVIDENCE_REFS]))


# ---------------------------------------------------------------------------
# A1
# ---------------------------------------------------------------------------


def _check_a1(session: Session, *, now: datetime, settings: Settings) -> CheckResult:
    job_ids = (
        session.execute(
            select(Job.id)
            .where(
                Job.job_type.in_(sorted(BROKER_TOUCHING_JOB_TYPES)),
                Job.status.in_((JobStatus.QUEUED, JobStatus.RUNNING)),
            )
            .order_by(Job.created_at.asc(), Job.id.asc())
            .limit(MAX_EVIDENCE_REFS)
        )
        .scalars()
        .all()
    )
    # At most one open operation per strategy (unique partial index), so this is bounded
    # by the number of strategies, never by history.
    open_operations = (
        session.execute(
            select(ExecutionOperation)
            .where(ExecutionOperation.state.in_([s.value for s in OPEN_OPERATION_STATES]))
            .order_by(ExecutionOperation.created_at.asc(), ExecutionOperation.id.asc())
        )
        .scalars()
        .all()
    )
    effective_open: list[uuid.UUID] = []
    if open_operations:
        window = load_calendar_window(session, now=now, settings=settings)
        live = live_job_ids(session, [operation.id for operation in open_operations])
        for operation in open_operations:
            effective = effective_state(
                session,
                operation,
                now=now,
                settings=settings,
                window=window,
                has_live_job=bool(live.get(operation.id)),
            )
            if effective.state in OPEN_OPERATION_STATES:
                effective_open.append(operation.id)
    refs = [EvidenceRef(EvidenceKind.JOB, str(job_id)) for job_id in job_ids] + [
        EvidenceRef(EvidenceKind.OPERATION, str(operation_id)) for operation_id in effective_open
    ]
    if job_ids:
        return _failed(CheckId.A1, CheckReason.BROKER_JOB_ACTIVE, refs)
    if effective_open:
        return _failed(CheckId.A1, CheckReason.OPEN_OPERATION, refs)
    return _passed(CheckId.A1)


# ---------------------------------------------------------------------------
# A2-A4 (the latest completed account reconciliation) and A6
# ---------------------------------------------------------------------------


def _latest_completed_account_run(session: Session) -> AccountReconciliationRun | None:
    """Newest COMPLETED account run of any status (one statement); never an older fallback."""

    return session.execute(
        select(AccountReconciliationRun)
        .where(AccountReconciliationRun.completed_at.is_not(None))
        .order_by(
            AccountReconciliationRun.completed_at.desc(), AccountReconciliationRun.id.desc()
        )
        .limit(1)
    ).scalar_one_or_none()


def _usable(run: AccountReconciliationRun | None) -> bool:
    """A run whose stored findings can be read (a failed run stored none)."""

    return run is not None and run.status == "succeeded"


def _run_ref(run: AccountReconciliationRun) -> EvidenceRef:
    return EvidenceRef(EvidenceKind.ACCOUNT_RECONCILIATION_RUN, str(run.id))


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _check_a2(run: AccountReconciliationRun | None) -> CheckResult:
    if run is None or not _usable(run):
        return _failed(
            CheckId.A2,
            CheckReason.NO_ACCOUNT_RECONCILIATION,
            [_run_ref(run)] if run is not None else [],
        )
    count = _as_int((run.result_summary or {}).get("non_terminal_order_count"))
    if count is None:
        # Absent (a run stored before the count existed) or None (broker orders unread).
        return _failed(CheckId.A2, CheckReason.NO_ACCOUNT_RECONCILIATION, [_run_ref(run)])
    if count > 0:
        return _failed(CheckId.A2, CheckReason.NON_TERMINAL_ORDERS, [_run_ref(run)])
    return _passed(CheckId.A2)


def _nonzero_exposure(exposure: Any) -> bool:
    if not isinstance(exposure, dict):
        return True
    for value in exposure.values():
        try:
            if Decimal(str(value)) != 0:
                return True
        except InvalidOperation:
            return True
    return False


def _check_a3(session: Session, run: AccountReconciliationRun | None) -> CheckResult:
    snapshot = latest_broker_observed_account_snapshot(session)
    if snapshot is None:
        return _failed(CheckId.A3, CheckReason.NO_BROKER_OBSERVED_SNAPSHOT)
    snapshot_ref = EvidenceRef(EvidenceKind.ACCOUNT_SNAPSHOT, str(snapshot.id))
    if snapshot.open_positions > 0:
        return _failed(CheckId.A3, CheckReason.OPEN_POSITIONS, [snapshot_ref])
    if run is None or not _usable(run):
        return _failed(
            CheckId.A3,
            CheckReason.NO_ACCOUNT_RECONCILIATION,
            [_run_ref(run)] if run is not None else [],
        )
    if _nonzero_exposure(run.unexplained_exposure):
        return _failed(CheckId.A3, CheckReason.UNEXPLAINED_EXPOSURE, [_run_ref(run)])
    return _passed(CheckId.A3)


def _check_a4(run: AccountReconciliationRun | None) -> CheckResult:
    if run is None or not _usable(run):
        return _failed(
            CheckId.A4,
            CheckReason.NO_ACCOUNT_RECONCILIATION,
            [_run_ref(run)] if run is not None else [],
        )
    summary = run.classification_summary or {}
    orders = summary.get("orders")
    fills = summary.get("fills")
    if not isinstance(orders, dict) or not isinstance(fills, dict):
        return _failed(CheckId.A4, CheckReason.NO_ACCOUNT_RECONCILIATION, [_run_ref(run)])
    unrecognized_orders = _as_int(orders.get("unrecognized"))
    unrecognized_fills = _as_int(fills.get("unrecognized"))
    if unrecognized_orders is None or unrecognized_fills is None:
        return _failed(CheckId.A4, CheckReason.NO_ACCOUNT_RECONCILIATION, [_run_ref(run)])
    if unrecognized_orders + unrecognized_fills > 0:
        return _failed(CheckId.A4, CheckReason.UNRECOGNIZED_ITEMS, [_run_ref(run)])
    return _passed(CheckId.A4)


def _check_a5(session: Session, *, now: datetime) -> CheckResult:
    status = account_recovery_status(session, now=now)
    if status.resolved:
        return _passed(CheckId.A5)
    refs: list[EvidenceRef] = []
    seen_jobs: set[uuid.UUID] = set()
    for subject in status.subjects:
        if subject.resolved:
            continue
        for job in subject.jobs:
            if job.job_id not in seen_jobs:
                seen_jobs.add(job.job_id)
                refs.append(EvidenceRef(EvidenceKind.JOB, str(job.job_id)))
        for intent in subject.intents:
            if intent.blocking:
                refs.append(EvidenceRef(EvidenceKind.RECOVERY_INTENT, str(intent.intent_id)))
        if subject.strategy_id is not None:
            refs.append(EvidenceRef(EvidenceKind.STRATEGY, subject.strategy_id))
    return _failed(CheckId.A5, CheckReason.UNRESOLVED_OUTCOME, refs)


def _check_a6(session: Session, latest_run: AccountReconciliationRun | None) -> CheckResult:
    boundary = latest_broker_effect_at(session)
    qualifying = latest_standalone_reconciliation(
        session, scope="account", completed_after=boundary
    )
    if qualifying is None:
        if latest_run is None:
            return _failed(CheckId.A6, CheckReason.NO_ACCOUNT_RECONCILIATION)
        return _failed(CheckId.A6, CheckReason.RECONCILIATION_STALE, [_run_ref(latest_run)])
    if not qualifying.is_clean:
        return _failed(
            CheckId.A6,
            CheckReason.RECONCILIATION_NOT_CLEAN,
            [EvidenceRef(EvidenceKind.ACCOUNT_RECONCILIATION_RUN, str(qualifying.run_id))],
        )
    return _passed(CheckId.A6)


# ---------------------------------------------------------------------------
# A7
# ---------------------------------------------------------------------------


def _check_a7(owner_public_id: str, owner_status: StrategyStatus) -> CheckResult:
    if owner_status is StrategyStatus.ACTIVE:
        return _failed(
            CheckId.A7,
            CheckReason.OUTGOING_OWNER_ENABLED,
            [EvidenceRef(EvidenceKind.STRATEGY, owner_public_id)],
        )
    return _passed(CheckId.A7)


def _load_owner(session: Session) -> tuple[str, StrategyStatus] | None:
    row = session.execute(
        select(Strategy.strategy_id, Strategy.status)
        .select_from(ActivePaperStrategy)
        .join(Strategy, Strategy.id == ActivePaperStrategy.strategy_id)
        .where(ActivePaperStrategy.id == ACTIVE_PAPER_STRATEGY_SINGLETON_ID)
    ).one_or_none()
    if row is None:
        return None
    return row[0], StrategyStatus(row[1])


def evaluate_account_checks(
    session: Session,
    *,
    now: datetime | None = None,
    include_handover: bool,
    settings: Settings | None = None,
) -> AccountChecks:
    """Evaluate A1..A6 (always) and A7 (``include_handover`` and an owner exists).

    Read-only: SELECTs only, no broker access, a statement count independent of history.
    """

    as_of = now or clock.now_utc()
    resolved = settings or load_settings()
    latest_run = _latest_completed_account_run(session)
    results = [
        _check_a1(session, now=as_of, settings=resolved),
        _check_a2(latest_run),
        _check_a3(session, latest_run),
        _check_a4(latest_run),
        _check_a5(session, now=as_of),
        _check_a6(session, latest_run),
    ]
    if include_handover:
        owner = _load_owner(session)
        if owner is not None:
            results.append(_check_a7(*owner))
    return AccountChecks(checks=tuple(results), as_of=as_of)


__all__ = [
    "ABSENT_EVIDENCE_REASONS",
    "MAX_EVIDENCE_REFS",
    "AccountChecks",
    "CheckId",
    "CheckReason",
    "CheckResult",
    "EvidenceKind",
    "EvidenceRef",
    "evaluate_account_checks",
]
