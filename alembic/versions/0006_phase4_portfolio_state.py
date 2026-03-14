"""Phase 4 foundation: live portfolio positions and account snapshots."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0006_phase4_port"
down_revision = "0005_phase3_btr"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "positions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("strategy_id", sa.UUID(), nullable=False),
        sa.Column("symbol_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("average_entry_price", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("cost_basis", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("opened_session_date", sa.Date(), nullable=True),
        sa.Column("closed_session_date", sa.Date(), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["strategy_id"],
            ["strategies.id"],
            name=op.f("fk_positions_strategy_id_strategies"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["symbol_id"],
            ["symbols.id"],
            name=op.f("fk_positions_symbol_id_symbols"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_positions")),
    )
    op.create_index("ix_positions_strategy_id_status", "positions", ["strategy_id", "status"], unique=False)
    op.create_index("ix_positions_symbol_id_status", "positions", ["symbol_id", "status"], unique=False)

    op.create_table(
        "account_snapshots",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("strategy_id", sa.UUID(), nullable=True),
        sa.Column("source_run_id", sa.UUID(), nullable=True),
        sa.Column("snapshot_source", sa.String(length=32), nullable=False),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cash", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("gross_exposure", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("total_equity", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("buying_power", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("open_positions", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_run_id"],
            ["strategy_runs.id"],
            name=op.f("fk_account_snapshots_source_run_id_strategy_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["strategy_id"],
            ["strategies.id"],
            name=op.f("fk_account_snapshots_strategy_id_strategies"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_account_snapshots")),
    )
    op.create_index("ix_account_snapshots_snapshot_at", "account_snapshots", ["snapshot_at"], unique=False)
    op.create_index(
        "ix_account_snapshots_strategy_id_snapshot_at",
        "account_snapshots",
        ["strategy_id", "snapshot_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_account_snapshots_strategy_id_snapshot_at", table_name="account_snapshots")
    op.drop_index("ix_account_snapshots_snapshot_at", table_name="account_snapshots")
    op.drop_table("account_snapshots")

    op.drop_index("ix_positions_symbol_id_status", table_name="positions")
    op.drop_index("ix_positions_strategy_id_status", table_name="positions")
    op.drop_table("positions")
