"""ReconciliationJobHandler: the ``reconciliation`` Job type's execution
unit (OPS-04, D-01, D-06, D-21/D-22).

Calls the existing ``services.reconciliation.reconcile_paper_execution``
exactly once, matching the retired CLI's call shape (report-only). This
handler never invokes the separate corrective entrypoint that mutates
per-order sync-failure state -- corrections still run only inside the
``paper-session`` Job (RECON-04: correction is a separate,
explicitly-invoked step).

The ``reconciliation`` Job type is cancellable only while queued (D-01):
it writes broker-derived state inside one opaque service call, so this
handler contains no cooperative-cancellation checkpoint of any kind --
cancellation of a RUNNING Job of this type is rejected upstream by
``JobOrchestrationService`` (D-02), never inside the handler. Progress is
reported as step text only, never a fabricated completion fraction -- the
framework owns the Job's own terminal-state progress. This handler never
writes the linked strategy run's lifecycle state itself; the service call
already owns that, and the framework owns the Job's own lifecycle.

Scope (ACCT-01, D-09): payload ``scope == "account"`` runs the owner-less,
report-only ``reconcile_account`` instead. It needs no strategy, assigns nothing to
any strategy, submits nothing, and stores its result in
``account_reconciliation_runs`` (no strategy reference). Omitted ``scope`` is
``strategy`` -- the behaviour above, unchanged.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from trading_platform.core.settings import Settings
from trading_platform.jobs.contracts import JobContext
from trading_platform.services.config.validation import ExecutionMode
from trading_platform.services.reconciliation import reconcile_account, reconcile_paper_execution

STEP_RESOLVING = "resolving strategy"
STEP_RECONCILING = "reconciling"
STEP_RECORDING = "recording result"


class ReconciliationJobHandler:
    """Runs one report-only broker/local reconciliation for a registered
    strategy over one explicit trading session."""

    job_type = "reconciliation"
    required_execution_mode = ExecutionMode.PAPER

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings

    def run(self, context: JobContext) -> dict[str, Any]:
        if context.payload.get("scope") == "account":
            return self._run_account(context)

        context.report_progress(step=STEP_RESOLVING)
        strategy_id = context.payload["strategy_id"]
        as_of_session = date.fromisoformat(context.payload["as_of_session"])

        context.report_progress(step=STEP_RECONCILING)
        context.log(
            level="info",
            event_code="reconciliation_started",
            message="Reconciliation started.",
            context={
                "strategy_id": strategy_id,
                "as_of_session": context.payload["as_of_session"],
            },
        )

        report = reconcile_paper_execution(
            strategy_id,
            as_of_session=as_of_session,
            trigger_source="job",
            job_id=context.job_id,
            settings=self._settings,
        )

        context.report_progress(step=STEP_RECORDING)
        context.log(
            level="info",
            event_code="reconciliation_completed",
            message="Reconciliation finished.",
            context={
                "run_id": report.run_id,
                "finding_count": report.finding_count,
                "blocking_count": report.blocking_count,
            },
        )

        return {
            "run_id": report.run_id,
            "produced_run_ids": [report.run_id],
            "strategy_id": report.strategy_id,
            "as_of_session": context.payload["as_of_session"],
            "finding_count": report.finding_count,
            "blocking_count": report.blocking_count,
            "blocks_execution": report.blocks_execution,
        }

    def _run_account(self, context: JobContext) -> dict[str, Any]:
        raw_session = context.payload.get("as_of_session")
        as_of_session = date.fromisoformat(raw_session) if raw_session is not None else None

        context.report_progress(step=STEP_RECONCILING)
        context.log(
            level="info",
            event_code="account_reconciliation_started",
            message="Account reconciliation started.",
            context={"scope": "account", "as_of_session": raw_session},
        )

        report = reconcile_account(
            as_of_session=as_of_session,
            trigger_source="job",
            job_id=context.job_id,
            settings=self._settings,
        )

        context.report_progress(step=STEP_RECORDING)
        context.log(
            level="info",
            event_code="account_reconciliation_completed",
            message="Account reconciliation finished.",
            context={
                "run_id": report.run_id,
                "finding_count": report.finding_count,
                "blocking_count": report.blocking_count,
            },
        )

        return {
            "scope": "account",
            "run_id": report.run_id,
            "produced_run_ids": [report.run_id],
            "strategy_id": None,
            "as_of_session": raw_session,
            "finding_count": report.finding_count,
            "blocking_count": report.blocking_count,
            "blocks_execution": report.blocks_execution,
            "unresolved_reasons": list(report.unresolved_reasons),
        }
