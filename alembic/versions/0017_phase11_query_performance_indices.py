"""Phase 11 query performance: EXPLAIN-confirmed index audit over critical query paths.

PERF-03 requires EXPLAIN-confirmed named index usage on the three critical query
paths (operator reads, reconciliation, order-lifecycle sync), with no full
sequential scan on a large table.

Investigation (tests/test_query_index_usage.py, seeded to ~120k rows -- well past
the Seq-Scan/Index-Scan cost crossover measured during this plan) found every
representative critical-path query ALREADY uses an existing named index at
realistic scale:

- Operator runs list / operator orders listing: ix_strategy_runs_strategy_id_status,
  ix_paper_orders_strategy_run_id_status.
- Reconciliation PaperOrder-by-strategy load (also used by order-lifecycle sync's
  local-order load): ix_paper_orders_strategy_run_id_status.
- Reconciliation / order-lifecycle-sync Position-by-strategy+status load:
  ix_positions_strategy_id_status.

The one remaining candidate (the order-lifecycle-sync broker-fill dedup query,
`select(PaperFill.broker_fill_id)` with no WHERE clause) already has a named
unique index (`uq_paper_fills_broker_fill_id`), but EXPLAIN with
`enable_seqscan=off` shows a forced Index Only Scan costs MORE than the Seq Scan
Postgres already chooses for this unconditional full-column read -- adding
another index would not change that comparison, so it is not an index gap (see
tests/test_query_index_usage.py and 11-03-SUMMARY.md for the full account).

No new index is required. This migration is therefore an intentional,
documented no-op, following the same precedent as
0016_phase8_stale_run_status.py's documented no-op downgrade: it exists to keep
the migration-numbering convention intact (0017 following 0016) and as the
durable record of this phase's EXPLAIN audit, per PERF-03's "any new index ships
via migration 0017" requirement -- there was simply nothing for it to add.

Deviation from the plan's stated revision id (Rule 1 - bug fix, verified by an
actual failure, not a stylistic choice): the plan's interfaces block specified
``revision = "0017_phase11_query_performance_indices"`` (38 characters), but
Alembic's ``alembic_version.version_num`` column is ``VARCHAR(32)`` (Alembic's
default width, unchanged by any migration in this repo) -- every other revision
id in this repo is <= 29 characters. Attempting the full 38-character id fails
at upgrade time with ``psycopg.errors.StringDataRightTruncation: value too long
for type character varying(32)``, which would break every fixture in the test
suite that upgrades to head, not just this migration. The revision id below is
shortened to fit while keeping the same 0017/phase11 prefix and clearly
matching this file's slug; ``down_revision`` is unaffected and still points at
0016's exact revision string verbatim, per the interfaces block's own
requirement.
"""

from __future__ import annotations

# revision identifiers, used by Alembic.
revision = "0017_phase11_query_perf_indices"
down_revision = "0016_phase8_stale_run_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No schema change: EXPLAIN over every critical query path (operator reads,
    # reconciliation, order-lifecycle sync), seeded to realistic scale, showed
    # each already uses an existing named index. See the module docstring above
    # and tests/test_query_index_usage.py for the evidence.
    pass


def downgrade() -> None:
    # Documented no-op: upgrade() made no schema change, so there is nothing to
    # reverse.
    pass
