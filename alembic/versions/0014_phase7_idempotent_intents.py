"""Phase 7 correctness kernel: deterministic order identity and version metadata."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0014_phase7_idempotent"
down_revision = "0013_phase7_order_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    op.add_column("paper_orders", sa.Column("intent_hash", sa.String(length=64), nullable=True))
    op.add_column(
        "paper_orders",
        sa.Column("intent_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
    )
    op.add_column("paper_orders", sa.Column("supersedes_paper_order_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        op.f("fk_paper_orders_supersedes_paper_order_id_paper_orders"),
        "paper_orders",
        "paper_orders",
        ["supersedes_paper_order_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_paper_orders_supersedes_paper_order_id",
        "paper_orders",
        ["supersedes_paper_order_id"],
        unique=False,
    )

    _backfill_intent_identity(bind)

    duplicate_hashes = bind.execute(
        sa.text(
            """
            SELECT intent_hash, COUNT(*) AS row_count
            FROM paper_orders
            GROUP BY intent_hash
            HAVING COUNT(*) > 1
            """
        )
    ).mappings().all()
    if duplicate_hashes:
        raise RuntimeError(
            "Cannot enforce deterministic intent uniqueness because duplicate material identities "
            f"already exist: {[row['intent_hash'] for row in duplicate_hashes]}"
        )

    op.alter_column("paper_orders", "intent_hash", nullable=False)
    op.create_unique_constraint("uq_paper_orders_intent_hash", "paper_orders", ["intent_hash"])
    op.alter_column("paper_orders", "intent_version", server_default=None)


def downgrade() -> None:
    op.drop_constraint("uq_paper_orders_intent_hash", "paper_orders", type_="unique")
    op.drop_index("ix_paper_orders_supersedes_paper_order_id", table_name="paper_orders")
    op.drop_constraint(
        op.f("fk_paper_orders_supersedes_paper_order_id_paper_orders"),
        "paper_orders",
        type_="foreignkey",
    )
    op.drop_column("paper_orders", "supersedes_paper_order_id")
    op.drop_column("paper_orders", "intent_version")
    op.drop_column("paper_orders", "intent_hash")


def _backfill_intent_identity(bind) -> None:
    rows = bind.execute(
        sa.text(
            """
            SELECT
                paper_orders.id,
                strategies.strategy_id AS strategy_id,
                paper_orders.intended_session_date AS intended_session_date,
                symbols.ticker AS symbol,
                paper_orders.side AS side,
                paper_orders.quantity AS quantity
            FROM paper_orders
            JOIN strategy_runs ON strategy_runs.id = paper_orders.strategy_run_id
            JOIN strategies ON strategies.id = strategy_runs.strategy_id
            JOIN symbols ON symbols.id = paper_orders.symbol_id
            ORDER BY paper_orders.created_at ASC, paper_orders.id ASC
            """
        )
    ).mappings().all()

    update_stmt = sa.text(
        """
        UPDATE paper_orders
        SET
            intent_hash = :intent_hash,
            intent_version = :intent_version,
            supersedes_paper_order_id = NULL
        WHERE id = :paper_order_id
        """
    )
    for row in rows:
        bind.execute(
            update_stmt,
            {
                "paper_order_id": row["id"],
                "intent_hash": _build_intent_hash(
                    strategy_id=row["strategy_id"],
                    session_date=row["intended_session_date"],
                    symbol=row["symbol"],
                    side=row["side"],
                    quantity=Decimal(str(row["quantity"])),
                ),
                "intent_version": 1,
            },
        )


def _build_intent_hash(
    *,
    strategy_id: str,
    session_date: date,
    symbol: str,
    side: str,
    quantity: Decimal,
) -> str:
    payload = {
        "strategy_id": strategy_id.strip(),
        "session_date": session_date.isoformat(),
        "symbol": _normalize_symbol(symbol),
        "side": side.strip().lower(),
        "quantity": _normalize_quantity(quantity),
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _normalize_symbol(symbol: str) -> str:
    cleaned = "".join(char for char in symbol.strip().upper() if char.isalnum())
    if not cleaned:
        raise ValueError("Order identity requires a non-empty symbol.")
    return cleaned


def _normalize_quantity(quantity: Decimal) -> str:
    normalized = quantity.normalize()
    as_text = format(normalized, "f")
    if "." in as_text:
        as_text = as_text.rstrip("0").rstrip(".")
    return as_text or "0"
