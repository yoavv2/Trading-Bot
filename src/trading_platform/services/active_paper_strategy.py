"""Active paper strategy ownership: the domain read and the pure predicate (PAPER-01).

At most one strategy owns the Alpaca paper account (D-01). The owner is the
single row of the ``active_paper_strategy`` singleton; ``strategy_id IS NULL``
means no owner (D-02). This module owns:

- ``OwnershipBlock``: the closed set of submit-time ownership refusals.
- ``BLOCKED_REASON_NOT_ACTIVE_PAPER_STRATEGY``: the run-time blocked reason
  (distinct from the submit-time codes; it does not distinguish no-owner from
  another owner, and the run's ``ownership_block`` detail carries which).
- ``load_active_paper_strategy``: a pure read of the singleton (no write, no
  get-or-create). A missing row is a typed error: the row is seeded by
  migration 0022 and every consumer fails closed without it.
- ``ownership_block_for``: the ONE ownership predicate. It is a thin wrapper
  over the shared gate loader (``operator_controls.load_trading_gate_state``,
  R-Q1), so the submit-time check, the Job-admission re-check, the run-time
  pre-checks and the per-candidate re-check all apply the same decision to the
  same single-statement read. Ownership is re-read at every call, never cached.
- ``lock_active_paper_strategy_shared``: the SER admission lock (FOR SHARE on
  the singleton row), taken inside the Job-insert transaction.

Seeding/handover mutations are NOT here (20.1-12).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_platform.core.settings import Settings
from trading_platform.db.models import ActivePaperStrategy, Strategy
from trading_platform.db.models.active_paper_strategy import ACTIVE_PAPER_STRATEGY_SINGLETON_ID
from trading_platform.db.session import session_scope

if TYPE_CHECKING:
    from trading_platform.services.operator_controls import TradingGateState

BLOCKED_REASON_NOT_ACTIVE_PAPER_STRATEGY = "not_active_paper_strategy"


class OwnershipBlock(StrEnum):
    """Closed set of submit-time ownership refusals (HTTP 409 codes, D-03)."""

    NO_ACTIVE_PAPER_STRATEGY = "no_active_paper_strategy"
    STRATEGY_NOT_ACTIVE_PAPER_STRATEGY = "strategy_not_active_paper_strategy"


class ActivePaperStrategyUnavailableError(LookupError):
    """The ``active_paper_strategy`` singleton row is missing.

    The row is seeded by migration 0022 and is never deleted or created by
    application code, so its absence means migrations are not current. Every
    ownership consumer fails closed.
    """


@dataclass(frozen=True)
class ActivePaperStrategyState:
    """The singleton row joined to the owning strategy's public identity.

    ``strategy_id`` is the public (string) strategy id, ``None`` when no
    strategy owns the account.
    """

    strategy_id: str | None
    display_name: str | None
    since: datetime
    reason: str | None
    set_by_run_id: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "display_name": self.display_name,
            "since": self.since.isoformat(),
            "reason": self.reason,
            "set_by_run_id": self.set_by_run_id,
        }


def ownership_block_from_state(
    state: ActivePaperStrategyState, strategy_id: str
) -> OwnershipBlock | None:
    """Pure decision: ``None`` when ``strategy_id`` is the owner."""

    if state.strategy_id is None:
        return OwnershipBlock.NO_ACTIVE_PAPER_STRATEGY
    if state.strategy_id != strategy_id:
        return OwnershipBlock.STRATEGY_NOT_ACTIVE_PAPER_STRATEGY
    return None


def load_active_paper_strategy(
    settings: Settings | None = None, *, session: Session | None = None
) -> ActivePaperStrategyState:
    """Pure read of the singleton (one SELECT, no write, no get-or-create)."""

    if session is not None:
        return _load_active_paper_strategy(session)
    with session_scope(settings) as scoped:
        return _load_active_paper_strategy(scoped)


def _load_active_paper_strategy(session: Session) -> ActivePaperStrategyState:
    row = session.execute(
        select(
            ActivePaperStrategy.since,
            ActivePaperStrategy.reason,
            ActivePaperStrategy.set_by_run_id,
            Strategy.strategy_id.label("owner_public_id"),
            Strategy.display_name.label("owner_display_name"),
        )
        .select_from(ActivePaperStrategy)
        .outerjoin(Strategy, Strategy.id == ActivePaperStrategy.strategy_id)
        .where(ActivePaperStrategy.id == ACTIVE_PAPER_STRATEGY_SINGLETON_ID)
    ).one_or_none()
    if row is None:
        raise ActivePaperStrategyUnavailableError(
            "Missing active_paper_strategy singleton row; database migrations may not be current."
        )
    return ActivePaperStrategyState(
        strategy_id=row.owner_public_id,
        display_name=row.owner_display_name,
        since=row.since,
        reason=row.reason,
        set_by_run_id=str(row.set_by_run_id) if row.set_by_run_id is not None else None,
    )


def ownership_block_for(
    strategy_id: str,
    *,
    settings: Settings | None = None,
    session: Session | None = None,
) -> OwnershipBlock | None:
    """The one ownership predicate (D-03): ``None`` when ``strategy_id`` owns the account.

    One fresh single-statement gate read per call (R-Q1), never cached, never
    writes. Pass ``session`` to evaluate inside an open transaction (Job
    admission); otherwise a short read session is used.
    """

    # Deferred import: operator_controls imports this module's types.
    from trading_platform.services import operator_controls

    if session is not None:
        gate: TradingGateState = operator_controls.load_trading_gate_state(session)
    else:
        gate = operator_controls.read_trading_gate_state(settings)
    return gate.ownership_block_for(strategy_id)


def lock_active_paper_strategy_shared(session: Session) -> None:
    """SER admission lock: ``SELECT ... FOR SHARE`` on the singleton row.

    Taken first inside the Job-insert transaction (lock order: singleton first)
    so a concurrent handover (FOR UPDATE) and an admission serialize. Fails
    closed when the row is missing.
    """

    locked = session.execute(
        select(ActivePaperStrategy.id)
        .where(ActivePaperStrategy.id == ACTIVE_PAPER_STRATEGY_SINGLETON_ID)
        .with_for_update(read=True)
    ).scalar_one_or_none()
    if locked is None:
        raise ActivePaperStrategyUnavailableError(
            "Missing active_paper_strategy singleton row; database migrations may not be current."
        )
