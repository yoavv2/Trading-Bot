"""Signals product, Step 2 (docs/SIGNALS-PLAN.md): the hardened daily signal engine.

Research databases only; the trading database stays at 0021.

* ``signal_log`` and ``signal_session_runs`` gain ``input_data_digest``: the SHA-256 of
  the market-data reads the session was computed from. Required on every new row (a
  ``NOT VALID`` check, so rows written before this migration keep ``NULL``).
* ``signal_log`` gets a transition guard. A signal's events must follow
  ``issued -> expired`` or ``issued -> entry_filled -> [rule_exit_signalled] ->
  closed_stop | closed_target | closed_rule``; every event repeats the levels of the
  ``issued`` event unchanged; a signal is issued only when no other signal of the same
  (strategy version, asset) is still active, and only after all of them.
* ``signal_session_runs`` gets an order guard: a session is recorded only after every
  session already recorded for the same (strategy version, asset).
* ``signal_session_skips``: one append-only row per (strategy version, asset, session,
  reason) for a session the engine could not process. The session stays unprocessed and
  blocks the sessions after it until its data arrives.

Migrations 0022-0034 are unchanged.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0035_signals_step2"
down_revision = "0034_signals_step1"
branch_labels = None
depends_on = None

SKIP_REASONS = ("missing_bar", "integrity_failed")
DIGEST_TABLES = ("signal_log", "signal_session_runs")


def _uuid() -> sa.types.TypeEngine:
    return sa.Uuid(as_uuid=True)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


TRANSITION_GUARD = """
CREATE FUNCTION signal_log_transition_guard() RETURNS trigger AS $$
DECLARE
    issued signal_log%ROWTYPE;
    has_entry boolean;
    has_terminal boolean;
    last_event date;
    problem text := NULL;
BEGIN
    -- One writer at a time per (strategy version, asset): the checks below read rows
    -- that a concurrent transaction could otherwise add unseen.
    PERFORM pg_advisory_xact_lock(hashtextextended(NEW.strategy_version_id::text || ':' || NEW.asset, 35));

    SELECT * INTO issued FROM signal_log
     WHERE strategy_version_id = NEW.strategy_version_id AND asset = NEW.asset
       AND signal_session = NEW.signal_session AND event_type = 'issued';
    SELECT coalesce(bool_or(event_type = 'entry_filled'), false),
           coalesce(bool_or(event_type IN ('expired', 'closed_stop', 'closed_target', 'closed_rule')), false),
           max(event_session)
      INTO has_entry, has_terminal, last_event
      FROM signal_log
     WHERE strategy_version_id = NEW.strategy_version_id AND asset = NEW.asset
       AND signal_session = NEW.signal_session;

    IF NEW.event_type = 'issued' THEN
        IF issued.id IS NOT NULL THEN
            problem := 'the signal is already issued';
        ELSIF EXISTS (
            SELECT 1 FROM signal_log other
             WHERE other.strategy_version_id = NEW.strategy_version_id AND other.asset = NEW.asset
               AND other.signal_session >= NEW.signal_session
        ) THEN
            problem := 'a later or equal signal exists for this version and asset';
        ELSIF EXISTS (
            SELECT 1 FROM signal_log other
             WHERE other.strategy_version_id = NEW.strategy_version_id AND other.asset = NEW.asset
               AND other.event_type = 'issued'
               AND NOT EXISTS (
                   SELECT 1 FROM signal_log done
                    WHERE done.strategy_version_id = other.strategy_version_id AND done.asset = other.asset
                      AND done.signal_session = other.signal_session
                      AND done.event_type IN ('expired', 'closed_stop', 'closed_target', 'closed_rule')
               )
        ) THEN
            problem := 'another signal of this version and asset is still active';
        END IF;
    ELSIF issued.id IS NULL THEN
        problem := 'the signal was never issued';
    ELSIF has_terminal THEN
        problem := 'the signal has already expired or closed';
    ELSIF NEW.event_type IN ('expired', 'entry_filled') AND has_entry THEN
        problem := 'the signal has already been entered';
    ELSIF NEW.event_type NOT IN ('expired', 'entry_filled') AND NOT has_entry THEN
        problem := 'the signal has not been entered';
    ELSIF NEW.event_session < last_event THEN
        problem := 'the event is before an earlier event of the signal';
    ELSIF NEW.reference_price <> issued.reference_price OR NEW.stop_price <> issued.stop_price
          OR NEW.target_price <> issued.target_price OR NEW.spec_sha256 <> issued.spec_sha256 THEN
        problem := 'the levels or the specification differ from the issued signal';
    END IF;

    IF problem IS NOT NULL THEN
        RAISE EXCEPTION USING
            ERRCODE = '23000',
            MESSAGE = 'signal_log: illegal transition to ' || NEW.event_type || ': ' || problem;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

