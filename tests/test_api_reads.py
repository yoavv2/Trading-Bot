from __future__ import annotations

import sys
import uuid
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.test_analytics_service import (  # noqa: E402
    _seed_market_data,
    _seed_paper_operational_state,
    _seed_strategy_record,
    _trading_fixture,
    migrated_analytics_db,  # noqa: F401 (reused DB harness fixture)
    strategy_config_override,  # noqa: F401 (reused DB harness fixture)
)

from trading_platform.api.app import create_app  # noqa: E402
from trading_platform.core.settings import clear_settings_cache, load_settings  # noqa: E402
from trading_platform.services.analytics import StrategyAnalyticsService  # noqa: E402
from trading_platform.services.backtesting import run_backtest  # noqa: E402
from trading_platform.services.operator_controls import OperatorControlService  # noqa: E402
from trading_platform.services.operator_reads import (  # noqa: E402
    OperatorReadFilters,
    OperatorReadService,
)


def _build_client() -> TestClient:
    clear_settings_cache()
    return TestClient(create_app())


def test_strategy_analytics_and_run_reads_match_shared_services(
    migrated_analytics_db: str,
    strategy_config_override: None,
) -> None:
    _seed_market_data(_trading_fixture())
    settings = load_settings()
    backtest_report = run_backtest(
        "trend_following_daily",
        from_date=date(2024, 1, 2),
        to_date=date(2024, 1, 10),
        settings=settings,
        trigger_source="pytest",
    )
    paper_state = _seed_paper_operational_state()

    analytics_service = StrategyAnalyticsService(settings)
    operator_reads = OperatorReadService(settings)
    run_filters = OperatorReadFilters(
        strategy_id="trend_following_daily",
        run_type="paper_execution",
        status="succeeded",
        session_start=date(2024, 1, 5),
        session_end=date(2024, 1, 5),
        limit=5,
    )
    expected_analytics = analytics_service.summarize_strategy(
        strategy_id="trend_following_daily",
        backtest_run_id=backtest_report.run_id,
        paper_run_id=paper_state["paper_run_id"],
        inspection_limit=3,
    )
    expected_runs = operator_reads.list_runs(run_filters)
    expected_run_detail = operator_reads.get_run_detail(paper_state["paper_run_id"])

    with _build_client() as client:
        strategies = client.get("/api/v1/strategies")
        strategy_detail = client.get("/api/v1/strategies/trend_following_daily")
        analytics = client.get(
            "/api/v1/analytics/strategies/trend_following_daily",
            params={
                "backtest_run_id": backtest_report.run_id,
                "paper_run_id": paper_state["paper_run_id"],
                "inspection_limit": 3,
            },
        )
        runs = client.get(
            "/api/v1/runs",
            params={
                "strategy_id": "trend_following_daily",
                "run_type": "paper_execution",
                "status": "succeeded",
                "session_start": "2024-01-05",
                "session_end": "2024-01-05",
                "limit": 5,
            },
        )
        run_detail = client.get(f"/api/v1/runs/{paper_state['paper_run_id']}")

    assert strategies.status_code == 200
    assert strategies.json()["count"] == 1
    assert strategies.json()["strategies"][0]["strategy_id"] == "trend_following_daily"

    assert strategy_detail.status_code == 200
    detail_body = strategy_detail.json()
    assert detail_body["strategy"]["strategy_id"] == "trend_following_daily"
    assert detail_body["operator_reads"]["analytics"] == "/api/v1/analytics/strategies/trend_following_daily"

    assert analytics.status_code == 200
    assert analytics.json() == expected_analytics

    assert runs.status_code == 200
    assert runs.json() == {
        "filters": {
            "strategy_id": "trend_following_daily",
            "run_type": "paper_execution",
            "status": "succeeded",
            "session_start": "2024-01-05",
            "session_end": "2024-01-05",
            "limit": 5,
        },
        "count": len(expected_runs),
        "items": expected_runs,
    }

    assert run_detail.status_code == 200
    assert run_detail.json() == expected_run_detail


