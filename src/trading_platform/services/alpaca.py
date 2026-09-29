"""Thin Alpaca paper-trading client and execution adapter."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Final, Literal, TypeVar

import httpx

from trading_platform.core.logging import get_logger
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

logger = get_logger(__name__)

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

# Alpaca docs + live check 2026-09-29: GET /v2/account/activities page_size max is 100
# (server-enforced with 422, even when date= is supplied).
ALPACA_ACTIVITIES_MAX_PAGE_SIZE: Final = 100
# Alpaca docs: GET /v2/orders limit max is 500 (NOT server-enforced, so the client caps it).
ALPACA_ORDERS_MAX_LIMIT: Final = 500
# Hard page caps bound memory/time (<= 10,000 items per call); exceeding one raises.
ALPACA_FILLS_MAX_PAGES: Final = 100
ALPACA_ORDERS_MAX_PAGES: Final = 20


class AlpacaClientError(Exception):
    """Raised when the Alpaca REST client encounters a non-recoverable error."""


class AlpacaAuthError(AlpacaClientError):
    """Raised when Alpaca credentials are missing or rejected."""


class AlpacaPaginationError(AlpacaClientError):
    """Raised when a paginated Alpaca listing cannot be returned as a complete set."""


class AlpacaPaginationCapExceededError(AlpacaPaginationError):
    """Raised when a listing still has full pages after the configured page cap."""

    def __init__(
        self,
        *,
        endpoint: str,
        pages_fetched: int,
        items_fetched: int,
        max_pages: int,
    ) -> None:
        self.endpoint = endpoint
        self.pages_fetched = pages_fetched
        self.items_fetched = items_fetched
        self.max_pages = max_pages
        super().__init__(
            f"Alpaca pagination cap exceeded for {endpoint}: {pages_fetched} full pages "
            f"({items_fetched} items) reached max_pages={max_pages}; "
            "refusing to return a partial list"
        )


class AlpacaPaginationStalledError(AlpacaPaginationError):
    """Raised when a listing cannot advance safely (repeat id, bad payload, stuck cursor)."""

    def __init__(self, *, endpoint: str, cursor: str | None, detail: str) -> None:
        self.endpoint = endpoint
        self.cursor = cursor
        self.detail = detail
        super().__init__(
            f"Alpaca pagination stalled for {endpoint} at cursor {cursor!r}: {detail}"
        )


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


def _normalize_money(value: Any) -> Decimal:
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


@dataclass(frozen=True)
class BrokerOrderSnapshot:
    broker_order_id: str
    client_order_id: str
    symbol: str
    side: OrderSide
    quantity: Decimal
    status: ExecutionOrderStatus
    broker_status: str
    submitted_at: datetime | None
    filled_at: datetime | None
    canceled_at: datetime | None
    updated_at: datetime | None
    raw_payload: dict[str, Any]


@dataclass(frozen=True)
class BrokerFillSnapshot:
    broker_fill_id: str
    broker_order_id: str
    symbol: str
    side: OrderSide
    quantity: Decimal
    price: Decimal
    filled_at: datetime
    raw_payload: dict[str, Any]


@dataclass(frozen=True)
class BrokerPositionSnapshot:
    symbol: str
    quantity: Decimal
    average_entry_price: Decimal
    cost_basis: Decimal
    market_value: Decimal
    current_price: Decimal
    raw_payload: dict[str, Any]


@dataclass(frozen=True)
class BrokerAccountSnapshot:
    cash: Decimal
    buying_power: Decimal
    equity: Decimal
    long_market_value: Decimal
    short_market_value: Decimal
    raw_payload: dict[str, Any]


def _normalized_order_snapshot(payload: dict[str, Any]) -> BrokerOrderSnapshot:
    broker_status = str(payload.get("status") or "unknown")
    return BrokerOrderSnapshot(
        broker_order_id=str(payload.get("id") or ""),
        client_order_id=str(payload.get("client_order_id") or ""),
        symbol=str(payload.get("symbol") or ""),
        side=_coerce_enum(OrderSide, str(payload.get("side") or "buy"), OrderSide.BUY),
        quantity=_normalize_quantity(payload.get("qty") or "0"),
        status=_normalize_status(broker_status),
        broker_status=broker_status,
        submitted_at=_parse_datetime(payload.get("submitted_at")),
        filled_at=_parse_datetime(payload.get("filled_at")),
        canceled_at=_parse_datetime(payload.get("canceled_at")),
        updated_at=_parse_datetime(payload.get("updated_at")),
        raw_payload=payload,
    )


def _normalized_fill_snapshot(payload: dict[str, Any]) -> BrokerFillSnapshot:
    return BrokerFillSnapshot(
        broker_fill_id=str(payload.get("id") or ""),
        broker_order_id=str(payload.get("order_id") or ""),
        symbol=str(payload.get("symbol") or ""),
        side=_coerce_enum(OrderSide, str(payload.get("side") or "buy"), OrderSide.BUY),
        quantity=_normalize_quantity(payload.get("qty") or "0"),
        price=_normalize_money(payload.get("price") or "0"),
        filled_at=_parse_datetime(payload.get("transaction_time")) or datetime.now(UTC),
        raw_payload=payload,
    )


def _normalized_position_snapshot(payload: dict[str, Any]) -> BrokerPositionSnapshot:
    return BrokerPositionSnapshot(
        symbol=str(payload.get("symbol") or ""),
        quantity=_normalize_quantity(payload.get("qty") or "0"),
        average_entry_price=_normalize_money(payload.get("avg_entry_price") or "0"),
        cost_basis=_normalize_money(payload.get("cost_basis") or "0"),
        market_value=_normalize_money(payload.get("market_value") or "0"),
        current_price=_normalize_money(payload.get("current_price") or "0"),
        raw_payload=payload,
    )


def _normalized_account_snapshot(payload: dict[str, Any]) -> BrokerAccountSnapshot:
    return BrokerAccountSnapshot(
        cash=_normalize_money(payload.get("cash") or "0"),
        buying_power=_normalize_money(payload.get("buying_power") or "0"),
        equity=_normalize_money(payload.get("equity") or "0"),
        long_market_value=_normalize_money(payload.get("long_market_value") or "0"),
        short_market_value=_normalize_money(payload.get("short_market_value") or "0"),
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
        response_payload = self._request_with_retry("POST", "/v2/orders", payload=payload)
        return _normalized_result(response_payload)

    def list_orders(
        self,
        *,
        status: Literal["open", "closed", "all"] = "all",
    ) -> list[BrokerOrderSnapshot]:
        # No date bounding: the local fills/orders history is unbounded, so the broker
        # side must be too (UAT gap 2). before_order_id must not combine with after/until.
        items = self._paginate(
            endpoint="/v2/orders",
            base_params={
                "status": status,
                "direction": "desc",
                "limit": str(ALPACA_ORDERS_MAX_LIMIT),
            },
            cursor_param="before_order_id",
            page_size=ALPACA_ORDERS_MAX_LIMIT,
            max_pages=ALPACA_ORDERS_MAX_PAGES,
        )
        return [_normalized_order_snapshot(item) for item in items]

    def list_fills(self) -> list[BrokerFillSnapshot]:
        # No date bounding: the local fills/orders history is unbounded, so the broker
        # side must be too (UAT gap 2).
        items = self._paginate(
            endpoint="/v2/account/activities/FILL",
            base_params={
                "direction": "desc",
                "page_size": str(ALPACA_ACTIVITIES_MAX_PAGE_SIZE),
            },
            cursor_param="page_token",
            page_size=ALPACA_ACTIVITIES_MAX_PAGE_SIZE,
            max_pages=ALPACA_FILLS_MAX_PAGES,
        )
        return [_normalized_fill_snapshot(item) for item in items]

    def _paginate(
        self,
        *,
        endpoint: str,
        base_params: dict[str, str],
        cursor_param: str,
        page_size: int,
        max_pages: int,
    ) -> list[dict[str, Any]]:
        """Fetch the COMPLETE listing via last-id cursors, or raise a typed error.

        The cursor key is added only once a cursor exists: live Alpaca answers 200 [] for a
        bogus/empty token, which would silently truncate the set.
        """
        seen_ids: set[str] = set()
        items: list[dict[str, Any]] = []
        cursor: str | None = None
        pages = 0

        while True:
            params = dict(base_params)
            if cursor is not None:
                params[cursor_param] = cursor
            payload = self._request_with_retry("GET", endpoint, params=params)
            pages += 1

            if not isinstance(payload, list):
                raise AlpacaPaginationStalledError(
                    endpoint=endpoint, cursor=cursor, detail="non-list page payload"
                )
            for item in payload:
                item_id = item.get("id") if isinstance(item, dict) else None
                if not isinstance(item_id, str) or not item_id:
                    raise AlpacaPaginationStalledError(
                        endpoint=endpoint, cursor=cursor, detail="item without a string id"
                    )
                if item_id in seen_ids:
                    raise AlpacaPaginationStalledError(
                        endpoint=endpoint, cursor=cursor, detail=f"duplicate id {item_id}"
                    )
                seen_ids.add(item_id)
                items.append(item)

            if len(payload) < page_size:
                return items

            next_cursor = payload[-1]["id"]
            if next_cursor == cursor:
                raise AlpacaPaginationStalledError(
                    endpoint=endpoint, cursor=cursor, detail="cursor did not advance"
                )
            if pages >= max_pages:
                raise AlpacaPaginationCapExceededError(
                    endpoint=endpoint,
                    pages_fetched=pages,
                    items_fetched=len(items),
                    max_pages=max_pages,
                )
            cursor = next_cursor

    def list_positions(self) -> list[BrokerPositionSnapshot]:
        payload = self._request_with_retry("GET", "/v2/positions")
        return [_normalized_position_snapshot(item) for item in payload]

    def get_account(self) -> BrokerAccountSnapshot:
        payload = self._request_with_retry("GET", "/v2/account")
        return _normalized_account_snapshot(payload)

    def _request_with_retry(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        attempts = 0
        last_error: Exception | None = None

        while attempts <= self._settings.max_retries:
            try:
                response = self._client.request(method, path, params=params, json=payload)
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
                        f"Alpaca request failed with status {response.status_code}: {response.text}"
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
            f"Alpaca request failed after {self._settings.max_retries} retries: {last_error}"
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
