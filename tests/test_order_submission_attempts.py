"""COR-06 attempt log: classification table, persistence and intent-state derivation."""

from __future__ import annotations

import json
import re
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.support.migrated_db import migrated_database
from tests.support.submission_attempts import seed_attempt_row, seed_paper_order_row

from trading_platform.core.settings import Settings, load_settings
from trading_platform.db.models import (
    AttemptOutcomeClass,
    OrderLifecycleState,
    PaperOrder,
)
from trading_platform.db.session import session_scope
from trading_platform.services.execution import attempts as attempts_module
from trading_platform.services.execution.attempts import (
    AttemptAlreadyCompletedError,
    AttemptRecord,
    DbSubmissionAttemptLog,
    SubmissionClass,
    SubmissionIntentState,
    bind_attempt_log,
    classify_attempt_exception,
    classify_http_response,
    classify_submission,
    current_attempt_log,
    derive_intent_state,
    load_submission_attempts,
)

A = AttemptOutcomeClass
PRE, DEAD, AMB = A.PRE_CONNECTION, A.DEADLINE_EXPIRED, A.AMBIGUOUS
DUP, REJ, ACC = A.DUPLICATE_REPORTED, A.REJECTED, A.ACCEPTED


def _records(*outcomes: AttemptOutcomeClass | None) -> list[AttemptRecord]:
    return [
        AttemptRecord(attempt_number=i, outcome_class=outcome)
        for i, outcome in enumerate(outcomes, start=1)
    ]


# --- closedness -------------------------------------------------------------


def test_enum_member_sets_are_exact() -> None:
    assert {m.value for m in AttemptOutcomeClass} == {
        "pre_connection",
        "deadline_expired",
        "ambiguous",
        "duplicate_reported",
        "rejected",
        "accepted",
    }
    assert len(AttemptOutcomeClass) == 6
    assert {m.value for m in SubmissionClass} == {
        "not_sent",
        "ambiguous",
        "exists_reported",
        "rejected",
        "accepted",
    }
    assert {m.value for m in SubmissionIntentState} == {
        "planned",
        "registered_unsent",
        "not_sent",
        "submitted",
        "ambiguous",
        "rejected",
    }


# --- classification (pure) --------------------------------------------------


def test_empty_history_has_no_class() -> None:
    assert classify_submission([]) is None


@pytest.mark.parametrize(
    ("outcomes", "expected"),
    [
        ([AMB], SubmissionClass.AMBIGUOUS),
        ([AMB, PRE], SubmissionClass.AMBIGUOUS),
        ([PRE, AMB], SubmissionClass.AMBIGUOUS),
        ([None], SubmissionClass.AMBIGUOUS),
        ([PRE, None], SubmissionClass.AMBIGUOUS),
        ([None, PRE], SubmissionClass.AMBIGUOUS),
        ([PRE, PRE, ACC], SubmissionClass.ACCEPTED),
        ([ACC], SubmissionClass.ACCEPTED),
        ([PRE, REJ], SubmissionClass.REJECTED),
        ([REJ], SubmissionClass.REJECTED),
        ([DUP], SubmissionClass.EXISTS_REPORTED),
        ([PRE, DUP], SubmissionClass.EXISTS_REPORTED),
        ([DUP, AMB], SubmissionClass.AMBIGUOUS),
        ([PRE], SubmissionClass.NOT_SENT),
        ([PRE, PRE, PRE], SubmissionClass.NOT_SENT),
        ([DEAD], SubmissionClass.NOT_SENT),
        ([PRE, DEAD], SubmissionClass.NOT_SENT),
        ([DEAD, PRE], SubmissionClass.NOT_SENT),
        ([AMB, DEAD], SubmissionClass.AMBIGUOUS),
        ([DEAD, AMB], SubmissionClass.AMBIGUOUS),
        ([None, DEAD], SubmissionClass.AMBIGUOUS),
        ([AMB, REJ], SubmissionClass.AMBIGUOUS),
    ],
)
def test_submission_class_precedence(
    outcomes: list[AttemptOutcomeClass | None], expected: SubmissionClass
) -> None:
    assert classify_submission(_records(*outcomes)) == expected


def test_deadline_expired_alone_is_not_sent() -> None:
    assert classify_submission(_records(DEAD)) == SubmissionClass.NOT_SENT
    assert classify_submission(_records(PRE, DEAD)) == SubmissionClass.NOT_SENT


def test_deadline_expired_never_erases_earlier_ambiguous() -> None:
    assert classify_submission(_records(AMB, DEAD)) == SubmissionClass.AMBIGUOUS


