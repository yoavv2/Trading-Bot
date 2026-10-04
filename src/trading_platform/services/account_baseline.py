"""Broker-observed account baseline (D-27, COR-01).

The account baseline is the newest ``AccountSnapshot`` whose ``snapshot_source``
is ``broker_sync``: the only source that records what the broker itself
reported. Every other source (``risk_evaluation``, ``seed``, ``derived``) is
excluded by that equality filter, so no data migration is needed for the
``risk_evaluation`` rows written before 20.1-03. Those rows carried
``buying_power = cash`` and, once they became the "latest" local account state,
made every reconciliation diverge from a broker whose buying power is a
multiple of cash (29 Sep). The filter is account-wide: ``strategy_id`` may be
any strategy or NULL (owner-less account-level snapshots).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_platform.db.models import AccountSnapshot

BROKER_OBSERVED_SNAPSHOT_SOURCE = "broker_sync"


def latest_broker_observed_account_snapshot(session: Session) -> AccountSnapshot | None:
    """Return the newest broker-observed account snapshot, or ``None`` (one statement)."""

    return session.execute(
        select(AccountSnapshot)
        .where(AccountSnapshot.snapshot_source == BROKER_OBSERVED_SNAPSHOT_SOURCE)
        .order_by(AccountSnapshot.snapshot_at.desc(), AccountSnapshot.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
