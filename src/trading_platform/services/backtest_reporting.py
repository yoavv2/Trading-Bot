"""Reporting and export helpers for persisted backtest runs."""

from __future__ import annotations

import csv
import json
import uuid
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import select

from trading_platform.core.settings import Settings, load_settings
from trading_platform.db.models import (
    BacktestEquitySnapshot,
    BacktestMetric,
    BacktestTrade,
    Strategy,
    StrategyRun,
    StrategyRunStatus,
    StrategyRunType,
    Symbol,
)
from trading_platform.db.session import session_scope

MONEY_SCALE = Decimal("0.000001")


@dataclass(frozen=True)
class BacktestExportManifest:
    run_id: str
    strategy_id: str
    summary_path: str
    trades_csv_path: str
    equity_csv_path: str
    summary_format: str
    rendered_summary: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "strategy_id": self.strategy_id,
            "summary_path": self.summary_path,
            "trades_csv_path": self.trades_csv_path,
            "equity_csv_path": self.equity_csv_path,
            "summary_format": self.summary_format,
        }


def materialize_backtest_report(
    *,
    run_id: str | None = None,
    strategy_id: str = "trend_following_daily",
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Load a persisted backtest run, refresh its metrics row, and serialize a report."""
    resolved_settings = settings or load_settings()

    with session_scope(resolved_settings) as session:
        strategy_run = _resolve_backtest_run(session, run_id=run_id, strategy_id=strategy_id)
        trade_rows = _load_trade_rows(session, strategy_run.id)
        equity_rows = _load_equity_rows(session, strategy_run.id)
        metrics = _compute_metrics(
            strategy_run=strategy_run,
            trades=trade_rows["models"],
            equity_snapshots=equity_rows["models"],
        )
        _upsert_backtest_metric(session, strategy_run.id, metrics)

        return {
            "run_id": str(strategy_run.id),
            "strategy_id": strategy_run.strategy.strategy_id,
            "status": strategy_run.status.value,
            "trigger_source": strategy_run.trigger_source,
            "started_at": strategy_run.started_at.isoformat(),
            "completed_at": strategy_run.completed_at.isoformat() if strategy_run.completed_at else None,
            "summary": strategy_run.result_summary,
            "assumptions": strategy_run.parameters_snapshot,
            "metrics": _serialize_metrics(metrics),
            "trades": trade_rows["serialized"],
            "equity_curve": equity_rows["serialized"],
        }


def render_backtest_summary(
    report: dict[str, Any],
    *,
    summary_format: Literal["markdown", "json"] = "markdown",
) -> str:
    """Render a report as either markdown or pretty JSON."""
    if summary_format == "json":
        return json.dumps(report, indent=2, sort_keys=True)

    metrics = report["metrics"]
    assumptions = report["assumptions"]
    summary = report["summary"]
    date_range = assumptions.get("date_range", {})
    backtest = assumptions.get("backtest", {})

    return "\n".join(
        [
            f"# Backtest Report: {report['strategy_id']}",
            "",
            f"- Run ID: `{report['run_id']}`",
            f"- Status: `{report['status']}`",
            f"- Trigger Source: `{report['trigger_source']}`",
            f"- Date Range: {date_range.get('from_date', '-')} -> {date_range.get('to_date', '-')}",
            "",
            "## Assumptions",
            f"- Initial capital: {backtest.get('initial_capital', 0)}",
            f"- Fill strategy: `{backtest.get('fill_strategy', 'unknown')}`",
            f"- Allocation model: `{backtest.get('allocation_model', 'unknown')}`",
            f"- Max concurrent positions: {backtest.get('max_concurrent_positions', 0)}",
            f"- Commission per order: {backtest.get('fees', {}).get('commission_per_order', 0)}",
            f"- Slippage model: `{backtest.get('slippage', {}).get('model', 'unknown')}` @ {backtest.get('slippage', {}).get('bps', 0)} bps",
            "",
            "## Metrics",
            "| Metric | Value |",
            "| --- | --- |",
            f"| Total return | {metrics['total_return_pct']:.6f}% |",
            f"| Max drawdown | {metrics['max_drawdown_pct']:.6f}% |",
            f"| Trade count | {metrics['trade_count']} |",
            f"| Win rate | {metrics['win_rate_pct']:.6f}% |",
            f"| Average win | {metrics['average_win']:.6f} |",
            f"| Average loss | {metrics['average_loss']:.6f} |",
            f"| Profit factor | {metrics['profit_factor']:.6f} |",
            f"| Exposure | {metrics['exposure_pct']:.6f}% |",
            f"| Average holding period | {metrics['average_holding_period_sessions']:.6f} sessions |",
            "",
            "## Persisted Artifacts",
            f"- Signals persisted: {summary.get('signals_persisted', 0)}",
            f"- Trades persisted: {summary.get('trades_persisted', 0)}",
            f"- Equity snapshots persisted: {summary.get('equity_snapshots_persisted', 0)}",
            f"- Ending equity: {summary.get('ending_equity', 0)}",
        ]
    )


def export_backtest_report(
    *,
    run_id: str | None = None,
    strategy_id: str = "trend_following_daily",
    output_dir: str | Path | None = None,
    summary_format: Literal["markdown", "json"] = "markdown",
    settings: Settings | None = None,
) -> BacktestExportManifest:
    """Write a rendered summary plus CSV exports for trades and equity history."""
    resolved_settings = settings or load_settings()
    report = materialize_backtest_report(
        run_id=run_id,
        strategy_id=strategy_id,
        settings=resolved_settings,
    )
    rendered_summary = render_backtest_summary(report, summary_format=summary_format)

    base_dir = (
        Path(output_dir)
        if output_dir is not None
        else resolved_settings.paths.data_dir / "backtest-reports" / report["run_id"]
    )
    base_dir.mkdir(parents=True, exist_ok=True)

    summary_name = "summary.md" if summary_format == "markdown" else "summary.json"
    summary_path = base_dir / summary_name
    trades_path = base_dir / "trades.csv"
    equity_path = base_dir / "equity_curve.csv"

    summary_path.write_text(rendered_summary + ("\n" if not rendered_summary.endswith("\n") else ""))
    _write_csv(
        trades_path,
        report["trades"],
        fieldnames=[
            "symbol",
            "status",
            "quantity",
            "entry_signal_session",
            "entry_fill_session",
            "entry_price",
            "entry_commission",
            "entry_slippage",
            "exit_signal_session",
            "exit_fill_session",
            "exit_price",
            "exit_commission",
            "exit_slippage",
            "realized_pnl",
            "net_pnl",
            "holding_period_sessions",
        ],
    )
    _write_csv(
        equity_path,
        report["equity_curve"],
        fieldnames=[
            "session_date",
            "cash",
            "gross_exposure",
            "total_equity",
            "realized_pnl",
            "unrealized_pnl",
            "open_positions",
        ],
    )

    return BacktestExportManifest(
        run_id=report["run_id"],
        strategy_id=report["strategy_id"],
        summary_path=str(summary_path),
        trades_csv_path=str(trades_path),
        equity_csv_path=str(equity_path),
        summary_format=summary_format,
        rendered_summary=rendered_summary,
    )


def _resolve_backtest_run(session, *, run_id: str | None, strategy_id: str) -> StrategyRun:
    if run_id is not None:
        strategy_run = session.get(StrategyRun, uuid.UUID(run_id))
        if strategy_run is None:
            raise LookupError(f"Backtest run '{run_id}' was not found.")
        if strategy_run.run_type != StrategyRunType.BACKTEST:
            raise ValueError(f"Run '{run_id}' is not a backtest run.")
        return strategy_run

    strategy_run = session.execute(
        select(StrategyRun)
        .join(Strategy)
        .where(Strategy.strategy_id == strategy_id)
        .where(StrategyRun.run_type == StrategyRunType.BACKTEST)
        .where(StrategyRun.status == StrategyRunStatus.SUCCEEDED)
        .order_by(StrategyRun.started_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if strategy_run is None:
        raise LookupError(f"No completed backtest runs found for strategy '{strategy_id}'.")
    return strategy_run


def _load_trade_rows(session, strategy_run_id: uuid.UUID) -> dict[str, Any]:
    rows = session.execute(
        select(BacktestTrade, Symbol.ticker)
        .join(Symbol, Symbol.id == BacktestTrade.symbol_id)
        .where(BacktestTrade.strategy_run_id == strategy_run_id)
        .order_by(BacktestTrade.entry_fill_session.asc(), Symbol.ticker.asc())
    ).all()

    serialized = []
    models = []
    for trade, ticker in rows:
        models.append(trade)
        serialized.append(
            {
                "symbol": ticker,
                "status": trade.status,
                "quantity": float(trade.quantity),
                "entry_signal_session": trade.entry_signal_session.isoformat(),
                "entry_fill_session": trade.entry_fill_session.isoformat(),
                "entry_price": float(trade.entry_price),
                "entry_commission": float(trade.entry_commission),
                "entry_slippage": float(trade.entry_slippage),
                "exit_signal_session": trade.exit_signal_session.isoformat() if trade.exit_signal_session else "",
                "exit_fill_session": trade.exit_fill_session.isoformat() if trade.exit_fill_session else "",
                "exit_price": float(trade.exit_price) if trade.exit_price is not None else "",
                "exit_commission": float(trade.exit_commission),
                "exit_slippage": float(trade.exit_slippage),
                "realized_pnl": float(trade.realized_pnl) if trade.realized_pnl is not None else "",
                "net_pnl": float(trade.net_pnl) if trade.net_pnl is not None else "",
                "holding_period_sessions": trade.holding_period_sessions or "",
            }
        )

    return {"models": models, "serialized": serialized}


def _load_equity_rows(session, strategy_run_id: uuid.UUID) -> dict[str, Any]:
    rows = session.execute(
        select(BacktestEquitySnapshot)
        .where(BacktestEquitySnapshot.strategy_run_id == strategy_run_id)
        .order_by(BacktestEquitySnapshot.session_date.asc())
    ).scalars().all()

    serialized = [
        {
            "session_date": row.session_date.isoformat(),
            "cash": float(row.cash),
            "gross_exposure": float(row.gross_exposure),
            "total_equity": float(row.total_equity),
            "realized_pnl": float(row.realized_pnl),
            "unrealized_pnl": float(row.unrealized_pnl),
            "open_positions": row.open_positions,
        }
        for row in rows
    ]
    return {"models": rows, "serialized": serialized}


def _compute_metrics(
    *,
    strategy_run: StrategyRun,
    trades: list[BacktestTrade],
    equity_snapshots: list[BacktestEquitySnapshot],
) -> dict[str, Decimal | int]:
    initial_capital = _money(
        strategy_run.parameters_snapshot.get("backtest", {}).get(
            "initial_capital",
            strategy_run.result_summary.get("starting_capital", 0),
        )
    )
    closed_trades = [trade for trade in trades if trade.net_pnl is not None]
    wins = [trade.net_pnl for trade in closed_trades if trade.net_pnl is not None and trade.net_pnl > 0]
    losses = [trade.net_pnl for trade in closed_trades if trade.net_pnl is not None and trade.net_pnl < 0]

    ending_equity = equity_snapshots[-1].total_equity if equity_snapshots else initial_capital
    total_return_pct = Decimal("0")
    if initial_capital > 0:
        total_return_pct = ((ending_equity / initial_capital) - Decimal("1")) * Decimal("100")

    peak = Decimal("0")
    max_drawdown_pct = Decimal("0")
    for snapshot in equity_snapshots:
        equity = snapshot.total_equity
        if equity > peak:
            peak = equity
        if peak > 0:
            drawdown = ((equity / peak) - Decimal("1")) * Decimal("100")
            if drawdown < max_drawdown_pct:
                max_drawdown_pct = drawdown

    win_rate_pct = Decimal("0")
    if closed_trades:
        win_rate_pct = (Decimal(len(wins)) / Decimal(len(closed_trades))) * Decimal("100")

    average_win = sum(wins, Decimal("0")) / Decimal(len(wins)) if wins else Decimal("0")
    average_loss = sum(losses, Decimal("0")) / Decimal(len(losses)) if losses else Decimal("0")

    gross_profit = sum(wins, Decimal("0"))
    gross_loss = sum((abs(loss) for loss in losses), Decimal("0"))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else Decimal("0")

    exposure_pct = Decimal("0")
    if equity_snapshots:
        exposures = [
            (snapshot.gross_exposure / snapshot.total_equity) * Decimal("100")
            if snapshot.total_equity > 0
            else Decimal("0")
            for snapshot in equity_snapshots
        ]
        exposure_pct = sum(exposures, Decimal("0")) / Decimal(len(exposures))

    holding_periods = [
        Decimal(trade.holding_period_sessions)
        for trade in closed_trades
        if trade.holding_period_sessions is not None
    ]
    average_holding_period = (
        sum(holding_periods, Decimal("0")) / Decimal(len(holding_periods))
        if holding_periods
        else Decimal("0")
    )

    return {
        "total_return_pct": _money(total_return_pct),
        "max_drawdown_pct": _money(max_drawdown_pct),
        "trade_count": len(trades),
        "win_rate_pct": _money(win_rate_pct),
        "average_win": _money(average_win),
        "average_loss": _money(average_loss),
        "profit_factor": _money(profit_factor),
        "exposure_pct": _money(exposure_pct),
        "average_holding_period_sessions": _money(average_holding_period),
    }


def _upsert_backtest_metric(
    session,
    strategy_run_id: uuid.UUID,
    metrics: dict[str, Decimal | int],
) -> BacktestMetric:
    metric_row = session.execute(
        select(BacktestMetric).where(BacktestMetric.strategy_run_id == strategy_run_id)
    ).scalar_one_or_none()

    payload = {
        "total_return_pct": metrics["total_return_pct"],
        "max_drawdown_pct": metrics["max_drawdown_pct"],
        "trade_count": metrics["trade_count"],
        "win_rate_pct": metrics["win_rate_pct"],
        "average_win": metrics["average_win"],
        "average_loss": metrics["average_loss"],
        "profit_factor": metrics["profit_factor"],
        "exposure_pct": metrics["exposure_pct"],
        "average_holding_period_sessions": metrics["average_holding_period_sessions"],
    }

    if metric_row is None:
        metric_row = BacktestMetric(strategy_run_id=strategy_run_id, **payload)
        session.add(metric_row)
    else:
        for field_name, value in payload.items():
            setattr(metric_row, field_name, value)

    session.flush()
    return metric_row


def _serialize_metrics(metrics: dict[str, Decimal | int]) -> dict[str, Any]:
    return {
        "total_return_pct": float(metrics["total_return_pct"]),
        "max_drawdown_pct": float(metrics["max_drawdown_pct"]),
        "trade_count": metrics["trade_count"],
        "win_rate_pct": float(metrics["win_rate_pct"]),
        "average_win": float(metrics["average_win"]),
        "average_loss": float(metrics["average_loss"]),
        "profit_factor": float(metrics["profit_factor"]),
        "exposure_pct": float(metrics["exposure_pct"]),
        "average_holding_period_sessions": float(metrics["average_holding_period_sessions"]),
    }


def _write_csv(path: Path, rows: list[dict[str, Any]], *, fieldnames: list[str]) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _money(value: Decimal | float | int) -> Decimal:
    return Decimal(str(value)).quantize(MONEY_SCALE)
