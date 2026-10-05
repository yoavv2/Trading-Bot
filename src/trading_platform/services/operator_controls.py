"""Persisted operator kill-switch controls and audit helpers."""

from __future__ import annotations

import functools
import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, TypeVar

from sqlalchemy import false as sa_false
from sqlalchemy import func as sa_func
from sqlalchemy import literal, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, aliased

from trading_platform.core.logging import emit_structured_log, get_logger
from trading_platform.core.settings import Settings, load_settings
from trading_platform.db.models import (
    GLOBAL_KILL_SWITCH_NAME,
    ActivePaperStrategy,
    ExecutionEvent,
    ExecutionOperation,
    KillSwitchState,
    PaperOrder,
    Strategy,
    StrategyRun,
    StrategyRunStatus,
    StrategyRunType,
    StrategyStatus,
    SystemControl,
)
from trading_platform.db.models.active_paper_strategy import ACTIVE_PAPER_STRATEGY_SINGLETON_ID
from trading_platform.db.session import session_scope
from trading_platform.services.active_paper_strategy import (
    ActivePaperStrategyState,
    ActivePaperStrategyUnavailableError,
    OwnershipBlock,
    load_active_paper_strategy,
    ownership_block_from_state,
)
from trading_platform.services.bootstrap import ensure_strategy_record
from trading_platform.services.execution import operations as _operations

# Re-exported for the execution-operation route adapter (REC-02), which may import only this
# services module and ``operation_reads``.
from trading_platform.services.execution.operations import (
    OperationNotFoundError as OperationNotFoundError,
)
from trading_platform.services.execution.operations import (
    OperationNotOpenError as OperationNotOpenError,
)
from trading_platform.services.execution.operations import (
    OperationRunningError as OperationRunningError,
)

# Re-exported for the recovery route adapter, which may import only this services module.
from trading_platform.services.paper_account_checks import (
    AccountChecks,
    evaluate_account_checks,
)
from trading_platform.services.recovery import (
    IntentNotFoundError,
)
from trading_platform.services.recovery import (
    IntentNotOnMissingOrderPathError as IntentNotOnMissingOrderPathError,
)
from trading_platform.services.recovery import (
    InvalidBrokerStatementError as InvalidBrokerStatementError,
)
from trading_platform.services.recovery import (
    StatementConflictError as StatementConflictError,
)
from trading_platform.services.recovery import record_broker_statement as _record_broker_statement
from trading_platform.strategies.registry import StrategyRegistry, build_default_registry

_BLOCKED_REASON_STRATEGY_DISABLED = "strategy_disabled"
BLOCKED_REASON_GLOBAL_KILL_SWITCH = "global_kill_switch_tripped"
_DEFAULT_KILL_SWITCH_STRATEGY_ID = "trend_following_daily"

_F = TypeVar("_F", bound=Callable[..., Any])


def _translate_db_errors(error_type: type[Exception]) -> Callable[[_F], _F]:
    """Re-raise ``SQLAlchemyError`` from the wrapped method as ``error_type``."""

    def decorator(func: _F) -> _F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return func(*args, **kwargs)
            except SQLAlchemyError as exc:
                raise error_type(f"{type(exc).__name__}: control state database error") from exc

        return wrapper  # type: ignore[return-value]

    return decorator


class StrategyArchivedError(Exception):
    """An enable/disable was requested for an ARCHIVED strategy.

    ARCHIVED is not representable in the enabled|disabled control vocabulary
    (D-10), so the control path refuses to transition out of it rather than
    silently resurrecting the strategy for paper execution.
    """

    def __init__(self, strategy_id: str) -> None:
        super().__init__(f"Strategy '{strategy_id}' is archived and cannot be enabled or disabled.")
        self.strategy_id = strategy_id


class ControlWriteError(RuntimeError):
    """A control mutation failed at the database layer (nothing was committed).

    Wraps ``SQLAlchemyError`` so the HTTP adapter can map it to a typed error
    without importing the persistence layer.
    """


class ControlStateUnavailableError(LookupError):
    """Persisted control state needed by a mutator is missing or unresolvable.

    Raised when the global kill-switch row is absent (migrations not current)
    or when no strategy row can be found/created to anchor the audit run.
    """


class AccountCheckFailedError(Exception):
    """A seeding, handover or release was refused by an account check (A1-A7, D-04).

    Raised inside the control transaction BEFORE any write, so the refusal performs zero
    writes. ``code`` is ``check_failed:<first failing check id>``; ``checks`` carries every
    evaluated check (passed, reason code, evidence refs) and ``failed_checks`` all failing ids.
    """

    def __init__(self, checks: AccountChecks) -> None:
        first = checks.first_failed()
        if first is None:  # pragma: no cover - guarded by the caller
            raise ValueError("AccountCheckFailedError requires a failing check")
        super().__init__(f"Account check {first.id.value} failed: {first.reason_code}")
        self.check = first.id.value
        self.code = f"check_failed:{first.id.value}"
        self.failed_checks = [check_id.value for check_id in checks.failed_ids()]
        self.checks = checks.to_list()

    def to_detail(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "check": self.check,
            "failed_checks": list(self.failed_checks),
            "checks": list(self.checks),
        }


#: Closed transition kinds of the owner control (``reaffirm`` = target equals current).
OWNER_KIND_SEEDING = "seeding"
OWNER_KIND_HANDOVER = "handover"
OWNER_KIND_RELEASE = "release"
OWNER_KIND_REAFFIRM = "reaffirm"

ACTIVE_PAPER_STRATEGY_CHANGED_EVENT = "active_paper_strategy_changed"
ACTIVE_PAPER_STRATEGY_UNCHANGED_EVENT = "active_paper_strategy_unchanged"


@dataclass(frozen=True)
class ActivePaperStrategyReport:
    """Outcome of ``set_active_paper_strategy`` (the audited owner control)."""

    run_id: str
    kind: str
    changed: bool
    strategy_id: str | None
    previous_strategy_id: str | None
    since: str
    new_owner_status: str | None
    new_owner_disabled: bool
    checks: list[dict[str, Any]]
    reason: str
    actor: str
    trigger_source: str
    anchor_only: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "active_paper_strategy": {"strategy_id": self.strategy_id, "since": self.since},
            "previous_strategy_id": self.previous_strategy_id,
            "changed": self.changed,
            "kind": self.kind,
            "new_owner_status": self.new_owner_status,
            "run_id": self.run_id,
        }


