"""Persisted operator kill-switch controls and audit helpers."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from trading_platform.core.logging import emit_structured_log
from trading_platform.core.settings import Settings, load_settings
from trading_platform.db.models import (
    GLOBAL_KILL_SWITCH_NAME,
    ExecutionEvent,
    KillSwitchState,
    Strategy,
    StrategyRun,
    StrategyRunStatus,
    StrategyRunType,
    StrategyStatus,
    SystemControl,
)
from trading_platform.db.session import session_scope
from trading_platform.services.bootstrap import ensure_strategy_record
from trading_platform.strategies.registry import StrategyRegistry, build_default_registry

_BLOCKED_REASON_STRATEGY_DISABLED = "strategy_disabled"
BLOCKED_REASON_GLOBAL_KILL_SWITCH = "global_kill_switch_tripped"
_DEFAULT_KILL_SWITCH_STRATEGY_ID = "trend_following_daily"


@dataclass(frozen=True)
class StrategyControlState:
    strategy_id: str
    display_name: str
    status: str
    updated_at: str

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


class OperatorControlService:
    def __init__(
        self,
        settings: Settings | None = None,
        registry: StrategyRegistry | None = None,
    ) -> None:
        self._settings = settings
        self._registry = registry
        self._logger = logging.getLogger("trading_platform.operator_controls")

    @property
    def settings(self) -> Settings:
        return self._settings or load_settings()

    @property
    def registry(self) -> StrategyRegistry:
        return self._registry or build_default_registry(self.settings)

    def get_strategy_state(self, strategy_id: str) -> StrategyControlState:
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
        changed_at = datetime.now(UTC)
        with session_scope(self.settings) as session:
            strategy_record = ensure_strategy_record(session, metadata)
            previous_status = strategy_record.status
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

    def get_kill_switch_state(self) -> KillSwitchStateSnapshot:
        with session_scope(self.settings) as session:
            control = _load_global_kill_switch(session)
            return _serialize_kill_switch(control)

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

    def _set_kill_switch_state(
        self,
        *,
        target_state: KillSwitchState,
        action: str,
        reason: str | None,
        actor: str,
        trigger_source: str,
    ) -> KillSwitchControlReport:
        metadata = self.registry.resolve(_DEFAULT_KILL_SWITCH_STRATEGY_ID).metadata
        changed_at = datetime.now(UTC)
        with session_scope(self.settings) as session:
            strategy_record = ensure_strategy_record(session, metadata)
            control = _load_global_kill_switch(session)
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


def _load_global_kill_switch(session) -> SystemControl:
    control = session.execute(
        select(SystemControl).where(SystemControl.name == GLOBAL_KILL_SWITCH_NAME)
    ).scalar_one_or_none()
    if control is None:
        raise LookupError(
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
