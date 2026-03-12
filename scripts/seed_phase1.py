"""Seed the minimal Phase 1 strategy catalog records."""

from __future__ import annotations

import argparse
from pathlib import Path

from sqlalchemy import select

from trading_platform.core.settings import get_strategy_config, load_settings
from trading_platform.db.models import Strategy, StrategyStatus
from trading_platform.db.session import session_scope


def _config_reference(strategy_id: str) -> str:
    settings = load_settings()
    strategy_path = settings.paths.strategy_config_dir / f"{strategy_id}.yaml"
    project_root = Path(__file__).resolve().parents[1]

    try:
        return str(strategy_path.relative_to(project_root))
    except ValueError:
        return str(strategy_path)


def seed_phase_one(strategy_id: str = "trend_following_daily") -> tuple[Strategy, bool]:
    """Insert or refresh the minimal strategy metadata required by Phase 1."""

    settings = load_settings()
    strategy_config = get_strategy_config(settings, strategy_id)
    payload = {
        "strategy_id": strategy_config.strategy_id,
        "display_name": strategy_config.display_name,
        "version": "v1",
        "status": StrategyStatus.ACTIVE.value,
        "description": "Phase 1 dry-run bootstrap strategy registration.",
        "config_reference": _config_reference(strategy_id),
        "universe_symbols": list(strategy_config.universe),
        "settings_snapshot": strategy_config.model_dump(mode="json"),
    }

    with session_scope(settings) as session:
        existing = session.execute(
            select(Strategy).where(Strategy.strategy_id == strategy_config.strategy_id)
        ).scalar_one_or_none()

        if existing is None:
            strategy = Strategy(**payload)
            session.add(strategy)
            created = True
        else:
            strategy = existing
            for field_name, value in payload.items():
                setattr(strategy, field_name, value)
            created = False

        session.flush()
        session.refresh(strategy)
        return strategy, created


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scripts/seed_phase1.py")
    parser.add_argument("--strategy", default="trend_following_daily")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    strategy, created = seed_phase_one(args.strategy)
    action = "created" if created else "updated"
    print(
        f"{action}:{strategy.strategy_id}:"
        f"{strategy.display_name}:symbols={len(strategy.universe_symbols)}"
    )


if __name__ == "__main__":
    main()
