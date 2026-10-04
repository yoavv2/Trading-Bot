"""S2-R3: the read-only latest-trade price read (``AlpacaClient.get_latest_trade``).

All HTTP is served by ``httpx.MockTransport``; no live traffic. The read is a GET on the
market-data host, parses ``trade.p`` and ``trade.t``, retries only as a GET and never
POSTs.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from trading_platform.core.settings import AlpacaBrokerSettings, Settings
from trading_platform.services.alpaca import (
    AlpacaClient,
    AlpacaPriceSource,
    PriceFailure,
    PriceLookupError,
)


def _settings(**overrides: object) -> AlpacaBrokerSettings:
    return AlpacaBrokerSettings(
        base_url="https://paper-api.alpaca.markets",
        api_key="test-key",
        api_secret="test-secret",
        max_retries=2,
        retry_backoff_factor=0.0,
        timeout_seconds=5.0,
        **overrides,  # type: ignore[arg-type]
    )


def _client(
    handler: Callable[[httpx.Request], httpx.Response], **overrides: object
) -> tuple[AlpacaClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    http_client = httpx.Client(
        transport=httpx.MockTransport(wrapped), base_url="https://paper-api.alpaca.markets"
    )
    return AlpacaClient(_settings(**overrides), http_client=http_client), seen


def _trade(price: object = "187.42", timestamp: str = "2024-01-08T15:30:00.123456789Z"):
    return {"symbol": "AAPL", "trade": {"p": price, "t": timestamp, "s": 100}}


def test_settings_defaults_are_the_documented_ones() -> None:
    settings = Settings()
    assert settings.broker.alpaca.alpaca_data_base_url == "https://data.alpaca.markets"
    assert settings.broker.alpaca.price_feed == "iex"
    assert settings.execution.pre_send_price_max_age_seconds == 120
    assert settings.execution.pre_send_max_price_deviation == 0.05
    assert settings.execution.send_authorization_ttl_seconds == 5


@pytest.mark.parametrize(
    "overrides",
    [
        {"pre_send_price_max_age_seconds": 0},
        {"pre_send_max_price_deviation": 0},
        {"pre_send_max_price_deviation": 1.5},
        {"send_authorization_ttl_seconds": 0},
    ],
)
def test_price_and_authorization_settings_are_validated(overrides: dict[str, object]) -> None:
    from pydantic import ValidationError

    from trading_platform.core.settings import ExecutionSettings

    with pytest.raises(ValidationError):
        ExecutionSettings(**overrides)  # type: ignore[arg-type]


def test_price_feed_is_a_closed_choice() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AlpacaBrokerSettings(price_feed="otc")  # type: ignore[arg-type]


def test_latest_trade_is_a_get_on_the_data_endpoint_and_parses_price_and_time() -> None:
    client, seen = _client(lambda request: httpx.Response(200, json=_trade()))

    observation = client.get_latest_trade("aapl")

    assert len(seen) == 1
    request = seen[0]
    assert request.method == "GET"
    assert request.url.host == "data.alpaca.markets"
    assert request.url.path == "/v2/stocks/AAPL/trades/latest"
    assert dict(request.url.params) == {"feed": "iex"}
    assert observation.symbol == "AAPL"
    assert observation.price == Decimal("187.42")
    assert observation.observed_at == datetime(2024, 1, 8, 15, 30, 0, 123456, tzinfo=UTC)
    assert observation.source == "alpaca_latest_trade"
    assert observation.feed == "iex"
    assert observation.fetched_at.tzinfo is not None


def test_feed_and_data_host_come_from_settings() -> None:
    client, seen = _client(
        lambda request: httpx.Response(200, json=_trade()),
        alpaca_data_base_url="https://data.example.test",
        price_feed="sip",
    )

    observation = client.get_latest_trade("AAPL")

    assert seen[0].url.host == "data.example.test"
    assert dict(seen[0].url.params) == {"feed": "sip"}
    assert observation.feed == "sip"


def test_latest_trade_retries_only_as_a_get_and_never_posts() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503, json={"message": "unavailable"})
        return httpx.Response(200, json=_trade())

    client, seen = _client(handler)

    observation = client.get_latest_trade("AAPL")

    assert observation.price == Decimal("187.42")
    assert [request.method for request in seen] == ["GET", "GET", "GET"]
    assert not any(request.method == "POST" for request in seen)


def test_lookup_error_after_the_get_retries_is_price_lookup_failed() -> None:
    client, seen = _client(lambda request: httpx.Response(503, json={"message": "down"}))

    with pytest.raises(PriceLookupError) as excinfo:
        client.get_latest_trade("AAPL")

    assert excinfo.value.failure is PriceFailure.PRICE_LOOKUP_FAILED
    assert len(seen) == 3  # initial attempt + max_retries
    assert {request.method for request in seen} == {"GET"}


@pytest.mark.parametrize("status", [401, 403])
def test_auth_statuses_are_feed_not_authorized(status: int) -> None:
    client, _ = _client(lambda request: httpx.Response(status, json={"message": "no"}))

    with pytest.raises(PriceLookupError) as excinfo:
        client.get_latest_trade("AAPL")

    assert excinfo.value.failure is PriceFailure.FEED_NOT_AUTHORIZED


def test_404_is_no_trade_today() -> None:
    client, seen = _client(lambda request: httpx.Response(404, json={"message": "nope"}))

    with pytest.raises(PriceLookupError) as excinfo:
        client.get_latest_trade("AAPL")

    assert excinfo.value.failure is PriceFailure.NO_TRADE_TODAY
    assert len(seen) == 1


@pytest.mark.parametrize(
    "payload",
    [
        _trade(price="0"),
        _trade(price="-3"),
        _trade(price="not-a-number"),
        _trade(price="NaN"),
        _trade(timestamp="yesterday"),
        {"symbol": "AAPL"},
        {"symbol": "AAPL", "trade": {"t": "2024-01-08T15:30:00Z"}},
        {"symbol": "AAPL", "trade": {"p": 10}},
    ],
)
def test_unparseable_or_non_positive_price_is_price_invalid(payload: dict[str, object]) -> None:
    client, _ = _client(lambda request: httpx.Response(200, json=payload))

    with pytest.raises(PriceLookupError) as excinfo:
        client.get_latest_trade("AAPL")

    assert excinfo.value.failure is PriceFailure.PRICE_INVALID


def test_blank_symbol_is_rejected_before_any_request() -> None:
    client, seen = _client(lambda request: httpx.Response(200, json=_trade()))

    with pytest.raises(ValueError):
        client.get_latest_trade("  ")

    assert seen == []


def test_price_source_builds_the_client_lazily_and_maps_missing_credentials() -> None:
    source = AlpacaPriceSource(AlpacaBrokerSettings(api_key="", api_secret=""))

    with pytest.raises(PriceLookupError) as excinfo:
        source.latest_trade("AAPL")

    assert excinfo.value.failure is PriceFailure.FEED_NOT_AUTHORIZED


def test_price_source_delegates_to_the_client_and_ignores_the_reference_hint() -> None:
    client, seen = _client(lambda request: httpx.Response(200, json=_trade("101.5")))
    source = AlpacaPriceSource(_settings(), client=client)

    observation = source.latest_trade("AAPL", reference_price=Decimal("1"))

    assert observation.price == Decimal("101.5")
    assert len(seen) == 1
