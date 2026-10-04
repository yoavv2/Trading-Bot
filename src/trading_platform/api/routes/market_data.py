"""Read-only market-data / calendar state route (interim runbook R4, COR-04).

``GET /api/v1/market-data/calendar-state[?strategy_id=]`` returns the three
separate calendar facts (trading day, evaluation session, execution window; each
with an explicit ``unknown(calendar_data_unavailable)``), the calendar coverage
(last persisted session, runway, ``runway_low``) and per-symbol readiness for
every registered strategy: bars, history (warm-up) and METADATA (20.1-04).

The evaluation session ``status`` is a bar/history rule only; a symbol with
``metadata: not_ready`` is reported individually and never presented as ready for
trading, without flipping the session status. The read performs no write; ``as_of``
is server time; the statement count is independent of the number of symbols (the
facts load one window statement, the strategies' universes are read with one
statement each for bars, history and metadata).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.concurrency import run_in_threadpool

from trading_platform.api.dependencies import (
    get_settings,
    get_strategy_registry,
    resolve_strategy_metadata,
)
from trading_platform.core.settings import Settings
from trading_platform.db.session import session_scope
from trading_platform.services.calendar_facts import (
    EvaluationSession,
    UnknownReason,
    calendar_facts,
    readiness_from_rows,
)
from trading_platform.services.market_data_access import (
    bar_counts_through_session,
    missing_bars_for_session,
)
from trading_platform.services.symbol_readiness import SymbolReadiness, symbols_readiness
from trading_platform.strategies.base import BaseStrategy
from trading_platform.strategies.registry import StrategyRegistry

router = APIRouter(prefix="/api/v1/market-data", tags=["market-data"])


def _now() -> datetime:
    """Server time seam (tests monkeypatch it)."""

    return datetime.now(UTC)


def _evaluation_payload(
    evaluation: EvaluationSession,
    metadata: dict[str, SymbolReadiness],
) -> dict[str, Any]:
    payload = evaluation.to_dict()
    rows: list[dict[str, Any]] = []
    for item in evaluation.symbol_readiness:
        symbol_metadata = metadata[item.symbol]
        rows.append(
            {
                "symbol": item.symbol,
                "bars": "present" if item.has_bar else "missing",
                "history": "sufficient" if item.history_sufficient else "insufficient",
                "bars_through_session": item.bars_through_session,
                "metadata": "ready" if symbol_metadata.ready else "not_ready",
                "metadata_reason": (
                    symbol_metadata.reason.value if symbol_metadata.reason is not None else None
                ),
            }
        )
    payload["symbols"] = rows
    # ``symbols`` of the closed fact lists the symbols that fail the bar/history rule.
    payload["not_ready_symbols"] = list(evaluation.symbols)
    return payload


def _build_calendar_state(
    settings: Settings, strategies: list[BaseStrategy], now: datetime
) -> dict[str, Any]:
    with session_scope(settings) as session:
        facts = calendar_facts(session, now=now, settings=settings)
        candidate = facts.evaluation_candidate

        evaluations: dict[str, dict[str, Any]] = {}
        windows: dict[str, dict[str, Any]] = {}
        if isinstance(candidate, date):
            universe = sorted({symbol for s in strategies for symbol in s.metadata.universe})
            missing = set(missing_bars_for_session(session, candidate, symbols=universe))
            counts = bar_counts_through_session(session, universe, candidate)
            metadata = symbols_readiness(session, universe)
            for strategy in strategies:
                tickers = tuple(strategy.metadata.universe)
                evaluation = readiness_from_rows(
                    session_date=candidate,
                    row_present=candidate in facts.window.rows,
                    universe=tickers,
                    missing_symbols=missing.intersection(tickers),
                    bar_counts=counts,
                    warmup_periods=int(strategy.warmup_periods),
                )
                evaluations[strategy.strategy_id] = _evaluation_payload(evaluation, metadata)
                windows[strategy.strategy_id] = facts.execution_window.to_dict()
        else:
            for strategy in strategies:
                evaluations[strategy.strategy_id] = {
                    "status": "unknown",
                    "session_date": None,
                    "reason": UnknownReason.CALENDAR_DATA_UNAVAILABLE.value,
                    "symbols": [],
                    "not_ready_symbols": [],
                }
                windows[strategy.strategy_id] = facts.execution_window.to_dict()

        return {
            "as_of": now.astimezone(UTC).isoformat(),
            "policy": {
                "name": settings.execution.execution_policy,
                "cutoff_minutes": settings.execution.execution_window_cutoff_minutes,
            },
            "trading_day": facts.trading_day.to_dict(),
            "calendar_coverage": facts.coverage.to_dict(),
            "evaluation_sessions": evaluations,
            "execution_windows": windows,
        }


@router.get("/calendar-state")
async def calendar_state(
    strategy_id: str | None = Query(default=None),
    settings: Settings = Depends(get_settings),
    registry: StrategyRegistry = Depends(get_strategy_registry),
) -> dict[str, Any]:
    if strategy_id is not None:
        resolve_strategy_metadata(strategy_id=strategy_id, registry=registry)  # 404 if unknown
        strategies = [registry.resolve(strategy_id)]
    else:
        strategies = [registry.resolve(m.strategy_id) for m in registry.list_metadata()]
    return await run_in_threadpool(_build_calendar_state, settings, strategies, _now())
