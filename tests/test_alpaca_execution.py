from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import httpx
import pytest

from trading_platform.core.settings import AlpacaBrokerSettings
from trading_platform.services.alpaca import AlpacaAuthError, AlpacaClient, AlpacaExecutionService
from trading_platform.services.execution import ExecutionOrderStatus, OrderIntent, OrderSide


def _alpaca_settings() -> AlpacaBrokerSettings:
    return AlpacaBrokerSettings(
        base_url="https://paper-api.alpaca.markets",
        api_key="test-key",
        api_secret="test-secret",
        max_retries=2,
        retry_backoff_factor=0.01,
        timeout_seconds=5.0,
    )


def _order_intent() -> OrderIntent:
    return OrderIntent(
        strategy_id="trend_following_daily",
        symbol="AAPL",
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        intended_session=date(2024, 1, 5),
        client_order_id="tp-20240105-aapl-123456",
    )


def _success_payload() -> dict[str, object]:
    return {
        "id": "broker-order-123",
        "client_order_id": "tp-20240105-aapl-123456",
        "symbol": "AAPL",
        "side": "buy",
        "qty": "10",
        "type": "market",
        "time_in_force": "day",
        "status": "new",
        "submitted_at": "2024-01-05T14:31:00Z",
    }


def test_alpaca_client_requires_credentials() -> None:
    with pytest.raises(AlpacaAuthError):
        AlpacaClient(AlpacaBrokerSettings(api_key="", api_secret=""))

    with pytest.raises(AlpacaAuthError):
        AlpacaClient(AlpacaBrokerSettings(api_key="key", api_secret=""))


def test_alpaca_execution_maps_payload_and_normalizes_response() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = {
            "APCA-API-KEY-ID": request.headers["APCA-API-KEY-ID"],
            "APCA-API-SECRET-KEY": request.headers["APCA-API-SECRET-KEY"],
        }
        captured["path"] = request.url.path
        captured["json"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json=_success_payload())

    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport, base_url="https://paper-api.alpaca.markets")
    client = AlpacaClient(_alpaca_settings(), http_client=http_client)
    service = AlpacaExecutionService(_alpaca_settings(), client=client)

    result = service.submit_order(_order_intent())

    assert captured["path"] == "/v2/orders"
    assert captured["headers"] == {
        "APCA-API-KEY-ID": "test-key",
        "APCA-API-SECRET-KEY": "test-secret",
    }
    assert captured["json"] == {
        "symbol": "AAPL",
        "qty": "10",
        "side": "buy",
        "type": "market",
        "time_in_force": "day",
        "client_order_id": "tp-20240105-aapl-123456",
    }
    assert result.client_order_id == "tp-20240105-aapl-123456"
    assert result.broker_order_id == "broker-order-123"
    assert result.status == ExecutionOrderStatus.PENDING
    assert result.broker_status == "new"
    assert result.submitted_at is not None
    assert result.submitted_at.isoformat() == "2024-01-05T14:31:00+00:00"
    assert service.describe()["provider"] == "alpaca"

    service.close()
    http_client.close()


def test_alpaca_client_retries_transient_transport_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise httpx.ReadTimeout("timed out", request=request)
        return httpx.Response(200, json=_success_payload())

    monkeypatch.setattr("trading_platform.services.alpaca.time.sleep", lambda *_: None)

    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport, base_url="https://paper-api.alpaca.markets")
    client = AlpacaClient(_alpaca_settings(), http_client=http_client)

    result = client.submit_order(_order_intent())

    assert attempts["count"] == 2
    assert result.broker_order_id == "broker-order-123"

    client.close()
    http_client.close()
