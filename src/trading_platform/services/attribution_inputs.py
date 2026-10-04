"""Read-only ORM loaders feeding the pure attribution classifier (COR-05, D-07).

Services layer: these functions only READ through the caller's session and never
write. The classification itself lives in ``services.attribution`` (pure).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_platform.db.models import (
    ActivePaperStrategy,
    PaperOrder,
    Strategy,
    StrategyRun,
    Symbol,
)
from trading_platform.services.attribution import LocalIntentRecord, OwnershipPeriod


def load_local_intent_records(session: Session) -> tuple[LocalIntentRecord, ...]:
    """One record per persisted paper order across ALL strategies, in ONE statement.

    ``registered_at`` is ``PaperOrder.created_at``: the order row is written (pending
    submission) before any broker call, so it is the local registration time.
    """

    rows = session.execute(
        select(
            Strategy.strategy_id,
            PaperOrder.client_order_id,
            PaperOrder.broker_order_id,
            Symbol.ticker,
            PaperOrder.side,
            PaperOrder.quantity,
            PaperOrder.order_type,
            PaperOrder.created_at,
        )
        .join(StrategyRun, StrategyRun.id == PaperOrder.strategy_run_id)
        .join(Strategy, Strategy.id == StrategyRun.strategy_id)
        .join(Symbol, Symbol.id == PaperOrder.symbol_id)
        .order_by(PaperOrder.created_at.asc(), PaperOrder.id.asc())
    ).all()
    return tuple(
        LocalIntentRecord(
            strategy_id=row[0],
            client_order_id=row[1],
            broker_order_id=row[2],
            symbol=row[3],
            side=row[4],
            quantity=row[5],
            order_type=row[6],
            registered_at=row[7],
        )
        for row in rows
    )


def load_ownership_periods(session: Session) -> tuple[OwnershipPeriod, ...]:
    """Recorded ownership periods: today the current owner's open period ``[since, open)``.

    Empty when no strategy owns the account. Extension point: 20.1-12 appends the closed
    periods of prior owners when it records handovers; until then registrations that
    pre-date the earliest recorded start are pre-ownership history (no evidence either
    way) and remain explained by the other evidence conditions.
    """

    row = session.execute(
        select(Strategy.strategy_id, ActivePaperStrategy.since)
        .select_from(ActivePaperStrategy)
        .join(Strategy, Strategy.id == ActivePaperStrategy.strategy_id)
        .where(ActivePaperStrategy.id == 1)
    ).one_or_none()
    if row is None:
        return ()
    return (OwnershipPeriod(strategy_id=row[0], start=row[1], end=None),)


def load_recorded_external_order_ids(session: Session) -> frozenset[str]:
    """Broker order ids recorded as verified external activity (D-10 hook).

    Always empty until 20.1-09 supplies verified external-activity items; nothing in
    this plan writes such a record.
    """

    return frozenset()
