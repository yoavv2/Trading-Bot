"""Break-glass `kill-switch-trip` worker CLI subcommand (D-15).

Covers: trip-only semantics, required/validated `--reason`, DB-only audit
rows identical in shape to the HTTP `OperatorControlService.trip_kill_switch`
path, and independence from ORCH-07 mutation-gating / broker credentials.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.test_paper_execution import migrated_paper_db  # noqa: E402,F401

from trading_platform.core.settings import load_settings  # noqa: E402
from trading_platform.db.models import (  # noqa: E402
    GLOBAL_KILL_SWITCH_NAME,
    ExecutionEvent,
    KillSwitchState,
    StrategyRun,
    StrategyRunType,
    SystemControl,
)
from trading_platform.db.session import session_scope  # noqa: E402
from trading_platform.worker.commands import DISPATCH  # noqa: E402
from trading_platform.worker.commands.operator import (  # noqa: E402
    run_kill_switch_trip_command,
)
from trading_platform.worker.parser import build_parser  # noqa: E402


def _count_operator_control_rows(settings) -> tuple[int, int]:
    with session_scope(settings) as session:
        run_count = len(
            session.execute(
                select(StrategyRun).where(StrategyRun.run_type == StrategyRunType.OPERATOR_CONTROL)
            )
            .scalars()
            .all()
        )
        event_count = len(
            session.execute(select(ExecutionEvent).where(ExecutionEvent.event_type == "kill_switch_trip"))
            .scalars()
            .all()
        )
        return run_count, event_count


def test_dispatch_wires_kill_switch_trip_to_its_handler() -> None:
    assert DISPATCH["kill-switch-trip"] is run_kill_switch_trip_command


def test_kill_switch_trip_on_armed_switch_trips_and_writes_audit_rows(
    migrated_paper_db: str,
) -> None:
    settings = load_settings()
    args = build_parser().parse_args(["kill-switch-trip", "--reason", "api down"])

    DISPATCH["kill-switch-trip"](args)

    with session_scope(settings) as session:
        control = session.execute(
            select(SystemControl).where(SystemControl.name == GLOBAL_KILL_SWITCH_NAME)
        ).scalar_one()
        run = session.execute(
            select(StrategyRun).where(StrategyRun.run_type == StrategyRunType.OPERATOR_CONTROL)
        ).scalar_one()
        event = session.execute(
            select(ExecutionEvent).where(ExecutionEvent.event_type == "kill_switch_trip")
        ).scalar_one()

    assert control.state == KillSwitchState.TRIPPED
    assert run.trigger_source == "break_glass_cli"
    assert run.parameters_snapshot["reason"] == "api down"
    assert run.parameters_snapshot["scope"] == "global_kill_switch"
    assert event.strategy_run_id == run.id
    assert event.blocks_execution is True


def test_kill_switch_trip_on_already_tripped_switch_stays_tripped_and_audits_again(
    migrated_paper_db: str,
) -> None:
    settings = load_settings()
    first_args = build_parser().parse_args(["kill-switch-trip", "--reason", "first trip"])
    DISPATCH["kill-switch-trip"](first_args)

    second_args = build_parser().parse_args(["kill-switch-trip", "--reason", "second trip"])
    DISPATCH["kill-switch-trip"](second_args)

    with session_scope(settings) as session:
        control = session.execute(
            select(SystemControl).where(SystemControl.name == GLOBAL_KILL_SWITCH_NAME)
        ).scalar_one()
        runs = (
            session.execute(
                select(StrategyRun)
                .where(StrategyRun.run_type == StrategyRunType.OPERATOR_CONTROL)
                .order_by(StrategyRun.started_at.asc())
            )
            .scalars()
            .all()
        )
        events = (
            session.execute(
                select(ExecutionEvent)
                .where(ExecutionEvent.event_type == "kill_switch_trip")
                .order_by(ExecutionEvent.event_at.asc())
            )
            .scalars()
            .all()
        )

    assert control.state == KillSwitchState.TRIPPED
    assert len(runs) == 2
    assert len(events) == 2
    second_result_summary = runs[1].result_summary
    assert second_result_summary["changed"] is False


def test_kill_switch_trip_works_with_mutations_disabled_and_empty_broker_credentials(
    migrated_paper_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED", raising=False)
    monkeypatch.setenv("TRADING_PLATFORM_BROKER__ALPACA__API_KEY", "")
    monkeypatch.setenv("TRADING_PLATFORM_BROKER__ALPACA__API_SECRET", "")

    settings = load_settings()
    assert settings.orchestration.mutations_enabled is False
    assert settings.broker.alpaca.api_key == ""

    args = build_parser().parse_args(["kill-switch-trip", "--reason", "no broker creds needed"])

    DISPATCH["kill-switch-trip"](args)

    with session_scope(settings) as session:
        control = session.execute(
            select(SystemControl).where(SystemControl.name == GLOBAL_KILL_SWITCH_NAME)
        ).scalar_one()
    assert control.state == KillSwitchState.TRIPPED


def test_kill_switch_trip_missing_reason_exits_code_2_and_writes_zero_rows(
    migrated_paper_db: str,
) -> None:
    settings = load_settings()

    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args(["kill-switch-trip"])

    assert exc_info.value.code == 2
    assert _count_operator_control_rows(settings) == (0, 0)


def test_kill_switch_trip_blank_reason_exits_nonzero_and_writes_zero_rows(
    migrated_paper_db: str,
) -> None:
    settings = load_settings()
    args = build_parser().parse_args(["kill-switch-trip", "--reason", "   "])

    with pytest.raises(SystemExit) as exc_info:
        DISPATCH["kill-switch-trip"](args)

    assert exc_info.value.code != 0
    assert _count_operator_control_rows(settings) == (0, 0)


def test_kill_switch_trip_reason_over_500_chars_exits_nonzero_and_writes_zero_rows(
    migrated_paper_db: str,
) -> None:
    settings = load_settings()
    args = build_parser().parse_args(["kill-switch-trip", "--reason", "x" * 501])

    with pytest.raises(SystemExit) as exc_info:
        DISPATCH["kill-switch-trip"](args)

    assert exc_info.value.code != 0
    assert _count_operator_control_rows(settings) == (0, 0)
