"""Research strategy registry keyed by strategy version id.

The static trading registry (``strategies/registry.py``) is untouched. Research
resolves approved ``strategy_versions`` rows from the research database into
``DeclarativeDailyStrategy`` instances; the universe is supplied by the study, not
the specification.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_platform.core.settings import Settings
from trading_platform.db.models.research import StrategyVersion
from trading_platform.strategies.declarative import DeclarativeDailyStrategy
from trading_platform.strategies.spec.validate import CompiledSpec, validate_spec


@dataclass(frozen=True)
class UnknownStrategyVersionError(KeyError):
    version_id: str

    def __str__(self) -> str:
        return f"Unknown strategy version '{self.version_id}'."


def strategy_key(version_id: uuid.UUID) -> str:
    return f"research:{version_id}"


def strategy_from_compiled(
    settings: Settings,
    compiled: CompiledSpec,
    *,
    universe: tuple[str, ...],
    version_id: uuid.UUID | None = None,
    version_label: str = "v1",
    bar_source: tuple[str, bool] | None = None,
) -> DeclarativeDailyStrategy:
    key = strategy_key(version_id) if version_id is not None else f"research:{compiled.spec_sha256[:12]}"
    return DeclarativeDailyStrategy(
        settings,
        compiled,
        strategy_id=key,
        universe=universe,
        version_label=version_label,
        config_reference=(f"research:strategy_version:{version_id}" if version_id else "research:spec"),
        bar_source=bar_source,
    )


def strategy_from_version(
    settings: Settings,
    version: StrategyVersion,
    *,
    universe: tuple[str, ...],
    bar_source: tuple[str, bool] | None = None,
) -> DeclarativeDailyStrategy:
    compiled = validate_spec(version.spec_json)
    if compiled.spec_sha256 != version.spec_sha256:
        raise ValueError(
            f"strategy version {version.id} spec_sha256 does not match its stored specification"
        )
    return strategy_from_compiled(
        settings,
        compiled,
        universe=universe,
        version_id=version.id,
        version_label=f"v{version.version_no}",
        bar_source=bar_source,
    )


class ResearchStrategyRegistry:
    def __init__(self) -> None:
        self._strategies: dict[str, DeclarativeDailyStrategy] = {}

    def register(self, version_id: uuid.UUID, strategy: DeclarativeDailyStrategy) -> None:
        key = strategy_key(version_id)
        if key in self._strategies:
            raise ValueError(f"Strategy version '{version_id}' is already registered.")
        self._strategies[key] = strategy

    def resolve(self, version_id: uuid.UUID | str) -> DeclarativeDailyStrategy:
        key = version_id if isinstance(version_id, str) and version_id.startswith("research:") else strategy_key(uuid.UUID(str(version_id)))
        try:
            return self._strategies[key]
        except KeyError as exc:
            raise UnknownStrategyVersionError(str(version_id)) from exc

    def list_keys(self) -> list[str]:
        return sorted(self._strategies)


def build_research_strategy_registry(
    settings: Settings, session: Session, *, universe: tuple[str, ...]
) -> ResearchStrategyRegistry:
    registry = ResearchStrategyRegistry()
    versions = session.execute(
        select(StrategyVersion).order_by(StrategyVersion.strategy_id, StrategyVersion.version_no)
    ).scalars()
    for version in versions:
        registry.register(version.id, strategy_from_version(settings, version, universe=universe))
    return registry
