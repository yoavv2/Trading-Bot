"""Assistant provenance (plan S5).

Research databases only; the trading database stays at 0021. Widens ``ai_drafts`` from the
0031 placeholder into the full provenance row of one assistant attempt: what was asked
(kind, the draft it revises, the YAML it started from), what came back (YAML, note,
unsupported requests, stop reason, cache token counts), how it ended (status, deadline,
timestamps), the retry chain, the console's idempotency token and the shared ledger row
that charged the attempt. Request accounting itself stays in ``provider_request_ledger``
(provider ``anthropic``, purpose ``assistant``) so the daily cap and the concurrency cap
are counted across every process under the same advisory lock as the Tiingo budget.

Migrations 0022-0032 are unchanged.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0033_research_assistant"
down_revision = "0032_research_studies"
branch_labels = None
depends_on = None


def _uuid() -> sa.types.TypeEngine:
    return sa.Uuid(as_uuid=True)


_COLUMNS = (
    sa.Column("kind", sa.String(16), nullable=False, server_default="draft"),
    sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
    sa.Column("attempt_no", sa.Integer(), nullable=False, server_default="1"),
    sa.Column("request_token", sa.String(64), nullable=True),
    sa.Column(
        "draft_id", _uuid(), sa.ForeignKey("strategy_drafts.id", ondelete="SET NULL"), nullable=True
    ),
    sa.Column(
        "parent_ai_draft_id",
        _uuid(),
        sa.ForeignKey("ai_drafts.id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Column(
        "retry_of_ai_draft_id",
        _uuid(),
        sa.ForeignKey("ai_drafts.id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Column(
        "ledger_entry_id",
        _uuid(),
        sa.ForeignKey("provider_request_ledger.id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Column("base_yaml_text", sa.Text(), nullable=True),
    sa.Column("output_yaml_text", sa.Text(), nullable=True),
    sa.Column("note", sa.Text(), nullable=True),
    sa.Column("unsupported_requests", sa.JSON(), nullable=False, server_default="[]"),
    sa.Column("stop_reason", sa.String(48), nullable=True),
    sa.Column("cache_read_input_tokens", sa.Integer(), nullable=True),
    sa.Column("cache_creation_input_tokens", sa.Integer(), nullable=True),
    sa.Column("deadline_seconds", sa.Numeric(8, 2), nullable=True),
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
)


def upgrade() -> None:
    for column in _COLUMNS:
        op.add_column("ai_drafts", column)
    op.create_index("ix_ai_drafts_draft_id", "ai_drafts", ["draft_id"])
    op.create_index("ux_ai_drafts_request_token", "ai_drafts", ["request_token"], unique=True)


def downgrade() -> None:
    op.drop_index("ux_ai_drafts_request_token", table_name="ai_drafts")
    op.drop_index("ix_ai_drafts_draft_id", table_name="ai_drafts")
    for column in reversed(_COLUMNS):
        op.drop_column("ai_drafts", column.name)
