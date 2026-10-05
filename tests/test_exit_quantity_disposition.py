"""SAF-08 / S3-R4(5): an exit is exactly the whole verified position, refused at intent creation."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from tests.test_paper_execution import migrated_paper_db  # noqa: F401  (database fixture)
from tests.test_paper_session_operations import (
    DEFAULT_BATCH,
    ScriptedExecutionService,
    _seams,  # noqa: F401  (autouse submit-time seams)
    _start,
    dispositions,
    end_all_open_operations,
    evaluation,
    put_position,
    settle,
)

from trading_platform.core.settings import load_settings
from trading_platform.db.models import OrderLifecycleState, PaperOrder
from trading_platform.db.session import session_scope
from trading_platform.services.execution.intent_identity import (
    CandidateDisposition,
    CandidateKey,
    classify_candidate,
)

SESSION = date(2024, 1, 5)


def _classify(side: str, quantity: str, held: str | None) -> CandidateDisposition | None:
    verdict = classify_candidate(
        CandidateKey(symbol="AAPL", side=side, session_date=SESSION, quantity=Decimal(quantity)),
        "fingerprint",
        orders=[],
        earlier_intents=[],
        basis_positions=None if held is None else {"AAPL": Decimal(held)},
    )
    return verdict.disposition


def test_sell_smaller_than_the_held_position_is_refused() -> None:
    assert _classify("sell", "9", "10") is CandidateDisposition.EXIT_QUANTITY_MISMATCH


def test_sell_larger_than_the_held_position_is_refused() -> None:
    assert _classify("sell", "11", "10") is CandidateDisposition.EXIT_QUANTITY_MISMATCH


def test_sell_of_exactly_the_held_position_is_eligible() -> None:
    assert _classify("sell", "10", "10") is None
    assert _classify("sell", "10.000000", "10") is None  # Decimal comparison, not text


def test_sell_with_no_held_position_keeps_the_no_open_position_precedence() -> None:
    assert _classify("sell", "10", "0") is CandidateDisposition.NO_OPEN_POSITION
    assert _classify("sell", "10", None) is None  # hand-seeded run: step (5) skipped


def test_buy_with_a_held_position_keeps_the_duplicate_open_position_disposition() -> None:
    assert _classify("buy", "10", "10") is CandidateDisposition.DUPLICATE_OPEN_POSITION
    assert _classify("buy", "10", "0") is None


def test_hand_seeded_run_without_a_basis_is_unchanged() -> None:
    assert _classify("sell", "9", None) is None
    assert _classify("buy", "9", None) is None


def _sell_count() -> int:
    with session_scope(load_settings()) as session:
        return session.execute(
            select(func.count()).select_from(PaperOrder).where(PaperOrder.side == "sell")
        ).scalar_one()


def _held_ten_then_exit_run(exit_quantity: str):
    buy_session, exit_session = date(2024, 1, 4), SESSION
    buy_run = evaluation(DEFAULT_BATCH[:1], session_date=buy_session, verified=False)
    _start(ScriptedExecutionService(["accept"]), risk_run_id=buy_run, session_date=buy_session)
    settle("AAPL", OrderLifecycleState.FILLED, fills="10")
    put_position("AAPL", "10")
    end_all_open_operations()
    return evaluation(
        [("AAPL", exit_quantity, "120")],
        session_date=exit_session,
        side="exit",
        positions=[("AAPL", "10")],
    )


@pytest.mark.parametrize("exit_quantity", ["9", "11"])
def test_start_refuses_partial_exit_candidate_at_creation(
    migrated_paper_db: str,
    exit_quantity: str,  # noqa: F811
) -> None:
    exit_run = _held_ten_then_exit_run(exit_quantity)
    service = ScriptedExecutionService(["accept"])

    report = _start(service, risk_run_id=exit_run)

    assert dispositions(report) == [("AAPL", "sell", "exit_quantity_mismatch")]
    assert service.post_attempts == 0
    assert service.submitted_intents == []
    assert _sell_count() == 0  # no intent was registered


def test_start_sends_the_whole_position_exit(migrated_paper_db: str) -> None:  # noqa: F811
    exit_run = _held_ten_then_exit_run("10")
    service = ScriptedExecutionService(["accept"])

    report = _start(service, risk_run_id=exit_run)

    assert dispositions(report) == []
    assert [(i.symbol, i.side.value, i.quantity) for i in service.submitted_intents] == [
        ("AAPL", "sell", Decimal("10.000000"))
    ]
