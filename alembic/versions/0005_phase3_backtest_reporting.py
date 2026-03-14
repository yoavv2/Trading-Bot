"""Phase 3 reporting: persisted run-level backtest metrics."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0005_phase3_btr"
down_revision = "0004_phase3_btf"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "backtest_metrics",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("strategy_run_id", sa.UUID(), nullable=False),
        sa.Column("total_return_pct", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("max_drawdown_pct", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("trade_count", sa.Integer(), nullable=False),
        sa.Column("win_rate_pct", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("average_win", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("average_loss", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("profit_factor", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("exposure_pct", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column(
            "average_holding_period_sessions",
            sa.Numeric(precision=20, scale=6),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["strategy_run_id"],
            ["strategy_runs.id"],
            name=op.f("fk_backtest_metrics_strategy_run_id_strategy_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_backtest_metrics")),
        sa.UniqueConstraint(
            "strategy_run_id",
            name="uq_backtest_metrics_strategy_run_id",
        ),
    )
    op.create_index(
        "ix_backtest_metrics_strategy_run_id",
        "backtest_metrics",
        ["strategy_run_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_backtest_metrics_strategy_run_id", table_name="backtest_metrics")
    op.drop_table("backtest_metrics")
