"""EXPLAIN-based proof that critical query paths use a named index (PERF-03).

Seeds a large volume of rows (empirically past the Seq-Scan / Index-Scan cost
crossover, see the module docstring below on ``_ROW_VOLUME_RATIONALE``) into one
throwaway Postgres database, runs ``ANALYZE`` so planner statistics reflect that
volume, then EXPLAINs the exact ORM statements the operator-reads, reconciliation,
and order-lifecycle-sync services execute.

Why bulk-seed instead of a handful of rows: Postgres correctly prefers a Seq Scan
over an index on a tiny table (the whole table fits in a couple of pages, so a
sequential read is cheaper than any index probe). Proving "this query uses a named
index and not a full scan" therefore only means something once the table is large
enough that the cost model's own numbers would favor an index -- otherwise a
passing assertion would be accidental, and a "seq scan" finding would be a false
positive (the planner is right to Seq Scan a tiny table). All seeding here is
INSERT/ANALYZE against a scratch database created and dropped by this module.

Investigation performed before writing this test (manual EXPLAIN against a
throwaway DB seeded with a matching shape) found the following ground truth, which
this test suite encodes directly rather than asserting hypotheses from the plan
that turned out not to hold:

- Operator runs list / operator orders listing / reconciliation PaperOrder-by-
  strategy / reconciliation Position-by-strategy+status / order-lifecycle-sync
  open-position load: all use an existing named index once the table holds tens
  of thousands of rows. The reconciliation PaperOrder-by-strategy join in
  particular flips from Seq Scan (~4k total paper_orders) to Index Scan via the
  existing ``ix_paper_orders_strategy_run_id_status`` index (~40k total
  paper_orders) purely because of table-size growth -- no new index was needed,
  the existing composite index already covers it once the planner's own cost
  model prefers it.
- The order-lifecycle-sync broker-fill dedup query is selective to the current
  broker batch. At the same ~40k-row history volume, its
  ``WHERE broker_fill_id IN (...)`` predicate uses the existing named unique
  index ``uq_paper_fills_broker_fill_id`` and never Seq Scans ``paper_fills``.

Net result: every genuine gap this plan set out to find turned out, on actual
EXPLAIN evidence, to already be covered by an existing named index once seeded
at realistic scale. No Task-1 case below is marked ``xfail`` -- there was nothing
for a migration to fix. See 11-03-SUMMARY.md for the full account.
"""

from __future__ import annotations

import os
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from alembic import command
from sqlalchemy import select, text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.migrate import build_alembic_config

from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.db.models import (
    PaperFill,
    PaperOrder,
    Position,
    Strategy,
    StrategyRun,
)
from trading_platform.db.session import clear_engine_cache, get_engine, session_scope

# Row volumes chosen with empirical margin past the observed Seq-Scan -> Index-Scan
# cost crossover for the reconciliation PaperOrder-by-strategy query (crossover
# measured, on Postgres 14 default cost settings, between 4,050 and 40,050 total
# paper_orders rows: 4,050 total -> Seq Scan; 40,050 total -> Index Scan via the
# existing ix_paper_orders_strategy_run_id_status index). NOISE_STRATEGY_COUNT *
# NOISE_RUNS_PER_STRATEGY provides ~40k "other strategy" rows so the target
# strategy's own rows stay a small, realistic, selective slice (~0.1%) of the
# table, exactly like a real multi-strategy deployment.
NOISE_STRATEGY_COUNT = 20
NOISE_RUNS_PER_STRATEGY = 2000
TARGET_RUN_COUNT = 50
NOISE_POSITIONS_PER_STRATEGY = 150
TARGET_POSITION_COUNT = 50
TARGET_STRATEGY_ID = "trend_following_daily"


