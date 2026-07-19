"""Worker CLI command → handler dispatch mapping (STRUCT-03).

`worker/__main__.py` imports `DISPATCH` from this package to resolve a
parsed `args.command` string to its handler, keeping the entrypoint itself
limited to parser construction, dispatch, and top-level error handling.

`serve`/`dry-run` are intentionally excluded from `DISPATCH`: their handlers
(`run_placeholder_worker`, `run_dry_bootstrap`) take positional scalar
arguments rather than the uniform `(args: argparse.Namespace) -> None`
signature every other handler shares, so `__main__.main()` special-cases
them exactly as the pre-split entrypoint did.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable

from trading_platform.worker.commands.backtest import (
    run_backtest_command,
    run_report_backtest_command,
    run_report_strategy_analytics_command,
)
from trading_platform.worker.commands.bootstrap import run_dry_bootstrap, run_placeholder_worker
from trading_platform.worker.commands.ingest import (
    run_ingest_bars,
    run_sync_metadata,
    run_sync_sessions,
)
from trading_platform.worker.commands.operator import (
    run_operator_control_command,
    run_operator_status_command,
)
from trading_platform.worker.commands.paper_execute import (
    run_paper_session_command,
    run_submit_paper_orders_command,
    run_sync_paper_state_command,
)
from trading_platform.worker.commands.reconcile import run_reconcile_paper_execution_command
from trading_platform.worker.commands.risk_check import run_evaluate_risk_command
from trading_platform.worker.commands.run_jobs import run_jobs_command

DISPATCH: dict[str, Callable[[argparse.Namespace], None]] = {
    "backtest": run_backtest_command,
    "report-backtest": run_report_backtest_command,
    "report-strategy-analytics": run_report_strategy_analytics_command,
    "evaluate-risk": run_evaluate_risk_command,
    "submit-paper-orders": run_submit_paper_orders_command,
    "run-paper-session": run_paper_session_command,
    "sync-paper-state": run_sync_paper_state_command,
    "reconcile-paper-execution": run_reconcile_paper_execution_command,
    "operator-control": run_operator_control_command,
    "operator-status": run_operator_status_command,
    "ingest-bars": run_ingest_bars,
    "sync-metadata": run_sync_metadata,
    "sync-sessions": run_sync_sessions,
    "run-jobs": run_jobs_command,
}

__all__ = ["DISPATCH", "run_dry_bootstrap", "run_placeholder_worker"]
