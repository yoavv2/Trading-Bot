"""Argparse construction for the worker CLI (STRUCT-03: extracted from __main__.py)."""

from __future__ import annotations

import argparse


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
