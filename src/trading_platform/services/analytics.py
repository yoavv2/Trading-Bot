"""Strategy analytics summaries derived from persisted platform state."""

from __future__ import annotations

import json
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import distinct, func, select

from trading_platform.core.settings import Settings, load_settings
from trading_platform.db.models import (
    AccountSnapshot,
    ExecutionEvent,
    PaperFill,
    PaperOrder,
    Position,
    Strategy,
    StrategyRun,
    StrategyRunStatus,
    StrategyRunType,
    StrategyStatus,
)
from trading_platform.db.session import session_scope
from trading_platform.services.backtest_reporting import materialize_backtest_report
from trading_platform.services.operator_reads import OperatorReadFilters, OperatorReadService
from trading_platform.strategies.registry import UnknownStrategyError, build_default_registry


@dataclass(frozen=True)
class StrategyAnalyticsRequest:
    strategy_id: str = "trend_following_daily"
    backtest_run_id: str | None = None
    paper_run_id: str | None = None
    inspection_limit: int = 5


class AnalyticsService(ABC):
    @abstractmethod
    def describe(self) -> dict[str, Any]:
        """Describe the analytics capability exposed to the platform."""

    @abstractmethod
    def summarize(self, payload: object) -> object:
        """Summarize persisted analytics state for a strategy."""


