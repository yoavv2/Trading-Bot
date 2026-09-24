"""D-02 tests: run_backtest threads an opaque job_id into the creating
transaction, and run-detail reads expose it (D-07 backend half).

Reuses the migrated_backtest_db / strategy_config_override / _seed_market_data
fixtures from tests/test_backtest_runner.py rather than duplicating the
Postgres-DB-lifecycle harness.
"""

from __future__ import annotations

import sys
import uuid
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.test_backtest_runner import (  # noqa: E402
    _seed_market_data,
    migrated_backtest_db,  # noqa: F401 (reused DB harness fixture)
    strategy_config_override,  # noqa: F401 (reused DB harness fixture)
)

from trading_platform.core.settings import load_settings  # noqa: E402
from trading_platform.db.models import StrategyRun  # noqa: E402
from trading_platform.db.session import session_scope  # noqa: E402
from trading_platform.jobs.dependencies import submit_job  # noqa: E402
from trading_platform.services import backtesting  # noqa: E402
from trading_platform.services.backtesting import run_backtest  # noqa: E402
from trading_platform.services.operator_reads import OperatorReadService  # noqa: E402


def test_run_backtest_links_job_in_creating_transaction(
    migrated_backtest_db: str,
    strategy_config_override: None,
) -> None:
    _seed_market_data()
    settings = load_settings()

    job_id = submit_job(job_type="backtest", payload={}, settings=settings)

    recorded: dict[str, Any] = {}
    original_execute = backtesting._execute_backtest_run

    def _probe(settings_arg, *, run_id, strategy, from_date, to_date):
        with session_scope(settings_arg) as session:
            probed_run = session.execute(
                select(StrategyRun).where(StrategyRun.id == run_id)
            ).scalar_one()
            recorded["status"] = probed_run.status
            recorded["job_id"] = probed_run.job_id
        return original_execute(
            settings_arg,
            run_id=run_id,
            strategy=strategy,
            from_date=from_date,
            to_date=to_date,
        )

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(backtesting, "_execute_backtest_run", _probe)
        report = run_backtest(
            "trend_following_daily",
            from_date=date(2024, 1, 2),
            to_date=date(2024, 1, 10),
            settings=settings,
            trigger_source="job",
            job_id=job_id,
        )

    assert recorded["status"].value == "running"
    assert recorded["job_id"] == job_id

    with session_scope(settings) as session:
        strategy_run = session.execute(
            select(StrategyRun).where(StrategyRun.id == uuid.UUID(report.run_id))
        ).scalar_one()
        assert strategy_run.job_id == job_id
        assert strategy_run.trigger_source == "job"


def test_run_backtest_without_job_id_leaves_link_null(
    migrated_backtest_db: str,
    strategy_config_override: None,
) -> None:
    _seed_market_data()
    settings = load_settings()

    report = run_backtest(
        "trend_following_daily",
        from_date=date(2024, 1, 2),
        to_date=date(2024, 1, 10),
        settings=settings,
        trigger_source="pytest",
    )

    with session_scope(settings) as session:
        strategy_run = session.execute(
            select(StrategyRun).where(StrategyRun.id == uuid.UUID(report.run_id))
        ).scalar_one()
        assert strategy_run.job_id is None


def test_run_detail_exposes_job_id(
    migrated_backtest_db: str,
    strategy_config_override: None,
) -> None:
    _seed_market_data()
    settings = load_settings()

    job_id = submit_job(job_type="backtest", payload={}, settings=settings)
    linked_report = run_backtest(
        "trend_following_daily",
        from_date=date(2024, 1, 2),
        to_date=date(2024, 1, 10),
        settings=settings,
        trigger_source="job",
        job_id=job_id,
    )
    unlinked_report = run_backtest(
        "trend_following_daily",
        from_date=date(2024, 1, 2),
        to_date=date(2024, 1, 10),
        settings=settings,
        trigger_source="pytest",
    )

    operator_reads = OperatorReadService(settings)
    linked_detail = operator_reads.get_run_detail(linked_report.run_id)
    unlinked_detail = operator_reads.get_run_detail(unlinked_report.run_id)

    assert linked_detail["run"]["job_id"] == str(job_id)
    assert unlinked_detail["run"]["job_id"] is None
