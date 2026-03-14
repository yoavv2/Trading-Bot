"""Phase 2 market-data foundation: symbols, daily_bars, market_data_ingestion_runs."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0002_phase2_mdf"
down_revision = "0001_phase1_foundation"
branch_labels = None
depends_on = None

ingestion_run_status = postgresql.ENUM(
    "running",
    "succeeded",
    "failed",
    "partial",
    name="ingestion_run_status",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    ingestion_run_status.create(bind, checkfirst=True)

    op.create_table(
        "symbols",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("ticker", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("market", sa.String(length=64), nullable=True),
        sa.Column("locale", sa.String(length=16), nullable=True),
        sa.Column("primary_exchange", sa.String(length=32), nullable=True),
        sa.Column("symbol_type", sa.String(length=32), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_symbols")),
        sa.UniqueConstraint("ticker", name=op.f("uq_symbols_ticker")),
    )
    op.create_index(op.f("ix_symbols_ticker"), "symbols", ["ticker"], unique=False)
    op.create_index(op.f("ix_symbols_active"), "symbols", ["active"], unique=False)

    op.create_table(
        "daily_bars",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("symbol_id", sa.UUID(), nullable=False),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column("open", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("high", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("low", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("close", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("volume", sa.Integer(), nullable=False),
        sa.Column("vwap", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("trade_count", sa.Integer(), nullable=True),
        sa.Column("adjusted", sa.Boolean(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("provider_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["symbol_id"],
            ["symbols.id"],
            name=op.f("fk_daily_bars_symbol_id_symbols"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_daily_bars")),
        sa.UniqueConstraint(
            "symbol_id",
            "session_date",
            "adjusted",
            "provider",
            name="uq_daily_bars_symbol_session_adjusted_provider",
        ),
    )
    op.create_index(
        op.f("ix_daily_bars_symbol_id_session_date"),
        "daily_bars",
        ["symbol_id", "session_date"],
        unique=False,
    )

    op.create_table(
        "market_data_ingestion_runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("from_date", sa.Date(), nullable=False),
        sa.Column("to_date", sa.Date(), nullable=False),
        sa.Column("adjusted", sa.Boolean(), nullable=False),
        sa.Column("status", ingestion_run_status, nullable=False),
        sa.Column("symbols_requested", sa.JSON(), nullable=False),
        sa.Column("symbols_failed", sa.JSON(), nullable=False),
        sa.Column("bars_upserted", sa.Integer(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("trigger_source", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("request_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_market_data_ingestion_runs")),
    )
    op.create_index(
        op.f("ix_market_data_ingestion_runs_provider_status"),
        "market_data_ingestion_runs",
        ["provider", "status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_market_data_ingestion_runs_from_date_to_date"),
        "market_data_ingestion_runs",
        ["from_date", "to_date"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_market_data_ingestion_runs_from_date_to_date"),
        table_name="market_data_ingestion_runs",
    )
    op.drop_index(
        op.f("ix_market_data_ingestion_runs_provider_status"),
        table_name="market_data_ingestion_runs",
    )
    op.drop_table("market_data_ingestion_runs")

    op.drop_index(op.f("ix_daily_bars_symbol_id_session_date"), table_name="daily_bars")
    op.drop_table("daily_bars")

    op.drop_index(op.f("ix_symbols_active"), table_name="symbols")
    op.drop_index(op.f("ix_symbols_ticker"), table_name="symbols")
    op.drop_table("symbols")

    bind = op.get_bind()
    ingestion_run_status.drop(bind, checkfirst=True)
