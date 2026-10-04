"""COR-03/D-28: Job reads expose an additive derived ``outcome``.

The outcome is never stored on the Job (the ``jobs`` table is unchanged); it is
derived at read time from the persisted result_summary / status. Includes the
``710d46bf`` fixture: a SUCCEEDED ingest-bars Job whose summary lists failed
symbols and carries no stored outcome (written before COR-03).
"""

from __future__ import annotations

import pytest
from tests.support.migrated_db import migrated_database

from trading_platform.core.settings import load_settings
from trading_platform.db.models import Job, JobStatus
from trading_platform.db.session import session_scope
from trading_platform.services.job_reads import JobReadService

_BASE_KEYS = {
    "id",
    "job_type",
    "status",
    "queued_at",
    "started_at",
    "completed_at",
    "failure_reason",
    "outcome_uncertain",
    "cancellation_requested_at",
    "progress",
}


@pytest.fixture()
def outcomes_db(monkeypatch: pytest.MonkeyPatch):
    with migrated_database(monkeypatch, "job_outcomes") as name:
        yield name


def _seed(session, *, job_type: str, status: JobStatus, summary: dict) -> str:
    job = Job(job_type=job_type, payload={}, status=status, result_summary=summary)
    session.add(job)
    session.flush()
    return str(job.id)


def _read(job_id: str) -> tuple[dict, dict]:
    service = JobReadService(load_settings())
    detail = service.get_job_detail(job_id)
    listed = next(item for item in service.list_jobs() if item["id"] == job_id)
    return detail, listed


def test_job_710d46bf_fixture_reads_partial(outcomes_db: str) -> None:
    with session_scope(load_settings()) as session:
        job_id = _seed(
            session,
            job_type="ingest-bars",
            status=JobStatus.SUCCEEDED,
            summary={
                "symbols_failed": ["XYZ"],
                "ingestion_succeeded": False,
                "bars_upserted": 3,
            },
        )

    detail, listed = _read(job_id)

    assert detail["status"] == "succeeded"
    assert detail["outcome"] == "partial"
    assert listed["outcome"] == "partial"
    # additive: the stored summary is untouched and carries no outcome key
    assert "outcome" not in detail["result_summary"]


def test_clean_succeeded_job_reads_complete(outcomes_db: str) -> None:
    with session_scope(load_settings()) as session:
        job_id = _seed(
            session,
            job_type="ingest-bars",
            status=JobStatus.SUCCEEDED,
            summary={"symbols_failed": [], "ingestion_succeeded": True},
        )

    detail, listed = _read(job_id)

    assert detail["outcome"] == "complete"
    assert listed["outcome"] == "complete"


def test_all_symbols_failed_job_reads_failed(outcomes_db: str) -> None:
    with session_scope(load_settings()) as session:
        job_id = _seed(session, job_type="ingest-bars", status=JobStatus.FAILED, summary={})

    detail, listed = _read(job_id)

    assert detail["outcome"] == "failed"
    assert listed["outcome"] == "failed"


def test_stored_partial_outcome_is_read_as_stored(outcomes_db: str) -> None:
    with session_scope(load_settings()) as session:
        job_id = _seed(
            session,
            job_type="sync-symbol-metadata",
            status=JobStatus.SUCCEEDED,
            summary={"outcome": "partial", "failed": ["ZZZ"]},
        )

    detail, listed = _read(job_id)

    assert detail["outcome"] == "partial"
    assert listed["outcome"] == "partial"


def test_non_batch_job_outcome_is_null_and_contract_keys_remain(outcomes_db: str) -> None:
    with session_scope(load_settings()) as session:
        job_id = _seed(
            session,
            job_type="backtest",
            status=JobStatus.SUCCEEDED,
            summary={"symbols_failed": ["XYZ"]},
        )

    detail, listed = _read(job_id)

    assert detail["outcome"] is None
    assert listed["outcome"] is None
    assert _BASE_KEYS <= set(listed)
    assert _BASE_KEYS <= set(detail)
    assert "result_summary" in detail and "payload" in detail
