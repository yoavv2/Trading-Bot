"""Phase 8 (Concurrency Guard) capstone: CLI exit-code mapping (LOCK-01) and
crash/restart lock-release proof (LOCK-06), end-to-end against a real
migrated Postgres database.

Test A proves the operator/scheduler-distinguishable exit path: holding the
tuple's advisory lock forces `submit-paper-orders` to exit with the reserved
`CONCURRENT_RUN_LOCK_EXIT_CODE`, no traceback, and zero side effects.

Test B proves the crash-release guarantee: a lock-holder that crashes (its
connection drops without an explicit unlock) auto-releases the advisory
lock, so a subsequent run for the SAME tuple acquires cleanly -- no manual
intervention -- and reclaims the crashed run's leftover `running` row as
STALE.
"""

from __future__ import annotations

import os
import sys
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from alembic import command
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.migrate import build_alembic_config
from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.db.models import (
    ExecutionEvent,
    PaperOrder,
    RiskEvent,
    StrategyRun,
    StrategyRunStatus,
    StrategyRunType,
)
from trading_platform.db.models.symbol import Symbol
from trading_platform.db.session import clear_engine_cache, session_scope
from trading_platform.services.bootstrap import ensure_strategy_record
from trading_platform.services.concurrency_guard import (
    CONCURRENT_RUN_LOCK_EXIT_CODE,
    advisory_lock_key,
    session_run_lock,
)
from trading_platform.services.execution import (
    ExecutionOrderStatus,
    ExecutionService,
    OrderIntent,
    OrderSubmissionResult,
)
from trading_platform.services.paper_execution import run_paper_order_submission
from trading_platform.strategies.registry import build_default_registry
from trading_platform.worker.__main__ import build_parser, run_submit_paper_orders_command


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


def _connect_raw(database_name: str) -> psycopg.Connection:
    """A connection OUTSIDE the SQLAlchemy pool, so closing it guarantees the
    backend session actually terminates -- required to simulate a real crash
    (as opposed to a pooled connection quietly returning to the pool)."""
    params = _admin_connection_settings()
    return psycopg.connect(
        host=params["host"],
        port=params["port"],
        user=params["user"],
        password=params["password"],
        dbname=database_name,
        autocommit=True,
    )


def _set_database_env(monkeypatch: pytest.MonkeyPatch, database_name: str) -> None:
    params = _admin_connection_settings()
    monkeypatch.setenv("TRADING_PLATFORM_DATABASE__HOST", params["host"])
    monkeypatch.setenv("TRADING_PLATFORM_DATABASE__PORT", params["port"])
    monkeypatch.setenv("TRADING_PLATFORM_DATABASE__USER", params["user"])
    monkeypatch.setenv("TRADING_PLATFORM_DATABASE__PASSWORD", params["password"])
    monkeypatch.setenv("TRADING_PLATFORM_DATABASE__NAME", database_name)


@pytest.fixture()
def migrated_paper_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    database_name = f"concurrency_guard_e2e_{uuid.uuid4().hex[:8]}"
    admin_params = _admin_connection_settings()

    try:
        with _connect_admin(admin_params) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f'CREATE DATABASE "{database_name}"')
    except psycopg.Error as exc:
        pytest.fail(
            "PostgreSQL is required for tests/test_concurrency_guard_e2e.py. "
            f"Connection error: {exc}"
        )

    _set_database_env(monkeypatch, database_name)
    clear_settings_cache()
    clear_engine_cache()
    command.upgrade(build_alembic_config(), "head")

    try:
        yield database_name
    finally:
        clear_settings_cache()
        clear_engine_cache()
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


