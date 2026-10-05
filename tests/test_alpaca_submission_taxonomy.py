"""COR-06 Alpaca client: single-attempt order POST, GET lookup (404-only), status table."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from trading_platform.core.settings import AlpacaBrokerSettings
from trading_platform.db.models import AttemptOutcomeClass
from trading_platform.services.alpaca import (
    _BROKER_STATUS_CLASSES,
    _PENDING_BROKER_STATUSES,
    UNMAPPED_BROKER_STATUS,
    AlpacaClient,
    AlpacaClientError,
    AlpacaOrderSubmissionError,
    AmbiguousOrderSubmissionError,
    AttemptLogNotBoundError,
    BrokerStatusClass,
    OrderNotSentError,
    OrderRejectedError,
    _normalize_status,
    _normalized_order_snapshot,
    _normalized_result,
    classify_broker_status,
)
from trading_platform.services.execution import (
    ExecutionOrderStatus,
    OrderIntent,
    OrderSide,
)
from trading_platform.services.execution.attempts import (
    AttemptRecord,
    SubmissionClass,
    bind_attempt_log,
)

A = AttemptOutcomeClass
CLIENT_ORDER_ID = "tp-20240105-aapl-123456"
REGISTERED_AT = datetime(2024, 1, 5, 14, 0, tzinfo=UTC)


def _settings() -> AlpacaBrokerSettings:
    return AlpacaBrokerSettings(
        base_url="https://paper-api.alpaca.markets",
        api_key="test-key",
        api_secret="test-secret",
        max_retries=2,
        retry_backoff_factor=0.01,
        timeout_seconds=5.0,
    )


def _intent() -> OrderIntent:
    return OrderIntent(
        strategy_id="trend_following_daily",
        symbol="AAPL",
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        intended_session=date(2024, 1, 5),
        client_order_id=CLIENT_ORDER_ID,
    )


def _order_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": "broker-order-123",
        "client_order_id": CLIENT_ORDER_ID,
        "symbol": "AAPL",
        "side": "buy",
        "qty": "10",
        "type": "market",
        "time_in_force": "day",
        "status": "new",
        "created_at": (REGISTERED_AT + timedelta(minutes=1)).isoformat(),
        "submitted_at": "2024-01-05T14:31:00Z",
    }
    payload.update(overrides)
    return payload


class RecordingLog:
    """DB-free attempt log that records every begin/complete call."""

    def __init__(
        self,
        existing: Sequence[AttemptRecord] = (),
        *,
        registered_at: datetime | None = REGISTERED_AT,
        fail_complete: bool = False,
    ) -> None:
        self.records: list[AttemptRecord] = list(existing)
        self._registered_at = registered_at
        self._fail_complete = fail_complete
        self.calls: list[str] = []

    def existing_attempts(self) -> Sequence[AttemptRecord]:
        return tuple(self.records)

    def intent_registered_at(self) -> datetime | None:
        return self._registered_at

    def begin_attempt(self) -> int:
        number = len(self.records) + 1
        self.records.append(AttemptRecord(attempt_number=number))
        self.calls.append(f"begin:{number}")
        return number

    def complete_attempt(
        self,
        number: int,
        *,
        outcome_class: AttemptOutcomeClass,
        http_status: int | None = None,
        error_type: str | None = None,
        broker_message: str | None = None,
    ) -> None:
        if self._fail_complete:
            raise RuntimeError("database unavailable")
        self.calls.append(f"complete:{number}:{outcome_class.value}")
        self.records[number - 1] = AttemptRecord(
            attempt_number=number,
            completed_at=datetime.now(UTC),
            outcome_class=outcome_class,
            http_status=http_status,
            error_type=error_type,
            broker_message=broker_message,
        )

    @property
    def outcomes(self) -> list[AttemptOutcomeClass | None]:
        return [record.outcome_class for record in self.records]


class Transport:
    """Counts requests per (method, path) and plays scripted responses."""

    def __init__(self, script: Callable[[httpx.Request, int], httpx.Response]) -> None:
        self._script = script
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._script(request, len(self.requests))

    def count(self, method: str, path_prefix: str) -> int:
        return sum(
            1 for r in self.requests if r.method == method and r.url.path.startswith(path_prefix)
        )


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("trading_platform.services.alpaca.time.sleep", lambda *_: None)


def _client(transport: Transport) -> AlpacaClient:
    http_client = httpx.Client(
        transport=httpx.MockTransport(transport), base_url="https://paper-api.alpaca.markets"
    )
    return AlpacaClient(_settings(), http_client=http_client)


def _submit(client: AlpacaClient, log: RecordingLog):
    with bind_attempt_log(log):
        return client.submit_order(_intent())


# --- POST: ambiguous / not sent ---------------------------------------------------


def test_read_timeout_after_send_is_exactly_one_post_and_ambiguous() -> None:
    def script(request: httpx.Request, n: int) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    transport, log = Transport(script), RecordingLog()
    with pytest.raises(AmbiguousOrderSubmissionError) as excinfo:
        _submit(_client(transport), log)
    assert transport.count("POST", "/v2/orders") == 1
    assert log.outcomes == [A.AMBIGUOUS]
    assert excinfo.value.submission_class == SubmissionClass.AMBIGUOUS
    assert excinfo.value.attempt_numbers == (1,)


def test_connect_error_twice_then_accepted_is_three_attempts() -> None:
    def script(request: httpx.Request, n: int) -> httpx.Response:
        if n <= 2:
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(201, json=_order_payload())

    transport, log = Transport(script), RecordingLog()
    result = _submit(_client(transport), log)
    assert transport.count("POST", "/v2/orders") == 3
    assert log.outcomes == [A.PRE_CONNECTION, A.PRE_CONNECTION, A.ACCEPTED]
    assert result.broker_order_id == "broker-order-123"
    # durable ordering: each attempt is begun before and completed after its POST
    assert log.calls == [
        "begin:1",
        "complete:1:pre_connection",
        "begin:2",
        "complete:2:pre_connection",
        "begin:3",
        "complete:3:accepted",
    ]


def test_connect_errors_beyond_max_retries_raise_not_sent() -> None:
    def script(request: httpx.Request, n: int) -> httpx.Response:
        raise httpx.ConnectTimeout("connect timed out", request=request)

    transport, log = Transport(script), RecordingLog()
    with pytest.raises(OrderNotSentError) as excinfo:
        _submit(_client(transport), log)
    assert transport.count("POST", "/v2/orders") == _settings().max_retries + 1
    assert excinfo.value.submission_class == SubmissionClass.NOT_SENT
    assert log.outcomes == [A.PRE_CONNECTION] * 3


def test_ambiguous_then_connect_error_never_sends_a_third_post() -> None:
    def script(request: httpx.Request, n: int) -> httpx.Response:
        if n == 1:
            raise httpx.ReadTimeout("timed out", request=request)
        raise httpx.ConnectError("refused", request=request)

    transport, log = Transport(script), RecordingLog()
    client = _client(transport)
    with pytest.raises(AmbiguousOrderSubmissionError):
        _submit(client, log)
    assert transport.count("POST", "/v2/orders") == 1
    # a second call over the same history is refused before any POST
    with pytest.raises(AmbiguousOrderSubmissionError):
        _submit(client, log)
    assert transport.count("POST", "/v2/orders") == 1
    assert classify(log) == SubmissionClass.AMBIGUOUS


def classify(log: RecordingLog) -> SubmissionClass | None:
    from trading_platform.services.execution.attempts import classify_submission

    return classify_submission(log.records)


@pytest.mark.parametrize(
    "history",
    [
        [A.AMBIGUOUS],
        [None],
        [A.PRE_CONNECTION, None],
        [A.AMBIGUOUS, A.PRE_CONNECTION],
        [A.DUPLICATE_REPORTED],
        [A.ACCEPTED],
        [A.REJECTED],
        [A.AMBIGUOUS, A.DEADLINE_EXPIRED],
    ],
)
def test_prior_unclean_history_sends_zero_posts(history: list[AttemptOutcomeClass | None]) -> None:
    def script(request: httpx.Request, n: int) -> httpx.Response:
        return httpx.Response(201, json=_order_payload())

    existing = [
        AttemptRecord(
            attempt_number=i, outcome_class=o, completed_at=None if o is None else REGISTERED_AT
        )
        for i, o in enumerate(history, start=1)
    ]
    transport, log = Transport(script), RecordingLog(existing)
    with pytest.raises(AmbiguousOrderSubmissionError):
        _submit(_client(transport), log)
    assert transport.requests == []


def test_prior_not_sent_history_may_be_retried_and_counts_toward_the_in_loop_rule() -> None:
    def script(request: httpx.Request, n: int) -> httpx.Response:
        return httpx.Response(201, json=_order_payload())

    existing = [
        AttemptRecord(attempt_number=1, outcome_class=A.PRE_CONNECTION, completed_at=REGISTERED_AT),
        AttemptRecord(
            attempt_number=2, outcome_class=A.DEADLINE_EXPIRED, completed_at=REGISTERED_AT
        ),
    ]
    transport, log = Transport(script), RecordingLog(existing)
    _submit(_client(transport), log)
    assert transport.count("POST", "/v2/orders") == 1
    assert log.outcomes == [A.PRE_CONNECTION, A.DEADLINE_EXPIRED, A.ACCEPTED]


def test_unbound_log_fails_closed_with_zero_posts() -> None:
    transport = Transport(lambda request, n: httpx.Response(201, json=_order_payload()))
    client = _client(transport)
    with pytest.raises(AttemptLogNotBoundError):
        client.submit_order(_intent())
    assert transport.requests == []


def test_outcome_not_recorded_after_a_response_is_ambiguous_and_not_retried() -> None:
    transport = Transport(lambda request, n: httpx.Response(201, json=_order_payload()))
    log = RecordingLog(fail_complete=True)
    with pytest.raises(AmbiguousOrderSubmissionError) as excinfo:
        _submit(_client(transport), log)
    assert transport.count("POST", "/v2/orders") == 1
    assert excinfo.value.reason == "attempt_outcome_not_recorded"


# --- POST: HTTP classes ---------------------------------------------------------------


def test_duplicate_message_triggers_one_lookup_and_no_inference() -> None:
    def script(request: httpx.Request, n: int) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                422, json={"code": 40010001, "message": "client_order_id must be unique"}
            )
        return httpx.Response(200, json=_order_payload())

    transport, log = Transport(script), RecordingLog()
    result = _submit(_client(transport), log)
    assert transport.count("POST", "/v2/orders") == 1
    assert transport.count("GET", "/v2/orders:by_client_order_id") == 1
    assert log.outcomes == [A.DUPLICATE_REPORTED]
    assert result.broker_order_id == "broker-order-123"
    assert result.client_order_id == CLIENT_ORDER_ID


@pytest.mark.parametrize(
    ("lookup", "reason"),
    [
        (lambda: httpx.Response(404, json={"message": "order not found"}), "lookup_not_found"),
        (lambda: httpx.Response(500, json={"message": "boom"}), "lookup_failed"),
        (lambda: httpx.Response(422, json={"message": "bad"}), "lookup_failed"),
    ],
)
def test_duplicate_without_a_found_record_is_ambiguous(
    lookup: Callable[[], httpx.Response], reason: str
) -> None:
    def script(request: httpx.Request, n: int) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(422, json={"message": "client_order_id must be unique"})
        return lookup()

    transport, log = Transport(script), RecordingLog()
    with pytest.raises(AmbiguousOrderSubmissionError) as excinfo:
        _submit(_client(transport), log)
    assert excinfo.value.reason == reason
    assert transport.count("POST", "/v2/orders") == 1


@pytest.mark.parametrize(
    "override",
    [
        {"symbol": "MSFT"},
        {"side": "sell"},
        {"qty": "11"},
        {"type": "limit"},
        {"client_order_id": "someone-else"},
        {"created_at": (REGISTERED_AT - timedelta(days=1)).isoformat()},
        {"created_at": None},
    ],
)
def test_duplicate_lookup_record_must_match_the_intent(override: dict[str, object]) -> None:
    def script(request: httpx.Request, n: int) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(422, json={"message": "client_order_id must be unique"})
        return httpx.Response(200, json=_order_payload(**override))

    with pytest.raises(AmbiguousOrderSubmissionError) as excinfo:
        _submit(_client(Transport(script)), RecordingLog())
    assert excinfo.value.reason == "id_mismatch"


def test_page_size_422_with_code_40010001_is_rejected_not_duplicate() -> None:
    def script(request: httpx.Request, n: int) -> httpx.Response:
        return httpx.Response(
            422, json={"code": 40010001, "message": "page_size must be at most 100"}
        )

    transport, log = Transport(script), RecordingLog()
    with pytest.raises(OrderRejectedError) as excinfo:
        _submit(_client(transport), log)
    assert excinfo.value.http_status == 422
    assert transport.count("GET", "/v2/orders:by_client_order_id") == 0
    assert log.outcomes == [A.REJECTED]


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_other_4xx_is_rejected_with_one_post(status: int) -> None:
    transport = Transport(lambda request, n: httpx.Response(status, json={"message": "no"}))
    log = RecordingLog()
    with pytest.raises(OrderRejectedError) as excinfo:
        _submit(_client(transport), log)
    assert excinfo.value.http_status == status
    assert excinfo.value.submission_class == SubmissionClass.REJECTED
    assert transport.count("POST", "/v2/orders") == 1
    assert log.records[0].broker_message == "no"


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_transient_statuses_are_ambiguous_after_one_post(status: int) -> None:
    transport = Transport(lambda request, n: httpx.Response(status, json={"message": "later"}))
    log = RecordingLog()
    with pytest.raises(AmbiguousOrderSubmissionError):
        _submit(_client(transport), log)
    assert transport.count("POST", "/v2/orders") == 1
    assert log.outcomes == [A.AMBIGUOUS]


def test_2xx_with_a_non_json_body_is_ambiguous() -> None:
    transport = Transport(lambda request, n: httpx.Response(200, content=b"<html>ok</html>"))
    log = RecordingLog()
    with pytest.raises(AmbiguousOrderSubmissionError):
        _submit(_client(transport), log)
    assert transport.count("POST", "/v2/orders") == 1
    assert log.outcomes == [A.AMBIGUOUS]
    assert log.records[0].error_type == "InvalidResponseBody"


def test_accepted_reply_with_invalid_quantity_is_ambiguous() -> None:
    """SAF-06: a 2xx JSON object that fails normalization is an uncertain outcome, not a failure."""

    transport = Transport(lambda request, n: httpx.Response(201, json=_order_payload(qty="abc")))
    log = RecordingLog()
    with pytest.raises(AmbiguousOrderSubmissionError) as excinfo:
        _submit(_client(transport), log)
    assert excinfo.value.reason == "invalid_response_body"
    assert excinfo.value.submission_class == SubmissionClass.ACCEPTED
    assert transport.count("POST", "/v2/orders") == 1
    # the outcome was recorded once, as accepted, before the parse failed
    assert log.outcomes == [A.ACCEPTED]
    assert [call for call in log.calls if call.startswith("complete")] == ["complete:1:accepted"]


def test_accepted_reply_with_invalid_timestamp_is_ambiguous() -> None:
    transport = Transport(
        lambda request, n: httpx.Response(201, json=_order_payload(submitted_at="not-a-date"))
    )
    log = RecordingLog()
    with pytest.raises(AmbiguousOrderSubmissionError) as excinfo:
        _submit(_client(transport), log)
    assert excinfo.value.reason == "invalid_response_body"
    assert excinfo.value.submission_class == SubmissionClass.ACCEPTED
    assert transport.count("POST", "/v2/orders") == 1
    assert log.outcomes == [A.ACCEPTED]


def test_duplicate_reply_with_unparseable_snapshot_is_ambiguous() -> None:
    def script(request: httpx.Request, n: int) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(422, json={"message": "client_order_id must be unique"})
        return httpx.Response(200, json=_order_payload(submitted_at="not-a-date"))

    transport, log = Transport(script), RecordingLog()
    with pytest.raises(AmbiguousOrderSubmissionError) as excinfo:
        _submit(_client(transport), log)
    assert excinfo.value.reason == "invalid_response_body"
    assert excinfo.value.submission_class == SubmissionClass.EXISTS_REPORTED
    assert transport.count("POST", "/v2/orders") == 1
    assert log.outcomes == [A.DUPLICATE_REPORTED]


def test_all_submission_errors_are_alpaca_client_errors() -> None:
    for exc_type in (AmbiguousOrderSubmissionError, OrderNotSentError, OrderRejectedError):
        assert issubclass(exc_type, AlpacaOrderSubmissionError)
        assert issubclass(exc_type, AlpacaClientError)


def test_stored_evidence_contains_no_credentials() -> None:
    transport = Transport(lambda request, n: httpx.Response(400, json={"message": "bad qty"}))
    log = RecordingLog()
    with pytest.raises(OrderRejectedError):
        _submit(_client(transport), log)
    stored = json.dumps([r.__dict__ for r in log.records], default=str)
    assert "test-key" not in stored and "test-secret" not in stored


# --- generic retry loop is GET-only ------------------------------------------------------


def test_post_through_request_with_retry_raises_value_error() -> None:
    transport = Transport(lambda request, n: httpx.Response(200, json={}))
    with pytest.raises(ValueError):
        _client(transport)._request_with_retry("POST", "/v2/orders")
    assert transport.requests == []


def test_get_calls_still_retry_transient_failures() -> None:
    def make(payload: object) -> Transport:
        def script(request: httpx.Request, n: int) -> httpx.Response:
            if n == 1:
                raise httpx.ReadTimeout("timed out", request=request)
            if n == 2:
                return httpx.Response(503, json={"message": "later"})
            return httpx.Response(200, json=payload)

        return Transport(script)

    orders = make([_order_payload()])
    assert [o.broker_order_id for o in _client(orders).list_orders()] == ["broker-order-123"]
    assert len(orders.requests) == 3

    fills = make([])
    assert _client(fills).list_fills() == []
    assert len(fills.requests) == 3

    positions = make([])
    assert _client(positions).list_positions() == []
    assert len(positions.requests) == 3

    account = make(
        {
            "cash": "1",
            "buying_power": "2",
            "equity": "3",
            "long_market_value": "0",
            "short_market_value": "0",
        }
    )
    assert _client(account).get_account().cash == Decimal("1")
    assert len(account.requests) == 3


# --- GET lookup: 404 only ---------------------------------------------------------------------


def test_lookup_treats_only_404_as_not_found() -> None:
    assert (
        _client(Transport(lambda r, n: httpx.Response(404, json={}))).get_order_by_client_order_id(
            "x"
        )
        is None
    )

    five = Transport(lambda r, n: httpx.Response(500, json={"message": "boom"}))
    with pytest.raises(AlpacaClientError):
        _client(five).get_order_by_client_order_id("x")
    assert len(five.requests) == _settings().max_retries + 1  # GET retries preserved

    with pytest.raises(AlpacaClientError):
        _client(
            Transport(lambda r, n: httpx.Response(422, json={"message": "bad"}))
        ).get_order_by_client_order_id("x")

    def timeout_then_ok(request: httpx.Request, n: int) -> httpx.Response:
        if n == 1:
            raise httpx.ReadTimeout("timed out", request=request)
        return httpx.Response(200, json=_order_payload())

    snapshot = _client(Transport(timeout_then_ok)).get_order_by_client_order_id(CLIENT_ORDER_ID)
    assert snapshot is not None and snapshot.broker_order_id == "broker-order-123"

    with pytest.raises(AlpacaClientError):
        _client(
            Transport(lambda r, n: (_ for _ in ()).throw(httpx.ConnectError("x", request=r)))
        ).get_order_by_client_order_id("x")


def test_lookup_sends_the_client_order_id_as_a_query_parameter() -> None:
    transport = Transport(lambda r, n: httpx.Response(200, json=_order_payload()))
    _client(transport).get_order_by_client_order_id(CLIENT_ORDER_ID)
    request = transport.requests[0]
    assert request.url.path == "/v2/orders:by_client_order_id"
    assert request.url.params["client_order_id"] == CLIENT_ORDER_ID


# --- closed status table -----------------------------------------------------------------------

DOCUMENTED_STATUSES = [
    "new",
    "partially_filled",
    "filled",
    "done_for_day",
    "canceled",
    "expired",
    "replaced",
    "pending_cancel",
    "pending_replace",
    "accepted",
    "pending_new",
    "accepted_for_bidding",
    "stopped",
    "rejected",
    "suspended",
    "calculated",
]

EXPECTED_CLASS = {
    "new": BrokerStatusClass.WORKING,
    "partially_filled": BrokerStatusClass.WORKING,
    "filled": BrokerStatusClass.TERMINAL,
    "done_for_day": BrokerStatusClass.WORKING,
    "canceled": BrokerStatusClass.TERMINAL,
    "expired": BrokerStatusClass.TERMINAL,
    "replaced": BrokerStatusClass.TERMINAL_WITH_SUCCESSOR,
    "pending_cancel": BrokerStatusClass.WORKING,
    "pending_replace": BrokerStatusClass.WORKING,
    "accepted": BrokerStatusClass.WORKING,
    "pending_new": BrokerStatusClass.WORKING,
    "accepted_for_bidding": BrokerStatusClass.WORKING,
    "stopped": BrokerStatusClass.WORKING,
    "rejected": BrokerStatusClass.TERMINAL,
    "suspended": BrokerStatusClass.WORKING,
    "calculated": BrokerStatusClass.WORKING,
}


def test_sixteen_documented_statuses_are_named_literally() -> None:
    assert len(DOCUMENTED_STATUSES) == 16 and len(set(DOCUMENTED_STATUSES)) == 16
    assert set(DOCUMENTED_STATUSES) == set(EXPECTED_CLASS)


@pytest.mark.parametrize("status", DOCUMENTED_STATUSES)
def test_documented_status_maps_to_exactly_one_class(status: str) -> None:
    assert classify_broker_status(status) == EXPECTED_CLASS[status]
    assert status in _BROKER_STATUS_CLASSES


def test_legacy_held_stays_working() -> None:
    assert classify_broker_status("held") == BrokerStatusClass.WORKING


def test_status_table_has_no_extra_entries() -> None:
    assert set(_BROKER_STATUS_CLASSES) == set(DOCUMENTED_STATUSES) | {"held"}


def test_done_for_day_is_working_and_replaced_is_not_pending() -> None:
    assert "done_for_day" in _PENDING_BROKER_STATUSES
    assert "replaced" not in _PENDING_BROKER_STATUSES
    assert _normalize_status("done_for_day") == ExecutionOrderStatus.PENDING
    assert _normalize_status("replaced") == ExecutionOrderStatus.REPLACED
    assert _normalize_status("partially_filled") == ExecutionOrderStatus.PARTIALLY_FILLED


@pytest.mark.parametrize("raw", ["", None, "holding", "unknown", "NEW"])
def test_unmapped_status_is_unknown_with_a_reason(raw: str | None) -> None:
    assert classify_broker_status(raw) == BrokerStatusClass.UNKNOWN
    assert _normalize_status(raw) == ExecutionOrderStatus.UNKNOWN
    payload = _order_payload()
    if raw is None:
        payload.pop("status")
    else:
        payload["status"] = raw
    assert _normalized_order_snapshot(payload).status_reason == UNMAPPED_BROKER_STATUS
    assert _normalized_result(payload).status_reason == UNMAPPED_BROKER_STATUS


@pytest.mark.parametrize("status", DOCUMENTED_STATUSES + ["held"])
def test_mapped_status_has_no_reason(status: str) -> None:
    payload = _order_payload(status=status)
    assert _normalized_order_snapshot(payload).status_reason is None
    assert _normalized_result(payload).status_reason is None
