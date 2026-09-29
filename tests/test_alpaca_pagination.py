"""Pagination contract tests for AlpacaClient.list_fills / list_orders (UAT gap 2).

All HTTP is served by httpx.MockTransport; no live Alpaca traffic.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from trading_platform.core.settings import AlpacaBrokerSettings
from trading_platform.services import alpaca as alpaca_module
from trading_platform.services.alpaca import (
    ALPACA_ACTIVITIES_MAX_PAGE_SIZE,
    ALPACA_FILLS_MAX_PAGES,
    ALPACA_ORDERS_MAX_LIMIT,
    ALPACA_ORDERS_MAX_PAGES,
    AlpacaClient,
    AlpacaClientError,
    AlpacaPaginationCapExceededError,
    AlpacaPaginationError,
    AlpacaPaginationStalledError,
)

FILLS_PATH = "/v2/account/activities/FILL"
ORDERS_PATH = "/v2/orders"


def _alpaca_settings() -> AlpacaBrokerSettings:
    return AlpacaBrokerSettings(
        base_url="https://paper-api.alpaca.markets",
        api_key="test-key",
        api_secret="test-secret",
        max_retries=2,
        retry_backoff_factor=0.01,
        timeout_seconds=5.0,
    )


class _Recorder:
    """Captures every request's path and query params."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, str]]] = []

    def record(self, request: httpx.Request) -> None:
        self.requests.append((request.url.path, dict(request.url.params)))

    def for_path(self, path: str) -> list[dict[str, str]]:
        return [params for p, params in self.requests if p == path]


