"""Test-only builders for the paper account checks A1-A7 (PAPER-02, 20.1-12).

Direct ORM writes: production code never seeds snapshots or reconciliation results this
way. Every function takes an open ``Session`` (the caller owns the transaction).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session
from tests.support.recovery_fixtures import at

from trading_platform.db.models import AccountReconciliationRun, AccountSnapshot


def seed_snapshot(
    session: Session,
    *,
    open_positions: int = 0,
    source: str = "broker_sync",
    snapshot_at: datetime | None = None,
) -> AccountSnapshot:
    snapshot = AccountSnapshot(
        strategy_id=None,
        snapshot_source=source,
        snapshot_at=snapshot_at if snapshot_at is not None else at(0),
        cash=Decimal("100000"),
        gross_exposure=Decimal("0"),
        total_equity=Decimal("100000"),
        buying_power=Decimal("200000"),
        open_positions=open_positions,
    )
    session.add(snapshot)
    session.flush()
    return snapshot


def seed_clean_account_run(
    session: Session,
    *,
    completed_at: datetime,
    non_terminal: int | None = 0,
    unrecognized_orders: int | None = 0,
    unrecognized_fills: int | None = 0,
    exposure: dict[str, str] | None = None,
    blocks: bool = False,
    status: str = "succeeded",
    unresolved_reasons: Sequence[str] = (),
) -> AccountReconciliationRun:
    """An account run with every A2-A4 field populated (override one to fail a check)."""

    result_summary: dict[str, Any] = {"stage": "completed", "scope": "account"}
    if non_terminal is not None:
        result_summary["non_terminal_order_count"] = non_terminal
    classification: dict[str, Any] = {}
    if unrecognized_orders is not None and unrecognized_fills is not None:
        classification = {
            "orders": {"owned": 0, "recorded_external": 0, "unrecognized": unrecognized_orders},
            "fills": {"owned": 0, "recorded_external": 0, "unrecognized": unrecognized_fills},
        }
    run = AccountReconciliationRun(
        trigger_source="job",
        status=status,
        completed_at=completed_at,
        blocks_execution=blocks,
        unresolved_reasons=list(unresolved_reasons),
        unexplained_exposure=dict(exposure or {}),
        classification_summary=classification,
        result_summary=result_summary,
    )
    session.add(run)
    session.flush()
    return run


def seed_quiet_account(
    session: Session, *, snapshot_minute: int = 0, run_minute: int = 10
) -> AccountReconciliationRun:
    """A flat broker-observed snapshot plus a fresh clean account reconciliation."""

    seed_snapshot(session, snapshot_at=at(snapshot_minute))
    return seed_clean_account_run(session, completed_at=at(run_minute))
