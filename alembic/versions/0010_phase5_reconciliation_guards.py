"""Phase 5 reconciliation: durable execution events and guardrail counters."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0010_phase5_recon"
down_revision = "0009_phase5_order"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    op.execute("ALTER TYPE strategy_run_type ADD VALUE IF NOT EXISTS 'reconciliation'")

    op.add_column(
        "paper_orders",
        sa.Column("submission_attempt_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "paper_orders",
        sa.Column("sync_failure_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("paper_orders", sa.Column("last_submission_attempt_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("paper_orders", sa.Column("last_sync_failure_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("paper_orders", sa.Column("last_submission_error", sa.Text(), nullable=True))
    op.add_column("paper_orders", sa.Column("last_sync_error", sa.Text(), nullable=True))

    op.create_table(
        "execution_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("strategy_run_id", sa.UUID(), nullable=False),
        sa.Column("paper_order_id", sa.UUID(), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("blocks_execution", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("event_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["strategy_run_id"],
            ["strategy_runs.id"],
            name=op.f("fk_execution_events_strategy_run_id_strategy_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["paper_order_id"],
            ["paper_orders.id"],
            name=op.f("fk_execution_events_paper_order_id_paper_orders"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_execution_events")),
    )
    op.create_index(
        "ix_execution_events_strategy_run_id_event_at",
        "execution_events",
        ["strategy_run_id", "event_at"],
        unique=False,
    )
    op.create_index(
        "ix_execution_events_blocks_execution_event_at",
        "execution_events",
        ["blocks_execution", "event_at"],
        unique=False,
    )
    op.create_index(
        "ix_execution_events_paper_order_id_event_type",
        "execution_events",
        ["paper_order_id", "event_type"],
        unique=False,
    )

    op.alter_column("paper_orders", "submission_attempt_count", server_default=None)
    op.alter_column("paper_orders", "sync_failure_count", server_default=None)
    op.alter_column("execution_events", "details", server_default=None)
    op.alter_column("execution_events", "blocks_execution", server_default=None)


def downgrade() -> None:
    bind = op.get_bind()

    op.drop_index("ix_execution_events_paper_order_id_event_type", table_name="execution_events")
    op.drop_index("ix_execution_events_blocks_execution_event_at", table_name="execution_events")
    op.drop_index("ix_execution_events_strategy_run_id_event_at", table_name="execution_events")
    op.drop_table("execution_events")

    op.drop_column("paper_orders", "last_sync_error")
    op.drop_column("paper_orders", "last_submission_error")
    op.drop_column("paper_orders", "last_sync_failure_at")
    op.drop_column("paper_orders", "last_submission_attempt_at")
    op.drop_column("paper_orders", "sync_failure_count")
    op.drop_column("paper_orders", "submission_attempt_count")

    op.execute("DELETE FROM strategy_runs WHERE run_type = 'reconciliation'")
    op.execute("ALTER TYPE strategy_run_type RENAME TO strategy_run_type_old")
    strategy_run_type = postgresql.ENUM(
        "dry_bootstrap",
        "backtest",
        "risk_evaluation",
        "paper_execution",
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
