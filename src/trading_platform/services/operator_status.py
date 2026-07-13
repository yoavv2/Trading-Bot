"""Operator-facing status summary composed from persisted read services."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from trading_platform.core.logging import emit_structured_log, get_logger
from trading_platform.core.settings import Settings, load_settings
from trading_platform.services.operator_controls import (
    load_kill_switch_state,
    load_strategy_control_state,
)
from trading_platform.services.operator_reads import OperatorReadFilters, OperatorReadService
from trading_platform.strategies.registry import StrategyRegistry


@dataclass(frozen=True)
class OperatorStatusReport:
    strategy: dict[str, Any]
    kill_switch: dict[str, Any]
    latest_control: dict[str, Any] | None
    latest_account_snapshot: dict[str, Any] | None
    latest_paper_execution: dict[str, Any] | None
    latest_reconciliation: dict[str, Any] | None
    latest_paper_session: dict[str, Any] | None
    recent_blocking_events: list[dict[str, Any]]
    recent_blocked_paper_executions: list[dict[str, Any]]
    recent_failed_runs: list[dict[str, Any]]
    inspection_limit: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "kill_switch": self.kill_switch,
            "latest_control": self.latest_control,
            "latest_account_snapshot": self.latest_account_snapshot,
            "latest_paper_execution": self.latest_paper_execution,
            "latest_reconciliation": self.latest_reconciliation,
            "latest_paper_session": self.latest_paper_session,
            "recent_blocking_events": self.recent_blocking_events,
            "recent_blocked_paper_executions": self.recent_blocked_paper_executions,
            "recent_failed_runs": self.recent_failed_runs,
            "inspection_limit": self.inspection_limit,
        }


class OperatorStatusService:
    def __init__(
        self,
        settings: Settings | None = None,
        registry: StrategyRegistry | None = None,
    ) -> None:
        self._settings = settings
        self._registry = registry
        self._logger = get_logger("trading_platform.operator_status")

    @property
    def settings(self) -> Settings:
        return self._settings or load_settings()

    def build_report(
        self,
        *,
        strategy_id: str = "trend_following_daily",
        inspection_limit: int = 5,
    ) -> OperatorStatusReport:
        operator_reads = OperatorReadService(self.settings)
        strategy_state = load_strategy_control_state(
            strategy_id,
            settings=self.settings,
            registry=self._registry,
        )
        kill_switch_snapshot = load_kill_switch_state(
            settings=self.settings,
            registry=self._registry,
        )

        latest_control = _first_item(
            operator_reads.list_runs(
                OperatorReadFilters(
                    strategy_id=strategy_id,
                    run_type="operator_control",
                    limit=1,
                )
            )
        )
        latest_account_snapshot = _first_item(
            operator_reads.list_account_snapshots(
                OperatorReadFilters(strategy_id=strategy_id, limit=1)
            )
        )
        latest_paper_execution = _first_item(
            operator_reads.list_runs(
                OperatorReadFilters(
                    strategy_id=strategy_id,
                    run_type="paper_execution",
                    limit=1,
                )
            )
        )
        latest_reconciliation = _first_item(
            operator_reads.list_runs(
                OperatorReadFilters(
                    strategy_id=strategy_id,
                    run_type="reconciliation",
                    limit=1,
                )
            )
        )
        recent_blocking_events = _select_blocking_events(
            operator_reads.list_execution_events(
                OperatorReadFilters(
                    strategy_id=strategy_id,
                    limit=max(inspection_limit * 3, inspection_limit),
                )
            ),
            limit=inspection_limit,
        )
        recent_blocked_paper_executions = operator_reads.list_blocked_paper_executions(
            OperatorReadFilters(
                strategy_id=strategy_id,
                limit=inspection_limit,
            )
        )
        recent_failed_runs = operator_reads.list_runs(
            OperatorReadFilters(
                strategy_id=strategy_id,
                status="failed",
                limit=inspection_limit,
            )
        )
        latest_paper_session = _resolve_latest_paper_session(
            latest_paper_execution=latest_paper_execution,
            latest_reconciliation=latest_reconciliation,
        )

        report = OperatorStatusReport(
            strategy=strategy_state.to_dict(),
            kill_switch=kill_switch_snapshot.to_dict(),
            latest_control=latest_control,
            latest_account_snapshot=latest_account_snapshot,
            latest_paper_execution=latest_paper_execution,
            latest_reconciliation=latest_reconciliation,
            latest_paper_session=latest_paper_session,
            recent_blocking_events=recent_blocking_events,
            recent_blocked_paper_executions=recent_blocked_paper_executions,
            recent_failed_runs=recent_failed_runs,
            inspection_limit=inspection_limit,
        )
        emit_structured_log(
            self._logger,
            logging.INFO,
            "operator_status_generated",
            strategy_id=strategy_id,
            strategy_status=strategy_state.status,
            kill_switch_state=kill_switch_snapshot.state,
            inspection_limit=inspection_limit,
            blocking_event_count=len(recent_blocking_events),
            blocked_paper_execution_count=len(recent_blocked_paper_executions),
            failed_run_count=len(recent_failed_runs),
        )
        return report


def build_operator_status_report(
    *,
    strategy_id: str = "trend_following_daily",
    inspection_limit: int = 5,
    settings: Settings | None = None,
    registry: StrategyRegistry | None = None,
) -> OperatorStatusReport:
    return OperatorStatusService(settings=settings, registry=registry).build_report(
        strategy_id=strategy_id,
        inspection_limit=inspection_limit,
    )


def render_operator_status_report(
    report: OperatorStatusReport,
    *,
    summary_format: str = "markdown",
) -> str:
    if summary_format == "json":
        return json.dumps(report.to_dict(), indent=2)

    lines = [
        f"# Operator Status: {report.strategy['strategy_id']}",
        "",
        f"- Status: `{report.strategy['status']}`",
        f"- Execution enabled: `{str(report.strategy['is_execution_enabled']).lower()}`",
        f"- Updated at: `{report.strategy['updated_at']}`",
        f"- Global kill switch: `{report.kill_switch['state']}` "
        f"(last changed `{report.kill_switch['last_changed_at']}` by "
        f"`{report.kill_switch['last_change_actor']}`)",
    ]
    if report.latest_paper_session is not None:
        lines.extend(
            [
                f"- Latest paper-session outcome: `{report.latest_paper_session['action']}`",
                f"- Latest paper-session status: `{report.latest_paper_session['status']}`",
            ]
        )
    if report.latest_account_snapshot is not None:
        lines.append(
            f"- Latest equity snapshot: `{report.latest_account_snapshot['total_equity']}` at "
            f"`{report.latest_account_snapshot['snapshot_at']}`"
        )

    if report.recent_blocking_events:
        lines.extend(["", "## Recent Blocking Events"])
        for event in report.recent_blocking_events:
            lines.append(
                f"- `{event['event_type']}` at `{event['event_at']}`: {event['message']}"
            )

    if report.recent_blocked_paper_executions:
        lines.extend(["", "## Recent Blocked Paper Executions"])
        for blocked in report.recent_blocked_paper_executions:
            lines.append(
                f"- `{blocked['blocked_reason']}` run `{blocked['run_id']}` at "
                f"`{blocked['started_at']}` (action=`{blocked['action']}`)"
            )

    if report.recent_failed_runs:
        lines.extend(["", "## Recent Failed Runs"])
        for run in report.recent_failed_runs:
            lines.append(
                f"- `{run['run_type']}` run `{run['run_id']}` at `{run['started_at']}` with status `{run['status']}`"
            )

    return "\n".join(lines)


def _first_item(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    return items[0] if items else None


def _select_blocking_events(
    items: list[dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    selected = [
        item
        for item in items
        if item["blocks_execution"] or item["severity"] in {"warning", "error", "critical"}
    ]
    return selected[:limit]


def _resolve_latest_paper_session(
    *,
    latest_paper_execution: dict[str, Any] | None,
    latest_reconciliation: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if latest_paper_execution is None and latest_reconciliation is None:
        return None

    paper_started = _parse_timestamp(latest_paper_execution["started_at"]) if latest_paper_execution else None
    recon_started = _parse_timestamp(latest_reconciliation["started_at"]) if latest_reconciliation else None

    if latest_paper_execution is not None and (
        latest_reconciliation is None
        or recon_started is None
        or (paper_started is not None and paper_started >= recon_started)
    ):
        result_summary = latest_paper_execution["result_summary"]
        return {
            "source": "paper_execution",
            "run_id": latest_paper_execution["run_id"],
            "status": latest_paper_execution["status"],
            "action": result_summary.get("action") or result_summary.get("stage") or "paper_execution",
            "blocked_reason": result_summary.get("blocked_reason"),
            "started_at": latest_paper_execution["started_at"],
            "completed_at": latest_paper_execution["completed_at"],
            "result_summary": result_summary,
        }

    result_summary = latest_reconciliation["result_summary"]
    # RECON-09: since the read-only orchestrator (09-03) no longer emits a synthetic
    # "reconciliation_clean" ExecutionEvent, derive the "clean" label directly from the
    # materialized report's finding_count == 0 signal (rather than an old event_type
    # string), alongside the existing blocks_execution check.
    is_clean = not result_summary.get("blocks_execution") and result_summary.get("finding_count", 0) == 0
    return {
        "source": "reconciliation",
        "run_id": latest_reconciliation["run_id"],
        "status": latest_reconciliation["status"],
        "action": "reconciliation_clean" if is_clean else "blocked_reconciliation",
        "blocked_reason": "reconciliation_blocks_execution" if result_summary.get("blocks_execution") else None,
        "started_at": latest_reconciliation["started_at"],
        "completed_at": latest_reconciliation["completed_at"],
        "result_summary": result_summary,
    }


def _parse_timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)
