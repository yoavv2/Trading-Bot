"""Phase 5 paper execution: add paper orders and run type."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0008_phase5_paper"
down_revision = "0007_phase4_risk"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    op.execute("ALTER TYPE strategy_run_type ADD VALUE IF NOT EXISTS 'paper_execution'")

    op.create_table(
        "paper_orders",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("strategy_run_id", sa.UUID(), nullable=False),
        sa.Column("source_risk_event_id", sa.UUID(), nullable=False),
        sa.Column("symbol_id", sa.UUID(), nullable=False),
        sa.Column("intended_session_date", sa.Date(), nullable=False),
        sa.Column("side", sa.String(length=8), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("order_type", sa.String(length=16), nullable=False),
        sa.Column("time_in_force", sa.String(length=16), nullable=False),
        sa.Column("client_order_id", sa.String(length=64), nullable=False),
        sa.Column("broker_order_id", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("broker_status", sa.String(length=32), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("broker_payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["strategy_run_id"],
            ["strategy_runs.id"],
            name=op.f("fk_paper_orders_strategy_run_id_strategy_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_risk_event_id"],
            ["risk_events.id"],
            name=op.f("fk_paper_orders_source_risk_event_id_risk_events"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["symbol_id"],
            ["symbols.id"],
            name=op.f("fk_paper_orders_symbol_id_symbols"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_paper_orders")),
        sa.UniqueConstraint("source_risk_event_id", name="uq_paper_orders_source_risk_event_id"),
        sa.UniqueConstraint("client_order_id", name="uq_paper_orders_client_order_id"),
        sa.UniqueConstraint("broker_order_id", name="uq_paper_orders_broker_order_id"),
    )
    op.create_index(
        "ix_paper_orders_strategy_run_id_status",
        "paper_orders",
        ["strategy_run_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_paper_orders_strategy_run_id_symbol_id",
        "paper_orders",
        ["strategy_run_id", "symbol_id"],
        unique=False,
    )

    op.alter_column("paper_orders", "broker_payload", server_default=None)


def downgrade() -> None:
    bind = op.get_bind()

    op.drop_index("ix_paper_orders_strategy_run_id_symbol_id", table_name="paper_orders")
    op.drop_index("ix_paper_orders_strategy_run_id_status", table_name="paper_orders")
    op.drop_table("paper_orders")

    op.execute("DELETE FROM strategy_runs WHERE run_type = 'paper_execution'")
    op.execute("ALTER TYPE strategy_run_type RENAME TO strategy_run_type_old")
    strategy_run_type = postgresql.ENUM(
        "dry_bootstrap",
        "backtest",
        "risk_evaluation",
        name="strategy_run_type",
        create_type=False,
    )
    strategy_run_type.create(bind, checkfirst=False)
    op.execute(
        """
        ALTER TABLE strategy_runs
        ALTER COLUMN run_type
        TYPE strategy_run_type
        USING run_type::text::strategy_run_type
        """
    )
    op.execute("DROP TYPE strategy_run_type_old")
