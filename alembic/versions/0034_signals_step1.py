"""Signals product, Step 1 (docs/SIGNALS-PLAN.md): stop/target results and the signal log.

Research databases only; the trading database stays at 0021.

* ``backtest_trades`` gains the levels of the signal that opened the trade
  (``reference_price``, ``stop_price``, ``target_price``), the entry and exit prices the
  rules gave before costs (``rule_entry_price``, ``rule_exit_price``), the closed
  ``exit_reason`` and the ``intrabar_order_unknown`` flag. All nullable: rows written by a v1
  specification or by the trading path keep ``NULL`` in every one of them.
* ``signal_log``: the internal, append-only log the daily signal engine writes. One row
  per event of a signal; a signal is identified by (strategy version, asset, the session
  that produced it). A trigger refuses UPDATE and DELETE, the same pattern as
  ``strategy_versions`` (0031).
* ``signal_session_runs``: one append-only row per (strategy version, asset, session)
  the daily engine has processed. It makes a re-run of a session write nothing and lets
  the engine refuse to skip a session.

Migrations 0022-0033 are unchanged.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0034_signals_step1"
down_revision = "0033_research_assistant"
branch_labels = None
depends_on = None

EXIT_REASONS = ("stop", "target", "rule")
EVENT_TYPES = (
    "issued",
    "expired",
    "entry_filled",
    "rule_exit_signalled",
    "closed_stop",
    "closed_target",
    "closed_rule",
)
EXPIRY_REASONS = (
    "expired_open_at_or_below_stop",
    "expired_open_at_or_above_target",
    "expired_reward_risk_below_minimum",
)
APPEND_ONLY_TABLES = ("signal_log", "signal_session_runs")


def _uuid() -> sa.types.TypeEngine:
    return sa.Uuid(as_uuid=True)


def _price() -> sa.types.TypeEngine:
    return sa.Numeric(precision=20, scale=6)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _guard_names(table: str) -> tuple[str, str]:
    return f"{table}_append_only", f"trg_{table}_append_only"


def upgrade() -> None:
    op.add_column("backtest_trades", sa.Column("reference_price", _price(), nullable=True))
    op.add_column("backtest_trades", sa.Column("stop_price", _price(), nullable=True))
    op.add_column("backtest_trades", sa.Column("target_price", _price(), nullable=True))
    op.add_column("backtest_trades", sa.Column("rule_entry_price", _price(), nullable=True))
    op.add_column("backtest_trades", sa.Column("rule_exit_price", _price(), nullable=True))
    op.add_column("backtest_trades", sa.Column("exit_reason", sa.String(16), nullable=True))
    op.add_column("backtest_trades", sa.Column("intrabar_order_unknown", sa.Boolean(), nullable=True))
    op.create_check_constraint(
        "ck_backtest_trades_exit_reason",
        "backtest_trades",
        f"exit_reason IS NULL OR {_in('exit_reason', EXIT_REASONS)}",
    )
    op.create_check_constraint(
        "ck_backtest_trades_levels_ordered",
        "backtest_trades",
        "(reference_price IS NULL AND stop_price IS NULL AND target_price IS NULL) "
        "OR (stop_price > 0 AND stop_price < reference_price AND reference_price < target_price)",
    )

    op.create_table(
        "signal_log",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "strategy_version_id",
            _uuid(),
            sa.ForeignKey("strategy_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("asset", sa.String(32), nullable=False),
        sa.Column("signal_session", sa.Date(), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("event_session", sa.Date(), nullable=False),
        sa.Column("spec_sha256", sa.String(64), nullable=False),
        sa.Column("reference_price", _price(), nullable=False),
        sa.Column("stop_price", _price(), nullable=False),
        sa.Column("target_price", _price(), nullable=False),
        sa.Column("price", _price(), nullable=True),
        sa.Column("expiry_reason", sa.String(48), nullable=True),
        sa.Column("intrabar_order_unknown", sa.Boolean(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "strategy_version_id",
            "asset",
            "signal_session",
            "event_type",
            name="uq_signal_log_signal_event",
        ),
        sa.CheckConstraint(_in("event_type", EVENT_TYPES), name="ck_signal_log_event_type"),
        sa.CheckConstraint(
            "stop_price > 0 AND stop_price < reference_price AND reference_price < target_price",
            name="ck_signal_log_levels_ordered",
        ),
        sa.CheckConstraint("event_session >= signal_session", name="ck_signal_log_event_not_before_signal"),
        sa.CheckConstraint(
            "(event_type = 'issued') = (event_session = signal_session)",
            name="ck_signal_log_only_issue_on_signal_session",
        ),
        sa.CheckConstraint(
            f"(event_type = 'expired' AND expiry_reason IS NOT NULL AND {_in('expiry_reason', EXPIRY_REASONS)}) "
            "OR (event_type <> 'expired' AND expiry_reason IS NULL)",
            name="ck_signal_log_expiry_reason",
        ),
        sa.CheckConstraint(
            "(event_type IN ('entry_filled', 'closed_stop', 'closed_target', 'closed_rule')) = (price IS NOT NULL)",
            name="ck_signal_log_price_on_fills",
        ),
        sa.CheckConstraint(
            "(event_type IN ('closed_stop', 'closed_target', 'closed_rule')) = (intrabar_order_unknown IS NOT NULL)",
            name="ck_signal_log_flag_on_close",
        ),
        sa.CheckConstraint(
            "intrabar_order_unknown IS NOT TRUE OR event_type = 'closed_stop'",
            name="ck_signal_log_flag_only_on_stop",
        ),
    )
    op.create_index(
        "ix_signal_log_version_asset_event_session",
        "signal_log",
        ["strategy_version_id", "asset", "event_session"],
    )

    op.create_table(
        "signal_session_runs",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "strategy_version_id",
            _uuid(),
            sa.ForeignKey("strategy_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("asset", sa.String(32), nullable=False),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column("spec_sha256", sa.String(64), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "strategy_version_id", "asset", "session_date", name="uq_signal_session_runs_version_asset_session"
        ),
    )

    for table in APPEND_ONLY_TABLES:
        function, trigger = _guard_names(table)
        op.execute(
            f"""
            CREATE FUNCTION {function}() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION USING
                    ERRCODE = '23000',
                    MESSAGE = '{table} is append-only';
            END;
            $$ LANGUAGE plpgsql;
            """
        )
        op.execute(
            f"""
            CREATE TRIGGER {trigger}
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION {function}();
            """
        )


def downgrade() -> None:
    for table in APPEND_ONLY_TABLES:
        function, trigger = _guard_names(table)
        op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
        op.execute(f"DROP FUNCTION IF EXISTS {function}()")
    op.drop_table("signal_session_runs")
    op.drop_index("ix_signal_log_version_asset_event_session", table_name="signal_log")
    op.drop_table("signal_log")
    op.drop_constraint("ck_backtest_trades_levels_ordered", "backtest_trades", type_="check")
    op.drop_constraint("ck_backtest_trades_exit_reason", "backtest_trades", type_="check")
    for column in ("intrabar_order_unknown", "exit_reason", "rule_exit_price", "rule_entry_price", "target_price", "stop_price", "reference_price"):
        op.drop_column("backtest_trades", column)