@dataclass(frozen=True)
class ActivePaperStrategyView:
    """R1: the owner plus the readable account checks (read-only, no write)."""

    state: ActivePaperStrategyState
    checks: list[dict[str, Any]]
    seeding_available: bool
    handover_available: bool
    as_of: datetime
    #: 20.1-14: closed ``TradingBlocker`` values, deterministic order; empty = nothing blocks.
    trading_blocked_reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.state.to_dict(),
            "trading_blocked_reasons": list(self.trading_blocked_reasons),
            "checks": list(self.checks),
            "seeding_available": self.seeding_available,
            "handover_available": self.handover_available,
            "as_of": self.as_of.isoformat(),
        }


@dataclass(frozen=True)
class StrategyControlState:
    strategy_id: str
    display_name: str
    status: str
    updated_at: str | None

    @property
    def is_execution_enabled(self) -> bool:
        return self.status == StrategyStatus.ACTIVE.value

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "display_name": self.display_name,
            "status": self.status,
            "updated_at": self.updated_at,
            "is_execution_enabled": self.is_execution_enabled,
        }


@dataclass(frozen=True)
class OperatorControlReport:
    run_id: str
    strategy_id: str
    action: str
    previous_status: str
    current_status: str
    changed: bool
    trigger_source: str
    started_at: str
    completed_at: str | None
    reason: str | None
    actor: str
    result_summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "strategy_id": self.strategy_id,
            "action": self.action,
            "previous_status": self.previous_status,
            "current_status": self.current_status,
            "changed": self.changed,
            "trigger_source": self.trigger_source,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "reason": self.reason,
            "actor": self.actor,
            "result_summary": self.result_summary,
        }


@dataclass(frozen=True)
class KillSwitchStateSnapshot:
    """Serializable snapshot of the persisted global kill switch."""

    name: str
    state: str
    is_tripped: bool
    last_changed_at: str
    last_change_actor: str
    last_change_reason: str | None
    last_change_run_id: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state,
            "is_tripped": self.is_tripped,
            "last_changed_at": self.last_changed_at,
            "last_change_actor": self.last_change_actor,
            "last_change_reason": self.last_change_reason,
            "last_change_run_id": self.last_change_run_id,
        }


@dataclass(frozen=True)
class KillSwitchControlReport:
    run_id: str
    action: str
    previous_state: str
    current_state: str
    changed: bool
    trigger_source: str
    started_at: str
    completed_at: str | None
    reason: str | None
    actor: str
    state_snapshot: dict[str, Any]
    result_summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "action": self.action,
            "previous_state": self.previous_state,
            "current_state": self.current_state,
            "changed": self.changed,
            "trigger_source": self.trigger_source,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "reason": self.reason,
            "actor": self.actor,
            "state_snapshot": self.state_snapshot,
            "result_summary": self.result_summary,
        }


@dataclass(frozen=True)
class EndOperationControlReport:
    """Result of the REC-02 End control (D-20): an audited, synchronous operator control."""

    run_id: str
    operation_id: str
    strategy_id: str
    state: str
    reason: str | None
    changed: bool
    ended_by_expiry: bool
    unsent_cancelled: list[str]
    working_orders: list[dict[str, Any]]
    unresolved_intents: list[dict[str, Any]]
    actor: str
    trigger_source: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "state": self.state,
            "reason": self.reason,
            "changed": self.changed,
            "ended_by_expiry": self.ended_by_expiry,
            "unsent_cancelled": self.unsent_cancelled,
            "working_orders": self.working_orders,
            "unresolved_intents": self.unresolved_intents,
            # End never changes the kill switch or the strategy status (J-2).
            "trading_permission_changed": False,
            "run_id": self.run_id,
        }


@dataclass(frozen=True)
class BrokerStatementControlReport:
    """Result of the REC-01 broker-statement control (M14); audited evidence only."""

    run_id: str
    intent_id: str
    strategy_id: str
    statement: str
    changed: bool
    classification: str
    actor: str
    trigger_source: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "intent_id": self.intent_id,
            "strategy_id": self.strategy_id,
            "statement": self.statement,
            "changed": self.changed,
            "classification": self.classification,
        }


@dataclass(frozen=True)
class TradingGateState:
    """Everything a trading gate decides on, read in ONE statement (R-Q1).

    ``kill_switch``, ``owner`` and ``owner_status`` are the three gate inputs;
    ``strategy`` is the requested strategy's own control state when the loader
    was asked for one (``None`` when it has no DB row). ``kill_switch`` and
    ``owner`` raise typed errors when their persisted row is missing, so a
    gate can never read "absent" as "fine" (fail closed). ``owner.strategy_id``
    is ``None`` when no strategy owns the account (D-02).
    """

    kill_switch_state: KillSwitchStateSnapshot | None
    owner_state: ActivePaperStrategyState | None
    owner_status: str | None
    strategy: StrategyControlState | None = None

    @property
    def kill_switch(self) -> KillSwitchStateSnapshot:
        if self.kill_switch_state is None:
            raise ControlStateUnavailableError(
                f"Missing global kill switch row '{GLOBAL_KILL_SWITCH_NAME}'; "
                "database migrations may not be current."
            )
        return self.kill_switch_state

    @property
    def owner(self) -> ActivePaperStrategyState:
        if self.owner_state is None:
            raise ActivePaperStrategyUnavailableError(
                "Missing active_paper_strategy singleton row; database migrations may not be current."
            )
        return self.owner_state

    def ownership_block_for(self, strategy_id: str) -> OwnershipBlock | None:
        """The existing pure ownership decision applied to this read."""

        return ownership_block_from_state(self.owner, strategy_id)


