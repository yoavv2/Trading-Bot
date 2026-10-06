"""Runs the REAL standalone reconciliation services for G-1 evidence (20.1-32).

Never seeds a reconciliation row; every result is produced by ``reconcile_account`` /
``reconcile_paper_execution`` against a scripted read-side broker. Time is wall-clock because both
services stamp ``completed_at`` with ``datetime.now(UTC)``: a standalone reconciliation can only
ever be "after" an effect Job that completed at or before the wall clock, so a test timeline must
never be dated in the future (a Job completed in the future could never be followed by a real
reconciliation).

The helpers mirror what the ``reconciliation`` Job handler does (``trigger_source="job"`` and the
Job's id), which is exactly what the recovery gate requires of a standalone result: an account run
qualifies for every strategy, a strategy run only when it was created by a ``reconciliation`` Job.
The caller owns the database (a throwaway migrated database) and the strategy rows.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from tests.test_paper_execution import FakeBrokerClient

from trading_platform.core.settings import load_settings
from trading_platform.db.models import (
    AccountReconciliationRun,
    Job,
    JobEventType,
    JobStatus,
    StrategyRun,
)
from trading_platform.db.session import session_scope
from trading_platform.jobs.lifecycle import JobTransitionRequest, apply_job_transition
from trading_platform.services.account_baseline import latest_broker_observed_account_snapshot
from trading_platform.services.alpaca import (
    BrokerAccountSnapshot,
    BrokerFillSnapshot,
    BrokerOrderSnapshot,
    BrokerPositionSnapshot,
)
from trading_platform.services.reconciliation import (
    AccountReconciliationReport,
    ReconciliationReport,
    reconcile_account,
    reconcile_paper_execution,
)

#: The lease holder written on the RUNNING Job a helper creates (the Job is finished before the
#: helper returns, so the lease is never contended).
_LEASE_OWNER = "real-reconciliation-helper"

#: The flat account used when no broker-observed account snapshot exists yet.
_FLAT_ACCOUNT_EQUITY = Decimal("100000.000000")


class WallClockTimeline:
    """Strictly increasing WALL-CLOCK instants.

    Same ``next()`` interface as the seeded timeline of the CR-01 end-to-end suite, so a scenario
    can take either. Every instant is the real current time (never dated ahead), so a real
    reconciliation that runs afterwards completes AFTER an effect boundary placed with it.
    """

    def __init__(self) -> None:
        self._last: datetime | None = None

    def next(self) -> datetime:
        instant = datetime.now(UTC)
        while self._last is not None and instant <= self._last:
            time.sleep(0.001)
            instant = datetime.now(UTC)
        self._last = instant
        return instant


def _mirrored_account(positions: Sequence[BrokerPositionSnapshot]) -> BrokerAccountSnapshot:
    """The broker account the local books expect: the latest broker-observed snapshot, so account
    divergence is zero; the flat 100000 account when none exists."""

    long_value = sum((p.market_value for p in positions if p.quantity > 0), start=Decimal("0"))
    short_value = sum((p.market_value for p in positions if p.quantity < 0), start=Decimal("0"))
    with session_scope(load_settings()) as session:
        snapshot = latest_broker_observed_account_snapshot(session)
        if snapshot is None:
            return BrokerAccountSnapshot(
                cash=_FLAT_ACCOUNT_EQUITY,
                buying_power=_FLAT_ACCOUNT_EQUITY,
                equity=_FLAT_ACCOUNT_EQUITY,
                long_market_value=long_value,
                short_market_value=short_value,
                raw_payload={"equity": str(_FLAT_ACCOUNT_EQUITY)},
            )
        return BrokerAccountSnapshot(
            cash=snapshot.cash,
            buying_power=snapshot.buying_power,
            equity=snapshot.total_equity,
            long_market_value=long_value,
            short_market_value=short_value,
            raw_payload={"equity": str(snapshot.total_equity)},
        )


def scripted_read_broker(
    *,
    orders: Sequence[BrokerOrderSnapshot] = (),
    fills: Sequence[BrokerFillSnapshot] = (),
    positions: Sequence[BrokerPositionSnapshot] = (),
) -> FakeBrokerClient:
    """A read-side broker for a reconciliation: it lists the given orders, fills and positions and
    reports an account that mirrors the latest broker-observed snapshot (long/short market value
    0 when flat), so account divergence stays zero. Nothing is ever POSTed through it."""

    return FakeBrokerClient(
        orders=list(orders),
        fills=list(fills),
        positions=list(positions),
        account=_mirrored_account(positions),
    )


def _start_reconciliation_job(payload: Mapping[str, Any]) -> uuid.UUID:
    """A RUNNING ``reconciliation`` Job holding a lease, as the worker would have claimed it."""

    now = datetime.now(UTC)
    with session_scope(load_settings()) as session:
        job = Job(
            job_type="reconciliation",
            payload=dict(payload),
            status=JobStatus.RUNNING,
            started_at=now,
            lease_owner=_LEASE_OWNER,
            lease_expires_at=now + timedelta(days=1),
        )
        session.add(job)
        session.flush()
        return job.id


def _finish_job(job_id: uuid.UUID) -> None:
    """The Job succeeds through the Job lifecycle at the current wall-clock instant."""

    with session_scope(load_settings()) as session:
        apply_job_transition(
            session,
            job_id=job_id,
            request=JobTransitionRequest(event_type=JobEventType.SUCCEEDED, event_at=datetime.now(UTC)),
        )


def run_real_account_reconciliation(
    *, broker: FakeBrokerClient
) -> tuple[AccountReconciliationReport, uuid.UUID]:
    """One REAL account-scope standalone reconciliation, through a ``reconciliation`` Job."""

    job_id = _start_reconciliation_job({"scope": "account"})
    report = reconcile_account(
        trigger_source="job", job_id=job_id, broker_client=broker, settings=load_settings()
    )
    _finish_job(job_id)
    return report, job_id


def run_real_strategy_reconciliation(
    *, strategy_id: str, as_of_session: date, broker: FakeBrokerClient
) -> tuple[ReconciliationReport, uuid.UUID]:
    """One REAL strategy-scope standalone reconciliation, through a ``reconciliation`` Job, with the
    trigger source and Job link that make it qualify for the recovery gate."""

    job_id = _start_reconciliation_job(
        {"strategy_id": strategy_id, "as_of_session": as_of_session.isoformat()}
    )
    report = reconcile_paper_execution(
        strategy_id,
        as_of_session=as_of_session,
        trigger_source="job",
        job_id=job_id,
        broker_client=broker,
        settings=load_settings(),
    )
    _finish_job(job_id)
    return report, job_id


def reconciliation_completed_at(report: AccountReconciliationReport | ReconciliationReport) -> datetime:
    """The stored run's ``completed_at`` (the account or the strategy table, by report type)."""

    run_id = uuid.UUID(report.run_id)
    with session_scope(load_settings()) as session:
        if isinstance(report, AccountReconciliationReport):
            completed = session.execute(
                select(AccountReconciliationRun.completed_at).where(
                    AccountReconciliationRun.id == run_id
                )
            ).scalar_one()
        else:
            completed = session.execute(
                select(StrategyRun.completed_at).where(StrategyRun.id == run_id)
            ).scalar_one()
    if completed is None:
        raise LookupError(f"Reconciliation run '{run_id}' has no completed_at.")
    return completed
