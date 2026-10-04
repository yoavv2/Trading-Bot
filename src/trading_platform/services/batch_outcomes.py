"""Closed batch-operation outcome (COR-03, D-28).

A batch operation (``ingest-bars``, ``sync-symbol-metadata``) reports
``complete | partial | failed``. The outcome is derived at write time from the
DOMAIN result (``MarketDataIngestionRun.status`` / ``symbols_failed``,
``MetadataSyncResult``) and, for Jobs that predate it, at read time from the
persisted ``result_summary``. It is never stored on the Job (invariant 2: the
Job lifecycle stays the closed five-state set and the ``jobs`` table gains no
column).

Rules that hold everywhere in this module:

* an outcome is ``complete`` only when the failed set is empty;
* a FAILED Job is always ``failed``;
* only job types in ``BATCH_OUTCOME_JOB_TYPES`` have an outcome; every other
  Job type (and every non-terminal / cancelled Job) reads ``None``.

This module has no ``jobs/`` import (JOB-04 boundary): job status is compared
by its string value.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any


class BatchOutcome(StrEnum):
    """Closed batch outcome."""

    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"


class SymbolFailureReason(StrEnum):
    """Closed per-symbol failure reasons of the metadata sync (D-28)."""

    NOT_FOUND = "not_found"
    MISSING_REQUIRED_FIELDS = "missing_required_fields"
    INVALID_RESPONSE = "invalid_response"
    FETCH_ERROR = "fetch_error"


class OperationFailureReason(StrEnum):
    """Closed operation-level failure reasons of the metadata sync (D-28)."""

    PROVIDER_AUTH = "provider_auth"
    INVALID_CONFIGURATION = "invalid_configuration"
    DATABASE_WRITE = "database_write"
    PROVIDER_UNAVAILABLE = "provider_unavailable"


INGEST_BARS_JOB_TYPE = "ingest-bars"
SYNC_SYMBOL_METADATA_JOB_TYPE = "sync-symbol-metadata"

BATCH_OUTCOME_JOB_TYPES: frozenset[str] = frozenset(
    {INGEST_BARS_JOB_TYPE, SYNC_SYMBOL_METADATA_JOB_TYPE}
)

_JOB_STATUS_SUCCEEDED = "succeeded"
_JOB_STATUS_FAILED = "failed"

_RUN_STATUS_TO_OUTCOME: dict[str, BatchOutcome] = {
    "succeeded": BatchOutcome.COMPLETE,
    "partial": BatchOutcome.PARTIAL,
    "failed": BatchOutcome.FAILED,
}


def outcome_from_ingestion_run_status(run_status: str) -> BatchOutcome:
    """Map a persisted ``MarketDataIngestionRun.status`` to the batch outcome."""

    try:
        return _RUN_STATUS_TO_OUTCOME[str(run_status)]
    except KeyError:
        raise ValueError(f"Unknown ingestion run status: {run_status!r}") from None


def derive_batch_outcome(
    succeeded_count: int,
    failed_count: int,
    operation_failed: bool = False,
) -> BatchOutcome:
    """Derive the outcome from domain counts.

    An operation-level failure or zero successes is ``failed``; otherwise any
    failure is ``partial``; otherwise ``complete``.
    """

    if operation_failed or succeeded_count <= 0:
        return BatchOutcome.FAILED
    if failed_count >= 1:
        return BatchOutcome.PARTIAL
    return BatchOutcome.COMPLETE


def _coerce_outcome(value: Any) -> BatchOutcome | None:
    if isinstance(value, BatchOutcome):
        return value
    if isinstance(value, str):
        try:
            return BatchOutcome(value)
        except ValueError:
            return None
    return None


def _non_empty(value: Any) -> bool:
    return isinstance(value, (list, tuple)) and len(value) > 0


def derive_job_outcome(
    job_type: str,
    job_status: Any,
    result_summary: Mapping[str, Any] | None,
) -> BatchOutcome | None:
    """Read-time outcome of a Job, or ``None`` when the Job has none.

    The stored ``result_summary['outcome']`` is used when valid; otherwise the
    legacy summary is interpreted (for example the Job whose summary only
    carries ``symbols_failed``). A non-empty failed set is never ``complete``.
    """

    if job_type not in BATCH_OUTCOME_JOB_TYPES:
        return None
    status = getattr(job_status, "value", job_status)
    if status == _JOB_STATUS_FAILED:
        return BatchOutcome.FAILED
    if status != _JOB_STATUS_SUCCEEDED:
        return None

    summary: Mapping[str, Any] = result_summary or {}
    failed_key = "symbols_failed" if job_type == INGEST_BARS_JOB_TYPE else "failed"
    has_failures = _non_empty(summary.get(failed_key))

    stored = _coerce_outcome(summary.get("outcome"))
    if stored is not None:
        if stored is BatchOutcome.COMPLETE and has_failures:
            return BatchOutcome.PARTIAL
        return stored
    return BatchOutcome.PARTIAL if has_failures else BatchOutcome.COMPLETE


__all__ = [
    "BATCH_OUTCOME_JOB_TYPES",
    "BatchOutcome",
    "OperationFailureReason",
    "SymbolFailureReason",
    "derive_batch_outcome",
    "derive_job_outcome",
    "outcome_from_ingestion_run_status",
]
