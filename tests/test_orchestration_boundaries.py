"""Structural fences for the HTTP-only operator orchestration surface."""

from __future__ import annotations

import argparse
import ast
from pathlib import Path

import pytest

from trading_platform.worker.commands import DISPATCH
from trading_platform.worker.commands.run_jobs import run_jobs_command
from trading_platform.worker.parser import build_parser

_ROOT = Path(__file__).resolve().parents[1]
_RETAINED_CLI_COMMANDS = {
    "serve",
    "report-backtest",
    "report-strategy-analytics",
    "operator-status",
    "run-jobs",
}
_RETAINED_DISPATCH_COMMANDS = {
    "report-backtest",
    "report-strategy-analytics",
    "operator-status",
    "run-jobs",
}
_REMOVED_CLI_COMMANDS = {
    "dry-run",
    "backtest",
    "evaluate-risk",
    "submit-paper-orders",
    "run-paper-session",
    "sync-paper-state",
    "reconcile-paper-execution",
    "operator-control",
    "ingest-bars",
    "sync-metadata",
    "sync-sessions",
}


def _parser_commands(parser: argparse.ArgumentParser) -> set[str]:
    actions = [
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    ]
    assert len(actions) == 1
    return set(actions[0].choices)


def test_parser_exposes_only_retained_cli_surface() -> None:
    assert _parser_commands(build_parser()) == _RETAINED_CLI_COMMANDS


def test_dispatch_exposes_only_retained_non_serve_commands() -> None:
    assert set(DISPATCH) == _RETAINED_DISPATCH_COMMANDS


@pytest.mark.parametrize("command", sorted(_REMOVED_CLI_COMMANDS))
def test_removed_cli_commands_are_rejected_by_parser(command: str) -> None:
    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args([command])

    assert exc_info.value.code == 2


def test_run_jobs_once_parses_and_dispatches_to_thin_worker_adapter() -> None:
    args = build_parser().parse_args(["run-jobs", "--once"])

    assert args.command == "run-jobs"
    assert args.once is True
    assert DISPATCH[args.command] is run_jobs_command


def test_worker_entrypoint_has_only_serve_special_case_and_dispatch_lookup() -> None:
    entrypoint = _ROOT / "src/trading_platform/worker/__main__.py"
    tree = ast.parse(entrypoint.read_text(), filename=str(entrypoint))
    main = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    special_cases = [node for node in main.body if isinstance(node, ast.If)]

    assert len(special_cases) == 2
    assert isinstance(special_cases[0].test, ast.Compare)
    assert ast.unparse(special_cases[0].test) == "args.command == 'serve'"
    assert "DISPATCH.get(args.command)" in ast.unparse(main)
