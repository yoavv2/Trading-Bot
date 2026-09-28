"""Zero-write proof for the D-29 exempt read/report paths (D-31).

Pins that the three exempt CLI scripts (export_backtest_report.py,
report_strategy_analytics.py, operator_status.py), the analytics GET route,
and the pure strategy-control-state reads genuinely perform zero database
writes -- proven at runtime via an engine ``before_cursor_execute`` spy plus a
Session ``before_flush`` spy, not just by code inspection.
"""

from __future__ import annotations

import inspect
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session as SASession

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.export_backtest_report import main as export_backtest_report_main  # noqa: E402
from scripts.operator_status import main as operator_status_main  # noqa: E402
from scripts.report_strategy_analytics import main as report_strategy_analytics_main  # noqa: E402
from tests.test_analytics_service import (  # noqa: E402
    _seed_market_data,
    _trading_fixture,
    migrated_analytics_db,  # noqa: F401 (reused DB harness fixture)
    strategy_config_override,  # noqa: F401 (reused DB harness fixture)
)

from trading_platform.api.app import create_app  # noqa: E402
from trading_platform.core.settings import Settings, load_settings  # noqa: E402
from trading_platform.db.models import (  # noqa: E402
    BacktestMetric,
    ExecutionEvent,
    Strategy,
    StrategyRun,
    SystemControl,
)
from trading_platform.db.session import session_scope  # noqa: E402
from trading_platform.services.backtest_reporting import build_backtest_report  # noqa: E402
from trading_platform.services.backtesting import run_backtest  # noqa: E402
from trading_platform.services.operator_controls import (  # noqa: E402
    OperatorControlService,
    ensure_strategy_control_state,
    load_strategy_control_state,
)


@dataclass
class WriteSpy:
    statements: list[str] = field(default_factory=list)
    flushes: list[tuple[int, int, int]] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.statements and not self.flushes


@contextmanager
def write_spy():
    """Records every INSERT/UPDATE/DELETE cursor execution plus any
    non-empty Session before_flush (new/dirty/deleted) for the duration of
    the ``with`` block. Attach AFTER seeding/startup so setup writes are not
    counted."""
    spy = WriteSpy()

    def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        stripped = statement.lstrip()
        first_token = stripped.split(None, 1)[0].upper() if stripped else ""
        if first_token in {"INSERT", "UPDATE", "DELETE"}:
            spy.statements.append(statement)

    def _before_flush(session, flush_context, instances):
        counts = (len(session.new), len(session.dirty), len(session.deleted))
        if any(counts):
            spy.flushes.append(counts)

    event.listen(Engine, "before_cursor_execute", _before_cursor_execute)
    event.listen(SASession, "before_flush", _before_flush)
    try:
        yield spy
    finally:
        event.remove(Engine, "before_cursor_execute", _before_cursor_execute)
        event.remove(SASession, "before_flush", _before_flush)


def _row_counts(settings: Settings) -> dict[str, Any]:
    with session_scope(settings) as session:
        strategy_updated_at = session.execute(
            select(Strategy.updated_at).where(Strategy.strategy_id == "trend_following_daily")
        ).scalar_one_or_none()
        return {
            "strategies": session.execute(select(func.count()).select_from(Strategy)).scalar_one(),
            "strategy_runs": session.execute(select(func.count()).select_from(StrategyRun)).scalar_one(),
            "backtest_metrics": session.execute(
                select(func.count()).select_from(BacktestMetric)
            ).scalar_one(),
            "execution_events": session.execute(
                select(func.count()).select_from(ExecutionEvent)
            ).scalar_one(),
            "system_controls": session.execute(
                select(func.count()).select_from(SystemControl)
            ).scalar_one(),
            "strategy_updated_at": strategy_updated_at,
        }


def _seed_completed_backtest(settings: Settings) -> str:
    _seed_market_data(_trading_fixture())
    run_report = run_backtest(
        "trend_following_daily",
        from_date=date(2024, 1, 2),
        to_date=date(2024, 1, 10),
        settings=settings,
        trigger_source="pytest",
    )
    return run_report.run_id


