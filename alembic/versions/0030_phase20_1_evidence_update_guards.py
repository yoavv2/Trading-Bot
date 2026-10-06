"""Phase 20.1 UPDATE guards for recovery and reconciliation evidence (review WR-01, gap G-2).

0028 made the attempt log append-only and 0029 made order origin immutable and every evidence
row undeletable. Both left the UPDATE surface of the run and Job rows the recovery gate reads
open: a plain ``UPDATE`` could detach or re-scope a run, disqualify or forge a standalone
reconciliation through its Job's type, or rewrite a completed reconciliation result clean.

User decision 3 (2026-10-06), completed by the user's correction of 2026-10-06: ``strategy_id`` on
evidence runs and the Job type behind a standalone reconciliation are IN scope, because each can
make a newer dirty reconciliation disappear from the gate's query (the gate selects the strategy
through ``strategy_runs.strategy_id`` and needs ``rj.job_type = 'reconciliation'`` through
``strategy_runs.job_id``).

Threat model: any database client with ordinary DML (a hand-run UPDATE, a stray script), the
same class as 0028 / 0029. No product writer performs the refused updates.

Four guards (triggers and functions only; ERRCODE ``integrity_constraint_violation``, SQLSTATE
23000, as in 0026 / 0028 / 0029). OLD is the row before the update, NEW the row after it.

1. ``trg_strategy_runs_link_immutable`` (``BEFORE UPDATE OF job_id, run_type, strategy_id``).
   When any of the three changes and the run is EVIDENCE-BEARING, the UPDATE is refused.
   Evidence-bearing is the 0029 delete predicate R, extended to the NEW type so no run can be
   converted into evidence: ``run_type`` of OLD or NEW is ``paper_execution`` / ``reconciliation``,
   or a ``paper_orders`` / ``order_events`` / ``order_submission_attempts`` row references the run.
   There is NO exception for the foreign key ``ON DELETE SET NULL``: a Job delete that would detach
   an evidence run aborts as a whole (the user approved this on 2026-10-06, whatever the Job type).
   It removes no product path: every evidence run the product creates hangs off a Job that 0029 J
   already makes undeletable (a paper-session Job, or a ``reconciliation`` Job referenced by a
   reconciliation run). Non-evidence runs keep any change, including the SET NULL.
2. ``trg_strategy_runs_reconciliation_complete_once`` (``BEFORE UPDATE`` of a run whose OLD
   ``run_type`` is ``reconciliation``). ``trigger_source`` never changes (the gate filters on it).
   Once the run is complete (``completed_at`` set, or status ``succeeded`` / ``failed`` /
   ``stale``) ``status``, ``completed_at``, ``started_at``, ``error_message``, ``result_summary``
   and ``parameters_snapshot`` never change. A pending run accepts its single completion write.
3. ``trg_account_reconciliation_runs_complete_once`` (``BEFORE UPDATE``). ``trigger_source`` and
   ``scope`` never change. Once the run is complete (``completed_at`` set, or status ``succeeded``
   / ``failed``) ``status``, ``completed_at``, ``started_at``, ``as_of_session``,
   ``blocks_execution``, ``finding_count``, ``blocking_count``, ``error_message``, ``findings``,
   ``account_divergence``, ``unexplained_exposure``, ``classification_summary``,
   ``unresolved_reasons`` and ``result_summary`` never change. ``job_id`` is deliberately NOT
   frozen: no gate reads it and its ``ON DELETE SET NULL`` must keep working.
4. ``trg_jobs_reconciliation_job_type_immutable`` (``BEFORE UPDATE OF job_type ... WHEN
   OLD.job_type IS DISTINCT FROM NEW.job_type``). Every actual ``job_type`` change is refused when
   OLD or NEW ``job_type`` is ``'reconciliation'`` (``services/broker_jobs.py``
   ``RECONCILIATION_JOB_TYPE``): a queued, running or completed reconciliation Job of either scope
   never changes type, and no Job becomes one. These column arms decide without reading any table,
   so they hold before the Job's run exists and while its INSERT is uncommitted. In addition a Job
   of ANY type referenced by a ``strategy_runs`` row with ``run_type`` ``reconciliation`` and
   ``trigger_source`` ``'job'`` (``reconciliation/latest.py`` ``STANDALONE_TRIGGER_SOURCE``) never
   changes type. Away from ``reconciliation`` would drop a newer dirty run from the gate's query,
   into ``reconciliation`` would make a run qualify. The guard fires only on a ``job_type`` change,
   so no other Job column is frozen and every Job lifecycle write (claim, lease, progress,
   completion, outcome, cancellation) is untouched; a change between two other types of a Job no
   standalone run references stays legal. In-session reconciliation runs (trigger
   ``<x>_reconciliation``) never qualify whatever their Job's type and are not covered.

E1 correction (user decision 2026-10-06, plan 20.1-38; this revision was amended in place while it
was unpublished and had run only on throwaway databases): the round-3 review (WR-01) and the
round-4 re-verification (escalation E-1) showed guard 4 holding only for a COMMITTED run row found
through the caller's search path: a retype before the run INSERT, during the uncommitted INSERT, or
under a session TEMP table named ``strategy_runs`` passed. Hence the column arms of guard 4, and
trusted lookups in every function of this revision and in the 0029 delete guard:

* every function here runs with ``SET search_path = pg_catalog, public, pg_temp`` (``public`` is the
  application schema; an omitted ``pg_temp`` is searched FIRST for relation names, so it is listed
  explicitly LAST) and names every table ``public.<table>``, so neither a TEMP table nor a
  caller-owned schema answers a guard's lookup, whatever search path the caller set;
* every compared enum or varchar value is cast to ``text``: PUBLIC holds CREATE on ``public`` on
  PostgreSQL 14, and an ``=`` planted there for the exact column type would otherwise win operator
  resolution against pg_catalog's (pg_catalog is searched first, but an exact argument-type match
  beats a coercion); ``text``, ``uuid``, ``timestamptz``, ``date``, ``boolean``, ``integer`` and
  ``jsonb`` comparisons resolve to pg_catalog's own exact operators;
* ``phase20_1_evidence_no_delete`` (0029; the only 0028 / 0029 function that reads tables) gets the
  same search path through ``ALTER FUNCTION``. Its body is 0029's, unchanged; the three other 0029
  functions and the 0028 function read no table and keep their configuration. Downgrade RESETs
  exactly that setting, which restores the 0029 function as 0029 created it.

No function is SECURITY DEFINER. JSON columns have no equality operator in PostgreSQL, so every
comparison casts both sides to jsonb; an explicit column list is compared (never the whole row), so
``created_at`` and ``updated_at`` (the ORM touches the latter on every flush) never matter.

Legitimate writers keep working (inventory of src / scripts at the planning base, re-run at
execution): the single pending -> completed write of ``reconcile_paper_execution``
(``report._update_reconciliation_run``) and of ``reconcile_account`` (``account._finalize_run``),
success and failure paths (the failure write runs only when the success write did not commit);
``stale_runs.reclaim_stale_runs`` (``paper_execution`` runs only); every completion write of the
other run types; every Job lifecycle write; ``account_reconciliation_runs.job_id`` and its SET NULL;
a Job delete that SET NULLs a non-evidence run. ``strategy_runs.job_id`` / ``run_type`` /
``strategy_id`` are written only by constructors; the one post-INSERT writer of ``job_id`` is
the database foreign key itself; no writer changes ``jobs.job_type``.

Deliberately NOT covered (OPEN user decisions, listed in the 20.1-37 deferred section):
``jobs.outcome_uncertain``, ``jobs.completed_at`` and ``jobs.job_type`` of flagged or effect Jobs
that are not reconciliation Jobs (they decide which Jobs are uncertain and where the effect
boundary lies), ``jobs.payload`` (a Job's strategy attribution) and a rename of
``strategies.strategy_id`` (the public id), a forged clean reconciliation INSERT by a database client
(and, the same class, deleting a run-less reconciliation Job and re-inserting its id with another
type), ``account_snapshots``.

IN-02 (kept separate and documented): the table-owning role, which is the role that runs
migrations and today also the application role, can ``ALTER TABLE ... DISABLE TRIGGER``, drop a
trigger or replace a function. These guards stop ordinary DML only; runtime / migration role
separation (and revoking PUBLIC's CREATE on ``public``) is outside this phase.

The 0028 and 0029 source files are unchanged. Triggers and functions only (no foreign key, column,
table or data change). Downgrade drops exactly the 4 triggers and 4 functions created here and resets
the search path set on the 0029 delete guard.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "0030_phase20_1_evidence_update_guards"
down_revision = "0029_phase20_1_order_origin_immutable"
branch_labels = None
depends_on = None

LINK_FUNCTION = "phase20_1_strategy_run_link_immutable"
LINK_TRIGGER = "trg_strategy_runs_link_immutable"
RECONCILIATION_RUN_FUNCTION = "phase20_1_reconciliation_run_complete_once"
RECONCILIATION_RUN_TRIGGER = "trg_strategy_runs_reconciliation_complete_once"
ACCOUNT_RUN_FUNCTION = "phase20_1_account_reconciliation_run_complete_once"
ACCOUNT_RUN_TRIGGER = "trg_account_reconciliation_runs_complete_once"
JOB_TYPE_FUNCTION = "phase20_1_reconciliation_job_type_immutable"
JOB_TYPE_TRIGGER = "trg_jobs_reconciliation_job_type_immutable"
#: The 0029 delete guard: the only earlier guard function that reads tables.
DELETE_GUARD_0029_FUNCTION = "phase20_1_evidence_no_delete"

#: The application schema, then pg_temp explicitly LAST (never searched first).
TRUSTED_SEARCH_PATH = "pg_catalog, public, pg_temp"


def _link_guard() -> None:
    op.execute(
        f"""
        CREATE FUNCTION {LINK_FUNCTION}() RETURNS trigger AS $$
        BEGIN
            IF NEW.job_id IS DISTINCT FROM OLD.job_id
               OR NEW.run_type::text IS DISTINCT FROM OLD.run_type::text
               OR NEW.strategy_id IS DISTINCT FROM OLD.strategy_id THEN
                IF OLD.run_type::text IN ('paper_execution', 'reconciliation')
                   OR NEW.run_type::text IN ('paper_execution', 'reconciliation')
                   OR EXISTS (SELECT 1 FROM public.paper_orders WHERE strategy_run_id = OLD.id)
                   OR EXISTS (SELECT 1 FROM public.order_events WHERE strategy_run_id = OLD.id)
                   OR EXISTS (
                       SELECT 1 FROM public.order_submission_attempts
                       WHERE strategy_run_id = OLD.id
                   ) THEN
                    RAISE EXCEPTION
                        'strategy_runs evidence link is immutable (job_id / run_type / strategy_id UPDATE rejected)'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql SET search_path = {TRUSTED_SEARCH_PATH}
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {LINK_TRIGGER}
        BEFORE UPDATE OF job_id, run_type, strategy_id ON strategy_runs
        FOR EACH ROW EXECUTE FUNCTION {LINK_FUNCTION}()
        """
    )


def _drop_link_guard() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS {LINK_TRIGGER} ON strategy_runs")
    op.execute(f"DROP FUNCTION IF EXISTS {LINK_FUNCTION}()")


def _reconciliation_run_guard() -> None:
    op.execute(
        f"""
        CREATE FUNCTION {RECONCILIATION_RUN_FUNCTION}() RETURNS trigger AS $$
        BEGIN
            IF NEW.trigger_source::text IS DISTINCT FROM OLD.trigger_source::text THEN
                RAISE EXCEPTION 'reconciliation run is complete-once (trigger_source is immutable)'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            IF OLD.completed_at IS NOT NULL
               OR OLD.status::text IN ('succeeded', 'failed', 'stale') THEN
                IF NEW.status::text IS DISTINCT FROM OLD.status::text
                   OR NEW.completed_at IS DISTINCT FROM OLD.completed_at
                   OR NEW.started_at IS DISTINCT FROM OLD.started_at
                   OR NEW.error_message IS DISTINCT FROM OLD.error_message
                   OR NEW.result_summary::jsonb IS DISTINCT FROM OLD.result_summary::jsonb
                   OR NEW.parameters_snapshot::jsonb IS DISTINCT FROM OLD.parameters_snapshot::jsonb
                THEN
                    RAISE EXCEPTION
                        'reconciliation run is complete-once (UPDATE of a completed run rejected)'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql SET search_path = {TRUSTED_SEARCH_PATH}
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {RECONCILIATION_RUN_TRIGGER}
        BEFORE UPDATE ON strategy_runs
        FOR EACH ROW WHEN (OLD.run_type::text = 'reconciliation')
        EXECUTE FUNCTION {RECONCILIATION_RUN_FUNCTION}()
        """
    )


def _drop_reconciliation_run_guard() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS {RECONCILIATION_RUN_TRIGGER} ON strategy_runs")
    op.execute(f"DROP FUNCTION IF EXISTS {RECONCILIATION_RUN_FUNCTION}()")


def _account_run_guard() -> None:
    op.execute(
        f"""
        CREATE FUNCTION {ACCOUNT_RUN_FUNCTION}() RETURNS trigger AS $$
        BEGIN
            IF NEW.trigger_source::text IS DISTINCT FROM OLD.trigger_source::text
               OR NEW.scope::text IS DISTINCT FROM OLD.scope::text THEN
                RAISE EXCEPTION
                    'account reconciliation run is complete-once (trigger_source / scope are immutable)'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            IF OLD.completed_at IS NOT NULL OR OLD.status::text IN ('succeeded', 'failed') THEN
                IF NEW.status::text IS DISTINCT FROM OLD.status::text
                   OR NEW.completed_at IS DISTINCT FROM OLD.completed_at
                   OR NEW.started_at IS DISTINCT FROM OLD.started_at
                   OR NEW.as_of_session IS DISTINCT FROM OLD.as_of_session
                   OR NEW.blocks_execution IS DISTINCT FROM OLD.blocks_execution
                   OR NEW.finding_count IS DISTINCT FROM OLD.finding_count
                   OR NEW.blocking_count IS DISTINCT FROM OLD.blocking_count
                   OR NEW.error_message IS DISTINCT FROM OLD.error_message
                   OR NEW.findings::jsonb IS DISTINCT FROM OLD.findings::jsonb
                   OR NEW.account_divergence::jsonb IS DISTINCT FROM OLD.account_divergence::jsonb
                   OR NEW.unexplained_exposure::jsonb IS DISTINCT FROM OLD.unexplained_exposure::jsonb
                   OR NEW.classification_summary::jsonb IS DISTINCT FROM OLD.classification_summary::jsonb
                   OR NEW.unresolved_reasons::jsonb IS DISTINCT FROM OLD.unresolved_reasons::jsonb
                   OR NEW.result_summary::jsonb IS DISTINCT FROM OLD.result_summary::jsonb
                THEN
                    RAISE EXCEPTION
                        'account reconciliation run is complete-once (UPDATE of a completed run rejected)'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql SET search_path = {TRUSTED_SEARCH_PATH}
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {ACCOUNT_RUN_TRIGGER}
        BEFORE UPDATE ON account_reconciliation_runs
        FOR EACH ROW EXECUTE FUNCTION {ACCOUNT_RUN_FUNCTION}()
        """
    )


def _drop_account_run_guard() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS {ACCOUNT_RUN_TRIGGER} ON account_reconciliation_runs")
    op.execute(f"DROP FUNCTION IF EXISTS {ACCOUNT_RUN_FUNCTION}()")


def _job_type_guard() -> None:
    op.execute(
        f"""
        CREATE FUNCTION {JOB_TYPE_FUNCTION}() RETURNS trigger AS $$
        BEGIN
            -- A reconciliation Job never changes type and no Job becomes one: decided from the
            -- row alone (no table read), so no visibility or lock window exists.
            IF OLD.job_type::text = 'reconciliation' OR NEW.job_type::text = 'reconciliation' THEN
                RAISE EXCEPTION
                    'job_type of a reconciliation Job or of a Job behind a standalone reconciliation run is immutable'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            -- A Job of any other type that a standalone reconciliation run already references.
            IF EXISTS (
                SELECT 1 FROM public.strategy_runs sr
                WHERE sr.job_id = OLD.id
                  AND sr.run_type::text = 'reconciliation'
                  AND sr.trigger_source::text = 'job'
            ) THEN
                RAISE EXCEPTION
                    'job_type of a reconciliation Job or of a Job behind a standalone reconciliation run is immutable'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql SET search_path = {TRUSTED_SEARCH_PATH}
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {JOB_TYPE_TRIGGER}
        BEFORE UPDATE OF job_type ON jobs
        FOR EACH ROW WHEN (OLD.job_type IS DISTINCT FROM NEW.job_type)
        EXECUTE FUNCTION {JOB_TYPE_FUNCTION}()
        """
    )


def _drop_job_type_guard() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS {JOB_TYPE_TRIGGER} ON jobs")
    op.execute(f"DROP FUNCTION IF EXISTS {JOB_TYPE_FUNCTION}()")


def _pin_0029_delete_guard_search_path() -> None:
    op.execute(
        f"ALTER FUNCTION {DELETE_GUARD_0029_FUNCTION}() SET search_path = {TRUSTED_SEARCH_PATH}"
    )


def _reset_0029_delete_guard_search_path() -> None:
    op.execute(f"ALTER FUNCTION {DELETE_GUARD_0029_FUNCTION}() RESET search_path")


def upgrade() -> None:
    _link_guard()
    _reconciliation_run_guard()
    _account_run_guard()
    _job_type_guard()
    _pin_0029_delete_guard_search_path()


def downgrade() -> None:
    _reset_0029_delete_guard_search_path()
    _drop_job_type_guard()
    _drop_account_run_guard()
    _drop_reconciliation_run_guard()
    _drop_link_guard()
