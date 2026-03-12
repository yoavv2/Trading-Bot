"""ORM model for persisted strategy metadata."""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import Enum, Index, JSON, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from trading_platform.db.base import Base, TimestampedModel

if TYPE_CHECKING:
    from trading_platform.db.models.strategy_run import StrategyRun


class StrategyStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"
    ARCHIVED = "archived"


def _enum_values(enum_cls: type[StrEnum]) -> list[str]:
    return [member.value for member in enum_cls]


class Strategy(TimestampedModel, Base):
    """Minimal persisted strategy catalog entry for Phase 1."""

    __tablename__ = "strategies"
    __table_args__ = (
        Index("ix_strategies_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    strategy_id: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False, default="v1")
    status: Mapped[StrategyStatus] = mapped_column(
        Enum(
            StrategyStatus,
            name="strategy_status",
            values_callable=_enum_values,
            validate_strings=True,
        ),
        nullable=False,
        default=StrategyStatus.ACTIVE,
    )
    description: Mapped[str | None] = mapped_column(Text(), nullable=True)
    config_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    universe_symbols: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    settings_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    runs: Mapped[list["StrategyRun"]] = relationship(
        back_populates="strategy",
        cascade="all, delete-orphan",
    )
