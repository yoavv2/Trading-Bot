"""Signals product, Step 2 (docs/SIGNALS-PLAN.md): adjusted levels and kept session inputs.

Research databases only; the trading database stays at 0021.

* ``signal_log`` gains ``adjustment_factor``, ``effective_stop_price`` and
  ``effective_target_price``. The engine reads adjusted bars, and a dividend or a split
  after the signal session changes the price scale of the whole adjusted history. The
  levels a signal was issued with (``reference_price``, ``stop_price``, ``target_price``)
  are never changed. On every later event the engine derives the levels in the scale of
  that session from the original levels and the factor
  ``adjusted close of the signal session as read on the event session / reference_price``,
  and records the factor and the derived stop and target. Deriving from the original
  levels every time means an adjustment is never applied twice. Required on every new
  row (``NOT VALID`` check, so earlier rows keep ``NULL``).
* ``signal_session_inputs``: the bars a session was computed from, kept when the daily
  Job runs a book. Adjusted history is rewritten by every later download, so this is the
  only place the inputs of a past session can be read back; the replay uses it.
  Append-only, one row per (strategy version, asset, session).

Migrations 0022-0036 are unchanged.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0037_signal_inputs"
down_revision = "0036_signal_books"
branch_labels = None
depends_on = None


def _uuid() -> sa.types.TypeEngine:
    return sa.Uuid(as_uuid=True)


def upgrade() -> None:
    op.add_column("signal_log", sa.Column("adjustment_factor", sa.Numeric(precision=24, scale=12), nullable=True))
    op.add_column("signal_log", sa.Column("effective_stop_price", sa.Numeric(precision=20, scale=6), nullable=True))
    op.add_column("signal_log", sa.Column("effective_target_price", sa.Numeric(precision=20, scale=6), nullable=True))
    op.execute(
        """
        ALTER TABLE signal_log ADD CONSTRAINT ck_signal_log_effective_levels CHECK (
            adjustment_factor IS NOT NULL AND effective_stop_price IS NOT NULL
            AND effective_target_price IS NOT NULL
            AND adjustment_factor > 0
            AND effective_stop_price > 0 AND effective_stop_price < effective_target_price
            AND (adjustment_factor <> 1
                 OR (effective_stop_price = stop_price AND effective_target_price = target_price))
            AND (event_type <> 'issued' OR adjustment_factor = 1)
        ) NOT VALID
        """
    )

    op.create_table(
        "signal_session_inputs",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "strategy_version_id",
            _uuid(),
            sa.ForeignKey("strategy_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("asset", sa.String(32), nullable=False),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column("inputs", postgresql.JSONB(), nullable=False),
        sa.Column("inputs_sha256", sa.String(64), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "strategy_version_id", "asset", "session_date", name="uq_signal_session_inputs_version_asset_session"
        ),
        sa.CheckConstraint("inputs_sha256 ~ '^[0-9a-f]{64}$'", name="inputs_sha256_format"),
    )
    op.execute(
        """
        CREATE FUNCTION signal_session_inputs_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION USING ERRCODE = '23000', MESSAGE = 'signal_session_inputs is append-only';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER trg_signal_session_inputs_append_only BEFORE UPDATE OR DELETE ON signal_session_inputs "
        "FOR EACH ROW EXECUTE FUNCTION signal_session_inputs_append_only()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_signal_session_inputs_append_only ON signal_session_inputs")
    op.execute("DROP FUNCTION IF EXISTS signal_session_inputs_append_only()")
    op.drop_table("signal_session_inputs")
    op.execute("ALTER TABLE signal_log DROP CONSTRAINT ck_signal_log_effective_levels")
    for column in ("effective_target_price", "effective_stop_price", "adjustment_factor"):
        op.drop_column("signal_log", column)