def _client(
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[AlpacaClient, _Recorder]:
    recorder = _Recorder()

    def wrapped(request: httpx.Request) -> httpx.Response:
        recorder.record(request)
        return handler(request)

    http_client = httpx.Client(
        transport=httpx.MockTransport(wrapped),
        base_url="https://paper-api.alpaca.markets",
    )
    return AlpacaClient(_alpaca_settings(), http_client=http_client), recorder


def _fill(i: int) -> dict[str, Any]:
    return {
        "id": f"fill-{i:05d}",
        "order_id": "o-1",
        "symbol": "AAPL",
        "side": "buy",
        "qty": "1",
        "price": "10",
        "transaction_time": "2024-01-05T14:31:00Z",
    }


def _order(i: int) -> dict[str, Any]:
    return {
        "id": f"order-{i:05d}",
        "client_order_id": f"client-{i:05d}",
        "symbol": "AAPL",
        "side": "buy",
        "qty": "1",
        "status": "filled",
        "submitted_at": "2024-01-05T14:31:00Z",
    }


def _paged_handler(
    path: str,
    pages: list[list[dict[str, Any]]],
    cursor_param: str,
) -> Callable[[httpx.Request], httpx.Response]:
    """Serve `pages` in order for `path`, verifying the cursor chain as it goes."""
    state = {"served": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == path
        index = state["served"]
        state["served"] += 1
        if index == 0:
            assert cursor_param not in request.url.params
        else:
            assert request.url.params[cursor_param] == pages[index - 1][-1]["id"]
        body = pages[index] if index < len(pages) else []
        return httpx.Response(200, json=body)

    return handler


def _unique_pages(
    make: Callable[[int], dict[str, Any]], sizes: list[int]
) -> list[list[dict[str, Any]]]:
    pages: list[list[dict[str, Any]]] = []
    counter = 0
    for size in sizes:
        page = []
        for _ in range(size):
            page.append(make(counter))
            counter += 1
        pages.append(page)
    return pages


# ---------------------------------------------------------------------------
# Contract: constants, signatures, hierarchy
# ---------------------------------------------------------------------------


def test_constants_match_documented_limits() -> None:
    assert ALPACA_ACTIVITIES_MAX_PAGE_SIZE == 100
    assert ALPACA_ORDERS_MAX_LIMIT == 500
    assert ALPACA_FILLS_MAX_PAGES == 100
    assert ALPACA_ORDERS_MAX_PAGES == 20


def test_list_fills_takes_no_size_arguments() -> None:
    assert list(inspect.signature(AlpacaClient.list_fills).parameters) == ["self"]


def test_pagination_error_hierarchy() -> None:
    assert issubclass(AlpacaPaginationError, AlpacaClientError)
    assert issubclass(AlpacaPaginationCapExceededError, AlpacaPaginationError)
    assert issubclass(AlpacaPaginationStalledError, AlpacaPaginationError)


# ---------------------------------------------------------------------------
# list_fills
# ---------------------------------------------------------------------------


def test_list_fills_single_short_page() -> None:
    pages = _unique_pages(_fill, [3])
    client, recorder = _client(_paged_handler(FILLS_PATH, pages, "page_token"))

    fills = client.list_fills()

    assert len(recorder.requests) == 1
    params = recorder.requests[0][1]
    assert set(params) == {"direction", "page_size"}
    assert params["page_size"] == "100"
    assert params["direction"] == "desc"
    assert len(fills) == 3


def test_list_fills_empty() -> None:
    client, recorder = _client(lambda request: httpx.Response(200, json=[]))

    assert client.list_fills() == []
    assert len(recorder.requests) == 1


def test_list_fills_concatenates_pages_with_last_id_cursor() -> None:
    pages = _unique_pages(_fill, [100, 100, 37])
    client, recorder = _client(_paged_handler(FILLS_PATH, pages, "page_token"))

    fills = client.list_fills()

    assert len(recorder.requests) == 3
    fills_params = recorder.for_path(FILLS_PATH)
    assert "page_token" not in fills_params[0]
    assert fills_params[1]["page_token"] == "fill-00099"
    assert fills_params[2]["page_token"] == "fill-00199"
    assert set(fills_params[0]) == {"direction", "page_size"}
    assert set(fills_params[1]) == {"direction", "page_size", "page_token"}
    assert set(fills_params[2]) == {"direction", "page_size", "page_token"}
    assert all(p["page_size"] == "100" and p["direction"] == "desc" for p in fills_params)
    assert [f.broker_fill_id for f in fills] == [item["id"] for page in pages for item in page]
    assert len(fills) == 237


def test_list_fills_exact_multiple_terminates_on_empty_page() -> None:
    pages = _unique_pages(_fill, [100])
    client, recorder = _client(_paged_handler(FILLS_PATH, pages, "page_token"))

    fills = client.list_fills()

    assert len(recorder.requests) == 2
    assert len(fills) == 100


def test_list_fills_cap_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(alpaca_module, "ALPACA_FILLS_MAX_PAGES", 3)
    counter = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        page = []
        for _ in range(100):
            page.append(_fill(counter["n"]))
            counter["n"] += 1
        return httpx.Response(200, json=page)

    client, recorder = _client(handler)

    with pytest.raises(AlpacaPaginationCapExceededError) as excinfo:
        client.list_fills()

    assert len(recorder.requests) == 3
    err = excinfo.value
    assert err.endpoint == FILLS_PATH
    assert err.pages_fetched == 3
    assert err.items_fetched == 300
    assert err.max_pages == 3
    assert "test-key" not in str(err) and "test-secret" not in str(err)


def test_list_fills_duplicate_id_across_pages_raises_stalled() -> None:
    page1 = [_fill(i) for i in range(100)]
    page2 = [_fill(5)] + [_fill(i) for i in range(100, 199)]
    client, _ = _client(_paged_handler(FILLS_PATH, [page1, page2], "page_token"))

    with pytest.raises(AlpacaPaginationStalledError) as excinfo:
        client.list_fills()

    assert excinfo.value.endpoint == FILLS_PATH
    assert excinfo.value.cursor == "fill-00099"


def test_list_fills_duplicate_id_within_page_raises_stalled() -> None:
    page = [_fill(1), _fill(2), _fill(1)]
    client, _ = _client(lambda request: httpx.Response(200, json=page))

    with pytest.raises(AlpacaPaginationStalledError) as excinfo:
        client.list_fills()

    assert excinfo.value.endpoint == FILLS_PATH


@pytest.mark.parametrize("bad_id", [None, 123, ""])
def test_list_fills_item_without_string_id_raises_stalled(bad_id: object) -> None:
    bad = _fill(1)
    if bad_id is None:
        del bad["id"]
    else:
        bad["id"] = bad_id
    client, _ = _client(lambda request: httpx.Response(200, json=[_fill(0), bad]))

    with pytest.raises(AlpacaPaginationStalledError):
        client.list_fills()


def test_list_fills_non_list_payload_raises_stalled() -> None:
    client, _ = _client(lambda request: httpx.Response(200, json={"error": "x"}))

    with pytest.raises(AlpacaPaginationStalledError) as excinfo:
        client.list_fills()

    assert excinfo.value.endpoint == FILLS_PATH


def test_fill_requests_never_send_date_bounds() -> None:
    pages = _unique_pages(_fill, [100, 100, 37])
    client, recorder = _client(_paged_handler(FILLS_PATH, pages, "page_token"))

    client.list_fills()

    assert len(recorder.requests) == 3
    for _, params in recorder.requests:
        assert not {"date", "after", "until"} & set(params)


# ---------------------------------------------------------------------------
# list_orders
# ---------------------------------------------------------------------------


def test_list_orders_takes_only_status() -> None:
    signature = inspect.signature(AlpacaClient.list_orders)
    assert list(signature.parameters) == ["self", "status"]
    assert signature.parameters["status"].default == "all"


def test_list_orders_single_short_page() -> None:
    pages = _unique_pages(_order, [2])
    client, recorder = _client(_paged_handler(ORDERS_PATH, pages, "before_order_id"))

    orders = client.list_orders()

    assert len(recorder.requests) == 1
    params = recorder.requests[0][1]
    assert set(params) == {"status", "direction", "limit"}
    assert params["limit"] == "500"
    assert params["direction"] == "desc"
    assert params["status"] == "all"
    assert len(orders) == 2

    open_client, open_recorder = _client(_paged_handler(ORDERS_PATH, pages, "before_order_id"))
    open_client.list_orders(status="open")
    assert open_recorder.requests[0][1]["status"] == "open"


def test_list_orders_concatenates_pages_with_before_order_id() -> None:
    pages = _unique_pages(_order, [500, 500, 12])
    client, recorder = _client(_paged_handler(ORDERS_PATH, pages, "before_order_id"))

    orders = client.list_orders()

    assert len(recorder.requests) == 3
    order_params = recorder.for_path(ORDERS_PATH)
    assert "before_order_id" not in order_params[0]
    assert order_params[1]["before_order_id"] == pages[0][-1]["id"]
    assert order_params[2]["before_order_id"] == pages[1][-1]["id"]
    assert set(order_params[0]) == {"status", "direction", "limit"}
    assert set(order_params[1]) == {"status", "direction", "limit", "before_order_id"}
    assert set(order_params[2]) == {"status", "direction", "limit", "before_order_id"}
    assert len(orders) == 1012
    assert [o.broker_order_id for o in orders] == [item["id"] for page in pages for item in page]


def test_list_orders_never_combines_cursor_with_after_until() -> None:
    pages = _unique_pages(_order, [500, 500, 12])
    client, recorder = _client(_paged_handler(ORDERS_PATH, pages, "before_order_id"))

    client.list_orders()

    assert len(recorder.requests) == 3
    for _, params in recorder.requests:
        assert not ("before_order_id" in params and ("after" in params or "until" in params))
        assert "after" not in params
        assert "until" not in params
        assert "date" not in params


def test_list_orders_exact_multiple_terminates_on_empty_page() -> None:
    pages = _unique_pages(_order, [500])
    client, recorder = _client(_paged_handler(ORDERS_PATH, pages, "before_order_id"))

    orders = client.list_orders()

    assert len(recorder.requests) == 2
    assert len(orders) == 500


def test_list_orders_cap_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(alpaca_module, "ALPACA_ORDERS_MAX_PAGES", 2)
    counter = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        page = []
        for _ in range(500):
            page.append(_order(counter["n"]))
            counter["n"] += 1
        return httpx.Response(200, json=page)

    client, recorder = _client(handler)

    with pytest.raises(AlpacaPaginationCapExceededError) as excinfo:
        client.list_orders()

    assert len(recorder.requests) == 2
    err = excinfo.value
    assert err.endpoint == ORDERS_PATH
    assert err.pages_fetched == 2
    assert err.items_fetched == 1000
    assert err.max_pages == 2


def test_list_orders_duplicate_id_raises_stalled() -> None:
    page1 = [_order(i) for i in range(500)]
    page2 = [_order(7)] + [_order(i) for i in range(500, 999)]
    client, _ = _client(_paged_handler(ORDERS_PATH, [page1, page2], "before_order_id"))

    with pytest.raises(AlpacaPaginationStalledError) as excinfo:
        client.list_orders()

    assert excinfo.value.endpoint == ORDERS_PATH


# ---------------------------------------------------------------------------
# load_broker_state wiring (callers stay zero-argument)
# ---------------------------------------------------------------------------


def test_load_broker_state_returns_complete_multi_page_sets() -> None:
    from trading_platform.core.settings import load_settings
    from trading_platform.services.reconciliation.report import load_broker_state

    order_pages = _unique_pages(_order, [500, 3])
    fill_pages = _unique_pages(_fill, [100, 5])
    order_handler = _paged_handler(ORDERS_PATH, order_pages, "before_order_id")
    fill_handler = _paged_handler(FILLS_PATH, fill_pages, "page_token")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == ORDERS_PATH:
            return order_handler(request)
        if request.url.path == FILLS_PATH:
            return fill_handler(request)
        if request.url.path == "/v2/positions":
            return httpx.Response(200, json=[])
        if request.url.path == "/v2/account":
            return httpx.Response(
                200,
                json={
                    "cash": "1000",
                    "buying_power": "2000",
                    "equity": "1000",
                    "long_market_value": "0",
                    "short_market_value": "0",
                },
            )
        return httpx.Response(404, json={"message": "unexpected path"})

    client, _ = _client(handler)

    snapshot = load_broker_state(settings=load_settings(), broker_client=client)

    assert len(snapshot.orders) == 503
    assert len(snapshot.fills) == 105
