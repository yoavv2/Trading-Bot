"""Phase 20.1 active paper strategy: single-row ownership singleton (PAPER-01).

Invariant: at most one strategy owns the Alpaca paper account (D-01). The
table is a database-enforced singleton -- primary key ``id`` plus the CHECK
``ck_active_paper_strategy_singleton`` (``id = 1``) -- so a second row, a
duplicate id and an UPDATE of ``id`` to any other value are all rejected by
PostgreSQL, and ``strategy_id`` is the only owner slot.

The seed row is ``id = 1, strategy_id = NULL`` (no owner, D-02). Trend following
is never auto-selected. The row must always exist: submit-time admission and
the handover control lock it, and both fail closed when it is missing. No code
path deletes or get-or-creates it.

Revision-id width: the mandated revision id is longer than Alembic's default
``alembic_version.version_num`` VARCHAR(32), so upgrade widens that column to
VARCHAR(255) first (same transaction, before Alembic stamps the new version).
Downgrade deliberately does NOT narrow it back: at that moment the column still
holds this (too long) revision id, so narrowing would fail; the wider column is
harmless to older revisions.

Downgrade drops the table. It is lossless apart from the owner record itself.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0022_phase20_1_active_paper_strategy"
down_revision = "0021_phase20_operations_safety"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "alembic_version",
        "version_num",
        existing_type=sa.String(length=32),
        type_=sa.String(length=255),
        existing_nullable=False,
    )
    op.create_table(
        "active_paper_strategy",
        sa.Column("id", sa.SmallInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column("strategy_id", sa.Uuid(), nullable=True),
        sa.Column(
            "since",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("set_by_run_id", sa.Uuid(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
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
        sa.CheckConstraint("id = 1", name=op.f("ck_active_paper_strategy_singleton")),
        sa.ForeignKeyConstraint(
            ["strategy_id"],
            ["strategies.id"],
            name=op.f("fk_active_paper_strategy_strategy_id_strategies"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["set_by_run_id"],
            ["strategy_runs.id"],
            name=op.f("fk_active_paper_strategy_set_by_run_id_strategy_runs"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_active_paper_strategy")),
    )
    # D-02: the initial state is NO owner.
    op.execute(
        sa.text(
            "INSERT INTO active_paper_strategy (id, strategy_id, reason) "
            "VALUES (1, NULL, 'initial state: no active paper strategy')"
        )
    )


def downgrade() -> None:
    op.drop_table("active_paper_strategy")
