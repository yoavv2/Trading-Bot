"""Phase 20.1 order origin immutability + durable evidence protection (CR-01, V-1, V-2).

CR-01: ``paper_orders.strategy_run_id`` used to be re-assigned by a retrying run, which
made an order "move" away from the Job that originated it. 20.1-26 fixed the writer
(the origin run is immutable; later registering runs are accepted ``intent_registered`` /
``retry_requested`` ``order_events`` rows). This revision makes that a DATABASE invariant,
together with the evidence it rests on.

User decisions 2026-10-05:

* durable linkage: once a ``paper_orders`` row exists its ``strategy_run_id`` (the ORIGIN
  run) cannot change;
* V-1 approved: ``order_events`` is append-only (every UPDATE rejected) and TRUNCATE of any
  evidence table is rejected, including through ``TRUNCATE ... CASCADE`` of a parent
  (PostgreSQL fires BEFORE TRUNCATE triggers on every table a CASCADE reaches);
* V-2 RETENTION POLICY CHANGE: evidence rows are no longer deletable, directly or by
  parent cascade. The earlier promise "DELETE behaviour unchanged" is withdrawn.
* [W4: reconciliation evidence, added 2026-10-05 under V-2 "recovery evidence"] the
  reconciliation facts the recovery gate reads are protected too.

FK / cascade map (short form). A parent delete cascades (ON DELETE CASCADE / SET NULL) into
evidence rows; the row-level BEFORE DELETE trigger of the child raises and aborts the whole
statement, so no FK has to be rebuilt:

* strategies -> strategy_runs (CASCADE): run guard R fires on the cascaded run.
* strategy_runs -> paper_orders / order_events (CASCADE): O / E guards plus R.
* strategy_runs -> order_submission_attempts.strategy_run_id (SET NULL): R makes it
  unreachable for any run an attempt references (the 0028 shape B stays legal, unused).
* paper_orders -> order_events / paper_fills (CASCADE), execution_operation_intents /
  recovery_records / execution_events / supersedes (SET NULL): O makes them unreachable.
* symbols -> paper_orders / paper_fills (CASCADE): the O / X guards abort the delete.
* jobs -> strategy_runs / recovery_records / execution_operation_jobs /
  account_reconciliation_runs / external_broker_activity (SET NULL): J protects the Jobs the
  recovery predicate reads; other Job types stay deletable.

Delete predicates (OLD is the row being deleted):

* O (paper_orders) = TRUE. Every order is evidence for a gate: TL-10 session allowance and
  replay detection (a broker-reaching order consumes the key in any later state, so
  deleting even a terminal filled order would re-open the key), D-07 attribution, recovery
  (an unestablished legacy zero-attempt order has no events or attempts; deleting it would
  remove the uncertainty and, through the intent link's SET NULL, make its intent planned
  and re-sendable) and the version chain. No product delete path exists.
* E (order_events) = TRUE: the registration history the recovery predicate reads.
* R (strategy_runs) = run_type IN ('paper_execution', 'reconciliation') OR referenced by
  paper_orders / order_events / order_submission_attempts. A paper_execution run keeps a
  flagged Job blocked (execution_path_unproven) even with zero orders; deleting it would
  turn the Job into nothing_submitted. [W4] 'reconciliation' runs are the newest standalone
  strategy reconciliation the gate reads.
* J (jobs) = job_type IN ('paper-session', 'broker-order-sync') OR outcome_uncertain OR
  referenced by a strategy_runs row with run_type 'reconciliation'. Uncertain Jobs and the
  effect-time boundary of broker-touching Jobs; [W4/W-N2] the strategy_latest join of the
  reconciliation gate goes through the run's Job, and a SET NULL on job_id would drop the
  run out of that join. Account-level and unreferenced reconciliation Jobs stay deletable.
* X (execution_operations, execution_operation_intents, execution_operation_jobs,
  paper_fills) = TRUE: fencing epoch / executor / basis verification, operation-bound
  evidence, Continue attribution (SAF-05) and exposure attribution.
* A (account_reconciliation_runs) = TRUE [W4]: deleting a newer dirty run would let an older
  clean one release the gate. account_snapshots are deliberately NOT guarded (open user
  decision).

0028 objects untouched; triggers only (no FK, column, table or data change). Downgrade drops
exactly what this revision created. ERRCODE 'integrity_constraint_violation' (0026/0028).
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "0029_phase20_1_order_origin_immutable"
down_revision = "0028_phase20_1_attempt_log_append_only"
branch_labels = None
depends_on = None

ORIGIN_FUNCTION = "paper_orders_origin_run_immutable"
ORIGIN_TRIGGER = "trg_paper_orders_origin_run_immutable"
LEDGER_FUNCTION = "order_events_append_only"
LEDGER_TRIGGER = "trg_order_events_append_only"
DELETE_FUNCTION = "phase20_1_evidence_no_delete"
TRUNCATE_FUNCTION = "phase20_1_evidence_no_truncate"

DELETE_GUARDED_TABLES = (
    "paper_orders",
    "order_events",
    "strategy_runs",
    "jobs",
    "execution_operations",
    "execution_operation_intents",
    "execution_operation_jobs",
    "paper_fills",
    "account_reconciliation_runs",
)
TRUNCATE_GUARDED_TABLES = (
    "order_submission_attempts",
    "paper_orders",
    "order_events",
    "paper_fills",
    "strategy_runs",
    "jobs",
    "execution_operations",
    "execution_operation_intents",
    "execution_operation_jobs",
    "recovery_records",
    "external_broker_activity",
    "account_reconciliation_runs",
)


def _origin_guard() -> None:
    op.execute(
        f"""
        CREATE FUNCTION {ORIGIN_FUNCTION}() RETURNS trigger AS $$
        BEGIN
            IF NEW.strategy_run_id IS DISTINCT FROM OLD.strategy_run_id THEN
                RAISE EXCEPTION
                    'paper_orders.strategy_run_id is the immutable origin run (UPDATE rejected)'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {ORIGIN_TRIGGER}
        BEFORE UPDATE OF strategy_run_id ON paper_orders
        FOR EACH ROW EXECUTE FUNCTION {ORIGIN_FUNCTION}()
        """
    )


def _drop_origin_guard() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS {ORIGIN_TRIGGER} ON paper_orders")
    op.execute(f"DROP FUNCTION IF EXISTS {ORIGIN_FUNCTION}()")


def _ledger_update_guard() -> None:
    op.execute(
        f"""
        CREATE FUNCTION {LEDGER_FUNCTION}() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'order_events rows are append-only (UPDATE rejected)'
                USING ERRCODE = 'integrity_constraint_violation';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {LEDGER_TRIGGER}
        BEFORE UPDATE ON order_events
        FOR EACH ROW EXECUTE FUNCTION {LEDGER_FUNCTION}()
        """
    )


def _drop_ledger_update_guard() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS {LEDGER_TRIGGER} ON order_events")
    op.execute(f"DROP FUNCTION IF EXISTS {LEDGER_FUNCTION}()")


def _evidence_delete_guards() -> None:
    op.execute(
        f"""
        CREATE FUNCTION {DELETE_FUNCTION}() RETURNS trigger AS $$
        DECLARE
            protected boolean := FALSE;
        BEGIN
            IF TG_TABLE_NAME = 'strategy_runs' THEN
                protected := OLD.run_type::text IN ('paper_execution', 'reconciliation')
                    OR EXISTS (SELECT 1 FROM paper_orders WHERE strategy_run_id = OLD.id)
                    OR EXISTS (SELECT 1 FROM order_events WHERE strategy_run_id = OLD.id)
                    OR EXISTS (
                        SELECT 1 FROM order_submission_attempts WHERE strategy_run_id = OLD.id
                    );
            ELSIF TG_TABLE_NAME = 'jobs' THEN
                protected := OLD.job_type IN ('paper-session', 'broker-order-sync')
                    OR OLD.outcome_uncertain
                    OR EXISTS (
                        SELECT 1 FROM strategy_runs
                        WHERE job_id = OLD.id AND run_type::text = 'reconciliation'
                    );
            ELSE
                -- paper_orders (O), order_events (E), execution_operations,
                -- execution_operation_intents, execution_operation_jobs, paper_fills (X),
                -- account_reconciliation_runs (A): every row is evidence.
                protected := TRUE;
            END IF;
            IF protected THEN
                RAISE EXCEPTION '% rows are evidence and cannot be deleted (retention policy)',
                    TG_TABLE_NAME
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN OLD;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table in DELETE_GUARDED_TABLES:
        op.execute(
            f"""
            CREATE TRIGGER trg_{table}_no_delete
            BEFORE DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION {DELETE_FUNCTION}()
            """
        )


