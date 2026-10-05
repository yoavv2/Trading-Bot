"""SAF-06: a broker-accepted order whose 2xx reply cannot be parsed is an UNCERTAIN outcome.

The REAL Alpaca client and attempt log run behind ``httpx.MockTransport`` (no network, no
credentials): the broker answers 201 with a JSON object that parses but fails normalization
(``qty`` is not a number). The attempt outcome is recorded as accepted, then the client raises
``AmbiguousOrderSubmissionError(reason="invalid_response_body")``; the executor parks the order
UNKNOWN, pauses the operation ``outcome_unresolved`` and lets the exception propagate (the Job lands
failed with ``outcome_uncertain``). The intent is never re-POSTed.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import httpx
import pytest
from sqlalchemy import select
from tests.support.paper_eligibility import allow_paper_execution
from tests.support.paper_execution_seams import allow_direct_paper_execution
from tests.support.price_source import FreshPriceSource
from tests.test_paper_execution import migrated_paper_db  # noqa: F401  (database fixture)
from tests.test_paper_session_operations import (
    DEFAULT_BATCH,
    _continue_conflict,
    _start,
    attempt_outcomes,
    count,
    finish_jobs,
    intent_rows,
    intent_states,
    operation_row,
    seed_batch,
)

from trading_platform.core.settings import AlpacaBrokerSettings, load_settings
from trading_platform.db.models import OrderLifecycleState, PaperOrder
from trading_platform.db.session import session_scope
from trading_platform.services.alpaca import (
    AlpacaClient,
    AlpacaExecutionService,
    AmbiguousOrderSubmissionError,
)
from trading_platform.services.execution.attempts import SubmissionClass, SubmissionIntentState


@pytest.fixture(autouse=True)
def _seams(monkeypatch: pytest.MonkeyPatch) -> Iterator[FreshPriceSource]:
    allow_paper_execution(monkeypatch)
    yield allow_direct_paper_execution(monkeypatch)


def _service(posts: list[httpx.Request]) -> AlpacaExecutionService:
    """The real client; every POST is answered 201 with an unparseable ``qty``."""

    def broker(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST" and request.url.path == "/v2/orders"
        posts.append(request)
        return httpx.Response(
            201,
            json={
                "id": f"b-{uuid.uuid4().hex[:10]}",
                "client_order_id": "echoed-by-the-broker",
                "symbol": "AAPL",
                "side": "buy",
                "qty": "abc",  # a dict body that fails normalization
                "type": "market",
                "time_in_force": "day",
                "status": "new",
            },
        )

    client = AlpacaClient(
        AlpacaBrokerSettings(api_key="k", api_secret="s", max_retries=2, retry_backoff_factor=0.0),
        http_client=httpx.Client(
            transport=httpx.MockTransport(broker), base_url="https://paper-api.alpaca.markets"
        ),
    )
    return AlpacaExecutionService(load_settings().broker.alpaca, client=client)


def test_unparseable_accepted_reply_parks_unknown_and_never_resends(
    migrated_paper_db: str,  # noqa: F811
) -> None:
    seed_batch(DEFAULT_BATCH[:2])
    posts: list[httpx.Request] = []

    with pytest.raises(AmbiguousOrderSubmissionError) as excinfo:  # the Job fails uncertain
        _start(_service(posts))

    assert excinfo.value.reason == "invalid_response_body"
    assert excinfo.value.submission_class == SubmissionClass.ACCEPTED
    assert len(posts) == 1  # never re-sent, and the second intent was never reached
    operation = operation_row()
    assert (operation.state, operation.reason) == ("paused", "outcome_unresolved")
    with session_scope(load_settings()) as session:
        order = session.execute(select(PaperOrder)).scalar_one()
    assert order.status == OrderLifecycleState.UNKNOWN
    assert count(PaperOrder) == 1
    # the attempt outcome was recorded exactly once, as accepted (the broker DID answer 2xx)
    assert attempt_outcomes(order.client_order_id) == [(1, "accepted")]
    assert [state for _ticker, state in intent_states(operation.id)] == [
        SubmissionIntentState.AMBIGUOUS,
        SubmissionIntentState.PLANNED,
    ]
    # the recovery gate refuses a later Continue; the unresolved order blocks any re-send
    finish_jobs()
    assert _continue_conflict(operation.id).code == "outcome_unresolved"
    assert len(posts) == 1
    assert [cid for _intent_id, cid in intent_rows()][0] == order.client_order_id
