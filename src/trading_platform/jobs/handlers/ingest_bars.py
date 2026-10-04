"""IngestBarsJobHandler: the ``ingest-bars`` Job type's execution unit
(OPS-05, D-01, D-08, D-09, D-11, P19 D-22).

Calls the existing ``services.ingestion.ingest_daily_bars`` exactly once,
bracketed by two cooperative-cancellation checkpoints (D-01). Progress is
reported as step text only, never a fabricated completion fraction -- the
framework owns the Job's own terminal-state progress. The service decides the
run outcome (D-08a): a partial run returns normally and the Job SUCCEEDS with
``ingestion_succeeded`` mirroring ``IngestionResult.succeeded``; an
all-symbols-failed run raises the service's ``IngestionAllSymbolsFailedError``,
which this handler propagates unchanged, so the Job lands FAILED/handler_error.
The handler never derives status itself (invariant 2).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from trading_platform.core.settings import Settings, load_settings
from trading_platform.jobs.contracts import JobContext
from trading_platform.services.batch_outcomes import outcome_from_ingestion_run_status
from trading_platform.services.config.validation import ExecutionMode
from trading_platform.services.ingestion import ingest_daily_bars

STEP_RESOLVING = "resolving window"
STEP_INGESTING = "ingesting bars"
STEP_RECORDING = "recording result"


class IngestBarsJobHandler:
    """Runs one daily-bar ingestion for an explicit date range and symbol
    list."""

    job_type = "ingest-bars"
    required_execution_mode = ExecutionMode.BACKTEST

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings

    def run(self, context: JobContext) -> dict[str, Any]:
        context.report_progress(step=STEP_RESOLVING)
        resolved = self._settings or load_settings()
        from_date = date.fromisoformat(context.payload["from_date"])
        to_date = date.fromisoformat(context.payload["to_date"])
        symbols = list(context.payload["symbols"])

        # D-01: pre-call cancellation checkpoint -- a cancel requested
        # before start means ingest_daily_bars is never called.
        context.raise_if_cancelled()

        context.report_progress(step=STEP_INGESTING)
        context.log(
            level="info",
            event_code="ingest_bars_started",
            message="Bar ingestion started.",
            context={
                "from_date": context.payload["from_date"],
                "to_date": context.payload["to_date"],
                "symbol_count": len(symbols),
            },
        )

        result = ingest_daily_bars(
            from_date=from_date,
            to_date=to_date,
            symbols=symbols,
            settings=resolved.market_data,
            trigger_source="job",
            db_settings=resolved,
            job_id=context.job_id,
        )

        context.report_progress(step=STEP_RECORDING)
        context.log(
            level="info",
            event_code="ingest_bars_completed",
            message="Bar ingestion finished.",
            context={
                "run_id": result.run_id,
                "bars_upserted": result.bars_upserted,
                "failed_count": result.failed_count,
                "run_status": result.run_status,
            },
        )

        # D-08a: an all-symbols-failed run raises the service-defined typed
        # error here, BEFORE the post-call cancel checkpoint, so all-fail wins
        # over a concurrent cancel: the Job lands FAILED, not CANCELLED.
        result.raise_for_all_symbols_failed()

        # D-01: post-call cancellation checkpoint -- a cancel requested
        # during the call is acknowledged here. The linked
        # MarketDataIngestionRun keeps its real (already-persisted) outcome
        # (invariant 2); only the Job lands CANCELLED.
        context.raise_if_cancelled()

        return {
            "run_id": result.run_id,
            "produced_run_ids": [result.run_id],
            "from_date": context.payload["from_date"],
            "to_date": context.payload["to_date"],
            "symbol_count": result.symbol_count,
            "bars_upserted": result.bars_upserted,
            "symbols_failed": result.symbols_failed,
            "ingestion_succeeded": result.succeeded,
            # D-28/COR-03: closed outcome derived from the domain run status.
            "outcome": outcome_from_ingestion_run_status(result.run_status).value,
        }
