"""Shared operator-facing inspection reads for persisted trading artifacts."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select

from trading_platform.core.settings import Settings, load_settings
from trading_platform.db.models import (
    AccountSnapshot,
    BacktestEquitySnapshot,
    BacktestSignal,
    BacktestTrade,
    ExecutionEvent,
    PaperFill,
    PaperOrder,
    Position,
    RiskEvent,
    Strategy,
    StrategyRun,
    StrategyRunStatus,
    StrategyRunType,
    Symbol,
)
from trading_platform.db.session import session_scope


@dataclass(frozen=True)
class OperatorReadFilters:
    strategy_id: str = "trend_following_daily"
    run_type: str | None = None
    status: str | None = None
    session_start: date | None = None
    session_end: date | None = None
    limit: int = 20


class OperatorReadService:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings

    @property
    def settings(self) -> Settings:
        return self._settings or load_settings()

    def inspect_strategy(self, filters: OperatorReadFilters | None = None) -> dict[str, Any]:
        resolved_filters = filters or OperatorReadFilters()
        runs = self.list_runs(resolved_filters)
        orders = self.list_paper_orders(resolved_filters)
        fills = self.list_paper_fills(resolved_filters)
        positions = self.list_positions(resolved_filters)
        snapshots = self.list_account_snapshots(resolved_filters)
        risk_events = self.list_risk_events(resolved_filters)
        execution_events = self.list_execution_events(resolved_filters)

        return {
            "filters": _serialize_filters(resolved_filters),
            "runs": {"count": len(runs), "items": runs},
            "paper_orders": {"count": len(orders), "items": orders},
            "paper_fills": {"count": len(fills), "items": fills},
            "positions": {"count": len(positions), "items": positions},
            "account_snapshots": {"count": len(snapshots), "items": snapshots},
            "risk_events": {"count": len(risk_events), "items": risk_events},
            "execution_events": {"count": len(execution_events), "items": execution_events},
        }

    def list_runs(self, filters: OperatorReadFilters | None = None) -> list[dict[str, Any]]:
        resolved_filters = filters or OperatorReadFilters()
        run_type = _coerce_run_type(resolved_filters.run_type)
        status = _coerce_run_status(resolved_filters.status)

        with session_scope(self.settings) as session:
            stmt = (
                select(StrategyRun, Strategy)
                .join(Strategy, Strategy.id == StrategyRun.strategy_id)
                .where(Strategy.strategy_id == resolved_filters.strategy_id)
            )
            if run_type is not None:
                stmt = stmt.where(StrategyRun.run_type == run_type)
            if status is not None:
                stmt = stmt.where(StrategyRun.status == status)
            rows = session.execute(
                stmt.order_by(StrategyRun.started_at.desc(), StrategyRun.created_at.desc())
            ).all()

        items = [_serialize_run_summary(strategy_run, strategy) for strategy_run, strategy in rows]
        return _apply_window_and_limit(items, resolved_filters, key_fn=_run_payload_date)

    def get_run_detail(self, run_id: str) -> dict[str, Any]:
        run_uuid = uuid.UUID(run_id)

        with session_scope(self.settings) as session:
            row = session.execute(
                select(StrategyRun, Strategy)
                .join(Strategy, Strategy.id == StrategyRun.strategy_id)
                .where(StrategyRun.id == run_uuid)
            ).one_or_none()
            if row is None:
                raise LookupError(f"Run '{run_id}' was not found.")

            strategy_run, strategy = row
            artifact_counts = {
                "backtest_signals": session.execute(
                    select(func.count(BacktestSignal.id)).where(BacktestSignal.strategy_run_id == strategy_run.id)
                ).scalar_one(),
                "backtest_trades": session.execute(
                    select(func.count(BacktestTrade.id)).where(BacktestTrade.strategy_run_id == strategy_run.id)
                ).scalar_one(),
                "backtest_equity_snapshots": session.execute(
                    select(func.count(BacktestEquitySnapshot.id)).where(
                        BacktestEquitySnapshot.strategy_run_id == strategy_run.id
                    )
                ).scalar_one(),
                "risk_events": session.execute(
                    select(func.count(RiskEvent.id)).where(RiskEvent.strategy_run_id == strategy_run.id)
                ).scalar_one(),
                "paper_orders": session.execute(
                    select(func.count(PaperOrder.id)).where(PaperOrder.strategy_run_id == strategy_run.id)
                ).scalar_one(),
                "paper_fills": session.execute(
                    select(func.count(PaperFill.id))
                    .join(PaperOrder, PaperOrder.id == PaperFill.paper_order_id)
                    .where(PaperOrder.strategy_run_id == strategy_run.id)
                ).scalar_one(),
                "execution_events": session.execute(
                    select(func.count(ExecutionEvent.id)).where(ExecutionEvent.strategy_run_id == strategy_run.id)
                ).scalar_one(),
            }

        return {
            "run": _serialize_run_summary(strategy_run, strategy),
            "artifact_counts": artifact_counts,
        }

    def list_paper_orders(self, filters: OperatorReadFilters | None = None) -> list[dict[str, Any]]:
        resolved_filters = filters or OperatorReadFilters()
        run_type = _coerce_run_type(resolved_filters.run_type)
        status = _coerce_run_status(resolved_filters.status)

        with session_scope(self.settings) as session:
            stmt = (
                select(PaperOrder, StrategyRun, Strategy, Symbol.ticker)
                .join(StrategyRun, StrategyRun.id == PaperOrder.strategy_run_id)
                .join(Strategy, Strategy.id == StrategyRun.strategy_id)
                .join(Symbol, Symbol.id == PaperOrder.symbol_id)
                .where(Strategy.strategy_id == resolved_filters.strategy_id)
            )
            if run_type is not None:
                stmt = stmt.where(StrategyRun.run_type == run_type)
            if status is not None:
                stmt = stmt.where(StrategyRun.status == status)
            rows = session.execute(
                stmt.order_by(
                    func.coalesce(
                        PaperOrder.last_broker_update_at,
                        PaperOrder.filled_at,
                        PaperOrder.submitted_at,
                        PaperOrder.created_at,
                    ).desc(),
                    Symbol.ticker.asc(),
                )
            ).all()

        items = [
            {
                "order_id": str(paper_order.id),
                "run_id": str(strategy_run.id),
                "strategy_id": strategy.strategy_id,
                "run_type": strategy_run.run_type.value,
                "run_status": strategy_run.status.value,
                "symbol": ticker,
                "session_date": paper_order.intended_session_date.isoformat(),
                "side": paper_order.side,
                "quantity": _decimal_value(paper_order.quantity),
                "order_type": paper_order.order_type,
                "time_in_force": paper_order.time_in_force,
                "status": paper_order.status,
                "broker_status": paper_order.broker_status,
                "client_order_id": paper_order.client_order_id,
                "broker_order_id": paper_order.broker_order_id,
                "submitted_at": _dt_value(paper_order.submitted_at),
                "filled_at": _dt_value(paper_order.filled_at),
                "source_risk_event_id": str(paper_order.source_risk_event_id),
                "submission_attempt_count": paper_order.submission_attempt_count,
                "sync_failure_count": paper_order.sync_failure_count,
                "last_submission_error": paper_order.last_submission_error,
                "last_sync_error": paper_order.last_sync_error,
            }
            for paper_order, strategy_run, strategy, ticker in rows
        ]
        return _apply_window_and_limit(items, resolved_filters, key_fn=_session_date_from_payload)

    def list_paper_fills(self, filters: OperatorReadFilters | None = None) -> list[dict[str, Any]]:
        resolved_filters = filters or OperatorReadFilters()
        run_type = _coerce_run_type(resolved_filters.run_type)
        status = _coerce_run_status(resolved_filters.status)

        with session_scope(self.settings) as session:
            stmt = (
                select(PaperFill, PaperOrder, StrategyRun, Strategy, Symbol.ticker)
                .join(PaperOrder, PaperOrder.id == PaperFill.paper_order_id)
                .join(StrategyRun, StrategyRun.id == PaperOrder.strategy_run_id)
                .join(Strategy, Strategy.id == StrategyRun.strategy_id)
                .join(Symbol, Symbol.id == PaperFill.symbol_id)
                .where(Strategy.strategy_id == resolved_filters.strategy_id)
            )
            if run_type is not None:
                stmt = stmt.where(StrategyRun.run_type == run_type)
            if status is not None:
                stmt = stmt.where(StrategyRun.status == status)
            rows = session.execute(
                stmt.order_by(PaperFill.filled_at.desc(), Symbol.ticker.asc())
            ).all()

        items = [
            {
                "fill_id": str(paper_fill.id),
                "paper_order_id": str(paper_order.id),
                "run_id": str(strategy_run.id),
                "strategy_id": strategy.strategy_id,
                "run_type": strategy_run.run_type.value,
                "run_status": strategy_run.status.value,
                "symbol": ticker,
                "session_date": paper_order.intended_session_date.isoformat(),
                "side": paper_fill.side,
                "quantity": _decimal_value(paper_fill.quantity),
                "price": _decimal_value(paper_fill.price),
                "filled_at": paper_fill.filled_at.isoformat(),
                "broker_fill_id": paper_fill.broker_fill_id,
                "broker_order_id": paper_fill.broker_order_id,
                "order_status": paper_order.status,
            }
            for paper_fill, paper_order, strategy_run, strategy, ticker in rows
        ]
        return _apply_window_and_limit(items, resolved_filters, key_fn=_session_date_from_payload)

    def list_positions(self, filters: OperatorReadFilters | None = None) -> list[dict[str, Any]]:
        resolved_filters = filters or OperatorReadFilters()

        with session_scope(self.settings) as session:
            rows = session.execute(
                select(Position, Strategy, Symbol.ticker)
                .join(Strategy, Strategy.id == Position.strategy_id)
                .join(Symbol, Symbol.id == Position.symbol_id)
                .where(Strategy.strategy_id == resolved_filters.strategy_id)
                .order_by(
                    func.coalesce(Position.closed_at, Position.opened_at, Position.created_at).desc(),
                    Symbol.ticker.asc(),
                )
            ).all()

        items = [
            {
                "position_id": str(position.id),
                "strategy_id": strategy.strategy_id,
                "symbol": ticker,
                "status": position.status,
                "quantity": _decimal_value(position.quantity),
                "average_entry_price": _decimal_value(position.average_entry_price),
                "cost_basis": _decimal_value(position.cost_basis),
                "opened_session_date": _date_value(position.opened_session_date),
                "closed_session_date": _date_value(position.closed_session_date),
                "opened_at": _dt_value(position.opened_at),
                "closed_at": _dt_value(position.closed_at),
            }
            for position, strategy, ticker in rows
        ]
        return _apply_position_window_and_limit(items, resolved_filters)

    def list_account_snapshots(self, filters: OperatorReadFilters | None = None) -> list[dict[str, Any]]:
        resolved_filters = filters or OperatorReadFilters()
        run_type = _coerce_run_type(resolved_filters.run_type)
        status = _coerce_run_status(resolved_filters.status)

        with session_scope(self.settings) as session:
            stmt = (
                select(AccountSnapshot, Strategy, StrategyRun)
                .join(Strategy, Strategy.id == AccountSnapshot.strategy_id)
                .outerjoin(StrategyRun, StrategyRun.id == AccountSnapshot.source_run_id)
                .where(Strategy.strategy_id == resolved_filters.strategy_id)
            )
            if run_type is not None:
                stmt = stmt.where(StrategyRun.run_type == run_type)
            if status is not None:
                stmt = stmt.where(StrategyRun.status == status)
            rows = session.execute(
                stmt.order_by(AccountSnapshot.snapshot_at.desc(), AccountSnapshot.created_at.desc())
            ).all()

        items = [
            {
                "snapshot_id": str(snapshot.id),
                "strategy_id": strategy.strategy_id,
                "source_run_id": str(source_run.id) if source_run is not None else None,
                "source_run_type": source_run.run_type.value if source_run is not None else None,
                "source_run_status": source_run.status.value if source_run is not None else None,
                "snapshot_source": snapshot.snapshot_source,
                "snapshot_at": snapshot.snapshot_at.isoformat(),
                "cash": _decimal_value(snapshot.cash),
                "gross_exposure": _decimal_value(snapshot.gross_exposure),
                "total_equity": _decimal_value(snapshot.total_equity),
                "buying_power": _decimal_value(snapshot.buying_power),
                "open_positions": snapshot.open_positions,
            }
            for snapshot, strategy, source_run in rows
        ]
        return _apply_window_and_limit(items, resolved_filters, key_fn=_snapshot_payload_date)

    def list_risk_events(self, filters: OperatorReadFilters | None = None) -> list[dict[str, Any]]:
        resolved_filters = filters or OperatorReadFilters()
        run_type = _coerce_run_type(resolved_filters.run_type)
        status = _coerce_run_status(resolved_filters.status)

        with session_scope(self.settings) as session:
            stmt = (
                select(RiskEvent, StrategyRun, Strategy, Symbol.ticker)
                .join(StrategyRun, StrategyRun.id == RiskEvent.strategy_run_id)
                .join(Strategy, Strategy.id == StrategyRun.strategy_id)
                .join(Symbol, Symbol.id == RiskEvent.symbol_id)
                .where(Strategy.strategy_id == resolved_filters.strategy_id)
            )
            if run_type is not None:
                stmt = stmt.where(StrategyRun.run_type == run_type)
            if status is not None:
                stmt = stmt.where(StrategyRun.status == status)
            rows = session.execute(
                stmt.order_by(RiskEvent.session_date.desc(), RiskEvent.created_at.desc(), Symbol.ticker.asc())
            ).all()

        items = [
            {
                "risk_event_id": str(risk_event.id),
                "run_id": str(strategy_run.id),
                "strategy_id": strategy.strategy_id,
                "run_type": strategy_run.run_type.value,
                "run_status": strategy_run.status.value,
                "symbol": ticker,
                "session_date": risk_event.session_date.isoformat(),
                "signal_direction": risk_event.signal_direction,
                "signal_reason": risk_event.signal_reason,
                "outcome": risk_event.outcome,
                "decision_code": risk_event.decision_code,
                "decision_reason": risk_event.decision_reason,
                "reference_price": _decimal_value(risk_event.reference_price),
                "proposed_quantity": _decimal_value(risk_event.proposed_quantity),
                "proposed_notional": _decimal_value(risk_event.proposed_notional),
                "risk_metadata": risk_event.risk_metadata,
            }
            for risk_event, strategy_run, strategy, ticker in rows
        ]
        return _apply_window_and_limit(items, resolved_filters, key_fn=_session_date_from_payload)

    def list_execution_events(self, filters: OperatorReadFilters | None = None) -> list[dict[str, Any]]:
        resolved_filters = filters or OperatorReadFilters()
        run_type = _coerce_run_type(resolved_filters.run_type)
        status = _coerce_run_status(resolved_filters.status)

        with session_scope(self.settings) as session:
            stmt = (
                select(ExecutionEvent, StrategyRun, Strategy, PaperOrder, Symbol.ticker)
                .join(StrategyRun, StrategyRun.id == ExecutionEvent.strategy_run_id)
                .join(Strategy, Strategy.id == StrategyRun.strategy_id)
                .outerjoin(PaperOrder, PaperOrder.id == ExecutionEvent.paper_order_id)
                .outerjoin(Symbol, Symbol.id == PaperOrder.symbol_id)
                .where(Strategy.strategy_id == resolved_filters.strategy_id)
            )
            if run_type is not None:
                stmt = stmt.where(StrategyRun.run_type == run_type)
            if status is not None:
                stmt = stmt.where(StrategyRun.status == status)
            rows = session.execute(
                stmt.order_by(ExecutionEvent.event_at.desc(), ExecutionEvent.created_at.desc())
            ).all()

        items = [
            {
                "execution_event_id": str(execution_event.id),
                "run_id": str(strategy_run.id),
                "strategy_id": strategy.strategy_id,
                "run_type": strategy_run.run_type.value,
                "run_status": strategy_run.status.value,
                "paper_order_id": str(paper_order.id) if paper_order is not None else None,
                "symbol": ticker,
                "session_date": (
                    paper_order.intended_session_date.isoformat()
                    if paper_order is not None
                    else _run_session_date(strategy_run)
                ),
                "event_type": execution_event.event_type,
                "severity": execution_event.severity,
                "blocks_execution": execution_event.blocks_execution,
                "event_at": execution_event.event_at.isoformat(),
                "message": execution_event.message,
                "details": execution_event.details,
            }
            for execution_event, strategy_run, strategy, paper_order, ticker in rows
        ]
        return _apply_window_and_limit(items, resolved_filters, key_fn=_event_payload_date)


def _serialize_filters(filters: OperatorReadFilters) -> dict[str, Any]:
    return {
        "strategy_id": filters.strategy_id,
        "run_type": filters.run_type,
        "status": filters.status,
        "session_start": _date_value(filters.session_start),
        "session_end": _date_value(filters.session_end),
        "limit": filters.limit,
    }


def _serialize_run_summary(strategy_run: StrategyRun, strategy: Strategy) -> dict[str, Any]:
    as_of_session = _run_session_date(strategy_run)
    return {
        "run_id": str(strategy_run.id),
        "strategy_id": strategy.strategy_id,
        "display_name": strategy.display_name,
        "run_type": strategy_run.run_type.value,
        "status": strategy_run.status.value,
        "trigger_source": strategy_run.trigger_source,
        "as_of_session": as_of_session,
        "started_at": strategy_run.started_at.isoformat(),
        "completed_at": _dt_value(strategy_run.completed_at),
        "parameters_snapshot": strategy_run.parameters_snapshot,
        "result_summary": strategy_run.result_summary,
        "error_message": strategy_run.error_message,
    }


def _run_session_date(strategy_run: StrategyRun) -> str | None:
    as_of_session = strategy_run.parameters_snapshot.get("as_of_session") or strategy_run.result_summary.get(
        "as_of_session"
    )
    if as_of_session is not None:
        return str(as_of_session)

    date_range = strategy_run.parameters_snapshot.get("date_range", {})
    to_date = date_range.get("to_date")
    if to_date is not None:
        return str(to_date)

    return strategy_run.started_at.date().isoformat()


def _coerce_run_type(value: str | None) -> StrategyRunType | None:
    if value is None:
        return None
    return StrategyRunType(value)


def _coerce_run_status(value: str | None) -> StrategyRunStatus | None:
    if value is None:
        return None
    return StrategyRunStatus(value)


def _apply_window_and_limit(
    items: list[dict[str, Any]],
    filters: OperatorReadFilters,
    *,
    key_fn,
) -> list[dict[str, Any]]:
    if filters.session_start is not None or filters.session_end is not None:
        items = [item for item in items if _date_within_window(key_fn(item), filters)]
    limit = max(filters.limit, 1)
    return items[:limit]


def _apply_position_window_and_limit(
    items: list[dict[str, Any]],
    filters: OperatorReadFilters,
) -> list[dict[str, Any]]:
    if filters.session_start is not None or filters.session_end is not None:
        items = [item for item in items if _position_overlaps_window(item, filters)]
    limit = max(filters.limit, 1)
    return items[:limit]


def _date_within_window(value: date | None, filters: OperatorReadFilters) -> bool:
    if value is None:
        return False
    if filters.session_start is not None and value < filters.session_start:
        return False
    if filters.session_end is not None and value > filters.session_end:
        return False
    return True


def _position_overlaps_window(item: dict[str, Any], filters: OperatorReadFilters) -> bool:
    opened = _parse_date(item.get("opened_session_date"))
    closed = _parse_date(item.get("closed_session_date"))
    if opened is None and closed is None:
        return False

    window_start = filters.session_start or date.min
    window_end = filters.session_end or date.max
    item_start = opened or closed or date.min
    item_end = closed or date.max
    return item_start <= window_end and item_end >= window_start


def _run_payload_date(item: dict[str, Any]) -> date | None:
    return _parse_date(item.get("as_of_session")) or _parse_dt(item.get("started_at"))


def _session_date_from_payload(item: dict[str, Any]) -> date | None:
    return _parse_date(item.get("session_date"))


def _snapshot_payload_date(item: dict[str, Any]) -> date | None:
    return _parse_dt(item.get("snapshot_at"))


def _event_payload_date(item: dict[str, Any]) -> date | None:
    return _parse_date(item.get("session_date")) or _parse_dt(item.get("event_at"))


def _parse_date(value: object) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    return None


def _parse_dt(value: object) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        return datetime.fromisoformat(value).date()
    return None


def _date_value(value: date | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _dt_value(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _decimal_value(value: Decimal | None) -> float:
    if value is None:
        return 0.0
    return float(value)
