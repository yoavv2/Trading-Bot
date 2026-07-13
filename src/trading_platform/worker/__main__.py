"""Worker CLI for placeholder service, dry-run scaffolding, and research workflows."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import UTC, date, datetime, timedelta

from trading_platform.core.config_validation import ExecutionMode
from trading_platform.core.logging import build_log_context, configure_logging, emit_structured_log
from trading_platform.core.settings import get_strategy_config
from trading_platform.core.startup import enforce_startup_config
from trading_platform.services.analytics import build_strategy_analytics_report, render_strategy_analytics_report
from trading_platform.services.backtest_reporting import export_backtest_report
from trading_platform.services.backtesting import resolve_backtest_window, run_backtest
from trading_platform.services.bootstrap import run_dry_bootstrap as run_persisted_dry_bootstrap
from trading_platform.services.concurrency_guard import CONCURRENT_RUN_LOCK_EXIT_CODE, ConcurrentRunLockedError
from trading_platform.services.operator_controls import (
    OperatorControlService,
    render_kill_switch_report,
    render_operator_control_report,
)
from trading_platform.services.operator_status import (
    build_operator_status_report,
    render_operator_status_report,
)
from trading_platform.services.paper_execution import (
    resolve_submission_session,
    run_paper_order_submission,
    run_paper_session,
    sync_paper_state,
)
from trading_platform.services.reconciliation import reconcile_paper_execution
from trading_platform.services.risk import resolve_evaluation_session, run_risk_evaluation


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trading-platform-worker")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve", help="Run the placeholder worker loop.")
    serve_parser.add_argument("--interval-seconds", type=int, default=30)

    dry_run_parser = subparsers.add_parser("dry-run", help="Exercise config and strategy bootstrap.")
    dry_run_parser.add_argument("--strategy", default="trend_following_daily")

    backtest_parser = subparsers.add_parser("backtest", help="Run a deterministic daily-bar backtest.")
    backtest_parser.add_argument("--strategy", default="trend_following_daily")
    backtest_parser.add_argument("--from-date", metavar="YYYY-MM-DD", help="Backtest window start (inclusive).")
    backtest_parser.add_argument("--to-date", metavar="YYYY-MM-DD", help="Backtest window end (inclusive).")
    backtest_parser.add_argument("--compact", action="store_true", default=False)
    backtest_parser.add_argument("--trigger-source", default="worker_cli")

    report_parser = subparsers.add_parser(
        "report-backtest",
        help="Render and export a persisted backtest report.",
    )
    report_parser.add_argument("--run-id", help="Explicit backtest run ID to report.")
    report_parser.add_argument("--strategy", default="trend_following_daily")
    report_parser.add_argument("--summary-format", choices=("markdown", "json"), default="markdown")
    report_parser.add_argument("--output-dir", help="Directory for summary and CSV exports.")

    analytics_parser = subparsers.add_parser(
        "report-strategy-analytics",
        help="Render a strategy analytics summary plus recent operational inspection data.",
    )
    analytics_parser.add_argument("--strategy", default="trend_following_daily")
    analytics_parser.add_argument("--backtest-run-id", help="Explicit backtest run ID to summarize.")
    analytics_parser.add_argument("--paper-run-id", help="Explicit paper execution run ID to summarize.")
    analytics_parser.add_argument("--inspection-limit", type=int, default=5)
    analytics_parser.add_argument("--summary-format", choices=("markdown", "json"), default="markdown")

    risk_parser = subparsers.add_parser(
        "evaluate-risk",
        help="Run the persisted signal-to-risk evaluation flow.",
    )
    risk_parser.add_argument("--strategy", default="trend_following_daily")
    risk_parser.add_argument(
        "--as-of",
        metavar="YYYY-MM-DD",
        help="Session date to evaluate. Defaults to the latest completed persisted session.",
    )
    risk_parser.add_argument("--compact", action="store_true", default=False)
    risk_parser.add_argument("--trigger-source", default="worker_cli")

    submit_parser = subparsers.add_parser(
        "submit-paper-orders",
        help="Submit approved paper-trading orders through the broker adapter.",
    )
    submit_parser.add_argument("--strategy", default="trend_following_daily")
    submit_parser.add_argument(
        "--as-of",
        metavar="YYYY-MM-DD",
        help="Session date whose approved risk decisions should be submitted.",
    )
    submit_parser.add_argument("--risk-run-id", help="Explicit succeeded risk_evaluation run ID to consume.")
    submit_parser.add_argument("--compact", action="store_true", default=False)
    submit_parser.add_argument("--trigger-source", default="worker_cli")

    run_session_parser = subparsers.add_parser(
        "run-paper-session",
        help="Run the idempotent paper-trading session orchestration for one session.",
    )
    run_session_parser.add_argument("--strategy")
    run_session_parser.add_argument(
        "--as-of",
        metavar="YYYY-MM-DD",
        help="Target session date. Defaults to the latest completed persisted session.",
    )
    run_session_parser.add_argument("--risk-run-id", help="Explicit succeeded risk_evaluation run ID to consume.")
    run_session_parser.add_argument("--compact", action="store_true", default=False)
    run_session_parser.add_argument("--trigger-source")

    sync_paper_parser = subparsers.add_parser(
        "sync-paper-state",
        help="Sync broker order lifecycle, fills, positions, and account state into local storage.",
    )
    sync_paper_parser.add_argument("--strategy")
    sync_paper_parser.add_argument(
        "--as-of",
        metavar="YYYY-MM-DD",
        help="Target session date. Defaults to the latest completed persisted session.",
    )
    sync_paper_parser.add_argument("--compact", action="store_true", default=False)

    reconcile_parser = subparsers.add_parser(
        "reconcile-paper-execution",
        help="Reconcile broker paper state against local execution records and report unsafe drift.",
    )
    reconcile_parser.add_argument("--strategy")
    reconcile_parser.add_argument(
        "--as-of",
        metavar="YYYY-MM-DD",
        help="Target session date. Defaults to the latest completed persisted session.",
    )
    reconcile_parser.add_argument("--compact", action="store_true", default=False)
    reconcile_parser.add_argument("--trigger-source", default="worker_cli")

    operator_control_parser = subparsers.add_parser(
        "operator-control",
        help=(
            "Enable or disable persisted strategy execution, or trip/reset/show "
            "the durable global kill switch."
        ),
    )
    operator_control_parser.add_argument(
        "action",
        choices=(
            "enable",
            "disable",
            "trip-kill-switch",
            "reset-kill-switch",
            "show-kill-switch",
        ),
        help=(
            "enable/disable scope the per-strategy control; trip-kill-switch and "
            "reset-kill-switch mutate the persisted global submission halt; "
            "show-kill-switch prints the current global state without mutation."
        ),
    )
    operator_control_parser.add_argument(
        "--strategy",
        default="trend_following_daily",
        help=(
            "Target strategy for enable/disable. Ignored by kill-switch actions, "
            "which always act on the global submission halt."
        ),
    )
    operator_control_parser.add_argument("--reason")
    operator_control_parser.add_argument("--actor", default="worker_cli")
    operator_control_parser.add_argument("--trigger-source", default="worker_cli")
    operator_control_parser.add_argument("--summary-format", choices=("markdown", "json"), default="json")

    operator_status_parser = subparsers.add_parser(
        "operator-status",
        help="Show current strategy control state and recent operational failures.",
    )
    operator_status_parser.add_argument("--strategy", default="trend_following_daily")
    operator_status_parser.add_argument("--inspection-limit", type=int, default=5)
    operator_status_parser.add_argument("--summary-format", choices=("markdown", "json"), default="markdown")

    ingest_parser = subparsers.add_parser("ingest-bars", help="Ingest historical Polygon daily bars.")
    ingest_parser.add_argument("--from-date", metavar="YYYY-MM-DD", help="Ingest window start (inclusive).")
    ingest_parser.add_argument("--to-date", metavar="YYYY-MM-DD", help="Ingest window end (inclusive).")
    ingest_parser.add_argument("--symbols", nargs="+", metavar="TICKER", help="Symbol override list.")
    ingest_parser.add_argument("--trigger-source", default="worker_cli", help="Trigger label for the run record.")

    sync_meta_parser = subparsers.add_parser(
        "sync-metadata", help="Refresh symbol metadata from Polygon ticker overview."
    )
    sync_meta_parser.add_argument("--symbols", nargs="+", metavar="TICKER", help="Symbol override list.")
    sync_meta_parser.add_argument(
        "--dry-run", action="store_true", default=False, help="Print metadata without persisting."
    )

    sync_sessions_parser = subparsers.add_parser(
        "sync-sessions", help="Persist XNYS market sessions for a date range."
    )
    sync_sessions_parser.add_argument(
        "--from-date", metavar="YYYY-MM-DD", help="Session sync start (inclusive)."
    )
    sync_sessions_parser.add_argument(
        "--to-date", metavar="YYYY-MM-DD", help="Session sync end (inclusive)."
    )

    return parser


def run_placeholder_worker(interval_seconds: int) -> None:
    settings = enforce_startup_config(mode=ExecutionMode.BACKTEST)
    configure_logging(settings.logging)
    logger = logging.getLogger("trading_platform.worker")
    logger.info(
        "worker_started",
        extra={
            "context": {
                "interval_seconds": interval_seconds,
                "environment": settings.app.environment,
            }
        },
    )

    try:
        while True:
            logger.info(
                "worker_heartbeat",
                extra={"context": {"timestamp": datetime.now(UTC).isoformat()}},
            )
            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        logger.info("worker_stopped")


def run_dry_bootstrap(strategy_id: str) -> None:
    settings = enforce_startup_config(mode=ExecutionMode.BACKTEST)
    configure_logging(settings.logging)
    logger = logging.getLogger("trading_platform.worker")
    strategy = get_strategy_config(settings, strategy_id)
    report = run_persisted_dry_bootstrap(
        strategy.strategy_id,
        trigger_source="worker_cli",
        settings=settings,
    )
    logger.info(
        "worker_dry_run_completed",
        extra={"context": {"run_id": report.run_id, "strategy_id": report.strategy_id}},
    )
    print(json.dumps(report.to_dict(), default=str))


def run_backtest_command(args: argparse.Namespace) -> None:
    settings = enforce_startup_config(mode=ExecutionMode.BACKTEST)
    configure_logging(settings.logging)
    logger = logging.getLogger("trading_platform.worker")
    from_date, to_date = resolve_backtest_window(
        settings=settings,
        from_date_arg=args.from_date,
        to_date_arg=args.to_date,
    )
    report = run_backtest(
        args.strategy,
        from_date=from_date,
        to_date=to_date,
        trigger_source=args.trigger_source,
        settings=settings,
    )
    logger.info(
        "worker_backtest_completed",
        extra={
            "context": {
                "run_id": report.run_id,
                "strategy_id": report.strategy_id,
                "status": report.status,
                "from_date": from_date.isoformat(),
                "to_date": to_date.isoformat(),
            }
        },
    )
    indent = None if args.compact else 2
    print(json.dumps(report.to_dict(), indent=indent, default=str))


def run_report_backtest_command(args: argparse.Namespace) -> None:
    settings = enforce_startup_config(mode=ExecutionMode.BACKTEST)
    configure_logging(settings.logging)
    manifest = export_backtest_report(
        run_id=args.run_id,
        strategy_id=args.strategy,
        output_dir=args.output_dir,
        summary_format=args.summary_format,
        settings=settings,
    )
    print(manifest.rendered_summary)


def run_report_strategy_analytics_command(args: argparse.Namespace) -> None:
    settings = enforce_startup_config(mode=ExecutionMode.BACKTEST)
    configure_logging(settings.logging)
    logger = logging.getLogger("trading_platform.analytics.report.worker")
    report = build_strategy_analytics_report(
        strategy_id=args.strategy,
        backtest_run_id=args.backtest_run_id,
        paper_run_id=args.paper_run_id,
        inspection_limit=args.inspection_limit,
        settings=settings,
    )
    logger.info(
        "worker_strategy_analytics_report_completed",
        extra={
            "context": {
                "strategy_id": args.strategy,
                "backtest_run_id": args.backtest_run_id,
                "paper_run_id": args.paper_run_id,
                "inspection_limit": args.inspection_limit,
                "summary_format": args.summary_format,
            }
        },
    )
    print(render_strategy_analytics_report(report, summary_format=args.summary_format))


def run_evaluate_risk_command(args: argparse.Namespace) -> None:
    settings = enforce_startup_config(mode=ExecutionMode.BACKTEST)
    configure_logging(settings.logging)
    logger = logging.getLogger("trading_platform.worker")
    as_of_session = resolve_evaluation_session(
        settings=settings,
        as_of_arg=args.as_of,
    )
    report = run_risk_evaluation(
        args.strategy,
        as_of_session=as_of_session,
        trigger_source=args.trigger_source,
        settings=settings,
    )
    logger.info(
        "worker_risk_evaluation_completed",
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
    logger = logging.getLogger("trading_platform.worker")
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
    logger = logging.getLogger("trading_platform.worker")
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
    logger = logging.getLogger("trading_platform.worker")
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


def run_reconcile_paper_execution_command(args: argparse.Namespace) -> None:
    settings = enforce_startup_config(mode=ExecutionMode.PAPER)
    configure_logging(settings.logging)
    logger = logging.getLogger("trading_platform.worker")
    as_of_session = resolve_submission_session(
        settings=settings,
        as_of_arg=args.as_of,
    )
    strategy_id = args.strategy or settings.execution.paper_session_runner.default_strategy_id
    report = reconcile_paper_execution(
        strategy_id,
        as_of_session=as_of_session,
        settings=settings,
        trigger_source=args.trigger_source,
    )
    logger.info(
        "worker_paper_reconciliation_completed",
        extra={
            "context": {
                "strategy_id": strategy_id,
                "as_of_session": as_of_session.isoformat(),
                "finding_count": report.finding_count,
                "blocks_execution": report.blocks_execution,
            }
        },
    )
    indent = None if args.compact else 2
    print(json.dumps(report.to_dict(), indent=indent, default=str))


def run_operator_control_command(args: argparse.Namespace) -> None:
    settings = enforce_startup_config(mode=ExecutionMode.BACKTEST)
    configure_logging(settings.logging)
    logger = logging.getLogger("trading_platform.worker")
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


def run_operator_status_command(args: argparse.Namespace) -> None:
    settings = enforce_startup_config(mode=ExecutionMode.BACKTEST)
    configure_logging(settings.logging)
    logger = logging.getLogger("trading_platform.worker")
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


def run_ingest_bars(args: argparse.Namespace) -> None:
    from trading_platform.services.ingestion import ingest_daily_bars

    settings = enforce_startup_config(mode=ExecutionMode.BACKTEST)
    configure_logging(settings.logging)
    logger = logging.getLogger("trading_platform.worker")

    yesterday = date.today() - timedelta(days=1)
    to_date = date.fromisoformat(args.to_date) if args.to_date else yesterday
    from_date = (
        date.fromisoformat(args.from_date)
        if args.from_date
        else to_date - timedelta(days=settings.market_data.ingest.default_lookback_days)
    )
    symbols: list[str] = args.symbols or list(settings.market_data.ingest.universe)

    result = ingest_daily_bars(
        from_date=from_date,
        to_date=to_date,
        symbols=symbols,
        settings=settings.market_data,
        trigger_source=args.trigger_source,
    )
    summary = {
        "provider": result.provider,
        "from_date": result.from_date.isoformat(),
        "to_date": result.to_date.isoformat(),
        "symbols_requested": result.symbol_count,
        "bars_upserted": result.bars_upserted,
        "failed_symbols": result.symbols_failed,
        "succeeded": result.succeeded,
    }
    logger.info("ingest_bars_completed", extra={"context": summary})
    print(json.dumps(summary, default=str))


def run_sync_metadata(args: argparse.Namespace) -> None:
    from trading_platform.db.models.symbol import Symbol as SymbolModel
    from trading_platform.db.session import session_scope

    import uuid
    from datetime import UTC, datetime

    # sync-metadata's --dry-run flag deliberately never writes to the DB
    # (see the loop below), so the startup gate doesn't require reachability
    # for a dry-run invocation.
    settings = enforce_startup_config(mode=ExecutionMode.BACKTEST, require_database=not args.dry_run)
    configure_logging(settings.logging)
    logger = logging.getLogger("trading_platform.worker")

    # Import the standalone sync logic from the scripts module
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "scripts"))

    import importlib

    sync_mod = importlib.import_module("sync_symbol_metadata")

    symbols: list[str] = args.symbols or list(settings.market_data.metadata.universe)
    result = sync_mod.MetadataSyncResult(dry_run=args.dry_run)

    for ticker in symbols:
        try:
            overview = sync_mod._fetch_ticker_overview(ticker, settings)
            if overview is None:
                result.skipped.append(ticker)
                continue

            if args.dry_run:
                result.synced.append(ticker)
                continue

            with session_scope(settings) as db_session:
                sync_mod._upsert_symbol_metadata(db_session, ticker, overview)
            result.synced.append(ticker)
        except Exception as exc:
            logger.error(
                "metadata_sync_failed",
                extra={"context": {"ticker": ticker, "error": str(exc)}},
            )
            result.failed.append(ticker)

    print(json.dumps(result.to_dict(), default=str))


def run_sync_sessions(args: argparse.Namespace) -> None:
    from datetime import timedelta

    from trading_platform.db.session import session_scope
    from trading_platform.services.calendar import upsert_market_sessions

    settings = enforce_startup_config(mode=ExecutionMode.BACKTEST)
    configure_logging(settings.logging)
    logger = logging.getLogger("trading_platform.worker")

    exchange = settings.market_data.calendar.exchange
    yesterday = date.today() - timedelta(days=1)
    to_date = date.fromisoformat(args.to_date) if args.to_date else yesterday
    from_date = (
        date.fromisoformat(args.from_date)
        if args.from_date
        else to_date - timedelta(days=settings.market_data.ingest.default_lookback_days)
    )

    with session_scope(settings) as db_session:
        count = upsert_market_sessions(db_session, from_date, to_date, exchange)

    summary = {
        "exchange": exchange,
        "from_date": from_date.isoformat(),
        "to_date": to_date.isoformat(),
        "sessions_upserted": count,
    }
    logger.info("sync_sessions_completed", extra={"context": summary})
    print(json.dumps(summary, default=str))


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "serve":
        run_placeholder_worker(args.interval_seconds)
        return
    if args.command == "dry-run":
        run_dry_bootstrap(args.strategy)
        return
    if args.command == "backtest":
        run_backtest_command(args)
        return
    if args.command == "report-backtest":
        run_report_backtest_command(args)
        return
    if args.command == "report-strategy-analytics":
        run_report_strategy_analytics_command(args)
        return
    if args.command == "evaluate-risk":
        run_evaluate_risk_command(args)
        return
    if args.command == "submit-paper-orders":
        run_submit_paper_orders_command(args)
        return
    if args.command == "run-paper-session":
        run_paper_session_command(args)
        return
    if args.command == "sync-paper-state":
        run_sync_paper_state_command(args)
        return
    if args.command == "reconcile-paper-execution":
        run_reconcile_paper_execution_command(args)
        return
    if args.command == "operator-control":
        run_operator_control_command(args)
        return
    if args.command == "operator-status":
        run_operator_status_command(args)
        return
    if args.command == "ingest-bars":
        run_ingest_bars(args)
        return
    if args.command == "sync-metadata":
        run_sync_metadata(args)
        return
    if args.command == "sync-sessions":
        run_sync_sessions(args)
        return

    parser.error(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
