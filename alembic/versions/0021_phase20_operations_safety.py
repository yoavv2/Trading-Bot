"""Phase 20 operations safety: domain_conflict enum value, strategy_runs.job_id
non-unique index, market_data_ingestion_runs.job_id FK, jobs.retry_of_job_id
UNIQUE FK.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0021_phase20_operations_safety"
down_revision = "0020_phase19_job_operations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # D-04: add the new failure-reason enum value. Nothing in this migration
    # references the new value, so adding it inside the migration transaction
    # is safe (PostgreSQL only forbids *using* a newly added enum value in the
    # same transaction that adds it, not the ADD itself) -- same pattern as
    # migrations 0016 and 0020.
    op.execute("ALTER TYPE job_failure_reason ADD VALUE IF NOT EXISTS 'domain_conflict'")

    # D-07: multiple StrategyRun rows may now link to the same Job (e.g. a
    # paper-session Job that creates both a paper_execution run and an
    # internal reconciliation run), so the UNIQUE constraint added by 0020 is
    # replaced with a plain non-unique index.
    op.drop_constraint(op.f("uq_strategy_runs_job_id"), "strategy_runs", type_="unique")
    op.create_index(op.f("ix_strategy_runs_job_id"), "strategy_runs", ["job_id"])

    # D-07: market-data ingestion runs gain an opaque originating Job link.
    op.add_column("market_data_ingestion_runs", sa.Column("job_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_market_data_ingestion_runs_job_id_jobs"),
        "market_data_ingestion_runs",
        "jobs",
        ["job_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        op.f("ix_market_data_ingestion_runs_job_id"),
        "market_data_ingestion_runs",
        ["job_id"],
    )

    # D-16/D-17: retry lineage. At most one retry per Job, enforced as a
    # storage invariant via a named UNIQUE constraint.
    op.add_column("jobs", sa.Column("retry_of_job_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_jobs_retry_of_job_id_jobs"),
        "jobs",
        "jobs",
        ["retry_of_job_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_unique_constraint(op.f("uq_jobs_retry_of_job_id"), "jobs", ["retry_of_job_id"])


def downgrade() -> None:
    op.drop_constraint(op.f("uq_jobs_retry_of_job_id"), "jobs", type_="unique")
    op.drop_constraint(op.f("fk_jobs_retry_of_job_id_jobs"), "jobs", type_="foreignkey")
    op.drop_column("jobs", "retry_of_job_id")

    op.drop_index(op.f("ix_market_data_ingestion_runs_job_id"), table_name="market_data_ingestion_runs")
    op.drop_constraint(
        op.f("fk_market_data_ingestion_runs_job_id_jobs"),
        "market_data_ingestion_runs",
        type_="foreignkey",
    )
    op.drop_column("market_data_ingestion_runs", "job_id")

    op.drop_index(op.f("ix_strategy_runs_job_id"), table_name="strategy_runs")
    op.create_unique_constraint(op.f("uq_strategy_runs_job_id"), "strategy_runs", ["job_id"])

    # PostgreSQL cannot drop a single enum value in place without recreating
    # the whole type (rewriting every dependent column). That rewrite is
    # intentionally not performed here, so this downgrade is a documented
    # no-op for the enum: the domain-conflict value remains a valid
    # job_failure_reason value after downgrading past this revision. ADD
    # VALUE IF NOT EXISTS in upgrade() keeps re-upgrading idempotent.
