from __future__ import annotations

import sys
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.test_paper_execution import (  # noqa: E402
    FakeBrokerClient,
    FakeExecutionService,
    _seed_approved_risk_batch,
    migrated_paper_db,
)
from trading_platform.core.settings import clear_settings_cache, load_settings  # noqa: E402
from trading_platform.db.models import (  # noqa: E402
    GLOBAL_KILL_SWITCH_NAME,
    ExecutionEvent,
    KillSwitchState,
    Strategy,
    StrategyRun,
    StrategyRunStatus,
    StrategyRunType,
    StrategyStatus,
    SystemControl,
)
from trading_platform.db.session import clear_engine_cache, session_scope  # noqa: E402
from trading_platform.services.alpaca import BrokerAccountSnapshot  # noqa: E402
from trading_platform.services.execution import run_paper_order_submission, sync_paper_state  # noqa: E402
from trading_platform.services.operator_controls import (  # noqa: E402
    OperatorControlService,
    load_kill_switch_state,
)
from trading_platform.services.operator_reads import (  # noqa: E402
    OperatorReadFilters,
    OperatorReadService,
)
from trading_platform.services.operator_status import build_operator_status_report  # noqa: E402
from trading_platform.worker.__main__ import build_parser, run_operator_control_command  # noqa: E402


def test_operator_control_service_persists_status_transitions_and_audit_events(
    migrated_paper_db: str,
) -> None:
    settings = load_settings()
    service = OperatorControlService(settings=settings)

    disable_report = service.disable_strategy(
        "trend_following_daily",
        reason="manual kill switch",
        actor="pytest",
        trigger_source="pytest",
    )
    enable_report = service.enable_strategy(
        "trend_following_daily",
        reason="resume paper execution",
        actor="pytest",
        trigger_source="pytest",
    )

    assert disable_report.previous_status == StrategyStatus.ACTIVE.value
    assert disable_report.current_status == StrategyStatus.DISABLED.value
    assert disable_report.changed is True
    assert enable_report.previous_status == StrategyStatus.DISABLED.value
    assert enable_report.current_status == StrategyStatus.ACTIVE.value
    assert enable_report.changed is True

    with session_scope(settings) as session:
        strategy = session.execute(select(Strategy)).scalar_one()
        control_runs = session.execute(
            select(StrategyRun)
            .where(StrategyRun.run_type == StrategyRunType.OPERATOR_CONTROL)
            .order_by(StrategyRun.started_at.asc())
        ).scalars().all()
        control_events = session.execute(
            select(ExecutionEvent)
            .join(StrategyRun, StrategyRun.id == ExecutionEvent.strategy_run_id)
            .where(StrategyRun.run_type == StrategyRunType.OPERATOR_CONTROL)
            .order_by(ExecutionEvent.event_at.asc())
        ).scalars().all()

    assert strategy.status == StrategyStatus.ACTIVE
    assert [run.status for run in control_runs] == [
        StrategyRunStatus.SUCCEEDED,
        StrategyRunStatus.SUCCEEDED,
    ]
    assert [run.trigger_source for run in control_runs] == ["pytest", "pytest"]
    assert [event.event_type for event in control_events] == ["strategy_disabled", "strategy_enabled"]
    assert [event.blocks_execution for event in control_events] == [True, False]


def test_operator_status_report_surfaces_current_control_state_and_recent_blocks(
    migrated_paper_db: str,
) -> None:
    settings = load_settings()
    _seed_approved_risk_batch(session_date=date(2024, 1, 5))
    control_service = OperatorControlService(settings=settings)
    control_service.disable_strategy(
        "trend_following_daily",
        reason="maintenance window",
        actor="pytest",
        trigger_source="pytest",
    )
    sync_paper_state(
        "trend_following_daily",
        as_of_session=date(2024, 1, 5),
        settings=settings,
        broker_client=FakeBrokerClient(
            orders=[],
            fills=[],
            positions=[],
            account=BrokerAccountSnapshot(
                cash=Decimal("100000.000000"),
                buying_power=Decimal("100000.000000"),
                equity=Decimal("100000.000000"),
                long_market_value=Decimal("0"),
                short_market_value=Decimal("0"),
                raw_payload={"equity": "100000.000000"},
            ),
        ),
    )
    blocked_report = run_paper_order_submission(
        "trend_following_daily",
        as_of_session=date(2024, 1, 5),
        settings=settings,
        execution_service=FakeExecutionService(),
        trigger_source="pytest",
    )

    report = build_operator_status_report(
        strategy_id="trend_following_daily",
        inspection_limit=5,
        settings=settings,
    )

    assert report.strategy["status"] == StrategyStatus.DISABLED.value
    assert report.latest_control is not None
    assert report.latest_control["run_type"] == StrategyRunType.OPERATOR_CONTROL.value
    assert report.latest_account_snapshot is not None
    assert report.latest_account_snapshot["snapshot_source"] == "broker_sync"
    assert report.latest_paper_execution is not None
    assert report.latest_paper_execution["run_id"] == blocked_report.run_id
    assert report.latest_paper_session is not None
    assert report.latest_paper_session["action"] == "blocked_strategy_disabled"
    assert report.recent_blocking_events[0]["blocks_execution"] is True
    assert any(
        failed_run["run_id"] == blocked_report.run_id for failed_run in report.recent_failed_runs
    )