def load_trading_gate_state(session: Session, *, strategy_id: str | None = None) -> TradingGateState:
    """R-Q1: kill switch + active paper strategy + owner status in ONE statement.

    The statement starts from a constant one-row relation and LEFT JOINs each
    persisted row, so a missing kill-switch row or a missing singleton row is
    reported as ``None`` (and raised by the typed accessors) instead of
    silently hiding the other facts. Column-level select: nothing is entered
    into the session identity map, so a repeated read is always fresh.
    ``strategy_id`` additionally returns that strategy's control state from
    the same statement (``None`` when it has no DB row).
    """

    base = select(literal(1).label("one")).subquery("gate_base")
    owner_strategy = aliased(Strategy)
    subject = aliased(Strategy)
    subject_match = (
        subject.strategy_id == strategy_id if strategy_id is not None else sa_false()
    )
    row = session.execute(
        select(
            SystemControl.name.label("ks_name"),
            SystemControl.state.label("ks_state"),
            SystemControl.last_changed_at.label("ks_last_changed_at"),
            SystemControl.last_change_actor.label("ks_actor"),
            SystemControl.last_change_reason.label("ks_reason"),
            SystemControl.last_change_run_id.label("ks_run_id"),
            ActivePaperStrategy.id.label("owner_row_id"),
            ActivePaperStrategy.since.label("owner_since"),
            ActivePaperStrategy.reason.label("owner_reason"),
            ActivePaperStrategy.set_by_run_id.label("owner_set_by_run_id"),
            owner_strategy.strategy_id.label("owner_public_id"),
            owner_strategy.display_name.label("owner_display_name"),
            owner_strategy.status.label("owner_status"),
            subject.strategy_id.label("subject_public_id"),
            subject.display_name.label("subject_display_name"),
            subject.status.label("subject_status"),
            subject.updated_at.label("subject_updated_at"),
        )
        .select_from(base)
        .outerjoin(SystemControl, SystemControl.name == GLOBAL_KILL_SWITCH_NAME)
        .outerjoin(
            ActivePaperStrategy, ActivePaperStrategy.id == ACTIVE_PAPER_STRATEGY_SINGLETON_ID
        )
        .outerjoin(owner_strategy, owner_strategy.id == ActivePaperStrategy.strategy_id)
        .outerjoin(subject, subject_match)
    ).one()

    kill_switch = None
    if row.ks_name is not None:
        kill_switch = KillSwitchStateSnapshot(
            name=row.ks_name,
            state=row.ks_state.value,
            is_tripped=row.ks_state == KillSwitchState.TRIPPED,
            last_changed_at=row.ks_last_changed_at.isoformat(),
            last_change_actor=row.ks_actor,
            last_change_reason=row.ks_reason,
            last_change_run_id=str(row.ks_run_id) if row.ks_run_id is not None else None,
        )
    owner = None
    if row.owner_row_id is not None:
        owner = ActivePaperStrategyState(
            strategy_id=row.owner_public_id,
            display_name=row.owner_display_name,
            since=row.owner_since,
            reason=row.owner_reason,
            set_by_run_id=(
                str(row.owner_set_by_run_id) if row.owner_set_by_run_id is not None else None
            ),
        )
    strategy = None
    if row.subject_public_id is not None:
        strategy = StrategyControlState(
            strategy_id=row.subject_public_id,
            display_name=row.subject_display_name,
            status=row.subject_status.value,
            updated_at=row.subject_updated_at.isoformat(),
        )
    return TradingGateState(
        kill_switch_state=kill_switch,
        owner_state=owner,
        owner_status=row.owner_status.value if row.owner_status is not None else None,
        strategy=strategy,
    )


def read_trading_gate_state(
    settings: Settings | None = None, *, strategy_id: str | None = None
) -> TradingGateState:
    """``load_trading_gate_state`` in its own short read session (fresh, uncached)."""

    with session_scope(settings or load_settings()) as session:
        return load_trading_gate_state(session, strategy_id=strategy_id)


