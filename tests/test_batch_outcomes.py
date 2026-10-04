"""COR-03/D-28: closed batch outcome, derivation helpers, jobs table unchanged."""

from __future__ import annotations

import itertools

import pytest

from trading_platform.db.models import Job, JobStatus
from trading_platform.services.batch_outcomes import (
    BATCH_OUTCOME_JOB_TYPES,
    BatchOutcome,
    OperationFailureReason,
    SymbolFailureReason,
    derive_batch_outcome,
    derive_job_outcome,
    outcome_from_ingestion_run_status,
)

# Column set of ``jobs`` copied from the model BEFORE 20.1-04 edited anything.
# COR-03 outcomes are derived, never stored: any added column fails this test.
_JOBS_COLUMNS_BEFORE_20_1_04 = frozenset(
    {
        "blocking_job_id",
        "blocking_job_status",
        "cancellation_acknowledged_at",
        "cancellation_cause",
        "cancellation_reason",
        "cancellation_requested_at",
        "cancellation_requested_by",
        "completed_at",
        "created_at",
        "failure_message",
        "failure_reason",
        "heartbeat_at",
        "id",
        "job_type",
        "lease_expires_at",
        "lease_owner",
        "outcome_uncertain",
        "payload",
        "progress_current",
        "progress_percent",
        "progress_step",
        "progress_total",
        "progress_updated_at",
        "queued_at",
        "result_summary",
        "retry_of_job_id",
        "root_cause_job_id",
        "started_at",
        "status",
        "updated_at",
    }
)


def test_closed_outcome_enum() -> None:
    assert {member.value for member in BatchOutcome} == {"complete", "partial", "failed"}


def test_jobs_table_columns_are_unchanged() -> None:
    assert {column.name for column in Job.__table__.columns} == _JOBS_COLUMNS_BEFORE_20_1_04


def test_closed_reason_enums() -> None:
    assert {member.value for member in SymbolFailureReason} == {
        "not_found",
        "missing_required_fields",
        "invalid_response",
        "fetch_error",
    }
    assert {member.value for member in OperationFailureReason} == {
        "provider_auth",
        "invalid_configuration",
        "database_write",
        "provider_unavailable",
    }


@pytest.mark.parametrize(
    ("run_status", "expected"),
    [
        ("succeeded", BatchOutcome.COMPLETE),
        ("partial", BatchOutcome.PARTIAL),
        ("failed", BatchOutcome.FAILED),
    ],
)
def test_outcome_from_ingestion_run_status(run_status: str, expected: BatchOutcome) -> None:
    assert outcome_from_ingestion_run_status(run_status) is expected


@pytest.mark.parametrize("bad", ["running", "", "complete", "SUCCEEDED"])
def test_unknown_run_status_raises(bad: str) -> None:
    with pytest.raises(ValueError):
        outcome_from_ingestion_run_status(bad)


@pytest.mark.parametrize(
    ("succeeded", "failed", "operation_failed", "expected"),
    [
        (3, 0, False, BatchOutcome.COMPLETE),
        (2, 1, False, BatchOutcome.PARTIAL),
        (0, 2, False, BatchOutcome.FAILED),
        (0, 0, False, BatchOutcome.FAILED),
        (3, 0, True, BatchOutcome.FAILED),
        (2, 1, True, BatchOutcome.FAILED),
    ],
)
def test_derive_batch_outcome(
    succeeded: int, failed: int, operation_failed: bool, expected: BatchOutcome
) -> None:
    assert derive_batch_outcome(succeeded, failed, operation_failed) is expected


@pytest.mark.parametrize(
    ("job_type", "status", "summary", "expected"),
    [
        ("ingest-bars", JobStatus.FAILED, {}, "failed"),
        ("ingest-bars", JobStatus.SUCCEEDED, {"outcome": "partial"}, "partial"),
        ("ingest-bars", JobStatus.SUCCEEDED, {"outcome": "complete", "symbols_failed": []}, "complete"),
        ("ingest-bars", JobStatus.SUCCEEDED, {"symbols_failed": ["XYZ"]}, "partial"),
        ("ingest-bars", JobStatus.SUCCEEDED, {"symbols_failed": []}, "complete"),
        ("ingest-bars", JobStatus.SUCCEEDED, {"outcome": "bogus", "symbols_failed": ["X"]}, "partial"),
        ("sync-symbol-metadata", JobStatus.SUCCEEDED, {"failed": ["ZZZ"]}, "partial"),
        ("sync-symbol-metadata", JobStatus.SUCCEEDED, {"failed": []}, "complete"),
        ("sync-symbol-metadata", JobStatus.SUCCEEDED, {"outcome": "partial", "failed": ["ZZZ"]}, "partial"),
        ("sync-symbol-metadata", JobStatus.FAILED, {"failed": ["ZZZ"]}, "failed"),
        ("ingest-bars", JobStatus.QUEUED, {}, None),
        ("ingest-bars", JobStatus.RUNNING, {}, None),
        ("ingest-bars", JobStatus.CANCELLED, {}, None),
        ("backtest", JobStatus.SUCCEEDED, {"symbols_failed": ["X"]}, None),
        ("paper-session", JobStatus.FAILED, {}, None),
    ],
)
def test_derive_job_outcome_table(job_type, status, summary, expected) -> None:
    result = derive_job_outcome(job_type, status, summary)
    assert (result.value if result is not None else None) == expected


def test_batch_outcome_job_types_are_exactly_two() -> None:
    assert BATCH_OUTCOME_JOB_TYPES == {"ingest-bars", "sync-symbol-metadata"}


def test_no_read_returns_complete_for_a_non_empty_failed_set() -> None:
    """T-20.1-04-01: never `complete` when the failed set is non-empty."""

    stored_outcomes = [None, "complete", "partial", "failed", "bogus"]
    for job_type, status, stored in itertools.product(
        sorted(BATCH_OUTCOME_JOB_TYPES), list(JobStatus), stored_outcomes
    ):
        failed_key = "symbols_failed" if job_type == "ingest-bars" else "failed"
        summary: dict[str, object] = {failed_key: ["XYZ"]}
        if stored is not None:
            summary["outcome"] = stored
        outcome = derive_job_outcome(job_type, status, summary)
        assert outcome is not BatchOutcome.COMPLETE, (job_type, status, stored)
