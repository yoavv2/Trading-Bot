"""Phase 10 startup hardening: process-entrypoint gate (CFG-04, CFG-06).

`enforce_startup_config` wires the pure config validator (10-01,
`config_validation.py`) into a process-boot-time gate: validate the raw
payload, then (by default) preflight DB reachability, and exit non-zero with
a single actionable message on any failure — BEFORE any domain service
initializes.

The unreachable-DB tests point at `localhost:1` — a refused port on the
loopback interface — rather than a nonexistent hostname, so the connection
fails immediately and deterministically (a bad hostname risks a multi-second
DNS/TCP timeout).

`test_gate_blocks_service_init_ordering.py`-style ordering proof (CFG-06 —
no domain service initializes when the gate exits first) lives in Task 2's
tests, once the gate is wired into a real entrypoint.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trading_platform.core.settings import Settings, build_settings_payload
from trading_platform.core.startup import CONFIG_VALIDATION_EXIT_CODE, enforce_startup_config
from trading_platform.services.concurrency_guard import CONCURRENT_RUN_LOCK_EXIT_CODE
from trading_platform.core.config_validation import ExecutionMode


def _raw_payload() -> dict[str, Any]:
    """A fresh, deep-copied raw pre-pydantic payload for mutation in tests."""
    return copy.deepcopy(build_settings_payload())


def _reachable_db_payload() -> dict[str, Any]:
    payload = _raw_payload()
    payload["database"] = {
        "host": "localhost",
        "port": 5432,
        "name": "trading_platform",
        "user": "trading_platform",
        "password": "trading_platform",
        "echo": False,
        "driver": "psycopg",
    }
    return payload


def _refused_port_db_payload() -> dict[str, Any]:
    payload = _raw_payload()
    payload["database"] = {
        "host": "localhost",
        "port": 1,
        "name": "trading_platform",
        "user": "trading_platform",
        "password": "trading_platform",
        "echo": False,
        "driver": "psycopg",
    }
    return payload


# --- distinct, non-zero exit code -------------------------------------------


def test_config_validation_exit_code_is_non_zero_and_distinct_from_lock_code() -> None:
    assert CONFIG_VALIDATION_EXIT_CODE != 0
    assert CONFIG_VALIDATION_EXIT_CODE != CONCURRENT_RUN_LOCK_EXIT_CODE


# --- missing secret: CFG-01 surfaced at startup -----------------------------


def test_missing_paper_secret_exits_non_zero_naming_field(capsys: pytest.CaptureFixture[str]) -> None:
    payload = _reachable_db_payload()
    payload["broker"]["alpaca"]["api_key"] = ""
    payload["broker"]["alpaca"]["api_secret"] = ""

    with pytest.raises(SystemExit) as exc_info:
        enforce_startup_config(mode=ExecutionMode.PAPER, payload=payload)

    assert exc_info.value.code == CONFIG_VALIDATION_EXIT_CODE
    assert exc_info.value.code != 0

    captured = capsys.readouterr()
    assert "broker.alpaca.api_key" in captured.err


# --- out-of-range tolerance: CFG-05 surfaced at startup (not a raw pydantic
# traceback from a subsequent load_settings() call) -------------------------


def test_out_of_range_tolerance_exits_non_zero_naming_field(capsys: pytest.CaptureFixture[str]) -> None:
    payload = _reachable_db_payload()
    payload["strategies"]["trend_following_daily"]["risk"]["risk_per_trade"] = 2.0

    with pytest.raises(SystemExit) as exc_info:
        enforce_startup_config(mode=ExecutionMode.BACKTEST, payload=payload)

    assert exc_info.value.code == CONFIG_VALIDATION_EXIT_CODE
    assert exc_info.value.code != 0

    captured = capsys.readouterr()
    assert "strategies.trend_following_daily.risk.risk_per_trade" in captured.err


# --- unreachable DB: CFG-04 ---------------------------------------------------


def test_unreachable_db_exits_non_zero(capsys: pytest.CaptureFixture[str]) -> None:
    payload = _refused_port_db_payload()

    with pytest.raises(SystemExit) as exc_info:
        enforce_startup_config(mode=ExecutionMode.BACKTEST, payload=payload)

    assert exc_info.value.code == CONFIG_VALIDATION_EXIT_CODE
    assert exc_info.value.code != 0

    captured = capsys.readouterr()
    assert "localhost:1/trading_platform" in captured.err


def test_unreachable_db_skipped_when_require_database_false() -> None:
    payload = _refused_port_db_payload()

    settings = enforce_startup_config(
        mode=ExecutionMode.BACKTEST,
        require_database=False,
        payload=payload,
    )

    assert isinstance(settings, Settings)


# --- valid config + reachable DB: returns Settings, no exit -----------------


def test_valid_passes_config_and_reachable_db_returns_settings() -> None:
    payload = _reachable_db_payload()

    settings = enforce_startup_config(mode=ExecutionMode.BACKTEST, payload=payload)

    assert isinstance(settings, Settings)
    assert settings.database.host == "localhost"
