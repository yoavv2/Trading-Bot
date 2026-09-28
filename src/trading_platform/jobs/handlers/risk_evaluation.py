"""RiskEvaluationJobHandler: the ``risk-evaluation`` Job type's execution
unit (OPS-02, D-21/D-22, P19 D-11/D-12/D-13/D-16).

Calls the existing ``services.risk.run_risk_evaluation`` exactly once,
bracketed by two cooperative-cancellation checkpoints. Progress is reported
as step text only, never a fabricated completion fraction -- the framework
owns the Job's own terminal-state progress. This handler never writes the
linked strategy run's lifecycle state itself; the service call already owns
that, and the framework owns the Job's own lifecycle.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from trading_platform.core.settings import Settings
from trading_platform.jobs.contracts import JobContext
from trading_platform.services.config.validation import ExecutionMode
from trading_platform.services.risk import run_risk_evaluation

STEP_RESOLVING = "resolving strategy"
STEP_EVALUATING = "evaluating risk"
STEP_RECORDING = "recording result"


class RiskEvaluationJobHandler:
    """Runs one risk evaluation for a registered strategy over one explicit
    trading session."""

    job_type = "risk-evaluation"
    required_execution_mode = ExecutionMode.BACKTEST

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings

    def run(self, context: JobContext) -> dict[str, Any]:
        context.report_progress(step=STEP_RESOLVING)
        strategy_id = context.payload["strategy_id"]
        as_of_session = date.fromisoformat(context.payload["as_of_session"])

        # Pre-call cancellation checkpoint -- a cancel requested before start
        # means the service is never called.
        context.raise_if_cancelled()

        context.report_progress(step=STEP_EVALUATING)
        context.log(
            level="info",
            event_code="risk_evaluation_started",
            message="Risk evaluation started.",
            context={
                "strategy_id": strategy_id,
                "as_of_session": context.payload["as_of_session"],
            },
        )

        report = run_risk_evaluation(
            strategy_id,
            as_of_session=as_of_session,
            trigger_source="job",
            job_id=context.job_id,
            settings=self._settings,
        )

        context.report_progress(step=STEP_RECORDING)
        context.log(
            level="info",
            event_code="risk_evaluation_completed",
            message="Risk evaluation finished.",
            context={"run_id": report.run_id, "run_status": report.status},
        )

        # Post-call cancellation checkpoint -- a cancel requested during the
        # call is acknowledged here. The linked run keeps its real
        # (already-persisted) outcome; only the Job lands CANCELLED.
        context.raise_if_cancelled()

        return {
            "run_id": report.run_id,
            "produced_run_ids": [report.run_id],
            "strategy_id": report.strategy_id,
            "as_of_session": context.payload["as_of_session"],
            "run_status": report.status,
        }
