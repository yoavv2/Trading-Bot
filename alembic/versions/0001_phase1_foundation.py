"""Initial Phase 1 persistence foundation."""

from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0001_phase1_foundation"
down_revision = None
branch_labels = None
depends_on = None

strategy_status = postgresql.ENUM(
    "active",
    "disabled",
    "archived",
    name="strategy_status",
    create_type=False,
)
strategy_run_status = postgresql.ENUM(
    "pending",
    "running",
    "succeeded",
    "failed",
    name="strategy_run_status",
    create_type=False,
)
strategy_run_type = postgresql.ENUM(
    "dry_bootstrap",
    name="strategy_run_type",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    for enum_type in (strategy_status, strategy_run_status, strategy_run_type):
        enum_type.create(bind, checkfirst=True)

    op.create_table(
        "strategies",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("strategy_id", sa.String(length=120), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("status", strategy_status, nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("config_reference", sa.String(length=255), nullable=False),
        sa.Column("universe_symbols", sa.JSON(), nullable=False),
        sa.Column("settings_snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_strategies")),
        sa.UniqueConstraint("strategy_id", name=op.f("uq_strategies_strategy_id")),
    )
    op.create_index(op.f("ix_strategies_status"), "strategies", ["status"], unique=False)

    op.create_table(
        "strategy_runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("strategy_id", sa.UUID(), nullable=False),
        sa.Column("run_type", strategy_run_type, nullable=False),
        sa.Column("status", strategy_run_status, nullable=False),
        sa.Column("trigger_source", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_summary", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["strategy_id"],
            ["strategies.id"],
            name=op.f("fk_strategy_runs_strategy_id_strategies"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_strategy_runs")),
    )
    op.create_index(
        op.f("ix_strategy_runs_strategy_id_status"),
        "strategy_runs",
        ["strategy_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_strategy_runs_strategy_id_status"), table_name="strategy_runs")
    op.drop_table("strategy_runs")
    op.drop_index(op.f("ix_strategies_status"), table_name="strategies")
    op.drop_table("strategies")

    bind = op.get_bind()
    for enum_type in (
        strategy_run_type,
        strategy_run_status,
        strategy_status,
    ):
        enum_type.drop(bind, checkfirst=True)
