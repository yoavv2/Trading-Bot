"""``ingest-tiingo-bars`` Job: download only the requested assets for the requested
range into the research database (research mode only).

Mirrors ``ingest_bars.py``: one service call bracketed by two cooperative-cancellation
checkpoints, step-text progress only, the service decides the run outcome, an
all-assets-failed run raises the service's typed error so the Job lands FAILED.
Provider rate-limit and budget errors propagate with their closed codes so the
failure is visible, never retried silently.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from trading_platform.core.settings import Settings, load_settings
from trading_platform.jobs.contracts import JobContext
from trading_platform.services.batch_outcomes import outcome_from_ingestion_run_status
from trading_platform.services.config.validation import ExecutionMode
from trading_platform.services.research.tiingo_ingestion import ingest_tiingo_daily_bars

STEP_RESOLVING = "resolving window"
STEP_INGESTING = "downloading assets"
STEP_RECORDING = "recording result"


class IngestTiingoBarsJobHandler:
    job_type = "ingest-tiingo-bars"
    required_execution_mode = ExecutionMode.BACKTEST

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings

    def run(self, context: JobContext) -> dict[str, Any]:
        context.report_progress(step=STEP_RESOLVING)
        resolved = self._settings or load_settings()
        from_date = date.fromisoformat(context.payload["from_date"])
        to_date = date.fromisoformat(context.payload["to_date"])
        assets = list(context.payload["assets"])

        context.raise_if_cancelled()
        context.report_progress(step=STEP_INGESTING)
        context.log(
            level="info",
            event_code="ingest_tiingo_bars_started",
            message="Tiingo bar ingestion started.",
            context={
                "from_date": context.payload["from_date"],
                "to_date": context.payload["to_date"],
                "asset_count": len(assets),
            },
        )

        result = ingest_tiingo_daily_bars(
            assets=assets,
            from_date=from_date,
            to_date=to_date,
            settings=resolved,
            trigger_source="job",
            job_id=context.job_id,
        )

        context.report_progress(step=STEP_RECORDING)
        context.log(
            level="info",
            event_code="ingest_tiingo_bars_completed",
            message="Tiingo bar ingestion finished.",
            context={
                "run_id": result.run_id,
                "bars_upserted": result.bars_upserted,
                "failed_count": result.failed_count,
                "run_status": result.run_status,
            },
        )
        result.raise_for_all_symbols_failed()
        context.raise_if_cancelled()

        return {
            "run_id": result.run_id,
            "produced_run_ids": [result.run_id],
            "from_date": context.payload["from_date"],
            "to_date": context.payload["to_date"],
            "asset_count": result.symbol_count,
            "bars_upserted": result.bars_upserted,
            "assets_failed": result.symbols_failed,
            "ingestion_succeeded": result.succeeded,
            "outcome": outcome_from_ingestion_run_status(result.run_status).value,
        }
