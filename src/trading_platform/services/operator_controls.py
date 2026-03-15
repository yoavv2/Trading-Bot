"""Persisted operator kill-switch controls and audit helpers."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from trading_platform.core.logging import emit_structured_log
from trading_platform.core.settings import Settings, load_settings
from trading_platform.db.models import (
    ExecutionEvent,
    Strategy,
    StrategyRun,
    StrategyRunStatus,
    StrategyRunType,
    StrategyStatus,
)
from trading_platform.db.session import session_scope
from trading_platform.services.bootstrap import ensure_strategy_record
from trading_platform.strategies.registry import StrategyRegistry, build_default_registry

_BLOCKED_REASON_STRATEGY_DISABLED = "strategy_disabled"


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


def load_strategy_control_state(
    strategy_id: str,
    *,
    settings: Settings | None = None,
    registry: StrategyRegistry | None = None,
) -> StrategyControlState:
    return OperatorControlService(settings=settings, registry=registry).get_strategy_state(strategy_id)


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
