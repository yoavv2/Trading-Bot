"""Phase 18 endpoint-scoped Job mutation idempotency schema."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0019_phase18_job_idempotency"
down_revision = "0018_phase17_job_framework"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_mutations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("endpoint_id", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("job_id", sa.UUID(), nullable=False),
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
            ["job_id"],
            ["jobs.id"],
            name=op.f("fk_job_mutations_job_id_jobs"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job_mutations")),
        sa.UniqueConstraint("endpoint_id", "idempotency_key", name="uq_job_mutations_endpoint_key"),
    )
    op.create_index("ix_job_mutations_job_id", "job_mutations", ["job_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_job_mutations_job_id", table_name="job_mutations")
    op.drop_table("job_mutations")
