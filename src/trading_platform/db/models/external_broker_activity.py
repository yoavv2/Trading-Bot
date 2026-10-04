"""ORM model for immutable, verified external broker activity (EXT-01, D-10, S-4).

One row is the verified broker snapshot of one external order and its fills, written
by the ``record-external-activity`` Job. Evidence only: there is no owner column and no
relationship to orders, fills, positions or runs; the only foreign key is the optional
originating Job. Rows are immutable (a database trigger rejects UPDATE and DELETE), so
there is no ``updated_at``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from trading_platform.db.base import Base


class ExternalActivityOriginTag(StrEnum):
    """Closed origin set preserved from the classification at record time."""

    EXTERNAL_FORMAT = "external_format"
    PLATFORM_FORMAT_UNVERIFIED = "platform_format_unverified"
    REPLACED_BY_SUCCESSOR = "replaced_by_successor"


_ORIGIN_VALUES_SQL = ", ".join(f"'{member.value}'" for member in ExternalActivityOriginTag)


class ExternalBrokerActivity(Base):
    """One verified, immutable external broker order snapshot (no owner reference)."""

    __tablename__ = "external_broker_activity"
    __table_args__ = (
        # Naming convention renders ck_external_broker_activity_<name>.
        CheckConstraint("side IN ('buy', 'sell')", name="side"),
        CheckConstraint(f"origin_tag IN ({_ORIGIN_VALUES_SQL})", name="origin_tag"),
        CheckConstraint("btrim(reason) <> ''", name="reason_not_blank"),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="content_hash_format"),
        UniqueConstraint(
            "broker_order_id", "content_hash", name="uq_external_broker_activity_order_hash"
        ),
        Index(
            "ix_external_broker_activity_broker_order_id_created_at",
            "broker_order_id",
            "created_at",
        ),
        Index("ix_external_broker_activity_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("jobs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    broker_order_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    client_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    qty: Mapped[Decimal | None] = mapped_column(Numeric(24, 6), nullable=True)
    filled_qty: Mapped[Decimal] = mapped_column(Numeric(24, 6), nullable=False)
    filled_avg_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 6), nullable=True)
    broker_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    successor_broker_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    origin_tag: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(Text(), nullable=False)
    order_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    fills: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