def test_deadline_expired_never_erases_incomplete_attempt() -> None:
    assert classify_submission(_records(None, DEAD)) == SubmissionClass.AMBIGUOUS


@pytest.mark.parametrize(
    ("status", "message", "expected"),
    [
        (200, None, ACC),
        (201, None, ACC),
        (202, "", ACC),
        (422, "client_order_id must be unique", DUP),
        (422, '{"code":40010001,"message":"client_order_id must be unique"}', DUP),
        (422, "page_size must be at most 100 (code 40010001)", REJ),
        (400, "bad", REJ),
        (401, "unauthorized", REJ),
        (403, "insufficient buying power", REJ),
        (404, "nope", REJ),
        (429, "slow down", AMB),
        (500, "boom", AMB),
        (503, "down", AMB),
        # The duplicate message is only meaningful on HTTP 422.
        (400, "client_order_id must be unique", REJ),
        (500, "client_order_id must be unique", AMB),
    ],
)
def test_classify_http_response(status: int, message: str | None, expected: A) -> None:
    assert classify_http_response(status, message) == expected


def _request() -> httpx.Request:
    return httpx.Request("POST", "https://paper-api.alpaca.markets/v2/orders")


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (httpx.ConnectError("refused", request=_request()), PRE),
        (httpx.ConnectTimeout("t", request=_request()), PRE),
        (httpx.PoolTimeout("t", request=_request()), PRE),
        (httpx.ReadTimeout("t", request=_request()), AMB),
        (httpx.WriteTimeout("t", request=_request()), AMB),
        (httpx.ReadError("r", request=_request()), AMB),
        (httpx.RemoteProtocolError("p", request=_request()), AMB),
        (RuntimeError("anything else"), AMB),
        (ValueError("bad body"), AMB),
    ],
)
def test_classify_attempt_exception(exc: BaseException, expected: A) -> None:
    assert classify_attempt_exception(exc) == expected


# --- intent state truth table ------------------------------------------------


def _order(
    status: OrderLifecycleState = OrderLifecycleState.PENDING_SUBMISSION,
    broker_order_id: str | None = None,
) -> PaperOrder:
    return PaperOrder(status=status, broker_order_id=broker_order_id)


@pytest.mark.parametrize(
    ("order", "outcomes", "expected"),
    [
        (None, [], SubmissionIntentState.PLANNED),
        (_order(), [], SubmissionIntentState.REGISTERED_UNSENT),
        (_order(OrderLifecycleState.SUBMISSION_FAILED), [], SubmissionIntentState.REGISTERED_UNSENT),
        (_order(), [PRE], SubmissionIntentState.NOT_SENT),
        (_order(OrderLifecycleState.SUBMISSION_FAILED), [PRE, PRE], SubmissionIntentState.NOT_SENT),
        (_order(), [DEAD], SubmissionIntentState.NOT_SENT),
        (_order(), [AMB, DEAD], SubmissionIntentState.AMBIGUOUS),
        (_order(), [None, DEAD], SubmissionIntentState.AMBIGUOUS),
        (_order(), [AMB, PRE], SubmissionIntentState.AMBIGUOUS),
        (_order(), [None], SubmissionIntentState.AMBIGUOUS),
        (_order(OrderLifecycleState.UNKNOWN), [AMB], SubmissionIntentState.AMBIGUOUS),
        (_order(OrderLifecycleState.UNKNOWN), [], SubmissionIntentState.AMBIGUOUS),
        (_order(), [DUP], SubmissionIntentState.AMBIGUOUS),
        (_order(), [REJ], SubmissionIntentState.REJECTED),
        (_order(OrderLifecycleState.REJECTED), [REJ], SubmissionIntentState.REJECTED),
        (_order(OrderLifecycleState.REJECTED), [], SubmissionIntentState.REJECTED),
        (_order(), [ACC], SubmissionIntentState.SUBMITTED),
        (_order(OrderLifecycleState.SUBMITTED, "b-1"), [PRE, ACC], SubmissionIntentState.SUBMITTED),
        (_order(OrderLifecycleState.FILLED, "b-1"), [], SubmissionIntentState.SUBMITTED),
        (_order(OrderLifecycleState.CANCELED, "b-1"), [AMB], SubmissionIntentState.SUBMITTED),
        (_order(OrderLifecycleState.UNKNOWN, "b-1"), [AMB], SubmissionIntentState.SUBMITTED),
    ],
)
def test_derive_intent_state_truth_table(
    order: PaperOrder | None,
    outcomes: list[AttemptOutcomeClass | None],
    expected: SubmissionIntentState,
) -> None:
    assert derive_intent_state(order, _records(*outcomes)) == expected


