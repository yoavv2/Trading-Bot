"""Signals product, Step 2 (docs/SIGNALS-PLAN.md): signal books and publication decisions.

Research databases only; the trading database stays at 0021.

* ``signal_books``: a named, fixed set of (strategy version, assets) that the daily Job
  runs and publishes under one cap on open published signals. Append-only: a book is
  never edited, a changed configuration is a new book.
* ``signal_publications``: the one publication decision of a signal in a book. Append-only.
  The guard computes the outcome the rules require and refuses any other:

  - ``held_back_late`` when the decision is recorded at or after the open of the next
    session (a signal is published before the session it applies to, or not at all);
  - otherwise ``held_back_open_cap`` when the book already has ``max_open`` published
    signals that have neither expired nor closed;
  - otherwise ``published``.

  So the cap cannot be exceeded and no signal is published after its entry session opened.

Migrations 0022-0035 are unchanged.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0036_signal_books"
down_revision = "0035_signals_step2"
branch_labels = None
depends_on = None

OUTCOMES = ("published", "held_back_open_cap", "held_back_late")
APPEND_ONLY_TABLES = ("signal_books", "signal_publications")


def _uuid() -> sa.types.TypeEngine:
    return sa.Uuid(as_uuid=True)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


PUBLICATION_GUARD = """
CREATE FUNCTION signal_publications_guard() RETURNS trigger AS $$
DECLARE
    book signal_books%ROWTYPE;
    open_count integer;
    next_open timestamptz;
    required text;
BEGIN
    -- One decision at a time per book: the count below must not miss a concurrent one.
    PERFORM pg_advisory_xact_lock(hashtextextended(NEW.book_id::text, 36));
    SELECT * INTO book FROM signal_books WHERE id = NEW.book_id;

    IF EXISTS (
        SELECT 1 FROM signal_publications
         WHERE book_id = NEW.book_id AND asset = NEW.asset AND signal_session = NEW.signal_session
    ) THEN
        RAISE EXCEPTION USING ERRCODE = '23000',
            MESSAGE = 'signal_publications: the signal already has a decision';
    END IF;
    IF NOT book.assets ? NEW.asset THEN
        RAISE EXCEPTION USING ERRCODE = '23000',
            MESSAGE = 'signal_publications: the asset is not in the book';
    END IF;
    IF NEW.signal_session < book.first_session THEN
        RAISE EXCEPTION USING ERRCODE = '23000',
            MESSAGE = 'signal_publications: the signal is before the first session of the book';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM signal_log
         WHERE strategy_version_id = book.strategy_version_id AND asset = NEW.asset
           AND signal_session = NEW.signal_session AND event_type = 'issued'
    ) THEN
        RAISE EXCEPTION USING ERRCODE = '23000',
            MESSAGE = 'signal_publications: no issued signal for this asset and session';
    END IF;

    SELECT count(*) INTO open_count
      FROM signal_publications p
     WHERE p.book_id = NEW.book_id AND p.outcome = 'published'
       AND NOT EXISTS (
           SELECT 1 FROM signal_log done
            WHERE done.strategy_version_id = book.strategy_version_id AND done.asset = p.asset
              AND done.signal_session = p.signal_session
              AND done.event_type IN ('expired', 'closed_stop', 'closed_target', 'closed_rule')
       );
    SELECT min(market_open) INTO next_open
      FROM market_sessions
     WHERE exchange = book.exchange AND session_date > NEW.signal_session;

    IF next_open IS NULL OR NEW.recorded_at >= next_open THEN
        required := 'held_back_late';
    ELSIF open_count >= book.max_open THEN
        required := 'held_back_open_cap';
    ELSE
        required := 'published';
    END IF;

    IF NEW.outcome <> required OR NEW.open_before <> open_count THEN
        RAISE EXCEPTION USING ERRCODE = '23000',
            MESSAGE = 'signal_publications: the rules require ' || required || ' with '
                      || open_count || ' open published signals, not ' || NEW.outcome
                      || ' with ' || NEW.open_before;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.create_table(
        "signal_books",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column(
            "strategy_version_id",
            _uuid(),
            sa.ForeignKey("strategy_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("assets", postgresql.JSONB(), nullable=False),
        sa.Column("max_open", sa.Integer(), nullable=False),
        sa.Column("first_session", sa.Date(), nullable=False),
        sa.Column("exchange", sa.String(16), nullable=False),
        sa.Column("bar_provider", sa.String(32), nullable=False),
        sa.Column("bar_adjusted", sa.Boolean(), nullable=False),
        sa.Column("config_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name", name="uq_signal_books_name"),
        sa.CheckConstraint("max_open > 0", name="max_open_positive"),
        sa.CheckConstraint(
            "jsonb_typeof(assets) = 'array' AND jsonb_array_length(assets) > 0", name="assets_nonempty_array"
        ),
        sa.CheckConstraint("config_sha256 ~ '^[0-9a-f]{64}$'", name="config_sha256_format"),
    )
    op.create_table(
        "signal_publications",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("book_id", _uuid(), sa.ForeignKey("signal_books.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("asset", sa.String(32), nullable=False),
        sa.Column("signal_session", sa.Date(), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("order_key", sa.String(64), nullable=False),
        sa.Column("open_before", sa.Integer(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("book_id", "asset", "signal_session", name="uq_signal_publications_book_asset_session"),
        sa.CheckConstraint(_in("outcome", OUTCOMES), name="outcome"),
        sa.CheckConstraint("open_before >= 0", name="open_before_nonnegative"),
        sa.CheckConstraint("order_key ~ '^[0-9a-f]{64}$'", name="order_key_format"),
    )
    op.create_index(
        "ix_signal_publications_book_outcome", "signal_publications", ["book_id", "outcome", "signal_session"]
    )

    op.execute(PUBLICATION_GUARD)
    op.execute(
        "CREATE TRIGGER trg_signal_publications_guard BEFORE INSERT ON signal_publications "
        "FOR EACH ROW EXECUTE FUNCTION signal_publications_guard()"
    )
    for table in APPEND_ONLY_TABLES:
        op.execute(
            f"""
            CREATE FUNCTION {table}_append_only() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION USING ERRCODE = '23000', MESSAGE = '{table} is append-only';
            END;
            $$ LANGUAGE plpgsql;
            """
        )
        op.execute(
            f"CREATE TRIGGER trg_{table}_append_only BEFORE UPDATE OR DELETE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION {table}_append_only()"
        )


def downgrade() -> None:
    for table in APPEND_ONLY_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_append_only ON {table}")
        op.execute(f"DROP FUNCTION IF EXISTS {table}_append_only()")
    op.execute("DROP TRIGGER IF EXISTS trg_signal_publications_guard ON signal_publications")
    op.execute("DROP FUNCTION IF EXISTS signal_publications_guard()")
    op.drop_index("ix_signal_publications_book_outcome", table_name="signal_publications")
    op.drop_table("signal_publications")
    op.drop_table("signal_books")
