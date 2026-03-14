"""Thin Alpaca paper-trading client and execution adapter."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from decimal import Decimal
from typing import Any, TypeVar

import httpx

from trading_platform.core.settings import AlpacaBrokerSettings
from trading_platform.services.execution import (
    ExecutionOrderStatus,
    ExecutionService,
    OrderIntent,
    OrderSide,
    OrderSubmissionResult,
    OrderTimeInForce,
    OrderType,
)

logger = logging.getLogger(__name__)

_TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}
_PENDING_BROKER_STATUSES = {
    "accepted",
    "accepted_for_bidding",
    "calculated",
    "held",
    "new",
    "pending_cancel",
    "pending_new",
    "pending_replace",
    "replaced",
    "stopped",
    "suspended",
}

EnumT = TypeVar("EnumT")


class AlpacaClientError(Exception):
    """Raised when the Alpaca REST client encounters a non-recoverable error."""


class AlpacaAuthError(AlpacaClientError):
    """Raised when Alpaca credentials are missing or rejected."""


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _coerce_enum(enum_cls: type[EnumT], value: str, default: EnumT) -> EnumT:
    try:
        return enum_cls(value)  # type: ignore[arg-type]
    except ValueError:
        return default


def _normalize_status(broker_status: str | None) -> ExecutionOrderStatus:
    if not broker_status:
        return ExecutionOrderStatus.UNKNOWN
    if broker_status in _PENDING_BROKER_STATUSES:
        return ExecutionOrderStatus.PENDING
    if broker_status == "partially_filled":
        return ExecutionOrderStatus.PARTIALLY_FILLED
    if broker_status == "filled":
        return ExecutionOrderStatus.FILLED
    if broker_status == "canceled":
        return ExecutionOrderStatus.CANCELED
    if broker_status == "rejected":
        return ExecutionOrderStatus.REJECTED
    if broker_status == "expired":
        return ExecutionOrderStatus.EXPIRED
    return ExecutionOrderStatus.UNKNOWN


def _normalize_quantity(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.000001"))


def _normalized_result(payload: dict[str, Any]) -> OrderSubmissionResult:
    broker_status = str(payload.get("status") or "unknown")
    return OrderSubmissionResult(
        client_order_id=str(payload.get("client_order_id") or ""),
        broker_order_id=str(payload.get("id") or ""),
        symbol=str(payload.get("symbol") or ""),
        side=_coerce_enum(OrderSide, str(payload.get("side") or "buy"), OrderSide.BUY),
        quantity=_normalize_quantity(payload.get("qty") or "0"),
        order_type=_coerce_enum(OrderType, str(payload.get("type") or "market"), OrderType.MARKET),
        time_in_force=_coerce_enum(
            OrderTimeInForce,
            str(payload.get("time_in_force") or "day"),
            OrderTimeInForce.DAY,
        ),
        status=_normalize_status(broker_status),
        broker_status=broker_status,
        submitted_at=_parse_datetime(payload.get("submitted_at")),
        raw_payload=payload,
    )


class AlpacaClient:
    """Thin httpx-based client for Alpaca paper-order submission."""

    def __init__(
        self,
        settings: AlpacaBrokerSettings,
        *,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._settings = settings
        if not settings.api_key or not settings.api_secret:
            raise AlpacaAuthError(
                "Alpaca API credentials are not configured. "
                "Set TRADING_PLATFORM_BROKER__ALPACA__API_KEY and "
                "TRADING_PLATFORM_BROKER__ALPACA__API_SECRET."
            )
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(
            base_url=settings.base_url,
            timeout=settings.timeout_seconds,
        )
        self._client.headers.update(
            {
                "APCA-API-KEY-ID": settings.api_key,
                "APCA-API-SECRET-KEY": settings.api_secret,
            }
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "AlpacaClient":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def submit_order(self, intent: OrderIntent) -> OrderSubmissionResult:
        payload = {
            "symbol": intent.symbol,
            "qty": str(intent.quantity),
            "side": intent.side.value,
            "type": intent.order_type.value,
            "time_in_force": intent.time_in_force.value,
            "client_order_id": intent.client_order_id,
        }
        response_payload = self._post_with_retry("/v2/orders", payload=payload)
        return _normalized_result(response_payload)

    def _post_with_retry(self, path: str, *, payload: dict[str, Any]) -> dict[str, Any]:
        attempts = 0
        last_error: Exception | None = None

        while attempts <= self._settings.max_retries:
            try:
                response = self._client.post(path, json=payload)
                if response.status_code in (401, 403):
                    raise AlpacaAuthError(
                        f"Alpaca returned {response.status_code}. "
                        "Check the configured paper-trading credentials."
                    )
                if response.status_code in _TRANSIENT_STATUS_CODES:
                    raise httpx.HTTPStatusError(
                        f"Transient Alpaca response: {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                if response.is_error:
                    raise AlpacaClientError(
                        f"Alpaca order submission failed with status {response.status_code}: {response.text}"
                    )
                return response.json()
            except (httpx.TransportError, httpx.TimeoutException, httpx.HTTPStatusError) as exc:
                last_error = exc
                attempts += 1
                if attempts <= self._settings.max_retries:
                    sleep_seconds = self._settings.retry_backoff_factor * (2 ** (attempts - 1))
                    logger.warning(
                        "alpaca_request_retry",
                        extra={
                            "context": {
                                "path": path,
                                "attempt": attempts,
                                "sleep_seconds": sleep_seconds,
                                "error": str(exc),
                            }
                        },
                    )
                    time.sleep(sleep_seconds)
                    continue
                break

        raise AlpacaClientError(
            f"Alpaca order submission failed after {self._settings.max_retries} retries: {last_error}"
        )


class AlpacaExecutionService(ExecutionService):
    """Provider-agnostic execution adapter backed by the Alpaca REST client."""

    def __init__(
        self,
        settings: AlpacaBrokerSettings,
        *,
        client: AlpacaClient | None = None,
    ) -> None:
        self._settings = settings
        self._client = client or AlpacaClient(settings)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "AlpacaExecutionService":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def describe(self) -> dict[str, Any]:
        return {
            "service": "execution",
            "status": "available",
            "provider": "alpaca",
            "base_url": self._settings.base_url,
        }

    def submit_order(self, intent: OrderIntent) -> OrderSubmissionResult:
        return self._client.submit_order(intent)