def test_kill_switch_is_armed_by_default(migrated_paper_db: str) -> None:
    settings = load_settings()

    snapshot = load_kill_switch_state(settings=settings)

    assert snapshot.name == GLOBAL_KILL_SWITCH_NAME
    assert snapshot.state == KillSwitchState.ARMED.value
    assert snapshot.is_tripped is False
    assert snapshot.last_change_actor == "system_bootstrap"
    assert snapshot.last_change_run_id is None


def test_operator_control_service_persists_kill_switch_trip_and_reset_with_audit(
    migrated_paper_db: str,
) -> None:
    settings = load_settings()
    service = OperatorControlService(settings=settings)

    trip_report = service.trip_kill_switch(
        reason="global halt for incident",
        actor="pytest",
        trigger_source="pytest",
    )
    reset_report = service.reset_kill_switch(
        reason="incident resolved",
        actor="pytest",
        trigger_source="pytest",
    )

    assert trip_report.previous_state == KillSwitchState.ARMED.value
    assert trip_report.current_state == KillSwitchState.TRIPPED.value
    assert trip_report.changed is True
    assert trip_report.state_snapshot["is_tripped"] is True
    assert reset_report.previous_state == KillSwitchState.TRIPPED.value
    assert reset_report.current_state == KillSwitchState.ARMED.value
    assert reset_report.changed is True

    with session_scope(settings) as session:
        control = session.execute(
            select(SystemControl).where(SystemControl.name == GLOBAL_KILL_SWITCH_NAME)
        ).scalar_one()
        strategy = session.execute(select(Strategy)).scalar_one()
        control_runs = session.execute(
            select(StrategyRun)
            .where(StrategyRun.run_type == StrategyRunType.OPERATOR_CONTROL)
            .order_by(StrategyRun.started_at.asc())
        ).scalars().all()
        kill_switch_events = session.execute(
            select(ExecutionEvent)
            .join(StrategyRun, StrategyRun.id == ExecutionEvent.strategy_run_id)
            .where(ExecutionEvent.event_type.in_(["kill_switch_trip", "kill_switch_reset"]))
            .order_by(ExecutionEvent.event_at.asc())
        ).scalars().all()

    assert control.state == KillSwitchState.ARMED
    assert control.last_change_actor == "pytest"
    assert control.last_change_reason == "incident resolved"
    assert strategy.status == StrategyStatus.ACTIVE  # Strategy status must NOT be mutated
    assert [run.status for run in control_runs] == [
        StrategyRunStatus.SUCCEEDED,
        StrategyRunStatus.SUCCEEDED,
    ]
    assert [
        run.parameters_snapshot.get("scope") for run in control_runs
    ] == ["global_kill_switch", "global_kill_switch"]
    assert [event.event_type for event in kill_switch_events] == [
        "kill_switch_trip",
        "kill_switch_reset",
    ]
    assert [event.blocks_execution for event in kill_switch_events] == [True, False]


def test_kill_switch_tripped_state_is_restart_safe(migrated_paper_db: str) -> None:
    settings = load_settings()
    service = OperatorControlService(settings=settings)
    service.trip_kill_switch(
        reason="ensure persistence survives reload",
        actor="pytest",
        trigger_source="pytest",
    )

    clear_settings_cache()
    clear_engine_cache()

    settings_after_reload = load_settings()
    snapshot = load_kill_switch_state(settings=settings_after_reload)

    assert snapshot.state == KillSwitchState.TRIPPED.value
    assert snapshot.is_tripped is True
    assert snapshot.last_change_reason == "ensure persistence survives reload"


def test_operator_control_cli_exposes_kill_switch_actions() -> None:
    parser = build_parser()
    args = parser.parse_args([
        "operator-control",
        "trip-kill-switch",
        "--reason",
        "global halt for incident",
        "--actor",
        "pytest",
        "--trigger-source",
        "pytest",
    ])

    assert args.command == "operator-control"
    assert args.action == "trip-kill-switch"
    assert args.reason == "global halt for incident"

    for action in ("trip-kill-switch", "reset-kill-switch", "show-kill-switch"):
        parsed = parser.parse_args(["operator-control", action])
        assert parsed.action == action


