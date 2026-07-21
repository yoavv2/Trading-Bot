"""Argparse construction for the worker CLI (STRUCT-03: extracted from __main__.py)."""

from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trading-platform-worker")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve", help="Run the placeholder worker loop.")
    serve_parser.add_argument("--interval-seconds", type=int, default=30)

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
    analytics_parser.add_argument(
        "--backtest-run-id", help="Explicit backtest run ID to summarize."
    )
    analytics_parser.add_argument(
        "--paper-run-id", help="Explicit paper execution run ID to summarize."
    )
    analytics_parser.add_argument("--inspection-limit", type=int, default=5)
    analytics_parser.add_argument(
        "--summary-format", choices=("markdown", "json"), default="markdown"
    )

    operator_status_parser = subparsers.add_parser(
        "operator-status",
        help="Show current strategy control state and recent operational failures.",
    )
    operator_status_parser.add_argument("--strategy", default="trend_following_daily")
    operator_status_parser.add_argument("--inspection-limit", type=int, default=5)
    operator_status_parser.add_argument(
        "--summary-format", choices=("markdown", "json"), default="markdown"
    )

    run_jobs_parser = subparsers.add_parser(
        "run-jobs",
        help="Run the restart-safe generic Job worker loop: claim, execute, sweep.",
    )
    run_jobs_parser.add_argument(
        "--worker-id",
        default=None,
        help="Worker identity for lease ownership. Defaults to '<hostname>:<pid>'.",
    )
    run_jobs_parser.add_argument(
        "--max-jobs",
        type=int,
        default=None,
        help="Stop after executing this many Jobs. Defaults to running indefinitely.",
    )
    run_jobs_parser.add_argument(
        "--once",
        action="store_true",
        default=False,
        help="Run a single poll pass (sweep + at most one claim) and exit.",
    )
    run_jobs_parser.add_argument("--compact", action="store_true", default=False)

    return parser
