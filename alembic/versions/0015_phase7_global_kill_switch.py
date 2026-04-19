"""Phase 7 correctness kernel: durable global kill switch state."""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0015_phase7_kill_switch"
down_revision = "0014_phase7_idempotent"
branch_labels = None
depends_on = None


_KILL_SWITCH_STATE_VALUES = ("armed", "tripped")
GLOBAL_KILL_SWITCH_NAME = "global_kill_switch"


def upgrade() -> None:
    bind = op.get_bind()

    kill_switch_state_enum = postgresql.ENUM(
        *_KILL_SWITCH_STATE_VALUES,
        name="kill_switch_state",
    )
    kill_switch_state_enum.create(bind, checkfirst=False)

    op.create_table(
        "system_controls",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column(
            "state",
            postgresql.ENUM(
                *_KILL_SWITCH_STATE_VALUES,
                name="kill_switch_state",
                create_type=False,
            ),
            nullable=False,
            server_default=sa.text("'armed'::kill_switch_state"),
        ),
        sa.Column("last_changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_change_actor", sa.String(length=120), nullable=False),
        sa.Column("last_change_reason", sa.Text(), nullable=True),
        sa.Column("last_change_run_id", sa.UUID(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["last_change_run_id"],
            ["strategy_runs.id"],
            name=op.f("fk_system_controls_last_change_run_id_strategy_runs"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_system_controls")),
        sa.UniqueConstraint("name", name=op.f("uq_system_controls_name")),
    )

    bind.execute(
        sa.text(
            """
            INSERT INTO system_controls (
                id,
                name,
                state,
                last_changed_at,
                last_change_actor,
                last_change_reason,
                last_change_run_id
            ) VALUES (
                :id,
                :name,
                CAST(:state AS kill_switch_state),
                NOW(),
                :actor,
                :reason,
                NULL
            )
            """
        ),
        {
            "id": str(uuid.uuid4()),
            "name": GLOBAL_KILL_SWITCH_NAME,
            "state": "armed",
            "actor": "system_bootstrap",
            "reason": "Initial armed state for global kill switch.",
        },
    )

    op.alter_column("system_controls", "state", server_default=None)


def downgrade() -> None:
    bind = op.get_bind()

    op.drop_table("system_controls")

    kill_switch_state_enum = postgresql.ENUM(
        *_KILL_SWITCH_STATE_VALUES,
        name="kill_switch_state",
    )
    kill_switch_state_enum.drop(bind, checkfirst=False)
