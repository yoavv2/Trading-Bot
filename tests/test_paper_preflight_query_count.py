"""PERF-01: paper preflight (auto-resolve path) query-count invariant.

Pins the literal ROADMAP success criterion: `_build_paper_session_plan`'s
auto-resolve path (`requested_risk_run_id=None`) issues at most 2 SQL
queries total, and that count does NOT grow with the number of approved
candidates. This is the hard, testable proof that the per-candidate N+1
intent-resolution query pattern is gone -- not a timing or "roughly
constant" heuristic.
"""

from __future__ import annotations

import sys
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import trading_platform.services.paper_execution as paper_execution_module
from tests.support.query_counter import count_queries
from tests.test_paper_execution import migrated_paper_db  # noqa: F401 (reused DB harness fixture)
from trading_platform.core.settings import load_settings
from trading_platform.db.models import RiskEvent, StrategyRun, StrategyRunStatus, StrategyRunType
from trading_platform.db.models.symbol import Symbol
from trading_platform.db.session import session_scope
from trading_platform.services.bootstrap import ensure_strategy_record
from trading_platform.strategies.registry import build_default_registry

_STRATEGY_ID = "trend_following_daily"


def _seed_approved_candidates(*, session_date: date, count: int) -> uuid.UUID:
    """Seed a succeeded risk_evaluation StrategyRun with `count` approved
    RiskEvents on distinct symbols for `session_date`. Mirrors
    tests/test_paper_execution.py::_seed_approved_risk_batch's seeding style,
    parametrized by candidate count.
    """
    settings = load_settings()
    registry = build_default_registry(settings)
    strategy = registry.resolve(_STRATEGY_ID)

    with session_scope(settings) as session:
        strategy_record = ensure_strategy_record(session, strategy.metadata)

        symbols = [
            Symbol(ticker=f"SYM{session_date.isoformat().replace('-', '')}{i:04d}", active=True)
            for i in range(count)
        ]
        session.add_all(symbols)
        session.flush()

        risk_run = StrategyRun(
            strategy_id=strategy_record.id,
            run_type=StrategyRunType.RISK_EVALUATION,
            status=StrategyRunStatus.SUCCEEDED,
            trigger_source="test_suite",
            parameters_snapshot={"as_of_session": session_date.isoformat()},
            result_summary={"stage": "completed", "as_of_session": session_date.isoformat()},
        )
        session.add(risk_run)
        session.flush()

        risk_events = [
            RiskEvent(
                strategy_run_id=risk_run.id,
                symbol_id=symbol.id,
                session_date=session_date,
                signal_direction="long",
                signal_reason="trend_entry",
                outcome="approved",
                decision_code="approved",
                decision_reason="Approved for paper execution.",
                reference_price=Decimal("100.000000"),
                proposed_quantity=Decimal("1.000000"),
                proposed_notional=Decimal("100.000000"),
                risk_metadata={},
            )
            for symbol in symbols
        ]
        session.add_all(risk_events)
        session.flush()
        return risk_run.id


def _measure_preflight_query_count(*, as_of_session: date) -> int:
    settings = load_settings()

    with session_scope(settings) as session:
        with count_queries(session) as counter:
            plan = paper_execution_module._build_paper_session_plan(
                session,
                strategy_id=_STRATEGY_ID,
                as_of_session=as_of_session,
                requested_risk_run_id=None,
                failure_threshold=settings.execution.safety.repeated_failure_threshold,
                client_order_id_prefix=settings.execution.client_order_id_prefix,
            )
        assert plan.source_risk_run_id is not None

    return counter.count, counter.statements


def test_preflight_query_count_is_at_most_two(migrated_paper_db: str) -> None:
    session_date = date(2024, 1, 5)
    _seed_approved_candidates(session_date=session_date, count=1)

    count, statements = _measure_preflight_query_count(as_of_session=session_date)

    assert count <= 2, (
        f"Auto-resolve preflight issued {count} SQL queries (expected <= 2). "
        f"Statements: {statements}"
    )


def test_preflight_query_count_is_invariant_to_candidate_count(migrated_paper_db: str) -> None:
    small_session_date = date(2024, 1, 5)
    large_session_date = date(2024, 1, 6)

    _seed_approved_candidates(session_date=small_session_date, count=1)
    _seed_approved_candidates(session_date=large_session_date, count=25)

    small_count, small_statements = _measure_preflight_query_count(as_of_session=small_session_date)
    large_count, large_statements = _measure_preflight_query_count(as_of_session=large_session_date)

    assert small_count == large_count, (
        f"Preflight query count grew with candidate count: K=1 -> {small_count} "
        f"({small_statements}), K=25 -> {large_count} ({large_statements})."
    )
