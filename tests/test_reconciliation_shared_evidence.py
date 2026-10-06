"""G-1 service-level matrix over the three reconciliation passes (20.1-35).

The rule under test is the one the user decided on 2026-10-06 (D-G1-A): an unmatched pre-send local
order (``pending_submission`` / ``submission_failed``) is reported MISSING_BROKER unless the shared
submission verdict (``attempts.classify_submission_evidence`` over the COMPLETE attempt history,
carried since 20.1-32 as ``LocalOrderSnapshot.submission_evidence``) is PROVEN_NOT_SENT or a
definitive REJECTED. UNESTABLISHED (legacy orders, unfinished, ambiguous and accepted-without-id
histories, and every history where an unfinished, ambiguous or duplicate_reported attempt comes
before OR after a rejection: ambiguity dominates) and BROKER_EVIDENCE keep the finding, and the
finding names the verdict. The attempt counter may only ADD blocking, never suppress a finding.

The three passes are the three real call shapes: the standalone strategy-scope Job
(``reconcile_paper_execution`` with ``trigger_source="job"``), the in-session pass of
``run_paper_session`` (the same function with ``trigger_source="<x>_reconciliation"``; it has no
code of its own) and the standalone account scope (``reconcile_account``).

Order shapes are INSERT-seeded through the shared ``SHAPES`` table of 20.1-32 (a legacy or odd
attempt history cannot be produced by the current product path, so the ORDER is arranged by hand);
every reconciliation RESULT comes from the real service against a scripted read-side broker. No
reconciliation row is seeded, nothing is POSTed and no network or broker service is contacted.

One migrated database is shared by the whole module. Every test seeds ITS OWN order(s) on a fresh
run (``risk_events`` is unique per run / symbol / session / direction), runs the pass and filters
every finding by that order id (or by its own unique broker order id); nothing here asserts a
report-level or gate-level value that an earlier test already satisfies, so any node passes alone,
in any order and as part of the whole module.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy.orm import Session
from tests.support.migrated_db import migrated_database
from tests.support.real_reconciliation import scripted_read_broker
from tests.support.recovery_fixtures import (
    OTHER,
    OWNER,
    SESSION_DATE,
    seed_intent,
    seed_paper_run,
    strategy_row,
)
from tests.test_paper_execution import FakeBrokerClient
from tests.test_reconciliation_evidence_input import SHAPES, ShapeSpec

from trading_platform.core import settings as settings_module
from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.db.models import (
    AttemptOutcomeClass,
    OrderLifecycleState,
    PaperOrder,
    StrategyRun,
    Symbol,
)
from trading_platform.db.session import session_scope
from trading_platform.services.execution.attempts import SubmissionEvidence
from trading_platform.services.execution.intent_identity import load_strategy_order_facts
from trading_platform.services.reconciliation import (
    AccountReconciliationReport,
    ReconciliationReport,
    apply_reconciliation_corrections,
    load_broker_state,
    reconcile_account,
    reconcile_paper_execution,
)
from trading_platform.services.recovery import strategy_recovery_status

#: The ONLY verdicts that explain why the broker does not report a pre-send order (D-G1-A).
EXPLAINED = frozenset({SubmissionEvidence.PROVEN_NOT_SENT, SubmissionEvidence.REJECTED})

#: The three call shapes of a reconciliation (see the module docstring).
PASSES = ("strategy_job", "in_session", "account")

#: The control shape: an UNKNOWN order with an ambiguous attempt. The shared verdict would NOT
#: explain it (UNESTABLISHED), yet reconciliation reports nothing for it because ``unknown`` is not
#: a pre-send status (unchanged behaviour); the recovery gate is what keeps blocking it.
CONTROL = "unknown_ambiguous"

Report = ReconciliationReport | AccountReconciliationReport


def _build_control(session: Session, run: StrategyRun) -> PaperOrder:
    return seed_intent(
        session,
        run,
        status=OrderLifecycleState.UNKNOWN,
        attempts=(AttemptOutcomeClass.AMBIGUOUS,),
    )


#: The 24 shared pre-send shapes plus the local control (25 ids; 3 passes -> 75 matrix nodes).
MATRIX: dict[str, ShapeSpec] = {
    **SHAPES,
    CONTROL: ShapeSpec(_build_control, SubmissionEvidence.UNESTABLISHED),
}


@pytest.fixture(scope="module")
def shared_db() -> Iterator[str]:
    patcher = pytest.MonkeyPatch()
    try:
        # A module-scoped fixture is built BEFORE the function-scoped autouse
        # ``isolate_operator_env`` of tests/conftest.py, so keep the operator's real dotenv file
        # out of the settings this fixture loads as well.
        patcher.setitem(settings_module.EnvironmentOverrides.model_config, "env_file", None)
        clear_settings_cache()
        with migrated_database(patcher, "recon_shared_evidence") as name:
            with session_scope(load_settings()) as session:
                strategy_row(session, OWNER)
                strategy_row(session, OTHER)
                session.add(Symbol(ticker="AAPL", active=True))
            yield name
    finally:
        patcher.undo()
        clear_settings_cache()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed(shape_id: str, *, strategy_id: str = OWNER) -> uuid.UUID:
    """INSERT one shape on its OWN fresh run, in its own transaction; return the order id.

    Only legacy shapes belong to another strategy: an operation-bound shape pins its operation to
    OWNER (``seed_operation_bound_intent`` defaults the operation to the owner)."""

    with session_scope(load_settings()) as session:
        run = seed_paper_run(session, None, strategy_id)
        return MATRIX[shape_id].build(session, run).id


def _run_pass(pass_name: str, broker: FakeBrokerClient, *, strategy_id: str = OWNER) -> Report:
    """One REAL reconciliation of the named pass against the scripted broker."""

    settings = load_settings()
    if pass_name == "strategy_job":
        return reconcile_paper_execution(
            strategy_id,
            as_of_session=SESSION_DATE,
            settings=settings,
            broker_client=broker,
            trigger_source="job",
        )
    if pass_name == "in_session":
        # The call shape run_paper_session uses: the broker state is read once and handed over, the
        # trigger source is '<x>_reconciliation'. There is no separate in-session code path.
        return reconcile_paper_execution(
            strategy_id,
            as_of_session=SESSION_DATE,
            settings=settings,
            broker_state=load_broker_state(settings=settings, broker_client=broker),
            trigger_source="paper_session_reconciliation",
        )
    if pass_name == "account":
        return reconcile_account(settings=settings, broker_client=broker, trigger_source="job")
    raise AssertionError(f"unknown reconciliation pass {pass_name!r}")


def _finding_dicts(report: Report) -> list[dict[str, Any]]:
    """The findings of either report shape as plain dicts (strategy: dataclass, account: dict)."""

    return [f if isinstance(f, dict) else f.to_dict() for f in report.findings]


def _named_findings(report: Report, order_id: uuid.UUID) -> list[dict[str, Any]]:
    return [f for f in _finding_dicts(report) if f["paper_order_id"] == str(order_id)]


def _order_findings(report: Report, order_id: uuid.UUID) -> list[str]:
    """The event types of the findings that name this order (both report shapes)."""

    return [f["event_type"] for f in _named_findings(report, order_id)]


def _expects_a_finding(shape_id: str) -> bool:
    """D-G1-A: reported exactly when the shared verdict does not explain the absence at the broker.
    The control (status ``unknown``) never produces a finding: the verdict is not consulted."""

    return shape_id != CONTROL and MATRIX[shape_id].expected not in EXPLAINED


# ---------------------------------------------------------------------------
# The matrix itself
# ---------------------------------------------------------------------------


def test_matrix_is_the_agreed_set() -> None:
    """The matrix cannot shrink silently: 24 shared shapes + the control, 3 passes, and the split
    of the decided rule (9 explained shapes, 15 reported ones, the control reports nothing)."""

    assert PASSES == ("strategy_job", "in_session", "account")
    assert len(SHAPES) == 24
    assert set(MATRIX) == set(SHAPES) | {CONTROL}
    explained = {shape_id for shape_id in SHAPES if SHAPES[shape_id].expected in EXPLAINED}
    reported = {shape_id for shape_id in MATRIX if _expects_a_finding(shape_id)}
    assert len(explained) == 9
    assert len(reported) == 15
    assert explained | reported | {CONTROL} == set(MATRIX)
    assert not _expects_a_finding(CONTROL)
    # PROVEN_NOT_SENT and a definitive REJECTED are explained ...
    assert {
        "op_bound_pending_count1_no_attempts",
        "pending_pre_connection",
        "pending_rejected",
        "failed_pre_connection_then_rejected",
        "pending_rejected_then_deadline_expired",
    } <= explained
    # ... and ambiguity dominates a rejection in BOTH directions; legacy, unfinished and broker
    # evidence shapes keep the finding.
    assert {
        "failed_ambiguous_then_rejected",
        "pending_unfinished_then_rejected",
        "pending_rejected_then_unfinished",
        "failed_duplicate_reported_then_rejected",
        "legacy_pending_count0",
        "legacy_pending_count1",
        "legacy_failed",
        "legacy_order_later_referenced",
        "pending_accepted_without_broker_id",
        "failed_with_broker_id",
    } <= reported


@pytest.mark.parametrize("shape_id", sorted(MATRIX))
@pytest.mark.parametrize("pass_name", PASSES)
def test_shape_matrix(shared_db: str, pass_name: str, shape_id: str) -> None:
    """Every shape in every pass, against a broker that holds no order at all."""

    order_id = _seed(shape_id)
    report = _run_pass(pass_name, scripted_read_broker())

    if not _expects_a_finding(shape_id):
        assert _order_findings(report, order_id) == []
        if shape_id == CONTROL:
            # Not explained and not reported: the recovery gate is what keeps blocking an UNKNOWN
            # order. Order-specific: the gate CODE is already set by earlier shapes of this
            # database, so look for THIS intent among the blocking ones.
            with session_scope(load_settings()) as session:
                status = strategy_recovery_status(session, OWNER)
                facts = {f.paper_order_id: f for f in load_strategy_order_facts(session, OWNER)}
            assert order_id in {intent.intent_id for intent in status.intents if intent.blocking}
            assert facts[order_id].submission_evidence not in EXPLAINED
        return

    findings = _named_findings(report, order_id)
    assert [finding["event_type"] for finding in findings] == ["MISSING_BROKER"]
    (finding,) = findings
    assert finding["details"]["submission_evidence"] == MATRIX[shape_id].expected.value
    # The finding ITSELF blocks (not merely the report it sits in).
    assert finding["blocks_execution"] is True
    assert finding["severity"] == "error"


@pytest.mark.parametrize("shape_id", sorted(SHAPES))
def test_verdict_agrees_with_the_recovery_oracle(shared_db: str, shape_id: str) -> None:
    """The reconciliation finding is a pure function of the verdict the recovery gate reads."""

    order_id = _seed(shape_id)
    report = _run_pass("strategy_job", scripted_read_broker())
    with session_scope(load_settings()) as session:
        facts = {f.paper_order_id: f for f in load_strategy_order_facts(session, OWNER)}
    verdict = facts[order_id].submission_evidence
    assert verdict is SHAPES[shape_id].expected  # the oracle itself did not move

    findings = _named_findings(report, order_id)
    assert bool(findings) == (verdict not in EXPLAINED)
    if findings:
        assert [finding["event_type"] for finding in findings] == ["MISSING_BROKER"]
        assert findings[0]["details"]["submission_evidence"] == verdict.value


def test_in_session_corrections_do_not_count_an_explained_order(shared_db: str) -> None:
    """``run_paper_session`` follows its in-session pass with ``apply_reconciliation_corrections``,
    which counts a sync failure for every order a finding names. An explained order (proven not
    sent, or definitively rejected) has no finding, so its counter stays 0; a legacy pending order
    is reported and counted. Pinned to pending shapes: the decided rule is the only rule."""

    proven = _seed("op_bound_pending_count1_no_attempts")
    rejected = _seed("pending_rejected")
    legacy = _seed("legacy_pending_count1")
    report = _run_pass("in_session", scripted_read_broker())
    assert _order_findings(report, proven) == []
    assert _order_findings(report, rejected) == []
    assert _order_findings(report, legacy) == ["MISSING_BROKER"]

    apply_reconciliation_corrections(OWNER, report=report, settings=load_settings())

    with session_scope(load_settings()) as session:
        rows = {
            name: session.get(PaperOrder, order_id)
            for name, order_id in (("proven", proven), ("rejected", rejected), ("legacy", legacy))
        }
        assert all(row is not None for row in rows.values())
        counts = {name: row.sync_failure_count for name, row in rows.items() if row is not None}
        errors = {name: row.last_sync_error for name, row in rows.items() if row is not None}
        failed_at = {
            name: row.last_sync_failure_at for name, row in rows.items() if row is not None
        }
    assert counts == {"proven": 0, "rejected": 0, "legacy": 1}
    assert errors["proven"] is None and errors["rejected"] is None
    assert errors["legacy"] == _named_findings(report, legacy)[0]["message"]
    assert failed_at["proven"] is None and failed_at["rejected"] is None
    assert failed_at["legacy"] is not None
