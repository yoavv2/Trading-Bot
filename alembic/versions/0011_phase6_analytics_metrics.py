"""Phase 6 analytics: richer persisted backtest metrics."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0011_phase6_analytics"
down_revision = "0010_phase5_recon"
branch_labels = None
depends_on = None


def upgrade() -> None:
    metric_columns = (
        "cagr_pct",
        "sharpe_ratio",
        "sortino_ratio",
        "expectancy",
        "turnover_pct",
        "best_trade",
        "worst_trade",
    )
    for column_name in metric_columns:
        op.add_column(
            "backtest_metrics",
            sa.Column(column_name, sa.Numeric(precision=20, scale=6), nullable=False, server_default="0"),
        )
        op.alter_column("backtest_metrics", column_name, server_default=None)


def downgrade() -> None:
    for column_name in (
        "worst_trade",
        "best_trade",
        "turnover_pct",
        "expectancy",
        "sortino_ratio",
        "sharpe_ratio",
        "cagr_pct",
    ):
        op.drop_column("backtest_metrics", column_name)