def test_deadline_expired_through_derive_intent_state() -> None:
    assert derive_intent_state(_order(), _records(DEAD)) == SubmissionIntentState.NOT_SENT
    assert derive_intent_state(_order(), _records(AMB, DEAD)) == SubmissionIntentState.AMBIGUOUS
    assert derive_intent_state(_order(), _records(None, DEAD)) == SubmissionIntentState.AMBIGUOUS


# --- bind / source scan -------------------------------------------------------


def test_bind_attempt_log_is_scoped() -> None:
    sentinel = attempts_module.NullSubmissionAttemptLog()
    assert current_attempt_log() is None
    with bind_attempt_log(sentinel):
        assert current_attempt_log() is sentinel
    assert current_attempt_log() is None


def test_attempts_module_has_no_delete_or_started_at_update() -> None:
    source = Path(attempts_module.__file__).read_text()
    assert "delete(" not in source
    assert not re.search(r"(?<!with_for_)update\(", source)
    assert not re.search(r"\.started_at\s*=[^=]", source)
    assert "values(started_at" not in source
    assert "services.alpaca" not in source


# --- persistence (database) ----------------------------------------------------


@pytest.fixture()
def db(monkeypatch: pytest.MonkeyPatch) -> Iterator[Settings]:
    with migrated_database(monkeypatch, "attempts_log"):
        yield load_settings()


def _log(settings: Settings, order_id: uuid.UUID, run_id: uuid.UUID) -> DbSubmissionAttemptLog:
    return DbSubmissionAttemptLog(settings, paper_order_id=order_id, strategy_run_id=run_id)


def _seed(settings: Settings) -> tuple[uuid.UUID, uuid.UUID]:
    with session_scope(settings) as session:
        return seed_paper_order_row(session)


def test_begin_attempt_commits_before_returning_and_is_visible_elsewhere(db: Settings) -> None:
    order_id, run_id = _seed(db)
    log = _log(db, order_id, run_id)
    number = log.begin_attempt()
    assert number == 1
    # Visible from an independent session before any outcome exists.
    with session_scope(db) as session:
        rows = load_submission_attempts(session, order_id)
    assert [(r.attempt_number, r.outcome_class, r.completed_at) for r in rows] == [(1, None, None)]
    assert classify_submission(rows) == SubmissionClass.AMBIGUOUS


def test_sequence_spans_two_sessions(db: Settings) -> None:
    order_id, run_id = _seed(db)
    first = _log(db, order_id, run_id)
    n1 = first.begin_attempt()
    first.complete_attempt(n1, outcome_class=PRE, error_type="ConnectError")
    second = _log(db, order_id, run_id)  # a later session
    assert [r.attempt_number for r in second.existing_attempts()] == [1]
    n2 = second.begin_attempt()
    assert (n1, n2) == (1, 2)


def test_complete_attempt_writes_the_outcome_exactly_once(db: Settings) -> None:
    order_id, run_id = _seed(db)
    log = _log(db, order_id, run_id)
    number = log.begin_attempt()
    log.complete_attempt(number, outcome_class=REJ, http_status=403, broker_message="x" * 900)
    with pytest.raises(AttemptAlreadyCompletedError):
        log.complete_attempt(number, outcome_class=ACC)
    row = log.existing_attempts()[0]
    assert row.outcome_class == REJ
    assert row.http_status == 403
    assert row.broker_message == "x" * 500
    assert row.completed_at is not None


def test_complete_unknown_attempt_raises(db: Settings) -> None:
    order_id, run_id = _seed(db)
    with pytest.raises(LookupError):
        _log(db, order_id, run_id).complete_attempt(7, outcome_class=ACC)


def test_begin_attempt_inside_an_outer_session_commits_with_it(db: Settings) -> None:
    order_id, run_id = _seed(db)
    log = _log(db, order_id, run_id)
    with session_scope(db) as outer:
        assert log.begin_attempt(outer) == 1
        # not committed yet: an independent read sees nothing
        with session_scope(db) as other:
            assert load_submission_attempts(other, order_id) == []
    assert [r.attempt_number for r in log.existing_attempts()] == [1]


def test_intent_registered_at_reads_the_order_row(db: Settings) -> None:
    order_id, run_id = _seed(db)
    registered_at = _log(db, order_id, run_id).intent_registered_at()
    assert registered_at is not None


