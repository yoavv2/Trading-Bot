"""ORM model for the active paper strategy singleton (PAPER-01, D-01/D-02).

One row (``id = 1``) records which strategy, if any, owns the Alpaca paper
account. Two owners are unrepresentable: the primary key plus the CHECK
``id = 1`` allow exactly one row, and ``strategy_id`` is the only owner slot.
The migration seeds the row with no owner; no production path deletes or
get-or-creates it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, SmallInteger, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from trading_platform.db.base import Base, TimestampedModel

if TYPE_CHECKING:
    from trading_platform.db.models.strategy import Strategy

ACTIVE_PAPER_STRATEGY_SINGLETON_ID = 1


class ActivePaperStrategy(TimestampedModel, Base):
    """Database singleton naming the strategy that owns the paper account."""

    __tablename__ = "active_paper_strategy"
    __table_args__ = (
        # Naming convention renders this as ck_active_paper_strategy_singleton.
        CheckConstraint("id = 1", name="singleton"),
    )

    id: Mapped[int] = mapped_column(
        SmallInteger,
        primary_key=True,
        default=ACTIVE_PAPER_STRATEGY_SINGLETON_ID,
        server_default=text("1"),
    )
    strategy_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("strategies.id", ondelete="RESTRICT"),
        nullable=True,
    )
    since: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
    )
    set_by_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("strategy_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    reason: Mapped[str | None] = mapped_column(Text(), nullable=True)

    strategy: Mapped["Strategy | None"] = relationship(viewonly=True)