def test_export_backtest_report_script_writes_no_rows(
    migrated_analytics_db: str,
    strategy_config_override: None,
    tmp_path: Path,
) -> None:
    settings = load_settings()
    _seed_completed_backtest(settings)
    before = _row_counts(settings)

    with write_spy() as spy:
        exit_code = export_backtest_report_main(
            ["--output-dir", str(tmp_path), "--summary-format", "json"]
        )

    assert exit_code == 0
    assert (tmp_path / "summary.json").exists()
    assert (tmp_path / "trades.csv").exists()
    assert (tmp_path / "equity_curve.csv").exists()
    assert spy.is_empty
    assert _row_counts(settings) == before


def test_report_strategy_analytics_script_writes_no_rows(
    migrated_analytics_db: str,
    strategy_config_override: None,
) -> None:
    settings = load_settings()
    _seed_completed_backtest(settings)
    before = _row_counts(settings)

    with write_spy() as spy:
        exit_code = report_strategy_analytics_main(
            ["--strategy", "trend_following_daily", "--summary-format", "json"]
        )

    assert exit_code == 0
    assert spy.is_empty
    assert _row_counts(settings) == before


def test_operator_status_script_writes_no_rows(
    migrated_analytics_db: str,
    strategy_config_override: None,
) -> None:
    settings = load_settings()
    _seed_completed_backtest(settings)
    before = _row_counts(settings)
    assert before["strategies"] == 1

    with write_spy() as spy:
        exit_code = operator_status_main(
            ["--strategy", "trend_following_daily", "--summary-format", "json"]
        )

    assert exit_code == 0
    assert spy.is_empty
    assert _row_counts(settings) == before


def test_operator_status_without_strategy_row_returns_registry_default(
    migrated_analytics_db: str,
    strategy_config_override: None,
) -> None:
    settings = load_settings()
    before = _row_counts(settings)
    assert before["strategies"] == 0

    with write_spy() as spy:
        state = load_strategy_control_state("trend_following_daily", settings=settings)

    assert state.status == "active"
    assert state.updated_at is None
    assert spy.is_empty
    after = _row_counts(settings)
    assert after["strategies"] == 0


def test_empty_db_read_default_matches_ensure_status(
    migrated_analytics_db: str,
    strategy_config_override: None,
) -> None:
    settings = load_settings()
    assert _row_counts(settings)["strategies"] == 0

    read_state = load_strategy_control_state("trend_following_daily", settings=settings)
    ensure_state = ensure_strategy_control_state("trend_following_daily", settings=settings)

    assert read_state.status == ensure_state.status


def test_analytics_get_route_writes_no_rows(
    migrated_analytics_db: str,
    strategy_config_override: None,
) -> None:
    settings = load_settings()
    _seed_completed_backtest(settings)
    before = _row_counts(settings)

    with TestClient(create_app()) as client:
        with write_spy() as spy:
            response = client.get("/api/v1/analytics/strategies/trend_following_daily")

    assert response.status_code == 200
    assert spy.is_empty
    assert _row_counts(settings) == before


def test_ensure_strategy_control_state_preserves_get_or_create(
    migrated_analytics_db: str,
    strategy_config_override: None,
) -> None:
    settings = load_settings()
    before = _row_counts(settings)
    assert before["strategies"] == 0

    state = ensure_strategy_control_state("trend_following_daily", settings=settings)

    assert state.status == "active"
    after = _row_counts(settings)
    assert after["strategies"] == 1


def test_write_spy_detects_writes(
    migrated_analytics_db: str,
    strategy_config_override: None,
) -> None:
    """Positive control: proves write_spy actually fires on a real write,
    so every spy.is_empty assertion above is evidence of purity rather than
    a listener that is attached wrong or never dispatched (advisor review)."""
    settings = load_settings()
    assert _row_counts(settings)["strategies"] == 0

    with write_spy() as spy:
        ensure_strategy_control_state("trend_following_daily", settings=settings)

    assert any(
        statement.lstrip().upper().startswith("INSERT") for statement in spy.statements
    )
    assert spy.flushes
    assert not spy.is_empty


def test_read_functions_contain_no_write_calls() -> None:
    build_source = inspect.getsource(build_backtest_report)
    for forbidden in ("_upsert_backtest_metric", "session.add", ".flush(", "commit"):
        assert forbidden not in build_source

    get_strategy_state_source = inspect.getsource(OperatorControlService.get_strategy_state)
    assert "ensure_strategy_record" not in get_strategy_state_source
    assert ".flush(" not in get_strategy_state_source