def test_seed_attempt_row_helper_round_trips(db: Settings) -> None:
    order_id, run_id = _seed(db)
    seed_attempt_row(db, order_id, number=1, outcome=None, strategy_run_id=run_id)
    seed_attempt_row(db, order_id, number=2, outcome=PRE)
    with session_scope(db) as session:
        records = load_submission_attempts(session, order_id)
    assert [r.outcome_class for r in records] == [None, PRE]


# --- DB-backed client loop: numbering across sessions and derived intent states --------


def _real_client(handler):
    from trading_platform.core.settings import AlpacaBrokerSettings
    from trading_platform.services.alpaca import AlpacaClient

    http_client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://paper-api.alpaca.markets"
    )
    return AlpacaClient(
        AlpacaBrokerSettings(
            api_key="test-key",
            api_secret="test-secret",
            max_retries=2,
            retry_backoff_factor=0.0,
        ),
        http_client=http_client,
    )


def _send(settings: Settings, order_id: uuid.UUID, run_id: uuid.UUID, handler):
    from datetime import date
    from decimal import Decimal

    from trading_platform.services.execution import OrderIntent, OrderSide

    with session_scope(settings) as session:
        client_order_id = session.get(PaperOrder, order_id).client_order_id
    intent = OrderIntent(
        strategy_id="s",
        symbol="AAPL",
        side=OrderSide.BUY,
        quantity=Decimal("1"),
        intended_session=date(2024, 1, 5),
        client_order_id=client_order_id,
    )
    log = _log(settings, order_id, run_id)
    with bind_attempt_log(log):
        return _real_client(handler).submit_order(intent)


def _state(settings: Settings, order_id: uuid.UUID) -> SubmissionIntentState:
    with session_scope(settings) as session:
        order = session.get(PaperOrder, order_id)
        return derive_intent_state(order, load_submission_attempts(session, order_id))


def test_client_loop_persists_attempts_and_derives_intent_states(
    db: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trading_platform.services.alpaca import (
        AmbiguousOrderSubmissionError,
        OrderNotSentError,
        OrderRejectedError,
    )

    monkeypatch.setattr("trading_platform.services.alpaca.time.sleep", lambda *_: None)

    def connect_error(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    def read_timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    def bad_request(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"message": "bad"})

    def created(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        return httpx.Response(
            201,
            json={
                "id": "b-1",
                "client_order_id": body["client_order_id"],
                "symbol": "AAPL",
                "side": "buy",
                "qty": "1",
                "type": "market",
                "status": "new",
            },
        )

    # fresh intent: no attempts yet
    fresh, fresh_run = _seed(db)
    assert _state(db, fresh) == SubmissionIntentState.REGISTERED_UNSENT

    # exhausted connect errors -> not_sent; a later session continues the numbering
    order_id, run_id = _seed(db)
    with pytest.raises(OrderNotSentError):
        _send(db, order_id, run_id, connect_error)
    assert _state(db, order_id) == SubmissionIntentState.NOT_SENT
    with session_scope(db) as session:
        assert [a.attempt_number for a in load_submission_attempts(session, order_id)] == [1, 2, 3]
    _send(db, order_id, run_id, created)  # a later session retries: history is proven not sent
    with session_scope(db) as session:
        attempts = load_submission_attempts(session, order_id)
    assert [(a.attempt_number, a.outcome_class) for a in attempts] == [
        (1, PRE),
        (2, PRE),
        (3, PRE),
        (4, ACC),
    ]
    assert _state(db, order_id) == SubmissionIntentState.SUBMITTED

    ambiguous_id, ambiguous_run = _seed(db)
    with pytest.raises(AmbiguousOrderSubmissionError):
        _send(db, ambiguous_id, ambiguous_run, read_timeout)
    assert _state(db, ambiguous_id) == SubmissionIntentState.AMBIGUOUS
    # a later session over the same ambiguous history sends nothing
    with pytest.raises(AmbiguousOrderSubmissionError):
        _send(db, ambiguous_id, ambiguous_run, created)
    with session_scope(db) as session:
        assert len(load_submission_attempts(session, ambiguous_id)) == 1

    rejected_id, rejected_run = _seed(db)
    with pytest.raises(OrderRejectedError):
        _send(db, rejected_id, rejected_run, bad_request)
    assert _state(db, rejected_id) == SubmissionIntentState.REJECTED

    accepted_id, accepted_run = _seed(db)
    _send(db, accepted_id, accepted_run, created)
    assert _state(db, accepted_id) == SubmissionIntentState.SUBMITTED
