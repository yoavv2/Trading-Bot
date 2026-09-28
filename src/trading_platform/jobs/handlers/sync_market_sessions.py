"""SyncMarketSessionsJobHandler: the ``sync-market-sessions`` Job type's
execution unit (OPS-05, D-01, D-08).

Calls the existing ``services.calendar.sync_market_sessions`` exactly once,
bracketed by two cooperative-cancellation checkpoints (D-01), and never
opens a database session itself -- the calendar service wrapper owns the
session (Elevation-of-Privilege mitigation, T-20-15-03). Progress is
reported as step text only, never a fabricated completion fraction -- the
framework owns the Job's own terminal-state progress.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from trading_platform.core.settings import Settings, load_settings
from trading_platform.jobs.contracts import JobContext
from trading_platform.services.calendar import sync_market_sessions
from trading_platform.services.config.validation import ExecutionMode

STEP_RESOLVING = "resolving window"
STEP_SYNCING = "syncing market sessions"
STEP_RECORDING = "recording result"


class SyncMarketSessionsJobHandler:
    """Runs one exchange trading-session sync for an explicit date range."""

    job_type = "sync-market-sessions"
    required_execution_mode = ExecutionMode.BACKTEST

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings

    def run(self, context: JobContext) -> dict[str, Any]:
        context.report_progress(step=STEP_RESOLVING)
        resolved = self._settings or load_settings()
        from_date = date.fromisoformat(context.payload["from_date"])
        to_date = date.fromisoformat(context.payload["to_date"])

        # D-01: pre-call cancellation checkpoint -- a cancel requested
        # before start means sync_market_sessions is never called.
        context.raise_if_cancelled()

        context.report_progress(step=STEP_SYNCING)
        context.log(
            level="info",
            event_code="sync_market_sessions_started",
            message="Market session sync started.",
            context={
                "from_date": context.payload["from_date"],
                "to_date": context.payload["to_date"],
            },
        )

        result = sync_market_sessions(from_date=from_date, to_date=to_date, settings=resolved)

        context.report_progress(step=STEP_RECORDING)
        context.log(
            level="info",
            event_code="sync_market_sessions_completed",
            message="Market session sync finished.",
            context={"sessions_upserted": result.sessions_upserted},
        )

        # D-01: post-call cancellation checkpoint -- a cancel requested
        # during the call is acknowledged here.
        context.raise_if_cancelled()

        return {**result.to_dict(), "produced_run_ids": []}
