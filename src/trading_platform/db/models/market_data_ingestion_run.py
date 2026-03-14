"""ORM model for market-data ingestion run records."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, Enum, Index, Integer, JSON, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from trading_platform.db.base import Base, TimestampedModel


class MarketDataIngestionRun(TimestampedModel, Base):
    """Records a single market-data ingest operation with request metadata and outcome."""

    __tablename__ = "market_data_ingestion_runs"
    __table_args__ = (
        Index("ix_market_data_ingestion_runs_provider_status", "provider", "status"),
        Index("ix_market_data_ingestion_runs_from_date_to_date", "from_date", "to_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="polygon")
    from_date: Mapped[date] = mapped_column(Date, nullable=False)
    to_date: Mapped[date] = mapped_column(Date, nullable=False)
    adjusted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(
        Enum(
            "running",
            "succeeded",
            "failed",
            "partial",
            name="ingestion_run_status",
            create_constraint=True,
        ),
        nullable=False,
        default="running",
    )
    symbols_requested: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    symbols_failed: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    bars_upserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    trigger_source: Mapped[str] = mapped_column(String(64), nullable=False, default="cli")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
