"""Frozen handler contract for Phase 17 (job-framework).

``JobContext`` is the only channel through which a ``JobHandler`` reports
progress (D-11), emits structured logs (D-13), or observes cooperative
cancellation (D-08). This surface is frozen for the phase: downstream
Phase 17 plans (queue, lifecycle, dependencies, cancellation, progress,
API read routes) and Phase 19's concrete operation handlers all implement
or consume this contract as-is -- they do not extend it. Any change to
these Protocols is an architectural decision, not a routine addition.
"""

from __future__ import annotations

import uuid
from typing import Any, Mapping, Protocol, runtime_checkable


class JobCancelledError(Exception):
    """Raised by ``JobContext.raise_if_cancelled()`` once cancellation has
    been requested for the current Job.

    This is the cooperative-cancellation signal described by D-08: a
    handler that lets this exception propagate out of ``run()`` is
    acknowledging the cancellation request. The framework, not the
    handler, is responsible for translating that acknowledgement into a
    terminal ``CANCELLED`` state transition.
    """

    def __init__(self, job_id: uuid.UUID) -> None:
        self.job_id = job_id
        super().__init__(f"Job '{job_id}' was cancelled.")


@runtime_checkable
class JobContext(Protocol):
    """Framework-owned context passed to every ``JobHandler.run()`` call.

    A handler observes and reports through this object only -- it has no
    other route to Job state. The context implementation (provided by the
    queue/runner in a later Phase 17 plan) owns persistence, sanitization,
    and the lifecycle write path; nothing here grants a handler direct
    database or lifecycle access.
    """

    @property
    def job_id(self) -> uuid.UUID:
        """The identity of the Job this context was created for."""
        ...

    @property
    def job_type(self) -> str:
        """The registry key of the Job type currently executing."""
        ...

    @property
    def payload(self) -> Mapping[str, Any]:
        """The Job's submission-time payload, read-only from the handler's
        perspective."""
        ...

    def report_progress(
        self,
        *,
        percent: int | None = None,
        step: str | None = None,
        current: int | None = None,
        total: int | None = None,
    ) -> None:
        """Report a progress snapshot for the running Job (D-11).

        All four fields are optional; passing none of them is a no-op.
        ``percent``, when provided, must be within 0-100 inclusive --
        passing a value outside that range raises ``ValueError``.
        """
        ...

    def log(
        self,
        *,
        level: str,
        event_code: str,
        message: str,
        context: Mapping[str, Any] | None = None,
    ) -> None:
        """Append one structured log record for the running Job (D-13).

        The implementation sanitizes ``context`` before persisting it --
        callers must not assume raw values reach storage unmodified.
        """
        ...

    def is_cancellation_requested(self) -> bool:
        """Return whether cancellation has been requested for this Job,
        without raising (D-08 safe-point check)."""
        ...

    def raise_if_cancelled(self) -> None:
        """Raise ``JobCancelledError`` if cancellation has been requested
        for this Job (D-08 safe-point check); otherwise return normally."""
        ...


@runtime_checkable
class JobHandler(Protocol):
    """The registry-resolvable unit of work for one Job type.

    A handler may import and call ``trading_platform.services.*`` only.
    Per JOB-04, a handler must NOT import HTTP (``fastapi``/``starlette``),
    scheduling (``apscheduler``/``celery``), or UI modules, and it must
    NOT perform its own lifecycle writes to the ``jobs`` table -- all
    lifecycle state transitions are owned by the framework, never by
    handler code. A handler observes and reports exclusively through the
    ``JobContext`` it is given.
    """

    @property
    def job_type(self) -> str:
        """The registry key this handler is registered under."""
        ...

    def run(self, context: JobContext) -> Mapping[str, Any]:
        """Execute the operation and return a JSON-serializable result
        summary, persisted to ``jobs.result_summary`` by the framework."""
        ...