class OperatorControlService:
    def __init__(
        self,
        settings: Settings | None = None,
        registry: StrategyRegistry | None = None,
    ) -> None:
        self._settings = settings
        self._registry = registry
        self._logger = get_logger("trading_platform.operator_controls")

    @property
    def settings(self) -> Settings:
        return self._settings or load_settings()

    @property
    def registry(self) -> StrategyRegistry:
        return self._registry or build_default_registry(self.settings)

    @_translate_db_errors(ControlStateUnavailableError)
    def get_strategy_state(self, strategy_id: str) -> StrategyControlState:
        """Pure read (D-31): one gate statement, a registry default, never a get-or-create.

        The default below is the exact status the mutating sibling method
        (`ensure_strategy_state`) persists for a brand-new row (always DISABLED,
        regardless of StrategyMetadata.enabled -- R-8: a newly registered
        strategy never trades until an operator enables it), so the pure read
        and the mutating path agree on an empty DB. This method never creates
        a strategy row and never writes to the session. It is a thin wrapper
        over the shared gate loader (R-Q1).
        """
        metadata = self.registry.resolve(strategy_id).metadata
        with session_scope(self.settings) as session:
            gate = load_trading_gate_state(session, strategy_id=metadata.strategy_id)
            if gate.strategy is not None:
                return gate.strategy
            return StrategyControlState(
                strategy_id=metadata.strategy_id,
                display_name=metadata.display_name,
                status=StrategyStatus.DISABLED.value,
                updated_at=None,
            )

    @_translate_db_errors(ControlStateUnavailableError)
    def get_active_paper_strategy_view(self) -> ActivePaperStrategyView:
        """Read-only owner view with the A1..A6 checks (A7 when an owner exists) (R1).

        No write, no get-or-create, no broker call; a statement count independent of
        history. A missing singleton row is reported as ``ControlStateUnavailableError``
        (HTTP 503 ``control_state_unavailable``).
        """
        with session_scope(self.settings) as session:
            try:
                state = load_active_paper_strategy(session=session)
            except ActivePaperStrategyUnavailableError as exc:
                raise ControlStateUnavailableError(str(exc)) from exc
            has_owner = state.strategy_id is not None
            checks = evaluate_account_checks(
                session, include_handover=has_owner, settings=self.settings
            )
            # Local import: services.execution.permission imports this module.
            from trading_platform.services.execution.permission import current_trading_blockers

            blockers = current_trading_blockers(session)
            return ActivePaperStrategyView(
                state=state,
                checks=checks.to_list(),
                seeding_available=(not has_owner) and checks.all_passed,
                handover_available=has_owner and checks.all_passed,
                as_of=checks.as_of,
                trading_blocked_reasons=tuple(b.value for b in blockers),
            )

    @_translate_db_errors(ControlWriteError)
    def set_active_paper_strategy(
        self,
        strategy_id: str | None,
        *,
        reason: str,
        actor: str = "local_operator",
        trigger_source: str = "api_control",
    ) -> ActivePaperStrategyReport:
        """Seed (none -> B), hand over (A -> B), release (A -> none) or reaffirm the owner.

        PAPER-02 / D-04. ONE transaction: the singleton row is locked FOR UPDATE (the same
        lock Job admission takes FOR SHARE), the transition is computed from the locked
        row, account checks A1-A7 are evaluated against persisted evidence and, only if all
        pass, the owner is set, the NEW owner is left DISABLED, and one operator_control
        run plus one ExecutionEvent are written. A refusal raises
        ``AccountCheckFailedError`` before any write. Reaffirming the current owner evaluates
        no checks and changes nothing but is audited. No broker access, no worker, no Job;
        a handover or release never disables the outgoing owner (the operator does that first).
        """

        metadata = self.registry.resolve(strategy_id).metadata if strategy_id is not None else None
        with session_scope(self.settings) as session:
            singleton = session.execute(
                select(ActivePaperStrategy)
                .where(ActivePaperStrategy.id == ACTIVE_PAPER_STRATEGY_SINGLETON_ID)
                .with_for_update()
            ).scalar_one_or_none()
            if singleton is None:
                raise ControlStateUnavailableError(
                    "Missing active_paper_strategy singleton row; database migrations may not "
                    "be current."
                )
            changed_at = _db_clock_now(session)
            outgoing: Strategy | None = None
            if singleton.strategy_id is not None:
                outgoing = session.execute(
                    select(Strategy).where(Strategy.id == singleton.strategy_id).with_for_update()
                ).scalar_one()
            target = _ensure_locked_strategy_record(session, metadata) if metadata else None

            previous_public = outgoing.strategy_id if outgoing is not None else None
            new_public = target.strategy_id if target is not None else None
            if new_public == previous_public:
                kind = OWNER_KIND_REAFFIRM
            elif previous_public is None:
                kind = OWNER_KIND_SEEDING
            elif new_public is None:
                kind = OWNER_KIND_RELEASE
            else:
                kind = OWNER_KIND_HANDOVER

            if (
                kind in (OWNER_KIND_SEEDING, OWNER_KIND_HANDOVER)
                and target is not None
                and target.status == StrategyStatus.ARCHIVED
            ):
                raise StrategyArchivedError(target.strategy_id)

            checks_list: list[dict[str, Any]] = []
            if kind != OWNER_KIND_REAFFIRM:
                checks = evaluate_account_checks(
                    session,
                    include_handover=kind in (OWNER_KIND_HANDOVER, OWNER_KIND_RELEASE),
                    settings=self.settings,
                )
                if not checks.all_passed:
                    raise AccountCheckFailedError(checks)
                checks_list = checks.to_list()

            anchor_only = False
            if kind in (OWNER_KIND_SEEDING, OWNER_KIND_HANDOVER):
                audit_strategy = target
            elif outgoing is not None:  # release or reaffirm of the current owner
                audit_strategy = outgoing
            else:  # none -> none: the documented kill-switch anchor strategy
                audit_strategy = self._resolve_audit_strategy_record(session)
                anchor_only = True
            assert audit_strategy is not None

            new_owner_disabled = False
            if (
                kind in (OWNER_KIND_SEEDING, OWNER_KIND_HANDOVER)
                and target is not None
                and target.status == StrategyStatus.ACTIVE
            ):
                target.status = StrategyStatus.DISABLED
                new_owner_disabled = True

            strategy_run = StrategyRun(
                strategy_id=audit_strategy.id,
                run_type=StrategyRunType.OPERATOR_CONTROL,
                status=StrategyRunStatus.PENDING,
                trigger_source=trigger_source,
                parameters_snapshot={
                    "action": "set_active_paper_strategy",
                    "kind": kind,
                    "actor": actor,
                    "reason": reason,
                    "previous_strategy_id": previous_public,
                    "requested_strategy_id": new_public,
                },
                result_summary={
                    "stage": "pending",
                    "action": "set_active_paper_strategy",
                    "kind": kind,
                },
            )
            session.add(strategy_run)
            session.flush()

            changed = kind != OWNER_KIND_REAFFIRM
            if changed:
                singleton.strategy_id = target.id if target is not None else None
                singleton.since = changed_at
                singleton.reason = reason
                singleton.set_by_run_id = strategy_run.id
                session.flush()
            since = changed_at if changed else singleton.since

            details: dict[str, Any] = {
                "kind": kind,
                "previous_strategy_id": previous_public,
                "new_strategy_id": new_public,
                "reason": reason,
                "actor": actor,
                "anchor_only": anchor_only,
            }
            if changed:
                details["checks"] = checks_list
                details["new_owner_disabled"] = new_owner_disabled
            result_summary = {
                "stage": "completed",
                "action": "set_active_paper_strategy",
                "changed": changed,
                "changed_at": changed_at.isoformat(),
                **details,
            }
            strategy_run.status = StrategyRunStatus.SUCCEEDED
            strategy_run.completed_at = changed_at
            strategy_run.result_summary = result_summary
            session.add(
                ExecutionEvent(
                    strategy_run_id=strategy_run.id,
                    paper_order_id=None,
                    event_type=(
                        ACTIVE_PAPER_STRATEGY_CHANGED_EVENT
                        if changed
                        else ACTIVE_PAPER_STRATEGY_UNCHANGED_EVENT
                    ),
                    severity="info",
                    blocks_execution=False,
                    event_at=changed_at,
                    message=_build_owner_message(
                        kind=kind,
                        previous=previous_public,
                        new=new_public,
                        new_owner_disabled=new_owner_disabled,
                        reason=reason,
                    ),
                    details=details,
                )
            )
            session.flush()
            report = ActivePaperStrategyReport(
                run_id=str(strategy_run.id),
                kind=kind,
                changed=changed,
                strategy_id=new_public,
                previous_strategy_id=previous_public,
                since=since.isoformat(),
                new_owner_status=StrategyStatus.DISABLED.value if new_owner_disabled else None,
                new_owner_disabled=new_owner_disabled,
                checks=checks_list,
                reason=reason,
                actor=actor,
                trigger_source=trigger_source,
                anchor_only=anchor_only,
            )

        emit_structured_log(
            self._logger,
            logging.INFO,
            "active_paper_strategy_set",
            run_id=report.run_id,
            kind=report.kind,
            changed=report.changed,
            previous_strategy_id=report.previous_strategy_id,
            strategy_id=report.strategy_id,
            new_owner_disabled=report.new_owner_disabled,
            actor=actor,
            trigger_source=trigger_source,
        )
        return report

    def ensure_strategy_state(self, strategy_id: str) -> StrategyControlState:
        """Get-or-create for mutating callers only (D-31)."""
        metadata = self.registry.resolve(strategy_id).metadata
        with session_scope(self.settings) as session:
            strategy_record = ensure_strategy_record(session, metadata)
            session.flush()
            session.refresh(strategy_record)
            return _serialize_strategy_control_state(strategy_record)

    def enable_strategy(
        self,
        strategy_id: str,
        *,
        reason: str | None = None,
        actor: str = "local_operator",
        trigger_source: str = "operator_control_script",
    ) -> OperatorControlReport:
        return self._set_strategy_status(
            strategy_id,
            target_status=StrategyStatus.ACTIVE,
            action="enable",
            reason=reason,
            actor=actor,
            trigger_source=trigger_source,
        )

    def disable_strategy(
        self,
        strategy_id: str,
        *,
        reason: str | None = None,
        actor: str = "local_operator",
        trigger_source: str = "operator_control_script",
    ) -> OperatorControlReport:
        return self._set_strategy_status(
            strategy_id,
            target_status=StrategyStatus.DISABLED,
            action="disable",
            reason=reason,
            actor=actor,
            trigger_source=trigger_source,
        )

    @_translate_db_errors(ControlWriteError)
    def _set_strategy_status(
        self,
        strategy_id: str,
        *,
        target_status: StrategyStatus,
        action: str,
        reason: str | None,
        actor: str,
        trigger_source: str,
    ) -> OperatorControlReport:
        metadata = self.registry.resolve(strategy_id).metadata
        with session_scope(self.settings) as session:
            strategy_record = _ensure_locked_strategy_record(session, metadata)
            # D-11a: one DB clock read after the row lock. clock_timestamp() is
            # >= now() (the transaction start that StrategyRun.started_at
            # records), so completed_at >= started_at by construction.
            changed_at = _db_clock_now(session)
            previous_status = strategy_record.status
            if previous_status == StrategyStatus.ARCHIVED:
                # Raised before any audit row is added; session_scope rolls back
                # the whole transaction, so the rejection performs zero writes.
                raise StrategyArchivedError(metadata.strategy_id)
            changed = previous_status != target_status

            strategy_run = StrategyRun(
                strategy_id=strategy_record.id,
                run_type=StrategyRunType.OPERATOR_CONTROL,
                status=StrategyRunStatus.PENDING,
                trigger_source=trigger_source,
                parameters_snapshot={
                    "strategy": metadata.to_public_dict(),
                    "action": action,
                    "actor": actor,
                    "reason": reason,
                    "previous_status": previous_status.value,
                    "requested_status": target_status.value,
                },
                result_summary={
                    "stage": "pending",
                    "strategy_id": metadata.strategy_id,
                    "action": action,
                    "requested_status": target_status.value,
                },
            )
            session.add(strategy_run)
            session.flush()

            if changed:
                strategy_record.status = target_status
                session.flush()
            session.refresh(strategy_record)

            result_summary = {
                "stage": "completed",
                "strategy_id": metadata.strategy_id,
                "action": action,
                "changed": changed,
                "actor": actor,
                "reason": reason,
                "previous_status": previous_status.value,
                "current_status": strategy_record.status.value,
                "changed_at": changed_at.isoformat(),
            }
            strategy_run.status = StrategyRunStatus.SUCCEEDED
            strategy_run.completed_at = changed_at
            strategy_run.result_summary = result_summary

            event_type = f"strategy_{action}d"
            session.add(
                ExecutionEvent(
                    strategy_run_id=strategy_run.id,
                    paper_order_id=None,
                    event_type=event_type,
                    severity="warning" if target_status == StrategyStatus.DISABLED else "info",
                    blocks_execution=target_status == StrategyStatus.DISABLED,
                    event_at=changed_at,
                    message=_build_control_message(
                        strategy_id=metadata.strategy_id,
                        action=action,
                        current_status=strategy_record.status.value,
                        changed=changed,
                        reason=reason,
                    ),
                    details=result_summary,
                )
            )
            session.flush()
            session.refresh(strategy_run)

            report = OperatorControlReport(
                run_id=str(strategy_run.id),
                strategy_id=metadata.strategy_id,
                action=action,
                previous_status=previous_status.value,
                current_status=strategy_record.status.value,
                changed=changed,
                trigger_source=strategy_run.trigger_source,
                started_at=strategy_run.started_at.isoformat(),
                completed_at=strategy_run.completed_at.isoformat() if strategy_run.completed_at else None,
                reason=reason,
                actor=actor,
                result_summary=strategy_run.result_summary,
            )

        emit_structured_log(
            self._logger,
            logging.WARNING if target_status == StrategyStatus.DISABLED else logging.INFO,
            "operator_control_applied",
            strategy_id=report.strategy_id,
            run_id=report.run_id,
            strategy_status=report.current_status,
            blocked_reason=(
                _BLOCKED_REASON_STRATEGY_DISABLED if report.current_status == StrategyStatus.DISABLED.value else None
            ),
            action=action,
            actor=actor,
            changed=changed,
            trigger_source=trigger_source,
        )
        return report

    @_translate_db_errors(ControlWriteError)
    def record_broker_statement(
        self,
        intent_id: uuid.UUID,
        *,
        statement: str,
        reference: str,
        reason: str,
        actor: str = "local_operator",
        trigger_source: str = "api_control",
    ) -> BrokerStatementControlReport:
        """Record the broker statement of an ambiguous intent (REC-01 / 05 M14).

        Synchronous, no broker call, audited as an ``operator_control`` run attached to the
        INTENT'S OWN strategy (resolved through order -> run -> strategy), with one
        ``recovery_broker_statement_recorded`` ExecutionEvent, in one transaction. The
        statement is EVIDENCE ONLY (round 5, 2026-10-04): it never resolves the intent and
        never authorizes a resend. A refusal (unknown intent, not on the missing-order
        path, conflicting statement, invalid field) raises before anything is committed, so
        it performs zero writes; an idempotent repeat (``changed`` False) is audited too.
        """

        with session_scope(self.settings) as session:
            changed_at = _db_clock_now(session)
            intent_strategy = self._intent_strategy_public_id(session, intent_id)
            strategy_record = session.execute(
                select(Strategy).where(Strategy.strategy_id == intent_strategy)
            ).scalar_one_or_none()
            if strategy_record is None:
                raise ControlStateUnavailableError(
                    f"Strategy '{intent_strategy}' of intent '{intent_id}' has no row."
                )
            strategy_run = StrategyRun(
                strategy_id=strategy_record.id,
                run_type=StrategyRunType.OPERATOR_CONTROL,
                status=StrategyRunStatus.PENDING,
                trigger_source=trigger_source,
                parameters_snapshot={
                    "action": "record_broker_statement",
                    "actor": actor,
                    "intent_id": str(intent_id),
                    "statement": statement,
                    "reference": reference,
                    "reason": reason,
                },
                result_summary={
                    "stage": "pending",
                    "strategy_id": intent_strategy,
                    "action": "record_broker_statement",
                },
            )
            session.add(strategy_run)
            session.flush()

            # Raises (and rolls the whole transaction back) on any refusal.
            result = _record_broker_statement(
                session, intent_id, statement, reference, reason, actor
            )

            result_summary = {
                "stage": "completed",
                "strategy_id": intent_strategy,
                "action": "record_broker_statement",
                "intent_id": str(intent_id),
                "statement": result.statement.value,
                "reference": reference.strip(),
                "reason": reason.strip(),
                "changed": result.changed,
                "classification": result.classification.value,
                "actor": actor,
                "changed_at": changed_at.isoformat(),
            }
            strategy_run.status = StrategyRunStatus.SUCCEEDED
            strategy_run.completed_at = changed_at
            strategy_run.result_summary = result_summary
            session.add(
                ExecutionEvent(
                    strategy_run_id=strategy_run.id,
                    paper_order_id=intent_id,
                    event_type="recovery_broker_statement_recorded",
                    severity="info",
                    blocks_execution=False,
                    event_at=changed_at,
                    message=(
                        f"Broker statement '{result.statement.value}' "
                        f"{'recorded' if result.changed else 'reaffirmed'} for intent "
                        f"'{intent_id}'; it is audited evidence only and resolves nothing."
                    ),
                    details=result_summary,
                )
            )
            session.flush()
            report = BrokerStatementControlReport(
                run_id=str(strategy_run.id),
                intent_id=str(intent_id),
                strategy_id=intent_strategy,
                statement=result.statement.value,
                changed=result.changed,
                classification=result.classification.value,
                actor=actor,
                trigger_source=trigger_source,
            )

        emit_structured_log(
            self._logger,
            logging.INFO,
            "recovery_broker_statement_applied",
            strategy_id=report.strategy_id,
            run_id=report.run_id,
            intent_id=report.intent_id,
            statement=report.statement,
            actor=actor,
            changed=report.changed,
            trigger_source=trigger_source,
        )
        return report

    @_translate_db_errors(ControlWriteError)
    def end_operation(
        self,
        operation_id: uuid.UUID,
        *,
        reason: str,
        actor: str = "local_operator",
        trigger_source: str = "api_control",
    ) -> EndOperationControlReport:
        """End an execution operation (REC-02 / D-20 / 05 R2).

        Synchronous, no broker call, audited as an ``operator_control`` run attached to the
        OPERATION'S OWN strategy with one ``execution_operation_ended`` ExecutionEvent (the
        operator reason and the cancelled intent ids), all in one transaction. Only UNSENT
        intents are cancelled; submitted orders are not cancelled at the broker, ambiguous
        intents stay ambiguous and blocking, recovery records and attempt logs are untouched
        and trading permission is unchanged. A refusal (``OperationNotFoundError``,
        ``OperationRunningError``, ``OperationNotOpenError``) raises before anything is
        committed, so it performs zero writes; ending an already terminated operation is an
        idempotent ``changed=False`` (audited too).
        """

        with session_scope(self.settings) as session:
            changed_at = _db_clock_now(session)
            row = session.execute(
                select(Strategy, ExecutionOperation.id)
                .join(ExecutionOperation, ExecutionOperation.strategy_id == Strategy.id)
                .where(ExecutionOperation.id == operation_id)
            ).one_or_none()
            if row is None:
                raise OperationNotFoundError(operation_id)
            strategy_record, _operation_pk = row
            strategy_public_id = strategy_record.strategy_id
            strategy_run = StrategyRun(
                strategy_id=strategy_record.id,
                run_type=StrategyRunType.OPERATOR_CONTROL,
                status=StrategyRunStatus.PENDING,
                trigger_source=trigger_source,
                parameters_snapshot={
                    "action": "end_operation",
                    "actor": actor,
                    "operation_id": str(operation_id),
                    "reason": reason,
                },
                result_summary={
                    "stage": "pending",
                    "strategy_id": strategy_public_id,
                    "action": "end_operation",
                },
            )
            session.add(strategy_run)
            session.flush()

            # Raises (and rolls the whole transaction back, audit run included) on a refusal.
            result = _operations.end_operation(
                session, operation_id, operator_reason=reason, actor=actor
            )

            cancelled = [str(intent_id) for intent_id in result.unsent_cancelled]
            result_summary = {
                "stage": "completed",
                "strategy_id": strategy_public_id,
                "action": "end_operation",
                "operation_id": str(operation_id),
                "state": result.state.value,
                "reason": result.reason,
                "operator_reason": reason,
                "changed": result.changed,
                "ended_by_expiry": result.ended_by_expiry,
                "cancelled_intent_ids": cancelled,
                "remaining_working_orders": len(result.working_orders),
                "unresolved_intents": len(result.unresolved_intents),
                "actor": actor,
                "changed_at": changed_at.isoformat(),
            }
            strategy_run.status = StrategyRunStatus.SUCCEEDED
            strategy_run.completed_at = changed_at
            strategy_run.result_summary = result_summary
            session.add(
                ExecutionEvent(
                    strategy_run_id=strategy_run.id,
                    paper_order_id=None,
                    event_type="execution_operation_ended",
                    severity="info",
                    blocks_execution=False,
                    event_at=changed_at,
                    message=(
                        f"Execution operation '{operation_id}' "
                        f"{'ended' if result.changed else 'was already ended'} "
                        f"({result.state.value}"
                        f"{'/' + result.reason if result.reason else ''}); unsent intents only, "
                        "no broker order was cancelled and trading permission is unchanged."
                    ),
                    details=result_summary,
                )
            )
            session.flush()
            report = EndOperationControlReport(
                run_id=str(strategy_run.id),
                operation_id=str(operation_id),
                strategy_id=strategy_public_id,
                state=result.state.value,
                reason=result.reason,
                changed=result.changed,
                ended_by_expiry=result.ended_by_expiry,
                unsent_cancelled=cancelled,
                working_orders=list(result.working_orders),
                unresolved_intents=list(result.unresolved_intents),
                actor=actor,
                trigger_source=trigger_source,
            )

        emit_structured_log(
            self._logger,
            logging.INFO,
            "execution_operation_ended",
            strategy_id=report.strategy_id,
            run_id=report.run_id,
            operation_id=report.operation_id,
            state=report.state,
            changed=report.changed,
            actor=actor,
            trigger_source=trigger_source,
        )
        return report

    @staticmethod
    def _intent_strategy_public_id(session: Session, intent_id: uuid.UUID) -> str:
        """The public id of the strategy that owns an intent (order -> run -> strategy)."""

        public_id = session.execute(
            select(Strategy.strategy_id)
            .join(StrategyRun, StrategyRun.strategy_id == Strategy.id)
            .join(PaperOrder, PaperOrder.strategy_run_id == StrategyRun.id)
            .where(PaperOrder.id == intent_id)
        ).scalar_one_or_none()
        if public_id is None:
            raise IntentNotFoundError(intent_id)
        return public_id

    def get_kill_switch_state(self) -> KillSwitchStateSnapshot:
        """Thin wrapper over the shared gate loader (R-Q1)."""
        with session_scope(self.settings) as session:
            return load_trading_gate_state(session).kill_switch

    def trip_kill_switch(
        self,
        *,
        reason: str | None = None,
        actor: str = "local_operator",
        trigger_source: str = "operator_control_script",
    ) -> KillSwitchControlReport:
        return self._set_kill_switch_state(
            target_state=KillSwitchState.TRIPPED,
            action="trip",
            reason=reason,
            actor=actor,
            trigger_source=trigger_source,
        )

    def reset_kill_switch(
        self,
        *,
        reason: str | None = None,
        actor: str = "local_operator",
        trigger_source: str = "operator_control_script",
    ) -> KillSwitchControlReport:
        return self._set_kill_switch_state(
            target_state=KillSwitchState.ARMED,
            action="reset",
            reason=reason,
            actor=actor,
            trigger_source=trigger_source,
        )

    def _resolve_audit_strategy_record(self, session: Session) -> Strategy:
        """Strategy row that anchors the kill-switch audit run's FK (D-15).

        The kill switch must stay operable when strategy configuration is
        unhealthy, so an already-persisted row is used without consulting the
        registry at all; the registry is only needed to create the row on a
        brand-new database.
        """
        existing = session.execute(
            select(Strategy).where(Strategy.strategy_id == _DEFAULT_KILL_SWITCH_STRATEGY_ID)
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        try:
            metadata = self.registry.resolve(_DEFAULT_KILL_SWITCH_STRATEGY_ID).metadata
        except Exception as exc:
            raise ControlStateUnavailableError(
                f"Cannot anchor the kill-switch audit run: strategy "
                f"'{_DEFAULT_KILL_SWITCH_STRATEGY_ID}' has no persisted row and is not "
                f"resolvable from the strategy registry ({exc})."
            ) from exc
        return _ensure_audit_strategy_record(session, metadata)

    @_translate_db_errors(ControlWriteError)
    def _set_kill_switch_state(
        self,
        *,
        target_state: KillSwitchState,
        action: str,
        reason: str | None,
        actor: str,
        trigger_source: str,
    ) -> KillSwitchControlReport:
        with session_scope(self.settings) as session:
            strategy_record = self._resolve_audit_strategy_record(session)
            control = _load_global_kill_switch(session, for_update=True)
            # D-11a: one DB clock read after the row lock. clock_timestamp() is
            # >= now() (the transaction start that StrategyRun.started_at
            # records), so completed_at >= started_at by construction.
            changed_at = _db_clock_now(session)
            previous_state = control.state
            changed = previous_state != target_state

            strategy_run = StrategyRun(
                strategy_id=strategy_record.id,
                run_type=StrategyRunType.OPERATOR_CONTROL,
                status=StrategyRunStatus.PENDING,
                trigger_source=trigger_source,
                parameters_snapshot={
                    "scope": "global_kill_switch",
                    "action": action,
                    "actor": actor,
                    "reason": reason,
                    "previous_state": previous_state.value,
                    "requested_state": target_state.value,
                },
                result_summary={
                    "stage": "pending",
                    "scope": "global_kill_switch",
                    "action": action,
                    "requested_state": target_state.value,
                },
            )
            session.add(strategy_run)
            session.flush()

            if changed:
                # D-10 only requires the unchanged audit rows (run + event); a
                # reaffirming no-op must not overwrite the state row's "last
                # change" provenance (mirrors _set_strategy_status).
                control.state = target_state
                control.last_changed_at = changed_at
                control.last_change_actor = actor
                control.last_change_reason = reason
                control.last_change_run_id = strategy_run.id
                session.flush()
            session.refresh(control)

            state_snapshot = _serialize_kill_switch(control).to_dict()
            result_summary = {
                "stage": "completed",
                "scope": "global_kill_switch",
                "action": action,
                "changed": changed,
                "actor": actor,
                "reason": reason,
                "previous_state": previous_state.value,
                "current_state": control.state.value,
                "changed_at": changed_at.isoformat(),
                "state_snapshot": state_snapshot,
            }
            strategy_run.status = StrategyRunStatus.SUCCEEDED
            strategy_run.completed_at = changed_at
            strategy_run.result_summary = result_summary

            event_type = f"kill_switch_{action}"
            severity = "warning" if target_state == KillSwitchState.TRIPPED else "info"
            blocks_execution = target_state == KillSwitchState.TRIPPED
            session.add(
                ExecutionEvent(
                    strategy_run_id=strategy_run.id,
                    paper_order_id=None,
                    event_type=event_type,
                    severity=severity,
                    blocks_execution=blocks_execution,
                    event_at=changed_at,
                    message=_build_kill_switch_message(
                        action=action,
                        current_state=control.state.value,
                        changed=changed,
                        reason=reason,
                    ),
                    details=result_summary,
                )
            )
            session.flush()
            session.refresh(strategy_run)

            report = KillSwitchControlReport(
                run_id=str(strategy_run.id),
                action=action,
                previous_state=previous_state.value,
                current_state=control.state.value,
                changed=changed,
                trigger_source=strategy_run.trigger_source,
                started_at=strategy_run.started_at.isoformat(),
                completed_at=strategy_run.completed_at.isoformat() if strategy_run.completed_at else None,
                reason=reason,
                actor=actor,
                state_snapshot=state_snapshot,
                result_summary=strategy_run.result_summary,
            )

        emit_structured_log(
            self._logger,
            logging.WARNING if target_state == KillSwitchState.TRIPPED else logging.INFO,
            "kill_switch_applied",
            run_id=report.run_id,
            kill_switch_state=report.current_state,
            blocked_reason=(
                BLOCKED_REASON_GLOBAL_KILL_SWITCH if report.current_state == KillSwitchState.TRIPPED.value else None
            ),
            action=action,
            actor=actor,
            changed=changed,
            trigger_source=trigger_source,
        )
        return report


def load_strategy_control_state(
    strategy_id: str,
    *,
    settings: Settings | None = None,
    registry: StrategyRegistry | None = None,
) -> StrategyControlState:
    return OperatorControlService(settings=settings, registry=registry).get_strategy_state(strategy_id)


def ensure_strategy_control_state(
    strategy_id: str,
    *,
    settings: Settings | None = None,
    registry: StrategyRegistry | None = None,
) -> StrategyControlState:
    return OperatorControlService(settings=settings, registry=registry).ensure_strategy_state(strategy_id)


def load_kill_switch_state(
    *,
    settings: Settings | None = None,
    registry: StrategyRegistry | None = None,
) -> KillSwitchStateSnapshot:
    return OperatorControlService(settings=settings, registry=registry).get_kill_switch_state()


def render_operator_control_report(
    report: OperatorControlReport,
    *,
    summary_format: str = "json",
) -> str:
    if summary_format == "json":
        return json.dumps(report.to_dict(), indent=2)

    lines = [
        f"# Operator Control: {report.strategy_id}",
        "",
        f"- Action: `{report.action}`",
        f"- Previous status: `{report.previous_status}`",
        f"- Current status: `{report.current_status}`",
        f"- Changed: `{str(report.changed).lower()}`",
        f"- Actor: `{report.actor}`",
        f"- Trigger source: `{report.trigger_source}`",
    ]
    if report.reason:
        lines.append(f"- Reason: {report.reason}")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(report.result_summary, indent=2))
    lines.append("```")
    return "\n".join(lines)


