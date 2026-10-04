"""Phase 20.1 recovery records: append-only recovery evidence (REC-01, D-12/D-14, S-5, J-2).

One table holds the durable evidence of uncertain-outcome recovery, in three closed
kinds:

* ``classification``: a per-intent (or Job-level ``nothing_submitted``) recovery
  classification written by a broker-sync pass;
* ``absence_evidence``: one of the four absence-evidence items (a-d) with its result and
  observation time;
* ``broker_statement``: an operator-recorded broker statement (audited evidence only; it
  never resolves an intent, round 5, 2026-10-04).

Resolution is COMPUTED from these rows plus the local orders, attempt log and
reconciliations; it is never stored as a flag. Rows are append-only (S-5, J-2): a
trigger rejects every DELETE and every UPDATE except what the two foreign keys'
``ON DELETE SET NULL`` perform (``job_id`` and/or ``paper_order_id`` NOT NULL to NULL,
every other column unchanged). Ending an operation, expiry or a hand-over therefore can
never erase or edit them.

Database invariants (each rejected by PostgreSQL, tested):

* closed sets: ``kind``, ``classification``, ``broker_state``, ``unresolved_reason``,
  ``evidence_item``, ``evidence_result`` and ``statement`` are CHECK constraints;
* per-kind shape: a ``classification`` row needs ``classification``; an
  ``absence_evidence`` row needs ``evidence_item``, ``evidence_result`` and
  ``observed_at``; a ``broker_statement`` row needs non-blank ``statement``,
  ``reference`` and ``reason``; columns of another kind must be NULL;
* ``broker_state`` only with ``found_verified`` (and required by it);
  ``unresolved_reason`` only with ``unresolved`` (and required by it);
* ``uq_recovery_records_one_statement_per_intent``: at most one ``broker_statement``
  row per ``paper_order_id``.

No CHECK requires ``job_id`` or ``paper_order_id`` to be non-null (the FK null-out must
stay legal); the writer enforces that an order-level row names its order. There is no
foreign key to strategies or strategy runs: ``strategy_public_id`` is a plain string
(NULL = account level).

Downgrade drops the table, the trigger function and the indexes (the recorded evidence
is lost).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0026_phase20_1_recovery_records"
down_revision = "0025_phase20_1_external_broker_activity"
branch_labels = None
depends_on = None

TABLE = "recovery_records"
TRIGGER = "recovery_records_immutable_trg"
FUNCTION = "recovery_records_immutable"


def _in(column: str, values: tuple[str, ...]) -> str:
    rendered = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({rendered})"


KINDS = ("classification", "absence_evidence", "broker_statement")
CLASSIFICATIONS = (
    "nothing_submitted",
    "not_sent",
    "rejected_at_submission",
    "found_verified",
    "not_found",
    "unresolved",
)
BROKER_STATES = (
    "working",
    "partially_filled",
    "filled",
    "canceled_expired",
    "rejected",
    "replaced",
)
UNRESOLVED_REASONS = (
    "lookup_error",
    "undocumented_not_found_response",
    "page_cap_reached",
    "unmapped_status",
    "id_mismatch",
    "execution_path_unproven",
)
EVIDENCE_ITEMS = (
    "a_client_order_id_404",
    "b_list_scan_no_match",
    "c_no_fill_reference",
    "d_no_exposure_change",
)
EVIDENCE_RESULTS = ("confirmed", "not_confirmed", "error")
STATEMENTS = ("not_received", "order_record")


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=True),
        sa.Column("paper_order_id", sa.Uuid(), nullable=True),
        sa.Column("strategy_public_id", sa.String(length=64), nullable=True),
        sa.Column("classification", sa.String(length=32), nullable=True),
        sa.Column("broker_state", sa.String(length=24), nullable=True),
        sa.Column("unresolved_reason", sa.String(length=48), nullable=True),
        sa.Column("evidence_item", sa.String(length=32), nullable=True),
        sa.Column("evidence_result", sa.String(length=16), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("statement", sa.String(length=16), nullable=True),
        sa.Column("reference", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("recorded_by", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(_in("kind", KINDS), name=op.f("ck_recovery_records_kind")),
        sa.CheckConstraint(
            "classification IS NULL OR " + _in("classification", CLASSIFICATIONS),
            name=op.f("ck_recovery_records_classification"),
        ),
        sa.CheckConstraint(
            "broker_state IS NULL OR " + _in("broker_state", BROKER_STATES),
            name=op.f("ck_recovery_records_broker_state"),
        ),
        sa.CheckConstraint(
            "unresolved_reason IS NULL OR " + _in("unresolved_reason", UNRESOLVED_REASONS),
            name=op.f("ck_recovery_records_unresolved_reason"),
        ),
        sa.CheckConstraint(
            "evidence_item IS NULL OR " + _in("evidence_item", EVIDENCE_ITEMS),
            name=op.f("ck_recovery_records_evidence_item"),
        ),
        sa.CheckConstraint(
            "evidence_result IS NULL OR " + _in("evidence_result", EVIDENCE_RESULTS),
            name=op.f("ck_recovery_records_evidence_result"),
        ),
        sa.CheckConstraint(
            "statement IS NULL OR " + _in("statement", STATEMENTS),
            name=op.f("ck_recovery_records_statement"),
        ),
        sa.CheckConstraint(
            "(kind = 'classification') = (classification IS NOT NULL)",
            name=op.f("ck_recovery_records_classification_shape"),
        ),
        sa.CheckConstraint(
            "COALESCE(classification = 'found_verified', false) = (broker_state IS NOT NULL)",
            name=op.f("ck_recovery_records_broker_state_shape"),
        ),
        sa.CheckConstraint(
            "COALESCE(classification = 'unresolved', false) = (unresolved_reason IS NOT NULL)",
            name=op.f("ck_recovery_records_unresolved_reason_shape"),
        ),
        sa.CheckConstraint(
            "(kind = 'absence_evidence') = (evidence_item IS NOT NULL)"
            " AND (kind = 'absence_evidence') = (evidence_result IS NOT NULL)"
            " AND (kind = 'absence_evidence') = (observed_at IS NOT NULL)",
            name=op.f("ck_recovery_records_absence_evidence_shape"),
        ),
        sa.CheckConstraint(
            "(kind = 'broker_statement') = (statement IS NOT NULL)"
            " AND (kind = 'broker_statement') = (reference IS NOT NULL)"
            " AND (kind = 'broker_statement') = (reason IS NOT NULL)"
            " AND (kind <> 'broker_statement'"
            " OR (btrim(reference) <> '' AND btrim(reason) <> ''))",
            name=op.f("ck_recovery_records_broker_statement_shape"),
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            name=op.f("fk_recovery_records_job_id_jobs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["paper_order_id"],
            ["paper_orders.id"],
            name=op.f("fk_recovery_records_paper_order_id_paper_orders"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_recovery_records")),
    )
    op.create_index(op.f("ix_recovery_records_job_id"), TABLE, ["job_id"])
    op.create_index(op.f("ix_recovery_records_paper_order_id"), TABLE, ["paper_order_id"])
    op.create_index(op.f("ix_recovery_records_strategy_public_id"), TABLE, ["strategy_public_id"])
    op.create_index(
        op.f("ix_recovery_records_paper_order_id_kind_created_at"),
        TABLE,
        ["paper_order_id", "kind", "created_at"],
    )
    op.create_index(
        op.f("ix_recovery_records_strategy_public_id_created_at"),
        TABLE,
        ["strategy_public_id", "created_at"],
    )
    op.create_index(
        "uq_recovery_records_one_statement_per_intent",
        TABLE,
        ["paper_order_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'broker_statement'"),
    )

    # json is not involved (no JSON columns), but compare through jsonb anyway so the
    # comparison is column-set agnostic. The ONLY permitted UPDATE is what the two
    # foreign keys' ON DELETE SET NULL perform: job_id and/or paper_order_id NOT NULL ->
    # NULL (a column that is not nulled must be unchanged), every other column unchanged.
    op.execute(
        f"""
        CREATE FUNCTION {FUNCTION}() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'recovery_records rows are append-only (DELETE rejected)'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            IF (to_jsonb(NEW) - 'job_id' - 'paper_order_id')
                   = (to_jsonb(OLD) - 'job_id' - 'paper_order_id')
               AND (NEW.job_id IS NULL OR NEW.job_id IS NOT DISTINCT FROM OLD.job_id)
               AND (NEW.paper_order_id IS NULL
                    OR NEW.paper_order_id IS NOT DISTINCT FROM OLD.paper_order_id)
               AND ((OLD.job_id IS NOT NULL AND NEW.job_id IS NULL)
                    OR (OLD.paper_order_id IS NOT NULL AND NEW.paper_order_id IS NULL)) THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'recovery_records rows are append-only (UPDATE rejected)'
                USING ERRCODE = 'integrity_constraint_violation';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {TRIGGER}
        BEFORE UPDATE OR DELETE ON {TABLE}
        FOR EACH ROW EXECUTE FUNCTION {FUNCTION}()
        """
    )


def downgrade() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS {TRIGGER} ON {TABLE}")
    op.execute(f"DROP FUNCTION IF EXISTS {FUNCTION}()")
    op.drop_index("uq_recovery_records_one_statement_per_intent", table_name=TABLE)
    op.drop_index(op.f("ix_recovery_records_strategy_public_id_created_at"), table_name=TABLE)
    op.drop_index(op.f("ix_recovery_records_paper_order_id_kind_created_at"), table_name=TABLE)
    op.drop_index(op.f("ix_recovery_records_strategy_public_id"), table_name=TABLE)
    op.drop_index(op.f("ix_recovery_records_paper_order_id"), table_name=TABLE)
    op.drop_index(op.f("ix_recovery_records_job_id"), table_name=TABLE)
    op.drop_table(TABLE)
