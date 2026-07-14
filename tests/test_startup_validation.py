"""Phase 10 startup hardening: process-entrypoint gate (CFG-04, CFG-06).

`enforce_startup_config` wires the pure config validator (10-01,
`services/config/validation.py`, relocated from `core/config_validation.py`
in 12-02) into a process-boot-time gate: validate the raw
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
from trading_platform.services.config.validation import ExecutionMode


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


# --- CFG-06: ordering — the gate runs before any domain service is constructed --


def test_submit_paper_orders_command_exits_before_domain_service_constructed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An invalid paper config (missing broker secrets — the default test-env
    state) must SystemExit at the gate before `run_paper_order_submission`
    (the domain service that would construct a broker client) is ever
    called."""
    import trading_platform.worker.__main__ as worker_main

    called = {"value": False}

    def _fail_if_called(*args: object, **kwargs: object) -> None:
        called["value"] = True
        raise AssertionError("run_paper_order_submission must not be called")

    monkeypatch.setattr(worker_main, "run_paper_order_submission", _fail_if_called)

    parser = worker_main.build_parser()
    args = parser.parse_args(
        [
            "submit-paper-orders",
            "--strategy",
            "trend_following_daily",
            "--as-of",
            "2024-01-05",
            "--compact",
        ]
    )

    with pytest.raises(SystemExit) as exc_info:
        worker_main.run_submit_paper_orders_command(args)

    assert exc_info.value.code == CONFIG_VALIDATION_EXIT_CODE
    assert called["value"] is False


# --- Backtest/API boot with empty Alpaca keys still succeeds -----------------


def test_api_lifespan_boots_with_empty_alpaca_keys_and_no_reachable_db() -> None:
    """mode=BACKTEST requires no broker secret, and the API gate does not
    require DB reachability (see api/app.py's lifespan comment) — an
    API-only boot with empty broker keys and no live DB must still succeed."""
    payload = _refused_port_db_payload()
    payload["broker"]["alpaca"]["api_key"] = ""
    payload["broker"]["alpaca"]["api_secret"] = ""

    settings = enforce_startup_config(
        mode=ExecutionMode.BACKTEST,
        require_database=False,
        payload=payload,
    )

    assert isinstance(settings, Settings)
    assert settings.broker.alpaca.api_key == ""


def test_gate_is_wired_into_api_worker_and_bootstrap_entrypoints() -> None:
    """Static proof the gate is invoked at every named entrypoint (not hooked
    into `load_settings`, which stays untouched — see 10-01/10-05 key
    facts)."""
    import inspect

    import trading_platform.api.app as api_app
    import trading_platform.services.bootstrap as bootstrap
    import trading_platform.worker.__main__ as worker_main

    assert "enforce_startup_config" in inspect.getsource(api_app.lifespan)
    assert "enforce_startup_config" in inspect.getsource(bootstrap.run_dry_bootstrap)

    gated_worker_functions = [
        worker_main.run_placeholder_worker,
        worker_main.run_dry_bootstrap,
        worker_main.run_backtest_command,
        worker_main.run_report_backtest_command,
        worker_main.run_report_strategy_analytics_command,
        worker_main.run_evaluate_risk_command,
        worker_main.run_submit_paper_orders_command,
        worker_main.run_paper_session_command,
        worker_main.run_sync_paper_state_command,
        worker_main.run_reconcile_paper_execution_command,
        worker_main.run_operator_control_command,
        worker_main.run_operator_status_command,
        worker_main.run_ingest_bars,
        worker_main.run_sync_metadata,
        worker_main.run_sync_sessions,
    ]
    for func in gated_worker_functions:
        assert "enforce_startup_config" in inspect.getsource(func), (
            f"{func.__name__} does not call enforce_startup_config"
        )

    import trading_platform.core.settings as settings_module

    assert "enforce_startup_config" not in inspect.getsource(settings_module)
