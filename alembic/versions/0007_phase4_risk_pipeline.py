"""Phase 4 execution gate: persisted risk events and run type."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0007_phase4_risk"
down_revision = "0006_phase4_port"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    op.execute("ALTER TYPE strategy_run_type ADD VALUE IF NOT EXISTS 'risk_evaluation'")

    op.create_table(
        "risk_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("strategy_run_id", sa.UUID(), nullable=False),
        sa.Column("symbol_id", sa.UUID(), nullable=False),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column("signal_direction", sa.String(length=16), nullable=False),
        sa.Column("signal_reason", sa.String(length=64), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("decision_code", sa.String(length=64), nullable=False),
        sa.Column("decision_reason", sa.Text(), nullable=False),
        sa.Column("reference_price", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("proposed_quantity", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("proposed_notional", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("risk_metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["strategy_run_id"],
            ["strategy_runs.id"],
            name=op.f("fk_risk_events_strategy_run_id_strategy_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["symbol_id"],
            ["symbols.id"],
            name=op.f("fk_risk_events_symbol_id_symbols"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_risk_events")),
        sa.UniqueConstraint(
            "strategy_run_id",
            "symbol_id",
            "session_date",
            "signal_direction",
            name="uq_risk_events_run_symbol_session_direction",
        ),
    )
    op.create_index(
        "ix_risk_events_strategy_run_id_session_date",
        "risk_events",
        ["strategy_run_id", "session_date"],
        unique=False,
    )
    op.create_index("ix_risk_events_decision_code", "risk_events", ["decision_code"], unique=False)

    op.alter_column("risk_events", "risk_metadata", server_default=None)


def downgrade() -> None:
    bind = op.get_bind()

    op.drop_index("ix_risk_events_decision_code", table_name="risk_events")
    op.drop_index("ix_risk_events_strategy_run_id_session_date", table_name="risk_events")
    op.drop_table("risk_events")

    op.execute("DELETE FROM strategy_runs WHERE run_type = 'risk_evaluation'")
    op.execute("ALTER TYPE strategy_run_type RENAME TO strategy_run_type_old")
    strategy_run_type = postgresql.ENUM(
        "dry_bootstrap",
        "backtest",
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
