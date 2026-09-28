"""PaperSessionJobHandler: the ``paper-session`` Job type's execution unit
(OPS-03, OPS-08, D-01..D-05, D-19, D-28).

Calls the existing ``services.execution.run_paper_session`` exactly once --
the only path to broker order submission after Phase 20 (D-28). One opaque
service call performs reconciliation, correction and submission, so this
handler contains no cooperative-cancellation checkpoint of any kind: the
``paper-session`` Job type is cancellable only while queued, and cancelling
a RUNNING Job of this type is rejected upstream by
``JobOrchestrationService``, never inside this handler (D-01/D-02/D-03).

Before the service call, this handler logs an event whose code names the
external broker-session start. Any log ``event_code`` beginning with the
``external_`` prefix is the runner's sole signal that a later
``handler_error`` must be recorded ``outcome_uncertain=true`` (D-19) --
which in turn gates retry behind a reconcile-first block, enforced outside
this handler.

A domain-layer ``ConcurrentRunLockedError`` raised by the service call is
translated into the framework-level ``JobDomainConflictError``, naming the
conflicting strategy and session (D-04).

A blocked (``blocked_strategy_disabled``, ``blocked_global_kill_switch``,
``blocked_reconciliation``) or no-op (``noop_*``) domain outcome is
returned as an ordinary, successful ``result_summary`` -- this handler
never reinterprets or raises on the domain decision (D-05). This handler
never writes any linked ``StrategyRun``'s lifecycle state itself; the
service call already owns that, and the framework owns the Job's own
lifecycle.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from trading_platform.core.settings import Settings
from trading_platform.jobs.contracts import JobContext
from trading_platform.jobs.handlers.domain_conflicts import translate_domain_conflicts
from trading_platform.services.config.validation import ExecutionMode
from trading_platform.services.execution import run_paper_session

STEP_RESOLVING = "resolving strategy"
STEP_RUNNING = "running paper session"
STEP_RECORDING = "recording result"


class PaperSessionJobHandler:
    """Runs one daily paper-trading session (reconcile, correct, submit
    orders) for a registered strategy over one explicit trading session."""

    job_type = "paper-session"
    required_execution_mode = ExecutionMode.PAPER

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings

    def run(self, context: JobContext) -> dict[str, Any]:
        context.report_progress(step=STEP_RESOLVING)
        strategy_id = context.payload["strategy_id"]
        as_of_session = date.fromisoformat(context.payload["as_of_session"])
        risk_run_id = context.payload.get("risk_run_id")

        context.report_progress(step=STEP_RUNNING)
        context.log(
            level="info",
            event_code="external_broker_session_started",
            message="Paper session started; broker submission may occur.",
            context={
                "strategy_id": strategy_id,
                "as_of_session": context.payload["as_of_session"],
            },
        )

        with translate_domain_conflicts():
            report = run_paper_session(
                strategy_id,
                as_of_session=as_of_session,
                risk_run_id=risk_run_id,
                trigger_source="job",
                job_id=context.job_id,
                settings=self._settings,
            )

        context.report_progress(step=STEP_RECORDING)
        context.log(
            level="info",
            event_code="paper_session_completed",
            message="Paper session finished.",
            context={
                "action": report.action,
                "execution_run_id": report.execution_run_id,
            },
        )

        produced_run_ids = [
            run_id
            for run_id in (report.reconciliation_run_id, report.execution_run_id)
            if run_id is not None
        ]

        return {
            "action": report.action,
            "strategy_id": report.strategy_id,
            "as_of_session": context.payload["as_of_session"],
            "source_risk_run_id": report.source_risk_run_id,
            "execution_run_id": report.execution_run_id,
            "execution_status": report.execution_status,
            "reconciliation_run_id": report.reconciliation_run_id,
            "produced_run_ids": produced_run_ids,
        }