def render_kill_switch_report(
    report: KillSwitchControlReport,
    *,
    summary_format: str = "json",
) -> str:
    if summary_format == "json":
        return json.dumps(report.to_dict(), indent=2)

    lines = [
        "# Operator Control: global_kill_switch",
        "",
        f"- Action: `{report.action}`",
        f"- Previous state: `{report.previous_state}`",
        f"- Current state: `{report.current_state}`",
        f"- Changed: `{str(report.changed).lower()}`",
        f"- Actor: `{report.actor}`",
        f"- Trigger source: `{report.trigger_source}`",
    ]
    if report.reason:
        lines.append(f"- Reason: {report.reason}")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(report.state_snapshot, indent=2))
    lines.append("```")
    return "\n".join(lines)


def _db_clock_now(session: Session) -> datetime:
    """Return the DB wall clock (``clock_timestamp()``) normalized to UTC.

    Read inside the mutating transaction, after the row lock (D-11a), so the
    value is never earlier than the transaction start recorded as ``started_at``.
    """
    value = session.execute(select(sa_func.clock_timestamp())).scalar_one()
    if value.tzinfo is None:
        raise ControlWriteError("control state database error: naive DB clock timestamp")
    return value.astimezone(UTC)


def _ensure_locked_strategy_record(session: Session, metadata: Any) -> Strategy:
    """Race-safe get-or-create of the strategy row, returned row-locked.

    Two concurrent first-use callers both see "no row" and both INSERT; the
    loser hits the unique ``strategy_id`` constraint. The insert runs in a
    SAVEPOINT so that loss is recovered by re-selecting the winner's row
    instead of surfacing an ``IntegrityError``. The row is then re-read
    ``FOR UPDATE`` so ``previous_status``/``changed`` reflect the state after
    any concurrent mutator has committed.
    """
    try:
        with session.begin_nested():
            strategy_record = ensure_strategy_record(session, metadata)
    except IntegrityError:
        strategy_record = session.execute(
            select(Strategy).where(Strategy.strategy_id == metadata.strategy_id)
        ).scalar_one()
    session.refresh(strategy_record, with_for_update=True)
    return strategy_record


