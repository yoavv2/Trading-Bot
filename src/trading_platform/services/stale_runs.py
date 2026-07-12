"""Stale paper-execution run detection and lazy reclaim (LOCK-04, LOCK-05).

A `strategy_run` row can be left in `status=running` forever if its process
crashed mid-flight without reaching a terminal status. `find_stale_runs()` is
the single-query detector: any `running` `paper_execution` run whose
`started_at` is older than the configured timeout is stale. `reclaim_stale_runs()`
is the explicit, audited remediation for one `(strategy_id, session_date)`
tuple: it flips every past-threshold `running` row for that tuple to `STALE`
and writes a durable `ExecutionEvent` per reclaimed row, reusing Phase 7's
`StrategyRun` + `ExecutionEvent` audit pattern rather than a parallel channel.
Detection and reclaim are lazy -- resolved when a new run for the tuple
starts and finds the leftover row, not by a background job.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_platform.db.models.execution_event import ExecutionEvent
from trading_platform.db.models.strategy import Strategy
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


def reclaim_stale_runs(
    session: Session,
    *,
    strategy_public_id: str,
    session_date: date,
    timeout_minutes: int,
    reclaiming_run_id: uuid.UUID | None = None,
) -> list[uuid.UUID]:
    """Flip every past-threshold running row for `(strategy, session_date)` to STALE.

    Marks ALL matching rows, not just the latest, and inserts one durable
    `ExecutionEvent` per reclaimed row. Idempotent: once a row is STALE it no
    longer matches `status=RUNNING`, so a second call finds nothing to
    reclaim and returns an empty list. Does not commit -- the caller owns the
    transaction boundary.
    """
    cutoff = datetime.now(UTC) - timedelta(minutes=timeout_minutes)
    stmt = (
        select(StrategyRun)
        .join(Strategy, Strategy.id == StrategyRun.strategy_id)
        .where(
            Strategy.strategy_id == strategy_public_id,
            StrategyRun.status == StrategyRunStatus.RUNNING,
            StrategyRun.run_type == StrategyRunType.PAPER_EXECUTION,
            StrategyRun.started_at < cutoff,
        )
    )
    candidates = session.execute(stmt).scalars().all()

    # session_date is deliberately NOT a first-class column on strategy_runs
    # (per Phase 8 CONTEXT, the only Phase-8 migration is the STALE enum
    # value). It instead lives inside the JSON parameters_snapshot /
    # result_summary blobs as "as_of_session", so the tuple-scoped match is
    # done in Python against the already-fetched rows rather than in SQL.
    target = session_date.isoformat()
    now = datetime.now(UTC)
    reclaimed_ids: list[uuid.UUID] = []

    for run in candidates:
        run_session_date = run.parameters_snapshot.get("as_of_session") or run.result_summary.get(
            "as_of_session"
        )
        if run_session_date != target:
            continue

        run.status = StrategyRunStatus.STALE
        run.completed_at = now
        session.add(
            ExecutionEvent(
                strategy_run_id=run.id,
                paper_order_id=None,
                event_type="paper_run_reclaimed_stale",
                severity="warning",
                blocks_execution=False,
                event_at=now,
                message=(
                    f"Reclaimed stale running paper-execution run '{run.id}' for strategy "
                    f"'{strategy_public_id}' session {target}: started_at was older than the "
                    f"{timeout_minutes}-minute timeout."
                ),
                details={
                    "reclaimed_run_id": str(run.id),
                    "reclaiming_run_id": str(reclaiming_run_id) if reclaiming_run_id else None,
                    "session_date": target,
                    "timeout_minutes": timeout_minutes,
                },
            )
        )
        reclaimed_ids.append(run.id)

    session.flush()
    return reclaimed_ids