def _admin_connection_settings() -> dict[str, str]:
    return {
        "host": os.getenv("TRADING_PLATFORM_DATABASE__HOST", "localhost"),
        "port": os.getenv("TRADING_PLATFORM_DATABASE__PORT", "5432"),
        "user": os.getenv("TRADING_PLATFORM_DATABASE__USER", "trading_platform"),
        "password": os.getenv("TRADING_PLATFORM_DATABASE__PASSWORD", "trading_platform"),
        "dbname": os.getenv("TRADING_PLATFORM_ADMIN_DB", "postgres"),
    }


def _connect_admin(params: dict[str, str] | None = None) -> psycopg.Connection:
    params = params or _admin_connection_settings()
    return psycopg.connect(
        host=params["host"],
        port=params["port"],
        user=params["user"],
        password=params["password"],
        dbname=params["dbname"],
        autocommit=True,
    )


def _set_database_env(database_name: str) -> dict[str, str | None]:
    params = _admin_connection_settings()
    overrides = {
        "TRADING_PLATFORM_DATABASE__HOST": params["host"],
        "TRADING_PLATFORM_DATABASE__PORT": params["port"],
        "TRADING_PLATFORM_DATABASE__USER": params["user"],
        "TRADING_PLATFORM_DATABASE__PASSWORD": params["password"],
        "TRADING_PLATFORM_DATABASE__NAME": database_name,
    }
    previous = {key: os.environ.get(key) for key in overrides}
    os.environ.update(overrides)
    return previous


