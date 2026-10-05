"""SAF-05: a Continue Job is attributed to its operation's strategy (20.1-19).

A Continue Job's payload is ``{mode: continue, operation_id}`` (no ``strategy_id``). Without
attribution every such Job counted as account-level: a flagged Continue Job of strategy A
gated strategy B and the account subject, and a completed one moved B's A6 boundary. All
Job-strategy reads go through ``broker_jobs.job_strategy_public_id_sql`` (recovery SQL) and
its ORM twin in ``reconciliation.latest``.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import select, text
from tests.support.migrated_db import migrated_database
from tests.support.operation_fixtures import seed_operation
from tests.support.query_counter import count_queries
from tests.support.recovery_fixtures import (
    OTHER,
    OWNER,
    at,
    seed_account_run,
    seed_job,
    seed_paper_run,
    strategy_row,
)

from trading_platform.core.settings import load_settings
from trading_platform.db.models import ExecutionOperation, ExecutionOperationJob, JobStatus
from trading_platform.db.session import session_scope
from trading_platform.services.broker_jobs import job_strategy_public_id_sql
from trading_platform.services.reconciliation import latest_broker_effect_at
from trading_platform.services.recovery import (
    GateCode,
    account_recovery_status,
    get_job_recovery,
    strategy_recovery_status,
)


@pytest.fixture()
def attribution_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    with migrated_database(monkeypatch, "continue_attribution") as name:
        yield name


def _arrange(builder: Any) -> Any:
    with session_scope(load_settings()) as session:
        return builder(session)


def _continue_job(
    session: Any,
    operation_id: uuid.UUID,
    *,
    uncertain: bool,
    completed_minute: int,
    linked_to: uuid.UUID | None = None,
) -> Any:
    """A Continue Job: payload carries only the operation id; the link row is optional."""

    job = seed_job(
        session,
        strategy_id=None,
        uncertain=uncertain,
        completed_at=at(completed_minute),
        status=JobStatus.FAILED if uncertain else JobStatus.SUCCEEDED,
        payload={"mode": "continue", "operation_id": str(operation_id)},
    )
    if linked_to is not None:
        session.add(ExecutionOperationJob(operation_id=linked_to, job_id=job.id, mode="continue"))
        session.flush()
    return job


def test_flagged_continue_job_of_a_does_not_block_b(attribution_db: str) -> None:
    def build(session: Any) -> uuid.UUID:
        strategy_row(session, OTHER)
        operation = seed_operation(session, strategy_id=OWNER)
        job = _continue_job(
            session, operation.id, uncertain=True, completed_minute=1, linked_to=operation.id
        )
        return job.id

    job_id = _arrange(build)
    with session_scope(load_settings()) as session:
        a = strategy_recovery_status(session, OWNER)
        b = strategy_recovery_status(session, OTHER)
        account = account_recovery_status(session)
    assert [j.job_id for j in a.jobs] == [job_id]
    assert not a.resolved and a.gate_code is GateCode.RECONCILIATION_REQUIRED
    assert b.jobs == () and b.intents == ()
    assert b.resolved and b.gate_code is None
    subjects = {s.strategy_id for s in account.subjects}
    assert OWNER in subjects
    # The Job is no longer account-level: it must not create the account-level subject.
    assert None not in subjects


def test_completed_continue_job_moves_only_its_strategy_boundary(attribution_db: str) -> None:
    def build(session: Any) -> None:
        strategy_row(session, OTHER)
        seed_job(
            session,
            strategy_id=OTHER,
            uncertain=False,
            completed_at=at(2),
            status=JobStatus.SUCCEEDED,
        )
        operation = seed_operation(session, strategy_id=OWNER)
        _continue_job(
            session, operation.id, uncertain=False, completed_minute=9, linked_to=operation.id
        )

    _arrange(build)
    with session_scope(load_settings()) as session:
        assert latest_broker_effect_at(session, OTHER) == at(2)
        assert latest_broker_effect_at(session, OWNER) == at(9)
        assert latest_broker_effect_at(session) == at(9)


def test_job_recovery_reports_the_operation_strategy_for_a_continue_job(
    attribution_db: str,
) -> None:
    def build(session: Any) -> uuid.UUID:
        strategy_row(session, OTHER)
        operation = seed_operation(session, strategy_id=OWNER)
        job = _continue_job(
            session, operation.id, uncertain=True, completed_minute=1, linked_to=operation.id
        )
        seed_paper_run(session, job, OWNER)
        return job.id

    job_id = _arrange(build)
    with session_scope(load_settings()) as session:
        view = get_job_recovery(session, job_id)
        statement = job_strategy_public_id_sql("jobs")
        resolved = session.execute(
            text(f"SELECT {statement} FROM jobs WHERE id = :id"), {"id": job_id}
        ).scalar_one()
    assert resolved == OWNER
    # The Job is listed under A's status (not the account-level subject) and stays unresolved.
    assert view["resolved"] is False
    assert view["gate_code"] == "outcome_unresolved"
    (intent,) = view["intents"]
    assert intent["unresolved_reason"] == "execution_path_unproven"


def test_unlinked_continue_job_is_attributed_through_its_payload_operation(
    attribution_db: str,
) -> None:
    """The run-time link row is written late; a Job that died first is still A's."""

    def build(session: Any) -> uuid.UUID:
        strategy_row(session, OTHER)
        operation = seed_operation(session, strategy_id=OWNER)
        job = _continue_job(session, operation.id, uncertain=True, completed_minute=1)
        return job.id

    job_id = _arrange(build)
    with session_scope(load_settings()) as session:
        a = strategy_recovery_status(session, OWNER)
        b = strategy_recovery_status(session, OTHER)
        account = account_recovery_status(session)
        assert get_job_recovery(session, job_id)["resolved"] is False
    assert [j.job_id for j in a.jobs] == [job_id]
    assert b.resolved and b.jobs == ()
    assert None not in {s.strategy_id for s in account.subjects}


