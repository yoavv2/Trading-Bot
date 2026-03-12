"""Placeholder execution service contracts."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ExecutionService(ABC):
    @abstractmethod
    def describe(self) -> dict[str, Any]:
        """Describe the execution capability exposed to the platform."""

    @abstractmethod
    def submit(self, payload: object) -> object:
        """Submit an execution payload once broker support exists."""


class PlaceholderExecutionService(ExecutionService):
    def describe(self) -> dict[str, Any]:
        return {
            "service": "execution",
            "status": "deferred",
            "detail": "Deferred to Phase 5 paper execution.",
        }

    def submit(self, payload: object) -> object:
        raise NotImplementedError("Execution is deferred to Phase 5.")