def _restore_env(previous: dict[str, str | None]) -> None:
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def _seed_bulk_data(settings) -> None:
    """Bulk-insert via SQL (not per-row ORM inserts) so seeding ~120k rows is fast."""
    with session_scope(settings) as session:
        session.execute(
            text(
                """
                INSERT INTO strategies
                    (id, strategy_id, display_name, version, status, config_reference,
                     universe_symbols, settings_snapshot, created_at, updated_at)
                VALUES
                    (gen_random_uuid(), :target_strategy_id, 'Trend Following Daily', 'v1',
                     'active', 'cfg.yaml', '[]'::json, '{}'::json, now(), now())
                """
            ),
            {"target_strategy_id": TARGET_STRATEGY_ID},
        )
        session.execute(
            text(
                """
                INSERT INTO strategies
                    (id, strategy_id, display_name, version, status, config_reference,
                     universe_symbols, settings_snapshot, created_at, updated_at)
                SELECT gen_random_uuid(), 'noise_strategy_' || g, 'Noise ' || g, 'v1',
                       'active', 'cfg.yaml', '[]'::json, '{}'::json, now(), now()
                FROM generate_series(1, :noise_count) g
                """
            ),
            {"noise_count": NOISE_STRATEGY_COUNT},
        )
        session.execute(
            text(
                "INSERT INTO symbols (id, ticker, active, created_at, updated_at) "
                "VALUES (gen_random_uuid(), 'AAPL', true, now(), now())"
            )
        )
        session.execute(
            text(
                """
                INSERT INTO strategy_runs
                    (id, strategy_id, run_type, status, trigger_source, started_at, completed_at,
                     parameters_snapshot, result_summary, error_message, created_at, updated_at)
                SELECT gen_random_uuid(), s.id, 'paper_execution', 'succeeded', 'seed',
                       now() - (g || ' minutes')::interval,
                       now() - (g || ' minutes')::interval + interval '5 minutes',
                       '{}'::json, '{}'::json, NULL, now(), now()
                FROM strategies s, generate_series(1, :target_runs) g
                WHERE s.strategy_id = :target_strategy_id
                """
            ),
            {"target_runs": TARGET_RUN_COUNT, "target_strategy_id": TARGET_STRATEGY_ID},
        )
        session.execute(
            text(
                """
                INSERT INTO strategy_runs
                    (id, strategy_id, run_type, status, trigger_source, started_at, completed_at,
                     parameters_snapshot, result_summary, error_message, created_at, updated_at)
                SELECT gen_random_uuid(), s.id, 'paper_execution', 'succeeded', 'seed',
                       now() - (g || ' minutes')::interval,
                       now() - (g || ' minutes')::interval + interval '5 minutes',
                       '{}'::json, '{}'::json, NULL, now(), now()
                FROM strategies s, generate_series(1, :noise_runs) g
                WHERE s.strategy_id LIKE 'noise_strategy_%'
                """
            ),
            {"noise_runs": NOISE_RUNS_PER_STRATEGY},
        )
        session.execute(
            text(
                """
                INSERT INTO risk_events
                    (id, strategy_run_id, symbol_id, session_date, signal_direction, signal_reason,
                     outcome, decision_code, decision_reason, reference_price, proposed_quantity,
                     proposed_notional, risk_metadata, created_at, updated_at)
                SELECT gen_random_uuid(), sr.id, (SELECT id FROM symbols WHERE ticker = 'AAPL'),
                       current_date, 'long', 'trend_entry', 'approved', 'approved', 'seed',
                       120.0, 10.0, 1200.0, '{}'::json, now(), now()
                FROM strategy_runs sr
                """
            )
        )
        session.execute(
            text(
                """
                INSERT INTO paper_orders (
                    id, strategy_run_id, source_risk_event_id, symbol_id, intended_session_date,
                    side, quantity, order_type, time_in_force, intent_hash, intent_version,
                    client_order_id, broker_order_id, status, broker_status, submitted_at,
                    submission_attempt_count, sync_failure_count, last_submission_attempt_at,
                    last_sync_failure_at, last_submission_error, last_sync_error, filled_at,
                    canceled_at, last_broker_update_at, last_synced_at, broker_payload,
                    created_at, updated_at
                )
                SELECT
                    gen_random_uuid(), re.strategy_run_id, re.id, re.symbol_id, current_date,
                    'buy', 10.0, 'market', 'day', md5(re.id::text), 1,
                    'client-' || re.id::text, 'broker-' || re.id::text, 'submitted', 'new',
                    now(), 1, 0, now(), NULL, NULL, NULL, NULL, NULL, now(), now(),
                    '{}'::json, now(), now()
                FROM risk_events re
                """
            )
        )
        session.execute(
            text(
                """
                INSERT INTO paper_fills
                    (id, paper_order_id, symbol_id, broker_fill_id, broker_order_id, side,
                     quantity, price, filled_at, broker_payload, created_at, updated_at)
                SELECT gen_random_uuid(), po.id, po.symbol_id, 'fill-' || po.id::text,
                       po.broker_order_id, 'buy', 10.0, 120.5, now(), '{}'::json, now(), now()
                FROM paper_orders po
                """
            )
        )
        session.execute(
            text(
                """
                INSERT INTO positions
                    (id, strategy_id, symbol_id, status, quantity, average_entry_price,
                     cost_basis, opened_session_date, closed_session_date, opened_at,
                     closed_at, created_at, updated_at)
                SELECT gen_random_uuid(), s.id, (SELECT id FROM symbols WHERE ticker = 'AAPL'),
                       'open', 10.0, 120.0, 1200.0, current_date, NULL, now(), NULL, now(), now()
                FROM strategies s, generate_series(1, :target_positions) g
                WHERE s.strategy_id = :target_strategy_id
                """
            ),
            {"target_positions": TARGET_POSITION_COUNT, "target_strategy_id": TARGET_STRATEGY_ID},
        )
        session.execute(
            text(
                """
                INSERT INTO positions
                    (id, strategy_id, symbol_id, status, quantity, average_entry_price,
                     cost_basis, opened_session_date, closed_session_date, opened_at,
                     closed_at, created_at, updated_at)
                SELECT gen_random_uuid(), s.id, (SELECT id FROM symbols WHERE ticker = 'AAPL'),
                       'open', 10.0, 120.0, 1200.0, current_date, NULL, now(), NULL, now(), now()
                FROM strategies s, generate_series(1, :noise_positions) g
                WHERE s.strategy_id LIKE 'noise_strategy_%'
                """
            ),
            {"noise_positions": NOISE_POSITIONS_PER_STRATEGY},
        )

    with session_scope(settings) as session:
        for table in ("strategies", "strategy_runs", "risk_events", "paper_orders", "paper_fills", "positions"):
            session.execute(text(f"ANALYZE {table}"))


