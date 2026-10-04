"""Regression tests for OPERATOR_CONTROL audit timestamp ordering (UAT gap 4, D-11a).

Every control run must satisfy ``completed_at >= started_at``. ``started_at`` is
the Postgres transaction start (``now()``); the service must therefore take its
single ``changed_at`` from the DB clock *inside* the transaction, after the row
lock, rather than from the Python clock before the transaction opens.
"""

from __future__ import annotations

import sys
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.test_paper_execution import (  # noqa: E402
    _admin_connection_settings,
    migrated_paper_db,  # noqa: F401 (reused DB harness fixture)
)

from trading_platform.core.settings import load_settings  # noqa: E402
from trading_platform.db.models import (  # noqa: E402
    GLOBAL_KILL_SWITCH_NAME,
    ExecutionEvent,
    StrategyRun,
    SystemControl,
)
from trading_platform.db.session import session_scope  # noqa: E402
from trading_platform.services.operator_controls import OperatorControlService  # noqa: E402

STRATEGY_ID = "trend_following_daily"
HOLD = 0.3
KWARGS: dict[str, str] = {"reason": "timestamp regression", "actor": "pytest", "trigger_source": "pytest"}


def _run_action(service: OperatorControlService, action: str) -> Any:
    if action == "trip":
        return service.trip_kill_switch(**KWARGS)
    if action == "reset":
        return service.reset_kill_switch(**KWARGS)
    if action == "enable":
        return service.enable_strategy(STRATEGY_ID, **KWARGS)
    if action == "disable":
        return service.disable_strategy(STRATEGY_ID, **KWARGS)
    raise AssertionError(action)


# (action, changed, setup actions that establish the previous state).
# The kill switch is armed and a NEW strategy row is DISABLED by default (R-8).
_CASES = [
    ("trip", True, []),
    ("trip", False, ["trip"]),
    ("reset", True, ["trip"]),
    ("reset", False, []),
    ("enable", True, []),
    ("enable", False, ["enable"]),
    ("disable", True, ["enable"]),
    ("disable", False, []),
]


def _load_run_and_event(settings: Any, run_id: str) -> tuple[StrategyRun, ExecutionEvent]:
    with session_scope(settings) as session:
        run = session.execute(select(StrategyRun).where(StrategyRun.id == run_id)).scalar_one()
        event = session.execute(
            select(ExecutionEvent).where(ExecutionEvent.strategy_run_id == run.id)
        ).scalar_one()
        session.expunge_all()
    return run, event


@pytest.mark.parametrize(
    ("action", "changed", "setup"),
    _CASES,
    ids=[f"{a}-{'changed' if c else 'unchanged'}" for a, c, _ in _CASES],
)
def test_control_run_timestamps_are_ordered(
    migrated_paper_db: str,  # noqa: F811
    action: str,
    changed: bool,
    setup: list[str],
) -> None:
    settings = load_settings()
    service = OperatorControlService(settings=settings)
    for prior in setup:
        _run_action(service, prior)

    report = _run_action(service, action)
    assert report.changed is changed

    run, event = _load_run_and_event(settings, report.run_id)
    assert run.completed_at is not None
    assert run.completed_at >= run.started_at
    assert event.event_at == run.completed_at
    changed_at_text = run.result_summary["changed_at"]
    assert datetime.fromisoformat(changed_at_text) == run.completed_at
    assert changed_at_text.endswith("+00:00")


def _kill_switch_row(settings: Any) -> SystemControl:
    with session_scope(settings) as session:
        control = session.execute(
            select(SystemControl).where(SystemControl.name == GLOBAL_KILL_SWITCH_NAME)
        ).scalar_one()
        session.expunge_all()
    return control


def test_kill_switch_changed_sets_last_changed_at_to_completed_at(
    migrated_paper_db: str,  # noqa: F811
) -> None:
    settings = load_settings()
    service = OperatorControlService(settings=settings)

    report = service.trip_kill_switch(**KWARGS)
    assert report.changed is True

    run, _ = _load_run_and_event(settings, report.run_id)
    control = _kill_switch_row(settings)
    assert control.last_changed_at == run.completed_at
    assert control.last_change_run_id == run.id