def _drop_evidence_delete_guards() -> None:
    for table in reversed(DELETE_GUARDED_TABLES):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_no_delete ON {table}")
    op.execute(f"DROP FUNCTION IF EXISTS {DELETE_FUNCTION}()")


def _truncate_guards() -> None:
    op.execute(
        f"""
        CREATE FUNCTION {TRUNCATE_FUNCTION}() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION '% is evidence and cannot be truncated (retention policy)',
                TG_TABLE_NAME
                USING ERRCODE = 'integrity_constraint_violation';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table in TRUNCATE_GUARDED_TABLES:
        op.execute(
            f"""
            CREATE TRIGGER trg_{table}_no_truncate
            BEFORE TRUNCATE ON {table}
            FOR EACH STATEMENT EXECUTE FUNCTION {TRUNCATE_FUNCTION}()
            """
        )


def _drop_truncate_guards() -> None:
    for table in reversed(TRUNCATE_GUARDED_TABLES):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_no_truncate ON {table}")
    op.execute(f"DROP FUNCTION IF EXISTS {TRUNCATE_FUNCTION}()")


def upgrade() -> None:
    _origin_guard()
    _ledger_update_guard()
    _evidence_delete_guards()
    _truncate_guards()


def downgrade() -> None:
    _drop_truncate_guards()
    _drop_evidence_delete_guards()
    _drop_ledger_update_guard()
    _drop_origin_guard()
