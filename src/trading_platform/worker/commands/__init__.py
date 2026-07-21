"""Worker CLI command → handler dispatch mapping (STRUCT-03).

`worker/__main__.py` imports `DISPATCH` from this package to resolve a
parsed `args.command` string to its handler, keeping the entrypoint itself
limited to parser construction, dispatch, and top-level error handling.

`serve` is intentionally excluded from `DISPATCH`: its handler
(`run_placeholder_worker`) takes a positional scalar rather than the uniform
`(args: argparse.Namespace) -> None` signature every other handler shares, so
`__main__.main()` special-cases it.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable

from trading_platform.worker.commands.backtest import (
    run_report_backtest_command,
    run_report_strategy_analytics_command,
)
from trading_platform.worker.commands.bootstrap import run_placeholder_worker
from trading_platform.worker.commands.operator import run_operator_status_command
from trading_platform.worker.commands.run_jobs import run_jobs_command

DISPATCH: dict[str, Callable[[argparse.Namespace], None]] = {
    "report-backtest": run_report_backtest_command,
    "report-strategy-analytics": run_report_strategy_analytics_command,
    "operator-status": run_operator_status_command,
    "run-jobs": run_jobs_command,
}

__all__ = ["DISPATCH", "run_placeholder_worker"]
