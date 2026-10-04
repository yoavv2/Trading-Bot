"""RecordExternalActivityJobHandler: the ``record-external-activity`` Job type's
execution unit (EXT-01, D-10, R-18).

One handler run does both halves of the recovery path:

1. ``record_external_orders`` re-fetches each listed order (and its fills) from the
   broker, enforces the closed preconditions and stores immutable, hash-verified
   snapshots. A refused request raises ``ExternalActivityRejectedError``, translated
   into ``JobDomainConflictError`` (Job FAILED / ``domain_conflict``, not uncertain:
   nothing was written and nothing was sent), with the closed reason as the first token
   of the failure message.
2. After the rows are committed, ``reconcile_account`` runs a fresh account-level
   reconciliation in the SAME handler. The Job outcome IS that reconciliation's stored
   result; only that stored result can lift a block, and only because every item is then
   explained. A blocking result is a domain result, not a lifecycle failure: the Job is
   SUCCEEDED and ``outcome`` says ``blocking``.

Both halves share ONE broker client. This handler performs broker READS and writes only
its own records, so it logs no ``external_``-prefixed event code (the runner reads that
prefix as "an external side effect may have happened" and would mark a later handler
error uncertain). The Job is cancellable only while queued; there is no cooperative
cancellation checkpoint and progress is step text only.
"""

from __future__ import annotations

from typing import Any

from trading_platform.core.settings import Settings, load_settings
from trading_platform.jobs.contracts import JobContext
from trading_platform.jobs.handlers.domain_conflicts import translate_domain_conflicts
from trading_platform.services.alpaca import AlpacaClient
from trading_platform.services.broker_jobs import RECORD_EXTERNAL_ACTIVITY_JOB_TYPE
from trading_platform.services.config.validation import ExecutionMode
from trading_platform.services.external_activity import (
    ExternalActivityRejectedError,
    record_external_orders,
)
from trading_platform.services.reconciliation import reconcile_account

STEP_RECORDING = "recording external activity"
STEP_CHECKING = "running fresh account reconciliation"

RECONCILIATION_TRIGGER_SOURCE = "record_external_activity"


class RecordExternalActivityJobHandler:
    """Records verified external broker orders, then runs the fresh account check."""

    job_type = RECORD_EXTERNAL_ACTIVITY_JOB_TYPE
    required_execution_mode = ExecutionMode.PAPER

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings

    def run(self, context: JobContext) -> dict[str, Any]:
        settings = self._settings or load_settings()
        order_ids = list(context.payload["order_ids"])
        reason = str(context.payload["reason"])

        context.report_progress(step=STEP_RECORDING)
        context.log(
            level="info",
            event_code="record_external_activity_recording_started",
            message="Recording external activity started.",
            context={"order_ids": order_ids},
        )

        client = AlpacaClient(settings.broker.alpaca)
        try:
            with translate_domain_conflicts():
                try:
                    recorded = record_external_orders(
                        order_ids,
                        reason,
                        job_id=context.job_id,
                        settings=settings,
                        broker_client=client,
                    )
                except ExternalActivityRejectedError as exc:
                    context.log(
                        level="warning",
                        event_code="record_external_activity_rejected",
                        message="External activity recording was refused; nothing was stored.",
                        context={"reason": exc.reason.value, "order_ids": list(exc.order_ids)},
                    )
                    raise

            context.report_progress(step=STEP_CHECKING)
            report = reconcile_account(
                trigger_source=RECONCILIATION_TRIGGER_SOURCE,
                job_id=context.job_id,
                settings=settings,
                broker_client=client,
            )
        finally:
            client.close()

        context.log(
            level="info",
            event_code="record_external_activity_recorded",
            message="External activity recorded and the fresh account check completed.",
            context={
                "recorded_activity_ids": list(recorded.recorded_activity_ids),
                "already_recorded_order_ids": list(recorded.already_recorded_order_ids),
                "run_id": report.run_id,
                "blocks_execution": report.blocks_execution,
            },
        )

        return {
            "scope": "account",
            "outcome": "blocking" if report.blocks_execution else "clean",
            "recorded_activity_ids": list(recorded.recorded_activity_ids),
            "already_recorded_order_ids": list(recorded.already_recorded_order_ids),
            "run_id": report.run_id,
            "produced_run_ids": [report.run_id],
            "blocks_execution": report.blocks_execution,
            "finding_count": report.finding_count,
            "blocking_count": report.blocking_count,
            "unresolved_reasons": list(report.unresolved_reasons),
        }