class StrategyAnalyticsService(AnalyticsService):
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings

    @property
    def settings(self) -> Settings:
        return self._settings or load_settings()

    def describe(self) -> dict[str, Any]:
        return {
            "service": "analytics",
            "status": "available",
            "detail": "Backtest and paper-trading analytics are derived from persisted artifacts.",
        }

    def summarize(self, payload: object) -> object:
        request = _coerce_request(payload)
        return self.summarize_strategy(
            strategy_id=request.strategy_id,
            backtest_run_id=request.backtest_run_id,
            paper_run_id=request.paper_run_id,
            inspection_limit=request.inspection_limit,
        )

    def summarize_strategy(
        self,
        *,
        strategy_id: str = "trend_following_daily",
        backtest_run_id: str | None = None,
        paper_run_id: str | None = None,
        inspection_limit: int = 5,
    ) -> dict[str, Any]:
        registry = build_default_registry(self.settings)
        try:
            metadata = registry.resolve(strategy_id).metadata
        except UnknownStrategyError as exc:
            raise LookupError(str(exc)) from exc

        with session_scope(self.settings) as session:
            strategy_record = session.execute(
                select(Strategy).where(Strategy.strategy_id == strategy_id)
            ).scalar_one_or_none()

            return {
                "strategy": {
                    "strategy_id": strategy_id,
                    "display_name": strategy_record.display_name if strategy_record is not None else metadata.display_name,
                    "status": (
                        strategy_record.status.value
                        if strategy_record is not None
                        else StrategyStatus.ACTIVE.value
                        if metadata.enabled
                        else StrategyStatus.DISABLED.value
                    ),
                    "version": strategy_record.version if strategy_record is not None else metadata.version,
                },
                "backtest": self._summarize_backtest(strategy_id=strategy_id, run_id=backtest_run_id),
                "paper": self._summarize_paper(
                    strategy=strategy_record,
                    paper_run_id=paper_run_id,
                    inspection_limit=inspection_limit,
                ),
            }

    def _summarize_backtest(
        self,
        *,
        strategy_id: str,
        run_id: str | None,
    ) -> dict[str, Any] | None:
        try:
            report = materialize_backtest_report(
                run_id=run_id,
                strategy_id=strategy_id,
                settings=self.settings,
            )
        except LookupError:
            if run_id is not None:
                raise
            return None

        return {
            "run_id": report["run_id"],
            "status": report["status"],
            "started_at": report["started_at"],
            "completed_at": report["completed_at"],
            "summary": report["summary"],
            "metrics": report["metrics"],
            "equity_curve": report["equity_curve"],
        }

    def _summarize_paper(
        self,
        *,
        strategy: Strategy | None,
        paper_run_id: str | None,
        inspection_limit: int,
    ) -> dict[str, Any]:
        if strategy is None:
            if paper_run_id is not None:
                raise LookupError(f"Paper run '{paper_run_id}' was not found.")
            return {
                "latest_account_snapshot": None,
                "latest_paper_run": None,
                "latest_reconciliation": None,
                "submitted_order_count": 0,
                "filled_order_count": 0,
                "fill_count": 0,
                "blocked_session_count": 0,
                "open_position_count": 0,
                "open_position_cost_basis": 0.0,
                "current_exposure_pct": 0.0,
                "recent_execution_findings": [],
            }

        with session_scope(self.settings) as session:
            latest_snapshot = session.execute(
                select(AccountSnapshot)
                .where(AccountSnapshot.strategy_id == strategy.id)
                .order_by(AccountSnapshot.snapshot_at.desc())
            ).scalars().first()

            latest_paper_run = self._resolve_paper_run(
                session,
                strategy_id=strategy.id,
                paper_run_id=paper_run_id,
            )
            latest_reconciliation = session.execute(
                select(StrategyRun)
                .where(
                    StrategyRun.strategy_id == strategy.id,
                    StrategyRun.run_type == StrategyRunType.RECONCILIATION,
                )
                .order_by(StrategyRun.started_at.desc())
            ).scalars().first()

            submitted_order_count = session.execute(
                select(func.count(PaperOrder.id))
                .join(StrategyRun, StrategyRun.id == PaperOrder.strategy_run_id)
                .where(
                    StrategyRun.strategy_id == strategy.id,
                    PaperOrder.submitted_at.is_not(None),
                )
            ).scalar_one()
            filled_order_count = session.execute(
                select(func.count(PaperOrder.id))
                .join(StrategyRun, StrategyRun.id == PaperOrder.strategy_run_id)
                .where(
                    StrategyRun.strategy_id == strategy.id,
                    PaperOrder.filled_at.is_not(None),
                )
            ).scalar_one()
            fill_count = session.execute(
                select(func.count(PaperFill.id))
                .join(PaperOrder, PaperOrder.id == PaperFill.paper_order_id)
                .join(StrategyRun, StrategyRun.id == PaperOrder.strategy_run_id)
                .where(StrategyRun.strategy_id == strategy.id)
            ).scalar_one()
            blocked_session_count = session.execute(
                select(func.count(distinct(StrategyRun.id)))
                .join(ExecutionEvent, ExecutionEvent.strategy_run_id == StrategyRun.id)
                .where(
                    StrategyRun.strategy_id == strategy.id,
                    StrategyRun.run_type == StrategyRunType.RECONCILIATION,
                    ExecutionEvent.blocks_execution.is_(True),
                )
            ).scalar_one()
            open_position_count = session.execute(
                select(func.count(Position.id))
                .where(
                    Position.strategy_id == strategy.id,
                    Position.status == "open",
                )
            ).scalar_one()
            open_position_cost_basis = session.execute(
                select(func.coalesce(func.sum(Position.cost_basis), Decimal("0")))
                .where(
                    Position.strategy_id == strategy.id,
                    Position.status == "open",
                )
            ).scalar_one()

            recent_findings = session.execute(
                select(ExecutionEvent)
                .join(StrategyRun, StrategyRun.id == ExecutionEvent.strategy_run_id)
                .where(StrategyRun.strategy_id == strategy.id)
                .order_by(ExecutionEvent.event_at.desc(), ExecutionEvent.created_at.desc())
                .limit(max(inspection_limit, 1))
            ).scalars().all()

        exposure_pct = Decimal("0")
        if latest_snapshot is not None and latest_snapshot.total_equity > 0:
            exposure_pct = (latest_snapshot.gross_exposure / latest_snapshot.total_equity) * Decimal("100")

        return {
            "latest_account_snapshot": _serialize_account_snapshot(latest_snapshot),
            "latest_paper_run": _serialize_run(latest_paper_run),
            "latest_reconciliation": _serialize_reconciliation(latest_reconciliation),
            "submitted_order_count": submitted_order_count,
            "filled_order_count": filled_order_count,
            "fill_count": fill_count,
            "blocked_session_count": blocked_session_count,
            "open_position_count": open_position_count,
            "open_position_cost_basis": _decimal_value(open_position_cost_basis),
            "current_exposure_pct": _decimal_value(exposure_pct),
            "recent_execution_findings": [
                {
                    "event_type": finding.event_type,
                    "severity": finding.severity,
                    "blocks_execution": finding.blocks_execution,
                    "event_at": finding.event_at.isoformat(),
                    "message": finding.message,
                    "details": finding.details,
                }
                for finding in recent_findings
            ],
        }

    @staticmethod
    def _resolve_paper_run(
        session,
        *,
        strategy_id,
        paper_run_id: str | None,
    ) -> StrategyRun | None:
        if paper_run_id is not None:
            strategy_run = session.get(StrategyRun, uuid.UUID(paper_run_id))
            if strategy_run is None:
                raise LookupError(f"Paper run '{paper_run_id}' was not found.")
            if strategy_run.strategy_id != strategy_id:
                raise ValueError(f"Run '{paper_run_id}' does not belong to the requested strategy.")
            if strategy_run.run_type != StrategyRunType.PAPER_EXECUTION:
                raise ValueError(f"Run '{paper_run_id}' is not a paper execution run.")
            return strategy_run

        return session.execute(
            select(StrategyRun)
            .where(
                StrategyRun.strategy_id == strategy_id,
                StrategyRun.run_type == StrategyRunType.PAPER_EXECUTION,
                StrategyRun.status == StrategyRunStatus.SUCCEEDED,
            )
            .order_by(StrategyRun.started_at.desc())
        ).scalars().first()


