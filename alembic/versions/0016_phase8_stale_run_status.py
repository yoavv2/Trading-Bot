"""Phase 8 concurrency guard: add STALE to the strategy_run_status enum for stale-run reclaim."""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "0016_phase8_stale_run_status"
down_revision = "0015_phase7_kill_switch"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE strategy_run_status ADD VALUE IF NOT EXISTS 'stale'")


def downgrade() -> None:
    # PostgreSQL cannot drop a single enum value in place without recreating the
    # whole type (rewriting every dependent column). That rewrite is intentionally
    # not performed here, so this downgrade is a documented no-op: 'stale' remains
    # a valid strategy_run_status value after downgrading past this revision.
    pass
