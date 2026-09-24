"""ORCH-05 / D-19: deploy-config assertions parsed directly from disk.

No database, no app import -- pure `yaml.safe_load` over the repo's own
`docker-compose.yml` / `render.yaml`, plus a plain-text scan of the
`Dockerfile` CMD line. This is the source of truth for "the compose worker
runs the production Job runner, not the retired placeholder serve loop" and
"local vs. deployed mutation-guard defaults are set correctly" -- it fails
the moment either config file drifts, independent of any Python import
boundary test.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(_ROOT))


def _load_compose() -> dict:
    return yaml.safe_load((_ROOT / "docker-compose.yml").read_text())


def _load_render() -> dict:
    return yaml.safe_load((_ROOT / "render.yaml").read_text())


def test_compose_worker_runs_job_runner() -> None:
    compose = _load_compose()
    assert compose["services"]["worker"]["command"] == [
        "python",
        "-m",
        "trading_platform.worker",
        "run-jobs",
    ]


def test_no_deploy_config_starts_serve_loop() -> None:
    compose = _load_compose()
    for service_name, service in compose["services"].items():
        command = service.get("command")
        if command is None:
            continue
        tokens = command if isinstance(command, list) else str(command).split()
        assert not ("trading_platform.worker" in tokens and "serve" in tokens), (
            f"docker-compose.yml service '{service_name}' still starts the "
            "placeholder serve loop"
        )

    render = _load_render()
    for service in render.get("services", []):
        for field in ("startCommand", "dockerCommand"):
            value = service.get(field)
            if isinstance(value, str):
                assert "trading_platform.worker serve" not in value

    dockerfile_text = (_ROOT / "Dockerfile").read_text()
    cmd_lines = [line for line in dockerfile_text.splitlines() if line.startswith("CMD")]
    assert cmd_lines, "Dockerfile has no CMD line"
    for cmd_line in cmd_lines:
        assert "trading_platform.worker" not in cmd_line


def test_compose_enables_mutations_for_local_api() -> None:
    compose = _load_compose()
    assert (
        compose["services"]["api"]["environment"]["TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED"]
        == "true"
    )


def test_render_disables_mutations() -> None:
    render = _load_render()
    found = False
    for service in render.get("services", []):
        for env_var in service.get("envVars", []):
            if (
                env_var.get("key") == "TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED"
                and env_var.get("value") == "false"
            ):
                found = True
    assert found, "no render.yaml service disables TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED"
