"""``signals-daily`` Job: run one signal book up to one session (research mode only).

A thin adapter around ``services.signals.daily.run_daily_book``: the service owns every
persistent write, the framework owns the Job lifecycle. Cancellation is honoured between
steps and between assets; the sessions already written stay written, and the next run
continues from them.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from trading_platform.core.settings import Settings, load_settings
from trading_platform.jobs.contracts import JobContext
from trading_platform.services.config.validation import ExecutionMode
from trading_platform.services.signals.daily import run_daily_book
from trading_platform.services.signals.publication import BOOKS_DIRECTORY
from trading_platform.services.tiingo import TiingoClient


class SignalsDailyJobHandler:
    job_type = "signals-daily"
    required_execution_mode = ExecutionMode.BACKTEST

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: TiingoClient | None = None,
        clock: Callable[[], datetime] | None = None,
        books_directory: Path = BOOKS_DIRECTORY,
    ) -> None:
        self._settings = settings
        self._client = client
        self._clock = clock or (lambda: datetime.now(UTC))
        self._books_directory = books_directory

    def run(self, context: JobContext) -> dict[str, Any]:
        settings = self._settings or load_settings()
        requested = context.payload.get("session_date")
        context.raise_if_cancelled()
        report = run_daily_book(
            settings,
            book_name=context.payload["book"],
            session_date=None if requested is None else date.fromisoformat(requested),
            ingest=bool(context.payload.get("ingest", True)),
            clock=self._clock,
            client=self._client,
            job_id=context.job_id,
            books_directory=self._books_directory,
            progress=lambda step: context.report_progress(step=step),
            checkpoint=context.raise_if_cancelled,
        )
        context.log(
            level="info" if report["complete"] else "warning",
            event_code="signals_daily_completed",
            message="Daily signal run finished.",
            context={
                key: report[key]
                for key in (
                    "book",
                    "session_date",
                    "sessions_processed",
                    "events",
                    "assets_skipped",
                    "assets_refused",
                    "publications",
                    "open_published",
                )
            },
        )
        return {**report, "produced_run_ids": []}
