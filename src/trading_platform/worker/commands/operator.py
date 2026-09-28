"""Worker CLI handlers: `operator-control`, `operator-status` (STRUCT-03).

Not one of the six domain modules STRUCT-03 names, but a legitimate seventh
sibling module: the entrypoint-must-be-pure-routing criterion (<~100 lines,
no business logic) forces a home for the two operator subcommands, which
don't fit any of `bootstrap`/`ingest`/`backtest`/`risk_check`/`paper_execute`/
`reconcile`. See 12-06-SUMMARY.md for the explicit scope note.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from trading_platform.core.logging import build_log_context, configure_logging, get_logger
from trading_platform.core.startup import enforce_startup_config
from trading_platform.services.config.validation import ExecutionMode
from trading_platform.services.operator_controls import (
    OperatorControlService,
    render_kill_switch_report,
    render_operator_control_report,
)
from trading_platform.services.operator_status import (
    build_operator_status_report,
    render_operator_status_report,
)


def run_operator_control_command(args: argparse.Namespace) -> None:
    settings = enforce_startup_config(mode=ExecutionMode.BACKTEST)
    configure_logging(settings.logging)
    logger = get_logger("trading_platform.worker")
    service = OperatorControlService(settings=settings)

    if args.action in {"trip-kill-switch", "reset-kill-switch", "show-kill-switch"}:
        _run_kill_switch_action(service, args, logger)
        return

    if args.action == "disable":
        report = service.disable_strategy(
            args.strategy,
            reason=args.reason,
            actor=args.actor,
            trigger_source=args.trigger_source,
        )
    else:
        report = service.enable_strategy(
            args.strategy,
            reason=args.reason,
            actor=args.actor,
            trigger_source=args.trigger_source,
        )

    logger.info(
        "worker_operator_control_completed",
        extra={
            "context": build_log_context(
                strategy_id=report.strategy_id,
                run_id=report.run_id,
                strategy_status=report.current_status,
                action=args.action,
                trigger_source=args.trigger_source,
            )
        },
    )
    print(render_operator_control_report(report, summary_format=args.summary_format))


def _run_kill_switch_action(
    service: OperatorControlService,
    args: argparse.Namespace,
    logger: logging.Logger,
) -> None:
    if args.action == "show-kill-switch":
        snapshot = service.get_kill_switch_state()
        logger.info(
            "worker_kill_switch_state_read",
            extra={
                "context": build_log_context(
                    run_id=snapshot.last_change_run_id,
                    kill_switch_state=snapshot.state,
                    action="show-kill-switch",
                    trigger_source=args.trigger_source,
                )
            },
        )
        print(json.dumps(snapshot.to_dict(), indent=2))
        return

    if args.action == "trip-kill-switch":
        report = service.trip_kill_switch(
            reason=args.reason,
            actor=args.actor,
            trigger_source=args.trigger_source,
        )
    else:
        report = service.reset_kill_switch(
            reason=args.reason,
            actor=args.actor,
            trigger_source=args.trigger_source,
        )

    logger.info(
        "worker_kill_switch_applied",
        extra={
            "context": build_log_context(
                run_id=report.run_id,
                kill_switch_state=report.current_state,
                action=args.action,
                trigger_source=args.trigger_source,
            )
        },
    )
    print(render_kill_switch_report(report, summary_format=args.summary_format))


def run_kill_switch_trip_command(args: argparse.Namespace) -> None:
    """Break-glass: trip the global kill switch when the API is unavailable.

    D-15: this is the sole ORCH-01 exception. Shell operator access is
    already privileged, and this command can only make the system safer
    (trip-only) -- it never re-arms the kill switch and never
    enables/disables a strategy. Reset and enable/disable exist only as
    HTTP controls behind ORCH-07's mutation-enablement gate; this command
    never reads that setting and boots at BACKTEST level (no broker
    credentials required), so it works even when the API is unavailable
    or HTTP mutations are disabled.
    """
    settings = enforce_startup_config(mode=ExecutionMode.BACKTEST)
    configure_logging(settings.logging)
    logger = get_logger("trading_platform.worker")

    reason = args.reason.strip()
    if not reason or len(reason) > 500:
        print(
            "kill-switch-trip: --reason must be 1-500 characters after trimming.",
            file=sys.stderr,
        )
        raise SystemExit(2)

    service = OperatorControlService(settings=settings)
    report = service.trip_kill_switch(
        reason=reason,
        actor="local_operator",
        trigger_source="break_glass_cli",
    )
    logger.warning(
        "worker_kill_switch_break_glass_trip",
        extra={
            "context": build_log_context(
                run_id=report.run_id,
                kill_switch_state=report.current_state,
                trigger_source=report.trigger_source,
            )
        },
    )
    print(render_kill_switch_report(report, summary_format="json"))


def run_operator_status_command(args: argparse.Namespace) -> None:
    settings = enforce_startup_config(mode=ExecutionMode.BACKTEST)
    configure_logging(settings.logging)
    logger = get_logger("trading_platform.worker")
    report = build_operator_status_report(
        strategy_id=args.strategy,
        inspection_limit=args.inspection_limit,
        settings=settings,
    )
    logger.info(
        "worker_operator_status_completed",
        extra={
            "context": build_log_context(
                strategy_id=args.strategy,
                strategy_status=report.strategy["status"],
                inspection_limit=args.inspection_limit,
            )
        },
    )
    print(render_operator_status_report(report, summary_format=args.summary_format))
