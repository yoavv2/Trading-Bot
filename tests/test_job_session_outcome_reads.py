"""20.1-14: additive Job read fields ``outcome`` / ``outcome_reason`` / ``outcome_detail`` /
``operation`` on GET /api/v1/jobs items and GET /api/v1/jobs/{id}.

``outcome`` is derived at read time (never stored). Batch values map one-to-one from the
20.1-04 ``derive_job_outcome``; a paper-session Job takes the operation state RECORDED BY
THAT JOB at its end, while ``operation`` is the CURRENT operation of the Job's operation.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

import pytest
from sqlalchemy import inspect as sa_inspect
from tests.support.migrated_db import migrated_database
from tests.support.operation_fixtures import seed_operation, seed_operation_job
from tests.support.paper_ownership import seed_registered_strategy
from tests.support.query_counter import count_queries
from tests.support.recovery_fixtures import OTHER, OWNER

from trading_platform.core.settings import load_settings
from trading_platform.db.models import ExecutionOperation, Job, JobStatus
from trading_platform.db.session import get_engine, session_scope
from trading_platform.services.batch_outcomes import BatchOutcome
from trading_platform.services.job_reads import JobOutcome, JobReadService

#: Statements ``list_jobs`` issued BEFORE this plan (one SELECT over jobs).
PRE_PLAN_LIST_STATEMENTS = 1

_NEW_KEYS = {"outcome", "outcome_reason", "outcome_detail", "operation"}


@pytest.fixture()
def reads_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    with migrated_database(monkeypatch, "job_session_outcomes") as name:
        seed_registered_strategy(load_settings(), OWNER, enabled=True, owner=True)
        seed_registered_strategy(load_settings(), OTHER, enabled=True, owner=False)
        yield name


def _seed(job_type: str, status: JobStatus, summary: dict | None) -> str:
    with session_scope(load_settings()) as session:
        job = Job(job_type=job_type, payload={}, status=status, result_summary=summary)
        session.add(job)
        session.flush()
        return str(job.id)


def _read(job_id: str) -> tuple[dict, dict]:
    service = JobReadService(load_settings())
    listed = next(item for item in service.list_jobs() if item["id"] == job_id)
    return service.get_job_detail(job_id), listed


def _fields(job_id: str) -> dict:
    detail, listed = _read(job_id)
    for key in ("outcome", "outcome_reason", "outcome_detail"):
        assert detail[key] == listed[key], key
    return {key: listed[key] for key in ("outcome", "outcome_reason", "outcome_detail")}


def _session_summary(state: str, reason: str | None, **extra: object) -> dict:
    return {
        "action": "submitted_orders",
        "operation": {"id": "op", "state": state, "reason": reason, "next_action": "none"},
        **extra,
    }


def test_job_outcome_value_set_is_the_closed_superset() -> None:
    assert {o.value for o in JobOutcome} == {
        "complete",
        "partial",
        "failed",
        "paused",
        "requires_reevaluation",
        "terminated",
        "blocked",
        "no_action",
    }
    assert {o.value for o in BatchOutcome} <= {o.value for o in JobOutcome}


def test_both_reads_carry_every_new_key(reads_db: str) -> None:
    job_id = _seed("backtest", JobStatus.SUCCEEDED, {})
    detail, listed = _read(job_id)

    assert _NEW_KEYS <= set(detail)
    assert _NEW_KEYS <= set(listed)
    assert listed["operation"] is None and detail["operation"] is None


def test_batch_complete_partial_failed_map_one_to_one(reads_db: str) -> None:
    complete = _seed("ingest-bars", JobStatus.SUCCEEDED, {"symbols_failed": []})
    partial = _seed("ingest-bars", JobStatus.SUCCEEDED, {"symbols_failed": ["XYZ", "ABC"]})
    failed = _seed("ingest-bars", JobStatus.FAILED, {})
    sync_partial = _seed("sync-symbol-metadata", JobStatus.SUCCEEDED, {"failed": [{"s": "A"}]})

    assert _fields(complete) == {
        "outcome": "complete",
        "outcome_reason": None,
        "outcome_detail": None,
    }
    assert _fields(partial) == {
        "outcome": "partial",
        "outcome_reason": None,
        "outcome_detail": {"failed_count": 2},
    }
    assert _fields(failed)["outcome"] == "failed"
    assert _fields(sync_partial)["outcome_detail"] == {"failed_count": 1}


@pytest.mark.parametrize(
    ("state", "reason", "expected"),
    [
        ("completed", None, "complete"),
        ("paused", "working_order_commitments_unaccounted", "paused"),
        ("requires_reevaluation", "evaluation_data_changed", "requires_reevaluation"),
        ("terminated", "execution_window_elapsed", "terminated"),
    ],
)
def test_paper_session_job_takes_the_operation_state_recorded_at_its_end(
    reads_db: str, state: str, reason: str | None, expected: str
) -> None:
    job_id = _seed("paper-session", JobStatus.SUCCEEDED, _session_summary(state, reason))

    assert _fields(job_id) == {
        "outcome": expected,
        "outcome_reason": reason,
        "outcome_detail": None,
    }


def test_older_paused_job_stays_paused_after_later_continue_completes_operation(
    reads_db: str,
) -> None:
    with session_scope(load_settings()) as session:
        start = seed_operation_job(session, status=JobStatus.SUCCEEDED)
        start.result_summary = _session_summary("paused", "kill_switch_tripped")
        cont = seed_operation_job(session, status=JobStatus.SUCCEEDED)
        cont.result_summary = _session_summary("completed", None, action="continued_session")
        seed_operation(
            session,
            state="completed",
            reason=None,
            session_date=date(2025, 12, 2),
            jobs=[(start, "start"), (cont, "continue")],
        )
        start_id, cont_id = str(start.id), str(cont.id)

    detail, listed = _read(start_id)
    for view in (detail, listed):
        assert view["outcome"] == "paused"
        assert view["outcome_reason"] == "kill_switch_tripped"
        # ... while the CURRENT operation shows it completed.
        assert view["operation"]["state"] == "completed"
    assert _read(cont_id)[1]["outcome"] == "complete"


@pytest.mark.parametrize(
    ("action", "expected", "reason"),
    [
        ("blocked_global_kill_switch", "blocked", "global_kill_switch"),
        ("blocked_outcome_unresolved", "blocked", "outcome_unresolved"),
        ("blocked_reconciliation", "blocked", "reconciliation"),
        ("noop_no_candidates", "no_action", "no_candidates"),
        ("noop_existing_orders", "no_action", "existing_orders"),
    ],
)
def test_blocked_and_noop_session_actions(
    reads_db: str, action: str, expected: str, reason: str
) -> None:
    job_id = _seed("paper-session", JobStatus.SUCCEEDED, {"action": action})

    fields = _fields(job_id)
    assert fields["outcome"] == expected
    assert fields["outcome_reason"] == reason


@pytest.mark.parametrize(
    "status", [JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.QUEUED, JobStatus.RUNNING]
)
def test_non_succeeded_paper_session_jobs_read_null(reads_db: str, status: JobStatus) -> None:
    job_id = _seed("paper-session", status, _session_summary("paused", "kill_switch_tripped"))

    assert _fields(job_id) == {
        "outcome": None,
        "outcome_reason": None,
        "outcome_detail": None,
    }


def test_types_without_outcome_semantics_and_legacy_session_jobs_read_null(reads_db: str) -> None:
    for job_type, summary in (
        ("backtest", {"run_id": "r"}),
        ("reconciliation", {}),
        ("paper-session", {}),
        ("paper-session", None),
    ):
        assert _fields(_seed(job_type, JobStatus.SUCCEEDED, summary))["outcome"] is None


def test_jobs_table_unchanged(reads_db: str) -> None:
    columns = {c["name"] for c in sa_inspect(get_engine(load_settings())).get_columns("jobs")}

    assert not ({"outcome", "outcome_reason", "outcome_detail", "operation"} & columns)
    assert {s.value for s in JobStatus} == {
        "queued",
        "running",
        "succeeded",
        "failed",
        "cancelled",
    }


def test_reads_write_nothing(reads_db: str) -> None:
    job_id = _seed("paper-session", JobStatus.SUCCEEDED, _session_summary("paused", "x"))
    statements: list[str] = []
    with count_queries(get_engine(load_settings())) as counter:
        _read(job_id)
    statements = counter.statements

    for statement in statements:
        assert statement.lstrip().split(None, 1)[0].upper() not in {"INSERT", "UPDATE", "DELETE"}


def test_list_adds_at_most_one_statement_and_is_independent_of_the_job_count(
    reads_db: str,
) -> None:
    def measure() -> int:
        with count_queries(get_engine(load_settings())) as counter:
            JobReadService(load_settings()).list_jobs()
        return counter.count

    for _ in range(5):
        _seed("paper-session", JobStatus.SUCCEEDED, _session_summary("paused", "x"))
    small = measure()
    for _ in range(45):
        _seed("ingest-bars", JobStatus.SUCCEEDED, {"symbols_failed": ["A"]})
    large = measure()

    assert small == large
    assert large <= PRE_PLAN_LIST_STATEMENTS + 1
    with session_scope(load_settings()) as session:
        assert session.query(ExecutionOperation).count() == 0  # nothing was written
