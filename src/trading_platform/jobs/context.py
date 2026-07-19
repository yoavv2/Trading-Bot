"""The concrete ``JobContext`` a handler receives (JOB-04, D-08, D-11, D-13).

``DatabaseJobContext`` is the only object a ``JobHandler.run()`` call is
given. It exposes no session, no engine, and no status setter -- that is
what keeps JOB-04's "handlers never write Job status directly" boundary
true at runtime, not only statically via the ``JobContext`` Protocol.

Each public method opens its own short ``session_scope`` transaction
rather than holding one open across handler work, so progress and log
writes are durable and visible to API readers while the handler is still
running (the "during execution" half of JOB-07).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, Mapping

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from trading_platform.core.log_sanitizer import sanitize
from trading_platform.core.settings import Settings
from trading_platform.db.models.job import Job
from trading_platform.db.models.job_log import JobLog
from trading_platform.db.session import session_scope
from trading_platform.jobs.contracts import JobCancelledError
from trading_platform.jobs.progress import ProgressSnapshot, apply_progress

# API layer (plan 17-08) references these two limits directly.
MAX_LOG_MESSAGE_CHARS = 4000
MAX_LOG_CONTEXT_BYTES = 16384

_VALID_LOG_LEVELS = frozenset({"debug", "info", "warning", "error", "critical"})
_MAX_SEQUENCE_RETRY_ATTEMPTS = 2


class DatabaseJobContext:
    """Framework-owned execution context handed to a running Job's handler."""

    def __init__(
        self,
        job_id: uuid.UUID,
        job_type: str,
        payload: Mapping[str, Any],
        settings: Settings | None = None,
    ) -> None:
        self._job_id = job_id
        self._job_type = job_type
        self._payload = MappingProxyType(dict(payload))
        self._settings = settings
        self._cancellation_requested_cache = False

    @property
    def job_id(self) -> uuid.UUID:
        return self._job_id

    @property
    def job_type(self) -> str:
        return self._job_type

    @property
    def payload(self) -> Mapping[str, Any]:
        return self._payload

    def report_progress(
        self,
        *,
        percent: int | None = None,
        step: str | None = None,
        current: int | None = None,
        total: int | None = None,
    ) -> None:
        # ProgressSnapshot's own validation raises ValueError on a bad
        # percent/current/total before any database access happens.
        snapshot = ProgressSnapshot(percent=percent, step=step, current=current, total=total)
        if snapshot.is_empty():
            return

        now = datetime.now(UTC)
        with session_scope(self._settings) as session:
            job = session.get(Job, self._job_id)
            if job is None:
                raise ValueError(f"Job '{self._job_id}' does not exist.")
            apply_progress(job, snapshot, now=now)

    def log(
        self,
        *,
        level: str,
        event_code: str,
        message: str,
        context: Mapping[str, Any] | None = None,
    ) -> None:
        normalized_level = level.lower()
        if normalized_level not in _VALID_LOG_LEVELS:
            raise ValueError(
                f"Invalid log level '{level}'; must be one of {sorted(_VALID_LOG_LEVELS)}."
            )

        truncated_message = message[:MAX_LOG_MESSAGE_CHARS]

        # This is the single Job-log write path. Every context dict is
        # routed through trading_platform.core.log_sanitizer.sanitize --
        # the same chokepoint LOG-06's enforcement test pins for process
        # logging. Inventing a second sanitization path for Job logs, or
        # assigning to JobLog.context anywhere else in the codebase, is
        # forbidden (D-13).
        sanitized_context = sanitize(dict(context) if context is not None else {})
        sanitized_context = _enforce_context_size_limit(sanitized_context)

        logged_at = datetime.now(UTC)

        attempt = 0
        while True:
            attempt += 1
            try:
                with session_scope(self._settings) as session:
                    # Row-lock the parent Job so two concurrent writers
                    # cannot both compute the same next sequence value
                    # and collide on (job_id, sequence).
                    job = session.get(Job, self._job_id, with_for_update=True)
                    if job is None:
                        raise ValueError(f"Job '{self._job_id}' does not exist.")

                    max_sequence = session.execute(
                        select(func.max(JobLog.sequence)).where(JobLog.job_id == self._job_id)
                    ).scalar_one()
                    next_sequence = (max_sequence or 0) + 1

                    session.add(
                        JobLog(
                            job_id=self._job_id,
                            sequence=next_sequence,
                            logged_at=logged_at,
                            level=normalized_level,
                            event_code=event_code,
                            message=truncated_message,
                            handler_type=self._job_type,
                            context=sanitized_context,
                        )
                    )
                return
            except IntegrityError:
                if attempt >= _MAX_SEQUENCE_RETRY_ATTEMPTS:
                    raise
                continue

    def is_cancellation_requested(self) -> bool:
        # Never cache a False result -- only a confirmed True is durable
        # for the lifetime of this context (cancellation is one-way).
        if self._cancellation_requested_cache:
            return True

        with session_scope(self._settings) as session:
            job = session.get(Job, self._job_id)
            requested = job is not None and job.cancellation_requested_at is not None

        if requested:
            self._cancellation_requested_cache = True
        return requested

    def raise_if_cancelled(self) -> None:
        if self.is_cancellation_requested():
            raise JobCancelledError(self._job_id)


def _enforce_context_size_limit(context: dict[str, Any]) -> dict[str, Any]:
    """Replace an oversized context dict with a truncation marker.

    Applied after sanitization, so ``_original_bytes`` reflects the size
    of what would otherwise have been persisted.
    """
    serialized = json.dumps(context, default=str)
    size = len(serialized.encode("utf-8"))
    if size <= MAX_LOG_CONTEXT_BYTES:
        return context
    return {"_truncated": True, "_original_bytes": size}
