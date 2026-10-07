"""Tiingo adapter: header-only auth, no token in URLs, closed failure codes, budget, parsing."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import httpx
import pytest

from trading_platform.core.settings import TiingoProviderSettings
from trading_platform.services.research.budget import UnlimitedRequestBudget
from trading_platform.services.tiingo import (
    TiingoAuthError,
    TiingoClient,
    TiingoClientError,
    TiingoRateLimitError,
    TiingoRequestBudgetExceededError,
    parse_session_date,
)

PRICES = [
    {
        "date": "2020-08-31T00:00:00.000Z",
        "open": 127.58, "high": 131.0, "low": 126.0, "close": 129.04, "volume": 225702700,
        "adjOpen": 125.1, "adjHigh": 128.5, "adjLow": 123.6, "adjClose": 126.5, "adjVolume": 902810800,
        "divCash": 0.0, "splitFactor": 4.0,
    },
    {
        "date": "2020-08-28T00:00:00.000Z",
        "open": 504.05, "high": 505.77, "low": 498.31, "close": 499.23, "volume": 46907479,
        "adjOpen": 122.2, "adjHigh": 122.6, "adjLow": 120.8, "adjClose": 121.0, "adjVolume": 187629916,
        "divCash": 0.0, "splitFactor": 1.0,
    },
]


def _client(handler, **overrides) -> tuple[TiingoClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    settings = TiingoProviderSettings(api_key="secret-key", max_retries=1, retry_backoff_factor=0, **overrides)
    client = TiingoClient(settings, budget=UnlimitedRequestBudget(), transport=httpx.MockTransport(wrapped), sleep=lambda s: None)
    return client, seen


def test_header_auth_only_and_no_token_anywhere_in_the_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/prices"):
            return httpx.Response(200, json=PRICES)
        return httpx.Response(200, json={"ticker": "aapl", "name": "Apple Inc", "exchangeCode": "NASDAQ", "startDate": "1980-12-12", "endDate": "2026-10-06"})

    client, seen = _client(handler)
    with client:
        meta = client.fetch_metadata("AAPL")
        rows = client.fetch_daily_prices("AAPL", date(2020, 8, 28), date(2020, 8, 31))
    for request in seen:
        assert request.headers["Authorization"] == "Token secret-key"
        assert "token" not in str(request.url).lower().replace("/tiingo", "")
        assert "secret-key" not in str(request.url)
    assert meta.ticker == "AAPL" and meta.name == "Apple Inc" and meta.start_date == date(1980, 12, 12)
    assert [r.session_date for r in rows] == [date(2020, 8, 28), date(2020, 8, 31)]
    split_day = rows[-1]
    assert (split_day.split_factor, split_day.div_cash) == (Decimal("4.0"), Decimal("0.0"))
    assert split_day.adj_volume == 902810800 and split_day.volume == 225702700
    assert split_day.open == Decimal("127.58") and split_day.adj_open == Decimal("125.1")


def test_session_date_is_first_ten_characters_without_timezone_conversion() -> None:
    assert parse_session_date("2020-08-31T00:00:00.000Z") == date(2020, 8, 31)
    assert parse_session_date("2020-08-31T23:59:59.000-05:00") == date(2020, 8, 31)
    with pytest.raises(TiingoClientError):
        parse_session_date("2020-8")


def test_token_query_parameter_is_refused_before_any_request() -> None:
    client, seen = _client(lambda request: httpx.Response(200, json=[]))
    with client, pytest.raises(TiingoClientError):
        client._get("/tiingo/daily/AAPL/prices", params={"token": "x"})
    assert seen == []


@pytest.mark.parametrize(("status", "error"), [(401, TiingoAuthError), (403, TiingoAuthError), (429, TiingoRateLimitError)])
def test_closed_failure_codes(status: int, error) -> None:
    client, _ = _client(lambda request: httpx.Response(status, json={"detail": "x"}))
    with client, pytest.raises(error):
        client.fetch_metadata("AAPL")
    if error is TiingoRateLimitError:
        assert TiingoRateLimitError.code == "provider_rate_limited"


def test_every_attempt_is_admitted_and_completed_with_its_outcome() -> None:
    """Retries are separate attempts; a 429 completes as rate_limited and is not retried."""

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("boom", request=request)
        if request.url.path.endswith("/prices"):
            return httpx.Response(200, json=[])
        return httpx.Response(429, json={"detail": "slow down"})

    budget = UnlimitedRequestBudget()
    settings = TiingoProviderSettings(api_key="k", max_retries=1, retry_backoff_factor=0)
    client = TiingoClient(settings, budget=budget, transport=httpx.MockTransport(handler), sleep=lambda s: None)
    with client:
        assert client.fetch_daily_prices("A", date(2020, 1, 2), date(2020, 1, 3)) == []
        with pytest.raises(TiingoRateLimitError):
            client.fetch_metadata("A")
    assert client.requests_made == 3
    assert [(a.symbol, a.purpose) for a in budget.admitted] == [("A", "prices"), ("A", "prices"), ("A", "metadata")]
    assert [(o, c) for _, o, c in budget.completed] == [("transport_error", None), ("ok", 200), ("rate_limited", 429)]
    assert TiingoRequestBudgetExceededError.code == "provider_budget_exhausted"


def test_transport_errors_are_retried_then_raised() -> None:
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(200, json=[])

    client, _ = _client(handler)
    with client:
        assert client.fetch_daily_prices("A", date(2020, 1, 2), date(2020, 1, 3)) == []
    assert attempts["n"] == 2

    always_failing, _ = _client(lambda request: (_ for _ in ()).throw(httpx.ConnectError("boom", request=request)))
    with always_failing, pytest.raises(TiingoClientError):
        always_failing.fetch_daily_prices("A", date(2020, 1, 2), date(2020, 1, 3))


def test_missing_key_is_refused_at_construction() -> None:
    with pytest.raises(TiingoAuthError):
        TiingoClient(TiingoProviderSettings(api_key=""), budget=UnlimitedRequestBudget())


def test_rows_are_sorted_ascending_even_when_the_provider_returns_descending() -> None:
    client, _ = _client(lambda request: httpx.Response(200, content=json.dumps(PRICES)))
    with client:
        rows = client.fetch_daily_prices("AAPL", date(2020, 8, 28), date(2020, 8, 31))
    assert rows[0].session_date < rows[1].session_date
