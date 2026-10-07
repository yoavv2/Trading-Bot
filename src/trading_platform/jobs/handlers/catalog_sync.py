"""``catalog-sync`` Job: refresh the research asset catalog from the public ticker list
and the public name directories (manual refresh; research mode only).

Downloads are keyless public files (no Tiingo API budget is spent). The sync is one
transaction; the result reports total and named rows and states that name search
covers populated names only.
"""

from __future__ import annotations

from typing import Any

import httpx

from trading_platform.core.settings import Settings, load_settings
from trading_platform.db.session import session_scope
from trading_platform.jobs.contracts import JobContext
from trading_platform.services.config.validation import ExecutionMode
from trading_platform.services.research.catalog import fetch_catalog_sources, sync_asset_catalog

STEP_DOWNLOADING = "downloading public ticker and name directories"
STEP_SYNCING = "syncing catalog"
STEP_RECORDING = "recording result"


class CatalogSyncJobHandler:
    job_type = "catalog-sync"
    required_execution_mode = ExecutionMode.BACKTEST

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings

    def run(self, context: JobContext) -> dict[str, Any]:
        resolved = self._settings or load_settings()
        include_names = bool(context.payload["include_names"])
        user_agent = f"{resolved.app.slug}/{resolved.app.version} research catalog sync"

        context.raise_if_cancelled()
        context.report_progress(step=STEP_DOWNLOADING)
        with httpx.Client(timeout=resolved.research.tiingo.timeout_seconds, follow_redirects=True) as client:
            sources = fetch_catalog_sources(client, user_agent=user_agent, include_names=include_names)

        context.raise_if_cancelled()
        context.report_progress(step=STEP_SYNCING)
        with session_scope(resolved) as session:
            report = sync_asset_catalog(session, sources)

        context.report_progress(step=STEP_RECORDING)
        context.log(
            level="info",
            event_code="catalog_sync_completed",
            message="Asset catalog synced.",
            context=report.to_dict(),
        )
        context.raise_if_cancelled()
        return report.to_dict()