@pytest.fixture(scope="module")
def seeded_index_db() -> Iterator[str]:
    """One throwaway Postgres DB, migrated to head and bulk-seeded once per module.

    Module-scoped (not per-test): seeding ~120k rows and analyzing is the expensive
    part, and every test in this module only reads this fixed dataset via EXPLAIN
    (no test mutates rows), so sharing one seed across all tests is safe and keeps
    the suite fast.
    """
    database_name = f"query_index_usage_{uuid.uuid4().hex[:8]}"
    admin_params = _admin_connection_settings()

    try:
        with _connect_admin(admin_params) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f'CREATE DATABASE "{database_name}"')
    except psycopg.Error as exc:  # pragma: no cover - exercised when local Postgres is unavailable
        pytest.fail(
            "PostgreSQL is required for tests/test_query_index_usage.py. "
            "Start the local db service first (for example `docker compose up -d db`). "
            f"Connection error: {exc}"
        )

    previous_env = _set_database_env(database_name)
    clear_settings_cache()
    clear_engine_cache()
    command.upgrade(build_alembic_config(), "head")

    settings = load_settings()
    engine = get_engine(settings)
    if engine.dialect.name != "postgresql":
        # EXPLAIN plan text below (Index Scan / Seq Scan) is Postgres-specific.
        clear_settings_cache()
        clear_engine_cache()
        _restore_env(previous_env)
        pytest.skip("tests/test_query_index_usage.py requires the Postgres dialect for EXPLAIN plan text.")

    _seed_bulk_data(settings)

    try:
        yield database_name
    finally:
        clear_settings_cache()
        clear_engine_cache()
        _restore_env(previous_env)
        with _connect_admin(admin_params) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT pg_terminate_backend(pid)
                    FROM pg_stat_activity
                    WHERE datname = %s
                      AND usename = current_user
                      AND pid <> pg_backend_pid()
                    """,
                    (database_name,),
                )
                cursor.execute(f'DROP DATABASE IF EXISTS "{database_name}"')


def _explain_plan(session, stmt) -> str:
    compiled = stmt.compile(bind=session.get_bind(), compile_kwargs={"literal_binds": True})
    rows = session.execute(text("EXPLAIN " + str(compiled))).scalars().all()
    return "\n".join(rows)


def assert_uses_index(session, stmt, *, large_tables: tuple[str, ...]) -> str:
    """Assert the plan uses an index and does not Seq Scan any of ``large_tables``.

    ``large_tables`` should list only the fact table(s) under test (e.g.
    "paper_orders") -- tiny lookup tables like "strategies" (a couple dozen rows)
    are expected and correct to Seq Scan regardless of indices, so they are
    deliberately excluded from this check.
    """
    plan = _explain_plan(session, stmt)
    assert "Index Scan" in plan or "Index Only Scan" in plan, (
        f"Expected the plan to use an index scan; got:\n{plan}"
    )
    for table in large_tables:
        assert f"Seq Scan on {table}" not in plan, (
            f"Expected no sequential scan on '{table}'; got:\n{plan}"
        )
    return plan


def _resolve_target_strategy_id(session) -> uuid.UUID:
    return session.execute(
        select(Strategy.id).where(Strategy.strategy_id == TARGET_STRATEGY_ID)
    ).scalar_one()


def test_operator_runs_list_query_uses_index(seeded_index_db: str) -> None:
    """operator_reads.py OperatorReadService.list_runs: StrategyRun join Strategy,
    filtered by Strategy.strategy_id, ordered by started_at desc (lines ~83-92)."""
    settings = load_settings()
    with session_scope(settings) as session:
        stmt = (
            select(StrategyRun, Strategy)
            .join(Strategy, Strategy.id == StrategyRun.strategy_id)
            .where(Strategy.strategy_id == TARGET_STRATEGY_ID)
            .order_by(StrategyRun.started_at.desc(), StrategyRun.created_at.desc())
        )
        assert_uses_index(session, stmt, large_tables=("strategy_runs",))


def test_operator_orders_listing_query_uses_index(seeded_index_db: str) -> None:
    """operator_reads.py OperatorReadService.list_paper_orders: PaperOrder join
    StrategyRun join Strategy, filtered by Strategy.strategy_id (line ~152)."""
    settings = load_settings()
    with session_scope(settings) as session:
        stmt = (
            select(PaperOrder, StrategyRun, Strategy)
            .join(StrategyRun, StrategyRun.id == PaperOrder.strategy_run_id)
            .join(Strategy, Strategy.id == StrategyRun.strategy_id)
            .where(Strategy.strategy_id == TARGET_STRATEGY_ID)
            .order_by(PaperOrder.created_at.desc())
        )
        assert_uses_index(session, stmt, large_tables=("paper_orders", "strategy_runs"))


def test_local_orders_by_strategy_query_uses_index(seeded_index_db: str) -> None:
    """Identical statement shape used by both:
    - reconciliation.py reconcile_paper_execution's local_orders load (line ~296)
    - paper_execution.py sync_paper_state's local_orders load (line ~1027/1041)

    Both do: select(PaperOrder).join(StrategyRun, ...).where(StrategyRun.strategy_id
    == <resolved strategy row id>).order_by(PaperOrder.created_at.asc()). One test
    covers both call sites since the generated SQL -- and therefore the EXPLAIN
    plan -- is identical.
    """
    settings = load_settings()
    with session_scope(settings) as session:
        strategy_row_id = _resolve_target_strategy_id(session)
        stmt = (
            select(PaperOrder)
            .join(StrategyRun, StrategyRun.id == PaperOrder.strategy_run_id)
            .where(StrategyRun.strategy_id == strategy_row_id)
            .order_by(PaperOrder.created_at.asc())
        )
        assert_uses_index(session, stmt, large_tables=("paper_orders",))


def test_open_positions_by_strategy_query_uses_index(seeded_index_db: str) -> None:
    """Identical statement shape used by both:
    - reconciliation.py reconcile_paper_execution's local_positions load (line ~309)
    - paper_execution.py _sync_positions_from_broker's existing_open_positions load
      (line ~1787)

    Both filter select(Position) by strategy_id == <resolved id> and status ==
    'open'; one covers both call sites for the same reason as the test above.
    """
    settings = load_settings()
    with session_scope(settings) as session:
        strategy_row_id = _resolve_target_strategy_id(session)
        stmt = select(Position).where(
            Position.strategy_id == strategy_row_id,
            Position.status == "open",
        )
        assert_uses_index(session, stmt, large_tables=("positions",))


def test_broker_fill_dedup_selective_query_uses_named_unique_index(
    seeded_index_db: str,
) -> None:
    """The current-batch statement uses the existing named unique index."""
    from sqlalchemy import inspect

    settings = load_settings()
    with session_scope(settings) as session:
        inspector = inspect(session.get_bind())
        unique_constraints = {uc["name"] for uc in inspector.get_unique_constraints("paper_fills")}
        assert "uq_paper_fills_broker_fill_id" in unique_constraints, (
            "Expected the existing named unique index on paper_fills.broker_fill_id"
        )

        existing_fill_id = session.execute(select(PaperFill.broker_fill_id).limit(1)).scalar_one()
        stmt = select(PaperFill.broker_fill_id).where(
            PaperFill.broker_fill_id.in_([existing_fill_id, "missing-current-batch-fill"])
        )
        plan = assert_uses_index(session, stmt, large_tables=("paper_fills",))
        assert "uq_paper_fills_broker_fill_id" in plan, (
            "Expected the selective broker-fill lookup to use the existing named unique "
            f"index; got:\n{plan}"
        )