def test_operational_reads_match_shared_operator_service(
    migrated_analytics_db: str,
    strategy_config_override: None,
) -> None:
    _seed_market_data(_trading_fixture())
    settings = load_settings()
    _seed_paper_operational_state()

    operator_reads = OperatorReadService(settings)
    filters = OperatorReadFilters(
        strategy_id="trend_following_daily",
        run_type="paper_execution",
        status="succeeded",
        session_start=date(2024, 1, 5),
        session_end=date(2024, 1, 5),
        limit=10,
    )
    expected_orders = operator_reads.list_paper_orders(filters)
    expected_fills = operator_reads.list_paper_fills(filters)
    expected_positions = operator_reads.list_positions(filters)
    expected_snapshots = operator_reads.list_account_snapshots(filters)

    risk_filters = OperatorReadFilters(
        strategy_id="trend_following_daily",
        run_type="risk_evaluation",
        status="succeeded",
        session_start=date(2024, 1, 5),
        session_end=date(2024, 1, 5),
        limit=10,
    )
    execution_filters = OperatorReadFilters(
        strategy_id="trend_following_daily",
        run_type="reconciliation",
        status="succeeded",
        session_start=date(2024, 1, 5),
        session_end=date(2024, 1, 5),
        limit=10,
    )
    expected_risk_events = operator_reads.list_risk_events(risk_filters)
    expected_execution_events = operator_reads.list_execution_events(execution_filters)

    with _build_client() as client:
        orders = client.get(
            "/api/v1/operations/orders",
            params={
                "strategy_id": "trend_following_daily",
                "run_type": "paper_execution",
                "status": "succeeded",
                "session_start": "2024-01-05",
                "session_end": "2024-01-05",
                "limit": 10,
            },
        )
        fills = client.get(
            "/api/v1/operations/fills",
            params={
                "strategy_id": "trend_following_daily",
                "run_type": "paper_execution",
                "status": "succeeded",
                "session_start": "2024-01-05",
                "session_end": "2024-01-05",
                "limit": 10,
            },
        )
        positions = client.get(
            "/api/v1/operations/positions",
            params={
                "strategy_id": "trend_following_daily",
                "run_type": "paper_execution",
                "status": "succeeded",
                "session_start": "2024-01-05",
                "session_end": "2024-01-05",
                "limit": 10,
            },
        )
        snapshots = client.get(
            "/api/v1/operations/account-snapshots",
            params={
                "strategy_id": "trend_following_daily",
                "run_type": "paper_execution",
                "status": "succeeded",
                "session_start": "2024-01-05",
                "session_end": "2024-01-05",
                "limit": 10,
            },
        )
        risk_events = client.get(
            "/api/v1/operations/risk-events",
            params={
                "strategy_id": "trend_following_daily",
                "run_type": "risk_evaluation",
                "status": "succeeded",
                "session_start": "2024-01-05",
                "session_end": "2024-01-05",
                "limit": 10,
            },
        )
        execution_events = client.get(
            "/api/v1/operations/execution-events",
            params={
                "strategy_id": "trend_following_daily",
                "run_type": "reconciliation",
                "status": "succeeded",
                "session_start": "2024-01-05",
                "session_end": "2024-01-05",
                "limit": 10,
            },
        )

    assert orders.status_code == 200
    assert orders.json()["items"] == expected_orders
    assert fills.status_code == 200
    assert fills.json()["items"] == expected_fills
    assert positions.status_code == 200
    assert positions.json()["items"] == expected_positions
    assert snapshots.status_code == 200
    assert snapshots.json()["items"] == expected_snapshots
    assert risk_events.status_code == 200
    assert risk_events.json()["items"] == expected_risk_events
    assert execution_events.status_code == 200
    assert execution_events.json()["items"] == expected_execution_events


def test_api_reads_return_not_found_for_missing_resources(
    migrated_analytics_db: str,
    strategy_config_override: None,
) -> None:
    missing_run_id = str(uuid.uuid4())

    with _build_client() as client:
        missing_strategy = client.get("/api/v1/strategies/missing_strategy")
        missing_analytics = client.get("/api/v1/analytics/strategies/missing_strategy")
        missing_run_list = client.get("/api/v1/runs", params={"strategy_id": "missing_strategy"})
        missing_run_detail = client.get(f"/api/v1/runs/{missing_run_id}")

    assert missing_strategy.status_code == 404
    assert missing_analytics.status_code == 404
    assert missing_run_list.status_code == 404
    assert missing_run_detail.status_code == 404


def test_system_kill_switch_route_reports_persisted_state(
    migrated_analytics_db: str,
    strategy_config_override: None,
) -> None:
    with _build_client() as client:
        armed = client.get("/api/v1/system/kill-switch")

    assert armed.status_code == 200
    armed_body = armed.json()
    assert armed_body["name"] == "global_kill_switch"
    assert armed_body["state"] == "armed"
    assert armed_body["is_tripped"] is False
    for key in (
        "last_changed_at",
        "last_change_actor",
        "last_change_reason",
        "last_change_run_id",
    ):
        assert key in armed_body

    settings = load_settings()
    OperatorControlService(settings=settings).trip_kill_switch(
        reason="pytest halt", actor="pytest", trigger_source="pytest"
    )

    with _build_client() as client:
        tripped = client.get("/api/v1/system/kill-switch")

    assert tripped.status_code == 200
    tripped_body = tripped.json()
    assert tripped_body["state"] == "tripped"
    assert tripped_body["is_tripped"] is True
    assert tripped_body["last_change_reason"] == "pytest halt"


def test_api_reads_return_empty_state_for_known_strategy(
    migrated_analytics_db: str,
    strategy_config_override: None,
) -> None:
    _seed_strategy_record()

    with _build_client() as client:
        analytics = client.get("/api/v1/analytics/strategies/trend_following_daily")
        runs = client.get("/api/v1/runs", params={"strategy_id": "trend_following_daily"})
        orders = client.get("/api/v1/operations/orders", params={"strategy_id": "trend_following_daily"})
        fills = client.get("/api/v1/operations/fills", params={"strategy_id": "trend_following_daily"})
        positions = client.get("/api/v1/operations/positions", params={"strategy_id": "trend_following_daily"})
        snapshots = client.get(
            "/api/v1/operations/account-snapshots",
            params={"strategy_id": "trend_following_daily"},
        )
        risk_events = client.get("/api/v1/operations/risk-events", params={"strategy_id": "trend_following_daily"})
        execution_events = client.get(
            "/api/v1/operations/execution-events",
            params={"strategy_id": "trend_following_daily"},
        )

    assert analytics.status_code == 200
    analytics_body = analytics.json()
    assert analytics_body["backtest"] is None
    assert analytics_body["paper"]["latest_account_snapshot"] is None
    assert analytics_body["paper"]["recent_execution_findings"] == []

    for response in (
        runs,
        orders,
        fills,
        positions,
        snapshots,
        risk_events,
        execution_events,
    ):
        assert response.status_code == 200
        assert response.json()["count"] == 0
        assert response.json()["items"] == []
