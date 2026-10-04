"""SyncSymbolMetadataJobHandler: the ``sync-symbol-metadata`` Job type's
execution unit (OPS-05, ORCH-02, D-01, D-08).

Calls the existing ``services.symbol_metadata_sync.sync_symbol_metadata``
exactly once, bracketed by two cooperative-cancellation checkpoints (D-01).
Progress is reported as step text only, never a fabricated completion
fraction -- the framework owns the Job's own terminal-state progress.

COR-03/D-28: the closed outcome ``complete | partial | failed`` comes from the
domain result. A partial result (some tickers failed per-symbol, at least one
synced) returns normally and the Job SUCCEEDED; an operation-level failure or a
run in which no ticker synced raises via ``raise_if_failed()`` and lands the Job
FAILED (``handler_error``). ``raise_if_failed()`` is called only after the
completion log has recorded the full result, so a failure is never silent.
"""

from __future__ import annotations

from typing import Any

from trading_platform.core.settings import Settings, load_settings
from trading_platform.jobs.contracts import JobContext
from trading_platform.services.config.validation import ExecutionMode
from trading_platform.services.symbol_metadata_sync import sync_symbol_metadata

STEP_RESOLVING = "resolving symbols"
STEP_SYNCING = "syncing symbol metadata"
STEP_RECORDING = "recording result"


class SyncSymbolMetadataJobHandler:
    """Runs one symbol-metadata sync for an explicit symbol list."""

    job_type = "sync-symbol-metadata"
    required_execution_mode = ExecutionMode.BACKTEST

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings

    def run(self, context: JobContext) -> dict[str, Any]:
        context.report_progress(step=STEP_RESOLVING)
        resolved = self._settings or load_settings()
        symbols = list(context.payload["symbols"])

        # D-01: pre-call cancellation checkpoint -- a cancel requested
        # before start means sync_symbol_metadata is never called.
        context.raise_if_cancelled()

        context.report_progress(step=STEP_SYNCING)
        context.log(
            level="info",
            event_code="symbol_metadata_sync_started",
            message="Symbol metadata sync started.",
            context={"symbol_count": len(symbols)},
        )

        result = sync_symbol_metadata(symbols, settings=resolved)

        context.report_progress(step=STEP_RECORDING)
        counts = result.to_dict()
        context.log(
            level="info",
            event_code="symbol_metadata_sync_completed",
            message="Symbol metadata sync finished.",
            context={
                "synced_count": counts["synced_count"],
                "skipped_count": counts["skipped_count"],
                "failed_count": counts["failed_count"],
                "outcome": counts["outcome"],
                "operation_failure": counts["operation_failure"],
            },
        )

        # D-28: only outcome `failed` raises (after the completion log); a
        # partial result keeps the Job SUCCEEDED and is visible as outcome.
        result.raise_if_failed()

        # D-01: post-call cancellation checkpoint -- a cancel requested
        # during the call is acknowledged here.
        context.raise_if_cancelled()

        return {
            "synced": counts["synced"],
            "skipped": counts["skipped"],
            "failed": counts["failed"],
            "synced_count": counts["synced_count"],
            "skipped_count": counts["skipped_count"],
            "failed_count": counts["failed_count"],
            "outcome": counts["outcome"],
            "failures": counts["failures"],
            "operation_failure": counts["operation_failure"],
            "produced_run_ids": [],
        }
