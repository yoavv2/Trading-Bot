"""Placeholder analytics service contracts."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class AnalyticsService(ABC):
    @abstractmethod
    def describe(self) -> dict[str, Any]:
        """Describe the analytics capability exposed to the platform."""

    @abstractmethod
    def summarize(self, payload: object) -> object:
        """Summarize execution data once analytics are implemented."""


class PlaceholderAnalyticsService(AnalyticsService):
    def describe(self) -> dict[str, Any]:
        return {
            "service": "analytics",
            "status": "deferred",
            "detail": "Deferred to Phase 6 analytics and reporting.",
        }

    def summarize(self, payload: object) -> object:
        raise NotImplementedError("Analytics are deferred to Phase 6.")
