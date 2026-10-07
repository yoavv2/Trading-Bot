"""Seed the four example strategy versions (translations of the original Python strategies).

Seeding is gated on the parity tests (``tests/test_strategy_spec_parity.py``): the
examples are only seeded once those pass. Idempotent by ``(strategy_id, spec_sha256)``;
a changed example YAML seeds the next version number of the same family and the
earlier version stays (append-only table).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import yaml  # type: ignore[import-untyped]
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trading_platform.core.settings import PROJECT_ROOT
from trading_platform.db.models.research import StrategyVersion
from trading_platform.strategies.spec.explain import explain
from trading_platform.strategies.spec.validate import CompiledSpec, validate_spec

EXAMPLES_DIR = PROJECT_ROOT / "config" / "research" / "examples"
EXAMPLE_NAMESPACE = uuid.UUID("5f0d1f1e-9a3c-4f2e-9d6d-2a7f3c8e4b10")
EXAMPLE_KEYS: tuple[str, ...] = (
    "trend_following_daily",
    "rsi_mean_reversion_daily",
    "donchian_breakout_daily",
    "time_series_momentum_daily",
)
SOURCE_EXAMPLE = "example"


def example_strategy_id(key: str) -> uuid.UUID:
    return uuid.uuid5(EXAMPLE_NAMESPACE, key)


@dataclass(frozen=True)
class ExampleSpec:
    key: str
    yaml_text: str
    compiled: CompiledSpec


def load_example_specs(directory: Path | None = None) -> list[ExampleSpec]:
    root = directory or EXAMPLES_DIR
    out: list[ExampleSpec] = []
    for key in EXAMPLE_KEYS:
        path = root / f"{key}.yaml"
        text = path.read_text()
        out.append(ExampleSpec(key=key, yaml_text=text, compiled=validate_spec(yaml.safe_load(text))))
    return out


def seed_example_versions(
    session: Session, *, now: datetime | None = None, directory: Path | None = None
) -> list[StrategyVersion]:
    """Insert missing example versions; return every example version present afterwards."""

    approved_at = now or datetime.now(UTC)
    result: list[StrategyVersion] = []
    for example in load_example_specs(directory):
        strategy_id = example_strategy_id(example.key)
        existing = session.execute(
            select(StrategyVersion).where(
                StrategyVersion.strategy_id == strategy_id,
                StrategyVersion.spec_sha256 == example.compiled.spec_sha256,
            )
        ).scalar_one_or_none()
        if existing is not None:
            result.append(existing)
            continue
        latest = session.execute(
            select(func.max(StrategyVersion.version_no)).where(StrategyVersion.strategy_id == strategy_id)
        ).scalar_one()
        version = StrategyVersion(
            id=uuid.uuid4(),
            strategy_id=strategy_id,
            version_no=(latest or 0) + 1,
            name=example.compiled.spec.name,
            yaml_text=example.yaml_text,
            spec_json=example.compiled.spec.model_dump(mode="json"),
            spec_sha256=example.compiled.spec_sha256,
            history_required=example.compiled.history_required,
            history_minimum=example.compiled.history_minimum,
            scale_class=example.compiled.scale_class,
            explanation=explain(example.compiled),
            source=SOURCE_EXAMPLE,
            approved_at=approved_at,
            created_at=approved_at,
        )
        session.add(version)
        session.flush()
        result.append(version)
    return result