def _coerce_request(payload: object) -> StrategyAnalyticsRequest:
    if payload is None:
        return StrategyAnalyticsRequest()
    if isinstance(payload, StrategyAnalyticsRequest):
        return payload
    if isinstance(payload, dict):
        return StrategyAnalyticsRequest(
            strategy_id=payload.get("strategy_id", "trend_following_daily"),
            backtest_run_id=payload.get("backtest_run_id"),
            paper_run_id=payload.get("paper_run_id"),
            inspection_limit=payload.get("inspection_limit", 5),
        )
    raise TypeError("Analytics payload must be a StrategyAnalyticsRequest or mapping.")


def _serialize_run(strategy_run: StrategyRun | None) -> dict[str, Any] | None:
    if strategy_run is None:
        return None
    return {
        "run_id": str(strategy_run.id),
        "run_type": strategy_run.run_type.value,
        "status": strategy_run.status.value,
        "trigger_source": strategy_run.trigger_source,
        "started_at": strategy_run.started_at.isoformat(),
        "completed_at": strategy_run.completed_at.isoformat() if strategy_run.completed_at else None,
        "result_summary": strategy_run.result_summary,
    }


def _serialize_account_snapshot(snapshot: AccountSnapshot | None) -> dict[str, Any] | None:
    if snapshot is None:
        return None
    return {
        "snapshot_at": snapshot.snapshot_at.isoformat(),
        "snapshot_source": snapshot.snapshot_source,
        "cash": _decimal_value(snapshot.cash),
        "gross_exposure": _decimal_value(snapshot.gross_exposure),
        "total_equity": _decimal_value(snapshot.total_equity),
        "buying_power": _decimal_value(snapshot.buying_power),
        "open_positions": snapshot.open_positions,
    }


def _serialize_reconciliation(strategy_run: StrategyRun | None) -> dict[str, Any] | None:
    if strategy_run is None:
        return None
    result_summary = strategy_run.result_summary
    return {
        "run_id": str(strategy_run.id),
        "status": strategy_run.status.value,
        "as_of_session": result_summary.get("as_of_session"),
        "finding_count": result_summary.get("finding_count", 0),
        "blocking_count": result_summary.get("blocking_count", 0),
        "blocks_execution": result_summary.get("blocks_execution", False),
        "completed_at": strategy_run.completed_at.isoformat() if strategy_run.completed_at else None,
    }


def _decimal_value(value: Decimal | None) -> float:
    if value is None:
        return 0.0
    return float(value)


def build_strategy_analytics_report(
    *,
    strategy_id: str = "trend_following_daily",
    backtest_run_id: str | None = None,
    paper_run_id: str | None = None,
    inspection_limit: int = 5,
    settings: Settings | None = None,
) -> dict[str, Any]:
    resolved_settings = settings or load_settings()
    analytics_service = StrategyAnalyticsService(resolved_settings)
    operator_reads = OperatorReadService(resolved_settings)

    summary = analytics_service.summarize_strategy(
        strategy_id=strategy_id,
        backtest_run_id=backtest_run_id,
        paper_run_id=paper_run_id,
        inspection_limit=inspection_limit,
    )
    inspection = operator_reads.inspect_strategy(
        OperatorReadFilters(
            strategy_id=strategy_id,
            limit=inspection_limit,
        )
    )

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "strategy": summary["strategy"],
        "backtest": summary["backtest"],
        "paper": summary["paper"],
        "inspection": inspection,
    }


