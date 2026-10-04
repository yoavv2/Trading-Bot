"""Phase 20.1 external broker activity: immutable verified snapshots (EXT-01, D-10, S-4).

Each row is the verified broker snapshot of ONE external order and its fills, recorded
by the audited ``record-external-activity`` Job. It is evidence only: the table has no
strategy column and no foreign key to anything but the originating Job
(``ON DELETE SET NULL``), so a recorded item can never be attributed to an owner and
never becomes a position (D-08, no adoption).

Database invariants (each rejected by PostgreSQL, tested):

* closed sets: ``side`` (buy, sell) and ``origin_tag`` (external_format,
  platform_format_unverified, replaced_by_successor) are CHECK constraints;
* ``reason`` is non-blank and ``content_hash`` is 64 lowercase hex characters;
* ``uq_external_broker_activity_order_hash``: unique (broker_order_id, content_hash),
  which makes recording idempotent and concurrency-safe;
* immutability: a BEFORE UPDATE OR DELETE trigger rejects every DELETE and every UPDATE
  except the one the foreign key's ``ON DELETE SET NULL`` performs (``job_id`` NOT NULL
  to NULL with every other column unchanged).

Downgrade drops the table, the trigger function and the indexes (the recorded
evidence is lost).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0025_phase20_1_external_broker_activity"
down_revision = "0024_phase20_1_account_reconciliation_runs"
branch_labels = None
depends_on = None

TABLE = "external_broker_activity"
TRIGGER = "external_broker_activity_immutable_trg"
FUNCTION = "external_broker_activity_immutable"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=True),
        sa.Column("broker_order_id", sa.String(length=128), nullable=False),
        sa.Column("client_order_id", sa.String(length=128), nullable=True),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("side", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("qty", sa.Numeric(precision=24, scale=6), nullable=True),
        sa.Column("filled_qty", sa.Numeric(precision=24, scale=6), nullable=False),
        sa.Column("filled_avg_price", sa.Numeric(precision=24, scale=6), nullable=True),
        sa.Column("broker_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("successor_broker_order_id", sa.String(length=128), nullable=True),
        sa.Column("origin_tag", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("order_snapshot", sa.JSON(), nullable=False),
        sa.Column("fills", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "side IN ('buy', 'sell')",
            name=op.f("ck_external_broker_activity_side"),
        ),
        sa.CheckConstraint(
            "origin_tag IN ('external_format', 'platform_format_unverified', "
            "'replaced_by_successor')",
            name=op.f("ck_external_broker_activity_origin_tag"),
        ),
        sa.CheckConstraint(
            "btrim(reason) <> ''",
            name=op.f("ck_external_broker_activity_reason_not_blank"),
        ),
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_external_broker_activity_content_hash_format"),
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            name=op.f("fk_external_broker_activity_job_id_jobs"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_external_broker_activity")),
        sa.UniqueConstraint(
            "broker_order_id",
            "content_hash",
            name=op.f("uq_external_broker_activity_order_hash"),
        ),
    )
    op.create_index(op.f("ix_external_broker_activity_job_id"), TABLE, ["job_id"])
    op.create_index(op.f("ix_external_broker_activity_broker_order_id"), TABLE, ["broker_order_id"])
    op.create_index(
        op.f("ix_external_broker_activity_broker_order_id_created_at"),
        TABLE,
        ["broker_order_id", "created_at"],
    )
    op.create_index(op.f("ix_external_broker_activity_created_at"), TABLE, ["created_at"])

    # json has no equality operator: compare through jsonb. The ONLY permitted UPDATE is
    # the foreign key's ON DELETE SET NULL (job_id NOT NULL -> NULL, all else unchanged).
    op.execute(
        f"""
        CREATE FUNCTION {FUNCTION}() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'external_broker_activity rows are immutable (DELETE rejected)'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            IF OLD.job_id IS NOT NULL
               AND NEW.job_id IS NULL
               AND (to_jsonb(NEW) - 'job_id') = (to_jsonb(OLD) - 'job_id') THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'external_broker_activity rows are immutable (UPDATE rejected)'
                USING ERRCODE = 'integrity_constraint_violation';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {TRIGGER}
        BEFORE UPDATE OR DELETE ON {TABLE}
        FOR EACH ROW EXECUTE FUNCTION {FUNCTION}()
        """
    )


def downgrade() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS {TRIGGER} ON {TABLE}")
    op.execute(f"DROP FUNCTION IF EXISTS {FUNCTION}()")
    op.drop_index(op.f("ix_external_broker_activity_created_at"), table_name=TABLE)
    op.drop_index(op.f("ix_external_broker_activity_broker_order_id_created_at"), table_name=TABLE)
    op.drop_index(op.f("ix_external_broker_activity_broker_order_id"), table_name=TABLE)
    op.drop_index(op.f("ix_external_broker_activity_job_id"), table_name=TABLE)
    op.drop_table(TABLE)