def test_payload_and_account_jobs_keep_their_attribution(attribution_db: str) -> None:
    def build(session: Any) -> dict[str, uuid.UUID]:
        strategy_row(session, OTHER)
        named = seed_job(session, strategy_id=OTHER, uncertain=True, completed_at=at(1))
        account_sync = seed_job(
            session,
            job_type="broker-order-sync",
            strategy_id=None,
            uncertain=True,
            completed_at=at(2),
        )
        operation = seed_operation(session, strategy_id=OWNER)
        # A payload strategy_id wins over any operation (here deliberately different).
        both = seed_job(
            session,
            strategy_id=OTHER,
            uncertain=False,
            completed_at=at(3),
            status=JobStatus.SUCCEEDED,
            payload={"mode": "continue", "operation_id": str(operation.id)},
        )
        return {"named": named.id, "sync": account_sync.id, "both": both.id}

    ids = _arrange(build)
    with session_scope(load_settings()) as session:
        resolved = dict(
            session.execute(
                text(f"SELECT id, {job_strategy_public_id_sql('jobs')} AS s FROM jobs")
            ).all()
        )
        # The account-scope sync Job is account-level: it gates every strategy.
        a = strategy_recovery_status(session, OWNER)
    assert resolved[ids["named"]] == OTHER
    assert resolved[ids["sync"]] is None
    assert resolved[ids["both"]] == OTHER
    assert ids["sync"] in [j.job_id for j in a.jobs]


def test_sql_fragment_and_orm_expression_agree(attribution_db: str) -> None:
    """latest_broker_effect_at (ORM) and the SQL fragment attribute every Job kind alike."""

    def build(session: Any) -> None:
        strategy_row(session, OTHER)
        op_a = seed_operation(session, strategy_id=OWNER)
        # payload strategy_id (B), payload operation only (A), link only (A), account-level.
        seed_job(
            session,
            strategy_id=OTHER,
            uncertain=False,
            completed_at=at(1),
            status=JobStatus.SUCCEEDED,
        )
        _continue_job(session, op_a.id, uncertain=False, completed_minute=2)
        linked_only = seed_job(
            session,
            strategy_id=None,
            uncertain=False,
            completed_at=at(3),
            status=JobStatus.SUCCEEDED,
            payload={"mode": "continue"},
        )
        session.add(
            ExecutionOperationJob(operation_id=op_a.id, job_id=linked_only.id, mode="continue")
        )
        seed_job(
            session,
            job_type="broker-order-sync",
            strategy_id=None,
            uncertain=False,
            completed_at=at(4),
            status=JobStatus.SUCCEEDED,
        )
        session.flush()

    _arrange(build)
    sql = f"""
        SELECT coalesce(max(j.completed_at), NULL) FROM jobs j
         WHERE j.completed_at IS NOT NULL
           AND (CAST(:sid AS text) IS NULL OR {job_strategy_public_id_sql("j")} IS NULL
                OR {job_strategy_public_id_sql("j")} = CAST(:sid AS text))
    """
    with session_scope(load_settings()) as session:
        for sid in (OWNER, OTHER, None):
            via_sql = session.execute(text(sql), {"sid": sid}).scalar_one()
            assert via_sql == latest_broker_effect_at(session, sid), sid
        assert latest_broker_effect_at(session, OTHER) == at(4)  # B: own + account-level
        assert latest_broker_effect_at(session, OWNER) == at(4)


def test_fragment_rejects_a_non_identifier_alias() -> None:
    with pytest.raises(ValueError):
        job_strategy_public_id_sql("j; DROP TABLE jobs")


def test_status_reads_stay_bounded_with_continue_jobs(attribution_db: str) -> None:
    def grow(session: Any, count: int) -> None:
        operation = session.execute(select(ExecutionOperation)).scalars().first()
        if operation is None:
            operation = seed_operation(session, strategy_id=OWNER)
        for index in range(count):
            _continue_job(
                session,
                operation.id,
                uncertain=True,
                completed_minute=index,
                linked_to=operation.id,
            )
            seed_account_run(session, completed_at=at(index))

    def measure() -> tuple[int, int]:
        with session_scope(load_settings()) as session:
            with count_queries(session) as strategy_counter:
                strategy_recovery_status(session, OWNER)
            with count_queries(session) as account_counter:
                account_recovery_status(session)
        return strategy_counter.count, account_counter.count

    _arrange(lambda s: grow(s, 1))
    small = measure()
    _arrange(lambda s: grow(s, 10))
    assert measure() == small == (2, 2)
