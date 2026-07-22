"""Job handler registry (JOB-03: registry extensibility).

Mirrors ``trading_platform.strategies.registry``'s explicit
register/resolve pattern: an in-memory dict keyed by ``job_type``, a
duplicate-registration ``ValueError``, and a typed unknown-key error.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from trading_platform.core.settings import Settings, load_settings
from trading_platform.jobs.contracts import JobHandler


@dataclass(frozen=True)
class UnknownJobTypeError(KeyError):
    """Raised by ``JobRegistry.resolve()`` when the job type is unregistered."""

    job_type: str

    def __str__(self) -> str:
        return f"Unknown job type '{self.job_type}'."


@dataclass(frozen=True)
class InvalidJobPayloadError(ValueError):
    """Raised by a public submission specification before any persistence begins."""

    job_type: str
    reason: str

    def __str__(self) -> str:
        return f"Invalid payload for job type '{self.job_type}': {self.reason}"


@runtime_checkable
class JobSubmissionSpec(Protocol):
    """Transport-neutral validation and normalization for a public Job type."""

    job_type: str

    def validate_payload(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        """Return normalized JSON-safe payload or raise ``InvalidJobPayloadError``."""


class JobRegistry:
    """In-memory registry with explicit registration and resolution."""

    def __init__(self) -> None:
        self._handlers: dict[str, JobHandler] = {}
        self._submission_specs: dict[str, JobSubmissionSpec] = {}

    def register(
        self,
        handler: JobHandler,
        *,
        submission_spec: JobSubmissionSpec | None = None,
    ) -> None:
        """Register one runner handler and, optionally, its public input contract.

        The handler and specification registrations are validated before either
        registry mapping changes, so a mismatched specification cannot leave a
        partially registered public type behind.
        """

        job_type = handler.job_type
        if job_type in self._handlers:
            raise ValueError(f"Job type '{job_type}' is already registered.")
        if submission_spec is not None and submission_spec.job_type != job_type:
            raise ValueError(
                "Submission specification job type must match its handler: "
                f"'{submission_spec.job_type}' != '{job_type}'."
            )
        self._handlers[job_type] = handler
        if submission_spec is not None:
            self._submission_specs[job_type] = submission_spec

    def resolve(self, job_type: str) -> JobHandler:
        try:
            return self._handlers[job_type]
        except KeyError as exc:
            raise UnknownJobTypeError(job_type) from exc

    def resolve_submission_spec(self, job_type: str) -> JobSubmissionSpec:
        """Return a public submission contract for a registered handler.

        Runner-only registrations intentionally have no contract and are not
        publicly submittable; callers translate this typed registry miss to
        their transport-specific submission error.
        """

        try:
            return self._submission_specs[job_type]
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
