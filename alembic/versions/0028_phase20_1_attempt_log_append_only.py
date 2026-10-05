"""Phase 20.1 attempt log append-only + complete-once in the database (SAF-10).

The safety property "never resend after ambiguity" must not rest on application
discipline alone. Until this migration the attempt log was append-only and
complete-once only in code, and ``order_submission_attempts.paper_order_id`` was
``ON DELETE CASCADE``. Two consequences were reachable by any DB client:

* a completed ``ambiguous`` attempt could be rewritten to ``pre_connection`` (which
  the classifier treats as safe to re-send);
* deleting a ``paper_orders`` row cascaded away its attempt evidence and, through
  ``execution_operation_intents.paper_order_id ON DELETE SET NULL``, turned the intent
  into a ``planned`` one that looks re-sendable.

This revision adds, on ``order_submission_attempts``:

1. A ``BEFORE UPDATE OR DELETE ... FOR EACH ROW`` trigger. DELETE is always rejected.
   UPDATE is allowed in exactly two shapes:

   (A) the single completion of an INCOMPLETE row: ``outcome_class`` and ``completed_at``
       go from NULL to NOT NULL; ``completed_at``, ``outcome_class``, ``http_status``,
       ``error_type``, ``broker_message`` and ``updated_at`` may change, every other
       column is unchanged (so a second completion, a rewrite of a completed row and a
       change of ``started_at`` / ``attempt_number`` / the fencing columns are rejected);
   (B) the FK null-out of ``strategy_run_id`` performed by ``ON DELETE SET NULL``
       (``strategy_run_id`` NOT NULL -> NULL, every other column unchanged).

   Rejections use ``ERRCODE = integrity_constraint_violation`` (the 0026 precedent).
   Both production writers stay legal: ``DbSubmissionAttemptLog`` / ``GuardedAttemptLog``
   completion and ``operations.complete_attempt_late`` both perform shape (A).
2. ``paper_order_id`` becomes ``ON DELETE RESTRICT``: deleting an order that has attempt
   evidence fails and the evidence and the intent link survive; an order without
   attempts still deletes. ``paper_orders.strategy_run_id`` cascades from
   ``strategy_runs``, so deleting a run (or strategy) whose orders have attempts is also
   rejected; that is intended - evidence is never erased indirectly.

Downgrade drops the trigger and function and restores ``ON DELETE CASCADE``.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "0028_phase20_1_attempt_log_append_only"
down_revision = "0027_phase20_1_execution_operations"
branch_labels = None
depends_on = None

TABLE = "order_submission_attempts"
FUNCTION = "order_submission_attempts_append_only"
TRIGGER = "trg_order_submission_attempts_append_only"
FK = "fk_order_submission_attempts_paper_order_id_paper_orders"


def _recreate_paper_order_fk(ondelete: str) -> None:
    op.drop_constraint(op.f(FK), TABLE, type_="foreignkey")
    op.create_foreign_key(
        op.f(FK),
        TABLE,
        "paper_orders",
        ["paper_order_id"],
        ["id"],
        ondelete=ondelete,
    )


def upgrade() -> None:
    _recreate_paper_order_fk("RESTRICT")

    # Compare through jsonb so the comparison is column-set agnostic: a column added by a
    # later migration is automatically covered by "every other column unchanged".
    op.execute(
        f"""
        CREATE FUNCTION {FUNCTION}() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'order_submission_attempts rows are append-only (DELETE rejected)'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            -- (A) the single NULL -> outcome completion of an incomplete row.
            IF OLD.outcome_class IS NULL
               AND OLD.completed_at IS NULL
               AND NEW.outcome_class IS NOT NULL
               AND (to_jsonb(NEW) - 'completed_at' - 'outcome_class' - 'http_status'
                        - 'error_type' - 'broker_message' - 'updated_at')
                   = (to_jsonb(OLD) - 'completed_at' - 'outcome_class' - 'http_status'
                        - 'error_type' - 'broker_message' - 'updated_at') THEN
                RETURN NEW;
            END IF;
            -- (B) the strategy_run_id FK null-out (ON DELETE SET NULL).
            IF OLD.strategy_run_id IS NOT NULL
               AND NEW.strategy_run_id IS NULL
               AND (to_jsonb(NEW) - 'strategy_run_id') = (to_jsonb(OLD) - 'strategy_run_id') THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'order_submission_attempts rows are complete-once (UPDATE rejected)'
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
    _recreate_paper_order_fk("CASCADE")
