"""Research studies and the shared provider request ledger (plan S3).

Research databases only (``trading_research`` and throwaway test databases); the main
trading database stays at 0021. 0031 is not rewritten: this revision adds

* ``provider_request_ledger``: one row per attempted authenticated provider request
  (ingestion, metadata, name enrichment, every retry). The ``DatabaseRequestBudget``
  admits a request only after counting this table under a transaction advisory lock, so
  every process and worker of this application shares one budget. Requests made outside
  this application are not observable and never appear here.
* ``study_evaluations``: the stored ``comparison.json`` per (study revision, scope).
* ``study_revision_jobs``: the Jobs a study revision submitted, by role; a partial unique
  index allows exactly one non-rerun final-test graph per revision, enforced by the
  database under concurrent requests.

Migrations 0022-0031 are unchanged.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0032_research_studies"
down_revision = "0031_research_platform"
branch_labels = None
depends_on = None


def _uuid() -> sa.types.TypeEngine:
    return sa.Uuid(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "provider_request_ledger",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("symbol", sa.String(20), nullable=True),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("process_id", sa.String(128), nullable=False),
        sa.Column("job_id", _uuid(), nullable=True),
        sa.Column("outcome", sa.String(32), nullable=True),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_provider_request_ledger_provider_attempted_at",
        "provider_request_ledger",
        ["provider", "attempted_at"],
    )
    op.create_index(
        "ix_provider_request_ledger_provider_symbol_attempted_at",
        "provider_request_ledger",
        ["provider", "symbol", "attempted_at"],
    )

    op.create_table(
        "study_evaluations",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "study_revision_id",
            _uuid(),
            sa.ForeignKey("study_revisions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("job_id", _uuid(), sa.ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("comparison_json", sa.JSON(), nullable=False),
        sa.Column("is_rerun", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("scope IN ('initial', 'final_test')", name="study_evaluations_scope_closed"),
    )
    op.create_index(
        "ix_study_evaluations_study_revision_id_scope",
        "study_evaluations",
        ["study_revision_id", "scope"],
    )

    op.create_table(
        "study_revision_jobs",
        sa.Column("job_id", _uuid(), sa.ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True),
        sa.Column(
            "study_revision_id",
            _uuid(),
            sa.ForeignKey("study_revisions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("is_rerun", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("detail", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("scope IN ('initial', 'final_test')", name="study_revision_jobs_scope_closed"),
    )
    op.create_index(
        "ix_study_revision_jobs_study_revision_id", "study_revision_jobs", ["study_revision_id"]
    )
    # One initial graph and one non-rerun final-test graph per revision: the evaluate Job of
    # each graph is the unique anchor, so two concurrent ``run`` or ``final-test`` requests
    # cannot both succeed.
    op.create_index(
        "uq_study_revision_jobs_one_graph_per_scope",
        "study_revision_jobs",
        ["study_revision_id", "scope"],
        unique=True,
        postgresql_where=sa.text("role = 'evaluate' AND is_rerun = false"),
    )


def downgrade() -> None:
    op.drop_index("uq_study_revision_jobs_one_graph_per_scope", table_name="study_revision_jobs")
    op.drop_index("ix_study_revision_jobs_study_revision_id", table_name="study_revision_jobs")
    op.drop_table("study_revision_jobs")
    op.drop_index("ix_study_evaluations_study_revision_id_scope", table_name="study_evaluations")
    op.drop_table("study_evaluations")
    op.drop_index(
        "ix_provider_request_ledger_provider_symbol_attempted_at", table_name="provider_request_ledger"
    )
    op.drop_index("ix_provider_request_ledger_provider_attempted_at", table_name="provider_request_ledger")
    op.drop_table("provider_request_ledger")
