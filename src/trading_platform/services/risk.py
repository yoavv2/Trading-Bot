"""Placeholder risk service contracts."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class RiskService(ABC):
    @abstractmethod
    def describe(self) -> dict[str, Any]:
        """Describe the risk capability exposed to the platform."""

    @abstractmethod
    def validate(self, payload: object) -> object:
        """Validate a strategy payload once real risk checks exist."""


class PlaceholderRiskService(RiskService):
    def describe(self) -> dict[str, Any]:
        return {
            "service": "risk",
            "status": "deferred",
            "detail": "Deferred to Phase 4 portfolio and risk controls.",
        }

    def validate(self, payload: object) -> object:
        raise NotImplementedError("Risk validation is deferred to Phase 4.")
