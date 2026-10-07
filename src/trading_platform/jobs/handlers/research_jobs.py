"""Research study Job handlers (research mode only): freeze, backtest, evaluate.

Each handler is a thin adapter around ``services.research.studies.StudyService``,
bracketed by cooperative-cancellation checkpoints, reporting step text only. The
service owns every persistent write; the framework owns the Job lifecycle.
"""

from __future__ import annotations

import uuid
from typing import Any

from trading_platform.core.settings import Settings, load_settings
from trading_platform.jobs.contracts import JobContext
from trading_platform.services.config.validation import ExecutionMode
from trading_platform.services.research.studies import StudyService


class ResearchFreezeJobHandler:
    job_type = "research-freeze"
    required_execution_mode = ExecutionMode.BACKTEST

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings

    def run(self, context: JobContext) -> dict[str, Any]:
        settings = self._settings or load_settings()
        context.raise_if_cancelled()
        context.report_progress(step="checking integrity and freezing inputs")
        report = StudyService(settings).perform_freeze(
            uuid.UUID(context.payload["study_revision_id"]), job_id=context.job_id
        )
        context.log(level="info", event_code="research_freeze_completed", message="Inputs frozen.", context=report)
        context.raise_if_cancelled()
        return report


class ResearchBacktestJobHandler:
    job_type = "research-backtest"
    required_execution_mode = ExecutionMode.BACKTEST

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings

    def run(self, context: JobContext) -> dict[str, Any]:
        settings = self._settings or load_settings()
        context.raise_if_cancelled()
        context.report_progress(step="running single-asset backtest")
        report = StudyService(settings).run_backtest_job(dict(context.payload), job_id=context.job_id)
        context.log(
            level="info",
            event_code="research_backtest_completed",
            message="Research backtest finished.",
            context={k: report[k] for k in ("run_id", "asset", "window_role", "results_digest")},
        )
        context.raise_if_cancelled()
        return report


class ResearchEvaluateJobHandler:
    job_type = "research-evaluate"
    required_execution_mode = ExecutionMode.BACKTEST

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings

    def run(self, context: JobContext) -> dict[str, Any]:
        settings = self._settings or load_settings()
        context.raise_if_cancelled()
        context.report_progress(step="computing metrics, evidence and ranking")
        report = StudyService(settings).evaluate(
            uuid.UUID(context.payload["study_revision_id"]),
            scope=context.payload["scope"],
            job_id=context.job_id,
            is_rerun=bool(context.payload.get("is_rerun", False)),
        )
        context.log(level="info", event_code="research_evaluate_completed", message="Evaluation stored.", context={"scope": report["scope"], "verdict": report.get("verdict")})
        context.raise_if_cancelled()
        return report
