"""ORM model for durable global system-level controls (kill switch)."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from trading_platform.db.base import Base, TimestampedModel

if TYPE_CHECKING:
    from trading_platform.db.models.strategy_run import StrategyRun


GLOBAL_KILL_SWITCH_NAME = "global_kill_switch"


class KillSwitchState(StrEnum):
    """Closed state enum for the global submission kill switch."""

    ARMED = "armed"
    TRIPPED = "tripped"


def _enum_values(enum_cls: type[StrEnum]) -> list[str]:
    return [member.value for member in enum_cls]


class SystemControl(TimestampedModel, Base):
    """Single-row persisted record for a named global platform control."""

    __tablename__ = "system_controls"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    state: Mapped[KillSwitchState] = mapped_column(
        Enum(
            KillSwitchState,
            name="kill_switch_state",
            values_callable=_enum_values,
            validate_strings=True,
        ),
        nullable=False,
        default=KillSwitchState.ARMED,
    )
    last_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_change_actor: Mapped[str] = mapped_column(String(120), nullable=False)
    last_change_reason: Mapped[str | None] = mapped_column(Text(), nullable=True)
    last_change_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("strategy_runs.id", ondelete="SET NULL"),
        nullable=True,
    )

    last_change_run: Mapped["StrategyRun | None"] = relationship(foreign_keys=[last_change_run_id])
