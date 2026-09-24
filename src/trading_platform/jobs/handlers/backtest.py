"""BacktestJobHandler: the ``backtest`` Job type's execution unit (D-11, D-12,
D-13, D-16, D-06, D-22).

Calls the existing ``services.backtesting.run_backtest`` exactly once,
bracketed by two cooperative-cancellation checkpoints (D-12). Progress is
reported as step text only, never a fabricated percentage (D-16) -- the
framework sets 100% on SUCCEEDED. This handler never writes
``StrategyRun`` status itself (D-13); the service call already owns that,
and the framework owns the Job's own lifecycle.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from trading_platform.core.settings import Settings
from trading_platform.jobs.contracts import JobContext
from trading_platform.services.backtesting import run_backtest
from trading_platform.services.config.validation import ExecutionMode

STEP_RESOLVING = "resolving strategy"
STEP_RUNNING = "running backtest"
STEP_RECORDING = "recording result"

# D-06: display totals carried from the service's result_summary into the
# Job's own result_summary, when present.
_DISPLAY_SUMMARY_KEYS = (
    "sessions_evaluated",
    "trades_persisted",
    "starting_capital",
    "ending_equity",
)


class BacktestJobHandler:
    """Runs one deterministic daily-bar backtest for a registered strategy."""

    job_type = "backtest"
    required_execution_mode = ExecutionMode.BACKTEST

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings

    def run(self, context: JobContext) -> dict[str, Any]:
        context.report_progress(step=STEP_RESOLVING)
        strategy_id = context.payload["strategy_id"]
        from_date = date.fromisoformat(context.payload["from_date"])
        to_date = date.fromisoformat(context.payload["to_date"])

        # D-12: pre-call cancellation checkpoint -- a cancel requested
        # before start means run_backtest is never called.
        context.raise_if_cancelled()

        context.report_progress(step=STEP_RUNNING)
        context.log(
            level="info",
            event_code="backtest_run_started",
            message="Backtest started.",
            context={
                "strategy_id": strategy_id,
                "from_date": context.payload["from_date"],
                "to_date": context.payload["to_date"],
            },
        )

        report = run_backtest(
            strategy_id,
            from_date=from_date,
            to_date=to_date,
            trigger_source="job",
            job_id=context.job_id,
            settings=self._settings,
        )

        context.report_progress(step=STEP_RECORDING)
        context.log(
            level="info",
            event_code="backtest_run_completed",
            message="Backtest finished.",
            context={"run_id": report.run_id, "run_status": report.status},
        )

        # D-12: post-call cancellation checkpoint -- a cancel requested
        # during the call is acknowledged here. The linked StrategyRun keeps
        # its real (already-persisted) status (D-13); only the Job lands
        # CANCELLED.
        context.raise_if_cancelled()

        result: dict[str, Any] = {
            "run_id": report.run_id,
            "strategy_id": report.strategy_id,
            "run_status": report.status,
            "from_date": context.payload["from_date"],
            "to_date": context.payload["to_date"],
        }
        for key in _DISPLAY_SUMMARY_KEYS:
            if key in report.result_summary:
                result[key] = report.result_summary[key]
        return result
