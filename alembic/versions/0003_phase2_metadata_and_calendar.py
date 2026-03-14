"""Phase 2 plan 02: symbol metadata enrichment and market_sessions table."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0003_phase2_metacal"
down_revision = "0002_phase2_mdf"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Enrich symbols table with provider metadata columns
    op.add_column("symbols", sa.Column("list_date", sa.Date(), nullable=True))
    op.add_column("symbols", sa.Column("currency_name", sa.String(length=32), nullable=True))
    op.add_column("symbols", sa.Column("cik", sa.String(length=16), nullable=True))
    op.add_column("symbols", sa.Column("composite_figi", sa.String(length=32), nullable=True))
    op.add_column("symbols", sa.Column("share_class_figi", sa.String(length=32), nullable=True))
    op.add_column("symbols", sa.Column("metadata_provider", sa.String(length=64), nullable=True))

    # Create market_sessions table for persisted XNYS session context
    op.create_table(
        "market_sessions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("exchange", sa.String(length=16), nullable=False),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column("market_open", sa.DateTime(timezone=True), nullable=True),
        sa.Column("market_close", sa.DateTime(timezone=True), nullable=True),
        sa.Column("early_close", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_market_sessions")),
        sa.UniqueConstraint(
            "exchange",
            "session_date",
            name="uq_market_sessions_exchange_date",
        ),
    )
    op.create_index(
        op.f("ix_market_sessions_exchange_date"),
        "market_sessions",
        ["exchange", "session_date"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_market_sessions_exchange_date"), table_name="market_sessions")
    op.drop_table("market_sessions")

    op.drop_column("symbols", "metadata_provider")
    op.drop_column("symbols", "share_class_figi")
    op.drop_column("symbols", "composite_figi")
    op.drop_column("symbols", "cik")
    op.drop_column("symbols", "currency_name")
    op.drop_column("symbols", "list_date")
