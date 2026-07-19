"""Phase 17 job framework: jobs, job_dependencies, job_events, job_logs."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0018_phase17_job_framework"
down_revision = "0017_phase11_query_perf_indices"
branch_labels = None
depends_on = None

_JOB_STATUS_VALUES = ("queued", "running", "succeeded", "failed", "cancelled")
_JOB_FAILURE_REASON_VALUES = ("handler_error", "worker_lost", "lease_expired", "cancellation_timeout")
_JOB_CANCELLATION_CAUSE_VALUES = ("operator_request", "dependency_failed", "dependency_cancelled")
_JOB_EVENT_TYPE_VALUES = (
    "submitted",
    "claimed",
    "succeeded",
    "failed",
    "cancellation_requested",
    "cancelled",
    "lease_expired",
    "worker_lost",
    "cancellation_timeout",
    "dependency_resolved",
)
_JOB_TRANSITION_OUTCOME_VALUES = ("accepted", "rejected")


def upgrade() -> None:
    bind = op.get_bind()

    job_status = postgresql.ENUM(*_JOB_STATUS_VALUES, name="job_status")
    job_failure_reason = postgresql.ENUM(*_JOB_FAILURE_REASON_VALUES, name="job_failure_reason")
    job_cancellation_cause = postgresql.ENUM(*_JOB_CANCELLATION_CAUSE_VALUES, name="job_cancellation_cause")
    job_event_type = postgresql.ENUM(*_JOB_EVENT_TYPE_VALUES, name="job_event_type")
    job_transition_outcome = postgresql.ENUM(*_JOB_TRANSITION_OUTCOME_VALUES, name="job_transition_outcome")
    for enum_type in (
        job_status,
        job_failure_reason,
        job_cancellation_cause,
        job_event_type,
        job_transition_outcome,
    ):
        enum_type.create(bind, checkfirst=False)

    op.create_table(
        "jobs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("job_type", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column(
            "status",
            postgresql.ENUM(*_JOB_STATUS_VALUES, name="job_status", create_type=False),
            nullable=False,
        ),
        sa.Column("queued_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "failure_reason",
            postgresql.ENUM(*_JOB_FAILURE_REASON_VALUES, name="job_failure_reason", create_type=False),
            nullable=True,
        ),
        sa.Column("failure_message", sa.Text(), nullable=True),
        sa.Column("outcome_uncertain", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("result_summary", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("cancellation_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancellation_requested_by", sa.String(length=128), nullable=True),
        sa.Column("cancellation_reason", sa.Text(), nullable=True),
        sa.Column("cancellation_acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "cancellation_cause",
            postgresql.ENUM(*_JOB_CANCELLATION_CAUSE_VALUES, name="job_cancellation_cause", create_type=False),
            nullable=True,
        ),
        sa.Column("blocking_job_id", sa.UUID(), nullable=True),
        sa.Column(
            "blocking_job_status",
            postgresql.ENUM(*_JOB_STATUS_VALUES, name="job_status", create_type=False),
            nullable=True,
        ),
        sa.Column("root_cause_job_id", sa.UUID(), nullable=True),
        sa.Column("progress_percent", sa.Integer(), nullable=True),
        sa.Column("progress_step", sa.String(length=255), nullable=True),
        sa.Column("progress_current", sa.Integer(), nullable=True),
        sa.Column("progress_total", sa.Integer(), nullable=True),
        sa.Column("progress_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["blocking_job_id"],
            ["jobs.id"],
            name=op.f("fk_jobs_blocking_job_id_jobs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["root_cause_job_id"],
            ["jobs.id"],
            name=op.f("fk_jobs_root_cause_job_id_jobs"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jobs")),
        sa.CheckConstraint(
            "progress_percent IS NULL OR (progress_percent >= 0 AND progress_percent <= 100)",
            name=op.f("ck_jobs_job_progress_percent_range"),
        ),
    )
    op.create_index("ix_jobs_status_queued_at", "jobs", ["status", "queued_at"], unique=False)
    op.create_index("ix_jobs_status_lease_expires_at", "jobs", ["status", "lease_expires_at"], unique=False)
    op.create_index("ix_jobs_job_type_status", "jobs", ["job_type", "status"], unique=False)
    op.alter_column("jobs", "payload", server_default=None)
    op.alter_column("jobs", "result_summary", server_default=None)

    op.create_table(
        "job_dependencies",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column("depends_on_job_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            name=op.f("fk_job_dependencies_job_id_jobs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["depends_on_job_id"],
            ["jobs.id"],
            name=op.f("fk_job_dependencies_depends_on_job_id_jobs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job_dependencies")),
        sa.UniqueConstraint(
            "job_id",
            "depends_on_job_id",
            name=op.f("uq_job_dependencies_job_id_depends_on_job_id"),
        ),
        sa.CheckConstraint(
            "job_id <> depends_on_job_id",
            name=op.f("ck_job_dependencies_job_not_self_dependent"),
        ),
    )
    op.create_index(
        "ix_job_dependencies_depends_on_job_id",
        "job_dependencies",
        ["depends_on_job_id"],
        unique=False,
    )

    op.create_table(
        "job_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column(
            "from_status",
            postgresql.ENUM(*_JOB_STATUS_VALUES, name="job_status", create_type=False),
            nullable=True,
        ),
        sa.Column(
            "to_status",
            postgresql.ENUM(*_JOB_STATUS_VALUES, name="job_status", create_type=False),
            nullable=True,
        ),
        sa.Column(
            "event_type",
            postgresql.ENUM(*_JOB_EVENT_TYPE_VALUES, name="job_event_type", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "outcome",
            postgresql.ENUM(*_JOB_TRANSITION_OUTCOME_VALUES, name="job_transition_outcome", create_type=False),
            nullable=False,
        ),
        sa.Column("event_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("requested_by", sa.String(length=128), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terminal_cause", sa.String(length=64), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            name=op.f("fk_job_events_job_id_jobs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job_events")),
    )
    op.create_index("ix_job_events_job_id_event_at", "job_events", ["job_id", "event_at"], unique=False)
    op.create_index("ix_job_events_job_id_event_type", "job_events", ["job_id", "event_type"], unique=False)
    op.alter_column("job_events", "details", server_default=None)

    op.create_table(
        "job_logs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("logged_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("level", sa.String(length=16), nullable=False),
        sa.Column("event_code", sa.String(length=64), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("handler_type", sa.String(length=64), nullable=False),
        sa.Column("context", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            name=op.f("fk_job_logs_job_id_jobs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job_logs")),
        sa.UniqueConstraint("job_id", "sequence", name=op.f("uq_job_logs_job_id_sequence")),
    )
    op.create_index("ix_job_logs_job_id_sequence", "job_logs", ["job_id", "sequence"], unique=False)
    op.alter_column("job_logs", "context", server_default=None)


def downgrade() -> None:
    bind = op.get_bind()

    op.drop_index("ix_job_logs_job_id_sequence", table_name="job_logs")
    op.drop_table("job_logs")

    op.drop_index("ix_job_events_job_id_event_type", table_name="job_events")
    op.drop_index("ix_job_events_job_id_event_at", table_name="job_events")
    op.drop_table("job_events")

    op.drop_index("ix_job_dependencies_depends_on_job_id", table_name="job_dependencies")
    op.drop_table("job_dependencies")

    op.drop_index("ix_jobs_job_type_status", table_name="jobs")
    op.drop_index("ix_jobs_status_lease_expires_at", table_name="jobs")
    op.drop_index("ix_jobs_status_queued_at", table_name="jobs")
    op.drop_table("jobs")

    job_transition_outcome = postgresql.ENUM(*_JOB_TRANSITION_OUTCOME_VALUES, name="job_transition_outcome")
    job_event_type = postgresql.ENUM(*_JOB_EVENT_TYPE_VALUES, name="job_event_type")
    job_cancellation_cause = postgresql.ENUM(*_JOB_CANCELLATION_CAUSE_VALUES, name="job_cancellation_cause")
    job_failure_reason = postgresql.ENUM(*_JOB_FAILURE_REASON_VALUES, name="job_failure_reason")
    job_status = postgresql.ENUM(*_JOB_STATUS_VALUES, name="job_status")
    for enum_type in (
        job_transition_outcome,
        job_event_type,
        job_cancellation_cause,
        job_failure_reason,
        job_status,
    ):
        enum_type.drop(bind, checkfirst=False)