def render_strategy_analytics_report(
    report: dict[str, Any],
    *,
    summary_format: str = "markdown",
) -> str:
    if summary_format == "json":
        return json.dumps(report, indent=2, sort_keys=True)
    if summary_format != "markdown":
        raise ValueError(f"Unsupported summary format '{summary_format}'.")

    strategy = report["strategy"]
    backtest = report["backtest"]
    paper = report["paper"]
    inspection = report["inspection"]
    run_items = inspection["runs"]["items"]
    order_items = inspection["paper_orders"]["items"]
    event_items = inspection["execution_events"]["items"]

    lines = [
        f"# Strategy Analytics: {strategy['strategy_id']}",
        "",
        f"- Generated at: {report['generated_at']}",
        f"- Display name: {strategy['display_name']}",
        f"- Status: `{strategy['status']}`",
        f"- Version: `{strategy['version']}`",
        "",
    ]

    if backtest is None:
        lines.extend(
            [
                "## Backtest Summary",
                "",
                "No completed backtest report is available for this strategy.",
                "",
            ]
        )
    else:
        metrics = backtest["metrics"]
        lines.extend(
            [
                "## Backtest Summary",
                "",
                f"- Run ID: `{backtest['run_id']}`",
                f"- Status: `{backtest['status']}`",
                f"- Started: {backtest['started_at']}",
                f"- Completed: {backtest['completed_at'] or '-'}",
                f"- Total return: {metrics['total_return_pct']:.6f}%",
                f"- CAGR: {metrics['cagr_pct']:.6f}%",
                f"- Sharpe: {metrics['sharpe_ratio']:.6f}",
                f"- Sortino: {metrics['sortino_ratio']:.6f}",
                f"- Expectancy: {metrics['expectancy']:.6f}",
                f"- Turnover: {metrics['turnover_pct']:.6f}%",
                "",
            ]
        )

    latest_snapshot = paper["latest_account_snapshot"]
    latest_reconciliation = paper["latest_reconciliation"]
    lines.extend(
        [
            "## Paper Operations",
            "",
            (
                f"- Latest paper run: `{paper['latest_paper_run']['run_id']}`"
                if paper["latest_paper_run"]
                else "- Latest paper run: -"
            ),
            (
                f"- Latest equity: {latest_snapshot['total_equity']:.6f}"
                if latest_snapshot is not None
                else "- Latest equity: -"
            ),
            f"- Open positions: {paper['open_position_count']}",
            f"- Submitted orders: {paper['submitted_order_count']}",
            f"- Filled orders: {paper['filled_order_count']}",
            f"- Blocking reconciliation sessions: {paper['blocked_session_count']}",
            (
                f"- Latest reconciliation blocks execution: `{latest_reconciliation['blocks_execution']}`"
                if latest_reconciliation is not None
                else "- Latest reconciliation blocks execution: -"
            ),
            "",
            "## Recent Runs",
            "",
        ]
    )

    if run_items:
        for run in run_items:
            lines.append(
                f"- `{run['run_id']}` {run['run_type']} {run['status']} as_of={run['as_of_session'] or '-'}"
            )
    else:
        lines.append("- None")

    lines.extend(["", "## Recent Paper Orders", ""])
    if order_items:
        for order in order_items:
            lines.append(
                f"- {order['symbol']} {order['side']} {order['quantity']:.6f} "
                f"session={order['session_date']} status={order['status']} broker={order['broker_status'] or '-'}"
            )
    else:
        lines.append("- None")

    lines.extend(["", "## Recent Execution Events", ""])
    if event_items:
        for event in event_items:
            lines.append(
                f"- {event['event_at']} {event['severity']} {event['event_type']} "
                f"symbol={event['symbol'] or '-'} blocks_execution={event['blocks_execution']}"
            )
    else:
        lines.append("- None")

    return "\n".join(lines)
