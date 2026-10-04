"""DB-backed tests for the paper account checks A1-A7 (PAPER-02, D-04, 20.1-12 Task 1).

The checks read persisted evidence only. Every closed reason code is reached by a failing
case, every check has a passing case, and the evaluator is read-only and bounded.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Iterator
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from tests.support.account_check_fixtures import (
    seed_clean_account_run,
    seed_quiet_account,
    seed_snapshot,
)
from tests.support.calendar_facts import et, seed_calendar
from tests.support.migrated_db import migrated_database
from tests.support.operation_fixtures import seed_operation, seed_operation_intent
from tests.support.paper_ownership import set_active_paper_strategy
from tests.support.query_counter import count_queries
from tests.support.recovery_fixtures import (
    OTHER,
    OWNER,
    at,
    seed_job,
    seed_recording,
    seed_strategy_reconciliation,
    seed_uncertain_session,
    strategy_row,
)
from tests.test_recovery_predicate import LookupBroker

from trading_platform.core import clock
from trading_platform.core.settings import load_settings
from trading_platform.db.models import (
    AccountReconciliationRun,
    ExecutionEvent,
    ExecutionOperation,
    Job,
    JobStatus,
    StrategyRun,
    StrategyStatus,
)
from trading_platform.db.session import get_engine, session_scope
from trading_platform.services import paper_account_checks as checks_module
from trading_platform.services.broker_jobs import BROKER_TOUCHING_JOB_TYPES
from trading_platform.services.execution.sync_orders import sync_account_state
from trading_platform.services.paper_account_checks import (
    AccountChecks,
    CheckId,
    CheckReason,
    CheckResult,
    EvidenceKind,
    EvidenceRef,
    evaluate_account_checks,
)
from trading_platform.services.recovery import record_broker_statement

S = date(2025, 12, 2)
IN_WINDOW = et(2025, 12, 3, 10, 0)
PAST_CUTOFF = et(2025, 12, 3, 15, 50)


@pytest.fixture()
def checks_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    with migrated_database(monkeypatch, "paper_account_checks") as name:
        seed_calendar(date(2025, 11, 24), date(2026, 1, 9))
        yield name


def _arrange(builder: Callable[[Any], Any]) -> Any:
    with session_scope(load_settings()) as session:
        return builder(session)


def _evaluate(*, include_handover: bool = False, now: datetime | None = None) -> AccountChecks:
    with session_scope(load_settings()) as session:
        return evaluate_account_checks(session, now=now, include_handover=include_handover)


def _reason(result: AccountChecks, check_id: CheckId) -> CheckReason | None:
    check = result.get(check_id)
    assert check is not None, check_id
    return check.reason_code


# ---------------------------------------------------------------------------
# Closed vocabularies
# ---------------------------------------------------------------------------


def test_closed_enums_are_pinned() -> None:
    assert [member.value for member in CheckId] == ["A1", "A2", "A3", "A4", "A5", "A6", "A7"]
    assert {member.value for member in EvidenceKind} == {
        "job",
        "operation",
        "account_reconciliation_run",
        "account_snapshot",
        "strategy",
        "recovery_intent",
    }
    assert {member.value for member in CheckReason} == {
        "broker_job_active",
        "open_operation",
        "no_account_reconciliation",
        "non_terminal_orders",
        "no_broker_observed_snapshot",
        "open_positions",
        "unexplained_exposure",
        "unrecognized_items",
        "unresolved_outcome",
        "reconciliation_stale",
        "reconciliation_not_clean",
        "outgoing_owner_enabled",
    }


# ---------------------------------------------------------------------------
# One passing case and one failing case per reason code
# ---------------------------------------------------------------------------


def _quiet(session: Any) -> None:
    seed_quiet_account(session)


def _only_snapshot(session: Any) -> None:
    seed_snapshot(session)


def _only_run(session: Any) -> None:
    seed_clean_account_run(session, completed_at=at(10))


def _queued_job(session: Any) -> None:
    _quiet(session)
    seed_job(
        session,
        job_type="paper-session",
        uncertain=False,
        status=JobStatus.QUEUED,
        completed_at=None,
    )


def _open_operation(session: Any) -> None:
    _quiet(session)
    operation = seed_operation(session, state="paused", reason="awaiting_reconciliation")
    seed_operation_intent(session, operation)


def _non_terminal(session: Any) -> None:
    seed_snapshot(session)
    seed_clean_account_run(session, completed_at=at(10), non_terminal=2)


def _open_positions(session: Any) -> None:
    seed_snapshot(session, open_positions=3)
    seed_clean_account_run(session, completed_at=at(10))


def _exposure(session: Any) -> None:
    seed_snapshot(session)
    seed_clean_account_run(session, completed_at=at(10), exposure={"AAPL": "3"})


def _unrecognized(session: Any) -> None:
    seed_snapshot(session)
    seed_clean_account_run(session, completed_at=at(10), unrecognized_orders=1)


def _uncertain(session: Any) -> None:
    _quiet(session)
    seed_uncertain_session(session, completed_at=at(-5))


def _stale(session: Any) -> None:
    _quiet(session)
    seed_job(
        session,
        job_type="paper-session",
        uncertain=False,
        status=JobStatus.SUCCEEDED,
        completed_at=at(20),
    )


def _not_clean(session: Any) -> None:
    seed_snapshot(session)
    seed_clean_account_run(session, completed_at=at(10), blocks=True)


def _owner_enabled(session: Any) -> None:
    _quiet(session)
    strategy_row(session, OWNER, enabled=True)
    set_active_paper_strategy(session, OWNER)


#: (reason, arrange, check, evidence kinds expected on the failing check, include_handover)
REASON_CASES: list[tuple[CheckReason, Callable[[Any], None], CheckId, set[EvidenceKind], bool]] = [
    (CheckReason.BROKER_JOB_ACTIVE, _queued_job, CheckId.A1, {EvidenceKind.JOB}, False),
    (CheckReason.OPEN_OPERATION, _open_operation, CheckId.A1, {EvidenceKind.OPERATION}, False),
    (CheckReason.NO_ACCOUNT_RECONCILIATION, _only_snapshot, CheckId.A2, set(), False),
    (
        CheckReason.NON_TERMINAL_ORDERS,
        _non_terminal,
        CheckId.A2,
        {EvidenceKind.ACCOUNT_RECONCILIATION_RUN},
        False,
    ),
    (CheckReason.NO_BROKER_OBSERVED_SNAPSHOT, _only_run, CheckId.A3, set(), False),
    (
        CheckReason.OPEN_POSITIONS,
        _open_positions,
        CheckId.A3,
        {EvidenceKind.ACCOUNT_SNAPSHOT},
        False,
    ),
    (
        CheckReason.UNEXPLAINED_EXPOSURE,
        _exposure,
        CheckId.A3,
        {EvidenceKind.ACCOUNT_RECONCILIATION_RUN},
        False,
    ),
    (
        CheckReason.UNRECOGNIZED_ITEMS,
        _unrecognized,
        CheckId.A4,
        {EvidenceKind.ACCOUNT_RECONCILIATION_RUN},
        False,
    ),
    (
        CheckReason.UNRESOLVED_OUTCOME,
        _uncertain,
        CheckId.A5,
        {EvidenceKind.JOB, EvidenceKind.STRATEGY, EvidenceKind.RECOVERY_INTENT},
        False,
    ),
    (
        CheckReason.RECONCILIATION_STALE,
        _stale,
        CheckId.A6,
        {EvidenceKind.ACCOUNT_RECONCILIATION_RUN},
        False,
    ),
    (
        CheckReason.RECONCILIATION_NOT_CLEAN,
        _not_clean,
        CheckId.A6,
        {EvidenceKind.ACCOUNT_RECONCILIATION_RUN},
        False,
    ),
    (CheckReason.OUTGOING_OWNER_ENABLED, _owner_enabled, CheckId.A7, {EvidenceKind.STRATEGY}, True),
]


def test_every_reason_code_has_a_failing_case() -> None:
    assert {case[0] for case in REASON_CASES} == set(CheckReason)


@pytest.mark.parametrize(
    ("reason", "arrange", "check_id", "kinds", "handover"),
    REASON_CASES,
    ids=[case[0].value for case in REASON_CASES],
)
def test_failing_reason_cases(
    checks_db: str,
    reason: CheckReason,
    arrange: Callable[[Any], None],
    check_id: CheckId,
    kinds: set[EvidenceKind],
    handover: bool,
) -> None:
    _arrange(arrange)

    result = _evaluate(include_handover=handover)

    check = result.get(check_id)
    assert check is not None and check.passed is False
    assert check.reason_code is reason
    assert {ref.kind for ref in check.evidence_refs} == kinds or (
        reason is CheckReason.UNRESOLVED_OUTCOME
        and {ref.kind for ref in check.evidence_refs} >= {EvidenceKind.JOB}
    )


def test_quiet_account_passes_every_check_and_a7_needs_handover_with_an_owner(
    checks_db: str,
) -> None:
    _arrange(_quiet)

    seeding = _evaluate()
    assert [check.id for check in seeding.checks] == [
        CheckId.A1,
        CheckId.A2,
        CheckId.A3,
        CheckId.A4,
        CheckId.A5,
        CheckId.A6,
    ]
    assert seeding.all_passed and seeding.first_failed() is None
    assert all(check.reason_code is None and check.evidence_refs == () for check in seeding.checks)

    # include_handover without an owner has no outgoing owner: A7 is not evaluated.
    assert _evaluate(include_handover=True).get(CheckId.A7) is None

    def _disabled_owner(session: Any) -> None:
        strategy_row(session, OWNER, enabled=False)
        set_active_paper_strategy(session, OWNER)

    _arrange(_disabled_owner)
    assert _evaluate(include_handover=False).get(CheckId.A7) is None
    handover = _evaluate(include_handover=True)
    a7 = handover.get(CheckId.A7)
    assert a7 is not None and a7.passed and handover.all_passed


# ---------------------------------------------------------------------------
# A1
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("job_type", sorted(BROKER_TOUCHING_JOB_TYPES))
@pytest.mark.parametrize("status", [JobStatus.QUEUED, JobStatus.RUNNING])
@pytest.mark.parametrize("strategy_id", [OWNER, None])
def test_a1_counts_each_broker_touching_type_and_any_scope(
    checks_db: str, job_type: str, status: JobStatus, strategy_id: str | None
) -> None:
    def build(session: Any) -> Job:
        _quiet(session)
        return seed_job(
            session,
            job_type=job_type,
            strategy_id=strategy_id,
            uncertain=False,
            status=status,
            completed_at=None,
        )

    job = _arrange(build)
    a1 = _evaluate().get(CheckId.A1)
    assert a1 is not None and a1.reason_code is CheckReason.BROKER_JOB_ACTIVE
    assert a1.evidence_refs == (EvidenceRef(EvidenceKind.JOB, str(job.id)),)


def test_a1_ignores_terminal_jobs_and_non_broker_job_types(checks_db: str) -> None:
    def build(session: Any) -> None:
        _quiet(session)
        for status in (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED):
            seed_job(session, uncertain=False, status=status, completed_at=at(-30))
        seed_job(
            session,
            job_type="ingestion",
            uncertain=False,
            status=JobStatus.RUNNING,
            completed_at=None,
        )

    _arrange(build)
    assert _evaluate().get(CheckId.A1).passed is True  # type: ignore[union-attr]


def test_a1_counts_operations_by_effective_state_any_strategy(
    checks_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def build(session: Any) -> Any:
        _quiet(session)
        # Another strategy's open operation blocks too; the paused one is untouched.
        operation = seed_operation(
            session,
            strategy_id=OTHER,
            state="paused",
            reason="awaiting_reconciliation",
            session_date=S,
        )
        seed_operation_intent(session, operation)
        return operation.id

    operation_id = _arrange(build)

    in_window = _evaluate(now=IN_WINDOW).get(CheckId.A1)
    assert in_window is not None and in_window.reason_code is CheckReason.OPEN_OPERATION
    assert in_window.evidence_refs == (EvidenceRef(EvidenceKind.OPERATION, str(operation_id)),)

    # An expired but untouched operation counts as ended (no lazy expiry, no write).
    expired = _evaluate(now=PAST_CUTOFF).get(CheckId.A1)
    assert expired is not None and expired.passed is True
    with session_scope(load_settings()) as session:
        persisted = session.get(ExecutionOperation, operation_id)
        assert persisted is not None and persisted.state == "paused"

    # A terminated operation never blocks.
    def end(session: Any) -> None:
        operation = session.get(ExecutionOperation, operation_id)
        operation.state = "terminated"
        operation.reason = "cancelled_by_operator"
        operation.ended_by = "op"

    _arrange(end)
    assert _evaluate(now=IN_WINDOW).get(CheckId.A1).passed is True  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Absent evidence, subordination and ordering
# ---------------------------------------------------------------------------


def test_fresh_install_names_a6_not_a2_a3_a4(checks_db: str) -> None:
    result = _evaluate()

    assert {check.id for check in result.checks if not check.passed} == {
        CheckId.A2,
        CheckId.A3,
        CheckId.A4,
        CheckId.A6,
    }
    assert _reason(result, CheckId.A2) is CheckReason.NO_ACCOUNT_RECONCILIATION
    assert _reason(result, CheckId.A3) is CheckReason.NO_BROKER_OBSERVED_SNAPSHOT
    assert _reason(result, CheckId.A4) is CheckReason.NO_ACCOUNT_RECONCILIATION
    assert _reason(result, CheckId.A6) is CheckReason.NO_ACCOUNT_RECONCILIATION
    first = result.first_failed()
    assert first is not None and first.id is CheckId.A6
    assert set(result.failed_ids()) == {CheckId.A2, CheckId.A3, CheckId.A4, CheckId.A6}


def test_a_real_a3_failure_is_named_even_though_a6_passes(checks_db: str) -> None:
    _arrange(_open_positions)

    result = _evaluate()

    assert result.get(CheckId.A6).passed is True  # type: ignore[union-attr]
    first = result.first_failed()
    assert first is not None
    assert (first.id, first.reason_code) == (CheckId.A3, CheckReason.OPEN_POSITIONS)


def test_absent_snapshot_with_a_passing_a6_is_named_a3(checks_db: str) -> None:
    _arrange(_only_run)

    first = _evaluate().first_failed()

    assert first is not None
    assert (first.id, first.reason_code) == (CheckId.A3, CheckReason.NO_BROKER_OBSERVED_SNAPSHOT)


def test_first_failed_follows_a1_to_a7_order_over_multi_failure_states() -> None:
    def fail(check_id: CheckId, reason: CheckReason) -> CheckResult:
        return CheckResult(check_id, False, reason)

    def ok(check_id: CheckId) -> CheckResult:
        return CheckResult(check_id, True, None)

    now = datetime(2026, 1, 1, tzinfo=at(0).tzinfo)
    multi = AccountChecks(
        checks=(
            ok(CheckId.A1),
            ok(CheckId.A2),
            fail(CheckId.A3, CheckReason.OPEN_POSITIONS),
            ok(CheckId.A4),
            fail(CheckId.A5, CheckReason.UNRESOLVED_OUTCOME),
            fail(CheckId.A6, CheckReason.RECONCILIATION_STALE),
            fail(CheckId.A7, CheckReason.OUTGOING_OWNER_ENABLED),
        ),
        as_of=now,
    )
    first = multi.first_failed()
    assert first is not None and first.id is CheckId.A3

    # E2: A5 is named ahead of A6 (the 29 Sep outcomes), both are listed.
    e2 = AccountChecks(
        checks=(
            ok(CheckId.A1),
            fail(CheckId.A2, CheckReason.NO_ACCOUNT_RECONCILIATION),
            fail(CheckId.A3, CheckReason.NO_BROKER_OBSERVED_SNAPSHOT),
            fail(CheckId.A4, CheckReason.NO_ACCOUNT_RECONCILIATION),
            fail(CheckId.A5, CheckReason.UNRESOLVED_OUTCOME),
            fail(CheckId.A6, CheckReason.NO_ACCOUNT_RECONCILIATION),
        ),
        as_of=now,
    )
    first = e2.first_failed()
    assert first is not None and first.id is CheckId.A5

    # Absent-evidence A2/A4 are only subordinate while A6 is failing.
    no_a6 = AccountChecks(
        checks=(
            ok(CheckId.A1),
            fail(CheckId.A2, CheckReason.NO_ACCOUNT_RECONCILIATION),
            ok(CheckId.A6),
        ),
        as_of=now,
    )
    first = no_a6.first_failed()
    assert first is not None and first.id is CheckId.A2


def test_strategy_level_run_never_satisfies_account_checks(checks_db: str) -> None:
    def build(session: Any) -> None:
        seed_snapshot(session)
        seed_strategy_reconciliation(session, completed_at=at(10))
        seed_strategy_reconciliation(session, strategy_id=OTHER, completed_at=at(11))

    _arrange(build)

    result = _evaluate()

    assert _reason(result, CheckId.A2) is CheckReason.NO_ACCOUNT_RECONCILIATION
    assert _reason(result, CheckId.A4) is CheckReason.NO_ACCOUNT_RECONCILIATION
    assert _reason(result, CheckId.A6) is CheckReason.NO_ACCOUNT_RECONCILIATION
    first = result.first_failed()
    assert first is not None and first.id is CheckId.A6


def test_a_failed_account_run_is_never_clean_and_never_falls_back_to_an_older_run(
    checks_db: str,
) -> None:
    def build(session: Any) -> None:
        seed_snapshot(session)
        seed_clean_account_run(session, completed_at=at(10))
        seed_clean_account_run(session, completed_at=at(20), status="failed", blocks=True)

    _arrange(build)

    result = _evaluate()

    assert _reason(result, CheckId.A6) is CheckReason.RECONCILIATION_NOT_CLEAN
    assert _reason(result, CheckId.A2) is CheckReason.NO_ACCOUNT_RECONCILIATION
    first = result.first_failed()
    assert first is not None and first.id is CheckId.A6


@pytest.mark.parametrize(
    "kwargs",
    [
        {"non_terminal": None},
        {"unrecognized_orders": None, "unrecognized_fills": None},
    ],
)
def test_runs_missing_the_evidence_fields_fail_closed(
    checks_db: str, kwargs: dict[str, Any]
) -> None:
    def build(session: Any) -> None:
        seed_snapshot(session)
        seed_clean_account_run(session, completed_at=at(10), **kwargs)

    _arrange(build)

    result = _evaluate()

    failing = set(result.failed_ids())
    assert failing & {CheckId.A2, CheckId.A4}
    assert result.get(CheckId.A6).passed is True  # type: ignore[union-attr]
    first = result.first_failed()
    assert first is not None and first.reason_code is CheckReason.NO_ACCOUNT_RECONCILIATION


def test_a3_reads_only_broker_observed_snapshots(checks_db: str) -> None:
    def build(session: Any) -> None:
        seed_clean_account_run(session, completed_at=at(10))
        seed_snapshot(session, source="risk_evaluation", open_positions=0)

    _arrange(build)
    assert _reason(_evaluate(), CheckId.A3) is CheckReason.NO_BROKER_OBSERVED_SNAPSHOT

    # The NEWEST broker-observed snapshot decides.
    def newer(session: Any) -> None:
        seed_snapshot(session, snapshot_at=at(1), open_positions=2)

    _arrange(newer)
    assert _reason(_evaluate(), CheckId.A3) is CheckReason.OPEN_POSITIONS


# ---------------------------------------------------------------------------
# A5 and A6
# ---------------------------------------------------------------------------


def test_29_sep_fixture_fails_a5_and_a6_then_passes_after_fresh_clean_account_reconciliation(
    checks_db: str,
) -> None:
    """Two uncertain paper-session Jobs and one uncertain broker-order-sync Job, none with
    a linked paper_execution run (the 29 Sep shape)."""

    def build(session: Any) -> None:
        seed_snapshot(session, snapshot_at=at(0))
        # An account run that PREDATES the Jobs does not count.
        seed_clean_account_run(session, completed_at=at(-10))
        seed_job(session, job_type="paper-session", completed_at=at(1))
        seed_job(session, job_type="paper-session", completed_at=at(2))
        seed_job(session, job_type="broker-order-sync", completed_at=at(3))

    _arrange(build)

    before = _evaluate()
    assert _reason(before, CheckId.A5) is CheckReason.UNRESOLVED_OUTCOME
    assert _reason(before, CheckId.A6) is CheckReason.RECONCILIATION_STALE
    first = before.first_failed()
    assert first is not None and first.id is CheckId.A5
    a5 = before.get(CheckId.A5)
    assert a5 is not None
    assert sum(1 for ref in a5.evidence_refs if ref.kind is EvidenceKind.JOB) == 3

    _arrange(lambda s: seed_clean_account_run(s, completed_at=at(30)))

    after = _evaluate()
    assert after.all_passed and after.first_failed() is None


def arrange_not_received_world(monkeypatch: pytest.MonkeyPatch) -> tuple[LookupBroker, datetime]:
    """Only A5 fails: the operation ended, the outgoing owner is disabled, the account is
    flat, a fresh clean account reconciliation exists, and one never-found in-doubt intent
    carries a recorded not_received statement and complete absence evidence.

    Returns the (never-posting) broker and the patched clock instant.
    """

    now = {"value": at(2)}
    monkeypatch.setattr(clock, "now_utc", lambda: now["value"])

    def build(session: Any) -> Any:
        _, _, order = seed_uncertain_session(session, completed_at=at(0))
        operation = seed_operation(session, state="terminated", reason="cancelled_by_operator")
        operation.ended_by = "op"
        strategy_row(session, OWNER).status = StrategyStatus.DISABLED
        set_active_paper_strategy(session, OWNER)
        return order.id

    order_id = _arrange(build)
    broker = LookupBroker()
    sync_account_state(settings=load_settings(), broker_client=broker)
    now["value"] = at(2) + timedelta(
        seconds=load_settings().execution.recovery_absence_grace_seconds + 1
    )
    sync_account_state(settings=load_settings(), broker_client=broker)
    with session_scope(load_settings()) as session:
        record_broker_statement(
            session, order_id, "not_received", "ticket-1", "broker support: not received", "op"
        )
    now["value"] = at(2) + timedelta(hours=2)

    def quiet_after(session: Any) -> None:
        seed_snapshot(session, snapshot_at=at(60))
        seed_clean_account_run(session, completed_at=at(90))

    _arrange(quiet_after)
    return broker, now["value"]


def test_a5_not_received_statement_does_not_unblock_handover(
    checks_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker, now = arrange_not_received_world(monkeypatch)

    result = _evaluate(include_handover=True, now=now)

    assert result.failed_ids() == (CheckId.A5,)
    first = result.first_failed()
    assert first is not None
    assert (first.id, first.reason_code) == (CheckId.A5, CheckReason.UNRESOLVED_OUTCOME)
    assert broker.post_count == 0


def test_a6_stale_when_a_recording_or_a_job_is_newer_than_the_run(checks_db: str) -> None:
    _arrange(_quiet)
    assert _evaluate().get(CheckId.A6).passed is True  # type: ignore[union-attr]

    _arrange(lambda s: seed_recording(s, created_at=at(15)))
    stale = _evaluate()
    assert _reason(stale, CheckId.A6) is CheckReason.RECONCILIATION_STALE

    _arrange(lambda s: seed_clean_account_run(s, completed_at=at(16)))
    assert _evaluate().get(CheckId.A6).passed is True  # type: ignore[union-attr]

    _arrange(
        lambda s: seed_job(
            s,
            job_type="broker-order-sync",
            uncertain=False,
            status=JobStatus.SUCCEEDED,
            completed_at=at(17),
        )
    )
    assert _reason(_evaluate(), CheckId.A6) is CheckReason.RECONCILIATION_STALE


def test_a6_unresolved_reasons_make_a_run_not_clean(checks_db: str) -> None:
    def build(session: Any) -> None:
        seed_snapshot(session)
        seed_clean_account_run(
            session, completed_at=at(10), unresolved_reasons=["broker_history_exceeds_cap"]
        )

    _arrange(build)

    assert _reason(_evaluate(), CheckId.A6) is CheckReason.RECONCILIATION_NOT_CLEAN


# ---------------------------------------------------------------------------
# Purity, bounds, imports
# ---------------------------------------------------------------------------


def _row_counts() -> tuple[int, ...]:
    with session_scope(load_settings()) as session:
        return tuple(
            session.execute(select(func.count()).select_from(model)).scalar_one()
            for model in (
                Job,
                StrategyRun,
                ExecutionEvent,
                ExecutionOperation,
                AccountReconciliationRun,
            )
        )


def test_evaluation_is_read_only(checks_db: str) -> None:
    def build(session: Any) -> None:
        _quiet(session)
        operation = seed_operation(session, state="paused", reason="awaiting_reconciliation")
        seed_operation_intent(session, operation)
        seed_uncertain_session(session, completed_at=at(-5))
        strategy_row(session, OTHER, enabled=True)
        set_active_paper_strategy(session, OTHER)

    _arrange(build)
    before = _row_counts()
    engine = get_engine(load_settings())

    with session_scope(load_settings()) as session:
        with count_queries(engine) as counter:
            evaluate_account_checks(session, now=IN_WINDOW, include_handover=True)

    assert counter.statements
    # A SELECT or a read-only CTE (the recovery predicate's single-statement fact loaders).
    leading = [statement.lstrip().upper() for statement in counter.statements]
    assert all(text.startswith(("SELECT", "WITH")) for text in leading), leading
    assert not any(
        keyword in text for text in leading for keyword in ("INSERT ", "UPDATE ", "DELETE ")
    )
    assert _row_counts() == before


def _statement_count(include_handover: bool) -> int:
    engine = get_engine(load_settings())
    with session_scope(load_settings()) as session:
        with count_queries(engine) as counter:
            evaluate_account_checks(session, now=IN_WINDOW, include_handover=include_handover)
    return counter.count


def test_statement_count_is_independent_of_history_size(checks_db: str) -> None:
    def small(session: Any) -> None:
        _quiet(session)
        operation = seed_operation(session, state="paused", reason="awaiting_reconciliation")
        seed_operation_intent(session, operation)
        strategy_row(session, OTHER, enabled=False)
        set_active_paper_strategy(session, OTHER)

    _arrange(small)
    baseline = _statement_count(True)

    def history(session: Any) -> None:
        for minute in range(30):
            seed_clean_account_run(session, completed_at=at(-100 - minute))
            seed_snapshot(session, snapshot_at=at(-100 - minute))
            seed_job(
                session,
                job_type="paper-session" if minute % 2 else "broker-order-sync",
                uncertain=False,
                status=JobStatus.SUCCEEDED,
                completed_at=at(-200 - minute),
            )
            seed_job(
                session,
                job_type="reconciliation",
                uncertain=False,
                status=JobStatus.FAILED,
                completed_at=at(-300 - minute),
            )

    _arrange(history)
    assert _statement_count(True) == baseline


def test_module_imports_no_broker_client() -> None:
    source = inspect.getsource(checks_module)
    assert "alpaca" not in source.lower()
    assert "load_broker_state" not in source
    path = Path(checks_module.__file__)
    assert path.read_text().lower().count("alpaca") == 0
