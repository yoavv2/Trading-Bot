"""Phase 3 foundation: typed backtest runs and persisted backtest artifacts."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0004_phase3_btf"
down_revision = "0003_phase2_metacal"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    op.execute("ALTER TYPE strategy_run_type ADD VALUE IF NOT EXISTS 'backtest'")

    op.add_column(
        "strategy_runs",
        sa.Column(
            "parameters_snapshot",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
    )

    op.create_table(
        "backtest_signals",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("strategy_run_id", sa.UUID(), nullable=False),
        sa.Column("symbol_id", sa.UUID(), nullable=False),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.Column("close", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("sma_short", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("sma_long", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("bars_available", sa.Integer(), nullable=False),
        sa.Column("signal_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["strategy_run_id"],
            ["strategy_runs.id"],
            name=op.f("fk_backtest_signals_strategy_run_id_strategy_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["symbol_id"],
            ["symbols.id"],
            name=op.f("fk_backtest_signals_symbol_id_symbols"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_backtest_signals")),
        sa.UniqueConstraint(
            "strategy_run_id",
            "symbol_id",
            "session_date",
            name="uq_backtest_signals_run_symbol_session",
        ),
    )
    op.create_index(
        "ix_backtest_signals_strategy_run_id_session_date",
        "backtest_signals",
        ["strategy_run_id", "session_date"],
        unique=False,
    )

    op.create_table(
        "backtest_trades",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("strategy_run_id", sa.UUID(), nullable=False),
        sa.Column("symbol_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("entry_signal_session", sa.Date(), nullable=False),
        sa.Column("entry_fill_session", sa.Date(), nullable=False),
        sa.Column("entry_price", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column(
            "entry_commission",
            sa.Numeric(precision=20, scale=6),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "entry_slippage",
            sa.Numeric(precision=20, scale=6),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("exit_signal_session", sa.Date(), nullable=True),
        sa.Column("exit_fill_session", sa.Date(), nullable=True),
        sa.Column("exit_price", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column(
            "exit_commission",
            sa.Numeric(precision=20, scale=6),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "exit_slippage",
            sa.Numeric(precision=20, scale=6),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("realized_pnl", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("net_pnl", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("holding_period_sessions", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["strategy_run_id"],
            ["strategy_runs.id"],
            name=op.f("fk_backtest_trades_strategy_run_id_strategy_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["symbol_id"],
            ["symbols.id"],
            name=op.f("fk_backtest_trades_symbol_id_symbols"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_backtest_trades")),
    )
    op.create_index(
        "ix_backtest_trades_strategy_run_id_status",
        "backtest_trades",
        ["strategy_run_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_backtest_trades_strategy_run_id_symbol_id",
        "backtest_trades",
        ["strategy_run_id", "symbol_id"],
        unique=False,
    )

    op.create_table(
        "backtest_equity_snapshots",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("strategy_run_id", sa.UUID(), nullable=False),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column("cash", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("gross_exposure", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("total_equity", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("realized_pnl", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("unrealized_pnl", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("open_positions", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["strategy_run_id"],
            ["strategy_runs.id"],
            name=op.f("fk_backtest_equity_snapshots_strategy_run_id_strategy_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_backtest_equity_snapshots")),
        sa.UniqueConstraint(
            "strategy_run_id",
            "session_date",
            name="uq_backtest_equity_snapshots_run_session",
        ),
    )
    op.create_index(
        "ix_backtest_equity_snapshots_strategy_run_id_session_date",
        "backtest_equity_snapshots",
        ["strategy_run_id", "session_date"],
        unique=False,
    )

    # Remove the temporary server default; ORM default handles new rows.
    op.alter_column("strategy_runs", "parameters_snapshot", server_default=None)


def downgrade() -> None:
    op.drop_index(
        "ix_backtest_equity_snapshots_strategy_run_id_session_date",
        table_name="backtest_equity_snapshots",
    )
    op.drop_table("backtest_equity_snapshots")

    op.drop_index("ix_backtest_trades_strategy_run_id_symbol_id", table_name="backtest_trades")
    op.drop_index("ix_backtest_trades_strategy_run_id_status", table_name="backtest_trades")
    op.drop_table("backtest_trades")

    op.drop_index(
        "ix_backtest_signals_strategy_run_id_session_date",
        table_name="backtest_signals",
    )
    op.drop_table("backtest_signals")

    op.drop_column("strategy_runs", "parameters_snapshot")

    # Revert the enum to the Phase 1 shape.
    op.execute("DELETE FROM strategy_runs WHERE run_type = 'backtest'")
    op.execute("ALTER TYPE strategy_run_type RENAME TO strategy_run_type_old")
    strategy_run_type = postgresql.ENUM(
        "dry_bootstrap",
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
