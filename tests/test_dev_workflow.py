"""Canonical local-development workflow (`make dev`) and the opt-in Compose stack.

Pins the invariants that keep one API and one worker on the dev machine:
Compose starts only the database unless the `stack` profile is requested,
`make dev` refuses to start when a dev port is taken, runs the real
long-running Job worker, reloads the API on code changes, enables mutations
only for its own API process, and tears every child down on Ctrl-C.

Recipes are inspected with `make -n`, which is safe because the Makefile never
recurses via `$(MAKE)` (a recursive line would execute even under `-n`). The
process tests substitute `sleep` stubs for the real commands.
"""

from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
import time
from pathlib import Path

import pytest
import yaml

from trading_platform.core.settings import OrchestrationSettings
from trading_platform.worker.commands import DISPATCH
from trading_platform.worker.parser import build_parser

_ROOT = Path(__file__).resolve().parents[1]
_MUTATIONS_ENV = "TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED=true"

requires_make_and_lsof = pytest.mark.skipif(
    shutil.which("make") is None or shutil.which("lsof") is None,
    reason="make and lsof are required",
)


def _make_dry_run(target: str) -> str:
    result = subprocess.run(
        ["make", "-n", "--no-print-directory", target],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _compose_services() -> dict[str, dict]:
    return yaml.safe_load((_ROOT / "docker-compose.yml").read_text())["services"]


def test_compose_is_database_only_by_default() -> None:
    services = _compose_services()

    assert "profiles" not in services["db"]
    for name in ("migrate", "api", "worker"):
        assert services[name]["profiles"] == ["stack"], name
    assert {name for name, svc in services.items() if "profiles" not in svc} == {"db"}


@requires_make_and_lsof
@pytest.mark.parametrize("target", ["up", "down", "logs"])
def test_compose_make_targets_opt_into_the_stack_profile(target: str) -> None:
    assert "docker compose --profile stack" in _make_dry_run(target)


@requires_make_and_lsof
def test_make_api_reloads_and_is_configuration_neutral() -> None:
    recipe = _make_dry_run("api")

    assert "uvicorn trading_platform.api.app:app" in recipe
    assert "--reload --reload-dir src" in recipe
    assert "--host 127.0.0.1 --port 8000" in recipe
    assert "MUTATIONS_ENABLED" not in recipe


@requires_make_and_lsof
def test_make_worker_restarts_the_long_running_job_worker() -> None:
    recipe = _make_dry_run("worker")

    assert "watchfiles --filter python" in recipe
    assert "-m trading_platform.worker run-jobs' src" in recipe
    # The watched command is the real, unbounded Job loop: run-jobs is a
    # dispatched command and, without flags, neither exits after one pass
    # nor after a job count.
    assert "run-jobs" in DISPATCH
    args = build_parser().parse_args(["run-jobs"])
    assert args.once is False
    assert args.max_jobs is None


@requires_make_and_lsof
def test_make_dev_enables_mutations_only_for_its_api_process() -> None:
    recipe = _make_dry_run("dev")

    assert recipe.count(_MUTATIONS_ENV) == 1
    assert f"{_MUTATIONS_ENV} PYTHONPATH=src .venv/bin/uvicorn" in recipe
    assert "trading_platform.worker run-jobs" in recipe
    assert "npm run dev -- --port 3000" in recipe
    # The application default stays closed; only `make dev` opts in.
    assert OrchestrationSettings().mutations_enabled is False


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _stub_overrides(ports: list[int], marker: int) -> list[str]:
    return [
        f"DEV_PORTS={' '.join(str(port) for port in ports)}",
        f"API_CMD=sleep {marker + 1}",
        f"WORKER_CMD=sleep {marker + 2}",
        f"CONSOLE_CMD=sleep {marker + 3}",
    ]


def _running_stubs(marker: int) -> set[int]:
    running = set()
    for offset in (1, 2, 3):
        result = subprocess.run(
            ["pgrep", "-f", f"^sleep {marker + offset}$"], capture_output=True, text=True
        )
        if result.returncode == 0:
            running.add(offset)
    return running


def _kill_stubs(marker: int) -> None:
    for offset in (1, 2, 3):
        subprocess.run(["pkill", "-f", f"^sleep {marker + offset}$"], check=False)


@requires_make_and_lsof
def test_make_dev_fails_fast_when_a_dev_port_is_taken() -> None:
    marker = 31000 + os.getpid() % 1000 * 10
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        busy_port = int(occupied.getsockname()[1])

        try:
            result = subprocess.run(
                [
                    "make",
                    "--no-print-directory",
                    "dev",
                    *_stub_overrides([_free_port(), busy_port], marker),
                ],
                cwd=_ROOT,
                capture_output=True,
                text=True,
                timeout=15,
            )
            assert result.returncode != 0
            assert f"port {busy_port} is already in use" in result.stderr
            assert _running_stubs(marker) == set()
        finally:
            _kill_stubs(marker)


@requires_make_and_lsof
def test_make_dev_ctrl_c_stops_every_child() -> None:
    marker = 32000 + os.getpid() % 1000 * 10
    process = subprocess.Popen(
        ["make", "--no-print-directory", "dev", *_stub_overrides([_free_port()], marker)],
        cwd=_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 15
        while _running_stubs(marker) != {1, 2, 3}:
            assert process.poll() is None, "make dev exited before starting its children"
            assert time.monotonic() < deadline, "make dev did not start all three children"
            time.sleep(0.1)

        # Ctrl-C in a terminal signals the whole foreground process group.
        os.killpg(process.pid, signal.SIGINT)
        process.wait(timeout=15)

        deadline = time.monotonic() + 5
        while _running_stubs(marker) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert _running_stubs(marker) == set()
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        _kill_stubs(marker)
