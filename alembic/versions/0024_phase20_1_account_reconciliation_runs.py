"""Phase 20.1 account reconciliation runs: owner-less storage (ACCT-01, R-31, J-3).

An account-level reconciliation compares the whole broker account with the
local records of every owner, so its result cannot belong to any one owner and
must not borrow an arbitrary one. This table therefore carries NO owner
reference at all: there is no owner column and no foreign key other than the
optional originating Job (``ON DELETE SET NULL``).

Database invariants (each rejected by PostgreSQL, tested):

* ``ck_account_reconciliation_runs_scope``: the closed scope set, exactly
  ``account``.
* ``ck_account_reconciliation_runs_status``: the closed status set
  ``pending, succeeded, failed``.
* ``blocks_execution`` is NOT NULL (no unknown blocking state).

Downgrade drops the table (the stored account-level results are lost).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0024_phase20_1_account_reconciliation_runs"
down_revision = "0023_phase20_1_order_submission_attempts"
branch_labels = None
depends_on = None

TABLE = "account_reconciliation_runs"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=True),
        sa.Column("scope", sa.String(length=16), server_default="account", nullable=False),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("trigger_source", sa.String(length=64), nullable=False),
        sa.Column("as_of_session", sa.Date(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("blocks_execution", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("finding_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("blocking_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("findings", sa.JSON(), nullable=False),
        sa.Column("account_divergence", sa.JSON(), nullable=False),
        sa.Column("unexplained_exposure", sa.JSON(), nullable=False),
        sa.Column("classification_summary", sa.JSON(), nullable=False),
        sa.Column("unresolved_reasons", sa.JSON(), nullable=False),
        sa.Column("result_summary", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
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
            "scope IN ('account')",
            name=op.f("ck_account_reconciliation_runs_scope"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'succeeded', 'failed')",
            name=op.f("ck_account_reconciliation_runs_status"),
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            name=op.f("fk_account_reconciliation_runs_job_id_jobs"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_account_reconciliation_runs")),
    )
    op.create_index(op.f("ix_account_reconciliation_runs_job_id"), TABLE, ["job_id"])
    op.create_index(
        op.f("ix_account_reconciliation_runs_completed_at"), TABLE, ["completed_at"]
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_account_reconciliation_runs_completed_at"), table_name=TABLE)
    op.drop_index(op.f("ix_account_reconciliation_runs_job_id"), table_name=TABLE)
    op.drop_table(TABLE)