def _ensure_audit_strategy_record(session: Session, metadata: Any) -> Strategy:
    """Strategy row used only as the audit run's FK (no status is read)."""
    try:
        with session.begin_nested():
            return ensure_strategy_record(session, metadata)
    except IntegrityError:
        return session.execute(
            select(Strategy).where(Strategy.strategy_id == metadata.strategy_id)
        ).scalar_one()


def _load_global_kill_switch(session: Session, *, for_update: bool = False) -> SystemControl:
    """Load the kill-switch row; mutators pass ``for_update`` to serialize."""
    statement = select(SystemControl).where(SystemControl.name == GLOBAL_KILL_SWITCH_NAME)
    if for_update:
        statement = statement.with_for_update()
    control = session.execute(statement).scalar_one_or_none()
    if control is None:
        raise ControlStateUnavailableError(
            f"Missing global kill switch row '{GLOBAL_KILL_SWITCH_NAME}'; "
            "database migrations may not be current."
        )
    return control


def _serialize_kill_switch(control: SystemControl) -> KillSwitchStateSnapshot:
    return KillSwitchStateSnapshot(
        name=control.name,
        state=control.state.value,
        is_tripped=control.state == KillSwitchState.TRIPPED,
        last_changed_at=control.last_changed_at.isoformat(),
        last_change_actor=control.last_change_actor,
        last_change_reason=control.last_change_reason,
        last_change_run_id=(
            str(control.last_change_run_id) if control.last_change_run_id is not None else None
        ),
    )


