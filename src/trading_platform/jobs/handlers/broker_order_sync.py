"""BrokerOrderSyncJobHandler: the ``broker-order-sync`` Job type's execution
unit (OPS-06, D-01, D-19).

Calls the existing ``services.execution.sync_paper_state`` exactly once,
passing ``strategy_id`` explicitly (never the runner-settings default) so
this Job always syncs the strategy the operator submitted, never a silently
resolved fallback.

The ``broker-order-sync`` Job type is cancellable only while queued (D-01):
it writes broker-derived state inside one opaque service call, so this
handler contains no cooperative-cancellation checkpoint of any kind (D-02)
-- cancellation of a RUNNING Job of this type is rejected upstream by
``JobOrchestrationService``, never inside the handler. Progress is reported
as step text only, never a fabricated completion fraction -- the framework
owns the Job's own terminal-state progress.

Before the service call, this handler logs an event whose code names the
external broker-sync start. Any log ``event_code`` beginning with the
``external_`` prefix is the runner's sole signal that a later
``handler_error`` must be recorded ``outcome_uncertain=true`` (D-19) --
which in turn gates retry behind a reconcile-first block
(``retry_prerequisite_job_type = "reconciliation"``, declared on the
submission spec), enforced outside this handler.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from trading_platform.core.settings import Settings
from trading_platform.jobs.contracts import JobContext
from trading_platform.services.config.validation import ExecutionMode
from trading_platform.services.execution import sync_paper_state

STEP_RESOLVING = "resolving strategy"
STEP_SYNCING = "syncing broker orders"
STEP_RECORDING = "recording result"


class BrokerOrderSyncJobHandler:
    """Syncs paper order lifecycle, fills, positions and account state from
    the broker for a registered strategy over one explicit trading session."""

    job_type = "broker-order-sync"
    required_execution_mode = ExecutionMode.PAPER

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings

    def run(self, context: JobContext) -> dict[str, Any]:
        context.report_progress(step=STEP_RESOLVING)
        strategy_id = context.payload["strategy_id"]
        as_of_session = date.fromisoformat(context.payload["as_of_session"])

        context.report_progress(step=STEP_SYNCING)
        context.log(
            level="info",
            event_code="external_broker_sync_started",
            message="Broker order sync started; broker-derived state may be written.",
            context={
                "strategy_id": strategy_id,
                "as_of_session": context.payload["as_of_session"],
            },
        )

        report = sync_paper_state(
            strategy_id,
            as_of_session=as_of_session,
            settings=self._settings,
        )

        context.report_progress(step=STEP_RECORDING)
        context.log(
            level="info",
            event_code="broker_order_sync_completed",
            message="Broker order sync finished.",
            context={
                "orders_synced": report.orders_synced,
                "fills_ingested": report.fills_ingested,
            },
        )

        return {
            "strategy_id": report.strategy_id,
            "as_of_session": context.payload["as_of_session"],
            "orders_synced": report.orders_synced,
            "fills_ingested": report.fills_ingested,
            "positions_opened": report.positions_opened,
            "positions_closed": report.positions_closed,
            "open_positions": report.open_positions,
            "account_snapshot_id": report.account_snapshot_id,
            "produced_run_ids": [],
        }
