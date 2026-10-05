"""Test-only builder for a VERIFIED evaluation basis (S3-R4, 20.1-15).

A start on a risk run whose strategy already has earlier orders needs a basis verified from
persisted records: a broker-order-sync Job whose result summary records the snapshot and the
applied broker state of every earlier order, completed after the strategy's execution
watermark, a clean standalone reconciliation between that sync and the evaluation, and basis
positions equal to the positions derived from the ingested fills. Production writes these
through the real sync / reconciliation Jobs; tests arrange them directly.

Times default to ``base + 1/2/3 minutes`` (sync / reconciliation / evaluation completion) where
``base`` is the current time, so they are later than anything the test seeded before.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trading_platform.core import clock
from trading_platform.core.settings import load_settings
from trading_platform.db.models import (
    AccountReconciliationRun,
    AccountSnapshot,
    Job,
    JobStatus,
    OrderLifecycleState,
    PaperFill,
    PaperOrder,
    RiskEvent,
    Strategy,
    StrategyRun,
)

_LOCAL_TO_BROKER = {
    OrderLifecycleState.FILLED: "filled",
    OrderLifecycleState.CANCELED: "canceled",
    OrderLifecycleState.EXPIRED: "expired",
    OrderLifecycleState.REJECTED: "rejected",
}


def seed_fresh_broker_snapshot(
    session: Session, *, cash: Decimal | None = None, at: datetime | None = None
) -> AccountSnapshot:
    """Seed one broker-observed account snapshot (strategy NULL, ``broker_sync``) (SAF-09).

    Execution sizes only against cash from a fresh broker-observed snapshot, so a test that sends
    arranges one BEFORE its evaluation. ``cash`` defaults to the configured starting cash so the
    sized quantities are unchanged; ``at`` defaults to the application clock.
    """

    resolved_cash = cash if cash is not None else load_settings().portfolio.starting_cash_decimal
    snapshot = AccountSnapshot(
        strategy_id=None,
        source_run_id=None,
        snapshot_source="broker_sync",
        snapshot_at=at or clock.now_utc(),
        cash=resolved_cash,
        gross_exposure=Decimal("0"),
        total_equity=resolved_cash,
        buying_power=resolved_cash,
        open_positions=0,
    )
    session.add(snapshot)
    session.flush()
    return snapshot


@dataclass(frozen=True)
class SeededBasis:
    sync_job_id: uuid.UUID
    snapshot_id: uuid.UUID
    reconciliation_id: uuid.UUID | None
    sync_completed_at: datetime
    reconciliation_completed_at: datetime
    risk_completed_at: datetime


def seed_verified_basis(
    session: Session,
    *,
    risk_run_id: uuid.UUID,
    strategy_id: str = "trend_following_daily",
    positions: Sequence[tuple[str, str]] = (),
    base: datetime | None = None,
    clean_reconciliation: bool = True,
    with_reconciliation: bool = True,
    applied_status: Mapping[uuid.UUID, str] | None = None,
    broker_filled_qty: Mapping[uuid.UUID, str] | None = None,
) -> SeededBasis:
    """Write the sync Job, snapshot, reconciliation and the risk run's recorded basis."""

    start = base or datetime.now(UTC)
    sync_at = start + timedelta(minutes=1)
    recon_at = start + timedelta(minutes=2)
    risk_at = start + timedelta(minutes=3)

    risk_run = session.get(StrategyRun, risk_run_id)
    assert risk_run is not None
    orders = session.execute(
        select(PaperOrder)
        .join(StrategyRun, StrategyRun.id == PaperOrder.strategy_run_id)
        .join(Strategy, Strategy.id == StrategyRun.strategy_id)
        .outerjoin(RiskEvent, RiskEvent.id == PaperOrder.source_risk_event_id)
        .where(
            Strategy.strategy_id == strategy_id,
            (RiskEvent.strategy_run_id.is_(None)) | (RiskEvent.strategy_run_id != risk_run_id),
        )
    ).scalars().all()
    fills = dict(
        session.execute(
            select(PaperFill.paper_order_id, func.sum(PaperFill.quantity)).group_by(
                PaperFill.paper_order_id
            )
        ).all()
    )
    applied: list[dict[str, Any]] = []
    for order in orders:
        has_evidence = bool(order.broker_order_id) or order.status in _LOCAL_TO_BROKER
        if not has_evidence or order.status is OrderLifecycleState.REJECTED:
            continue
        raw = (applied_status or {}).get(order.id) or _LOCAL_TO_BROKER.get(order.status, "new")
        quantity = (broker_filled_qty or {}).get(order.id)
        if quantity is None:
            quantity = str(fills.get(order.id, Decimal("0")))
        applied.append(
            {
                "paper_order_id": str(order.id),
                "broker_status": raw,
                "broker_filled_qty": quantity,
                "applied_at": sync_at.isoformat(),
            }
        )
        order.last_synced_at = sync_at

    snapshot = AccountSnapshot(
        snapshot_source="broker_sync",
        snapshot_at=sync_at,
        cash=Decimal("100000"),
        gross_exposure=Decimal("0"),
        total_equity=Decimal("100000"),
        buying_power=Decimal("100000"),
        open_positions=len(positions),
    )
    session.add(snapshot)
    session.flush()
    sync_job = Job(
        job_type="broker-order-sync",
        payload={"strategy_id": strategy_id},
        status=JobStatus.SUCCEEDED,
        completed_at=sync_at,
        result_summary={
            "snapshot_id": str(snapshot.id),
            "account_snapshot_id": str(snapshot.id),
            "applied_orders": applied,
        },
    )
    session.add(sync_job)
    reconciliation = AccountReconciliationRun(
        trigger_source="job",
        status="succeeded",
        completed_at=recon_at,
        blocks_execution=not clean_reconciliation,
        unresolved_reasons=[],
    )
    if with_reconciliation:
        session.add(reconciliation)
    risk_run.completed_at = risk_at
    summary = dict(risk_run.result_summary or {})
    summary["portfolio_basis"] = {
        "source": "broker_sync",
        "snapshot_id": str(snapshot.id),
        "positions": [
            {"symbol": symbol, "quantity": quantity, "market_value": "0", "strategy_id": strategy_id}
            for symbol, quantity in positions
        ],
    }
    risk_run.result_summary = summary
    session.flush()
    return SeededBasis(
        sync_job_id=sync_job.id,
        snapshot_id=snapshot.id,
        reconciliation_id=reconciliation.id if with_reconciliation else None,
        sync_completed_at=sync_at,
        reconciliation_completed_at=recon_at,
        risk_completed_at=risk_at,
    )
