"""Job handler registry (JOB-03: registry extensibility).

Mirrors ``trading_platform.strategies.registry``'s explicit
register/resolve pattern: an in-memory dict keyed by ``job_type``, a
duplicate-registration ``ValueError``, and a typed unknown-key error.
"""

from __future__ import annotations

from dataclasses import dataclass

from trading_platform.core.settings import Settings, load_settings
from trading_platform.jobs.contracts import JobHandler


@dataclass(frozen=True)
class UnknownJobTypeError(KeyError):
    """Raised by ``JobRegistry.resolve()`` when the job type is unregistered."""

    job_type: str

    def __str__(self) -> str:
        return f"Unknown job type '{self.job_type}'."


class JobRegistry:
    """In-memory registry with explicit registration and resolution."""

    def __init__(self) -> None:
        self._handlers: dict[str, JobHandler] = {}

    def register(self, handler: JobHandler) -> None:
        job_type = handler.job_type
        if job_type in self._handlers:
            raise ValueError(f"Job type '{job_type}' is already registered.")
        self._handlers[job_type] = handler

    def resolve(self, job_type: str) -> JobHandler:
        try:
            return self._handlers[job_type]
        except KeyError as exc:
            raise UnknownJobTypeError(job_type) from exc

    def list_job_types(self) -> list[str]:
        return sorted(self._handlers)

    def __contains__(self, job_type: str) -> bool:
        return job_type in self._handlers


def build_default_registry(settings: Settings | None = None) -> JobRegistry:
    """Return the default ``JobRegistry`` for the running process.

    Phase 17 ships zero domain handlers -- this function returns an empty
    registry. Phase 19 registers the concrete operation handlers here
    (backtest, risk evaluation, paper session, reconciliation,
    market-data sync, broker sync, ...).

    JOB-03's extensibility contract: adding a new Job type means (1)
    writing a handler module implementing ``JobHandler`` and (2) appending
    one ``registry.register(SomeHandler(...))`` call to this function.
    Nothing under ``jobs/queue.py``, ``jobs/lifecycle.py``,
    ``jobs/runner.py``, ``jobs/dependencies.py``, or
    ``jobs/cancellation.py`` changes to add a Job type.
    """
    _ = settings or load_settings()
    return JobRegistry()
