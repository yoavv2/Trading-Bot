"""Job handler registry (JOB-03: registry extensibility).

Mirrors ``trading_platform.strategies.registry``'s explicit
register/resolve pattern: an in-memory dict keyed by ``job_type``, a
duplicate-registration ``ValueError``, and a typed unknown-key error.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from trading_platform.core.settings import Settings, load_settings
from trading_platform.jobs.contracts import JobHandler


class JobCancellationMode(StrEnum):
    """Closed vocabulary describing how a public Job type acknowledges cancellation.

    - ``STEP_BOUNDARY``: cooperative acknowledgement at handler step
      boundaries (Phase 17 D-08).
    - ``QUEUED_ONLY``: cancellable only while QUEUED; a RUNNING Job of such
      a type is never cancellable and ``JobOrchestrationService`` rejects
      the request (D-01/D-02).
    """

    STEP_BOUNDARY = "step_boundary"
    QUEUED_ONLY = "queued_only"


class ConsoleSubmission(StrEnum):
    """Closed catalog vocabulary (20.1-14, D-31, 05 sec.4): whether the EXISTING console may
    submit a Job type. ``api_only`` types are operated through the API in this version."""

    INTERACTIVE = "interactive"
    API_ONLY = "api_only"


def console_submission_for(spec: JobSubmissionSpec) -> ConsoleSubmission:
    """The console submission mode a spec declares (optional attribute ``console_submission``,
    default ``interactive``; deliberately not a Protocol member)."""

    declared = getattr(spec, "console_submission", None)
    if declared is None:
        return ConsoleSubmission.INTERACTIVE
    return ConsoleSubmission(getattr(declared, "value", declared))


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


class JobSubmissionConflictError(ValueError):
    """Raised by ``validate_payload`` for a state-dependent submission conflict.

    Not a payload-shape error (that stays ``InvalidJobPayloadError``, HTTP
    422): the payload is well formed but the platform state refuses it (for
    example a strategy that is not the active paper strategy). Mapped to HTTP
    409 ``{code, job_type, **detail}``. ``code`` is a member of the raising
    spec's own closed conflict enum.

    Deliberately NOT a frozen dataclass: the admission re-check raises inside
    ``session_scope`` (a contextmanager), and ``contextlib`` re-assigns
    ``__traceback__`` on the exception, which a frozen dataclass forbids.
    """

    def __init__(self, job_type: str, code: str, detail: Mapping[str, str] | None = None) -> None:
        self.job_type = job_type
        self.code = code
        self.detail: Mapping[str, str] = dict(detail or {})
        super().__init__(job_type, code, self.detail)

    def __str__(self) -> str:
        return f"Job type '{self.job_type}' submission refused: {self.code}"


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


def retry_prerequisite_for(spec: JobSubmissionSpec) -> str | None:
    """Phase 20 D-19 (SUPERSEDED by D-15 / 20.1-10): the Job type that had to SUCCEED
    before a FAILED outcome_uncertain Job could be retried.

    No registered type declares it any more (every type returns ``None``): the
    reconcile-first rule was replaced by the recovery predicate, see
    ``recovery_gated_for``. The helper and the registration validation stay so a future
    type could still declare a plain prerequisite.

    Optional spec attribute, deliberately not a Protocol member.
    """

    return getattr(spec, "retry_prerequisite_job_type", None)


#: The three typed 409 codes of the D-15 recovery gate. A ``JobSubmissionConflictError``
#: carrying one of these, raised by a ``recovery_gated`` spec, is what the orchestration
#: layer maps to a retry block. Equal to ``services.recovery.RECOVERY_GATE_CODES``
#: (a test pins the equality; orchestration may not import services).
RECOVERY_CONFLICT_CODES: frozenset[str] = frozenset(
    {"outcome_unresolved", "reconciliation_required", "reconciliation_not_clean"}
)


def recovery_gated_for(spec: JobSubmissionSpec) -> bool:
    """D-15: True when ``validate_payload`` of this type is gated by the uncertain-outcome
    recovery predicate (only ``paper-session``). Drives the retry block of the Job detail
    and of ``retry()``: they re-run ``validate_payload`` and map a recovery conflict code.

    Optional boolean spec attribute (``recovery_gated``), default False; deliberately not
    a Protocol member (same pattern as ``retry_prerequisite_for``).
    """

    return getattr(spec, "recovery_gated", False) is True


def admission_check_for(spec: JobSubmissionSpec) -> Callable[..., None] | None:
    """SER: the optional admission hook of a broker-touching Job type.

    Called inside the Job-insert transaction, as ``hook(payload, session=...)``,
    BEFORE the Job row is inserted (``orchestration/job_mutations`` submit and
    retry). The hook takes the active_paper_strategy singleton row FOR SHARE and
    re-runs the type's ownership check, raising ``JobSubmissionConflictError``
    on refusal. Optional spec attribute, deliberately not a Protocol member.
    """

    return getattr(spec, "check_admission", None)


def admission_lock_for(spec: JobSubmissionSpec) -> Callable[..., None] | None:
    """SER lock order: the lock step of the admission hook, callable on its own.

    ``hook(session=...)`` takes the active_paper_strategy singleton row FOR SHARE
    and nothing else. ``retry`` calls it BEFORE it row-locks the original Job, so
    the global lock order stays "singleton first, then other rows" (the handover
    and operation creation rely on it). Optional spec attribute, not a Protocol
    member.
    """

    return getattr(spec, "lock_admission", None)


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
            if hasattr(submission_spec, "retry_prerequisite_job_type"):
                retry_prerequisite = submission_spec.retry_prerequisite_job_type
                if retry_prerequisite is not None and not (
                    isinstance(retry_prerequisite, str) and retry_prerequisite.strip()
                ):
                    raise ValueError(
                        f"Job type '{job_type}': submission spec "
                        "'retry_prerequisite_job_type' must be None or a nonblank string."
                    )
            if hasattr(submission_spec, "recovery_gated") and not isinstance(
                submission_spec.recovery_gated, bool
            ):
                raise ValueError(
                    f"Job type '{job_type}': submission spec 'recovery_gated' must be a bool."
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

    Phase 19 registered the first concrete operation handler here
    (``backtest``). Phase 20 (Plan 16) appends the remaining seven
    registrations -- broker-order-sync, ingest-bars, paper-session,
    reconciliation, risk-evaluation, sync-market-sessions, and
    sync-symbol-metadata -- for eight total registered Job types. Phase 20.1
    (Plan 09) adds ``record-external-activity`` (nine).

    JOB-03's extensibility contract: adding a new Job type means (1)
    writing a handler module implementing ``JobHandler`` and (2) appending
    one call to this function's ``register`` method on the registry.
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
    from trading_platform.jobs.handlers.broker_order_sync import BrokerOrderSyncJobHandler
    from trading_platform.jobs.handlers.broker_order_sync_submission import (
        BrokerOrderSyncSubmissionSpec,
    )
    from trading_platform.jobs.handlers.ingest_bars import IngestBarsJobHandler
    from trading_platform.jobs.handlers.ingest_bars_submission import IngestBarsSubmissionSpec
    from trading_platform.jobs.handlers.paper_session import PaperSessionJobHandler
    from trading_platform.jobs.handlers.paper_session_submission import (
        PaperSessionSubmissionSpec,
    )
    from trading_platform.jobs.handlers.reconciliation import ReconciliationJobHandler
    from trading_platform.jobs.handlers.reconciliation_submission import (
        ReconciliationSubmissionSpec,
    )
    from trading_platform.jobs.handlers.record_external_activity import (
        RecordExternalActivityJobHandler,
    )
    from trading_platform.jobs.handlers.record_external_activity_submission import (
        RecordExternalActivitySubmissionSpec,
    )
    from trading_platform.jobs.handlers.risk_evaluation import RiskEvaluationJobHandler
    from trading_platform.jobs.handlers.risk_evaluation_submission import (
        RiskEvaluationSubmissionSpec,
    )
    from trading_platform.jobs.handlers.sync_market_sessions import (
        SyncMarketSessionsJobHandler,
    )
    from trading_platform.jobs.handlers.sync_market_sessions_submission import (
        SyncMarketSessionsSubmissionSpec,
    )
    from trading_platform.jobs.handlers.sync_symbol_metadata import (
        SyncSymbolMetadataJobHandler,
    )
    from trading_platform.jobs.handlers.sync_symbol_metadata_submission import (
        SyncSymbolMetadataSubmissionSpec,
    )

    registry.register(BacktestJobHandler(settings=resolved), submission_spec=BacktestSubmissionSpec(resolved))
    registry.register(
        BrokerOrderSyncJobHandler(settings=resolved),
        submission_spec=BrokerOrderSyncSubmissionSpec(resolved),
    )
    registry.register(
        IngestBarsJobHandler(settings=resolved),
        submission_spec=IngestBarsSubmissionSpec(resolved),
    )
    registry.register(
        PaperSessionJobHandler(settings=resolved),
        submission_spec=PaperSessionSubmissionSpec(resolved),
    )
    registry.register(
        ReconciliationJobHandler(settings=resolved),
        submission_spec=ReconciliationSubmissionSpec(resolved),
    )
    registry.register(
        RecordExternalActivityJobHandler(settings=resolved),
        submission_spec=RecordExternalActivitySubmissionSpec(resolved),
    )
    registry.register(
        RiskEvaluationJobHandler(settings=resolved),
        submission_spec=RiskEvaluationSubmissionSpec(resolved),
    )
    registry.register(
        SyncMarketSessionsJobHandler(settings=resolved),
        submission_spec=SyncMarketSessionsSubmissionSpec(resolved),
    )
    registry.register(
        SyncSymbolMetadataJobHandler(settings=resolved),
        submission_spec=SyncSymbolMetadataSubmissionSpec(resolved),
    )

    return registry


RESEARCH_JOB_TYPES: tuple[str, ...] = (
    "catalog-sync",
    "ingest-tiingo-bars",
    "sync-market-sessions",
    "research-freeze",
    "research-backtest",
    "research-evaluate",
)
"""The closed set a research-mode process registers (plan S0 + S3). No trading Job type
(paper session, broker sync, reconciliation, risk evaluation, external activity,
Polygon ingest, symbol metadata sync) is reachable in research mode."""


def build_research_registry(settings: Settings | None = None) -> JobRegistry:
    """Registry for ``research.mode`` processes: exactly ``RESEARCH_JOB_TYPES``.

    Separate from ``build_default_registry`` so the trading registry's pinned
    contents never change. Research Job types are appended here as later stages
    land (``research-backtest``, ``research-evaluate`` in S3).
    """
    resolved = settings or load_settings()
    registry = JobRegistry()

    from trading_platform.jobs.handlers.catalog_sync import CatalogSyncJobHandler
    from trading_platform.jobs.handlers.catalog_sync_submission import CatalogSyncSubmissionSpec
    from trading_platform.jobs.handlers.ingest_tiingo_bars import IngestTiingoBarsJobHandler
    from trading_platform.jobs.handlers.ingest_tiingo_bars_submission import (
        IngestTiingoBarsSubmissionSpec,
    )
    from trading_platform.jobs.handlers.research_jobs import (
        ResearchBacktestJobHandler,
        ResearchEvaluateJobHandler,
        ResearchFreezeJobHandler,
    )
    from trading_platform.jobs.handlers.research_submission import (
        ResearchBacktestSubmissionSpec,
        ResearchEvaluateSubmissionSpec,
        ResearchFreezeSubmissionSpec,
    )
    from trading_platform.jobs.handlers.sync_market_sessions import (
        SyncMarketSessionsJobHandler,
    )
    from trading_platform.jobs.handlers.sync_market_sessions_submission import (
        SyncMarketSessionsSubmissionSpec,
    )

    registry.register(
        CatalogSyncJobHandler(settings=resolved), submission_spec=CatalogSyncSubmissionSpec(resolved)
    )
    registry.register(
        IngestTiingoBarsJobHandler(settings=resolved),
        submission_spec=IngestTiingoBarsSubmissionSpec(resolved),
    )
    registry.register(
        SyncMarketSessionsJobHandler(settings=resolved),
        submission_spec=SyncMarketSessionsSubmissionSpec(resolved),
    )
    registry.register(ResearchFreezeJobHandler(settings=resolved), submission_spec=ResearchFreezeSubmissionSpec(resolved))
    registry.register(
        ResearchBacktestJobHandler(settings=resolved), submission_spec=ResearchBacktestSubmissionSpec(resolved)
    )
    registry.register(
        ResearchEvaluateJobHandler(settings=resolved), submission_spec=ResearchEvaluateSubmissionSpec(resolved)
    )
    return registry


def build_registry_for(settings: Settings | None = None) -> JobRegistry:
    """The registry a process should run: research when ``research.mode`` is on, else trading."""
    resolved = settings or load_settings()
    if resolved.research.mode:
        return build_research_registry(resolved)
    return build_default_registry(resolved)