class FakeExecutionService(ExecutionService):
    def __init__(self) -> None:
        self.submitted_intents: list[OrderIntent] = []

    def describe(self) -> dict[str, object]:
        return {"service": "execution", "status": "available", "provider": "fake"}

    def submit_order(self, intent: OrderIntent) -> OrderSubmissionResult:
        self.submitted_intents.append(intent)
        return OrderSubmissionResult(
            client_order_id=intent.client_order_id,
            broker_order_id=f"fake-{intent.symbol.lower()}-001",
            symbol=intent.symbol,
            side=intent.side,
            quantity=intent.quantity,
            order_type=intent.order_type,
            time_in_force=intent.time_in_force,
            status=ExecutionOrderStatus.PENDING,
            broker_status="new",
            submitted_at=datetime(2024, 1, 5, 14, 35, tzinfo=UTC),
            raw_payload={
                "id": f"fake-{intent.symbol.lower()}-001",
                "client_order_id": intent.client_order_id,
                "symbol": intent.symbol,
                "status": "new",
            },
        )


def _seed_approved_risk_batch(*, session_date: date = date(2024, 1, 5)) -> tuple[uuid.UUID, dict[str, uuid.UUID]]:
    """Seed a strategy + a succeeded risk_evaluation run with approved
    candidates, so a paper-order submission for this tuple has real work to
    do (adapted from tests/test_paper_execution.py's seeding helper)."""
    settings = load_settings()
    registry = build_default_registry(settings)
    strategy = registry.resolve("trend_following_daily")

    with session_scope(settings) as session:
        strategy_record = ensure_strategy_record(session, strategy.metadata)
        aapl = Symbol(ticker="AAPL", active=True)
        msft = Symbol(ticker="MSFT", active=True)
        session.add_all([aapl, msft])
        session.flush()

        risk_run = StrategyRun(
            strategy_id=strategy_record.id,
            run_type=StrategyRunType.RISK_EVALUATION,
            status=StrategyRunStatus.SUCCEEDED,
            trigger_source="test_suite",
            parameters_snapshot={"as_of_session": session_date.isoformat()},
            result_summary={"stage": "completed", "as_of_session": session_date.isoformat()},
        )
        session.add(risk_run)
        session.flush()

        approved_events = {
            "AAPL": RiskEvent(
                strategy_run_id=risk_run.id,
                symbol_id=aapl.id,
                session_date=session_date,
                signal_direction="long",
                signal_reason="trend_entry",
                outcome="approved",
                decision_code="approved",
                decision_reason="Approved for paper execution.",
                reference_price=Decimal("120.000000"),
                proposed_quantity=Decimal("10.000000"),
                proposed_notional=Decimal("1200.000000"),
                risk_metadata={"remaining_cash": 98800.0},
            ),
            "MSFT": RiskEvent(
                strategy_run_id=risk_run.id,
                symbol_id=msft.id,
                session_date=session_date,
                signal_direction="long",
                signal_reason="trend_entry",
                outcome="approved",
                decision_code="approved",
                decision_reason="Approved for paper execution.",
                reference_price=Decimal("300.000000"),
                proposed_quantity=Decimal("5.000000"),
                proposed_notional=Decimal("1500.000000"),
                risk_metadata={"remaining_cash": 97300.0},
            ),
        }
        session.add_all(list(approved_events.values()))
        session.flush()
        return risk_run.id, {symbol: event.id for symbol, event in approved_events.items()}


# ---------------------------------------------------------------------------
# Test A -- exit code (LOCK-01): a held lock forces the reserved exit code,
# no traceback, and zero side effects.
# ---------------------------------------------------------------------------


def test_submit_paper_orders_exits_with_reserved_exit_code_and_no_side_effects_when_lock_held(
    migrated_paper_db: str,
) -> None:
    _seed_approved_risk_batch()
    strategy_id = "trend_following_daily"
    session_date = date(2024, 1, 5)
    lock_key = advisory_lock_key(strategy_id, session_date)

    parser = build_parser()
    args = parser.parse_args(
        [
            "submit-paper-orders",
            "--strategy",
            strategy_id,
            "--as-of",
            session_date.isoformat(),
            "--compact",
        ]
    )

    holder_connection = _connect_raw(migrated_paper_db)
    try:
        with holder_connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", (lock_key,))
            (acquired,) = cursor.fetchone()
        assert acquired is True

        with pytest.raises(SystemExit) as exc_info:
            run_submit_paper_orders_command(args)

        assert exc_info.value.code == CONCURRENT_RUN_LOCK_EXIT_CODE
    finally:
        holder_connection.close()

    settings = load_settings()
    with session_scope(settings) as session:
        execution_runs = session.execute(
            select(StrategyRun).where(StrategyRun.run_type == StrategyRunType.PAPER_EXECUTION)
        ).scalars().all()
        paper_orders = session.execute(select(PaperOrder)).scalars().all()

    # The loser wrote NO run row and submitted NO orders -- the lock denial
    # happened before any DB write or broker call (LOCK-01).
    assert execution_runs == []
    assert paper_orders == []


