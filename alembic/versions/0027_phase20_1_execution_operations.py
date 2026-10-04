"""Phase 20.1 execution operations: persisted, closed operation state machine (REC-02, S-6).

Creates the execution operation (one per strategy, evaluation session and pinned risk run,
spanning several Jobs), its pinned intents and its Job links, and adds the S1-R3 fencing
columns to ``order_submission_attempts``.

Database invariants (each rejected by PostgreSQL, tested):

* ``ck_execution_operations_state``: the closed state set (running, paused,
  requires_reevaluation, terminated, completed);
* ``ck_execution_operations_reason_by_state``: the closed reason set of each state
  (twelve paused reasons, two fixed re-evaluation reasons plus the
  ``risk_limit_failed:<lowercase code>`` pattern, three terminated reasons; running and
  completed carry no reason);
* ``uq_execution_operations_one_open_per_strategy``: partial unique index on
  ``(strategy_id) WHERE state IN ('running', 'paused', 'requires_reevaluation')``;
* ``uq_execution_operations_risk_run``: one operation per (strategy, pinned risk run);
* intents: unique (operation, sequence) and unique (operation, client_order_id) (NOT a
  global unique: identities are derived without the risk run); closed ``disposition``
  (open | expired_unsent | cancelled_unsent); ``paper_order_id`` is SET NULL;
* Job links: ``job_id`` is SET NULL (a surrogate id is the primary key); closed ``mode``.

Persistence decision (CONTEXT "Claude's Discretion"): a small dedicated table set rather
than extending ``strategy_run_status``, because the operation must also persist pinned
intents that were never registered, their terminal unsent dispositions, the linked Jobs and
the reasons.

``order_submission_attempts`` gains three nullable columns (``execution_epoch``,
``executor_job_id``, ``authorization_deadline``) recording the authority under which each
attempt row was written. Downgrade drops the three tables, the columns and the indexes.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0027_phase20_1_execution_operations"
down_revision = "0026_phase20_1_recovery_records"
branch_labels = None
depends_on = None

OPERATIONS = "execution_operations"
INTENTS = "execution_operation_intents"
JOBS = "execution_operation_jobs"
ATTEMPTS = "order_submission_attempts"


def _in(column: str, values: tuple[str, ...]) -> str:
    rendered = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({rendered})"


STATES = ("running", "paused", "requires_reevaluation", "terminated", "completed")
OPEN_STATES = ("running", "paused", "requires_reevaluation")
PAUSED_REASONS = (
    "working_order_commitments_unaccounted",
    "awaiting_reconciliation",
    "kill_switch_tripped",
    "strategy_disabled",
    "reconciliation_blocking",
    "unrecognized_broker_activity",
    "outcome_unresolved",
    "broker_unavailable",
    "execution_window_not_open",
    "price_unavailable",
    "not_active_paper_strategy",
    "price_moved_beyond_tolerance",
)
REEVALUATION_REASONS = ("evaluation_data_changed", "strategy_settings_changed")
TERMINATED_REASONS = (
    "execution_window_elapsed",
    "cancelled_by_operator",
    "evaluation_superseded",
)
DISPOSITIONS = ("open", "expired_unsent", "cancelled_unsent")
JOB_MODES = ("start", "continue")

REASON_BY_STATE = (
    "(state IN ('running', 'completed') AND reason IS NULL)"
    f" OR (state = 'paused' AND reason IS NOT NULL AND {_in('reason', PAUSED_REASONS)})"
    " OR (state = 'requires_reevaluation' AND reason IS NOT NULL AND ("
    f"{_in('reason', REEVALUATION_REASONS)}"
    " OR reason ~ '^risk_limit_failed:[a-z_]+$'))"
    f" OR (state = 'terminated' AND reason IS NOT NULL AND {_in('reason', TERMINATED_REASONS)})"
)


def upgrade() -> None:
    op.create_table(
        OPERATIONS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("strategy_id", sa.Uuid(), nullable=False),
        sa.Column("as_of_session", sa.Date(), nullable=False),
        sa.Column("risk_run_id", sa.Uuid(), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=True),
        sa.Column("reason_detail", sa.String(length=64), nullable=True),
        sa.Column(
            "state_changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("basis_verification", sa.JSON(), nullable=True),
        sa.Column("executor_job_id", sa.Uuid(), nullable=True),
        sa.Column("execution_epoch", sa.Integer(), nullable=False),
        sa.Column("last_guarded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_by", sa.String(length=64), nullable=True),
        sa.Column("end_reason", sa.Text(), nullable=True),
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
        sa.CheckConstraint(_in("state", STATES), name=op.f("ck_execution_operations_state")),
        sa.CheckConstraint(REASON_BY_STATE, name=op.f("ck_execution_operations_reason_by_state")),
        sa.CheckConstraint(
            "execution_epoch >= 0",
            name=op.f("ck_execution_operations_execution_epoch_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["strategy_id"],
            ["strategies.id"],
            name=op.f("fk_execution_operations_strategy_id_strategies"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["risk_run_id"],
            ["strategy_runs.id"],
            name=op.f("fk_execution_operations_risk_run_id_strategy_runs"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_execution_operations")),
        sa.UniqueConstraint("strategy_id", "risk_run_id", name="uq_execution_operations_risk_run"),
    )
    op.create_index(
        "uq_execution_operations_one_open_per_strategy",
        OPERATIONS,
        ["strategy_id"],
        unique=True,
        postgresql_where=sa.text(f"state IN ({', '.join(repr(v) for v in OPEN_STATES)})"),
    )
    op.create_index(
        op.f("ix_execution_operations_strategy_id_created_at"),
        OPERATIONS,
        ["strategy_id", "created_at"],
    )
    op.create_index(op.f("ix_execution_operations_state"), OPERATIONS, ["state"])

    op.create_table(
        INTENTS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("risk_event_id", sa.Uuid(), nullable=True),
        sa.Column("symbol_id", sa.Uuid(), nullable=False),
        sa.Column("side", sa.String(length=8), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("reference_price", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("client_order_id", sa.String(length=64), nullable=False),
        sa.Column("paper_order_id", sa.Uuid(), nullable=True),
        sa.Column("decision_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("prior_execution_refs", sa.JSON(), nullable=False),
        sa.Column("disposition", sa.String(length=24), nullable=False),
        sa.Column("disposition_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            _in("disposition", DISPOSITIONS),
            name=op.f("ck_execution_operation_intents_disposition"),
        ),
        sa.CheckConstraint(
            "sequence >= 1", name=op.f("ck_execution_operation_intents_sequence_positive")
        ),
        sa.CheckConstraint(
            "quantity > 0", name=op.f("ck_execution_operation_intents_quantity_positive")
        ),
        sa.ForeignKeyConstraint(
            ["operation_id"],
            [f"{OPERATIONS}.id"],
            name=op.f("fk_execution_operation_intents_operation_id_execution_operations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["risk_event_id"],
            ["risk_events.id"],
            name=op.f("fk_execution_operation_intents_risk_event_id_risk_events"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["symbol_id"],
            ["symbols.id"],
            name=op.f("fk_execution_operation_intents_symbol_id_symbols"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["paper_order_id"],
            ["paper_orders.id"],
            name=op.f("fk_execution_operation_intents_paper_order_id_paper_orders"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_execution_operation_intents")),
        sa.UniqueConstraint(
            "operation_id",
            "sequence",
            name=op.f("uq_execution_operation_intents_operation_id_sequence"),
        ),
        sa.UniqueConstraint(
            "operation_id",
            "client_order_id",
            name="uq_execution_operation_intents_operation_client_order_id",
        ),
    )
    op.create_index(
        op.f("ix_execution_operation_intents_paper_order_id"), INTENTS, ["paper_order_id"]
    )
    op.create_index(
        op.f("ix_execution_operation_intents_client_order_id"), INTENTS, ["client_order_id"]
    )

    op.create_table(
        JOBS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=True),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(_in("mode", JOB_MODES), name=op.f("ck_execution_operation_jobs_mode")),
        sa.ForeignKeyConstraint(
            ["operation_id"],
            [f"{OPERATIONS}.id"],
            name=op.f("fk_execution_operation_jobs_operation_id_execution_operations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            name=op.f("fk_execution_operation_jobs_job_id_jobs"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_execution_operation_jobs")),
        sa.UniqueConstraint(
            "operation_id", "job_id", name=op.f("uq_execution_operation_jobs_operation_id_job_id")
        ),
    )
    op.create_index(op.f("ix_execution_operation_jobs_job_id"), JOBS, ["job_id"])

    op.add_column(ATTEMPTS, sa.Column("execution_epoch", sa.Integer(), nullable=True))
    op.add_column(ATTEMPTS, sa.Column("executor_job_id", sa.Uuid(), nullable=True))
    op.add_column(
        ATTEMPTS, sa.Column("authorization_deadline", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column(ATTEMPTS, "authorization_deadline")
    op.drop_column(ATTEMPTS, "executor_job_id")
    op.drop_column(ATTEMPTS, "execution_epoch")
    op.drop_index(op.f("ix_execution_operation_jobs_job_id"), table_name=JOBS)
    op.drop_table(JOBS)
    op.drop_index(op.f("ix_execution_operation_intents_client_order_id"), table_name=INTENTS)
    op.drop_index(op.f("ix_execution_operation_intents_paper_order_id"), table_name=INTENTS)
    op.drop_table(INTENTS)
    op.drop_index(op.f("ix_execution_operations_state"), table_name=OPERATIONS)
    op.drop_index(op.f("ix_execution_operations_strategy_id_created_at"), table_name=OPERATIONS)
    op.drop_index("uq_execution_operations_one_open_per_strategy", table_name=OPERATIONS)
    op.drop_table(OPERATIONS)
