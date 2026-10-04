"""Test-only helpers for strategy rows and the active paper strategy singleton.

These are DIRECT ORM writes. Production code never uses them: the owner is
seeded/handed over only by the guarded control (20.1-12). Tests use them to
arrange state explicitly -- no test may rely on an implicit owner or an
implicit ``enabled`` status (R-8: new strategy rows are created disabled).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from trading_platform.core.settings import Settings
from trading_platform.db.models import ActivePaperStrategy, Strategy, StrategyStatus
from trading_platform.db.session import session_scope
from trading_platform.services.bootstrap import ensure_strategy_record


def seed_strategy(session: Session, metadata: Any, *, enabled: bool = True) -> Strategy:
    """Get-or-create the strategy row with an EXPLICIT initial status.

    ``enabled=True`` creates an ``active`` (enabled) row, ``False`` a
    ``disabled`` one. An existing row keeps its status.
    """

    return ensure_strategy_record(
        session,
        metadata,
        initial_status=StrategyStatus.ACTIVE if enabled else StrategyStatus.DISABLED,
    )


def _apply_owner(session: Session, strategy_id: str | None, reason: str) -> None:
    owner_pk = None
    if strategy_id is not None:
        owner_pk = session.execute(
            select(Strategy.id).where(Strategy.strategy_id == strategy_id)
        ).scalar_one_or_none()
        if owner_pk is None:
            raise LookupError(
                f"Strategy '{strategy_id}' has no row; seed it with seed_strategy() first."
            )
    result = session.execute(
        update(ActivePaperStrategy)
        .where(ActivePaperStrategy.id == 1)
        .values(strategy_id=owner_pk, reason=reason, set_by_run_id=None)
    )
    if result.rowcount != 1:
        raise LookupError("active_paper_strategy singleton row is missing.")


def set_active_paper_strategy(
    settings_or_session: Settings | Session,
    strategy_id: str | None,
    *,
    reason: str = "test",
) -> None:
    """Directly set (or, with ``None``, clear) the owner. The strategy row must exist."""

    if isinstance(settings_or_session, Session):
        _apply_owner(settings_or_session, strategy_id, reason)
        settings_or_session.flush()
        return
    with session_scope(settings_or_session) as session:
        _apply_owner(session, strategy_id, reason)


def clear_active_paper_strategy(
    settings_or_session: Settings | Session, *, reason: str = "test: cleared"
) -> None:
    """Return the singleton to its seeded no-owner state."""

    set_active_paper_strategy(settings_or_session, None, reason=reason)


def seed_registered_strategy(
    settings: Settings,
    strategy_id: str = "trend_following_daily",
    *,
    enabled: bool = True,
    owner: bool = False,
) -> None:
    """Seed a registry strategy's row (explicit enabled/disabled) and, optionally, make it the owner.

    The one-call arrangement most paper-flow tests need: ``owner=True`` is an
    explicit direct write of the ownership singleton, never an implicit default.
    """

    from trading_platform.strategies.registry import build_default_registry

    metadata = build_default_registry(settings).resolve(strategy_id).metadata
    with session_scope(settings) as session:
        seed_strategy(session, metadata, enabled=enabled)
        if owner:
            _apply_owner(session, strategy_id, "test")