def test_kill_switch_unchanged_keeps_last_changed_at(
    migrated_paper_db: str,  # noqa: F811
) -> None:
    settings = load_settings()
    service = OperatorControlService(settings=settings)
    service.trip_kill_switch(**KWARGS)
    before = _kill_switch_row(settings).last_changed_at

    report = service.trip_kill_switch(**KWARGS)
    assert report.changed is False

    assert _kill_switch_row(settings).last_changed_at == before


def test_break_glass_report_orders_timestamps(
    migrated_paper_db: str,  # noqa: F811
) -> None:
    service = OperatorControlService(settings=load_settings())

    report = service.trip_kill_switch(
        reason="break glass", actor="pytest", trigger_source="break_glass_cli"
    )

    assert report.completed_at is not None
    assert datetime.fromisoformat(report.completed_at) >= datetime.fromisoformat(report.started_at)


def _hold_lock_while_running(
    database_name: str,
    lock_sql: str,
    lock_params: tuple[Any, ...],
    action: Callable[[], Any],
) -> Any:
    """Hold a row lock, run ``action`` in a thread, and release after ``HOLD``.

    Returns the action's result. The holder is always rolled back and closed in
    ``finally`` so a failure never blocks the fixture's DROP DATABASE.
    """
    params = _admin_connection_settings()
    holder = psycopg.connect(
        host=params["host"],
        port=params["port"],
        user=params["user"],
        password=params["password"],
        dbname=database_name,
        autocommit=False,
    )
    outcome: dict[str, Any] = {}

    def target() -> None:
        try:
            outcome["result"] = action()
        except BaseException as exc:  # noqa: BLE001 - re-raised in the main thread
            outcome["error"] = exc

    thread = threading.Thread(target=target, daemon=True)
    try:
        with holder.cursor() as cursor:
            cursor.execute(lock_sql, lock_params)
        thread.start()

        deadline = time.monotonic() + 5.0
        waiting = 0
        with psycopg.connect(
            host=params["host"],
            port=params["port"],
            user=params["user"],
            password=params["password"],
            dbname=database_name,
            autocommit=True,
        ) as monitor:
            while time.monotonic() < deadline:
                with monitor.cursor() as cursor:
                    cursor.execute(
                        "SELECT count(*) FROM pg_stat_activity "
                        "WHERE datname = %s AND wait_event_type = 'Lock'",
                        (database_name,),
                    )
                    row = cursor.fetchone()
                    waiting = int(row[0]) if row else 0
                if waiting >= 1:
                    break
                time.sleep(0.02)
        assert waiting >= 1, "service transaction never waited on the row lock"
        time.sleep(HOLD)
    finally:
        holder.rollback()
        holder.close()

    thread.join(timeout=5.0)
    assert not thread.is_alive(), "service call did not finish after the lock was released"
    if "error" in outcome:
        raise outcome["error"]
    return outcome["result"]


def test_kill_switch_lock_wait_orders_timestamps(
    migrated_paper_db: str,  # noqa: F811
) -> None:
    settings = load_settings()
    service = OperatorControlService(settings=settings)

    report = _hold_lock_while_running(
        migrated_paper_db,
        "SELECT 1 FROM system_controls WHERE name = %s FOR UPDATE",
        (GLOBAL_KILL_SWITCH_NAME,),
        lambda: service.trip_kill_switch(**KWARGS),
    )

    run, event = _load_run_and_event(settings, report.run_id)
    assert run.completed_at is not None
    assert run.completed_at - run.started_at >= timedelta(seconds=HOLD)
    assert event.event_at == run.completed_at


def test_strategy_lock_wait_orders_timestamps(
    migrated_paper_db: str,  # noqa: F811
) -> None:
    settings = load_settings()
    service = OperatorControlService(settings=settings)
    # A prior enable creates the strategies row so the holder can lock it.
    service.enable_strategy(STRATEGY_ID, **KWARGS)

    report = _hold_lock_while_running(
        migrated_paper_db,
        "SELECT 1 FROM strategies WHERE strategy_id = %s FOR UPDATE",
        (STRATEGY_ID,),
        lambda: service.disable_strategy(STRATEGY_ID, **KWARGS),
    )

    run, event = _load_run_and_event(settings, report.run_id)
    assert run.completed_at is not None
    assert run.completed_at - run.started_at >= timedelta(seconds=HOLD)
    assert event.event_at == run.completed_at
