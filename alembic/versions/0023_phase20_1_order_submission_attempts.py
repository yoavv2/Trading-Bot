"""Phase 20.1 order submission attempts: durable per-HTTP-attempt log (COR-06).

One row per HTTP attempt of an order POST (D-12). The row is committed BEFORE
the attempt (``outcome_class`` NULL, ``completed_at`` NULL) and completed once
AFTER it, so a crash between the two leaves an incomplete row that reads back
as ``ambiguous`` and is never re-sent.

Database invariants (each rejected by PostgreSQL, tested):

* ``ck_order_submission_attempts_outcome_class``: the closed outcome set
  ``pre_connection, deadline_expired, ambiguous, duplicate_reported,
  rejected, accepted`` (NULL only while incomplete). ``deadline_expired`` is part
  of the set from this migration on so no later migration alters this CHECK;
  its only writer is the executor that created the attempt row (20.1-15).
* ``ck_order_submission_attempts_outcome_pair``: completion and outcome are set
  together, ``(completed_at IS NULL) = (outcome_class IS NULL)``.
* ``ck_order_submission_attempts_attempt_number_positive``.
* ``uq_order_submission_attempts_paper_order_id_attempt_number``.

The table is append-only in code (no delete path; an outcome is written once)
and is never touched by operation end or expiry (J-2).

Downgrade drops the table (the attempt evidence itself is lost).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0023_phase20_1_order_submission_attempts"
down_revision = "0022_phase20_1_active_paper_strategy"
branch_labels = None
depends_on = None

TABLE = "order_submission_attempts"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("paper_order_id", sa.Uuid(), nullable=False),
        sa.Column("strategy_run_id", sa.Uuid(), nullable=True),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome_class", sa.String(length=24), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("error_type", sa.String(length=64), nullable=True),
        sa.Column("broker_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "outcome_class IS NULL OR outcome_class IN ("
            "'pre_connection', 'deadline_expired', 'ambiguous', "
            "'duplicate_reported', 'rejected', 'accepted')",
            name=op.f("ck_order_submission_attempts_outcome_class"),
        ),
        sa.CheckConstraint(
            "(completed_at IS NULL) = (outcome_class IS NULL)",
            name=op.f("ck_order_submission_attempts_outcome_pair"),
        ),
        sa.CheckConstraint(
            "attempt_number >= 1",
            name=op.f("ck_order_submission_attempts_attempt_number_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["paper_order_id"],
            ["paper_orders.id"],
            name=op.f("fk_order_submission_attempts_paper_order_id_paper_orders"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["strategy_run_id"],
            ["strategy_runs.id"],
            name=op.f("fk_order_submission_attempts_strategy_run_id_strategy_runs"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_submission_attempts")),
        sa.UniqueConstraint(
            "paper_order_id",
            "attempt_number",
            name=op.f("uq_order_submission_attempts_paper_order_id_attempt_number"),
        ),
    )
    op.create_index(
        op.f("ix_order_submission_attempts_strategy_run_id"),
        TABLE,
        ["strategy_run_id"],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_order_submission_attempts_strategy_run_id"), table_name=TABLE)
    op.drop_table(TABLE)
