"""Phase 7 correctness kernel: closed order lifecycle enums and append-only order events."""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0013_phase7_order_state"
down_revision = "0012_phase6_operator_controls"
branch_labels = None
depends_on = None


_ORDER_LIFECYCLE_VALUES = (
    "pending_submission",
    "submission_failed",
    "submitted",
    "partially_filled",
    "filled",
    "canceled",
    "rejected",
    "expired",
    "unknown",
)
_ORDER_EVENT_TYPE_VALUES = (
    "legacy_imported",
    "intent_registered",
    "retry_requested",
    "submission_failed",
    "broker_acknowledged",
    "broker_partially_filled",
    "broker_filled",
    "broker_canceled",
    "broker_rejected",
    "broker_expired",
    "broker_status_unknown",
)
_ORDER_EVENT_OUTCOME_VALUES = ("accepted", "rejected")
_LEGACY_STATUS_MAPPING = {
    "pending_submission": "pending_submission",
    "submission_failed": "submission_failed",
    "submitted": "submitted",
    "partially_filled": "partially_filled",
    "filled": "filled",
    "canceled": "canceled",
    "submission_rejected": "rejected",
    "rejected": "rejected",
    "expired": "expired",
}


def upgrade() -> None:
    bind = op.get_bind()

    lifecycle_enum = postgresql.ENUM(*_ORDER_LIFECYCLE_VALUES, name="order_lifecycle_state")
    event_type_enum = postgresql.ENUM(*_ORDER_EVENT_TYPE_VALUES, name="order_event_type")
    outcome_enum = postgresql.ENUM(*_ORDER_EVENT_OUTCOME_VALUES, name="order_event_outcome")
    lifecycle_enum.create(bind, checkfirst=False)
    event_type_enum.create(bind, checkfirst=False)
    outcome_enum.create(bind, checkfirst=False)

    op.execute(
        """
        ALTER TABLE paper_orders
        ALTER COLUMN status
        TYPE order_lifecycle_state
        USING (
            CASE status
                WHEN 'pending_submission' THEN 'pending_submission'
                WHEN 'submission_failed' THEN 'submission_failed'
                WHEN 'submitted' THEN 'submitted'
                WHEN 'partially_filled' THEN 'partially_filled'
                WHEN 'filled' THEN 'filled'
                WHEN 'canceled' THEN 'canceled'
                WHEN 'submission_rejected' THEN 'rejected'
                WHEN 'rejected' THEN 'rejected'
                WHEN 'expired' THEN 'expired'
                ELSE 'unknown'
            END
        )::order_lifecycle_state
        """
    )

    op.create_table(
        "order_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("paper_order_id", sa.UUID(), nullable=False),
        sa.Column("strategy_run_id", sa.UUID(), nullable=False),
        sa.Column(
            "from_state",
            postgresql.ENUM(*_ORDER_LIFECYCLE_VALUES, name="order_lifecycle_state", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "event_type",
            postgresql.ENUM(*_ORDER_EVENT_TYPE_VALUES, name="order_event_type", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "to_state",
            postgresql.ENUM(*_ORDER_LIFECYCLE_VALUES, name="order_lifecycle_state", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "outcome",
            postgresql.ENUM(*_ORDER_EVENT_OUTCOME_VALUES, name="order_event_outcome", create_type=False),
            nullable=False,
        ),
        sa.Column("event_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["paper_order_id"],
            ["paper_orders.id"],
            name=op.f("fk_order_events_paper_order_id_paper_orders"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["strategy_run_id"],
            ["strategy_runs.id"],
            name=op.f("fk_order_events_strategy_run_id_strategy_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_events")),
    )
    op.create_index(
        "ix_order_events_paper_order_id_event_at",
        "order_events",
        ["paper_order_id", "event_at"],
        unique=False,
    )
    op.create_index(
        "ix_order_events_strategy_run_id_event_at",
        "order_events",
        ["strategy_run_id", "event_at"],
        unique=False,
    )
    op.create_index(
        "ix_order_events_paper_order_id_outcome",
        "order_events",
        ["paper_order_id", "outcome"],
        unique=False,
    )
    op.alter_column("order_events", "details", server_default=None)

    _backfill_order_events(bind)


def downgrade() -> None:
    bind = op.get_bind()

    op.drop_index("ix_order_events_paper_order_id_outcome", table_name="order_events")
    op.drop_index("ix_order_events_strategy_run_id_event_at", table_name="order_events")
    op.drop_index("ix_order_events_paper_order_id_event_at", table_name="order_events")
    op.drop_table("order_events")

    op.execute("ALTER TABLE paper_orders ALTER COLUMN status TYPE TEXT USING status::text")

    outcome_enum = postgresql.ENUM(*_ORDER_EVENT_OUTCOME_VALUES, name="order_event_outcome")
    event_type_enum = postgresql.ENUM(*_ORDER_EVENT_TYPE_VALUES, name="order_event_type")
    lifecycle_enum = postgresql.ENUM(*_ORDER_LIFECYCLE_VALUES, name="order_lifecycle_state")
    outcome_enum.drop(bind, checkfirst=False)
    event_type_enum.drop(bind, checkfirst=False)
    lifecycle_enum.drop(bind, checkfirst=False)


def _backfill_order_events(bind) -> None:
    rows = bind.execute(
        sa.text(
            """
            SELECT
                id,
                strategy_run_id,
                status::text AS status,
                COALESCE(
                    last_broker_update_at,
                    filled_at,
                    canceled_at,
                    submitted_at,
                    last_submission_attempt_at,
                    last_synced_at,
                    updated_at,
                    created_at
                ) AS event_at,
                broker_status,
                broker_payload
            FROM paper_orders
            ORDER BY created_at ASC, id ASC
            """
        )
    ).mappings()

    insert_stmt = sa.text(
        """
        INSERT INTO order_events (
            id,
            paper_order_id,
            strategy_run_id,
            from_state,
            event_type,
            to_state,
            outcome,
            event_at,
            details
        ) VALUES (
            :id,
            :paper_order_id,
            :strategy_run_id,
            CAST(:from_state AS order_lifecycle_state),
            CAST(:event_type AS order_event_type),
            CAST(:to_state AS order_lifecycle_state),
            CAST(:outcome AS order_event_outcome),
            :event_at,
            :details
        )
        """
    ).bindparams(
        sa.bindparam("details", type_=sa.JSON()),
    )
    for row in rows:
        mapped_state = _LEGACY_STATUS_MAPPING.get(row["status"], "unknown")
        bind.execute(
            insert_stmt,
            {
                "id": uuid.uuid4(),
                "paper_order_id": row["id"],
                "strategy_run_id": row["strategy_run_id"],
                "from_state": mapped_state,
                "event_type": "legacy_imported",
                "to_state": mapped_state,
                "outcome": "accepted",
                "event_at": row["event_at"],
                "details": {
                    "source": "0013_phase7_order_state",
                    "legacy_status": row["status"],
                    "broker_status": row["broker_status"],
                    "broker_payload": row["broker_payload"] or {},
                },
            },
        )
