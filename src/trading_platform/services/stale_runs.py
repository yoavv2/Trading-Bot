"""Stale paper-execution run detection and lazy reclaim (LOCK-04, LOCK-05).

A `strategy_run` row can be left in `status=running` forever if its process
crashed mid-flight without reaching a terminal status. `find_stale_runs()` is
the single-query detector: any `running` `paper_execution` run whose
`started_at` is older than the configured timeout is stale. Detection is
lazy -- resolved when a new run for the tuple starts and finds the leftover
row, not by a background job.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_platform.db.models.strategy_run import StrategyRun, StrategyRunStatus, StrategyRunType


def find_stale_runs(session: Session, *, timeout_minutes: int) -> list[StrategyRun]:
    """Return every running paper-execution run past the timeout, via ONE query.

    A run started inside the timeout window is never returned; only rows with
    `status=RUNNING`, `run_type=PAPER_EXECUTION`, and `started_at` older than
    `now() - timeout_minutes` are stale.
    """
    cutoff = datetime.now(UTC) - timedelta(minutes=timeout_minutes)
    stmt = select(StrategyRun).where(
        StrategyRun.status == StrategyRunStatus.RUNNING,
        StrategyRun.run_type == StrategyRunType.PAPER_EXECUTION,
        StrategyRun.started_at < cutoff,
    )
    return list(session.execute(stmt).scalars().all())
