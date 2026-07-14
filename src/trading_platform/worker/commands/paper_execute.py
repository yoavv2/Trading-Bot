"""Worker CLI handlers: `submit-paper-orders`, `run-paper-session`, `sync-paper-state` (STRUCT-03)."""

from __future__ import annotations

import argparse
import json
import logging
import sys

from trading_platform.core.logging import configure_logging, emit_structured_log, get_logger
from trading_platform.core.startup import enforce_startup_config
from trading_platform.services.concurrency_guard import (
    CONCURRENT_RUN_LOCK_EXIT_CODE,
    ConcurrentRunLockedError,
)
from trading_platform.services.config.validation import ExecutionMode
from trading_platform.services.execution import (
    resolve_submission_session,
    run_paper_order_submission,
    run_paper_session,
    sync_paper_state,
)


def _handle_concurrent_run_lock_denied(
    logger: logging.Logger,
    exc: ConcurrentRunLockedError,
    *,
    command: str,
) -> None:
    """Map a lock-denial to the reserved exit code, no traceback.

    Emits a CLI-level WARNING naming the tuple and the command (distinct
    from the service-layer WARNING already logged by `session_run_lock`),
    prints one concise human line to stderr, then raises `SystemExit` so
    the process exits with `CONCURRENT_RUN_LOCK_EXIT_CODE` and no traceback
    reaches the operator/scheduler.
    """
    emit_structured_log(
        logger,
        logging.WARNING,
        "paper_command_lock_denied",
        strategy_id=exc.strategy_id,
        session_date=exc.session_date.isoformat(),
        command=command,
        exit_code=CONCURRENT_RUN_LOCK_EXIT_CODE,
    )
    print(
        f"Another session already holds the run lock for strategy '{exc.strategy_id}' "
        f"session {exc.session_date}; exiting without retrying.",
        file=sys.stderr,
    )
    raise SystemExit(CONCURRENT_RUN_LOCK_EXIT_CODE)


def run_submit_paper_orders_command(args: argparse.Namespace) -> None:
    settings = enforce_startup_config(mode=ExecutionMode.PAPER)
    configure_logging(settings.logging)
    logger = get_logger("trading_platform.worker")
    as_of_session = resolve_submission_session(
        settings=settings,
        as_of_arg=args.as_of,
    )
    try:
        report = run_paper_order_submission(
            args.strategy,
            as_of_session=as_of_session,
            risk_run_id=args.risk_run_id,
            trigger_source=args.trigger_source,
            settings=settings,
        )
    except ConcurrentRunLockedError as exc:
        _handle_concurrent_run_lock_denied(logger, exc, command="submit-paper-orders")
    logger.info(
        "worker_paper_order_submission_completed",
        extra={
            "context": {
                "run_id": report.run_id,
                "strategy_id": report.strategy_id,
                "status": report.status,
                "as_of_session": as_of_session.isoformat(),
            }
        },
    )
    indent = None if args.compact else 2
    print(json.dumps(report.to_dict(), indent=indent, default=str))


def run_paper_session_command(args: argparse.Namespace) -> None:
    settings = enforce_startup_config(mode=ExecutionMode.PAPER)
    configure_logging(settings.logging)
    logger = get_logger("trading_platform.worker")
    as_of_session = resolve_submission_session(
        settings=settings,
        as_of_arg=args.as_of,
    )
    strategy_id = args.strategy or settings.execution.paper_session_runner.default_strategy_id
    trigger_source = args.trigger_source or settings.execution.paper_session_runner.trigger_source
    try:
        report = run_paper_session(
            strategy_id,
            as_of_session=as_of_session,
            risk_run_id=args.risk_run_id,
            trigger_source=trigger_source,
            settings=settings,
        )
    except ConcurrentRunLockedError as exc:
        _handle_concurrent_run_lock_denied(logger, exc, command="run-paper-session")
    logger.info(
        "worker_paper_session_completed",
        extra={
            "context": {
                "strategy_id": strategy_id,
                "as_of_session": as_of_session.isoformat(),
                "action": report.action,
                "execution_run_id": report.execution_run_id,
            }
        },
    )
    indent = None if args.compact else 2
    print(json.dumps(report.to_dict(), indent=indent, default=str))


def run_sync_paper_state_command(args: argparse.Namespace) -> None:
    settings = enforce_startup_config(mode=ExecutionMode.PAPER)
    configure_logging(settings.logging)
    logger = get_logger("trading_platform.worker")
    as_of_session = resolve_submission_session(
        settings=settings,
        as_of_arg=args.as_of,
    )
    strategy_id = args.strategy or settings.execution.paper_session_runner.default_strategy_id
    report = sync_paper_state(
        strategy_id,
        as_of_session=as_of_session,
        settings=settings,
    )
    logger.info(
        "worker_paper_state_sync_completed",
        extra={
            "context": {
                "strategy_id": strategy_id,
                "as_of_session": as_of_session.isoformat(),
                "orders_synced": report.orders_synced,
                "fills_ingested": report.fills_ingested,
            }
        },
    )
    indent = None if args.compact else 2
    print(json.dumps(report.to_dict(), indent=indent, default=str))