def test_operator_control_cli_round_trips_global_kill_switch(
    migrated_paper_db: str, capsys
) -> None:
    settings = load_settings()
    parser = build_parser()

    trip_args = parser.parse_args(
        [
            "operator-control",
            "trip-kill-switch",
            "--reason",
            "cli-initiated halt",
            "--actor",
            "pytest",
            "--trigger-source",
            "pytest",
        ]
    )
    run_operator_control_command(trip_args)
    capsys.readouterr()

    show_args = parser.parse_args(
        [
            "operator-control",
            "show-kill-switch",
            "--trigger-source",
            "pytest",
        ]
    )
    run_operator_control_command(show_args)
    show_output = capsys.readouterr().out
    assert '"state": "tripped"' in show_output
    assert '"is_tripped": true' in show_output

    reset_args = parser.parse_args(
        [
            "operator-control",
            "reset-kill-switch",
            "--reason",
            "cli-initiated reset",
            "--actor",
            "pytest",
            "--trigger-source",
            "pytest",
        ]
    )
    run_operator_control_command(reset_args)
    capsys.readouterr()

    snapshot = load_kill_switch_state(settings=settings)
    assert snapshot.state == KillSwitchState.ARMED.value
    assert snapshot.last_change_reason == "cli-initiated reset"

    with session_scope(settings) as session:
        control_runs = session.execute(
            select(StrategyRun)
            .where(StrategyRun.run_type == StrategyRunType.OPERATOR_CONTROL)
            .order_by(StrategyRun.started_at.asc())
        ).scalars().all()
        kill_switch_events = session.execute(
            select(ExecutionEvent)
            .where(ExecutionEvent.event_type.in_(["kill_switch_trip", "kill_switch_reset"]))
            .order_by(ExecutionEvent.event_at.asc())
        ).scalars().all()

    assert [run.parameters_snapshot.get("action") for run in control_runs] == ["trip", "reset"]
    assert [event.event_type for event in kill_switch_events] == [
        "kill_switch_trip",
        "kill_switch_reset",
    ]


def test_operator_reads_list_blocked_paper_executions_includes_kill_switch_runs(
    migrated_paper_db: str,
) -> None:
    settings = load_settings()
    _seed_approved_risk_batch(session_date=date(2024, 1, 5))
    control_service = OperatorControlService(settings=settings)
    control_service.trip_kill_switch(
        reason="halt before paper submission",
        actor="pytest",
        trigger_source="pytest",
    )
    blocked_report = run_paper_order_submission(
        "trend_following_daily",
        as_of_session=date(2024, 1, 5),
        settings=settings,
        execution_service=FakeExecutionService(),
        trigger_source="pytest",
    )

    reads = OperatorReadService(settings)
    blocked_paper_executions = reads.list_blocked_paper_executions(
        OperatorReadFilters(strategy_id="trend_following_daily", limit=5)
    )

    assert len(blocked_paper_executions) == 1
    entry = blocked_paper_executions[0]
    assert entry["run_id"] == blocked_report.run_id
    assert entry["blocked_reason"] == "global_kill_switch_tripped"
    assert entry["action"] == "blocked_global_kill_switch"
    assert entry["kill_switch"]["is_tripped"] is True


def test_operator_status_report_surfaces_kill_switch_state_and_blocked_submissions(
    migrated_paper_db: str,
) -> None:
    settings = load_settings()
    _seed_approved_risk_batch(session_date=date(2024, 1, 5))
    control_service = OperatorControlService(settings=settings)
    control_service.trip_kill_switch(
        reason="status surface trip",
        actor="pytest",
        trigger_source="pytest",
    )
    blocked_report = run_paper_order_submission(
        "trend_following_daily",
        as_of_session=date(2024, 1, 5),
        settings=settings,
        execution_service=FakeExecutionService(),
        trigger_source="pytest",
    )

    report = build_operator_status_report(
        strategy_id="trend_following_daily",
        inspection_limit=5,
        settings=settings,
    )

    assert report.kill_switch["state"] == KillSwitchState.TRIPPED.value
    assert report.kill_switch["is_tripped"] is True
    assert report.kill_switch["last_change_reason"] == "status surface trip"
    assert report.kill_switch["last_change_run_id"] is not None

    recent_blocked = report.recent_blocked_paper_executions
    assert any(
        entry["run_id"] == blocked_report.run_id
        and entry["blocked_reason"] == "global_kill_switch_tripped"
        for entry in recent_blocked
    )

    report_dict = report.to_dict()
    assert "kill_switch" in report_dict
    assert "recent_blocked_paper_executions" in report_dict