# ---------------------------------------------------------------------------
# Test B -- crash-release + reclaim (LOCK-06): a crashed holder's lock
# auto-releases; the next run acquires cleanly with zero manual intervention
# and reclaims the leftover running row as STALE.
# ---------------------------------------------------------------------------


def test_run_paper_order_submission_acquires_cleanly_after_crash_and_reclaims_stale_predecessor(
    migrated_paper_db: str,
) -> None:
    _seed_approved_risk_batch()
    settings = load_settings()
    registry = build_default_registry(settings)
    strategy = registry.resolve("trend_following_daily")
    strategy_id = "trend_following_daily"
    session_date = date(2024, 1, 5)
    lock_key = advisory_lock_key(strategy_id, session_date)

    # (1) A crashed holder: take the tuple's advisory lock on a raw
    # connection OUTSIDE the SQLAlchemy pool.
    crashed_connection = _connect_raw(migrated_paper_db)
    with crashed_connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", (lock_key,))
        (acquired,) = cursor.fetchone()
    assert acquired is True

    # (2) ...that durably left a `running` row behind, committed on a
    # SEPARATE connection (session_scope) before the crash.
    with session_scope(settings) as session:
        strategy_record = ensure_strategy_record(session, strategy.metadata)
        stale_predecessor = StrategyRun(
            strategy_id=strategy_record.id,
            run_type=StrategyRunType.PAPER_EXECUTION,
            status=StrategyRunStatus.RUNNING,
            trigger_source="crashed_predecessor",
            started_at=datetime.now(UTC) - timedelta(minutes=40),
            parameters_snapshot={"as_of_session": session_date.isoformat()},
            result_summary={"stage": "running", "as_of_session": session_date.isoformat()},
        )
        session.add(stale_predecessor)
        session.flush()
        stale_run_id = stale_predecessor.id

    # (3) Simulate the crash: force-close the lock-holding connection
    # WITHOUT calling pg_advisory_unlock.
    crashed_connection.close()

    # (4) A real run for the SAME tuple must acquire the lock cleanly (no
    # ConcurrentRunLockedError, no hang -- a raised exception here would
    # fail this test) with zero manual intervention.
    execution_service = FakeExecutionService()
    report = run_paper_order_submission(
        strategy_id,
        as_of_session=session_date,
        settings=settings,
        execution_service=execution_service,
        trigger_source="pytest_crash_restart",
    )

    assert report.status == StrategyRunStatus.SUCCEEDED.value
    fresh_run_id = uuid.UUID(report.run_id)
    assert len(execution_service.submitted_intents) == 2

    with session_scope(settings) as session:
        stale_after = session.get(StrategyRun, stale_run_id)
        fresh_after = session.get(StrategyRun, fresh_run_id)
        reclaim_events = session.execute(
            select(ExecutionEvent).where(ExecutionEvent.strategy_run_id == stale_run_id)
        ).scalars().all()

    # The crashed run's leftover `running` row was reclaimed to STALE, with
    # a durable audit ExecutionEvent naming the reclaiming run...
    assert stale_after.status == StrategyRunStatus.STALE
    assert len(reclaim_events) == 1
    assert reclaim_events[0].event_type == "paper_run_reclaimed_stale"
    assert reclaim_events[0].details["reclaiming_run_id"] == str(fresh_run_id)
    # ...while the fresh run reached a terminal succeeded state cleanly.
    assert fresh_after.status == StrategyRunStatus.SUCCEEDED

    # A subsequent acquisition on the SAME tuple must also succeed now that
    # the fresh run has finalized and released the lock on normal exit.
    with session_run_lock(strategy_id=strategy_id, session_date=session_date, settings=settings):
        pass
