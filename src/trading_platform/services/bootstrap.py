"""Strategy catalog get-or-create for mutating service paths."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from trading_platform.db.models import Strategy, StrategyStatus
from trading_platform.strategies.base import StrategyMetadata


def _strategy_payload(metadata: StrategyMetadata) -> dict[str, Any]:
    return {
        "strategy_id": metadata.strategy_id,
        "display_name": metadata.display_name,
        "version": metadata.version,
        "description": metadata.description,
        "config_reference": metadata.config_reference,
        "universe_symbols": list(metadata.universe),
        "settings_snapshot": {
            "indicators": metadata.indicators,
            "risk": metadata.risk,
            "exits": metadata.exits,
        },
    }


def ensure_strategy_record(session, metadata: StrategyMetadata) -> Strategy:
    existing = session.execute(
        select(Strategy).where(Strategy.strategy_id == metadata.strategy_id)
    ).scalar_one_or_none()
    payload = _strategy_payload(metadata)

    if existing is None:
        strategy = Strategy(status=StrategyStatus.ACTIVE, **payload)
        session.add(strategy)
    else:
        strategy = existing
        for field_name, value in payload.items():
            setattr(strategy, field_name, value)

    session.flush()
    session.refresh(strategy)
    return strategy