SESSION_ORDER_GUARD = """
CREATE FUNCTION signal_session_runs_order_guard() RETURNS trigger AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended(NEW.strategy_version_id::text || ':' || NEW.asset, 35));
    IF EXISTS (
        SELECT 1 FROM signal_session_runs
         WHERE strategy_version_id = NEW.strategy_version_id AND asset = NEW.asset
           AND session_date >= NEW.session_date
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23000',
            MESSAGE = 'signal_session_runs: session is not after the last processed session';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    for table in DIGEST_TABLES:
        op.add_column(table, sa.Column("input_data_digest", sa.String(64), nullable=True))
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT ck_{table}_input_data_digest "
            "CHECK (input_data_digest IS NOT NULL AND input_data_digest ~ '^[0-9a-f]{64}$') NOT VALID"
        )

    op.execute(TRANSITION_GUARD)
    op.execute(
        "CREATE TRIGGER trg_signal_log_transition_guard BEFORE INSERT ON signal_log "
        "FOR EACH ROW EXECUTE FUNCTION signal_log_transition_guard()"
    )
    op.execute(SESSION_ORDER_GUARD)
    op.execute(
        "CREATE TRIGGER trg_signal_session_runs_order_guard BEFORE INSERT ON signal_session_runs "
        "FOR EACH ROW EXECUTE FUNCTION signal_session_runs_order_guard()"
    )

    op.create_table(
        "signal_session_skips",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "strategy_version_id",
            _uuid(),
            sa.ForeignKey("strategy_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("asset", sa.String(32), nullable=False),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column("reason", sa.String(32), nullable=False),
        sa.Column("detail", sa.String(64), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "strategy_version_id",
            "asset",
            "session_date",
            "reason",
            name="uq_signal_session_skips_version_asset_session_reason",
        ),
        sa.CheckConstraint(_in("reason", SKIP_REASONS), name="reason"),
        sa.CheckConstraint(
            "(reason = 'integrity_failed') = (detail IS NOT NULL)",
            name="detail_on_integrity",
        ),
    )
    op.execute(
        """
        CREATE FUNCTION signal_session_skips_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION USING
                ERRCODE = '23000',
                MESSAGE = 'signal_session_skips is append-only';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER trg_signal_session_skips_append_only BEFORE UPDATE OR DELETE ON signal_session_skips "
        "FOR EACH ROW EXECUTE FUNCTION signal_session_skips_append_only()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_signal_session_skips_append_only ON signal_session_skips")
    op.execute("DROP FUNCTION IF EXISTS signal_session_skips_append_only()")
    op.drop_table("signal_session_skips")
    op.execute("DROP TRIGGER IF EXISTS trg_signal_session_runs_order_guard ON signal_session_runs")
    op.execute("DROP FUNCTION IF EXISTS signal_session_runs_order_guard()")
    op.execute("DROP TRIGGER IF EXISTS trg_signal_log_transition_guard ON signal_log")
    op.execute("DROP FUNCTION IF EXISTS signal_log_transition_guard()")
    for table in DIGEST_TABLES:
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT ck_{table}_input_data_digest")
        op.drop_column(table, "input_data_digest")
