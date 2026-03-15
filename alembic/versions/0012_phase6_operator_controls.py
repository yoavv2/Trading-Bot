"""Phase 6 operator controls: persisted strategy control audit runs."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0012_phase6_operator_controls"
down_revision = "0011_phase6_analytics"
branch_labels = None
depends_on = None


_RUN_TYPE_VALUES = (
    "dry_bootstrap",
    "backtest",
    "risk_evaluation",
    "paper_execution",
    "reconciliation",
)
_RUN_TYPE_VALUES_WITH_OPERATOR_CONTROL = (*_RUN_TYPE_VALUES, "operator_control")


def upgrade() -> None:
    op.execute("ALTER TYPE strategy_run_type ADD VALUE IF NOT EXISTS 'operator_control'")


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM execution_events
        WHERE strategy_run_id IN (
            SELECT id FROM strategy_runs WHERE run_type = 'operator_control'
        )
        """
    )
    op.execute("DELETE FROM strategy_runs WHERE run_type = 'operator_control'")
    _recreate_strategy_run_type_enum(_RUN_TYPE_VALUES)


def _recreate_strategy_run_type_enum(values: tuple[str, ...]) -> None:
    bind = op.get_bind()
    old_enum = sa.Enum(*_RUN_TYPE_VALUES_WITH_OPERATOR_CONTROL, name="strategy_run_type")
    new_enum = sa.Enum(*values, name="strategy_run_type_next")

    op.execute("ALTER TABLE strategy_runs ALTER COLUMN run_type TYPE TEXT")
    old_enum.drop(bind, checkfirst=False)
    new_enum.create(bind, checkfirst=False)
    op.execute(
        "ALTER TABLE strategy_runs ALTER COLUMN run_type TYPE strategy_run_type_next "
        "USING run_type::strategy_run_type_next"
    )
    op.execute("ALTER TYPE strategy_run_type_next RENAME TO strategy_run_type")
