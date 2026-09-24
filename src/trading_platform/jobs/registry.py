"""Job handler registry (JOB-03: registry extensibility).

Mirrors ``trading_platform.strategies.registry``'s explicit
register/resolve pattern: an in-memory dict keyed by ``job_type``, a
duplicate-registration ``ValueError``, and a typed unknown-key error.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from trading_platform.core.settings import Settings, load_settings
from trading_platform.jobs.contracts import JobHandler


class JobCancellationMode(StrEnum):
    """Closed vocabulary describing how a public Job type acknowledges cancellation.

    Cooperative cancellation is acknowledged at handler step boundaries
    (Phase 17 D-08) -- every Phase 19 Job type uses ``STEP_BOUNDARY``. Phase
    20 is expected to add further values (e.g. "before broker submission")
    as new operation types with different cancellation semantics register.
    """

    STEP_BOUNDARY = "step_boundary"


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
    description: str
    cancellation_mode: JobCancellationMode

    def validate_payload(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        """Return normalized JSON-safe payload or raise ``InvalidJobPayloadError``."""

    def submission_defaults(self) -> Mapping[str, Any] | None:
        """Return console pre-fill defaults, computed at read time (D-10).

        Returns ``None`` when defaults are unavailable (e.g. no prior
        session data to derive them from). Must not mutate any state --
        this is called on every catalog read (``GET /api/v1/job-types``),
        including reads that never lead to a submission."""


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
        if submission_spec is not None:
            description = getattr(submission_spec, "description", None)
            if not isinstance(description, str) or not (1 <= len(description.strip()) <= 200):
                raise ValueError(
                    f"Job type '{job_type}': submission spec 'description' must be a "
                    "nonblank string of at most 200 characters."
                )
            cancellation_mode = getattr(submission_spec, "cancellation_mode", None)
            if not isinstance(cancellation_mode, JobCancellationMode):
                raise ValueError(
                    f"Job type '{job_type}': submission spec 'cancellation_mode' must be "
                    "a JobCancellationMode member."
                )
            if not callable(getattr(submission_spec, "submission_defaults", None)):
                raise ValueError(
                    f"Job type '{job_type}': submission spec must define a callable "
                    "'submission_defaults' method."
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

    Phase 19 registers the first concrete operation handler here
    (``backtest``). Phase 20 appends the remaining operations (risk
    evaluation, paper session, reconciliation, market-data sync, broker
    sync, ...) to this same function.

    JOB-03's extensibility contract: adding a new Job type means (1)
    writing a handler module implementing ``JobHandler`` and (2) appending
    one ``registry.register(SomeHandler(...))`` call to this function.
    Nothing under ``jobs/queue.py``, ``jobs/lifecycle.py``,
    ``jobs/runner.py``, ``jobs/dependencies.py``, or
    ``jobs/cancellation.py`` changes to add a Job type.

    Handler/submission-spec modules live under ``jobs/handlers/`` and are
    imported here, inside the function body rather than at module level --
    those modules import ``InvalidJobPayloadError``/``JobCancellationMode``
    from this module, so a top-of-file import here would be circular.
    """
    resolved = settings or load_settings()
    registry = JobRegistry()

    from trading_platform.jobs.handlers.backtest import BacktestJobHandler
    from trading_platform.jobs.handlers.backtest_submission import BacktestSubmissionSpec

    registry.register(BacktestJobHandler(settings=resolved), submission_spec=BacktestSubmissionSpec(resolved))

    return registry