def _build_kill_switch_message(
    *,
    action: str,
    current_state: str,
    changed: bool,
    reason: str | None,
) -> str:
    if changed:
        base = f"Global kill switch {action} by operator control; current state is {current_state}."
    else:
        base = (
            f"Global kill switch {action} reaffirmed by operator control; "
            f"current state remains {current_state}."
        )
    if reason:
        return f"{base} Reason: {reason}"
    return base


def _serialize_strategy_control_state(strategy_record: Strategy) -> StrategyControlState:
    return StrategyControlState(
        strategy_id=strategy_record.strategy_id,
        display_name=strategy_record.display_name,
        status=strategy_record.status.value,
        updated_at=strategy_record.updated_at.isoformat(),
    )


def _build_owner_message(
    *,
    kind: str,
    previous: str | None,
    new: str | None,
    new_owner_disabled: bool,
    reason: str,
) -> str:
    if kind == OWNER_KIND_REAFFIRM:
        return (
            f"Active paper strategy reaffirmed as {new or 'none'}: unchanged, no checks "
            f"evaluated. Reason: {reason}"
        )
    suffix = " The new owner was left disabled." if new_owner_disabled else ""
    if kind == OWNER_KIND_SEEDING:
        return f"Active paper strategy seeded: none -> {new}.{suffix} Reason: {reason}"
    if kind == OWNER_KIND_RELEASE:
        return f"Active paper strategy released: {previous} -> none. Reason: {reason}"
    return f"Active paper strategy handed over: {previous} -> {new}.{suffix} Reason: {reason}"


def _build_control_message(
    *,
    strategy_id: str,
    action: str,
    current_status: str,
    changed: bool,
    reason: str | None,
) -> str:
    if changed:
        base = f"Strategy '{strategy_id}' set to {current_status} by operator control."
    else:
        base = f"Strategy '{strategy_id}' already {current_status}; operator control was reaffirmed."
    if reason:
        return f"{base} Reason: {reason}"
    return base
