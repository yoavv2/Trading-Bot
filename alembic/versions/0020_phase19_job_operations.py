"""Phase 19 job operations: strategy_runs.job_id link + config_invalid failure reason."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0020_phase19_job_operations"
down_revision = "0019_phase18_job_idempotency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # D-22: add the new failure-reason enum value. Nothing in this migration
    # references the new value, so adding it inside the migration transaction
    # is safe (PostgreSQL only forbids *using* a newly added enum value in the
    # same transaction that adds it, not the ADD itself) -- same pattern as
    # migration 0016.
    op.execute("ALTER TYPE job_failure_reason ADD VALUE IF NOT EXISTS 'config_invalid'")

    # D-01: persisted Job -> StrategyRun link.
    op.add_column("strategy_runs", sa.Column("job_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_strategy_runs_job_id_jobs"),
        "strategy_runs",
        "jobs",
        ["job_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_unique_constraint(
        op.f("uq_strategy_runs_job_id"),
        "strategy_runs",
        ["job_id"],
    )


def downgrade() -> None:
    op.drop_constraint(op.f("uq_strategy_runs_job_id"), "strategy_runs", type_="unique")
    op.drop_constraint(op.f("fk_strategy_runs_job_id_jobs"), "strategy_runs", type_="foreignkey")
    op.drop_column("strategy_runs", "job_id")

    # PostgreSQL cannot drop a single enum value in place without recreating
    # the whole type (rewriting every dependent column). That rewrite is
    # intentionally not performed here, so this downgrade is a documented
    # no-op for the enum: 'config_invalid' remains a valid job_failure_reason
    # value after downgrading past this revision. ADD VALUE IF NOT EXISTS in
    # upgrade() keeps re-upgrading idempotent.
