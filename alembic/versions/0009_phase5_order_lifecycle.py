"""Phase 5 lifecycle sync: paper fills and broker-sync timestamps."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0009_phase5_order"
down_revision = "0008_phase5_paper"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("paper_orders", sa.Column("filled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("paper_orders", sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("paper_orders", sa.Column("last_broker_update_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("paper_orders", sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "paper_fills",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("paper_order_id", sa.UUID(), nullable=False),
        sa.Column("symbol_id", sa.UUID(), nullable=False),
        sa.Column("broker_fill_id", sa.String(length=64), nullable=False),
        sa.Column("broker_order_id", sa.String(length=64), nullable=False),
        sa.Column("side", sa.String(length=8), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("price", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("filled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("broker_payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["paper_order_id"],
            ["paper_orders.id"],
            name=op.f("fk_paper_fills_paper_order_id_paper_orders"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["symbol_id"],
            ["symbols.id"],
            name=op.f("fk_paper_fills_symbol_id_symbols"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_paper_fills")),
        sa.UniqueConstraint("broker_fill_id", name="uq_paper_fills_broker_fill_id"),
    )
    op.create_index(
        "ix_paper_fills_paper_order_id_filled_at",
        "paper_fills",
        ["paper_order_id", "filled_at"],
        unique=False,
    )
    op.create_index(
        "ix_paper_fills_symbol_id_filled_at",
        "paper_fills",
        ["symbol_id", "filled_at"],
        unique=False,
    )

    op.alter_column("paper_fills", "broker_payload", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_paper_fills_symbol_id_filled_at", table_name="paper_fills")
    op.drop_index("ix_paper_fills_paper_order_id_filled_at", table_name="paper_fills")
    op.drop_table("paper_fills")

    op.drop_column("paper_orders", "last_synced_at")
    op.drop_column("paper_orders", "last_broker_update_at")
    op.drop_column("paper_orders", "canceled_at")
    op.drop_column("paper_orders", "filled_at")
